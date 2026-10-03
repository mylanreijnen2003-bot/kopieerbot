"""Live databronnen: Hyperliquid-fills (websocket + REST-backup) en Kraken-prijzen (websocket + REST-fallback).
Alleen publieke endpoints, geen API-sleutel."""

from __future__ import annotations

import asyncio
import json
import logging
import time

import requests
import websockets

from bot import hl

from .config import short

log = logging.getLogger("kopieerbot")

HL_WS = "wss://api.hyperliquid.xyz/ws"
KRAKEN_WS = "wss://futures.kraken.com/ws/v1"
KRAKEN_TICKERS = "https://futures.kraken.com/derivatives/api/v3/tickers"
DOWN_ALERT_S = 120


def now_ms() -> int:
    return int(time.time() * 1000)


class KrakenBook:
    """Bid/ask per symbool. Websocket 'ticker'; is die weg of een prijs oud, dan REST /tickers."""

    def __init__(self, symbols: list[str]):
        self.symbols = symbols
        self.q: dict[str, tuple[float, float, float]] = {}   # symbool -> (bid, ask, ontvangen-tijd s)
        self.connected = False

    def quote(self, sym):
        q = self.q.get(sym)
        return (q[0], q[1]) if q else None

    def mid(self, sym):
        q = self.q.get(sym)
        return (q[0] + q[1]) / 2 if q else None

    def refresh_rest(self) -> None:
        data = requests.get(KRAKEN_TICKERS, timeout=15).json()
        t = time.time()
        for x in data.get("tickers", []):
            if x.get("bid") and x.get("ask"):
                self.q[x["symbol"]] = (float(x["bid"]), float(x["ask"]), t)

    async def ensure(self, sym: str, max_age_s: float = 30) -> None:
        q = self.q.get(sym)
        if q is None or not self.connected or time.time() - q[2] > max_age_s:
            try:
                await asyncio.to_thread(self.refresh_rest)
            except Exception as exc:  # noqa: BLE001
                log.warning("Kraken REST faalt: %s", exc)

    async def run(self) -> None:
        backoff = 1
        while True:
            try:
                async with websockets.connect(KRAKEN_WS, ping_interval=30, max_size=None) as ws:
                    await ws.send(json.dumps({"event": "subscribe", "feed": "ticker", "product_ids": self.symbols}))
                    self.connected, backoff = True, 1
                    log.info("Kraken-ticker verbonden (%d symbolen)", len(self.symbols))
                    async for raw in ws:
                        m = json.loads(raw)
                        if m.get("feed") in ("ticker", "ticker_snapshot") and m.get("bid") and m.get("ask"):
                            self.q[m["product_id"]] = (float(m["bid"]), float(m["ask"]), time.time())
            except Exception as exc:  # noqa: BLE001
                log.warning("Kraken-websocket weg: %s", exc)
            self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


class HLListener:
    """userFills per trader via één websocket; elke minuut REST-backup. Ontdubbelen op tid."""

    def __init__(self, traders: list[str], on_fill, notifier, state: dict, start_ms: int):
        self.traders, self.on_fill, self.notifier = traders, on_fill, notifier
        self.state = state
        self.min_ms = start_ms
        self.seen: dict[str, dict[int, int]] = {a: {int(t): 0 for t in state.get("seen", {}).get(a, [])}
                                                for a in traders}
        self.last_poll = {a: max(state.get("last_poll", {}).get(a, start_ms), now_ms() - 86_400_000)
                          for a in traders}
        self.down_since: float | None = time.time()
        self.alerted = False

    def _fill(self, addr: str, raw: dict, src: str) -> None:
        f = hl.parse(raw)
        if f["time"] < self.min_ms or f["tid"] in self.seen[addr]:
            return
        self.seen[addr][f["tid"]] = f["time"]
        if src == "backup":
            log.info("backup vult gemiste fill aan: %s %s", short(addr), f["coin"])
        self.on_fill(addr, f)

    def persist(self) -> None:
        cut = now_ms() - 86_400_000
        for a in self.traders:
            self.seen[a] = {t: ms for t, ms in self.seen[a].items() if ms == 0 or ms > cut}
        self.state["seen"] = {a: list(self.seen[a])[-3000:] for a in self.traders}
        self.state["last_poll"] = self.last_poll

    async def run_ws(self) -> None:
        backoff = 1
        while True:
            try:
                async with websockets.connect(HL_WS, ping_interval=None, max_size=None) as ws:
                    for a in self.traders:
                        await ws.send(json.dumps({"method": "subscribe",
                                                  "subscription": {"type": "userFills", "user": a}}))
                    log.info("Hyperliquid verbonden, %d traders", len(self.traders))
                    if self.alerted:
                        self.notifier.send("Hyperliquid-verbinding hersteld", "Verbinding")
                    self.down_since, self.alerted, backoff = None, False, 1
                    pinger = asyncio.create_task(self._ping(ws))
                    try:
                        async for raw in ws:
                            m = json.loads(raw)
                            if m.get("channel") != "userFills":
                                continue
                            d = m.get("data", {})
                            if d.get("isSnapshot"):
                                continue                 # geschiedenis bij (her)verbinden: negeren
                            user = str(d.get("user", "")).lower()
                            if user in self.seen:
                                for raw_f in d.get("fills", []):
                                    self._fill(user, raw_f, "ws")
                    finally:
                        pinger.cancel()
            except Exception as exc:  # noqa: BLE001
                log.warning("Hyperliquid-websocket weg: %s (opnieuw over %d s)", exc, backoff)
            if self.down_since is None:
                self.down_since = time.time()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    @staticmethod
    async def _ping(ws) -> None:
        while True:
            await asyncio.sleep(30)
            await ws.send(json.dumps({"method": "ping"}))

    async def watchdog(self) -> None:
        while True:
            await asyncio.sleep(10)
            if self.down_since and not self.alerted and time.time() - self.down_since > DOWN_ALERT_S:
                self.alerted = True
                self.notifier.send(f"Hyperliquid-verbinding > {DOWN_ALERT_S // 60} min weg; REST-backup loopt door",
                                   "Verbinding", "high")

    async def run_backup(self) -> None:
        while True:
            for a in self.traders:
                end = now_ms()
                body = {"type": "userFillsByTime", "user": a, "startTime": self.last_poll[a] - 120_000,
                        "endTime": end, "aggregateByTime": False}
                try:
                    batch = await asyncio.to_thread(hl.info, body, 20)
                except Exception as exc:  # noqa: BLE001
                    log.warning("REST-backup faalt voor %s: %s", short(a), exc)
                    continue
                for raw_f in sorted(batch or [], key=lambda x: (x["time"], x.get("tid", 0))):
                    self._fill(a, raw_f, "backup")
                self.last_poll[a] = end
            await asyncio.sleep(60)
