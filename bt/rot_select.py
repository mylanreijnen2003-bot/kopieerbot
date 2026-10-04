"""Rotatie-backtest stap 2: walk-forward. Per keuzemoment T kiezen op de terugkijkperiode, rendement uit de periode erna.
Eisen (gelijk aan de recent-ranglijst): >= 30 trades, geen scalper (mediaan houdtijd >= 2 u, <= 50 trades/week,
<= 150 fills/dag), gem. >= 0,3% per trade na kosten, K <= 5, >= 70% Kraken, laatste trade <= 7 dagen voor T, winstgevend.
Rangorde: winst per maand op het potje in de terugkijkperiode. Top N gelijk verdeeld; trader zonder trades = 0.
Varianten: L 28/42/84 d, N 10/20, wisselen elke 14 of 28 d. Basislijn: gemiddelde van ALLE wallets die door de eisen komen.
Gebruik: python -m bt.rot_select <statsmap> <uitmap>
"""

import glob
import os
import sys

import numpy as np
import pandas as pd

from bot import hl
from bt.rot_stats import DAG, EIND, TS


def main():
    sdir, out = sys.argv[1:3]
    os.makedirs(out, exist_ok=True)
    s = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sdir}/**/rot_*.parquet", recursive=True)],
                  ignore_index=True)
    ok = s[(s.houdtijd_uur >= 2) & (s.tpw <= 50) & (s.fills_per_dag <= 150) & (s.gem_r_pct >= 0.3) & (s.K <= 5)
           & (s.kraken_pct >= 70) & (s.dagen_sinds_laatste <= 7) & (s.per_maand_pct > 0)]
    btc = hl.candles("BTC", TS[0] - DAG, EIND + DAG, "1d")
    bpx = {t: v[0] for t, v in btc.items()}

    def btc_ret(t, h):
        a, b = bpx.get(t), bpx.get(t + h * DAG)
        return round(100 * (b / a - 1), 2) if a and b else None

    perioden, sam = [], []
    for L in [28, 42, 84]:
        for N in [10, 20]:
            for H in [14, 28]:
                ts = TS if H == 14 else TS[::2]
                ts = [t for t in ts if t + H * DAG <= EIND]
                reeks = []
                for T in ts:
                    g = ok[(ok.T == T) & (ok.L == L)].sort_values("per_maand_pct", ascending=False)
                    top = g.head(N)
                    rij = {"L": L, "N": N, "H": H, "T": pd.to_datetime(T, unit="ms").date(), "kandidaten": len(g),
                           "top_pct": round(top[f"v{H}_pct"].mean(), 2) if len(top) else 0.0,
                           "alle_pct": round(g[f"v{H}_pct"].mean(), 2) if len(g) else 0.0,
                           "top_gestopt": int(top[f"v{H}_stop"].sum()), "top_zonder_trades": int((top[f"v{H}_n"] == 0).sum()),
                           "btc_pct": btc_ret(T, H)}
                    reeks.append(rij)
                df = pd.DataFrame(reeks)
                perioden.append(df)
                pm = 30.44 / H
                sam.append({"L": L, "N": N, "H": H, "perioden": len(df),
                            "top_gem_per_periode_pct": round(df.top_pct.mean(), 2),
                            "top_per_maand_pct": round(df.top_pct.mean() * pm, 2),
                            "top_mediaan_per_periode_pct": round(df.top_pct.median(), 2),
                            "top_positieve_perioden_pct": round(100 * (df.top_pct > 0).mean(), 0),
                            "top_slechtste_periode_pct": round(df.top_pct.min(), 2),
                            "top_totaal_pct": round(100 * (np.prod(1 + df.top_pct / 100) - 1), 1),
                            "alle_per_maand_pct": round(df.alle_pct.mean() * pm, 2),
                            "btc_per_maand_pct": round(df.btc_pct.dropna().mean() * pm, 2),
                            "gem_kandidaten": round(df.kandidaten.mean(), 0)})
    pd.concat(perioden).to_csv(f"{out}/perioden.csv", index=False)
    sd = pd.DataFrame(sam)
    sd.to_csv(f"{out}/samenvatting.csv", index=False)
    print(sd.to_string(index=False))


if __name__ == "__main__":
    main()
