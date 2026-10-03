"""Praktisch-selectie stap 1 (per deel): per trader de trades doorrekenen zoals de bot ze echt pakt.
Bot-instap = de EERSTE fill van de trader (wij nemen meteen de volle inzet), bot-uitstap = hun gem. uitstapprijs.
Kosten 0,32% per trade (2x 0,05% taker + 2x 0,11% slippage, gemeten in de replay). Data t/m 18-8-2026.
Ook: aandeel van de eindpositie dat in de eerste fill zit (instap in één keer = goed te kopiëren).
Gebruik: python -m bt.bot_stats <deel> <aantal> <universe.parquet> <fillsmap> <beurzenmap> <uitmap>
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

from bt import data
from bt.brede_stats import pot_stats
from bt.engine import to_base

EIND = pd.Timestamp("2026-08-19").value // 10**6
KOSTEN = 0.0032


def bot_trades(fl):
    """(coin, open_t, sluit_t, richting, eerste_prijs, gem_instap, gem_uitstap, eerste_aandeel, bijkopen)."""
    st, out = {}, []
    for f in fl:
        c, s, a, px = f["coin"], f["start"], f["after"], f["px"]

        def nieuw():
            st[c] = {"dir": 1 if a > 0 else -1, "first": px, "q1": abs(a), "iq": abs(a), "ic": abs(a) * px,
                     "uq": 0.0, "uc": 0.0, "t": f["time"], "max": abs(a), "adds": 0}
        if s == 0 and a != 0:
            nieuw()
            continue
        if c not in st:
            continue
        p = st[c]
        flip = a != 0 and a * s < 0
        if abs(a) > abs(s) and not flip:
            p["iq"] += abs(a) - abs(s)
            p["ic"] += (abs(a) - abs(s)) * px
            p["adds"] += 1
            p["max"] = max(p["max"], abs(a))
        else:
            q = abs(s) if (a == 0 or flip) else abs(s) - abs(a)
            p["uq"] += q
            p["uc"] += q * px
        if a == 0 or flip:
            out.append((c, p["t"], f["time"], p["dir"], p["first"], p["ic"] / p["iq"], p["uc"] / p["uq"],
                        p["q1"] / p["max"], p["adds"]))
            st.pop(c)
            if flip:
                nieuw()
    return out


def main():
    shard, n, upath, d, vdir, out = int(sys.argv[1]), int(sys.argv[2]), *sys.argv[3:7]
    os.makedirs(out, exist_ok=True)
    kraken = set(data.beurzen(vdir).get("kraken", []))
    u = pd.read_parquet(upath).sort_values("address").iloc[shard::n]
    rows = []
    for chunk in np.array_split(u.address.values, max(1, len(u) // 300)):
        f = data.fills(d, set(chunk))
        f = f[f.ts < EIND]
        for a, x in f.groupby("address"):
            fl = [{"time": int(t), "coin": c, "start": s, "after": af, "px": p}
                  for t, c, s, af, p in zip(x.ts, x.coin, x.start, x.after, x.px)]
            bt_ = bot_trades(fl)
            if len(bt_) < int(os.environ.get("MIN_TRADES", "30")):
                continue
            hun = [(c, o, cl, r * (po / pv - 1) - KOSTEN) for c, o, cl, r, p1, pv, po, q, ad in bt_]
            bot = [(c, o, cl, r * (po / p1 - 1) - KOSTEN) for c, o, cl, r, p1, pv, po, q, ad in bt_]
            rows.append({"address": a,
                         "kraken_pct": round(100 * np.mean([to_base(t[0]) in kraken for t in bt_]), 1),
                         "laatste_trade": max(t[2] for t in bt_),
                         "eerste_fill_aandeel": round(float(np.median([t[7] for t in bt_])), 2),
                         "bijkopen_mediaan": float(np.median([t[8] for t in bt_])),
                         **pot_stats(hun, "hun_"), **pot_stats(bot, "bot_")})
        print(f"{len(rows)} wallets klaar", flush=True)
    pd.DataFrame(rows).to_parquet(f"{out}/bot_{shard}.parquet", index=False)


if __name__ == "__main__":
    main()
