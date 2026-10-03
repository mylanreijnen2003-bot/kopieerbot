"""Handmatig-route 2 (vooraf vastgelegd 3 okt 21:00): binnen de 1.264 uit de brede scan alleen langere, rustige traders.
Eisen (keuzeperiode t/m 18-8): originele eisen brede scan gehaald; mediane houdtijd >= 12 u; <= 14 trades/week;
na vertraging (15-75 min): winst-% > 50, gem. rendement/trade >= 0,5% en < 50% (koersfouten eruit),
gem. maandrendement > 0, verliesmaanden <= 25%. Rangorde: vertraagd maandrendement / max(|daling|, 2%).
Test 19-8 t/m gisteren met 15 min vertraging (15m-kaarsen).
Gebruik: python -m bt.hand2_select <dstatsmap> <alle_stats.csv> <universe.parquet> <uitmap>
"""

import glob
import os
import sys

import numpy as np
import pandas as pd

from bot import hl
from bt.delay_test import EIND, KOSTEN, NU, prijs_na, trades_detail


def main():
    ddir, spath, upath, out = sys.argv[1:5]
    os.makedirs(out, exist_ok=True)
    s = pd.read_csv(spath)
    u = pd.read_parquet(upath)[["address", "trades_per_week"]]
    orig = s[(s.winst_pct > 50) & (s.maanden >= 3) & (s.verliesmaanden <= 0.25 * s.maanden) & (s.gem_maand_pct > 0)
             & (s.handelbaar_pct >= 70) & (s.laatste_trade >= EIND - 30 * hl.DAY)]
    d = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{ddir}/**/dstats_*.parquet", recursive=True)])
    m = orig[["address", "houdtijd_uur", "trades", "winst_pct", "gem_maand_pct"]].merge(d, on="address").merge(u, on="address")
    ok = m[(m.houdtijd_uur >= 12) & (m.trades_per_week <= 14) & (m.d_winst_pct > 50) & (m.d_gem_r_pct >= 0.5)
           & (m.d_gem_r_pct < 50) & (m.d_gem_maand_pct > 0) & (m.d_verliesmaanden <= 0.25 * m.d_maanden)].copy()
    ok["score"] = ok.d_gem_maand_pct / ok.d_maxdd_pct.abs().clip(lower=2)
    print("trechter", {"brede_1264": len(orig), "met_vertraagde_stats": len(m),
                       "rustig_en_robuust": len(ok)}, flush=True)
    top = ok.sort_values("score", ascending=False).head(30).reset_index(drop=True)
    cache, rows = {}, []
    for _, w in top.iterrows():
        fl = [f for f in hl.fills(w.address, EIND, NU) if f["kind"] == "perp"]
        tr = [t for t in trades_detail(fl) if t[1] >= EIND]
        k = int(w.K)
        o_r, v_r = [], []
        for coin, o, c, richting, pin, pout in tr:
            o_r.append(richting * (pout / pin - 1) - KOSTEN)
            a, b = prijs_na(coin, o, 15, "15m", cache), prijs_na(coin, c, 15, "15m", cache)
            if a and b:
                v_r.append(richting * (b / a - 1) - KOSTEN)
        ch = hl.info({"type": "clearinghouseState", "user": w.address})
        rows.append({**w.to_dict(), "test_trades": len(tr), "test_trades_per_week": round(7 * len(tr) / ((NU - EIND) / hl.DAY), 1),
                     "test_winst_pct_15min": round(100 * np.mean(np.array(v_r) > 0), 1) if v_r else None,
                     "test_gem_r_15min_pct": round(100 * np.mean(v_r), 2) if v_r else None,
                     "test_euro_origineel_op_100": round(100 / k * sum(o_r), 2),
                     "test_euro_15min_op_100": round(100 / k * sum(v_r), 2),
                     "accountwaarde_nu": float(ch.get("marginSummary", {}).get("accountValue", 0))})
        print(w.address, rows[-1]["test_euro_15min_op_100"], flush=True)
    res = pd.DataFrame(rows)
    res.to_csv(f"{out}/handmatig2_top30.csv", index=False)
    for n in (5, 10):
        h = res.head(n)
        print(f"top {n}: per €100 gem. 15min {h.test_euro_15min_op_100.mean():.1f}, origineel {h.test_euro_origineel_op_100.mean():.1f}")


if __name__ == "__main__":
    main()
