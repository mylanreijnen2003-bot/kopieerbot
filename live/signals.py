"""Fills groeperen tot signalen. Puur: de klok wordt meegegeven, zodat live, replay en tests dezelfde code gebruiken."""

from __future__ import annotations

from dataclasses import dataclass

from .config import GROUP_S


@dataclass
class Signal:
    trader: str
    coin: str
    start: float      # positie trader vóór de eerste fill van de groep
    after: float      # positie trader na de laatste fill
    px: float         # gewogen gemiddelde fillprijs (Hyperliquid)
    t_first: int      # ms
    t_last: int       # ms, tijd van de laatste fill: basis voor de vertraging
    n: int


class Grouper:
    """Fills per (trader, munt) die elkaar binnen GROUP_S opvolgen worden één signaal."""

    def __init__(self, window_s: float = GROUP_S):
        self.window = window_s * 1000
        self.open: dict[tuple, dict] = {}

    def add(self, trader: str, f: dict, now_ms: int) -> None:
        key = (trader, f["coin"])
        g = self.open.get(key)
        if g is None:
            g = self.open[key] = {"fills": [], "seen": now_ms}
        g["fills"].append(f)
        g["seen"] = now_ms

    def flush(self, now_ms: int, force: bool = False) -> list[Signal]:
        out = []
        for key in list(self.open):
            g = self.open[key]
            if force or now_ms - g["seen"] >= self.window:
                out.append(_combine(key[0], key[1], g["fills"]))
                del self.open[key]
        out.sort(key=lambda s: s.t_last)
        return out


def chain(fills: list[dict]) -> list[dict]:
    """Volgorde via de positieketen (start van de één = na van de vorige). Nodig omdat fills in dezelfde
    milliseconde kunnen vallen en tid niet chronologisch is. Lukt de keten niet: op (tijd, tid)."""
    rest = sorted(fills, key=lambda f: (f["time"], f["tid"]))
    first = next((f for f in rest if not any(abs(f["start"] - g["after"]) < 1e-9 for g in rest if g is not f)),
                 None)
    if first is None:
        return rest
    out = [first]
    rest.remove(first)
    while rest:
        nxt = next((f for f in rest if abs(f["start"] - out[-1]["after"]) < 1e-9), None)
        if nxt is None:
            return sorted(fills, key=lambda f: (f["time"], f["tid"]))
        out.append(nxt)
        rest.remove(nxt)
    return out


def _combine(trader: str, coin: str, fills: list[dict]) -> Signal:
    fills = chain(fills)
    qty = sum(abs(f["signed"]) for f in fills)
    px = sum(abs(f["signed"]) * f["px"] for f in fills) / qty if qty else fills[-1]["px"]
    return Signal(trader, coin, fills[0]["start"], fills[-1]["after"], px,
                  fills[0]["time"], fills[-1]["time"], len(fills))


def group_history(fills_by_trader: dict[str, list[dict]], window_s: float = GROUP_S) -> list[Signal]:
    """Replay: historische fills groeperen op fill-tijd (gat <= window) in plaats van op aankomsttijd."""
    g = Grouper(window_s)
    events = sorted(((f["time"], f["tid"], t, f) for t, fl in fills_by_trader.items() for f in fl),
                    key=lambda x: (x[0], x[1]))
    out = []
    for t, _, trader, f in events:
        out += g.flush(t)
        g.add(trader, f, t)
    out += g.flush(0, force=True)
    out.sort(key=lambda s: s.t_last)
    return out
