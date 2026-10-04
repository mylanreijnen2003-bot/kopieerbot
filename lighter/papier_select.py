"""Papier C (Lighter): selectie maken voor de papierbot.

Gebruik: python -m lighter.papier_select <datamap met part-*> <kraken.json> <regel.json> <uit-privé> <uit-openbaar>
regel.json: {"min_equity": .., "max_pos": .., "handmatig": bool, "rang": "potje"|"potje_3m"|.., "lijst": 20}
Privé (wordt versleuteld): selectie.json met account-index + adres. Openbaar: selectie_kort.csv met afgekorte adressen.
"""
from __future__ import annotations

import json
import os
import sys
import time

import pandas as pd

from lighter import api
from lighter.edge import BASIS, HANDMATIG, KOSTEN_LEIDER, kenmerken, trades_dir
from lighter.selectie import kort, lees, reconstrueer

NU = int(time.time() * 1000)


def main():
    d, kraken_p, regel_p, prive, openbaar = sys.argv[1:6]
    os.makedirs(prive, exist_ok=True)
    os.makedirs(openbaar, exist_ok=True)
    kraken = set(json.load(open(kraken_p)))
    regel = json.load(open(regel_p))
    scan, fills, pnl, accs, meta = lees(d)
    sym = api.markten()
    fills = fills[~fills.kind.fillna("perp").astype(str).str.lower().str.contains("spot")]
    pnl_g = {i: g for i, g in pnl.groupby("idx")} if len(pnl) else {}
    rows = []
    for idx, f in fills.groupby("idx"):
        fl, _ = reconstrueer(f, sym)
        tr = trades_dir(fl)
        if len(tr) < 20:
            continue
        t = pd.DataFrame(tr)
        t["r"] = t.r_bruto - KOSTEN_LEIDER
        k = kenmerken(idx, t, f, pnl_g.get(idx), NU, kraken)
        if k:
            rows.append(k)
    df = pd.DataFrame(rows)
    m = BASIS(df) & (df.equity >= regel["min_equity"]) & (df.p90 <= regel["max_pos"])
    if regel.get("handmatig"):
        m &= HANDMATIG(df)
    g = df[m].sort_values([regel["rang"], "potje"], ascending=False).head(regel.get("lijst", 20))
    lijst = []
    for r in g.itertuples():
        l1 = (accs.get(int(r.idx)) or {}).get("l1")
        lijst.append({"idx": int(r.idx), "l1": l1, "K": max(1, int(r.p90)), "potje": round(float(r.potje), 4),
                      "potje_3m": round(float(r.potje_3m), 4), "equity": round(float(r.equity)), "kraken": round(float(r.kraken), 2),
                      "winst_pct": round(float(r.winst_pct), 3), "slechtste_r": round(float(r.slechtste_r), 4),
                      "tpd30": round(float(r.tpd30), 2), "houd_u": round(float(r.houd_u), 1)})
    json.dump({"gemaakt": NU, "regel": regel, "lijst": lijst}, open(f"{prive}/selectie.json", "w"), indent=1)
    kortlijst = pd.DataFrame([{**{k: v for k, v in x.items() if k not in ("idx", "l1")}, "trader": kort(x["l1"])} for x in lijst])
    kortlijst.to_csv(f"{openbaar}/selectie_kort.csv", index=False)
    print(f"geschikt {int(m.sum())}, lijst {len(lijst)}", flush=True)


if __name__ == "__main__":
    main()
