"""Recent (vooraf vastgelegd 4 okt 09:15): wie doet het goed op 19-8-2026 t/m gisteren? (achteraf-ranglijst, geen voorspelling)
Stap 1 `lb`: kandidaten uit de Hyperliquid-ranglijst (maand-PnL > 0, accountwaarde >= $100).
Stap 2 `stats <deel> <aantal> <kandidaten.parquet> <beurzenmap> <uitmap>`: fills 19-8 t/m gisteren via de API,
trades plat->plat (alleen nieuw geopend vanaf 19-8), statistieken op hun prijs en op bot-basis (eerste fill, 0,32% kosten).
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time

import numpy as np
import pandas as pd
import requests

from bot import hl
from bt import data
from bt.bot_stats import KOSTEN, bot_trades
from bt.brede_stats import pot_stats
from bt.engine import to_base

START = pd.Timestamp("2026-08-19").value // 10**6
NU = int(time.time() * 1000) // hl.DAY * hl.DAY
DAGEN = (NU - START) / hl.DAY


def lb(out):
    r = requests.get("https://stats-data.hyperliquid.xyz/Mainnet/leaderboard", timeout=120)
    r.raise_for_status()
    js = r.json()
    rows = js.get("leaderboardRows", js) if isinstance(js, dict) else js
    print("rijen", len(rows), "voorbeeld", json.dumps(rows[0])[:600], flush=True)
    out_rows = []
    for x in rows:
        wp = {k: v for k, v in x.get("windowPerformances", [])}
        m, a = wp.get("month", {}), wp.get("allTime", {})
        out_rows.append({"address": x["ethAddress"].lower(), "accountwaarde": float(x.get("accountValue") or 0),
                         "maand_pnl": float(m.get("pnl") or 0), "maand_roi": float(m.get("roi") or 0),
                         "maand_vlm": float(m.get("vlm") or 0), "alltime_pnl": float(a.get("pnl") or 0)})
    df = pd.DataFrame(out_rows)
    print("alle accounts", len(df), flush=True)
    k = df[(df.maand_pnl > 0) & (df.accountwaarde >= 100)].reset_index(drop=True)
    print("kandidaten (maand-PnL > 0, account >= $100)", len(k), flush=True)
    os.makedirs(out, exist_ok=True)
    k.to_parquet(f"{out}/kandidaten.parquet", index=False)
    pd.DataFrame([{"accounts": len(df), "kandidaten": len(k)}]).to_csv(f"{out}/lb_telling.csv", index=False)


def te_lang(*_):
    raise TimeoutError


def stats(shard, n, kpath, vdir, out):
    os.makedirs(out, exist_ok=True)
    kraken = set(data.beurzen(vdir).get("kraken", []))
    k = pd.read_parquet(kpath).sort_values("address").iloc[shard::n]
    signal.signal(signal.SIGALRM, te_lang)
    rows, t0 = [], time.time()
    for i, w in enumerate(k.itertuples()):
        signal.alarm(150)
        try:
            fl = hl.fills(w.address, START, NU)
        except Exception as exc:  # noqa: BLE001
            rows.append({"address": w.address, "status": f"fout:{type(exc).__name__}"})
            continue
        finally:
            signal.alarm(0)
        perp = [f for f in fl if f["kind"] == "perp"]
        rec = {"address": w.address, "accountwaarde": w.accountwaarde, "maand_pnl": w.maand_pnl, "fills": len(fl),
               "status": "ok"}
        if len(fl) >= 9500 and fl and fl[0]["time"] > START + hl.DAY:
            rec["status"] = "te_veel_fills"
        if perp:
            dagen = pd.Series(1, index=pd.to_datetime([f["time"] for f in perp], unit="ms").date).groupby(level=0).sum()
            rec["fills_per_dag_mediaan"] = float(dagen.median())
        bt_ = [t for t in bot_trades(perp) if t[1] >= START]
        rec["trades"] = len(bt_)
        if len(bt_) >= 30:
            hun = [(c, o, cl, r * (po / pv - 1)) for c, o, cl, r, p1, pv, po, q, ad in bt_]
            bot = [(c, o, cl, r * (po / p1 - 1) - KOSTEN) for c, o, cl, r, p1, pv, po, q, ad in bt_]
            hs, bs = pot_stats(hun, "hun_"), pot_stats(bot, "bot_")
            kk = bs["bot_K"]
            rec.update(hs)
            rec.update(bs)
            rec["trades_per_week"] = round(len(bt_) / (DAGEN / 7), 2)
            rec["hun_per_maand_pct"] = round(100 * sum(t[3] for t in hun) / kk / (DAGEN / 30.44), 2)
            rec["bot_per_maand_pct"] = round(100 * sum(t[3] for t in bot) / kk / (DAGEN / 30.44), 2)
            rec["kraken_pct"] = round(100 * np.mean([to_base(t[0]) in kraken for t in bt_]), 1)
            rec["eerste_fill_aandeel"] = round(float(np.median([t[7] for t in bt_])), 2)
            rec["munten"] = ",".join(pd.Series([to_base(t[0]) for t in bt_]).value_counts().head(5).index)
            rec["trades_json"] = json.dumps([[c, o, cl, r, p1, po] for c, o, cl, r, p1, pv, po, q, ad in bt_])
        rows.append(rec)
        if i % 100 == 0:
            print(f"{i}/{len(k)} wallets, {time.time() - t0:.0f}s", flush=True)
    pd.DataFrame(rows).to_parquet(f"{out}/recent_{shard}.parquet", index=False)


if __name__ == "__main__":
    if sys.argv[1] == "lb":
        lb(sys.argv[2])
    else:
        stats(int(sys.argv[2]), int(sys.argv[3]), *sys.argv[4:7])
