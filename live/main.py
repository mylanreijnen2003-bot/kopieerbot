"""Papieren kopieerbot. Start: python -m live.main  |  test: python -m live.main --replay 24  |  rapport: --rapport"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import replay, report
from .coins import fetch_instruments, map_coin
from .config import DATA, load, short
from .engine import Copier
from .feeds import HLListener, KrakenBook, now_ms
from .notify import Notifier, trade_text
from .signals import Grouper
from .store import Store

log = logging.getLogger("kopieerbot")
NL = ZoneInfo("Europe/Amsterdam")
REPORT_HOUR = 23


class Bot:
    def __init__(self, cfg):
        self.cfg = cfg
        self.store = Store(DATA)
        self.state = self.store.load_state() or {"start_ms": now_ms()}
        self.instruments = fetch_instruments()
        self.copier = Copier(cfg, self.instruments, self.state)
        self.book = KrakenBook(sorted(self.instruments))
        self.notifier = Notifier(cfg.ntfy_topic)
        self.grouper = Grouper()
        self.hl = HLListener(list(cfg.traders), self._on_fill, self.notifier, self.state, now_ms())

    def _on_fill(self, addr: str, f: dict) -> None:
        self.grouper.add(addr, f, now_ms())

    def _save(self) -> None:
        self.hl.persist()
        self.store.save_state(self.state)

    def _emit(self, rows, shadow, alerts) -> None:
        self.store.append(rows, shadow)
        for r in rows:
            log.info("%s %s %s %s %s @ %s (trader %s, %.1f s) %s", r["trader"], r["munt"], r["richting"],
                     r["actie"], r["hoeveelheid"], r["eigen_prijs"], r["prijs_trader"], r["vertraging_s"],
                     r["status"])
            if r["status"] == "uitgevoerd":
                self.notifier.send(trade_text(r), f"Kopie {r['munt']}")
        for al in alerts:
            self.notifier.send(al, "Pauze trader", "high")

    async def flush_loop(self) -> None:
        while True:
            await asyncio.sleep(0.2)
            for sig in self.grouper.flush(now_ms()):
                m = map_coin(sig.coin, self.instruments)
                if m:
                    await self.book.ensure(m[0])
                rows, shadow, alerts, note = self.copier.process(sig, self.book, now_ms())
                if note:
                    log.info("%s: %s", short(sig.trader), note)
                self._emit(rows, shadow, alerts)
                self._save()

    async def stop_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            if not self.book.connected:
                try:
                    await asyncio.to_thread(self.book.refresh_rest)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Kraken REST faalt: %s", exc)
            for a in self.cfg.traders:
                self._emit(*self.copier.check_stop(a, self.book, now_ms()))
            self._save()

    async def instruments_loop(self) -> None:
        while True:
            await asyncio.sleep(6 * 3600)
            try:
                self.instruments.update(await asyncio.to_thread(fetch_instruments))
            except Exception as exc:  # noqa: BLE001
                log.warning("instruments verversen faalt: %s", exc)

    def report_text(self) -> str:
        text, _ = report.build(self.cfg, self.copier, self.book, self.store.read("own"), self.store.read("shadow"),
                               self.state["start_ms"], now_ms(),
                               f"Dagrapport {datetime.now(NL):%d-%m-%Y}")
        return text

    async def report_loop(self) -> None:
        while True:
            nu = datetime.now(NL)
            nxt = nu.replace(hour=REPORT_HOUR, minute=0, second=0, microsecond=0)
            if nxt <= nu:
                nxt += timedelta(days=1)
            await asyncio.sleep((nxt - nu).total_seconds())
            try:
                await asyncio.to_thread(self.book.refresh_rest)
                text = self.report_text()
                (DATA / f"rapport_{nxt:%Y-%m-%d}.txt").write_text(text, encoding="utf-8")
                self.notifier.send(text, "Dagrapport")
            except Exception as exc:  # noqa: BLE001
                log.exception("dagrapport faalt: %s", exc)
            await asyncio.sleep(5)

    async def run(self) -> None:
        log.info("Start: %d traders (%s), potje %.0f, max %.0fx, stop %.0f%%", len(self.cfg.traders),
                 ", ".join(f"{short(a)} K={k}" for a, k in self.cfg.traders.items()), self.cfg.pot,
                 self.cfg.max_leverage, self.cfg.stop_pct)
        await asyncio.to_thread(self.book.refresh_rest)
        self.notifier.send(f"Gestart met {len(self.cfg.traders)} traders", "Kopieerbot")
        await asyncio.gather(self.hl.run_ws(), self.hl.run_backup(), self.hl.watchdog(), self.book.run(),
                             self.flush_loop(), self.stop_loop(), self.instruments_loop(), self.report_loop())


def main() -> None:
    ap = argparse.ArgumentParser(description="Papieren kopieerbot (Hyperliquid -> Kraken Futures)")
    ap.add_argument("--replay", type=float, metavar="UREN", help="historische fills afspelen (dry-run)")
    ap.add_argument("--rapport", action="store_true", help="rapport van de live-data nu tonen")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.Formatter.converter = time.gmtime
    cfg = load()
    if not cfg.traders:
        raise SystemExit("Geen TRADERS in .env")
    if args.replay:
        replay.run(cfg, args.replay)
    elif args.rapport:
        bot = Bot(cfg)
        bot.book.refresh_rest()
        print(bot.report_text())
    else:
        asyncio.run(Bot(cfg).run())


if __name__ == "__main__":
    main()
