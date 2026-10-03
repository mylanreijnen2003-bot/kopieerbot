"""Lighter probe 2: pnl-semantiek, trades-retentie/filter, leaderboard, websocket-URL.

Alleen aantallen en afgekorte adressen naar bestand/log (repo is publiek).
Gebruik: python -m lighter.probe2 <res_dir> <uit_dir>
"""
import asyncio
import csv
import json
import sys
import time
from pathlib import Path

from lighter.probe import (DAG, GENESIS_MS, NOW_MS, CALLS, account, datum, get,
                           kort, lees_adressen, ms, subaccounts)


def pnl_rows(idx):
    st, d = get("pnl", {"by": "index", "value": str(idx), "resolution": "1d",
                        "start_timestamp": GENESIS_MS, "end_timestamp": NOW_MS,
                        "count_back": 1000, "ignore_transfers": "true"})
    rows = (d or {}).get("pnl") or []
    st2, d2 = get("pnl", {"by": "index", "value": str(idx), "resolution": "1d",
                          "start_timestamp": GENESIS_MS, "end_timestamp": NOW_MS,
                          "count_back": 1000, "ignore_transfers": "false"})
    rows2 = (d2 or {}).get("pnl") or []
    return sorted(rows, key=lambda r: r["timestamp"]), sorted(rows2, key=lambda r: r["timestamp"])


def alle_trades(idx, max_pag=100):
    out, cursor = [], None
    for _ in range(max_pag):
        p = {"account_index": idx, "sort_by": "timestamp", "limit": 100}
        if cursor:
            p["cursor"] = cursor
        st, d = get("trades", p, pauze=0.2)
        if st != 200 or not d:
            break
        tr = d.get("trades") or []
        out += tr
        cursor = d.get("next_cursor")
        if not tr or not cursor:
            break
    return out


def maand_delta(rows, k):
    m = {}
    for r in rows:
        m[datum(r["timestamp"])[:7]] = float(r.get(k) or 0)
    ks = sorted(m)
    return {ks[i]: round(m[ks[i]] - m[ks[i - 1]], 0) for i in range(1, len(ks))}


def main():
    res, uit = sys.argv[1], Path(sys.argv[2])
    uit.mkdir(parents=True, exist_ok=True)
    rap = {}
    vast5, pool, _ = lees_adressen(res)

    # treffers opnieuw (alleen vast5 + pool)
    rijen = []
    semantiek = []
    retentie = []
    for label, lijst in [("vast5", vast5), ("pool", pool)]:
        for a in lijst:
            st, idxs = subaccounts(a)
            for j, idx in enumerate(idxs):
                st, acc = account(idx)
                if not acc:
                    continue
                eq_nu = float(acc.get("total_asset_value") or 0)
                coll = float(acc.get("collateral") or 0)
                upnl = sum(float(p.get("unrealized_pnl") or 0) for p in acc.get("positions") or [])
                open_pos = sum(1 for p in acc.get("positions") or [] if float(p.get("position") or 0) != 0)
                r_ign, r_all = pnl_rows(idx)
                tr = alle_trades(idx)
                ts = sorted(ms(t["timestamp"]) for t in tr)
                eigen = sum(1 for t in tr if idx in (t.get("ask_account_id"), t.get("bid_account_id")))
                last = r_ign[-1] if r_ign else {}
                last_all = r_all[-1] if r_all else {}
                # semantiek: vergelijk laatste punt met huidige equity
                if len(semantiek) < 6 and last:
                    semantiek.append({
                        "eq_nu(total_asset_value)": round(eq_nu, 0), "collateral": round(coll, 0), "upnl": round(upnl, 0),
                        "ignore_true_laatste": {k: round(float(v), 0) for k, v in last.items() if k != "timestamp" and float(v or 0) != 0},
                        "ignore_false_laatste": {k: round(float(v), 0) for k, v in last_all.items() if k != "timestamp" and float(v or 0) != 0},
                        "ignore_true_eerste": {k: round(float(v), 0) for k, v in r_ign[0].items() if k != "timestamp" and float(v or 0) != 0},
                    })
                # pnl gecorrigeerd voor stortingen: trade_pnl - (inflow - outflow)  [hypothese A]
                def corr(r):
                    return float(r.get("trade_pnl") or 0) - (float(r.get("inflow") or 0) - float(r.get("outflow") or 0))

                def delta(rows, f, dagen):
                    if not rows:
                        return None
                    grens = NOW_MS - dagen * DAG
                    voor = [r for r in rows if ms(r["timestamp"]) <= grens]
                    return round(f(rows[-1]) - f(voor[-1]), 0) if voor else None

                n30 = sum(1 for t in ts if t > NOW_MS - 30 * DAG)
                n90 = sum(1 for t in ts if t > NOW_MS - 90 * DAG)
                rij = {"adres": kort(a), "bron": label, "sub": j, "equity": round(eq_nu, 0), "open_pos": open_pos,
                       "upnl": round(upnl, 0), "fills": len(tr), "fills_eigen_pct": round(100 * eigen / len(tr), 1) if tr else None,
                       "fills_30d": n30, "fills_90d": n90,
                       "eerste_fill": datum(ts[0]) if ts else "", "laatste_fill": datum(ts[-1]) if ts else "",
                       "eerste_pnl": datum(r_ign[0]["timestamp"]) if r_ign else "",
                       "pnl_ruw_90d": delta(r_ign, lambda r: float(r.get("trade_pnl") or 0), 90),
                       "pnl_corr_30d": delta(r_ign, corr, 30), "pnl_corr_90d": delta(r_ign, corr, 90),
                       "pnl_allTransfers_90d": delta(r_all, lambda r: float(r.get("trade_pnl") or 0), 90),
                       "maanden_corr": json.dumps(maand_delta([dict(r, c=corr(r)) for r in r_ign], "c"))}
                rijen.append(rij)
                if ts:
                    retentie.append({"eerste_pnl": rij["eerste_pnl"], "eerste_fill": rij["eerste_fill"], "fills": len(tr),
                                     "paginas_vol": len(tr) >= 10000})
        print(f"probe2 {label}: klaar, rijen {len(rijen)}", flush=True)

    with open(uit / "stap0b.csv", "w", newline="") as f:
        w = csv.DictWriter(f, list(rijen[0].keys()))
        w.writeheader()
        w.writerows(rijen)
    rap["pnl_semantiek"] = semantiek
    rap["retentie"] = retentie

    # leaderboard
    lb = {}
    for params in [{}, {"type": "all"}, {"type": "weekly"}, {"type": "monthly"}, {"limit": 1000}, {"type": "all", "limit": 1000},
                   {"type": "all", "l1_address": "0x0000000000000000000000000000000000000000"}]:
        st, d = get("leaderboard", params)
        e = (d or {}).get("entries") or []
        lb[json.dumps(params)] = {"status": st, "n": len(e), "velden": sorted(e[0].keys()) if e else None,
                                  "fout": (d or {}).get("message") if st != 200 else None,
                                  "andere_velden": sorted(k for k in (d or {}) if k != "entries")}
    rap["leaderboard"] = lb

    # websocket-URL's
    async def ws_test():
        import websockets
        out = {}
        for url in ["wss://mainnet.zklighter.elliot.ai/stream", "wss://mainnet.zklighter.elliot.ai/stream?readonly=true",
                    "wss://mainnet.zklighter.elliot.ai/stream/", "wss://mainnet.zklighter.elliot.ai/ws"]:
            try:
                async with websockets.connect(url, open_timeout=15, additional_headers={"Origin": "https://app.lighter.xyz"}) as w:
                    msgs = []
                    await w.send(json.dumps({"type": "subscribe", "channel": "trade/1"}))
                    eind = time.time() + 8
                    while time.time() < eind and len(msgs) < 3:
                        try:
                            m = json.loads(await asyncio.wait_for(w.recv(), timeout=eind - time.time()))
                            msgs.append({"type": m.get("type"), "velden": sorted(m.keys())[:12]})
                        except asyncio.TimeoutError:
                            break
                    out[url] = msgs
            except Exception as ex:
                body = getattr(getattr(ex, "response", None), "body", b"") or b""
                out[url] = f"{type(ex).__name__}: {str(ex)[:120]} {body[:200]!r}"
        return out
    rap["ws"] = asyncio.run(ws_test())
    rap["calls"] = {str(k): v for k, v in CALLS.items()}
    (uit / "rapport2.json").write_text(json.dumps(rap, indent=1, ensure_ascii=False))
    print("klaar", dict(CALLS), flush=True)


if __name__ == "__main__":
    main()
