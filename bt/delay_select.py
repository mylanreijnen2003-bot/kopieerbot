"""Handmatig-route stap 2: kiezen op vertraagde cijfers (t/m 18-8), testen 19-8 t/m gisteren met 15 min vertraging
(15m-kaarsen). Eisen: winst-% > 50, >= 3 maanden, verliesmaanden <= 25%, gem. maandrendement > 0 (alles na vertraging).
Rangorde: gem. maandrendement / max(|grootste daling|, 2%). Top 30 getoetst.
Gebruik: python -m bt.delay_select <dstatsmap> <uitmap>
"""

import glob
import os
import sys

import numpy as np
import pandas as pd

from bot import hl
from bt.delay_test import EIND, KOSTEN, NU, prijs_na, trades_detail


def main():
    sdir, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    st = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sdir}/**/dstats_*.parquet", recursive=True)], ignore_index=True)
    ok = st[(st.d_winst_pct > 50) & (st.d_maanden >= 3) & (st.d_verliesmaanden <= 0.25 * st.d_maanden)
            & (st.d_gem_maand_pct > 0)].copy()
    ok["score"] = ok.d_gem_maand_pct / ok.d_maxdd_pct.abs().clip(lower=2)
    top = ok.sort_values("score", ascending=False).head(30).reset_index(drop=True)
    print("trechter", {"kandidaten": len(st), "door_eisen_na_vertraging": len(ok)}, flush=True)
    cache, rows = {}, []
    for _, w in top.iterrows():
        fl = [f for f in hl.fills(w.address, EIND, NU) if f["kind"] == "perp"]
        tr = [t for t in trades_detail(fl) if t[1] >= EIND]
        k = int(w.K)
        orig, vert = [], []
        for coin, o, c, richting, pin, pout in tr:
            a, b = prijs_na(coin, o, 15, "15m", cache), prijs_na(coin, c, 15, "15m", cache)
            orig.append(richting * (pout / pin - 1) - KOSTEN)
            if a and b:
                vert.append(richting * (b / a - 1) - KOSTEN)
        rows.append({**w.to_dict(), "test_trades": len(tr), "test_trades_per_dag": round(len(tr) / ((NU - EIND) / hl.DAY), 1),
                     "test_euro_origineel_op_100": round(100 / k * sum(orig), 2),
                     "test_euro_15min_op_100": round(100 / k * sum(vert), 2),
                     "test_winst_pct_15min": round(100 * np.mean(np.array(vert) > 0), 1) if vert else None,
                     "test_gem_r_15min_pct": round(100 * np.mean(vert), 3) if vert else None,
                     "houdtijd_uur": w.get("d_houdtijd_uur")})
        print(rows[-1]["address"], rows[-1]["test_euro_15min_op_100"], flush=True)
    res = pd.DataFrame(rows)
    res.to_csv(f"{out}/handmatig_top30.csv", index=False)
    for n in (5, 10):
        h = res.head(n)
        print(f"top {n}: per €100 gem. {h.test_euro_15min_op_100.mean():.1f} (origineel {h.test_euro_origineel_op_100.mean():.1f})")


if __name__ == "__main__":
    main()
