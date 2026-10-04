"""Papier v2: gedeelde functies (trades reconstrueren incl. open posities, K, kosten)."""

from __future__ import annotations

import numpy as np

KOSTEN = 0.0032          # per rondje (2x 0,05% taker + 2x 0,11% spread/slippage)
DAG = 86_400_000
UUR = 3_600_000


def trades_open(fl, vanaf=0):
    """Fills (dicts met time, coin, start, after, px) -> (gesloten, open).
    Alleen posities die vanaf `vanaf` vanuit plat (of richtingwissel) openen.
    gesloten: dict(coin, open, sluit, dir, p1, pv, po). open: {coin: dict(open, dir, p1)}"""
    st, uit = {}, []
    for f in fl:
        if f["time"] < vanaf:
            continue
        c, s, a, px = f["coin"], f["start"], f["after"], f["px"]

        def nieuw():
            st[c] = {"dir": 1 if a > 0 else -1, "p1": px, "iq": abs(a), "ic": abs(a) * px, "uq": 0.0, "uc": 0.0,
                     "open": f["time"]}
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
        else:
            q = abs(s) if (a == 0 or flip) else abs(s) - abs(a)
            p["uq"] += q
            p["uc"] += q * px
        if a == 0 or flip:
            uit.append({"coin": c, "open": p["open"], "sluit": f["time"], "dir": p["dir"], "p1": p["p1"],
                        "pv": p["ic"] / p["iq"], "po": p["uc"] / p["uq"]})
            st.pop(c)
            if flip:
                nieuw()
    return uit, {c: {"open": p["open"], "dir": p["dir"], "p1": p["p1"]} for c, p in st.items()}


def k90(o, c):
    ev = sorted([(x, 1) for x in o] + [(x, -1) for x in c])
    s, cs = 0, []
    for _, d in ev:
        s += d
        cs.append(s)
    return max(1, int(np.ceil(np.percentile(cs, 90)))) if cs else 1


def r_bot(t):
    return t["dir"] * (t["po"] / t["p1"] - 1) - KOSTEN


def max_daling(r_k):
    eq = 1 + np.cumsum(r_k)
    return float((eq / np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:] - 1).min()) if len(r_k) else 0.0
