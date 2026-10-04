"""Historie van de recent-top 30 (19-8 t/m 3-10) in de dataset (28-7-2025 t/m 18-8-2026), vooraf vastgelegd 4 okt 13:50.
Per trader: per maand trades, winst-% en winst op het potje (bot-basis: eerste fill, 0,32% kosten; inzet = potje / K,
K uit de recent-test), plus totaal, winstmaanden, grootste daling en eerste/laatste trade. Iedereen wordt getoond.
Gebruik: python -m bt.historie uni <top60.csv> <uit>  |  python -m bt.historie stats <top60.csv> <fillsmap> <uit>
"""

import os
import sys

import numpy as np
import pandas as pd

from bt import data
from bt.bot_stats import KOSTEN, bot_trades

EIND = pd.Timestamp("2026-08-19").value // 10**6


def uni(top, out):
    t = pd.read_csv(top).head(30)
    os.makedirs(out, exist_ok=True)
    pd.DataFrame({"address": t.address.str.lower()}).to_parquet(f"{out}/universe.parquet", index=False)


def stats(top, d, out):
    os.makedirs(out, exist_ok=True)
    t = pd.read_csv(top).head(30).reset_index(drop=True)
    t["address"] = t.address.str.lower()
    f = data.fills(d, set(t.address))
    f = f[f.ts < EIND]
    rows, maand = [], []
    for i, w in t.iterrows():
        x = f[f.address == w.address]
        k = max(1, int(w.bot_K))
        rec = {"plek": i + 1, "kort": w.kort, "recent_trades": w.trades, "recent_bot_per_maand_pct": w.bot_per_maand_pct,
               "recent_hand_1u_per_maand_pct": w.hand_1u_per_maand_pct, "K": k, "fills_dataset": len(x)}
        if len(x):
            fl = [{"time": int(a), "coin": c, "start": s, "after": af, "px": p}
                  for a, c, s, af, p in zip(x.ts, x.coin, x.start, x.after, x.px)]
            bt_ = bot_trades(fl)
            if bt_:
                df = pd.DataFrame({"open": [b[1] for b in bt_], "sluit": [b[2] for b in bt_],
                                   "r": [b[3] * (b[6] / b[4] - 1) - KOSTEN for b in bt_],
                                   "r_hun": [b[3] * (b[6] / b[5] - 1) for b in bt_]}).sort_values("sluit")
                df["maand"] = pd.to_datetime(df.sluit, unit="ms").dt.strftime("%Y-%m")
                g = df.groupby("maand")
                m = pd.DataFrame({"trades": g.size(), "winst_pct": (100 * g.r.apply(lambda s: (s > 0).mean())).round(0),
                                  "bot_pct": (100 * g.r.sum() / k).round(1), "hun_pct": (100 * g.r_hun.sum() / k).round(1)})
                for mm, row in m.iterrows():
                    maand.append({"plek": i + 1, "kort": w.kort, "maand": mm, **row.to_dict()})
                eq = 1 + (df.r / k).cumsum()
                dd = float((eq / np.maximum.accumulate(np.concatenate([[1.0], eq.values]))[1:] - 1).min())
                rec.update({"eerste_trade": pd.to_datetime(df.open.min(), unit="ms").date(),
                            "laatste_trade": pd.to_datetime(df.sluit.max(), unit="ms").date(),
                            "trades": len(df), "maanden": len(m), "winstmaanden": int((m.bot_pct > 0).sum()),
                            "winst_pct_trades": round(100 * (df.r > 0).mean(), 1),
                            "gem_r_pct": round(100 * df.r.mean(), 2),
                            "gem_per_maand_pct": round(m.bot_pct.mean(), 1),
                            "mediaan_per_maand_pct": round(m.bot_pct.median(), 1),
                            "slechtste_maand_pct": round(m.bot_pct.min(), 1),
                            "totaal_pct": round(100 * df.r.sum() / k, 1), "grootste_daling_pct": round(100 * dd, 1),
                            "houdtijd_uur": round(float(((df.sluit - df.open) / 3.6e6).median()), 1)})
        rows.append(rec)
    s = pd.DataFrame(rows)
    s.to_csv(f"{out}/samenvatting.csv", index=False)
    pd.DataFrame(maand).to_csv(f"{out}/per_maand.csv", index=False)
    print(s.to_string(index=False))


if __name__ == "__main__":
    if sys.argv[1] == "uni":
        uni(*sys.argv[2:4])
    else:
        stats(*sys.argv[2:5])
