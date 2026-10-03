"""Backtest stap B (parallel per deel van de wallets): kopieer-simulatie op de TRAIN-periode met de hoofdvariant P.
Gebruik: python -m bt.simulate <deel> <aantal> <datamap> <uitmap>
"""

from __future__ import annotations

import os
import sys

import pandas as pd

from bt import data
from bt.engine import Cfg, run
from bt.variants import variants

MAX_FILLS_DAG = 150     # kopieerbaar: niet te veel fills per dag (vertraging en kosten)
MIN_ACTIEVE_DAGEN = 30  # minimaal aantal dagrendementen voor een Sharpe
MIN_TRADES = 100        # steekproef: genoeg afgeronde trades, ongeacht hoe lang de wallet actief is
MAX_FILLS_TOTAAL = 150_000  # meer fills dan dit = gemiddeld > ~380 per dag: nooit kopieerbaar, niet inladen (geheugen)


def main():
    shard, n, d, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4]
    os.makedirs(out, exist_ok=True)
    uni = pd.read_parquet(os.environ.get("UNIVERSE", "data/universe.parquet")).sort_values("address")
    window = getattr(data, os.environ.get("WINDOW", "TRAIN"))
    mine = uni.iloc[shard::n]
    counts = data.fill_counts(d)
    te_groot = set(counts[counts > MAX_FILLS_TOTAAL].index)
    bars, fund, av, venues = data.bars(d), data.funding(d), data.av(d), data.beurzen(d)
    cfg: Cfg = variants(venues)[os.environ.get("CFG", "P")]
    f_all = data.fills(d, set(mine.address) - te_groot)
    f_all = f_all[(f_all.ts >= window[0]) & (f_all.ts < window[1])]
    stats, curves = [], []
    for i, (addr, w) in enumerate(mine.set_index("address").iterrows()):
        if addr in te_groot:
            stats.append({"address": addr, "median_av": w.median_av, "fills": int(counts[addr]), "gesimuleerd": False,
                          "reden_niet": "te veel fills"})
            continue
        f = f_all[f_all.address == addr]
        per_day = f.groupby(f.ts // 86_400_000).size()
        trades = int((((f.after == 0) & (f.start != 0)) | (f.start * f.after < 0)).sum())
        row = {"address": addr, "median_av": w.median_av, "verdict_train": w.verdict, "fills": len(f), "trades": trades,
               "actieve_dagen": int(len(per_day)), "fills_per_dag_mediaan": float(per_day.median()) if len(per_day) else 0.0,
               "maker_aandeel": float(f.maker.mean()) if len(f) else 0.0, "liquidaties": int(f.liq.sum()) if len(f) else 0}
        if (trades >= MIN_TRADES and row["actieve_dagen"] >= MIN_ACTIEVE_DAGEN
                and row["fills_per_dag_mediaan"] <= MAX_FILLS_DAG):
            av_t, av_v = av.get(addr, ([], []))
            eq, p = run(cfg, data.as_list(f), bars, fund, av_t, av_v, w.median_av, *window)
            row.update(gesimuleerd=True, gekopieerd=p.copied, overgeslagen_geerfd=p.skip_geerfd,
                       overgeslagen_beurs=p.skip_beurs, max_hefboom=p.max_lev, gestopt=p.dead, reden=p.reden,
                       **data.curve_stats(eq))
            curves += [{"address": addr, "dag": k, "equity": e} for k, e in enumerate(eq)]
        else:
            row["gesimuleerd"] = False
        stats.append(row)
        if i % 25 == 0:
            print(f"{i}/{len(mine)}", flush=True)
    pd.DataFrame(stats).to_parquet(f"{out}/stats_{shard}.parquet", index=False)
    pd.DataFrame(curves).to_parquet(f"{out}/curves_{shard}.parquet", index=False)


if __name__ == "__main__":
    main()
