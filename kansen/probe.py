"""Probe van alle databronnen voor de kansen-tests. Schrijft alleen vormen/aantallen en afgekorte adressen.

python -m kansen.probe <outdir>
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import requests

HEX = re.compile(r"0x[0-9a-fA-F]{20,}")
B58 = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")


def red(s: str) -> str:
    s = HEX.sub(lambda m: m.group(0)[:6] + "…" + m.group(0)[-4:], s)
    return B58.sub(lambda m: m.group(0)[:5] + "…", s)


def show(obj, n=1500) -> str:
    try:
        t = json.dumps(obj, default=str)[:n]
    except Exception:
        t = str(obj)[:n]
    return red(t)


OUT: Path


def log(name: str, text: str):
    with open(OUT / f"{name}.txt", "a", encoding="utf-8") as f:
        f.write(text + "\n")


def req(method, url, name, **kw):
    try:
        r = requests.request(method, url, timeout=60, **kw)
        log(name, f"== {method} {url} -> {r.status_code} ({len(r.content)} bytes)")
        try:
            return r.status_code, r.json()
        except Exception:
            log(name, red(r.text[:800]))
            return r.status_code, None
    except Exception as e:
        log(name, f"== {method} {url} -> FOUT {e!r}")
        return None, None


def keys_of(x):
    if isinstance(x, dict):
        return sorted(x.keys())
    if isinstance(x, list) and x and isinstance(x[0], dict):
        return sorted(x[0].keys())
    return type(x).__name__


def probe_hl():
    n = "hl_vaults"
    st, data = req("GET", "https://stats-data.hyperliquid.xyz/Mainnet/vaults", n)
    vaults = data if isinstance(data, list) else []
    log(n, f"aantal vaults: {len(vaults)}")
    if vaults:
        log(n, "keys: " + show(keys_of(vaults)))
        log(n, "voorbeeld: " + show(vaults[0], 3000))
        closed = sum(1 for v in vaults if (v.get("summary") or v).get("isClosed"))
        log(n, f"isClosed: {closed}")
    st, s2 = req("POST", "https://api.hyperliquid.xyz/info", n, json={"type": "vaultSummaries"})
    log(n, "vaultSummaries: " + show(s2, 400))
    # details van 3 vaults: grootste TVL, een gesloten, en HLP
    addrs = []
    def addr(v):
        s = v.get("summary") or v
        return s.get("vaultAddress"), float(s.get("tvl") or 0), bool(s.get("isClosed")), s.get("name")
    rows = [addr(v) for v in vaults]
    rows = [r for r in rows if r[0]]
    rows.sort(key=lambda r: -r[1])
    if rows:
        addrs.append(rows[0][0])
        cl = [r for r in rows if r[2]]
        if cl:
            addrs.append(cl[0][0])
        hlp = [r for r in rows if r[3] and "hyperliquidity" in r[3].lower()]
        if hlp:
            addrs.append(hlp[0][0])
    for a in addrs:
        st, d = req("POST", "https://api.hyperliquid.xyz/info", n, json={"type": "vaultDetails", "vaultAddress": a})
        if not isinstance(d, dict):
            continue
        log(n, "details keys: " + show(sorted(d.keys())))
        for k in ("name", "apr", "leaderFraction", "leaderCommission", "isClosed", "allowDeposits", "maxDistributable"):
            log(n, f"  {k}: {show(d.get(k), 200)}")
        log(n, f"  followers: {len(d.get('followers') or [])}; voorbeeld {show((d.get('followers') or [None])[0], 400)}")
        for per, p in (d.get("portfolio") or []):
            av, pn = p.get("accountValueHistory") or [], p.get("pnlHistory") or []
            log(n, f"  portfolio {per}: av {len(av)} punten {show(av[:2], 200)} … {show(av[-1:], 120)}; pnl {len(pn)}")
        time.sleep(1)


def probe_nado():
    n = "nado"
    for url, body in [
        ("https://gateway.prod.nado.xyz/v1/query", {"type": "all_products"}),
        ("https://gateway.prod.nado.xyz/v1/query?type=all_products", None),
        ("https://gateway.prod.nado.xyz/v1/symbols", None),
    ]:
        st, d = req("POST" if body else "GET", url, n, json=body) if body else req("GET", url, n)
        log(n, show(d, 1500))
    for body in [
        {"matches": {"product_ids": [2], "limit": 3}},
        {"matches": {"product_ids": [2], "limit": 3, "isolated": False}},
        {"trades": {"product_id": 2, "limit": 3}},
    ]:
        st, d = req("POST", "https://archive.prod.nado.xyz/v1", n, json=body)
        log(n, "body " + json.dumps(body) + " -> " + show(d, 2500))


def probe_gmx():
    n = "gmx"
    url = "https://gmx.squids.live/gmx-synthetics-arbitrum:prod/api/graphql"
    q = {"query": '{ __type(name: "TradeAction") { fields { name type { name kind ofType { name } } } } }'}
    st, d = req("POST", url, n, json=q)
    log(n, show(d, 4000))
    q2 = {"query": "{ tradeActions(limit: 3, orderBy: timestamp_DESC) { id account eventName orderType isLong "
                   "sizeDeltaUsd executionPrice pnlUsd timestamp marketAddress } }"}
    st, d = req("POST", url, n, json=q2)
    log(n, show(d, 2500))
    q3 = {"query": "{ tradeActions(limit: 1, orderBy: timestamp_ASC) { timestamp } }"}
    st, d = req("POST", url, n, json=q3)
    log(n, "oudste: " + show(d, 300))


def probe_unlocks():
    n = "unlocks"
    st, d = req("GET", "https://api.llama.fi/emissions", n)
    if isinstance(d, list):
        log(n, f"emissions: {len(d)} items; keys {show(keys_of(d))}; voorbeeld {show(d[0], 1500)}")
        name = d[0].get("token") or d[0].get("name") or d[0].get("protocolId")
    else:
        log(n, show(d, 800))
    for u in ["https://api.llama.fi/emission/arbitrum", "https://api.llama.fi/emission/coingecko:arbitrum",
              "https://defillama-datasets.llama.fi/emissionsProtocolsList"]:
        st, d = req("GET", u, n)
        log(n, show(d, 1500))


def probe_kraken():
    n = "kraken"
    st, d = req("GET", "https://futures.kraken.com/derivatives/api/v3/instruments", n)
    if isinstance(d, dict):
        ins = d.get("instruments") or []
        pf = [i for i in ins if str(i.get("symbol", "")).startswith("PF_")]
        log(n, f"instruments {len(ins)}, PF_ {len(pf)}; voorbeeld {show(pf[:1], 800)}")
    now = int(time.time())
    st, d = req("GET", f"https://futures.kraken.com/api/charts/v1/trade/PF_XBTUSD/1d?from={now-86400*900}&to={now}", n)
    if isinstance(d, dict):
        c = d.get("candles") or []
        log(n, f"candles {len(c)}; eerste {show(c[:1])}; more {d.get('more_candles')}")
    st, d = req("GET", "https://futures.kraken.com/derivatives/api/v4/historicalfundingrates?symbol=PF_XBTUSD", n)
    log(n, show(d, 600))
    st, d = req("GET", "https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440", n)
    log(n, show(d, 300))


def probe_velocity():
    n = "velocity"
    st, d = req("GET", "https://data.velocity.exchange/openapi.json", n)
    if isinstance(d, dict):
        log(n, "paths: " + show(sorted((d.get("paths") or {}).keys()), 4000))
    for u in ["https://data.velocity.exchange/stats/leaderboard", "https://data.velocity.exchange/market/SOL-PERP/trades/2025/06/01",
              "https://data.drift.trade/market/SOL-PERP/trades/2025/06/01", "https://data.api.drift.trade/openapi.json"]:
        st, d = req("GET", u, n)
        log(n, show(d, 1200))


def main():
    global OUT
    OUT = Path(sys.argv[1])
    OUT.mkdir(parents=True, exist_ok=True)
    for f in (probe_hl, probe_nado, probe_gmx, probe_unlocks, probe_kraken, probe_velocity):
        try:
            f()
        except Exception as e:
            log("fouten", f"{f.__name__}: {e!r}")
    print("probe klaar")


if __name__ == "__main__":
    main()
