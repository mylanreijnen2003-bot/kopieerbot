"""Kopieer-motor voor de backtest: dezelfde logica als bot/sim.py (H3b), maar met instelbare varianten."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

CAP = 1000.0
CLOSEOUT = 0.5
HOUR, DAY = 3_600_000, 86_400_000


@dataclass(frozen=True)
class Cfg:
    name: str
    lev: float = 2.0                 # math.inf = geen plafond
    stop: float | None = 0.65        # potje dicht bij equity <= stop x start; None = geen stop
    slip: float = 0.0005
    fee: float = 0.0005
    funding: bool = True
    long_only: bool = False
    coins: frozenset | None = None   # None = alle perps


def to_base(coin: str) -> str:
    return coin[1:] if coin.startswith("k") and coin[1:2].isupper() else coin


class Pot:
    __slots__ = ("cfg", "cash", "units", "frac", "armed", "last", "dead", "reden", "copied", "skip_geerfd",
                 "skip_beurs", "max_lev", "fund", "cost")

    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.cash, self.units, self.frac, self.armed, self.last = CAP, {}, {}, {}, {}
        self.dead, self.reden = False, None
        self.copied = self.skip_geerfd = self.skip_beurs = 0
        self.max_lev = self.fund = self.cost = 0.0

    def equity(self, px=None):
        px = px or {}
        return self.cash + sum(u * px.get(c, self.last.get(c, 0.0)) for c, u in self.units.items())

    def gross(self, px=None, skip=None):
        px = px or {}
        return sum(abs(u) * px.get(c, self.last.get(c, 0.0)) for c, u in self.units.items() if c != skip)

    def trade(self, c, delta, px):
        if abs(delta) < 1e-12 or px <= 0:
            return
        exe = px * (1 + self.cfg.slip if delta > 0 else 1 - self.cfg.slip)
        fee = abs(delta) * exe * self.cfg.fee
        self.cash -= delta * exe + fee
        self.cost += abs(delta) * px * self.cfg.slip + fee
        u = self.units.get(c, 0.0) + delta
        if abs(u) * px < 1e-6:
            self.units.pop(c, None)
            self.frac.pop(c, None)
        else:
            self.units[c] = u

    def close_all(self, px, reden):
        for c in list(self.units):
            self.trade(c, -self.units[c], px.get(c, self.last.get(c, 0.0)))
        self.units, self.frac = {}, {}
        self.dead, self.reden, self.cash = True, reden, max(self.cash, 0.0)

    def on_fill(self, c, start, after, px, av):
        if self.dead:
            return
        cfg = self.cfg
        if not self.armed.get(c):
            flip = after != 0 and start != 0 and (after > 0) != (start > 0)
            if start == 0 or flip:
                self.armed[c] = True
            else:
                self.skip_geerfd += 1
                if after == 0:
                    self.armed[c] = True
                return
        if cfg.coins is not None and to_base(c) not in cfg.coins:
            self.skip_beurs += 1
            return
        self.last[c] = px
        eq = max(self.equity(), 0.0)
        lead = eq / av * after
        if cfg.long_only:
            lead = max(lead, 0.0)
        cur = self.units.get(c, 0.0)
        reducing = start != 0 and after * start > 0 and abs(after) < abs(start)
        if lead == 0:
            target = 0.0
        elif reducing:
            target = self.frac.get(c, 1.0) * lead
            if abs(target) > abs(cur) and target * cur > 0:
                target = cur
        else:
            room = math.inf if cfg.lev == math.inf else max(0.0, cfg.lev * eq - self.gross(skip=c))
            target = math.copysign(min(abs(lead), room / px), lead)
            self.frac[c] = target / lead
        self.trade(c, target - cur, px)
        self.copied += 1
        self.max_lev = max(self.max_lev, self.gross() / max(self.equity(), 1e-9))

    def hour_end(self, h, bars, fund):
        if self.dead or not self.units:
            return
        close, worst = {}, {}
        for c, u in self.units.items():
            bar = bars.get(c, {}).get(h)
            last = self.last.get(c, 0.0)
            close[c] = bar[3] if bar else last
            worst[c] = (bar[2] if u > 0 else bar[1]) if bar else last
            if self.cfg.funding:
                rate = fund.get(c, {}).get(h)
                if rate:
                    pay = u * close[c] * rate
                    self.cash -= pay
                    self.fund -= pay
        eq_w = self.equity(worst)
        req = 0.0 if self.cfg.lev == math.inf else self.gross(worst) / self.cfg.lev
        if eq_w <= 0 or eq_w < CLOSEOUT * req:
            self.close_all(worst, "gedwongen sluiting (marge)")
        elif self.cfg.stop is not None and eq_w <= self.cfg.stop * CAP:
            self.close_all(worst, "stop")
        else:
            self.last.update(close)


def av_at(t_arr, v_arr, t, fallback):
    if len(t_arr) == 0:
        return fallback
    v = float(np.interp(t, t_arr, v_arr))
    return v if v > 1000 else fallback


def run(cfg: Cfg, fl, bars, fund, av_t, av_v, fallback, t0, t1):
    """fl: lijst (ts, coin, start, after, px) gesorteerd. Geeft dag-equity (lijst) en de Pot terug."""
    p = Pot(cfg)
    by_hour = {}
    for f in fl:
        if t0 <= f[0] < t1:
            by_hour.setdefault(f[0] // HOUR * HOUR, []).append(f)
    days = []
    for h in range(t0, t1, HOUR):
        for ts, c, start, after, px in by_hour.get(h, ()):
            p.on_fill(c, start, after, px, av_at(av_t, av_v, ts, fallback))
        p.hour_end(h, bars, fund)
        if (h + HOUR) % DAY == 0:
            days.append(max(p.equity(), 0.0))
    return days, p
