"""Brede scan stap 4 (per deel van de wallets): vaste-inzet-statistieken in de keuzeperiode (t/m 18-8-2026).
Gelijk potje per trader; inzet per trade = potje / K (K = 90e percentiel gelijktijdig open trades).
Gebruik: python -m bt.brede_stats <deel> <aantal> <universum.parquet> <datamap> <beurzenmap> <uitmap>
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

from bt import data
from bt.engine import to_base
from bt.vast_select import trades_pct

EIND = pd.Timestamp("2026-08-19").value // 10**6


def pot_stats(tr, prefix=""):
    """tr: lijst (coin, open, sluit, r). Statistieken op een gelijk potje."""
    if len(tr) < 2:
        return {f"{prefix}trades": len(tr)}
    df = pd.DataFrame(tr, columns=["coin", "open", "sluit", "r"]).sort_values("sluit")
    ev = sorted([(o, 1) for o in df.open] + [(s, -1) for s in df.sluit])
    c, cs = 0, []
    for _, d in ev:
        c += d
        cs.append(c)
    k = max(1, int(np.ceil(np.percentile(cs, 90))))
    eq = 1 + (df.r / k).cumsum()
    dd = float((eq / np.maximum.accumulate(np.concatenate([[1.0], eq.values]))[1:] - 1).min())
    m = (df.r / k).groupby(pd.to_datetime(df.sluit, unit="ms").dt.strftime("%Y-%m")).sum()
    return {f"{prefix}trades": len(df), f"{prefix}winst_pct": round(100 * (df.r > 0).mean(), 1),
            f"{prefix}gem_r_pct": round(100 * df.r.mean(), 2), f"{prefix}K": k,
            f"{prefix}maanden": len(m), f"{prefix}verliesmaanden": int((m <= 0).sum()),
            f"{prefix}gem_maand_pct": round(100 * m.mean(), 2), f"{prefix}slechtste_maand_pct": round(100 * m.min(), 2),
            f"{prefix}maxdd_pct": round(100 * dd, 2), f"{prefix}totaal_pct": round(100 * (eq.iloc[-1] - 1), 1),
            f"{prefix}houdtijd_uur": round(float(((df.sluit - df.open) / 3.6e6).median()), 1)}


def main():
    shard, n, upath, d, vdir, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]
    os.makedirs(out, exist_ok=True)
    v = data.beurzen(vdir)
    handelbaar = set(v.get("kraken", [])) | set(v.get("bitvavo", []))
    u = pd.read_parquet(upath).sort_values("address").iloc[shard::n]
    rows = []
    for chunk in np.array_split(u.address.values, max(1, len(u) // 300)):
        f = data.fills(d, set(chunk))
        f = f[f.ts < EIND]
        for a, x in f.groupby("address"):
            fl = [{"time": int(t), "coin": c, "start": s, "after": af, "px": p}
                  for t, c, s, af, p in zip(x.ts, x.coin, x.start, x.after, x.px)]
            tr = trades_pct(fl)
            if len(tr) < 50:
                continue
            hb = np.mean([to_base(t[0]) in handelbaar for t in tr])
            rows.append({"address": a, "handelbaar_pct": round(100 * hb, 1), "laatste_trade": max(t[2] for t in tr),
                         **pot_stats(tr)})
        print(f"{len(rows)} wallets klaar", flush=True)
    pd.DataFrame(rows).to_parquet(f"{out}/stats_{shard}.parquet", index=False)


if __name__ == "__main__":
    main()
