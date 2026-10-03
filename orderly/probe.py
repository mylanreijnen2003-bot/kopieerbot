"""Orderly stap 0 + 1: databron checken en bekende Hyperliquid-wallets opzoeken.
Gebruik: python -m orderly.probe <uitmap>
Schrijft: stap0_treffers.csv, stap1_check.json, pool.csv, pool_sample.csv, voorbeelden/*.json, log.txt
"""
from __future__ import annotations

import collections
import json
import os
import random
import sys
import time

import numpy as np
import pandas as pd
import requests

from bt.vast_select import stats, trades_pct

URL = "https://api.orderly.org/v1/public/query"
GEWICHT = {"rateLimitStatus": 0, "marketSummary": 1, "marketTrades": 1, "topAddresses": 10, "platformPositions": 20}
BUDGET = 1100                       # per minuut, onder de 1.200
DAG = 86_400_000
NU = int(time.time() * 1000)
OUT = sys.argv[1] if len(sys.argv) > 1 else "res/orderly"
os.makedirs(f"{OUT}/voorbeelden", exist_ok=True)
_venster: collections.deque = collections.deque()
_voorbeeld: set = set()
KRAKEN = set(json.load(open(os.path.join(os.path.dirname(__file__), "kraken.json"))))


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    open(f"{OUT}/log.txt", "a").write(s + "\n")


def q(typ, **kw):
    w = GEWICHT.get(typ, 5)
    while True:
        nu = time.time()
        while _venster and nu - _venster[0][0] > 60:
            _venster.popleft()
        if sum(x[1] for x in _venster) + w <= BUDGET:
            break
        time.sleep(0.5)
    _venster.append((time.time(), w))
    for poging in range(5):
        try:
            r = requests.post(URL, json={"type": typ, **kw}, timeout=30)
            if r.status_code == 429:
                time.sleep(10 * (poging + 1)); continue
            j = r.json()
            if typ not in _voorbeeld:
                _voorbeeld.add(typ)
                json.dump({"request": {"type": typ, **kw}, "status": r.status_code, "response": j},
                          open(f"{OUT}/voorbeelden/{typ}.json", "w"), indent=1, default=str)
            return j
        except Exception as e:  # noqa: BLE001
            log("fout", typ, kw.get("address", kw.get("symbol", "")), repr(e)[:200])
            time.sleep(3)
    return {}


def rows_cursor(j):
    d = j.get("data") if isinstance(j, dict) else None
    if isinstance(d, list):
        return d, None
    if not isinstance(d, dict):
        return [], None
    cur = d.get("next_cursor") or (d.get("meta") or {}).get("next_cursor")
    for k in ("rows", "accounts", "positions", "items"):
        if isinstance(d.get(k), list):
            return d[k], cur
    for v in d.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v, cur
    return [], cur


def alle(typ, max_pag=50, **kw):
    out, cur = [], None
    for _ in range(max_pag):
        j = q(typ, **kw, **({"cursor": cur} if cur else {}))
        r, cur = rows_cursor(j)
        out += r
        if not cur or not r:
            break
    return out, bool(cur)


def munt(sym):
    b = sym.split("_")[1] if sym.count("_") >= 2 else sym
    for p in ("1000000", "1000", "K"):
        if b.startswith(p) and len(b) > len(p) + 1 and b not in KRAKEN:
            b2 = b[len(p):]
            if b2 in KRAKEN:
                return b2
    return b


def naar_fills(tr):
    """Orderly-trades -> fills met start/after per (account, symbol), positie start op 0 bij eerste fill."""
    tr = sorted(tr, key=lambda t: (t.get("executed_timestamp") or 0, str(t.get("id"))))
    pos, fl = collections.defaultdict(float), []
    for t in tr:
        acc = t.get("account_id") or ""
        sym = t["symbol"]
        k = (acc, sym)
        qty = float(t["executed_quantity"]) * (1 if str(t["side"]).upper() == "BUY" else -1)
        s = pos[k]
        a = s + qty
        if abs(a) < 1e-9 * max(1.0, abs(s)):
            a = 0.0
        pos[k] = a
        fl.append({"time": int(t["executed_timestamp"]), "coin": f"{acc[-6:]}|{munt(sym)}", "start": s, "after": a,
                   "px": float(t["executed_price"]), "maker": t.get("is_maker")})
    return fl, pos


def profiel(adres, max_pag=40):
    """Alles wat we per wallet willen weten (stap 0 en straks stap 3)."""
    acc = rows_cursor(q("accounts", address=adres))[0]
    if not acc:
        return None
    tr, afgekapt = alle("trades", max_pag=max_pag, address=adres, limit=1000)
    st = q("accountState", address=adres).get("data") or {}
    pf, _ = alle("portfolio", max_pag=3, address=adres, start_time=NU - 365 * DAG, limit=365)
    fl, pos = naar_fills(tr)
    t = trades_pct(fl)
    s = stats(t)
    # klopt de reconstructie? eind-positie per account/symbol vs. accountState
    staat = {}
    for a in (st.get("accounts") or [st]):
        for p in a.get("positions") or []:
            qq = float(p.get("position_qty") or 0)
            if qq:
                staat[(a.get("account_id") or "", p["symbol"])] = qq
    rec = {k: v for k, v in pos.items() if v}
    klopt = all(abs(rec.get(k, 0) - staat.get(k, 0)) <= 1e-6 * max(1, abs(staat.get(k, 0))) for k in set(rec) | set(staat))
    r30 = [x for x in t if x[2] >= NU - 30 * DAG]
    uren = [(x[2] - x[1]) / 3.6e6 for x in r30]
    pf_s = sorted(pf, key=lambda r: r.get("timestamp") or 0)
    av = [float(r.get("account_value") or 0) for r in pf_s]
    return {
        "address": adres, "accounts": len(acc), "brokers": ",".join(sorted({str(a.get("broker_id")) for a in acc})),
        "fills": len(tr), "fills_afgekapt": afgekapt, "maker_pct": round(100 * np.mean([bool(f["maker"]) for f in fl]), 1) if fl else None,
        "eerste_fill": pd.Timestamp(min(f["time"] for f in fl), unit="ms").date() if fl else None,
        "laatste_fill": pd.Timestamp(max(f["time"] for f in fl), unit="ms").date() if fl else None,
        "trades_7d": sum(x[2] >= NU - 7 * DAG for x in t), "trades_14d": sum(x[2] >= NU - 14 * DAG for x in t),
        "trades_per_dag_30d": round(len(r30) / 30, 2), "houdtijd_mediaan_uur_30d": round(float(np.median(uren)), 1) if uren else None,
        "kraken_pct": round(100 * np.mean([x[0].split("|")[1] in KRAKEN for x in t]), 1) if t else None,
        **{k: v for k, v in s.items()},
        "reconstructie_klopt": klopt, "open_posities": len(staat),
        "account_value": st.get("account_value") if "account_value" in st else sum(float(a.get("account_value") or 0) for a in st.get("accounts") or []),
        "upnl": st.get("total_unrealized_pnl") if "total_unrealized_pnl" in st else sum(float(a.get("total_unrealized_pnl") or 0) for a in st.get("accounts") or []),
        "portfolio_dagen": len(av), "portfolio_eerste": pd.Timestamp(pf_s[0]["timestamp"], unit="ms").date() if pf_s else None,
        "av_begin": av[0] if av else None, "av_eind": av[-1] if av else None,
        "munten": ", ".join(f"{c.split('|')[1]} {100 * v:.0f}%" for c, v in pd.Series([x[0] for x in t]).value_counts(normalize=True).head(4).items()) if t else "",
    }


def stap1(adressen):
    """Diepe check op een paar echte adressen: hoe ver gaat de trade-historie terug, en zijn tijdvensters op te vragen?"""
    res = []
    for a in adressen:
        r = {"address": a}
        tr, afgekapt = alle("trades", max_pag=200, address=a, limit=1000)
        ts = [int(t["executed_timestamp"]) for t in tr if t.get("executed_timestamp")]
        r.update(fills=len(tr), afgekapt=afgekapt, eerste=str(pd.Timestamp(min(ts), unit="ms")) if ts else None,
                 laatste=str(pd.Timestamp(max(ts), unit="ms")) if ts else None,
                 velden=sorted(tr[0].keys()) if tr else [], realized_pnl_gevuld=sum(t.get("realized_pnl") is not None for t in tr))
        venster = {}
        for jaren in (3, 2, 1, 0.5):
            st = NU - int(jaren * 365 * DAG)
            j = q("trades", address=a, start_time=st, end_time=st + 30 * DAG, limit=2000)
            rr, _ = rows_cursor(j)
            venster[f"{jaren}j_geleden_30d"] = {"rijen": len(rr), "fout": None if j.get("success", True) else str(j)[:300]}
        r["vensters"] = venster
        pf, _ = alle("portfolio", max_pag=3, address=a, start_time=NU - 365 * DAG, limit=365)
        r["portfolio_dagen"] = len(pf)
        r["portfolio_eerste"] = str(pd.Timestamp(min(x["timestamp"] for x in pf), unit="ms").date()) if pf else None
        dw, _ = alle("userDepositsWithdrawals", max_pag=5, address=a)
        r["stortingen_rijen"] = len(dw)
        fp, _ = alle("fundingPayments", max_pag=2, address=a)
        r["funding_rijen"] = len(fp)
        r["profiel"] = {k: str(v) for k, v in (profiel(a, max_pag=200) or {}).items()}
        res.append(r)
        log("stap1", a[:10], r["fills"], r["eerste"], r["portfolio_dagen"], r["profiel"].get("reconstructie_klopt"))
    return res


def main():
    log("start", pd.Timestamp.now(tz="UTC"), q("rateLimitStatus"))
    ms, _ = rows_cursor(q("marketSummary"))
    symbolen = sorted({m.get("symbol") for m in ms if m.get("symbol")})
    log("symbolen", len(symbolen))

    # --- pool opbouwen (stap 1c) ---
    pool = collections.defaultdict(set)
    sorts = ["notional", "volume_24h", "volume_7d", "volume_30d", "pnl_24h", "pnl_7d", "pnl_30d", "trade_count_24h"]
    top_rows = []
    for sym in [None] + symbolen:
        for sb in sorts:
            kw = {"sort_by": sb, "limit": 200, **({"symbol": sym} if sym else {})}
            rr, _ = alle("topAddresses", max_pag=2, **kw)
            for x in rr:
                if x.get("address"):
                    pool[x["address"].lower()].add("top")
                    top_rows.append({**x, "q_symbol": sym, "q_sort": sb})
    log("pool na topAddresses", len(pool))
    pp, meer = alle("platformPositions", max_pag=40, limit=5000)
    for x in pp:
        if x.get("address"):
            pool[x["address"].lower()].add("posities")
    log("platformPositions rijen", len(pp), "meer pagina's", meer, "pool", len(pool))
    for sym in symbolen:
        rr, _ = rows_cursor(q("marketTrades", symbol=sym, limit=1000))
        for x in rr:
            if x.get("address"):
                pool[x["address"].lower()].add("markttrades")
    log("pool na marketTrades", len(pool))
    pd.DataFrame([{"address": a, "bron": ",".join(sorted(b))} for a, b in pool.items()]).to_csv(f"{OUT}/pool.csv", index=False)
    if top_rows:
        pd.DataFrame(top_rows).drop_duplicates("address").to_csv(f"{OUT}/top_rows.csv", index=False)

    # --- stap 1: diepe check op 3 echte adressen (drukste uit topAddresses op trade_count, + 2 willekeurig) ---
    tops = [r for r in top_rows if r.get("q_symbol") is None and r.get("q_sort") == "volume_30d"]
    kies = [tops[0]["address"]] if tops else []
    rest = sorted(set(pool) - set(kies))
    random.seed(1)
    kies += random.sample(rest, min(2, len(rest)))
    json.dump(stap1(kies), open(f"{OUT}/stap1_check.json", "w"), indent=1, default=str)

    # --- steekproef uit de pool: hoe oud / actief / groot (100 willekeurige) ---
    sample = []
    for a in random.sample(sorted(pool), min(100, len(pool))):
        try:
            p = profiel(a, max_pag=10)
            if p:
                sample.append({**p, "bron": ",".join(sorted(pool[a]))})
        except Exception as e:  # noqa: BLE001
            log("fout sample", a[:10], repr(e)[:200])
    pd.DataFrame(sample).to_csv(f"{OUT}/pool_sample.csv", index=False)
    log("sample klaar", len(sample))

    # --- stap 0: bekende Hyperliquid-wallets ---
    hl = pd.read_csv(os.path.join(os.path.dirname(__file__), "hl_adressen.csv"))
    hl = pd.concat([hl[hl.label != "overig"], hl[hl.label == "overig"]])  # eigen traders eerst
    hits = []
    for i, (a, lab) in enumerate(zip(hl.address, hl.label)):
        try:
            p = profiel(a)
        except Exception as e:  # noqa: BLE001
            log("fout stap0", a[:10], repr(e)[:200]); continue
        if p:
            hits.append({**p, "label": lab, "in_pool": a in pool})
            log("treffer", lab, a[:10], p["fills"], p.get("trades"), p.get("winst_pct_trades"))
            pd.DataFrame(hits).to_csv(f"{OUT}/stap0_treffers.csv", index=False)
        if i % 200 == 0:
            log("stap0", i, "/", len(hl), "treffers", len(hits))
    pd.DataFrame(hits).to_csv(f"{OUT}/stap0_treffers.csv", index=False)
    log("klaar", pd.Timestamp.now(tz="UTC"), "treffers", len(hits), "van", len(hl), "pool", len(pool))


if __name__ == "__main__":
    main()
