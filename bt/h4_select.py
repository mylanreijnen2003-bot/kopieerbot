"""H4: kies de top 5 wallets op alle data t/m 18-8-2026 (regels in H4.md).
Gebruik: python -m bt.h4_select <simulatie-uitvoermap> <datamap> <uitmap>
"""

from __future__ import annotations

import glob
import json
import os
import sys

import pandas as pd

from bt import data

TOP = 5


def main():
    sim_dir, d, out = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(out, exist_ok=True)
    st = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sim_dir}/**/stats_*.parquet", recursive=True)], ignore_index=True)
    sim = st[st.gesimuleerd.fillna(False).astype(bool)]
    elig = sim[(sim.gekopieerd >= 50) & (~sim.gestopt.astype(bool))].sort_values("sharpe", ascending=False)
    top = elig.head(TOP)
    cols = ["address", "median_av", "trades", "actieve_dagen", "fills_per_dag_mediaan", "gekopieerd", "sharpe",
            "rendement", "maxdd", "max_hefboom"]
    json.dump({"regels": "H4", "data_tot": "2026-08-18", "start_papier": "2026-08-19",
               "trechter": {"universum": int(len(st)), "gesimuleerd": int(len(sim)), "geschikt": int(len(elig))},
               "wallets": top[cols].round(4).to_dict("records")}, open(f"{out}/selection.json", "w"), indent=1)
    json.dump(data.beurzen(d).get("kraken", []), open(f"{out}/kraken.json", "w"))
    elig[cols].to_csv(f"{out}/geschikt.csv", index=False)
    print(top[cols].to_string(index=False))


if __name__ == "__main__":
    main()
