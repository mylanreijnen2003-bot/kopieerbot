"""Rotatie-backtest variant '0x2555-type' (vooraf vastgelegd 4 okt 17:05), op bestaande rot-stats.
Alleen traders met in de terugkijkperiode: mediaan houdtijd >= 12 u, gem. >= 1% per trade na kosten, >= 30 trades,
<= 150 fills/dag, K <= 5, >= 70% Kraken, laatste trade <= 7 d voor T, winstgevend.
Rangorde-varianten: rendement (per maand), constantheid (winst-% trades), voordeel per trade (gem. r).
L 42/84 d, N 5/10/20, wisselen 14/28 d. Basislijn = gemiddelde van alle traders die door de eisen komen.
Let op: rot-stats bevat alleen rijen met >= 30 trades in de terugkijkperiode.
Gebruik: python -m bt.rot_select2 <statsmap> <uitmap>
"""

import glob
import os
import sys

import numpy as np
import pandas as pd

from bot import hl
from bt.rot_stats import DAG, EIND, TS

RANG = {"rendement": "per_maand_pct", "constantheid": "winst_pct", "voordeel": "gem_r_pct"}


def main():
    sdir, out = sys.argv[1:3]
    os.makedirs(out, exist_ok=True)
    s = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sdir}/**/rot_*.parquet", recursive=True)], ignore_index=True)
    ok = s[(s.houdtijd_uur >= 12) & (s.gem_r_pct >= 1.0) & (s.fills_per_dag <= 150) & (s.K <= 5)
           & (s.kraken_pct >= 70) & (s.dagen_sinds_laatste <= 7) & (s.per_maand_pct > 0)]
    btc = {t: v[0] for t, v in hl.candles("BTC", TS[0] - DAG, EIND + DAG, "1d").items()}
    sam, per = [], []
    for L in [42, 84]:
        for H in [14, 28]:
            ts = [t for t in (TS if H == 14 else TS[::2]) if t + H * DAG <= EIND]
            for rang, kol in RANG.items():
                for N in [5, 10, 20]:
                    rij = []
                    for T in ts:
                        g = ok[(ok["T"] == T) & (ok["L"] == L)].sort_values(kol, ascending=False)
                        top = g.head(N)
                        a, b = btc.get(T), btc.get(T + H * DAG)
                        rij.append({"L": L, "H": H, "rang": rang, "N": N, "T": pd.to_datetime(T, unit="ms").date(),
                                    "kandidaten": len(g), "gekozen": len(top),
                                    "top_pct": round(top[f"v{H}_pct"].mean(), 2) if len(top) else 0.0,
                                    "alle_pct": round(g[f"v{H}_pct"].mean(), 2) if len(g) else 0.0,
                                    "btc_pct": round(100 * (b / a - 1), 2) if a and b else np.nan})
                    df = pd.DataFrame(rij)
                    per.append(df)
                    pm = 30.44 / H
                    sam.append({"L": L, "H": H, "rang": rang, "N": N, "perioden": len(df),
                                "gem_kandidaten": round(df.kandidaten.mean(), 1), "gem_gekozen": round(df.gekozen.mean(), 1),
                                "top_per_maand_pct": round(df.top_pct.mean() * pm, 2),
                                "top_positief_pct": round(100 * (df.top_pct > 0).mean()),
                                "top_slechtste_pct": round(df.top_pct.min(), 2),
                                "top_totaal_pct": round(100 * (np.prod(1 + df.top_pct / 100) - 1), 1),
                                "alle_per_maand_pct": round(df.alle_pct.mean() * pm, 2),
                                "btc_per_maand_pct": round(df.btc_pct.dropna().mean() * pm, 2),
                                "top_btc_stijgend": round(df[df.btc_pct > 0].top_pct.mean(), 2),
                                "top_btc_dalend": round(df[df.btc_pct <= 0].top_pct.mean(), 2)})
    sd = pd.DataFrame(sam)
    sd.to_csv(f"{out}/samenvatting.csv", index=False)
    pd.concat(per).to_csv(f"{out}/perioden.csv", index=False)
    print(sd.to_string(index=False))


if __name__ == "__main__":
    main()
