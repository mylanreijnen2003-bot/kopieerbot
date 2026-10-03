"""Orderly stap 1b: hoe kom je bij oude trades (ouder dan 24 u)? Test varianten op een paar actieve adressen.
Gebruik: python -m orderly.probe2 <uitmap> <pool-top_rows.csv>"""
import json
import sys
import time

import pandas as pd

from orderly import probe as P

OUT = sys.argv[1]
DAG, NU = P.DAG, P.NU
P.OUT = OUT


def kort(j, n=3):
    rows, cur = P.rows_cursor(j)
    ts = [r.get("executed_timestamp") or r.get("created_time") or r.get("updated_time") or r.get("timestamp") for r in rows]
    ts = [t for t in ts if t]
    return {"success": j.get("success") if isinstance(j, dict) else None, "rijen": len(rows), "cursor": cur,
            "oudste": str(pd.Timestamp(min(ts), unit="ms")) if ts else None, "nieuwste": str(pd.Timestamp(max(ts), unit="ms")) if ts else None,
            "fout": None if (isinstance(j, dict) and j.get("success", True)) else str(j)[:400], "voorbeeld": rows[:n]}


def test(a):
    r = {"address": a}
    acc = P.rows_cursor(P.q("accounts", address=a))[0]
    r["accounts"] = [{k: x.get(k) for k in ("broker_id", "account_id", "account_type", "account_value")} for x in acc]
    t24, _ = P.alle("trades", max_pag=3, address=a, limit=1000)
    sym = pd.Series([t["symbol"] for t in t24]).value_counts().index[0] if t24 else "PERP_BTC_USDC"
    v = {}
    for naam, st, en in [("2d-1d", 2, 1), ("3d-2d", 3, 2), ("7d-0", 7, 0), ("8d-7d", 8, 7), ("30d-0", 30, 0), ("60d-30d", 60, 30),
                         ("120d-90d", 120, 90)]:
        v[naam] = kort(P.q("trades", address=a, start_time=NU - st * DAG, end_time=NU - en * DAG, limit=2000))
        v[naam + "_symbol"] = kort(P.q("trades", address=a, symbol=sym, start_time=NU - st * DAG, end_time=NU - en * DAG, limit=2000))
        v[naam + "_alleen_start"] = kort(P.q("trades", address=a, start_time=NU - st * DAG, limit=2000)) if en == 0 else None
    for x in acc[:3]:
        v["30d-2d_acc_" + x["account_id"][:8]] = kort(P.q("trades", address=a, account_id=x["account_id"], broker_id=x["broker_id"],
                                                         start_time=NU - 30 * DAG, end_time=NU - 2 * DAG, limit=2000))
    # cursor doorlopen met tijdvenster: komt het archief na de realtime-pagina's?
    rows, cur, pag, src = [], None, 0, []
    while pag < 30:
        j = P.q("trades", address=a, start_time=NU - 30 * DAG, end_time=NU, limit=2000, **({"cursor": cur} if cur else {}))
        rr, cur = P.rows_cursor(j)
        rows += rr; pag += 1; src.append(cur)
        if not cur or not rr:
            break
    ts = [x["executed_timestamp"] for x in rows]
    v["30d_doorlopen"] = {"rijen": len(rows), "paginas": pag, "oudste": str(pd.Timestamp(min(ts), unit="ms")) if ts else None, "cursors": src[:6]}
    v["historicalOrders"] = kort(P.q("historicalOrders", address=a))
    v["historicalOrders_30d"] = kort(P.q("historicalOrders", address=a, start_time=NU - 30 * DAG, end_time=NU - 2 * DAG, limit=500))
    v["portfolio_nieuwste3"] = kort(P.q("portfolio", address=a, limit=3), 3)
    for x in acc[:2]:
        v["portfolio_acc_" + x["account_id"][:8]] = kort(P.q("portfolio", address=a, account_id=x["account_id"], limit=3), 3)
    r["varianten"] = v
    P.log("getest", a[:10], {k: (x or {}).get("rijen") for k, x in v.items()})
    return r


def main():
    top = pd.read_csv(sys.argv[2])
    top = top[(top.q_symbol.isna())]
    actief = top.sort_values("volume_30d", ascending=False)
    # 2 drukke + 3 middelmatige (5-200 trades/24u) + 1 groot HL-adres dat ook op Orderly zat
    mid = actief[(actief.trade_count_24h >= 5) & (actief.trade_count_24h <= 200)]
    adressen = list(actief.address[:2]) + list(mid.address[:3]) + ["0xa8758904"]
    hl = pd.read_csv("orderly/hl_adressen.csv")
    adressen = [next((h for h in hl.address if h.startswith(a)), a) if len(a) < 42 else a for a in adressen]
    out = [test(a) for a in adressen]
    json.dump(out, open(f"{OUT}/stap1b_historie.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
