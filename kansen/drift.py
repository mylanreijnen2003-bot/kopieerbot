"""V5: Drift-traders (Solana, nu Velocity) op de oude Drift-data (vóór de hack van 1-4-2026).

python -m kansen.drift fetch <shard> <n> <out>
python -m kansen.drift analyse <datadir> <res> <priv>

Bron: data.drift.trade/market/<SYM>/trades/<y>/<m>/<d> (alle fills met taker- en maker-account).
Positie per account en markt = som van gevulde hoeveelheden sinds het begin van de data (1-6-2025); juni = inloop.
Posities van vóór 1-6-2025 kloppen dan niet; zulke accounts sluiten zelden 'plat' en vallen meestal af (< 100 trades).
Knip 31-12-2025, test 1-1 t/m 31-3-2026 (vooraf vastgelegd).
"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

U = "https://data.drift.trade/market"
START = date(2025, 6, 1)
END = date(2026, 3, 31)
SYMS = ["SOL", "BTC", "ETH", "APT", "1MBONK", "POL", "ARB", "DOGE", "BNB", "SUI", "1MPEPE", "OP", "RENDER", "XRP", "HNT",
        "INJ", "LINK", "RLB", "PYTH", "TIA", "JTO", "SEI", "AVAX", "WIF", "JUP", "DYM", "TAO", "W", "KMNO", "TNSR", "DRIFT",
        "CLOUD", "IO", "ZEX", "POPCAT", "1KWEN", "TRUMP", "MELANIA", "HYPE", "FARTCOIN", "PENGU", "AI16Z", "BERA", "KAITO",
        "IP", "LAUNCHCOIN", "PUMP", "ADA", "LTC", "ZEC", "XPL", "ASTER", "ENA", "TON", "NEAR", "MOODENG", "GOAT", "PNUT",
        "MOTHER", "ME", "TRX", "XMR", "VIRTUAL", "S", "MNT"]


def get(url, tries=6):
    for i in range(tries):
        try:
            r = requests.get(url, timeout=120)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except (requests.RequestException, ValueError):
            pass
        time.sleep(4 * (i + 1))
    return None


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def day_rows(sym, d):
    rows, page = [], 1
    while True:
        j = get(f"{U}/{sym}-PERP/trades/{d.year}/{d.month:02d}/{d.day:02d}" + (f"?page={page}" if page > 1 else ""))
        if not j:
            break
        for r in j.get("records") or []:
            if r.get("action") != "fill" or r.get("marketType") != "perp":
                continue
            base, quote = f(r.get("baseAssetAmountFilled")), f(r.get("quoteAssetAmountFilled"))
            if not base or not quote:
                continue
            px = quote / base
            ts = int(r["ts"]) * 1000
            for side in ("taker", "maker"):
                acc = r.get(side)
                dirn = r.get(f"{side}OrderDirection")
                if not acc or dirn not in ("long", "short"):
                    continue
                rows.append({"ts": ts, "acc": acc, "sym": sym, "signed": base if dirn == "long" else -base, "px": px,
                             "maker": side == "maker", "exist": f(r.get(f"{side}ExistingBaseAssetAmount")),
                             "id": f"{r.get('fillRecordId')}-{side}"})
        nxt = (j.get("meta") or {}).get("nextPage")
        if not nxt:
            break
        page = nxt
    return rows


def fetch(shard: int, nsh: int, out: str):
    mine = [s for i, s in enumerate(SYMS) if i % nsh == shard]
    Path(out).mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    for sym in mine:
        if not get(f"{U}/{sym}-PERP/trades/2026/01/15"):
            print(f"{sym}: geen data")
            continue
        rows, d = [], START
        while d <= END:
            rows += day_rows(sym, d)
            d += timedelta(days=1)
        if rows:
            pd.DataFrame(rows).to_parquet(Path(out) / f"drift-{sym}.parquet")
        print(f"{sym}: {len(rows)} fills, {time.time()-t0:.0f}s", flush=True)


def analyse(datadir: str, res: str, priv: str):
    from bt.vast_select import trades_pct
    from kansen.kies import rapport
    df = pd.concat([pd.read_parquet(p) for p in sorted(Path(datadir).rglob("drift-*.parquet"))], ignore_index=True)
    df = df.drop_duplicates("id").sort_values(["acc", "ts", "id"])
    inloop = int(pd.Timestamp("2025-07-01").value // 10**6)
    data = {}
    for acc, g in df.groupby("acc", sort=False):
        if len(g) < 150:
            continue
        pos, fl = {}, []
        for r in g.itertuples(index=False):
            start = pos.get(r.sym, 0.0)
            after = start + r.signed
            if abs(after) < 1e-9:
                after = 0.0
            pos[r.sym] = after
            if r.ts >= inloop:
                fl.append({"time": int(r.ts), "coin": r.sym, "start": round(start, 9), "after": round(after, 9), "px": r.px})
        tr = trades_pct(fl)
        if len(tr) < 100:
            continue
        data[acc] = {"trades": tr, "fills_ts": g.ts.values, "maker": float(g.maker.mean())}
    knip = int(pd.Timestamp("2026-01-01").value // 10**6)
    eind = int(pd.Timestamp("2026-04-01").value // 10**6)
    noot = [f"Bron: data.drift.trade, {df.sym.nunique()} perp-markten, {len(df):,} fill-kanten, {df.acc.nunique():,} accounts "
            f"(1-6-2025 t/m 31-3-2026, juni = inloop). Posities deels gereconstrueerd (zie code). Kosten 0,2% per trade."]
    rapport("V5 Drift-traders (historisch)", data, res, priv, noot, knip=knip, eind=eind,
            robuust=("2025-10-01", "2025-11-01", "2025-12-01"), vandaag=False)


if __name__ == "__main__":
    if sys.argv[1] == "fetch":
        fetch(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4])
    else:
        analyse(*sys.argv[2:5])
