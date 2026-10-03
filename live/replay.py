"""Dry-run: historische fills (userFillsByTime) afspelen tegen Kraken 1m-kaarsen, om zonder wachten te testen.
Eigen prijs = Kraken-tradeprijs op (laatste fill + 2 s), lineair binnen de minuut, ± de huidige halve spread."""

from __future__ import annotations

import bisect
import logging
import shutil

import requests

from bot import hl
from bt.delay_test import trades_detail

from . import report
from .coins import fetch_instruments, map_coin
from .config import DATA, Config, short
from .engine import Copier
from .feeds import KRAKEN_TICKERS, now_ms
from .signals import group_history
from .store import Store

log = logging.getLogger("kopieerbot")
CHARTS = "https://futures.kraken.com/api/charts/v1/trade/{sym}/1m"
LATENCY_MS = 2000      # 1,5 s groeperen + ~0,5 s netwerk


class CandleBook:
    def __init__(self, s_ms: int, e_ms: int):
        self.s, self.e, self.t = s_ms, e_ms, e_ms
        self.c: dict[str, tuple[list[int], list[tuple[float, float]]]] = {}
        tick = requests.get(KRAKEN_TICKERS, timeout=30).json().get("tickers", [])
        self.spread = {x["symbol"]: (x["ask"] - x["bid"]) / ((x["ask"] + x["bid"]) / 2)
                       for x in tick if x.get("bid") and x.get("ask")}

    def _load(self, sym):
        if sym not in self.c:
            rows, cur = {}, self.s // 1000 - 120
            while cur < self.e // 1000:
                d = requests.get(CHARTS.format(sym=sym), params={"from": cur, "to": self.e // 1000}, timeout=30).json()
                cs = d.get("candles", [])
                for k in cs:
                    rows[int(k["time"])] = (float(k["open"]), float(k["close"]))
                if not cs or not d.get("more_candles"):
                    break
                cur = max(int(k["time"]) for k in cs) // 1000 + 60
            ts = sorted(rows)
            self.c[sym] = (ts, [rows[t] for t in ts])
        return self.c[sym]

    def mid(self, sym):
        ts, oc = self._load(sym)
        if not ts:
            return None
        minute = self.t // 60_000 * 60_000
        i = bisect.bisect_right(ts, minute) - 1
        if i < 0:
            return oc[0][0]
        o, c = oc[i]
        if ts[i] != minute:
            return c                       # geen handel die minuut: laatste slot
        return o + (c - o) * (self.t - minute) / 60_000

    def quote(self, sym):
        m = self.mid(sym)
        if not m:
            return None
        h = m * self.spread.get(sym, 0.0005) / 2
        return (m - h, m + h)


def run(cfg: Config, hours: float) -> None:
    end = now_ms()
    start = end - int(hours * hl.HOUR)
    folder = DATA / "replay"
    shutil.rmtree(folder, ignore_errors=True)
    store = Store(folder)
    instruments = fetch_instruments()
    log.info("Replay %.0f uur, %d traders, %d Kraken-perps", hours, len(cfg.traders), len(instruments))
    fills = {a: hl.fills(a, start, end) for a in cfg.traders}
    for a, fl in fills.items():
        rt = [t for t in trades_detail([f for f in fl if f["kind"] == "perp"]) if t[1] >= start]
        log.info("%s: %d fills, %d afgeronde trades (plat->plat) van de trader zelf", short(a), len(fl), len(rt))
    book = CandleBook(start, end)
    copier = Copier(cfg, instruments)
    notes: dict[str, int] = {}
    for sig in group_history(fills):
        book.t = sig.t_last + LATENCY_MS
        rows, shadow, alerts, note = copier.process(sig, book, book.t)
        for a in cfg.traders:
            r2, s2, al2 = copier.check_stop(a, book, book.t)
            rows, shadow, alerts = rows + r2, shadow + s2, alerts + al2
        store.append(rows, shadow)
        for r in rows:
            log.info("%s %s %s %s %s @ %s (trader %s) %s", r["trader"], r["munt"], r["richting"], r["actie"],
                     r["hoeveelheid"], r["eigen_prijs"], r["prijs_trader"], r["status"])
        for al in alerts:
            log.info("MELDING %s", al)
        if note:
            key = note.split(" ", 1)[1] if " " in note else note
            notes[key] = notes.get(key, 0) + 1
    for k, v in sorted(notes.items()):
        log.info("overgeslagen: %s x%d", k, v)
    book.t = end
    text, _ = report.build(cfg, copier, book, store.read("own"), store.read("shadow"), start, end,
                           f"Replay {hours:.0f} uur (prijzen: Kraken 1m-kaarsen + huidige spread)")
    print("\n" + text)
    print(f"\nCSV's: {folder}")
