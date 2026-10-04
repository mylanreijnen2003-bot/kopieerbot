"""Ronde 3 (vooraf vastgelegd 3 okt 21:30): alle Hyperliquid-traders met >= 3 mnd geschiedenis, >= 30 trades,
>= 2 trades/week, >= 70% trades in munten op Kraken/Bitvavo, actief in de 30 dagen voor 18-8.
Trades doorgerekend met >= 1 uur vertraging (uurkaarsen: 1-2 uur). Eisen na vertraging: >= 3 maanden,
gem. maandrendement > 0, gem. rendement/trade < 50% (koersfouten eruit).
Rangorde: meeste winst per maand op het potje (na vertraging). Test 19-8 t/m gisteren met 60 min vertraging (15m-kaarsen).
Gebruik: python -m bt.hand3_select <r3map> <universe.parquet> <uitmap>
"""

import glob
import os
import sys

import numpy as np
import pandas as pd

from bot import hl
from bt.delay_test import EIND, KOSTEN, NU, prijs_na, trades_detail


def main():
    ddir, upath, out = sys.argv[1:4]
    os.makedirs(out, exist_ok=True)
    d = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{ddir}/**/r3_*.parquet", recursive=True)])
    u = pd.read_parquet(upath)[["address", "trades_per_week"]]
    m = d.merge(u, on="address")
    m.to_csv(f"{out}/ronde3_alle.csv", index=False)
    ok = m[(m.trades_per_week >= 2) & (m.handelbaar_pct >= 70) & (m.laatste_trade >= EIND - 30 * hl.DAY)
           & (m.d_maanden >= 3) & (m.d_gem_maand_pct > 0) & (m.d_gem_r_pct < 50)].copy()
    print("trechter", {"universum": len(u), "met_stats": len(m), "door_eisen": len(ok)}, flush=True)
    top = ok.sort_values("d_gem_maand_pct", ascending=False).head(30).reset_index(drop=True)
    cache, rows = {}, []
    for _, w in top.iterrows():
        fl = [f for f in hl.fills(w.address, EIND, NU) if f["kind"] == "perp"]
        tr = [t for t in trades_detail(fl) if t[1] >= EIND]
        k = int(w.d_K)
        o_r, v_r = [], []
        for coin, o, c, richting, pin, pout in tr:
            o_r.append(richting * (pout / pin - 1) - KOSTEN)
            a, b = prijs_na(coin, o, 60, "15m", cache), prijs_na(coin, c, 60, "15m", cache)
            if a and b:
                v_r.append(richting * (b / a - 1) - KOSTEN)
        ch = hl.info({"type": "clearinghouseState", "user": w.address})
        rows.append({**w.to_dict(), "test_trades": len(tr),
                     "test_winst_pct_60min": round(100 * np.mean(np.array(v_r) > 0), 1) if v_r else None,
                     "test_gem_r_60min_pct": round(100 * np.mean(v_r), 2) if v_r else None,
                     "test_euro_origineel_op_100": round(100 / k * sum(o_r), 2),
                     "test_euro_60min_op_100": round(100 / k * sum(v_r), 2),
                     "accountwaarde_nu": float(ch.get("marginSummary", {}).get("accountValue", 0))})
        print(w.address, rows[-1]["test_euro_60min_op_100"], flush=True)
    res = pd.DataFrame(rows)
    res.to_csv(f"{out}/ronde3_top30.csv", index=False)
    for n in (5, 10):
        h = res.head(n)
        print(f"top {n}: per €100 gem. 60min {h.test_euro_60min_op_100.mean():.1f}, origineel {h.test_euro_origineel_op_100.mean():.1f}")


if __name__ == "__main__":
    main()
