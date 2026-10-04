"""Top 50 winstgevende wallets uit 'honderd' (>= 3 trades van +50% koers), met zoveel mogelijk statistieken.
Data 28-7-2025 t/m 18-8-2026 (HF-dataset). Bot-kopie: instap = eerste fill van de trader, uitstap = zijn gem.
uitstapprijs, kosten 0,32% per trade (Kraken), inzet per trade = pot / K (K = 90e pct gelijktijdige posities).
Alleen wallets die als bot-kopie netto winstgevend zijn. Rangorde: maandrendement van de bot-kopie.
Stappen: `stats <deel> <aantal> <wallets_50pct.csv> <fillsmap> <beurzenmap> <uit>` | `kies <statsmap> <uit>`
"""

from __future__ import annotations

import glob
import os
import sys
import time
from collections import Counter

import numpy as np
import pandas as pd

from bt import data
from bt.bot_stats import bot_trades
from bt.engine import to_base
from bt.p2_common import k90, max_daling

KOSTEN = 0.0032
DAG = 86_400_000
EIND = pd.Timestamp("2026-08-19").value // 10**6
OKT_A, OKT_B = pd.Timestamp("2025-10-10").value // 10**6, pd.Timestamp("2025-10-11 12:00").value // 10**6


def wallet_stats(fl, kraken, bitvavo):
    bt_ = bot_trades(fl)
    if len(bt_) < 3:
        return None, None
    o = np.array([t[1] for t in bt_]); c = np.array([t[2] for t in bt_])
    d = np.array([t[3] for t in bt_])
    eigen = d * (np.array([t[6] for t in bt_]) / np.array([t[5] for t in bt_]) - 1)
    r = d * (np.array([t[6] for t in bt_]) / np.array([t[4] for t in bt_]) - 1) - KOSTEN
    K = k90(o.tolist(), c.tolist())
    volg = np.argsort(c)
    rk = r[volg] / K
    maanden = max(1.0, (c.max() - o.min()) / (30.44 * DAG))
    mnd = pd.Series(rk, index=pd.to_datetime(c[volg], unit="ms").to_period("M")).groupby(level=0).sum()
    okt = (o >= OKT_A) & (o < OKT_B)
    l90 = c >= EIND - 90 * DAG
    munten = [to_base(t[0]) for t in bt_]
    top = Counter(munten).most_common(3)
    win, ver = r[r > 0].sum(), -r[r < 0].sum()
    hold = (c - o) / 3.6e6
    rec = {
        "trades": len(bt_), "trades_per_maand": round(len(bt_) / maanden, 1), "maanden_actief": round(maanden, 1),
        "eerste": str(pd.to_datetime(o.min(), unit="ms").date()), "laatste": str(pd.to_datetime(c.max(), unit="ms").date()),
        "K": K,
        "maand_rendement_pct": round(100 * rk.sum() / maanden, 2),
        "totaal_rendement_pct": round(100 * rk.sum(), 1),
        "max_daling_pct": round(100 * max_daling(rk), 1),
        "winstmaanden_pct": round(100 * (mnd > 0).mean(), 0), "aantal_maanden": int(len(mnd)),
        "beste_maand_pct": round(100 * mnd.max(), 1), "slechtste_maand_pct": round(100 * mnd.min(), 1),
        "laatste_90d_pct": round(100 * rk[c[volg] >= EIND - 90 * DAG].sum(), 1), "trades_laatste_90d": int(l90.sum()),
        "zonder_10okt_maand_pct": round(100 * (r[~okt].sum() / K) / maanden, 2), "trades_10okt": int(okt.sum()),
        "winst_pct_bot": round(100 * (r > 0).mean(), 1), "gem_netto_pct": round(100 * r.mean(), 2),
        "mediaan_netto_pct": round(100 * float(np.median(r)), 2),
        "beste_trade_pct": round(100 * r.max(), 1), "slechtste_trade_pct": round(100 * r.min(), 1),
        "profit_factor": round(win / ver, 2) if ver > 0 else 99.0,
        "verlies_10pct": int((r <= -0.1).sum()), "verlies_20pct": int((r <= -0.2).sum()),
        "winst_pct_eigen": round(100 * (eigen > 0).mean(), 1), "gem_eigen_pct": round(100 * eigen.mean(), 2),
        "n_100": int((eigen >= 1).sum()), "n_50": int((eigen >= 0.5).sum()), "n_20": int((eigen >= 0.2).sum()),
        "n_10": int((eigen >= 0.1).sum()),
        "houdtijd_mediaan_uur": round(float(np.median(hold)), 1), "houdtijd_gem_uur": round(float(hold.mean()), 1),
        "eerste_fill_aandeel": round(float(np.mean([t[7] for t in bt_])), 2),
        "bijkopen_gem": round(float(np.mean([t[8] for t in bt_])), 1),
        "long_pct": round(100 * (d > 0).mean(), 0),
        "gem_netto_long_pct": round(100 * r[d > 0].mean(), 2) if (d > 0).any() else None,
        "gem_netto_short_pct": round(100 * r[d < 0].mean(), 2) if (d < 0).any() else None,
        "n_munten": len(set(munten)), "top3_munten": ", ".join(f"{m} ({n})" for m, n in top),
        "kraken_pct": round(100 * np.mean([m in kraken for m in munten]), 0),
        "bitvavo_pct": round(100 * np.mean([m in bitvavo for m in munten]), 0),
        "rendement_alleen_kraken_maand_pct": round(100 * (r[[m in kraken for m in munten]].sum() / K) / maanden, 2),
    }
    return rec, {str(k): round(100 * v, 2) for k, v in mnd.items()}


def stats(shard, n, kcsv, d, vdir, out):
    os.makedirs(out, exist_ok=True)
    v = data.beurzen(vdir)
    kraken, bitvavo = set(v.get("kraken", [])), set(v.get("bitvavo", []))
    k = pd.read_csv(kcsv)
    k = k[k.som_r_pct > 0].sort_values("address").iloc[shard::n]
    f = data.fills(d, set(k.address))
    rows, mrows = [], []
    for a, x in f.groupby("address"):
        fl = [{"time": int(t), "coin": c, "start": s, "after": af, "px": p}
              for t, c, s, af, p in zip(x.ts, x.coin, x.start, x.after, x.px)
              if not str(c).startswith(("#", "@"))]
        rec, mnd = wallet_stats(fl, kraken, bitvavo)
        if rec:
            rows.append({"address": a, **rec})
            mrows.append({"address": a, **mnd})
    print(len(rows), "wallets", flush=True)
    pd.DataFrame(rows).to_parquet(f"{out}/s_{shard}.parquet", index=False)
    pd.DataFrame(mrows).to_parquet(f"{out}/m_{shard}.parquet", index=False)


def live(adr):
    from bot import hl
    nu = int(time.time() * 1000)
    try:
        ch = hl.info({"type": "clearinghouseState", "user": adr})
        fl = hl.info({"type": "userFillsByTime", "user": adr, "startTime": nu - 30 * DAG, "endTime": nu})
    except Exception as exc:  # noqa: BLE001
        print("live fout", exc, flush=True)
        return {}
    return {"accountwaarde_nu": round(float(ch.get("marginSummary", {}).get("accountValue", 0)), 0),
            "open_posities_nu": len(ch.get("assetPositions", [])),
            "fills_30d": len(fl), "laatste_fill_nu": str(pd.to_datetime(max([x["time"] for x in fl]), unit="ms").date()) if fl else ""}


def kies(sdir, out):
    os.makedirs(out, exist_ok=True)
    s = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sdir}/**/s_*.parquet", recursive=True)], ignore_index=True)
    m = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sdir}/**/m_*.parquet", recursive=True)], ignore_index=True)
    print("kandidaten", len(s), "| bot-kopie winstgevend", int((s.totaal_rendement_pct > 0).sum()))
    s = s[s.totaal_rendement_pct > 0].sort_values("maand_rendement_pct", ascending=False).head(50).reset_index(drop=True)
    s.insert(0, "rang", range(1, len(s) + 1))
    s.insert(2, "kort", s.address.str[:6] + "…" + s.address.str[-4:])
    lv = pd.DataFrame([{"address": a, **live(a)} for a in s.address])
    s = s.merge(lv, on="address", how="left")
    s.to_csv(f"{out}/top50.csv", index=False)
    m = m.set_index("address").loc[s.address]
    m = m[sorted(m.columns)]
    m.insert(0, "kort", s.kort.values)
    m.reset_index().to_csv(f"{out}/top50_maanden.csv", index=False)
    pd.set_option("display.width", 250)
    print(s.drop(columns=["address"]).to_string(index=False))


if __name__ == "__main__":
    if sys.argv[1] == "stats":
        stats(int(sys.argv[2]), int(sys.argv[3]), *sys.argv[4:8])
    else:
        kies(*sys.argv[2:4])
