"""F3-8: prijsdata met schijfcache (data/vol_tegenbar/cache, gitignored).

- Hyperliquid: dagslot (candleSnapshot 1d) en funding per uur (fundingHistory), via api.hyperliquid.xyz/info.
- Binance USDⓈ-M: 5m-klines per maand van data.binance.vision.
"""
from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HL = "https://api.hyperliquid.xyz/info"
BV = "https://data.binance.vision/data/futures/um/monthly/klines/{s}/5m/{s}-5m-{m}.zip"
SES = requests.Session()


def _post(payload, pogingen=6):
    for i in range(pogingen):
        try:
            r = SES.post(HL, json=payload, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"Hyperliquid-API faalt: {payload.get('type')}")


def hl_dag(cache: Path, coin: str, start_ms: int, eind_ms: int) -> pd.Series:
    p = cache / "hl_dag" / f"{coin}.parquet"
    if p.exists():
        d = pd.read_parquet(p)
    else:
        rij = _post({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1d", "startTime": start_ms,
                                                        "endTime": eind_ms}}) or []
        d = pd.DataFrame({"t": [int(x["t"]) for x in rij], "c": [float(x["c"]) for x in rij]})
        p.parent.mkdir(parents=True, exist_ok=True)
        d.to_parquet(p, index=False)
        time.sleep(0.3)
    if d.empty:
        return pd.Series(dtype=float)
    return pd.Series(d["c"].to_numpy(), index=pd.to_datetime(d["t"], unit="ms").dt.normalize()).sort_index()


def hl_funding(cache: Path, coin: str, start_ms: int, eind_ms: int) -> pd.Series:
    p = cache / "hl_funding" / f"{coin}.parquet"
    if p.exists():
        d = pd.read_parquet(p)
    else:
        tijden, rates, t = [], [], start_ms
        while t < eind_ms:
            rij = _post({"type": "fundingHistory", "coin": coin, "startTime": t, "endTime": eind_ms}) or []
            if not rij:
                break
            tijden += [int(x["time"]) for x in rij]
            rates += [float(x["fundingRate"]) for x in rij]
            nieuw = int(rij[-1]["time"]) + 1
            if nieuw <= t or len(rij) < 2:
                break
            t = nieuw
            time.sleep(0.25)
        d = pd.DataFrame({"t": tijden, "rate": rates}).drop_duplicates("t")
        p.parent.mkdir(parents=True, exist_ok=True)
        d.to_parquet(p, index=False)
    if d.empty:
        return pd.Series(dtype=float)
    # tijdstempel naar het hele uur (Hyperliquid zet er enkele ms achter)
    return pd.Series(d["rate"].to_numpy(), index=(d["t"].to_numpy() // 3_600_000) * 3_600_000).sort_index()


def bn_5m(cache: Path, sym: str, maand: str) -> pd.DataFrame | None:
    """5m-klines van één maand (index = open-tijd ms; open, close). None = bestaat niet (geen perp)."""
    p = cache / "bn_5m" / f"{sym}-{maand}.parquet"
    leeg = cache / "bn_5m" / f"{sym}-{maand}.geen"
    if p.exists():
        return pd.read_parquet(p)
    if leeg.exists():
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        try:
            r = SES.get(BV.format(s=sym, m=maand), timeout=60)
            break
        except requests.RequestException:
            time.sleep(3 * (i + 1))
    else:
        raise RuntimeError(f"data.binance.vision faalt: {sym} {maand}")
    if r.status_code == 404:
        leeg.touch()
        return None
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    raw = z.read(z.namelist()[0]).decode()
    eerste = raw.split("\n", 1)[0]
    d = pd.read_csv(io.StringIO(raw), header=0 if "open" in eerste else None)
    d = d.iloc[:, [0, 1, 4]]
    d.columns = ["t", "open", "close"]
    d = d.astype({"t": np.int64, "open": float, "close": float}).drop_duplicates("t").set_index("t").sort_index()
    d.to_parquet(p)
    return d
