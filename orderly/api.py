"""Orderly Public Info API (geen sleutel). Grens 1200 weight/min per IP; wij blijven op ~1000."""

from __future__ import annotations

import time

import requests

URL = "https://api.orderly.org/v1/public/query"
DAY = 86_400_000
WEIGHT = {"rateLimitStatus": 0, "marketSummary": 1, "marketTrades": 1, "accounts": 5, "accountState": 5,
          "fundingPayments": 5, "portfolio": 5, "trades": 5, "userDepositsWithdrawals": 5,
          "topAddresses": 10, "platformPositions": 20}
_next = [0.0]
_s = requests.Session()


def q(body: dict):
    """Eén query; geeft (json, http-status). Wacht zodat we onder ~1000 weight/min blijven."""
    w = WEIGHT.get(body["type"], 5)
    for attempt in range(8):
        wait = _next[0] - time.time()
        if wait > 0:
            time.sleep(wait)
        _next[0] = time.time() + w * 0.06
        try:
            r = _s.post(URL, json=body, timeout=60)
        except requests.RequestException:
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(10 * (attempt + 1))
            continue
        try:
            return r.json(), r.status_code
        except ValueError:
            return {"raw": r.text[:500]}, r.status_code
    return {"error": "faalt na 8 pogingen"}, 0


def rows(js):
    d = js.get("data") if isinstance(js, dict) else None
    if isinstance(d, dict):
        for k in ("rows", "accounts", "positions", "items"):
            if isinstance(d.get(k), list):
                return d[k], d.get("next_cursor")
        return [], d.get("next_cursor")
    if isinstance(d, list):
        return d, None
    return [], None


def alles(body: dict, max_pages: int = 200):
    """Pagineer via next_cursor tot het einde (of max_pages)."""
    out, cur = [], None
    for _ in range(max_pages):
        b = dict(body, **({"cursor": cur} if cur else {}))
        js, _st = q(b)
        r, cur = rows(js)
        out += r
        if not cur or not r:
            break
    return out
