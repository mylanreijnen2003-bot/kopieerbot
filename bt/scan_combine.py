"""Brede scan stap 2: universum bepalen (alleen data t/m 18-8-2026).
Eisen: >= 90 dagen tussen eerste en laatste trade, >= 100 trades, gemiddeld >= 2 trades/week over die periode,
mediaan <= 150 fills per actieve dag, maker-aandeel < 90% (geen market maker), <= 150.000 fills totaal.
Gebruik: python -m bt.scan_combine <scanmap> <uit.parquet>
"""

import glob
import os
import sys

import pandas as pd

EIND_DAG = pd.Timestamp("2026-08-19").value // 86_400_000_000_000


def main():
    s = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sys.argv[1]}/**/scan_*.parquet", recursive=True)])
    s = s[s.day < EIND_DAG].groupby(["address", "day"], as_index=False).sum()
    g = s.groupby("address")
    w = pd.DataFrame({"fills": g.fills.sum(), "trades": g.trades.sum(), "maker": g.maker.sum(),
                      "fills_dag_mediaan": g.fills.median(), "actieve_dagen": g.size()})
    td = s[s.trades > 0].groupby("address").day
    w["eerste"], w["laatste"] = td.min(), td.max()
    w["spanne_dagen"] = w.laatste - w.eerste
    w["trades_per_week"] = w.trades / (w.spanne_dagen.clip(lower=1) / 7)
    print("wallets met perps-fills", len(w))
    u = w[(w.spanne_dagen >= 90) & (w.trades >= int(os.environ.get("MIN_TRADES", "100"))) & (w.trades_per_week >= 2) & (w.fills_dag_mediaan <= 150)
          & (w.maker / w.fills < 0.9) & (w.fills <= 150_000)].reset_index()
    u["median_av"] = 0.0
    print("universum", len(u))
    u.to_parquet(sys.argv[2], index=False)


if __name__ == "__main__":
    main()
