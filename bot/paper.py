"""Papieren kopieerbot H3b (dagelijks): kopieert op papier de perps-trades van de 60 gekozen wallets.

Verwerkt alle hele uren t/m gisteren 23:59 UTC. Stand in <map>/state.json, dagregels in <map>/ledger.csv.
Gebruik: python -m bot.paper <map>
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd

from bot import hl, sim

START = "2026-08-19"


def ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def dstr(t: int) -> str:
    return datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    state_path, ledger_path = f"{d}/state.json", f"{d}/ledger.csv"
    if os.path.exists(state_path):
        state = json.load(open(state_path))
    else:
        sel = json.load(open("data/selection.json"))
        state = {"regels": "H3b", "next_hour": ms(START),
                 "wallets": {w["address"]: {"pot": sim.new_pot(), "last_t": ms(START) - 1, "last_tid": 0,
                                            "median_av": w["median_av"], "onvolledig": False}
                             for w in sel["wallets"]}}
    s = state["next_hour"]
    end = int(datetime.now(timezone.utc).timestamp() * 1000) // hl.DAY * hl.DAY   # vandaag 00:00 UTC
    if end <= s:
        print("niets te doen")
        return

    fills, avs = {}, {}
    for i, (a, w) in enumerate(state["wallets"].items()):
        if w["pot"]["dead"]:
            continue
        try:
            fl = hl.fills(a, w["last_t"], end - 1)
            avs[a] = hl.av_points(a, perp_only=True)[0]
        except Exception as exc:  # noqa: BLE001
            print("ophalen mislukt", a, exc)
            fl, avs[a] = [], []
        if len(fl) >= 9900:
            w["onvolledig"] = True
        fills[a] = [f for f in fl if (f["time"], f["tid"]) > (w["last_t"], w["last_tid"])]
        if i % 10 == 0:
            print(f"{i}/{len(state['wallets'])} wallets opgehaald", flush=True)

    coins = {f["coin"] for fl in fills.values() for f in fl if f["kind"] == "perp"}
    coins |= {c for w in state["wallets"].values() for c in w["pot"]["units"]}
    bars = {c: hl.candles(c, s, end) for c in sorted(coins)}
    fund = {c: hl.funding(c, s, end) for c in sorted(coins)}
    print(f"{len(coins)} munten, uurkaarsen en funding opgehaald", flush=True)

    rows = []
    for a, w in state["wallets"].items():
        p = w["pot"]
        by_hour: dict[int, list] = {}
        for f in fills.get(a, []):
            by_hour.setdefault(f["time"] // hl.HOUR * hl.HOUR, []).append(f)
        n_day = 0
        for h in range(s, end, hl.HOUR):
            for f in by_hour.get(h, []):
                sim.on_fill(p, f, hl.av_at(avs.get(a, []), f["time"]), w["median_av"])
                w["last_t"], w["last_tid"] = f["time"], f["tid"]
                n_day += 1
            sim.hour_end(p, h, bars, fund)
            if (h + hl.HOUR) % hl.DAY == 0:
                rows.append({"day": dstr(h), "address": a, "equity": round(max(sim.equity(p), 0.0), 4),
                             "fills": n_day, "dead": p["dead"], "hefboom": round(sim.gross(p) / max(sim.equity(p), 1e-9), 3)})
                n_day = 0
    state["next_hour"] = end
    json.dump(state, open(state_path, "w"), indent=0)
    new = pd.DataFrame(rows)
    if os.path.exists(ledger_path):
        new = pd.concat([pd.read_csv(ledger_path), new], ignore_index=True)
    new.to_csv(ledger_path, index=False)
    print(f"{dstr(s)} t/m {dstr(end - 1)} verwerkt; fills {sum(len(v) for v in fills.values())}")


if __name__ == "__main__":
    main()
