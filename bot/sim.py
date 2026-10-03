"""Kopieer-simulatie per potje (H3b). Pure functies: geen API, zodat de backtest dezelfde motor kan gebruiken.

Regels: zie REGELS.md.
"""

from __future__ import annotations

import math

CAP = 1000.0       # startpotje per wallet ($)
EXEC_BP = 0.0005   # vertraging + slippage: 5 bp
FEE = 0.00045      # taker-fee Hyperliquid: 4,5 bp
STOP = 0.80        # potje dicht bij equity <= 80% van de start
LEV = 2.0          # hefboomplafond (ESMA-limiet crypto voor particulieren)
CLOSEOUT = 0.5     # gedwongen sluiten als equity < 50% van de benodigde marge


def new_pot() -> dict:
    return {"cash": CAP, "units": {}, "frac": {}, "armed": {}, "last_px": {}, "dead": False, "dead_t": None,
            "reden": None, "gekopieerd": 0, "overgeslagen": {"spot": 0, "hip3": 0, "geerfd": 0},
            "max_hefboom": 0.0, "funding": 0.0, "kosten": 0.0}


def equity(p: dict, px: dict | None = None) -> float:
    px = px or {}
    return p["cash"] + sum(u * px.get(c, p["last_px"].get(c, 0.0)) for c, u in p["units"].items())


def gross(p: dict, px: dict | None = None, skip: str | None = None) -> float:
    px = px or {}
    return sum(abs(u) * px.get(c, p["last_px"].get(c, 0.0)) for c, u in p["units"].items() if c != skip)


def trade(p: dict, coin: str, delta: float, px: float) -> None:
    if abs(delta) < 1e-12 or px <= 0:
        return
    exe = px * (1 + EXEC_BP if delta > 0 else 1 - EXEC_BP)
    fee = abs(delta) * exe * FEE
    p["cash"] -= delta * exe + fee
    p["kosten"] += abs(delta) * px * EXEC_BP + fee
    u = p["units"].get(coin, 0.0) + delta
    if abs(u) * px < 1e-6:
        p["units"].pop(coin, None)
        p["frac"].pop(coin, None)
    else:
        p["units"][coin] = u


def close_all(p: dict, px: dict, t: int, reden: str) -> None:
    for c in list(p["units"]):
        trade(p, c, -p["units"][c], px.get(c, p["last_px"].get(c, 0.0)))
    p["units"], p["frac"] = {}, {}
    p.update(dead=True, dead_t=t, reden=reden, cash=max(p["cash"], 0.0))


def on_fill(p: dict, f: dict, av: float | None, fallback_av: float) -> None:
    """Eén fill van de leider verwerken."""
    if p["dead"]:
        return
    if f["kind"] != "perp":
        p["overgeslagen"][f["kind"]] += 1
        return
    c, start, after, px = f["coin"], f["start"], f["after"], f["px"]
    if not p["armed"].get(c):
        flip = after != 0 and start != 0 and (after > 0) != (start > 0)
        if start == 0 or flip:
            p["armed"][c] = True                      # nieuwe positie vanuit plat: kopiëren
        else:
            p["overgeslagen"]["geerfd"] += 1          # positie van vóór de start: niet overnemen
            if after == 0:
                p["armed"][c] = True                  # leider is nu plat; volgende opening kopiëren
            return
    p["last_px"][c] = px
    av_use = av if av and av > 1000 else fallback_av
    eq = max(equity(p), 0.0)
    lead = eq / av_use * after
    cur = p["units"].get(c, 0.0)
    reducing = start != 0 and after * start > 0 and abs(after) < abs(start)
    if after == 0:
        target = 0.0
    elif reducing:
        target = p["frac"].get(c, 1.0) * lead
        if abs(target) > abs(cur) and target * cur > 0:
            target = cur
    else:
        room = max(0.0, LEV * eq - gross(p, skip=c))
        target = math.copysign(min(abs(lead), room / px), lead)
        p["frac"][c] = target / lead if lead else 1.0
    trade(p, c, target - cur, px)
    p["gekopieerd"] += 1
    p["max_hefboom"] = max(p["max_hefboom"], gross(p) / max(equity(p), 1e-9))


def hour_end(p: dict, h: int, bars: dict, fund: dict) -> None:
    """Einde van uur h: funding, dan risico-check op de slechtste prijs van dat uur."""
    if p["dead"] or not p["units"]:
        return
    close, worst = {}, {}
    for c, u in p["units"].items():
        bar = bars.get(c, {}).get(h)
        last = p["last_px"].get(c, 0.0)
        close[c] = bar[3] if bar else last
        worst[c] = (bar[2] if u > 0 else bar[1]) if bar else last
        rate = fund.get(c, {}).get(h)
        if rate:
            pay = u * close[c] * rate                 # positieve rate: longs betalen, shorts ontvangen
            p["cash"] -= pay
            p["funding"] -= pay
    eq_w = equity(p, worst)
    if eq_w < CLOSEOUT * gross(p, worst) / LEV:
        close_all(p, worst, h, "gedwongen sluiting (marge)")
    elif eq_w <= STOP * CAP:
        close_all(p, worst, h, "stop -20%")
    else:
        p["last_px"].update(close)
