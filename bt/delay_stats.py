"""Handmatig-route stap 1 (per deel): voor de kandidaten uit de brede scan de trades herberekenen alsof je
15 min na de trader in- en uitstapt. Keuzeperiode heeft alleen uurkaarsen: prijs = open van het eerste uur dat begint
op of na (tijd trader + 15 min), dus 15-75 min te laat (streng). Kosten 0,2% per trade.
Gebruik: python -m bt.delay_stats <deel> <aantal> <alle_stats.csv> <fillsmap> <bars.parquet> <uitmap>
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

from bt import data
from bt.brede_stats import pot_stats
from bt.delay_test import trades_detail

EIND = pd.Timestamp("2026-08-19").value // 10**6
HOUR = 3_600_000
KOSTEN = 0.002
VERTRAGING = 15 * 60_000


def kandidaten(path):
    s = pd.read_csv(path)
    return s[(s.maanden >= 3) & (s.handelbaar_pct >= 70) & (s.laatste_trade >= EIND - 30 * 86_400_000)
             & (s.gem_maand_pct > 0)]


def laad_bars(path):
    b = pd.read_parquet(path)
    return {c: (x.hour.to_numpy(np.int64), x.o.to_numpy()) for c, x in b.sort_values("hour").groupby("coin")}


def prijs(bars, coin, ts):
    if coin not in bars:
        return None
    h, o = bars[coin]
    i = np.searchsorted(h, ts + VERTRAGING)
    return float(o[i]) if i < len(h) else None


def main():
    shard, n, spath, d, bpath, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]
    os.makedirs(out, exist_ok=True)
    kand = kandidaten(spath).sort_values("address").iloc[shard::n]
    bars = laad_bars(bpath)
    f = data.fills(d, set(kand.address))
    f = f[f.ts < EIND]
    rows = []
    for _, w in kand.iterrows():
        x = f[f.address == w.address]
        fl = [{"time": int(t), "coin": c, "start": s, "after": a, "px": p}
              for t, c, s, a, p in zip(x.ts, x.coin, x.start, x.after, x.px)]
        tr = []
        for coin, o, c, richting, pin, pout in trades_detail(fl):
            a, b = prijs(bars, coin, o), prijs(bars, coin, c)
            if a and b:
                tr.append((coin, o, c, richting * (b / a - 1) - KOSTEN))
        st = pot_stats(tr, "d_")
        rows.append({"address": w.address, "handelbaar_pct": w.handelbaar_pct, "laatste_trade": w.laatste_trade,
                     "K": w.K, "gem_maand_pct_origineel": w.gem_maand_pct, **st})
    pd.DataFrame(rows).to_parquet(f"{out}/dstats_{shard}.parquet", index=False)
    print(len(rows), "wallets")


if __name__ == "__main__":
    main()
