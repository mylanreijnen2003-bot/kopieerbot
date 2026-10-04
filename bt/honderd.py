"""Wallets met >= 3 'grote' trades (vooraf vastgelegd 4 okt 19:20). Dataset 28-7-2025 t/m 18-8-2026.
Trade = plat -> plat. Koersbeweging = richting x (gem. uitstap / gem. instap - 1) (hun eigen prijzen).
Niveaus: >= +100% koers (= +100% op inleg zonder hefboom, Bitvavo), >= +50% (= +100% bij 2x, Kraken-max),
>= +20% (= +100% bij 5x), >= +10% (= +100% bij 10x). Hefboom zit niet in de data, dus inleg-rendement is omgerekend.
Stappen: `stats <deel> <aantal> <universe> <fillsmap> <beurzenmap> <uit>` | `kies <statsmap> <uit>`
"""

from __future__ import annotations

import glob
import os
import sys
import time

import numpy as np
import pandas as pd

from bt import data
from bt.bot_stats import bot_trades
from bt.engine import to_base

NIV = [1.0, 0.5, 0.2, 0.1]


def stats(shard, n, upath, d, vdir, out):
    os.makedirs(out, exist_ok=True)
    v = data.beurzen(vdir)
    kraken, bitvavo = set(v.get("kraken", [])), set(v.get("bitvavo", []))
    u = pd.read_parquet(upath).sort_values("address").iloc[shard::n]
    rows, groot = [], []
    for chunk in np.array_split(u.address.values, max(1, len(u) // 2000)):
        f = data.fills(d, set(chunk))
        for a, x in f.groupby("address"):
            fl = [{"time": int(t), "coin": c, "start": s, "after": af, "px": p}
                  for t, c, s, af, p in zip(x.ts, x.coin, x.start, x.after, x.px)
                  if not str(c).startswith(("#", "@"))]   # '#…' = uitkomst-/gokmarkten, '@…' = spot
            bt_ = bot_trades(fl)
            if len(bt_) < 3:
                continue
            r = np.array([t[3] * (t[6] / t[5] - 1) for t in bt_])
            if (r >= 0.1).sum() < 3:
                continue
            rec = {"address": a, "trades": len(bt_), "winst_pct": round(100 * (r > 0).mean(), 1),
                   "gem_r_pct": round(100 * r.mean(), 2), "som_r_pct": round(100 * r.sum(), 1),
                   "verlies_trades_10pct": int((r <= -0.1).sum()),
                   "houdtijd_uur": round(float(np.median([(t[2] - t[1]) / 3.6e6 for t in bt_])), 1),
                   "eerste": str(pd.to_datetime(min(t[1] for t in bt_), unit="ms").date()),
                   "laatste": str(pd.to_datetime(max(t[2] for t in bt_), unit="ms").date())}
            for nv in NIV:
                rec[f"n_{int(nv * 100)}"] = int((r >= nv).sum())
            rows.append(rec)
            for t, ri in zip(bt_, r):
                if ri >= 0.5:
                    b = to_base(t[0])
                    groot.append({"address": a, "munt": b, "richting": "long" if t[3] > 0 else "short",
                                  "open": str(pd.to_datetime(t[1], unit="ms").date()),
                                  "dagen": round((t[2] - t[1]) / 86_400_000, 1), "koers_pct": round(100 * ri, 1),
                                  "kraken": b in kraken, "bitvavo": b in bitvavo})
        print(len(rows), "wallets", flush=True)
    pd.DataFrame(rows).to_parquet(f"{out}/h_{shard}.parquet", index=False)
    pd.DataFrame(groot).to_parquet(f"{out}/g_{shard}.parquet", index=False)


def kies(sdir, out):
    os.makedirs(out, exist_ok=True)
    s = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sdir}/**/h_*.parquet", recursive=True)], ignore_index=True)
    g = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sdir}/**/g_*.parquet", recursive=True)], ignore_index=True)
    tel = {"wallets_met_>=3_trades_10pct": len(s)}
    for nv in [100, 50, 20, 10]:
        tel[f"wallets_>=3x_{nv}pct"] = int((s[f"n_{nv}"] >= 3).sum())
    pd.DataFrame([tel]).to_csv(f"{out}/telling.csv", index=False)
    s["kort"] = s.address.str[:6] + "…" + s.address.str[-4:]
    for nv in [100, 50]:
        x = s[s[f"n_{nv}"] >= 3].sort_values([f"n_{nv}", "som_r_pct"], ascending=False)
        x.to_csv(f"{out}/wallets_{nv}pct.csv", index=False)
    s[s.n_20 >= 3].sort_values(["n_20", "som_r_pct"], ascending=False).head(500).to_csv(f"{out}/wallets_20pct_top500.csv", index=False)
    g.to_csv(f"{out}/grote_trades.csv", index=False)
    print(tel)
    print(s[s.n_100 >= 3].sort_values("n_100", ascending=False).drop(columns=["address"]).head(30).to_string(index=False))


if __name__ == "__main__":
    c = sys.argv[1]
    if c == "stats":
        stats(int(sys.argv[2]), int(sys.argv[3]), *sys.argv[4:8])
    else:
        kies(*sys.argv[2:4])
