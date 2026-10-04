"""Papier v2 groep F: 'constantheid' (vooraf vastgelegd 4 okt 17:15, uit rotatie2: L=84 d, top 10, elke 14 d).
Kandidaten: Hyperliquid-ranglijst, accountwaarde >= $1000, all-time PnL > 0, maandvolume $10k-$50M.
Per kandidaat de laatste 84 dagen (API). Eisen: >= 30 trades, mediaan houdtijd >= 12 u, gem. >= 1% per trade na kosten
(bot-basis), winstgevend, K <= 5, >= 70% Kraken, <= 150 fills/dag, laatste trade <= 7 d. Rangorde: winst-% van de trades.
Top 10 actief, 11-20 reserve.
Stappen: `kand <uit>` | `api <deel> <aantal> <kand.parquet> <kraken.json> <uit>` | `kies <apimap> <uit>`
"""

from __future__ import annotations

import glob
import json
import os
import signal
import sys
import time

import numpy as np
import pandas as pd
import requests

from bot import hl
from bt.engine import to_base
from bt.p2_common import DAG, UUR, k90, r_bot, trades_open

NU = int(time.time() * 1000)
L = 84


def kand(out):
    os.makedirs(out, exist_ok=True)
    js = requests.get("https://stats-data.hyperliquid.xyz/Mainnet/leaderboard", timeout=120).json()
    rows = js.get("leaderboardRows", js) if isinstance(js, dict) else js
    uit = []
    for x in rows:
        wp = {k: v for k, v in x.get("windowPerformances", [])}
        uit.append({"address": x["ethAddress"].lower(), "av": float(x.get("accountValue") or 0),
                    "all": float(wp.get("allTime", {}).get("pnl") or 0), "vlm": float(wp.get("month", {}).get("vlm") or 0)})
    df = pd.DataFrame(uit)
    k = df[(df.av >= 1000) & (df["all"] > 0) & (df.vlm >= 1e4) & (df.vlm <= 5e7)]
    print("ranglijst", len(df), "kandidaten", len(k))
    k[["address", "av"]].to_parquet(f"{out}/kand.parquet", index=False)


def te_lang(*_):
    raise TimeoutError


def api(shard, n, kpath, kjson, out):
    os.makedirs(out, exist_ok=True)
    kraken = set(json.load(open(kjson)))
    k = pd.read_parquet(kpath).sort_values("address").iloc[shard::n]
    signal.signal(signal.SIGALRM, te_lang)
    rows = []
    for i, w in enumerate(k.itertuples()):
        signal.alarm(60)
        try:
            fl = [f for f in hl.fills(w.address, NU - L * DAG, NU) if f["kind"] == "perp"]
        except Exception:  # noqa: BLE001
            continue
        finally:
            signal.alarm(0)
        dicht, _ = trades_open(fl, NU - L * DAG)
        if len(dicht) < 30:
            continue
        t = pd.DataFrame(dicht)
        r = t.apply(r_bot, axis=1)
        kk = k90(t.open, t.sluit)
        dagen = pd.Series([f["time"] // DAG for f in fl]).value_counts()
        rows.append({"address": w.address, "av": w.av, "n": len(t), "K": kk,
                     "winst_pct": round(100 * (r > 0).mean(), 1), "gem_r_pct": round(100 * r.mean(), 2),
                     "per_maand_pct": round(100 * r.sum() / kk / (L / 30.44), 2),
                     "houdtijd_uur": round(float(((t.sluit - t.open) / UUR).median()), 1),
                     "kraken_pct": round(100 * np.mean([to_base(c) in kraken for c in t.coin]), 1),
                     "fills_per_dag": float(dagen.median()),
                     "dagen_sinds_laatste": round((NU - max(f["time"] for f in fl)) / DAG, 1)})
        if i % 200 == 0:
            print(i, len(rows), flush=True)
    pd.DataFrame(rows).to_parquet(f"{out}/f_{shard}.parquet", index=False)


def kies(adir, out):
    os.makedirs(out, exist_ok=True)
    s = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{adir}/**/f_*.parquet", recursive=True)], ignore_index=True)
    ok = s[(s.houdtijd_uur >= 12) & (s.gem_r_pct >= 1.0) & (s.per_maand_pct > 0) & (s.K <= 5) & (s.kraken_pct >= 70)
           & (s.fills_per_dag <= 150) & (s.dagen_sinds_laatste <= 7)]
    top = ok.sort_values(["winst_pct", "per_maand_pct"], ascending=False).head(20)
    print("met >= 30 trades", len(s), "door eisen", len(ok))
    json.dump({"gemaakt": NU, "n_actief": 10, "lijst": top.to_dict("records")}, open(f"{out}/selectie_F.json", "w"),
              indent=1, default=str)
    top.assign(kort=top.address.str[:6] + "…" + top.address.str[-4:]).drop(columns=["address"]).to_csv(
        f"{out}/selectie_F.csv", index=False)
    print(top.drop(columns=["address"]).to_string(index=False))


if __name__ == "__main__":
    c = sys.argv[1]
    if c == "kand":
        kand(sys.argv[2])
    elif c == "api":
        api(int(sys.argv[2]), int(sys.argv[3]), *sys.argv[4:7])
    else:
        kies(*sys.argv[2:4])
