"""Backtest stap A2: hulpdata.
- av:      wekelijkse accountwaarde per wallet uit de dataset (av_archive)
- funding: uurlijkse fundingrates per munt via de Hyperliquid-API
- beurzen: welke munten op Kraken (perps) en Bitvavo (spot) staan
Gebruik: python -m bt.prep <av|funding|beurzen> <uitmap>
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

import pandas as pd
import requests

from bot import hl

REPO = "craftify2221/hyperliquid-fills-raw"
T0, T1 = "2025-07-28", "2026-08-19"


def av(out):
    from huggingface_hub import HfApi, hf_hub_download
    api = HfApi()
    wanted = set(pd.read_parquet("data/universe.parquet").address.str.lower())
    dates = sorted(x.path.split("date=")[1] for x in api.list_repo_tree(REPO, repo_type="dataset", path_in_repo="av_archive"))
    dates = [d for d in dates if T0 <= d[:10] <= T1]
    keep = dates[::7] + ([dates[-1]] if dates and dates[-1] not in dates[::7] else [])
    frames = []
    for d in keep:
        files = [x.path for x in api.list_repo_tree(REPO, repo_type="dataset", path_in_repo=f"av_archive/date={d}")
                 if x.path.endswith(".parquet")]
        tmp = tempfile.mkdtemp()
        try:
            for f in files:
                for attempt in range(5):
                    try:
                        p = hf_hub_download(REPO, f, repo_type="dataset", local_dir=tmp)
                        break
                    except Exception as exc:  # noqa: BLE001
                        print("av download fout", exc)
                        time.sleep(15 * (attempt + 1))
                t = pd.read_parquet(p, columns=["user", "account_value"])
                t["user"] = t.user.str.lower()
                t = t[t.user.isin(wanted)].groupby("user", as_index=False)["account_value"].sum()
                t["date"] = d[:10]
                frames.append(t)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        print("av", d, flush=True)
    pd.concat(frames).rename(columns={"user": "address"}).to_parquet(f"{out}/av.parquet", index=False)


def funding(out):
    meta = hl.info({"type": "meta"}, weight=20)
    coins = [u["name"] for u in meta.get("universe", [])]
    s, e = hl.HOUR * (pd.Timestamp(T0).value // 10**6 // hl.HOUR), pd.Timestamp(T1).value // 10**6
    rows = []
    for i, c in enumerate(coins):
        for h, r in hl.funding(c, s, e).items():
            rows.append((c, h, r))
        if i % 20 == 0:
            print(f"funding {i}/{len(coins)}", flush=True)
    pd.DataFrame(rows, columns=["coin", "hour", "rate"]).to_parquet(f"{out}/funding.parquet", index=False)


def beurzen(out):
    res = {}
    try:
        ins = requests.get("https://futures.kraken.com/derivatives/api/v3/instruments", timeout=60).json()["instruments"]
        res["kraken"] = sorted({i["symbol"][3:-3].replace("XBT", "BTC") for i in ins
                                if i.get("symbol", "").startswith("PF_") and i["symbol"].endswith("USD") and i.get("tradeable", True)})
    except Exception as exc:  # noqa: BLE001
        print("::error::kraken lijst mislukt", exc)
    try:
        mk = requests.get("https://api.bitvavo.com/v2/markets", timeout=60).json()
        res["bitvavo"] = sorted({m["base"] for m in mk if m.get("status") == "trading"})
    except Exception as exc:  # noqa: BLE001
        print("::error::bitvavo lijst mislukt", exc)
    print({k: len(v) for k, v in res.items()})
    json.dump(res, open(f"{out}/beurzen.json", "w"), indent=0)


if __name__ == "__main__":
    os.makedirs(sys.argv[2], exist_ok=True)
    {"av": av, "funding": funding, "beurzen": beurzen}[sys.argv[1]](sys.argv[2])
