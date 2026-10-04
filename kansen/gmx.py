"""V3: GMX v2 (Arbitrum) traders, via de gratis Subsquid-GraphQL.

python -m kansen.gmx fetch <outdir>                 # alle uitgevoerde orders sinds START (parquet)
python -m kansen.gmx analyse <datadir> <res> <priv>

Trade = positie (account, markt, long/short) van 0 naar 0. Rendement = som basePnlUsd / som afgebouwde grootte
(= koersrendement op de positie) − 0,2% kosten. GMX werkt met orakelprijzen: er zijn geen makers.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import requests

URL = "https://gmx.squids.live/gmx-synthetics-arbitrum:prod/api/graphql"
START = int(pd.Timestamp("2025-10-01").timestamp())
KOSTEN = 0.002
WANT = ["id", "account", "marketAddress", "isLong", "orderType", "sizeDeltaUsd", "basePnlUsd", "pnlUsd", "timestamp",
        "eventName"]
INCREASE = {2, 3}           # MarketIncrease, LimitIncrease
DECREASE = {4, 5, 6, 7}     # MarketDecrease, LimitDecrease, StopLossDecrease, Liquidation
E30 = 1e30


def gql(q, tries=8):
    for i in range(tries):
        try:
            r = requests.post(URL, json={"query": q}, timeout=120)
            if r.status_code == 200:
                j = r.json()
                if "errors" not in j:
                    return j["data"]
                print("gql-fout:", str(j["errors"])[:300])
        except requests.RequestException:
            pass
        time.sleep(5 * (i + 1))
    raise RuntimeError("GMX squid faalt")


def fields():
    d = gql('{ __type(name: "TradeAction") { fields { name } } }')
    have = {f["name"] for f in d["__type"]["fields"]}
    miss = [f for f in ("account", "marketAddress", "isLong", "orderType", "sizeDeltaUsd", "timestamp") if f not in have]
    if miss:
        raise RuntimeError(f"velden ontbreken: {miss}")
    return [f for f in WANT if f in have]


def fetch(out: str):
    fl = fields()
    print("velden:", fl)
    Path(out).mkdir(parents=True, exist_ok=True)
    rows, ts, last_ids, part = [], START, set(), 0
    t0 = time.time()
    where_ev = 'eventName_eq: "OrderExecuted", ' if "eventName" in fl else ""
    while True:
        q = (f'{{ tradeActions(limit: 1000, orderBy: [timestamp_ASC, id_ASC], where: {{ {where_ev}timestamp_gte: {ts} }}) '
             f'{{ {" ".join(fl)} }} }}')
        batch = gql(q)["tradeActions"]
        new = [b for b in batch if b["id"] not in last_ids]
        rows += new
        if len(batch) < 1000:
            break
        mx = max(int(b["timestamp"]) for b in batch)
        if mx == ts:   # meer dan 1000 in dezelfde seconde: id-cursor binnen de seconde
            last_ids |= {b["id"] for b in batch}
        else:
            last_ids = {b["id"] for b in batch if int(b["timestamp"]) == mx}
            ts = mx
        if len(rows) >= 500_000:
            pd.DataFrame(rows).to_parquet(Path(out) / f"gmx-{part:03d}.parquet")
            part += 1
            rows = []
            print(f"{part} delen, tot {pd.Timestamp(ts, unit='s')}, {time.time()-t0:.0f}s", flush=True)
    if rows:
        pd.DataFrame(rows).to_parquet(Path(out) / f"gmx-{part:03d}.parquet")
    print(f"klaar tot {pd.Timestamp(ts, unit='s')}, {time.time()-t0:.0f}s")


def trades(df: pd.DataFrame):
    """df van één account → [(coin, open_ms, close_ms, r)]"""
    out, open_ = [], {}
    for r in df.itertuples(index=False):
        key = (r.marketAddress, bool(r.isLong))
        ot = int(r.orderType)
        size = float(r.sizeDeltaUsd) / E30
        t = int(r.timestamp) * 1000
        if ot in INCREASE:
            p = open_.setdefault(key, {"size": 0.0, "t": t, "pnl": 0.0, "dec": 0.0})
            p["size"] += size
        elif ot in DECREASE and key in open_:
            p = open_[key]
            pnl = getattr(r, "basePnlUsd", None)
            if pnl is None or pd.isna(pnl):
                pnl = getattr(r, "pnlUsd", 0.0)
            p["pnl"] += float(pnl or 0) / E30
            p["dec"] += size
            p["size"] -= size
            if p["size"] <= max(1.0, 1e-6 * p["dec"]) or ot == 7:
                if p["dec"] > 0:
                    rr = p["pnl"] / p["dec"] - KOSTEN
                    out.append((key[0][:8] + ("L" if key[1] else "S"), p["t"], t, max(rr, -1.0)))
                open_.pop(key)
    return out


def markt_namen() -> dict:
    """marketAddress (eerste 8 tekens, zoals in trades) -> symbool van de index-token."""
    try:
        mi = gql("{ marketInfos(limit: 1000) { id indexTokenAddress } }")["marketInfos"]
        tok = requests.get("https://arbitrum-api.gmxinfra.io/tokens", timeout=60).json()
        tok = tok.get("tokens", tok) if isinstance(tok, dict) else tok
        sym = {t["address"].lower(): t["symbol"] for t in tok}
        return {m["id"][:8].lower(): sym.get((m.get("indexTokenAddress") or "").lower(), m["id"][:8]) for m in mi}
    except Exception as e:
        print("marktnamen niet op te halen:", repr(e)[:200])
        return {}


def analyse(datadir: str, res: str, priv: str):
    from kansen.kies import rapport
    namen = markt_namen()
    files = sorted(Path(datadir).rglob("gmx-*.parquet"))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True).drop_duplicates("id")
    df["orderType"] = df.orderType.astype(int)
    df = df[df.orderType.isin(INCREASE | DECREASE)].sort_values(["account", "timestamp", "id"])
    data = {}
    for acc, g in df.groupby("account", sort=False):
        if len(g) < 150:
            continue
        tr = [(namen.get(c[:8].lower(), c[:8]) + c[8:], a, b, r) for c, a, b, r in trades(g)]
        if len(tr) < 100:
            continue
        data[acc] = {"trades": tr, "fills_ts": (g.timestamp.astype("int64") * 1000).values, "maker": None}
    noot = [f"Bron: GMX v2 Arbitrum, uitgevoerde orders vanaf {pd.Timestamp(START, unit='s'):%Y-%m-%d} "
            f"({len(df):,} acties, {df.account.nunique():,} accounts). Geen makers (orakelprijzen); kosten 0,2% per trade."]
    rapport("V3 GMX v2-traders", data, res, priv, noot)


if __name__ == "__main__":
    if sys.argv[1] == "fetch":
        fetch(sys.argv[2])
    else:
        analyse(*sys.argv[2:5])
