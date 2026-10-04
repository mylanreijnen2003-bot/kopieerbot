"""Lighter REST-client met nette retries (429, 405 en 5xx zijn tijdelijk).

Logt nooit parameters (account-indexen zijn herleidbaar; repo is publiek).
"""
from __future__ import annotations

import time
from collections import Counter

import requests

BASE = "https://mainnet.zklighter.elliot.ai/api/v1/"
DAG = 86_400_000
CALLS = Counter()
_S = requests.Session()
_S.headers["User-Agent"] = "kopieerbot"
PAUZE = 0.22
TIJDELIJK = {405, 429, 500, 502, 503, 504}


def ms(t) -> int:
    t = int(t)
    return t * 1000 if t < 10**12 else t


def get(path: str, params: dict | None = None, pogingen: int = 8):
    """GET -> (status, json|None). Tijdelijke fouten: wachten en opnieuw (max ~8 min)."""
    st = None
    for i in range(pogingen):
        try:
            r = _S.get(BASE + path, params=params, timeout=30)
            st = r.status_code
        except requests.RequestException:
            st = "netwerk"
        CALLS[st] += 1
        if st in TIJDELIJK or st == "netwerk":
            time.sleep(min(120, 3 * 2 ** i))
            continue
        time.sleep(PAUZE)
        try:
            return st, r.json()
        except ValueError:
            return st, None
    CALLS["opgegeven"] += 1
    return st, None


def fills_pagina(idx: int, cursor: str | None = None):
    p = {"account_index": idx, "sort_by": "timestamp", "limit": 100}
    if cursor:
        p["cursor"] = cursor
    st, d = get("trades", p)
    d = d or {}
    return st, d.get("trades") or [], d.get("next_cursor")


def alle_fills(idx: int, eerste: list | None = None, cursor: str | None = None, max_pag: int = 40):
    """Alle beschikbare fills (Lighter geeft er max ~3.000), nieuwste eerst."""
    out = list(eerste or [])
    if eerste is None:
        st, tr, cursor = fills_pagina(idx)
        out += tr
    for _ in range(max_pag):
        if not cursor:
            break
        st, tr, cursor = fills_pagina(idx, cursor)
        if st != 200 or not tr:
            break
        out += tr
    return out


def pnl(idx: int, start_ms: int, eind_ms: int):
    """Dagelijkse PnL incl. ongerealiseerd, gecorrigeerd voor stortingen (ignore_transfers=false)."""
    st, d = get("pnl", {"by": "index", "value": str(idx), "resolution": "1d", "start_timestamp": start_ms,
                        "end_timestamp": eind_ms, "count_back": 1000, "ignore_transfers": "false"})
    return st, (d or {}).get("pnl") or []


def account(idx: int):
    st, d = get("account", {"by": "index", "value": str(idx)})
    return st, (((d or {}).get("accounts") or [None])[0] if st == 200 else None)


def markten():
    """Alle perp-markten (ook inactieve): {market_id: symbool}."""
    st, d = get("orderBooks")
    out = {}
    for m in (d or {}).get("order_books", []):
        if m.get("market_type", "perp") == "perp":
            out[int(m["market_id"])] = m.get("symbol")
    return out
