"""Rotatie-backtest stap 1 (per deel van de wallets), vooraf vastgelegd 4 okt 13:15.
Keuzemomenten T: elke 14 dagen vanaf 20-10-2025 zolang T + 28 d <= 18-8-2026.
Per wallet, per T en per terugkijkperiode L (28/42/84 d): statistieken van trades die in [T-L, T) open én dicht gingen
(bot-basis: instap = eerste fill, 0,32% kosten). Vooruit: trades die in [T, T+H) openen (H = 14 en 28 d), potje-rendement
met inzet = potje / K (K uit de terugkijkperiode), stop bij -20% op het potje.
Alleen rijen met >= 30 trades in de terugkijkperiode worden bewaard.
Gebruik: python -m bt.rot_stats <deel> <aantal> <universe.parquet> <fillsmap> <beurzenmap> <uitmap>
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

from bt import data
from bt.bot_stats import KOSTEN, bot_trades
from bt.engine import to_base

DAG = 86_400_000
EIND = pd.Timestamp("2026-08-19").value // 10**6
T0 = pd.Timestamp("2025-10-20").value // 10**6
TS = [t for t in range(T0, EIND, 14 * DAG) if t + 28 * DAG <= EIND]
LS = [28, 42, 84]
HS = [14, 28]
STOP = -0.20


def k90(o, c):
    ev = sorted([(x, 1) for x in o] + [(x, -1) for x in c])
    s, cs = 0, []
    for _, d in ev:
        s += d
        cs.append(s)
    return max(1, int(np.ceil(np.percentile(cs, 90)))) if cs else 1


def vooruit(r, k):
    """Potje-rendement met stop: cumulatief r/k in sluitvolgorde, stopt bij -20%."""
    tot = 0.0
    for x in r:
        tot += x / k
        if tot <= STOP:
            return STOP, True
    return tot, False


def main():
    shard, n, upath, d, vdir, out = int(sys.argv[1]), int(sys.argv[2]), *sys.argv[3:7]
    os.makedirs(out, exist_ok=True)
    kraken = set(data.beurzen(vdir).get("kraken", []))
    u = pd.read_parquet(upath).sort_values("address").iloc[shard::n]
    rows = []
    for chunk in np.array_split(u.address.values, max(1, len(u) // 400)):
        f = data.fills(d, set(chunk))
        f = f[f.ts < EIND]
        for a, x in f.groupby("address"):
            fl = [{"time": int(t), "coin": c, "start": s, "after": af, "px": p}
                  for t, c, s, af, p in zip(x.ts, x.coin, x.start, x.after, x.px)]
            bt_ = bot_trades(fl)
            if len(bt_) < 30:
                continue
            o = np.array([t[1] for t in bt_], dtype=np.int64)
            c = np.array([t[2] for t in bt_], dtype=np.int64)
            r = np.array([t[3] * (t[6] / t[4] - 1) - KOSTEN for t in bt_])
            kr = np.array([to_base(t[0]) in kraken for t in bt_])
            fts = x.ts.values
            for T in TS:
                for L in LS:
                    m = (o >= T - L * DAG) & (c < T)
                    nn = int(m.sum())
                    if nn < 30:
                        continue
                    lo, lc, lr = o[m], c[m], r[m]
                    k = k90(lo, lc)
                    fm = fts[(fts >= T - L * DAG) & (fts < T)]
                    fpd = float(pd.Series(fm // DAG).value_counts().median()) if len(fm) else 0.0
                    rec = {"address": a, "T": T, "L": L, "n": nn, "tpw": round(nn / (L / 7), 2), "K": k,
                           "gem_r_pct": round(100 * lr.mean(), 3), "winst_pct": round(100 * (lr > 0).mean(), 1),
                           "per_maand_pct": round(100 * lr.sum() / k / (L / 30.44), 2),
                           "houdtijd_uur": round(float(np.median((lc - lo) / 3.6e6)), 2),
                           "kraken_pct": round(100 * kr[m].mean(), 1), "fills_per_dag": fpd,
                           "dagen_sinds_laatste": round((T - lc.max()) / DAG, 1)}
                    for H in HS:
                        fwd = (o >= T) & (o < T + H * DAG)
                        idx = np.argsort(c[fwd])
                        tot, gestopt = vooruit(r[fwd][idx], k)
                        rec[f"v{H}_pct"] = round(100 * tot, 3)
                        rec[f"v{H}_n"] = int(fwd.sum())
                        rec[f"v{H}_stop"] = gestopt
                    rows.append(rec)
        print(f"{len(rows)} rijen", flush=True)
    pd.DataFrame(rows).to_parquet(f"{out}/rot_{shard}.parquet", index=False)


if __name__ == "__main__":
    main()
