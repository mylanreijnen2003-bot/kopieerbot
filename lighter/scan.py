"""Lighter pool-scan: alle account-indexen aflopen (onafhankelijk van Hyperliquid).

  python -m lighter.scan max                       -> hoogste bestaande account-index (+ slices naar $GITHUB_OUTPUT)
  python -m lighter.scan slice <van> <tot> <uit>   -> scan van een bereik; kandidaten volledig ophalen

Kandidaat = laatste fill op/na KANDIDAAT_VANAF (nodig om rond de knipdatum actief te zijn) en niet hyperdruk
(eerste pagina van 100 fills beslaat >= 16 uur, anders > 150 fills/dag en geen 3 maanden historie in Lighter's
3.000-fill-limiet). Logs: alleen aantallen.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

from lighter import api

GENESIS_MS = 1737072000000
KANDIDAAT_VANAF = int(pd.Timestamp("2026-07-25").value // 10**6)
SLICE = int(os.environ.get("SLICE", "30000"))
BUDGET_S = float(os.environ.get("BUDGET_S", str(5 * 3600)))


def bestaat(idx: int) -> bool:
    st, acc = api.account(idx)
    return acc is not None


def rond(n: int) -> bool:
    """Bestaat er een account op of vlak na n? (kleine gaten in de nummering overbruggen)"""
    return any(bestaat(n + k) for k in range(0, 40, 5))


def hoogste_index() -> int:
    hi = 1024
    while rond(hi * 2):
        hi *= 2
    lo, hi = hi, hi * 2
    while hi - lo > 50:
        mid = (lo + hi) // 2
        if rond(mid):
            lo = mid
        else:
            hi = mid
    return lo


def fill_rij(idx: int, t: dict):
    ask, bid = t.get("ask_account_id"), t.get("bid_account_id")
    if ask == bid or idx not in (ask, bid):
        return None
    maker_ask = bool(t.get("is_maker_ask"))
    koper = idx == bid
    maker = maker_ask if not koper else not maker_ask
    voor = t.get("maker_position_size_before") if maker else t.get("taker_position_size_before")
    return {"idx": idx, "ts": api.ms(t["timestamp"]), "tid": int(t.get("trade_id") or 0), "market_id": int(t["market_id"]),
            "kind": t.get("market_kind"), "type": t.get("type"), "size": float(t.get("size") or 0),
            "px": float(t.get("price") or 0), "usd": float(t.get("usd_amount") or 0), "koper": koper, "maker": maker,
            "pos_voor": float(voor) if voor not in (None, "") else None}


def scan_slice(van: int, tot: int, uit: Path):
    uit.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    scan, fills, pnls, accs = [], [], [], {}
    gestopt_bij = None
    for idx in range(van, tot):
        if time.time() - t0 > BUDGET_S:
            gestopt_bij = idx
            break
        st, tr, cursor = api.fills_pagina(idx)
        if st != 200:
            scan.append({"idx": idx, "status": st, "n": 0})
            continue
        ts = [api.ms(t["timestamp"]) for t in tr]
        rij = {"idx": idx, "status": st, "n": len(tr), "laatste": max(ts) if ts else None, "kandidaat": False, "druk": False}
        if ts and max(ts) >= KANDIDAAT_VANAF:
            if len(tr) == 100 and max(ts) - min(ts) < 16 * 3600 * 1000:
                rij["druk"] = True
            else:
                rij["kandidaat"] = True
                alle = api.alle_fills(idx, eerste=tr, cursor=cursor)
                fills += [r for r in (fill_rij(idx, t) for t in alle) if r]
                rij["fills_totaal"] = len(alle)
                rij["limiet_geraakt"] = bool(len(alle) >= 2900)
                _, rows = api.pnl(idx, GENESIS_MS, int(time.time() * 1000))
                for p in rows:
                    pnls.append({"idx": idx, "ts": api.ms(p["timestamp"]), "trade_pnl": float(p.get("trade_pnl") or 0),
                                 "inflow": float(p.get("inflow") or 0), "outflow": float(p.get("outflow") or 0)})
                _, acc = api.account(idx)
                if acc:
                    accs[idx] = {"l1": acc.get("l1_address"), "equity": float(acc.get("total_asset_value") or 0),
                                 "posities": [{k: p.get(k) for k in ("market_id", "symbol", "sign", "position", "position_value",
                                                                      "avg_entry_price", "unrealized_pnl", "liquidation_price")}
                                              for p in acc.get("positions") or [] if float(p.get("position") or 0) != 0]}
        scan.append(rij)
        if len(scan) % 2000 == 0:
            print(f"slice: {len(scan)} gescand, {sum(r.get('kandidaat', False) for r in scan)} kandidaten, "
                  f"{round(time.time() - t0)} s", flush=True)
    pd.DataFrame(scan).to_parquet(uit / "scan.parquet")
    pd.DataFrame(fills).to_parquet(uit / "fills.parquet")
    pd.DataFrame(pnls).to_parquet(uit / "pnl.parquet")
    (uit / "accounts.json").write_text(json.dumps({str(k): v for k, v in accs.items()}))
    meta = {"van": van, "tot": tot, "gestopt_bij": gestopt_bij, "gescand": len(scan), "seconden": round(time.time() - t0),
            "calls": {str(k): v for k, v in api.CALLS.items()}}
    (uit / "meta.json").write_text(json.dumps(meta))
    print("slice klaar:", json.dumps({k: v for k, v in meta.items() if k not in ("van", "tot", "gestopt_bij")}), flush=True)


def main():
    if sys.argv[1] == "max":
        m = hoogste_index()
        jobs = int(os.environ.get("JOBS", "20"))
        stap = -(-(m + 1000) // jobs)
        slices = [[a, min(a + stap, m + 1000)] for a in range(1, m + 1000, stap)]
        print(f"hoogste index ~{m}, {len(slices)} slices", flush=True)
        with open(os.environ.get("GITHUB_OUTPUT", "/dev/null"), "a") as f:
            f.write(f"slices={json.dumps(slices)}\n")
    else:
        scan_slice(int(sys.argv[2]), int(sys.argv[3]), Path(sys.argv[4]))


if __name__ == "__main__":
    main()
