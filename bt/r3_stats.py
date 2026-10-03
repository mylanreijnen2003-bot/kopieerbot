"""Ronde 3 stap 1 (per deel): per wallet uit het universum (>= 3 mnd, >= 30 trades, >= 2/week) de trades
doorrekenen met >= 1 uur vertraging (uurkaarsen: prijs = open van het eerste uur dat begint op of na tijd + 60 min,
dus 1-2 uur te laat). Kosten 0,2% per trade. Alleen data t/m 18-8-2026.
Gebruik: python -m bt.r3_stats <deel> <aantal> <universe.parquet> <fillsmap> <bars.parquet> <beurzenmap> <uitmap>
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

from bt import data
from bt.brede_stats import pot_stats
from bt.delay_test import trades_detail
from bt.engine import to_base

EIND = pd.Timestamp("2026-08-19").value // 10**6
KOSTEN = 0.002
VERTRAGING = 60 * 60_000


def main():
    shard, n, upath, d, bpath, vdir, out = int(sys.argv[1]), int(sys.argv[2]), *sys.argv[3:8]
    os.makedirs(out, exist_ok=True)
    v = data.beurzen(vdir)
    handelbaar = set(v.get("kraken", [])) | set(v.get("bitvavo", []))
    b = pd.read_parquet(bpath)
    bars = {c: (x.hour.to_numpy(np.int64), x.o.to_numpy()) for c, x in b.sort_values("hour").groupby("coin")}

    def prijs(coin, ts):
        if coin not in bars:
            return None
        h, o = bars[coin]
        i = np.searchsorted(h, ts + VERTRAGING)
        return float(o[i]) if i < len(h) else None

    u = pd.read_parquet(upath).sort_values("address").iloc[shard::n]
    rows = []
    for chunk in np.array_split(u.address.values, max(1, len(u) // 300)):
        f = data.fills(d, set(chunk))
        f = f[f.ts < EIND]
        for a, x in f.groupby("address"):
            fl = [{"time": int(t), "coin": c, "start": s, "after": af, "px": p}
                  for t, c, s, af, p in zip(x.ts, x.coin, x.start, x.after, x.px)]
            det = trades_detail(fl)
            if len(det) < 30:
                continue
            orig = [(c, o, cl, r * (po / pi - 1) - KOSTEN) for c, o, cl, r, pi, po in det]
            vert = []
            for c, o, cl, r, pi, po in det:
                pa, pb = prijs(c, o), prijs(c, cl)
                if pa and pb:
                    vert.append((c, o, cl, r * (pb / pa - 1) - KOSTEN))
            hb = np.mean([to_base(t[0]) in handelbaar for t in det])
            rows.append({"address": a, "handelbaar_pct": round(100 * hb, 1), "laatste_trade": max(t[2] for t in det),
                         **pot_stats(orig), **pot_stats(vert, "d_")})
        print(f"{len(rows)} wallets klaar", flush=True)
    pd.DataFrame(rows).to_parquet(f"{out}/r3_{shard}.parquet", index=False)


if __name__ == "__main__":
    main()
