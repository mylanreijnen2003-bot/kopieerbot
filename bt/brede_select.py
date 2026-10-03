"""Brede scan stap 5: kiezen (t/m 18-8) en eerlijk testen (19-8 t/m gisteren, Hyperliquid-API).
Eisen: winst-% > 50, verliesmaanden <= 25%, >= 3 maanden, gem. maandrendement > 0, >= 70% trades in munten op
Kraken of Bitvavo, laatste trade in de 30 dagen vóór 18-8.
Rangorde: gem. maandrendement op potje / max(|grootste daling|, 2%). Top 30 getoetst; €500 over de top 5 (en top 10).
Gebruik: python -m bt.brede_select <statsmap> <uitmap>
"""

from __future__ import annotations

import glob
import os
import sys
import time

import numpy as np
import pandas as pd

from bot import hl
from bt.brede_stats import pot_stats
from bt.vast_select import trades_pct

EIND = pd.Timestamp("2026-08-19").value // 10**6
NU = int(time.time() * 1000) // hl.DAY * hl.DAY


def main():
    sdir, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    st = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sdir}/**/stats_*.parquet", recursive=True)], ignore_index=True)
    st.to_csv(f"{out}/alle_stats.csv", index=False)
    ok = st[(st.winst_pct > 50) & (st.maanden >= 3) & (st.verliesmaanden <= 0.25 * st.maanden) & (st.gem_maand_pct > 0)
            & (st.handelbaar_pct >= 70) & (st.laatste_trade >= EIND - 30 * hl.DAY)].copy()
    ok["score"] = ok.gem_maand_pct / ok.maxdd_pct.abs().clip(lower=2)
    top = ok.sort_values("score", ascending=False).head(30).reset_index(drop=True)
    print("trechter", {"met_stats": len(st), "door_eisen": len(ok)}, flush=True)
    rows = []
    for _, w in top.iterrows():
        fl = [f for f in hl.fills(w.address, EIND, NU) if f["kind"] == "perp"]
        tr = [t for t in trades_pct(fl) if t[1] >= EIND]
        k = int(w.K)
        euro = 100 / k * sum(t[3] for t in tr)
        rows.append({**w.to_dict(), **pot_stats(tr, "test_"), "test_euro_op_100": round(euro, 2),
                     "nu_actief": bool(fl and fl[-1]["time"] >= NU - 7 * hl.DAY)})
        print(w.address, round(euro, 1), flush=True)
    res = pd.DataFrame(rows)
    res.to_csv(f"{out}/top30.csv", index=False)
    for n in (5, 10, 30):
        s = res.head(n)
        print(f"top {n}: inleg {100 * n} -> {100 * n + s.test_euro_op_100.sum():.0f}")
    pd.set_option("display.width", 250)
    print(res.T.to_string())


if __name__ == "__main__":
    main()
