"""Papier v2 selectie (vooraf vastgelegd 4 okt 14:40).
Kandidaten: de 297 kopieerbare, winstgevende traders uit results/recent (te_volgen_bot).
Versie A 'max rendement': laatste 90 dagen (API), >= 30 trades, geen scalper (mediaan houdtijd >= 2 u, <= 50 trades/week,
  <= 150 fills/dag), gem. >= 0,3% per trade na kosten, K <= 5, >= 70% Kraken, laatste trade <= 7 d. Rangorde: winst per maand
  op het potje (bot-basis). Top 8 actief, rest reserve (max 20 in de lijst).
Versie B 'laag risico': dataset (aug 2025 - 18-8-2026) + API (vanaf 19-8): >= 6 maanden, >= 50% winstmaanden, totaal > 0,
  sinds 19-8 > 0, grootste daling > -40%, plus dezelfde kopieerbaarheidseisen op de laatste 90 dagen (laatste trade <= 14 d).
  Rangorde: totaal / |grootste daling| (min 5%). Top 5 actief, rest reserve (max 12).
Stappen: `kand <recentcsv> <uit>` | `api <deel> <aantal> <kand.parquet> <uit>` | `kies <apimap> <fillsmap> <beurzenmap> <uit>`
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import time

import numpy as np
import pandas as pd

from bot import hl
from bt import data
from bt.engine import to_base
from bt.p2_common import DAG, UUR, k90, max_daling, r_bot, trades_open

NU = int(time.time() * 1000)
START_A = NU - 90 * DAG
DATA_EIND = pd.Timestamp("2026-08-19").value // 10**6


def kand(csv, out):
    os.makedirs(out, exist_ok=True)
    a = pd.read_csv(csv)
    a = a[a.te_volgen_bot.astype(str) == "True"]
    a[["address"]].to_parquet(f"{out}/kand.parquet", index=False)
    print("kandidaten", len(a))


def api(shard, n, kpath, out):
    os.makedirs(out, exist_ok=True)
    k = pd.read_parquet(kpath).sort_values("address").iloc[shard::n]
    tr, info = [], []
    for adr in k.address:
        try:
            fl = [f for f in hl.fills(adr, START_A, NU) if f["kind"] == "perp"]
            ch = hl.info({"type": "clearinghouseState", "user": adr})
        except Exception as exc:  # noqa: BLE001
            print(adr[:10], "fout", exc, flush=True)
            continue
        uit, _ = trades_open(fl)
        for t in uit:
            tr.append({"address": adr, **t})
        dagen = pd.Series([f["time"] // DAG for f in fl]).value_counts()
        info.append({"address": adr, "fills_per_dag": float(dagen.median()) if len(dagen) else 0.0,
                     "laatste_fill": max([f["time"] for f in fl], default=0),
                     "accountwaarde": float(ch.get("marginSummary", {}).get("accountValue", 0))})
    pd.DataFrame(tr).to_parquet(f"{out}/tr_{shard}.parquet", index=False)
    pd.DataFrame(info).to_parquet(f"{out}/info_{shard}.parquet", index=False)


def kopieerbaar(t, info, kraken, max_dagen):
    """t: trades laatste 90 d van één wallet. Geeft (ok, stats)."""
    if len(t) == 0:
        return False, {}
    k = k90(t.open, t.sluit)
    r = t.apply(r_bot, axis=1)
    st = {"n90": len(t), "K": k, "gem_r_pct": round(100 * r.mean(), 2), "winst_pct": round(100 * (r > 0).mean(), 1),
          "per_maand_pct": round(100 * r.sum() / k / (90 / 30.44), 2),
          "houdtijd_uur": round(float(((t.sluit - t.open) / UUR).median()), 1),
          "tpw": round(len(t) / (90 / 7), 1),
          "kraken_pct": round(100 * np.mean([to_base(c) in kraken for c in t.coin]), 1),
          "fills_per_dag": info.get("fills_per_dag", 0), "accountwaarde": round(info.get("accountwaarde", 0)),
          "dagen_sinds_laatste": round((NU - info.get("laatste_fill", 0)) / DAG, 1)}
    ok = (st["houdtijd_uur"] >= 2 and st["tpw"] <= 50 and st["fills_per_dag"] <= 150 and st["gem_r_pct"] >= 0.3
          and k <= 5 and st["kraken_pct"] >= 70 and st["dagen_sinds_laatste"] <= max_dagen and st["accountwaarde"] >= 1000)
    return ok, st


def kies(adir, fdir, vdir, out):
    os.makedirs(out, exist_ok=True)
    kraken = set(data.beurzen(vdir).get("kraken", []))
    tr = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{adir}/**/tr_*.parquet", recursive=True)], ignore_index=True)
    inf = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{adir}/**/info_*.parquet", recursive=True)],
                    ignore_index=True).set_index("address")
    f = data.fills(fdir, set(inf.index)) if glob.glob(f"{fdir}/**/fills_*.parquet", recursive=True) else pd.DataFrame()
    a_rows, b_rows = [], []
    for adr, t in tr.groupby("address"):
        info = inf.loc[adr].to_dict() if adr in inf.index else {}
        t90 = t[t.open >= START_A]
        okA, stA = kopieerbaar(t90, info, kraken, 7)
        if okA and stA["n90"] >= 30:
            a_rows.append({"address": adr, **stA})
        okB, stB = kopieerbaar(t90, info, kraken, 14)
        if not okB or f.empty:
            continue
        x = f[f.address == adr]
        fl = [{"time": int(a), "coin": c, "start": s, "after": af, "px": p}
              for a, c, s, af, p in zip(x.ts, x.coin, x.start, x.after, x.px)]
        oud, _ = trades_open(fl)
        alle = pd.DataFrame(oud + t[t.open >= DATA_EIND].drop(columns=["address"]).to_dict("records"))
        if len(alle) < 20:
            continue
        alle = alle.sort_values("sluit")
        k = stB["K"]
        rk = (alle.apply(r_bot, axis=1) / k).values
        maand = pd.Series(rk).groupby(pd.to_datetime(alle.sluit.values, unit="ms").strftime("%Y-%m")).sum()
        recent = rk[alle.open.values >= DATA_EIND].sum()
        dd = max_daling(rk)
        rec = {"address": adr, **stB, "maanden": len(maand), "winstmaanden": int((maand > 0).sum()),
               "totaal_pct": round(100 * rk.sum(), 1), "sinds_19aug_pct": round(100 * recent, 1),
               "grootste_daling_pct": round(100 * dd, 1), "eerste_trade": str(pd.to_datetime(alle.open.min(), unit="ms").date())}
        if (rec["maanden"] >= 6 and rec["winstmaanden"] >= 0.5 * rec["maanden"] and rec["totaal_pct"] > 0
                and rec["sinds_19aug_pct"] > 0 and dd > -0.40):
            rec["score"] = round(rec["totaal_pct"] / max(5, abs(rec["grootste_daling_pct"])), 2)
            b_rows.append(rec)
    A = pd.DataFrame(a_rows).sort_values("per_maand_pct", ascending=False).head(20) if a_rows else pd.DataFrame()
    B = pd.DataFrame(b_rows).sort_values("score", ascending=False).head(12) if b_rows else pd.DataFrame()
    sel = {"gemaakt": NU, "A": {"n_actief": 8, "lijst": A.to_dict("records")},
           "B": {"n_actief": 5, "lijst": B.to_dict("records")}}
    with open(f"{out}/selectie.json", "w") as fh:
        json.dump(sel, fh, indent=1, default=str)
    with open(f"{out}/kraken.json", "w") as fh:
        json.dump(sorted(kraken), fh)
    for naam, df in [("A", A), ("B", B)]:
        if len(df):
            df.assign(kort=df.address.str[:6] + "…" + df.address.str[-4:]).drop(columns=["address"]).to_csv(
                f"{out}/selectie_{naam}.csv", index=False)
    print("A", len(a_rows), "kandidaten door eisen;", "B", len(b_rows))
    print(A.drop(columns=["address"]).head(8).to_string(index=False) if len(A) else "A leeg")
    print(B.drop(columns=["address"]).head(5).to_string(index=False) if len(B) else "B leeg")


if __name__ == "__main__":
    c = sys.argv[1]
    if c == "kand":
        kand(*sys.argv[2:4])
    elif c == "api":
        api(int(sys.argv[2]), int(sys.argv[3]), *sys.argv[4:6])
    else:
        kies(*sys.argv[2:6])
