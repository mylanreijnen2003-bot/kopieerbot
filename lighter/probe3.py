"""Lighter probe 3: pool onafhankelijk van Hyperliquid.

A. Kan `trades?market_id=` naar een datum in het verleden springen (param `from`)?
   Dan kun je rond een knipdatum alle markten bemonsteren (vrij van survivorship).
B. Zijn account-indexen opeenvolgend? Hoeveel accounts bestaan er, en hoeveel
   handelden in de laatste 90 dagen? Dan kun je ALLE accounts aflopen.
Alleen aantallen naar bestand/log (repo is publiek).
Gebruik: python -m lighter.probe3 <uit_dir>
"""
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

from lighter.probe import CALLS, DAG, NOW_MS, datum, get, markten, ms

KNIP_MS = 1753401600000 + 365 * DAG  # 25-07-2026 00:00 UTC


def bereik(tr):
    ts = [ms(t["timestamp"]) for t in tr]
    return {"n": len(tr), "oudste": datum(min(ts)) if ts else "", "nieuwste": datum(max(ts)) if ts else ""}


def test_from(mkt):
    out = {}
    mid = mkt[0][0]
    st, d = get("trades", {"market_id": mid, "sort_by": "trade_id", "limit": 100})
    nu = (d or {}).get("trades") or []
    tid = int(nu[0]["trade_id"]) if nu else None
    blk = int(nu[0]["block_height"]) if nu else None
    proeven = {
        "timestamp_from_ms": {"sort_by": "timestamp", "from": KNIP_MS},
        "timestamp_from_s": {"sort_by": "timestamp", "from": KNIP_MS // 1000},
    }
    if tid:
        for stap in [10**5, 10**6, 10**7, 10**8]:
            proeven[f"trade_id_from_min_{stap}"] = {"sort_by": "trade_id", "from": tid - stap}
    if blk:
        for stap in [10**5, 10**6, 10**7]:
            proeven[f"block_from_min_{stap}"] = {"sort_by": "block_height", "from": blk - stap}
    for naam, p in proeven.items():
        st, d = get("trades", dict(p, market_id=mid, limit=100))
        tr = (d or {}).get("trades") or []
        out[naam] = {"status": st, **bereik(tr), "fout": (d or {}).get("message") if st != 200 else None}
    return out


def bestaat(idx):
    st, d = get("account", {"by": "index", "value": str(idx)}, pauze=0.2)
    acc = ((d or {}).get("accounts") or [None])[0] if st == 200 else None
    return st, acc


def test_indexen():
    out = {"probes": {}}
    punten = sorted({1, 2, 3, 5, 10, 50, 100, 500, 1000, 5000, 10**4, 5 * 10**4, 10**5, 2 * 10**5, 5 * 10**5,
                     10**6, 2 * 10**6, 5 * 10**6, 10**7, 10**8, 10**9, 10**12, 2**47, 2**48 - 2})
    laatste_ja, eerste_nee = 0, None
    for n in punten:
        st, acc = bestaat(n)
        out["probes"][str(n)] = {"status": st, "bestaat": bool(acc),
                                 "type": acc.get("account_type") if acc else None}
        if acc and n < 10**12:
            laatste_ja = max(laatste_ja, n)
        elif not acc and eerste_nee is None and n > laatste_ja and n < 10**12:
            eerste_nee = n
    # binair zoeken naar hoogste bestaande index (aanname: opeenvolgend)
    lo, hi = laatste_ja, eerste_nee or laatste_ja * 2
    stappen = 0
    while hi - lo > 1 and stappen < 40:
        mid = (lo + hi) // 2
        st, acc = bestaat(mid)
        stappen += 1
        if acc:
            lo = mid
        else:
            # gat? kijk ook iets verder
            st2, acc2 = bestaat(mid + 7)
            if acc2:
                lo = mid + 7
            else:
                hi = mid
    out["hoogste_index_geschat"] = lo
    # dichtheid + activiteit: steekproef
    rng = random.Random(3)
    steek = [rng.randint(1, max(2, lo)) for _ in range(300)]
    c = Counter()
    fills_dist = []
    for n in steek:
        st, acc = bestaat(n)
        if not acc:
            c["bestaat_niet"] += 1
            continue
        c["bestaat"] += 1
        c[f"type_{acc.get('account_type')}"] += 1
        st, d = get("trades", {"account_index": n, "sort_by": "timestamp", "limit": 100}, pauze=0.2)
        tr = (d or {}).get("trades") or []
        if not tr:
            c["nooit_gehandeld_of_geen_fills"] += 1
            continue
        laatste = max(ms(t["timestamp"]) for t in tr)
        if laatste > NOW_MS - 90 * DAG:
            c["fill_laatste_90d"] += 1
            n90 = sum(1 for t in tr if ms(t["timestamp"]) > NOW_MS - 90 * DAG)
            fills_dist.append(n90)
            if n90 >= 100:
                c["≥100_fills_90d(eerste pagina vol)"] += 1
        if laatste > KNIP_MS - 30 * DAG and laatste < NOW_MS:
            c["fill_na_25-6"] += 1
    out["steekproef_300"] = dict(c)
    out["fills_90d_verdeling(max 100)"] = sorted(fills_dist)
    return out


def main():
    uit = Path(sys.argv[1])
    uit.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    st, mkt = markten()
    rap = {"markten": len(mkt)}
    rap["A_from"] = test_from(mkt)
    print("A klaar", flush=True)
    rap["B_indexen"] = test_indexen()
    print("B klaar", flush=True)
    rap["calls"] = {str(k): v for k, v in CALLS.items()}
    rap["calls_per_s"] = round(sum(CALLS.values()) / (time.time() - t0), 2)
    (uit / "rapport3.json").write_text(json.dumps(rap, indent=1, ensure_ascii=False))
    print("klaar", dict(CALLS), flush=True)


if __name__ == "__main__":
    main()
