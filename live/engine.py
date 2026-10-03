"""Kopieerregels en papieren uitvoering per potje. Puur: prijzen komen uit een `book` (live: Kraken-ticker,
replay: Kraken-kaarsen, tests: vaste prijzen), de tijd wordt meegegeven.

Book-interface: quote(symbool) -> (bid, ask) | None, mid(symbool) -> float | None.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from .coins import classify, map_coin, round_down
from .config import FEE, MAX_OPEN_DELAY_S, Config, short
from .signals import Signal

FIELDS = ["tijd_trader", "tijd_eigen", "vertraging_s", "trader", "munt", "symbool", "richting", "actie",
          "hoeveelheid", "prijs_trader", "eigen_prijs", "slippage_pct", "slippage_eur", "fee", "inzet",
          "resultaat", "status"]

UITGEVOERD, TE_KLEIN, NIET_OP_KRAKEN, GEPAUZEERD = "uitgevoerd", "te klein", "niet op Kraken", "gepauzeerd"
TE_LAAT, GEEN_PRIJS = "te laat", "geen prijs"


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_trader(pot: float) -> dict:
    return {"cash": pot, "pos": {}, "armed": {}, "paused": False, "shadow_cash": pot, "shadow": {}}


class Copier:
    def __init__(self, cfg: Config, instruments: dict, state: dict | None = None):
        self.cfg, self.instruments = cfg, instruments
        self.state = state or {}
        self.state.setdefault("traders", {})
        for addr in cfg.traders:
            self.state["traders"].setdefault(addr, new_trader(cfg.pot))

    # ---------- waarderingen ----------
    def _mark(self, p: dict, book) -> float:
        m = book.mid(p["sym"])
        return m if m else p["entry"]

    def equity(self, addr: str, book) -> float:
        tr = self.state["traders"][addr]
        return tr["cash"] + sum(p["units"] * self._mark(p, book) for p in tr["pos"].values())

    def gross(self, addr: str, book) -> float:
        tr = self.state["traders"][addr]
        return sum(abs(p["units"]) * self._mark(p, book) for p in tr["pos"].values())

    # ---------- signaal verwerken ----------
    def process(self, sig: Signal, book, t_own: int) -> tuple[list[dict], list[dict], list[str], str]:
        """-> (rijen, rijen op traderprijzen, meldingen, notitie voor de log)."""
        tr = self.state["traders"].get(sig.trader)
        if tr is None:
            return [], [], [], "onbekende trader"
        c, start, after = sig.coin, sig.start, sig.after
        kind = classify(c)
        if kind != "perp":
            return [], [], [], f"{c} overgeslagen ({kind})"
        opening = start == 0 and after != 0
        flip = start * after < 0
        if not tr["armed"].get(c):
            if opening or flip:
                tr["armed"][c] = True
            else:
                if after == 0:
                    tr["armed"][c] = True          # trader is plat: volgende opening kopiëren
                return [], [], [], f"{c} genegeerd (positie van vóór de start)"
        pos = tr["pos"].get(c)
        if pos is None and not (opening or flip):
            return [], [], [], f"{c} geen eigen positie om te volgen"

        ctx = {"sig": sig, "t_own": t_own, "book": book, "rows": [], "shadow": [], "alerts": []}
        m = map_coin(c, self.instruments)
        if m is None:
            self._skip(ctx, "openen", 1 if after > 0 else -1, NIET_OP_KRAKEN, c, "")
            return ctx["rows"], ctx["shadow"], [], f"{c} niet op Kraken"
        sym, mult = m
        tp, after_k = sig.px / mult, abs(after) * mult
        step = self.instruments[sym]["step"]

        if pos is not None and (after == 0 or flip):
            self._exec(ctx, sig.trader, c, sym, -pos["units"], "sluiten", tp)
        elif pos is not None:
            cur = abs(pos["units"])
            sign = 1 if pos["units"] > 0 else -1
            target = pos["ratio"] * after_k
            if abs(after) > abs(start):
                q = self._quote(book, sym, sign)
                if q is None:
                    self._skip(ctx, "bijkopen", sign, GEEN_PRIJS, c, sym)
                else:
                    room = max(0.0, self.cfg.max_leverage * self.equity(sig.trader, book) - self.gross(sig.trader, book))
                    add = round_down(min(target - cur, room / q), step)
                    if add < step * 0.999:
                        self._skip(ctx, "bijkopen", sign, TE_KLEIN, c, sym)
                    else:
                        self._exec(ctx, sig.trader, c, sym, sign * add, "bijkopen", tp, q)
            else:
                new = round_down(target, step)
                cut = cur if new < step * 0.999 else cur - new
                if cut < step * 0.999:
                    self._skip(ctx, "afbouwen", sign, TE_KLEIN, c, sym)
                else:
                    self._exec(ctx, sig.trader, c, sym, -sign * cut, "sluiten" if cut >= cur else "afbouwen", tp)

        if opening or flip:
            sign = 1 if after > 0 else -1
            actie = "wissel" if flip else "openen"
            delay = (t_own - sig.t_last) / 1000
            if tr["paused"]:
                self._skip(ctx, actie, sign, GEPAUZEERD, c, sym)
            elif delay > MAX_OPEN_DELAY_S:
                self._skip(ctx, actie, sign, TE_LAAT, c, sym)
            else:
                q = self._quote(book, sym, sign)
                if q is None:
                    self._skip(ctx, actie, sign, GEEN_PRIJS, c, sym)
                else:
                    stake = self.cfg.pot / self.cfg.traders[sig.trader]
                    room = max(0.0, self.cfg.max_leverage * self.equity(sig.trader, book) - self.gross(sig.trader, book))
                    qty = round_down(min(stake, room) / q, step)
                    if qty < step * 0.999:
                        self._skip(ctx, actie, sign, TE_KLEIN, c, sym)
                    else:
                        self._exec(ctx, sig.trader, c, sym, sign * qty, actie, tp, q)
                        tr["pos"][c]["ratio"] = qty / after_k

        self.check_stop(sig.trader, book, t_own, ctx)
        return ctx["rows"], ctx["shadow"], ctx["alerts"], ""

    def check_stop(self, addr: str, book, t_own: int, ctx: dict | None = None):
        """Potje op TRADER_STOP_PCT: alles sluiten tegen bid/ask en trader pauzeren."""
        tr = self.state["traders"][addr]
        own_ctx = ctx is None
        if own_ctx:
            ctx = {"sig": None, "t_own": t_own, "book": book, "rows": [], "shadow": [], "alerts": []}
        if tr["paused"]:
            return ctx["rows"], ctx["shadow"], ctx["alerts"]
        eq = self.equity(addr, book)
        if eq <= self.cfg.pot * (1 + self.cfg.stop_pct / 100):
            for c, p in list(tr["pos"].items()):
                mark = self._mark(p, book)
                ctx["sig"] = Signal(addr, c, p["units"], 0.0, mark, t_own, t_own, 0)
                self._exec(ctx, addr, c, p["sym"], -p["units"], "sluiten (stop)", mark)
            tr["paused"] = True
            pct = (self.equity(addr, book) / self.cfg.pot - 1) * 100
            ctx["alerts"].append(f"PAUZE {short(addr)}: potje op {pct:.1f}% (grens {self.cfg.stop_pct:.0f}%), "
                                 f"alle posities gesloten")
        return ctx["rows"], ctx["shadow"], ctx["alerts"]

    # ---------- uitvoering ----------
    @staticmethod
    def _quote(book, sym: str, sign: int) -> float | None:
        q = book.quote(sym)
        if not q or not q[0] or not q[1]:
            return None
        return q[1] if sign > 0 else q[0]       # kopen tegen ask, verkopen tegen bid

    def _row(self, ctx, actie, sign, status, coin, sym) -> dict:
        sig = ctx["sig"]
        return {"tijd_trader": iso(sig.t_last), "tijd_eigen": iso(ctx["t_own"]),
                "vertraging_s": round((ctx["t_own"] - sig.t_last) / 1000, 2), "trader": short(sig.trader),
                "munt": coin, "symbool": sym, "richting": "long" if sign > 0 else "short", "actie": actie,
                "hoeveelheid": 0.0, "prijs_trader": sig.px, "eigen_prijs": "", "slippage_pct": "",
                "slippage_eur": 0.0, "fee": 0.0, "inzet": 0.0, "resultaat": 0.0, "status": status}

    def _skip(self, ctx, actie, sign, status, coin, sym):
        r = self._row(ctx, actie, sign, status, coin, sym)
        ctx["rows"].append(r)
        ctx["shadow"].append(dict(r, tijd_eigen=r["tijd_trader"], vertraging_s=0.0))

    @staticmethod
    def _fill(book_pos: dict, coin: str, sym: str, delta: float, px: float) -> float:
        """Positie bijwerken, gerealiseerde winst teruggeven."""
        p = book_pos.get(coin)
        units = p["units"] if p else 0.0
        realized = 0.0
        if units and units * delta < 0:
            closed = min(abs(delta), abs(units))
            realized = (px - p["entry"]) * closed * math.copysign(1, units)
        new = units + delta
        if abs(new) < 1e-12:
            book_pos.pop(coin, None)
        elif p is None or units * new <= 0:
            book_pos[coin] = {"sym": sym, "units": new, "entry": px, "ratio": p.get("ratio", 0.0) if p else 0.0}
        else:
            if abs(new) > abs(units):
                p["entry"] = (p["entry"] * abs(units) + px * abs(delta)) / abs(new)
            p["units"] = new
        return realized

    def _exec(self, ctx, addr, coin, sym, delta, actie, tp, px: float | None = None):
        sig, tr = ctx["sig"], self.state["traders"][addr]
        side = 1 if delta > 0 else -1
        if px is None:
            px = self._quote(ctx["book"], sym, side)
        if px is None:                                   # sluiten zonder quote: prijs van de trader
            px = tp
        pos = tr["pos"].get(coin)
        sign = int(math.copysign(1, pos["units"])) if pos else side
        fee = abs(delta) * px * FEE
        realized = self._fill(tr["pos"], coin, sym, delta, px)
        tr["cash"] -= delta * px + fee
        slip_eur = side * (px - tp) * abs(delta)
        r = self._row(ctx, actie, sign, UITGEVOERD, coin, sym)
        r.update(hoeveelheid=round(abs(delta), 10), prijs_trader=tp, eigen_prijs=px,
                 slippage_pct=round(side * (px / tp - 1) * 100, 4) if tp else 0.0,
                 slippage_eur=round(slip_eur, 6), fee=round(fee, 6), inzet=round(abs(delta) * px, 4),
                 resultaat=round(realized - fee, 6))
        ctx["rows"].append(r)
        # schaduwpotje: dezelfde hoeveelheid tegen de prijs van de trader, zonder vertraging, spread en fee
        s_real = self._fill(tr["shadow"], coin, sym, delta, tp)
        tr["shadow_cash"] -= delta * tp
        ctx["shadow"].append(dict(r, tijd_eigen=r["tijd_trader"], vertraging_s=0.0, eigen_prijs=tp,
                                  slippage_pct=0.0, slippage_eur=0.0, fee=0.0, inzet=round(abs(delta) * tp, 4),
                                  resultaat=round(s_real, 6)))
