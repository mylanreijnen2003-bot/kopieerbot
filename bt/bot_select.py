"""Praktisch-selectie stap 2 (vooraf vastgelegd 3 okt 23:00): traders die een bot in de praktijk goed kan kopiëren.
Eisen (t/m 18-8): >= 2 trades/week, >= 3 maanden, >= 30 trades, >= 70% trades in Kraken-perps, actief in de 30 dagen
voor 18-8, K <= 3, instap grotendeels in één keer (mediaan eerste fill >= 70% van eindpositie),
op bot-basis (eerste fill, 0,32% kosten): winst-% > 50, gem. maandrendement > 0, verliesmaanden <= 25%,
gem. rendement per trade >= 0,4% en < 50%.
Rangorde: bot-maandrendement op potje / max(|grootste daling|, 2%). Top 30 getoetst op 19-8 t/m gisteren (API), zelfde berekening.
Gebruik: python -m bt.bot_select <botmap> <universe.parquet> <uitmap>
"""

import glob
import os
import sys
import time

import numpy as np
import pandas as pd

from bot import hl
from bt.bot_stats import KOSTEN, bot_trades
from bt.brede_stats import pot_stats

EIND = pd.Timestamp("2026-08-19").value // 10**6
NU = int(time.time() * 1000) // hl.DAY * hl.DAY


def main():
    bdir, upath, out = sys.argv[1:4]
    os.makedirs(out, exist_ok=True)
    s = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{bdir}/**/bot_*.parquet", recursive=True)])
    u = pd.read_parquet(upath)[["address", "trades_per_week"]]
    m = s.merge(u, on="address")
    m.to_csv(f"{out}/alle.csv", index=False)
    ok = m[(m.trades_per_week >= 2) & (m.bot_maanden >= 3) & (m.kraken_pct >= 70)
           & (m.laatste_trade >= EIND - 30 * hl.DAY) & (m.bot_K <= 3) & (m.eerste_fill_aandeel >= 0.7)
           & (m.bot_winst_pct > 50) & (m.bot_gem_maand_pct > 0) & (m.bot_verliesmaanden <= 0.25 * m.bot_maanden)
           & (m.bot_gem_r_pct >= 0.4) & (m.bot_gem_r_pct < 50)].copy()
    ok["score"] = ok.bot_gem_maand_pct / ok.bot_maxdd_pct.abs().clip(lower=2)
    print("trechter", {"met_stats": len(m), "door_eisen": len(ok)}, flush=True)
    top = ok.sort_values("score", ascending=False).head(30).reset_index(drop=True)
    rows = []
    for _, w in top.iterrows():
        fl = [f for f in hl.fills(w.address, EIND, NU) if f["kind"] == "perp"]
        bt_ = [t for t in bot_trades(fl) if t[1] >= EIND]
        bot = [(c, o, cl, r * (po / p1 - 1) - KOSTEN) for c, o, cl, r, p1, pv, po, q, ad in bt_]
        hun = [(c, o, cl, r * (po / pv - 1) - KOSTEN) for c, o, cl, r, p1, pv, po, q, ad in bt_]
        k = int(w.bot_K)
        ch = hl.info({"type": "clearinghouseState", "user": w.address})
        rows.append({**w.to_dict(), **pot_stats(bot, "test_bot_"),
                     "test_euro_bot_op_100": round(100 / k * sum(t[3] for t in bot), 2),
                     "test_euro_hun_op_100": round(100 / k * sum(t[3] for t in hun), 2),
                     "accountwaarde_nu": float(ch.get("marginSummary", {}).get("accountValue", 0))})
        print(w.address[:10], rows[-1]["test_euro_bot_op_100"], flush=True)
    res = pd.DataFrame(rows)
    res.to_csv(f"{out}/praktisch_top30.csv", index=False)
    for n in (5, 10):
        h = res.head(n)
        print(f"top {n}: per €100 bot {h.test_euro_bot_op_100.mean():.1f}, op hun gem. prijs {h.test_euro_hun_op_100.mean():.1f}")


if __name__ == "__main__":
    main()
