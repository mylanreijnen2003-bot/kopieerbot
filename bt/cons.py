"""Consensus-test (vooraf vastgelegd 4 okt 09:15).
Groep: alle wallets die op data t/m 18-8 'lang winstgevend' waren (varianten-eisen: 0,5-15 trades/week, >= 180 dagen,
>= 20 trades, >= 5 maanden, <= 30% verliesmaanden, totaal > 0, >= 70% Kraken, actief <= 45 d voor 18-8, K <= 5,
gem. per trade < 50%, grootste daling > -50%).
Signaal per munt per uur: stemmen = wallets met een positie (long/short). Bij >= 5 stemmen en (long-short)/stemmen >= 0,6 -> long,
<= -0,6 -> short, anders plat. Positie geldt voor het volgende uur. Kosten 0,16% per kant (0,32% per rondje).
Elke munt eigen gelijk potje; totaal = gemiddelde over munten die ooit een signaal gaven. Ook gevoeligheid: drempel 0,5/0,8, dagelijks.
Stappen: `groep <statsmap> <uni> <uit>` | `fills <deel> <aantal> <groep.parquet> <uit>` | `sim <fillsmap> <beurzenmap> <uit>`
"""

from __future__ import annotations

import glob
import os
import sys
import time

import numpy as np
import pandas as pd

from bot import hl
from bt import data
from bt.engine import to_base

START = pd.Timestamp("2026-08-19").value // 10**6
EIND = START
NU = int(time.time() * 1000) // hl.DAY * hl.DAY
KANT = 0.0016


def groep(sdir, upath, out):
    s = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sdir}/**/bot_*.parquet", recursive=True)])
    m = s.merge(pd.read_parquet(upath)[["address", "trades_per_week", "spanne_dagen"]], on="address")
    m = m[(m.trades_per_week >= 0.5) & (m.trades_per_week < 15) & (m.spanne_dagen >= 180) & (m.bot_trades >= 20)
          & (m.bot_maanden >= 5) & (m.bot_verliesmaanden <= 0.3 * m.bot_maanden) & (m.bot_totaal_pct > 0)
          & (m.kraken_pct >= 70) & (m.laatste_trade >= EIND - 45 * hl.DAY) & (m.bot_K <= 5)
          & (m.bot_gem_r_pct < 50) & (m.bot_maxdd_pct > -50)]
    print("consensusgroep", len(m))
    os.makedirs(out, exist_ok=True)
    m[["address", "trades_per_week"]].to_parquet(f"{out}/groep.parquet", index=False)


def fills(shard, n, gpath, out):
    os.makedirs(out, exist_ok=True)
    g = pd.read_parquet(gpath).sort_values("address").iloc[shard::n]
    rows = []
    for a in g.address:
        try:
            for f in hl.fills(a, START, NU):
                if f["kind"] == "perp":
                    rows.append((a, f["time"], f["coin"], f["start"], f["after"]))
        except Exception as exc:  # noqa: BLE001
            print(a[:10], "fout", exc, flush=True)
    pd.DataFrame(rows, columns=["address", "ts", "coin", "start", "after"]).to_parquet(f"{out}/cf_{shard}.parquet")
    print(len(rows), "fills", flush=True)


def simuleer(stand, prijs, drempel, uren_stap=1):
    """stand: DataFrame uren x wallets met -1/0/1. prijs: Series slot per uur. Geeft (rendement, aantal wissels)."""
    lo, sh = (stand > 0).sum(1), (stand < 0).sum(1)
    n = lo + sh
    c = ((lo - sh) / n.replace(0, np.nan)).fillna(0)
    pos = pd.Series(0.0, index=stand.index)
    pos[(n >= 5) & (c >= drempel)] = 1.0
    pos[(n >= 5) & (c <= -drempel)] = -1.0
    if uren_stap > 1:
        keep = (pos.index // hl.HOUR) % uren_stap == 0
        pos = pos.where(keep).ffill().fillna(0)
    ret = prijs.pct_change().shift(-1).fillna(0)
    wissel = pos.diff().abs().fillna(pos.abs())
    pnl = pos * ret - wissel * KANT
    return float((1 + pnl).prod() - 1), int((wissel > 0).sum()), float(pos.abs().mean())


def sim(fdir, vdir, out):
    os.makedirs(out, exist_ok=True)
    kraken = set(data.beurzen(vdir).get("kraken", []))
    f = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{fdir}/**/cf_*.parquet", recursive=True)])
    f = f.sort_values("ts")
    uren = np.arange(START, NU, hl.HOUR) + hl.HOUR - 1
    res = []
    munten = [c for c, x in f.groupby("coin") if x.address.nunique() >= 5 and to_base(c) in kraken]
    print("munten met >= 5 wallets en op Kraken:", len(munten), flush=True)
    for c in munten:
        x = f[f.coin == c]
        cols = {}
        for a, y in x.groupby("address"):
            t = np.concatenate([[START - 1], y.ts.values])
            v = np.concatenate([[np.sign(y.start.values[0])], np.sign(y.after.values)])
            idx = np.searchsorted(t, uren, side="right") - 1
            cols[a] = v[idx]
        stand = pd.DataFrame(cols, index=uren)
        cd = hl.candles(c, START, NU + hl.HOUR, "1h")
        prijs = pd.Series({t + hl.HOUR - 1: v[3] for t, v in cd.items()}).reindex(uren).ffill()
        if prijs.isna().all():
            continue
        prijs = prijs.bfill()
        rij = {"munt": to_base(c), "wallets": len(cols),
               "bezet_uren_pct": round(100 * float(((stand != 0).sum(1) >= 5).mean()), 1),
               "koop_en_houd_pct": round(100 * (prijs.iloc[-1] / prijs.iloc[0] - 1), 1)}
        for naam, d, stap in [("hoofd_0.6_uur", 0.6, 1), ("d0.5_uur", 0.5, 1), ("d0.8_uur", 0.8, 1),
                              ("hoofd_0.6_dag", 0.6, 24)]:
            r, w, b = simuleer(stand, prijs, d, stap)
            rij[f"{naam}_pct"], rij[f"{naam}_wissels"], rij[f"{naam}_in_markt"] = round(100 * r, 2), w, round(b, 2)
        res.append(rij)
        print(rij, flush=True)
    df = pd.DataFrame(res)
    df.to_csv(f"{out}/per_munt.csv", index=False)
    act = df[df["hoofd_0.6_uur_wissels"] > 0]
    sam = {"munten_met_signaal": len(act), "dagen": round((NU - START) / hl.DAY, 1)}
    for k in ["hoofd_0.6_uur", "d0.5_uur", "d0.8_uur", "hoofd_0.6_dag"]:
        a2 = df[df[f"{k}_wissels"] > 0]
        sam[f"{k}_gem_pct"] = round(a2[f"{k}_pct"].mean(), 2) if len(a2) else None
        sam[f"{k}_munten"] = len(a2)
    btc = df[df.munt == "BTC"]
    sam["btc_koop_en_houd_pct"] = float(btc.koop_en_houd_pct.iloc[0]) if len(btc) else None
    pd.DataFrame([sam]).to_csv(f"{out}/samenvatting.csv", index=False)
    print(sam)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "groep":
        groep(*sys.argv[2:5])
    elif cmd == "fills":
        fills(int(sys.argv[2]), int(sys.argv[3]), *sys.argv[4:6])
    else:
        sim(*sys.argv[2:5])
