"""Probe 2: paginering Nado, Drift-velden, gratis unlock-data, GMX-marktnamen. Alleen vormen, afgekorte adressen.

python -m kansen.probe2 <outdir>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

from kansen import probe
from kansen.probe import keys_of, log, req, show


def nado():
    n = "nado2"
    A = "https://archive.prod.nado.xyz/v1"
    st, d = req("POST", A, n, json={"matches": {"product_ids": [2], "limit": 2}})
    if isinstance(d, dict):
        log(n, "top keys: " + show(sorted(d.keys())))
        for k, v in d.items():
            log(n, f"  {k}: {show(v, 1500)}")
    now = int(time.time())
    for body in [{"matches": {"product_ids": [2], "limit": 2, "max_time": now - 200 * 86400}},
                 {"matches": {"product_ids": [2], "limit": 2, "max_time": 1735689600}},
                 {"matches": {"product_ids": [2], "limit": 2, "idx": "1000"}},
                 {"subaccounts": {"limit": 3}},
                 {"subaccounts": {}},
                 {"events": {"product_ids": [2], "limit": 2, "event_types": ["match_orders"]}}]:
        st, d = req("POST", A, n, json=body)
        log(n, "body " + json.dumps(body) + " -> " + show(d, 1500))
    st, d = req("POST", A, n, json={"matches": {"product_ids": [2], "limit": 500}})
    if isinstance(d, dict) and d.get("matches"):
        log(n, f"limit 500 -> {len(d['matches'])} matches")


def drift():
    n = "drift"
    st, d = req("GET", "https://data.drift.trade/market/SOL-PERP/trades/2026/01/15", n)
    if isinstance(d, dict):
        recs = d.get("records") or []
        log(n, f"records {len(recs)}, meta {show(d.get('meta'))}")
        if recs:
            log(n, "keys: " + show(sorted(recs[0].keys()), 3000))
            r = dict(recs[0])
            for k in ("taker", "maker", "filler", "txSig", "user", "referrer"):
                r.pop(k, None)
            log(n, "record: " + show(r, 3000))
            acc = next((x.get("taker") for x in recs if x.get("taker")), None)
            if acc:
                for u in [f"https://data.drift.trade/user/{acc}/trades/2026/01", f"https://data.drift.trade/user/{acc}/trades"]:
                    st, e = req("GET", u, n)
                    if isinstance(e, dict):
                        rr = e.get("records") or []
                        log(n, f"  user: {len(rr)} records, meta {show(e.get('meta'))}")
    st, d = req("GET", "https://data.drift.trade/market/SOL-PERP/trades/2026/01/15?page=2", n)
    st, d = req("GET", "https://data.drift.trade/openapi.json", n)
    if isinstance(d, dict):
        log(n, "paths: " + show(sorted((d.get("paths") or {}).keys()), 3000))
    for m in ("BTC-PERP", "ETH-PERP", "SOL-PERP"):
        st, d = req("GET", f"https://data.drift.trade/market/{m}/trades/2026/03/31", n)
        if isinstance(d, dict):
            log(n, f"{m} 31-3: {len(d.get('records') or [])} records, meta {show(d.get('meta'))}")


def unlocks():
    n = "unlocks2"
    for u in ["https://defillama-datasets.llama.fi/emissions/arbitrum", "https://defillama-datasets.llama.fi/emissions/arbitrum.json",
              "https://defillama-datasets.llama.fi/emissions/hyperliquid", "https://defillama-datasets.llama.fi/emissionsIndex",
              "https://defillama-datasets.llama.fi/emissions/jupiter"]:
        st, d = req("GET", u, n)
        if isinstance(d, (dict, list)):
            log(n, "keys: " + show(keys_of(d)))
            log(n, show(d, 2500))


def gmx():
    n = "gmx2"
    st, d = req("POST", "https://gmx.squids.live/gmx-synthetics-arbitrum:prod/api/graphql", n,
                json={"query": "{ marketInfos(limit: 3) { id indexTokenAddress longTokenAddress } }"})
    log(n, show(d, 800))
    st, d = req("GET", "https://arbitrum-api.gmxinfra.io/tokens", n)
    log(n, show(d, 800))
    st, d = req("GET", "https://arbitrum-api.gmxinfra.io/markets", n)
    log(n, show(d, 800))


def main():
    probe.OUT = Path(sys.argv[1])
    probe.OUT.mkdir(parents=True, exist_ok=True)
    for f in (nado, drift, unlocks, gmx):
        try:
            f()
        except Exception as e:
            log("fouten2", f"{f.__name__}: {e!r}")
    print("probe2 klaar")


if __name__ == "__main__":
    main()
