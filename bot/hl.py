"""Hyperliquid info-API (publiek, geen sleutel). Grens ~1200 weight per minuut; wij blijven op ~1000."""

from __future__ import annotations

import bisect
import time

import requests

URL = "https://api.hyperliquid.xyz/info"
HOUR = 3_600_000
DAY = 86_400_000
_next = [0.0]


def info(body: dict, weight: float = 20):
    for attempt in range(8):
        wait = _next[0] - time.time()
        if wait > 0:
            time.sleep(wait)
        _next[0] = time.time() + weight * 0.06
        try:
            r = requests.post(URL, json=body, timeout=60)
        except requests.RequestException:
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(10 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"API faalt: {body.get('type')}")


def kind(coin: str) -> str:
    if coin.startswith("@") or "/" in coin:
        return "spot"
    if ":" in coin:
        return "hip3"
    return "perp"


def _window(addr, s, e, depth=0):
    batch = info({"type": "userFillsByTime", "user": addr, "startTime": int(s), "endTime": int(e),
                  "aggregateByTime": False}, weight=20)
    if not isinstance(batch, list):
        return []
    if len(batch) < 2000 or depth > 14:
        return batch
    times = [int(f["time"]) for f in batch]
    lo, hi = min(times), max(times)
    if lo <= s and hi >= e:
        return batch
    out = list(batch)
    if lo > s:
        out += _window(addr, s, lo, depth + 1)
    if hi < e:
        out += _window(addr, hi, e, depth + 1)
    return out


def fills(addr: str, s: int, e: int) -> list[dict]:
    """Alle fills in [s, e], ontdubbeld en op tijd gesorteerd. API: max 10.000 recentste fills per wallet."""
    seen, out = set(), []
    for f in _window(addr, s, e):
        key = (f.get("tid"), f.get("coin"), f.get("time"), f.get("oid"))
        if key in seen:
            continue
        seen.add(key)
        sz = float(f["sz"])
        signed = sz if f.get("side") == "B" else -sz
        start = float(f.get("startPosition") or 0.0)
        out.append({"time": int(f["time"]), "tid": int(f.get("tid") or 0), "coin": f["coin"], "px": float(f["px"]),
                    "signed": signed, "start": start, "after": start + signed, "dir": f.get("dir", ""),
                    "kind": kind(f["coin"]), "liq": bool(f.get("liquidation")),
                    "pnl": float(f.get("closedPnl") or 0.0), "fee": float(f.get("fee") or 0.0)})
    out.sort(key=lambda x: (x["time"], x["tid"]))
    return out


def av_points(addr: str, perp_only: bool = True) -> tuple[list[tuple[int, float]], str]:
    """Accountwaarde-historie. Liefst alleen perps (we kopiëren alleen perps)."""
    data = info({"type": "portfolio", "user": addr})
    per = dict(data) if isinstance(data, list) else {}
    keys = ["perpDay", "perpWeek", "perpMonth", "perpAllTime"]
    src = "perp"
    if not perp_only or not any(k in per for k in keys):
        keys, src = ["day", "week", "month", "allTime"], "totaal"
    pts = []
    for k in keys:
        pts += [(int(t), float(v)) for t, v in per.get(k, {}).get("accountValueHistory", [])]
    return sorted(set(pts)), src


def av_at(pts: list[tuple[int, float]], t: int) -> float | None:
    """Lineair geïnterpoleerde accountwaarde op tijd t."""
    if not pts:
        return None
    i = bisect.bisect_right(pts, (t, float("inf")))
    if i == 0:
        return pts[0][1]
    if i == len(pts):
        return pts[-1][1]
    (t0, v0), (t1, v1) = pts[i - 1], pts[i]
    return v0 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)


def candles(coin: str, s: int, e: int, interval: str = "1h") -> dict[int, tuple]:
    """{open-tijd ms: (open, hoog, laag, slot)}"""
    out, cur = {}, int(s)
    while cur < e:
        try:
            data = info({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
                                                           "startTime": cur, "endTime": int(e)}}, weight=25)
        except Exception as exc:  # noqa: BLE001
            print("geen candles", coin, exc)
            break
        if not isinstance(data, list) or not data:
            break
        for c in data:
            out[int(c["t"])] = (float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"]))
        nxt = max(int(c["t"]) for c in data) + 1
        if nxt <= cur or len(data) < 4000:
            break
        cur = nxt
    return out


def funding(coin: str, s: int, e: int) -> dict[int, float]:
    """{uur (ms, afgerond): fundingrate}"""
    out, cur = {}, int(s)
    while cur < e:
        try:
            data = info({"type": "fundingHistory", "coin": coin, "startTime": cur, "endTime": int(e)}, weight=20)
        except Exception as exc:  # noqa: BLE001
            print("geen funding", coin, exc)
            break
        if not isinstance(data, list) or not data:
            break
        for r in data:
            out[int(r["time"]) // HOUR * HOUR] = float(r["fundingRate"])
        nxt = max(int(r["time"]) for r in data) + 1
        if nxt <= cur or len(data) < 500:
            break
        cur = nxt
    return out
