"""Lighter stap 0 + 1: databron checken.

Stap 0: bekende Hyperliquid-adressen opzoeken op Lighter (accountsByL1Address),
        per treffer actief? en wint hij volgens pnl?
Stap 1: welke endpoints werken zonder auth voor een vreemde account,
        hoe ver terug gaan fills, hoe vind je een brede pool (trade-feed).

Privacy (repo is publiek): niets met volledige adressen of account-indexen
naar logs of bestanden. Adressen afgekort (0x1234…abcd), accounts als acc#n.

Gebruik: python -m lighter.probe <res_dir> <uit_dir>
"""
import asyncio
import csv
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import requests

BASE = "https://mainnet.zklighter.elliot.ai/api/v1/"
WS = "wss://mainnet.zklighter.elliot.ai/stream"
S = requests.Session()
S.headers["User-Agent"] = "kopieerbot-probe"
CALLS = Counter()
GENESIS_MS = 1737072000000  # 17-01-2025
NOW_MS = int(time.time() * 1000)
DAG = 86400000
FEED_MIN = float(os.environ.get("FEED_MIN", "15"))
BREED_N = int(os.environ.get("BREED_N", "1500"))
BREED_BUDGET_S = float(os.environ.get("BREED_BUDGET_S", "1200"))


def kort(a):
    return f"{a[:6]}…{a[-4:]}"


def get(path, params=None, pauze=0.25):
    """GET met backoff; geeft (status, json|None). Logt nooit params."""
    for poging in range(6):
        try:
            r = S.get(BASE + path, params=params, timeout=30)
        except requests.RequestException:
            CALLS["netwerkfout"] += 1
            time.sleep(2 * (poging + 1))
            continue
        CALLS[r.status_code] += 1
        if r.status_code == 429:
            time.sleep(min(60, 5 * 2 ** poging))
            continue
        time.sleep(pauze)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, None
    return 429, None


def ms(t):
    t = int(t)
    return t * 1000 if t < 10**12 else t


def datum(t):
    return time.strftime("%Y-%m-%d", time.gmtime(ms(t) / 1000)) if t else ""


# ---------- adressen uit de resultatenbranch ----------

def lees_adressen(res):
    res = Path(res)
    vast5, pool, breed = [], set(), []

    def uit_json(p):
        try:
            d = json.loads((res / p).read_text())
        except Exception:
            return []
        w = d.get("wallets", [])
        if isinstance(w, dict):
            return list(w.keys())
        return [x["address"] for x in w if "address" in x]

    def uit_csv(p, filt=None):
        try:
            with open(res / p) as f:
                return [r["address"] for r in csv.DictReader(f) if r.get("address") and (filt is None or filt(r))]
        except Exception:
            return []

    vast5 = uit_json("vast/selection.json")
    for p in ["live/selection.json", "paper_h4/selection.json", "paper_h4b/selection.json", "paper/state.json"]:
        pool.update(uit_json(p))
    for p in ["vast/overzicht.csv", "brede/top30.csv", "backtest/train_geschikt.csv", "live/overzicht.csv"]:
        pool.update(uit_csv(p))
    pool -= set(vast5)

    def ok(r):
        try:
            return float(r.get("trades") or 0) >= 100 and float(r.get("gem_r_pct") or 0) > 0
        except ValueError:
            return False

    b = [a for a in uit_csv("brede/alle_stats.csv", ok) if a not in pool and a not in vast5]
    random.Random(1).shuffle(b)
    breed = b[:BREED_N]
    norm = lambda xs: [x.lower() for x in xs if isinstance(x, str) and x.startswith("0x")]
    return norm(vast5), sorted(set(norm(pool))), norm(breed)


# ---------- stap 0 ----------

def subaccounts(adres):
    st, d = get("accountsByL1Address", {"l1_address": adres})
    if st != 200 or not d:
        return st, []
    subs = d.get("sub_accounts") or []
    return st, [s.get("index") for s in subs if s.get("index") is not None]


def account(idx):
    st, d = get("account", {"by": "index", "value": str(idx)})
    if st != 200 or not d:
        return st, None
    acc = (d.get("accounts") or [None])[0]
    return st, acc


def pnl_reeks(idx, resolutie="1d", auth_hdr=None):
    params = {"by": "index", "value": str(idx), "resolution": resolutie,
              "start_timestamp": GENESIS_MS, "end_timestamp": NOW_MS,
              "count_back": 1000, "ignore_transfers": "true"}
    st, d = get("pnl", params)
    if st != 200 or not d:
        return st, [], []
    rows = d.get("pnl") or d.get("data") or []
    keys = sorted(rows[0].keys()) if rows else sorted(d.keys())
    return st, rows, keys


def pnl_veld(rows):
    for k in ["trade_pnl", "pnl", "value", "total_pnl"]:
        if rows and k in rows[0]:
            return k
    return None


def pnl_samenvatting(rows):
    """Verandering in (cumulatieve) trade-PnL over 30/90 d, en eerste datum."""
    k = pnl_veld(rows)
    if not k:
        return {}
    pts = sorted((ms(r.get("timestamp", 0)), float(r.get(k) or 0)) for r in rows)
    if not pts:
        return {}
    eind = pts[-1][1]

    def op(dagen):
        grens = NOW_MS - dagen * DAG
        voor = [v for t, v in pts if t <= grens]
        return round(eind - voor[-1], 0) if voor else None

    # ook ongecumuleerd? als waarden per periode zijn, is som zinvoller: rapporteer beide
    som = lambda dagen: round(sum(v for t, v in pts if t > NOW_MS - dagen * DAG), 0)
    maanden = defaultdict(float)
    prev = None
    for t, v in pts:
        m = datum(t)[:7]
        if prev is not None:
            maanden[m] += v - prev
        prev = v
    return {"pnl_veld": k, "eerste_dag": datum(pts[0][0]), "punten": len(pts),
            "pnl_eind": round(eind, 0), "delta_30d": op(30), "delta_90d": op(90),
            "som_30d": som(30), "verliesmaanden_delta": sum(1 for v in maanden.values() if v < 0),
            "maanden": len(maanden)}


def stap0(vast5, pool, breed, uit):
    rijen, treffers = [], []
    t0 = time.time()
    for label, lijst in [("vast5", vast5), ("pool", pool), ("breed", breed)]:
        t_lijst = time.time()
        for i, a in enumerate(lijst):
            if label == "breed" and time.time() - t_lijst > BREED_BUDGET_S:
                break
            st, idxs = subaccounts(a)
            rij = {"adres": kort(a), "bron": label, "status": st, "accounts": len(idxs)}
            if idxs:
                treffers.append((label, a, idxs))
                if label != "breed":
                    rij.update(details(idxs))
            rijen.append(rij)
        print(f"stap0 {label}: {len(lijst)} adressen, treffers tot nu {len(treffers)}", flush=True)
    # details voor treffers uit breed
    for label, a, idxs in treffers:
        if label == "breed":
            for r in rijen:
                if r["adres"] == kort(a) and r["bron"] == "breed":
                    r.update(details(idxs))
    velden = sorted({k for r in rijen for k in r}, key=lambda k: (k not in ("adres", "bron", "status", "accounts"), k))
    with open(uit / "stap0_alle.csv", "w", newline="") as f:
        w = csv.DictWriter(f, velden)
        w.writeheader()
        w.writerows(rijen)
    tr = [r for r in rijen if r.get("accounts")]
    with open(uit / "stap0_treffers.csv", "w", newline="") as f:
        w = csv.DictWriter(f, velden)
        w.writeheader()
        w.writerows(tr)
    return treffers, {"vast5": len(vast5), "pool": len(pool), "breed_getest": sum(1 for r in rijen if r["bron"] == "breed"),
                      "treffers": Counter(r["bron"] for r in tr), "status": Counter(r["status"] for r in rijen)}


def details(idxs):
    """Som over subaccounts: collateral, open posities, upnl, laatste activiteit, pnl."""
    d = {"collateral": 0.0, "open_pos": 0, "upnl": 0.0, "trades_totaal": 0, "pnl_status": None}
    pnl_tot = Counter()
    eerste = []
    for idx in idxs:
        st, acc = account(idx)
        if acc:
            d["collateral"] += float(acc.get("collateral") or 0)
            d["trades_totaal"] += int(acc.get("total_order_count") or acc.get("total_trades_count") or 0)
            for p in acc.get("positions") or []:
                if float(p.get("position") or 0) != 0:
                    d["open_pos"] += 1
                    d["upnl"] += float(p.get("unrealized_pnl") or 0)
        st, rows, keys = pnl_reeks(idx)
        d["pnl_status"] = st
        s = pnl_samenvatting(rows)
        for k in ["delta_30d", "delta_90d", "som_30d", "pnl_eind"]:
            if s.get(k) is not None:
                pnl_tot[k] += s[k]
        if s.get("eerste_dag"):
            eerste.append(s["eerste_dag"])
        d["pnl_veld"] = s.get("pnl_veld")
        d["verliesmaanden"] = s.get("verliesmaanden_delta")
        d["maanden"] = s.get("maanden")
    d.update({k: round(v, 0) for k, v in pnl_tot.items()})
    d["eerste_pnl_dag"] = min(eerste) if eerste else ""
    d["collateral"] = round(d["collateral"], 0)
    d["upnl"] = round(d["upnl"], 0)
    return d


# ---------- stap 1: endpoints ----------

def markten():
    st, d = get("orderBooks")
    out = []
    for m in (d or {}).get("order_books", []):
        if m.get("market_type", "perp") == "perp" and m.get("status", "active") == "active":
            out.append((int(m["market_id"]), m.get("symbol")))
    return st, out


def endpoint_tests(test_idxs, mkt):
    """test_idxs: lijst van echte account-indexen (vreemd). Rapport zonder indexen."""
    r = {}
    for n, idx in enumerate(test_idxs, 1):
        tag = f"acc#{n}"
        e = {}
        st, acc = account(idx)
        e["account"] = {"status": st, "velden": sorted(acc.keys()) if acc else None,
                        "positie_velden": sorted((acc.get("positions") or [{}])[0].keys()) if acc and acc.get("positions") else None}
        st, rows, keys = pnl_reeks(idx)
        e["pnl_1d"] = {"status": st, "punten": len(rows), "velden": keys,
                       "eerste": datum(min((ms(x.get("timestamp", 0)) for x in rows), default=0)),
                       "voorbeeld": {k: v for k, v in (rows[-1] if rows else {}).items() if k != "timestamp"}}
        st, rows1h, _ = pnl_reeks(idx, "1h")
        e["pnl_1h"] = {"status": st, "punten": len(rows1h)}
        st, d = get("trades", {"account_index": idx, "sort_by": "timestamp", "limit": 100})
        e["trades_account"] = {"status": st, "n": len((d or {}).get("trades") or []), "fout": (d or {}).get("message") if st != 200 else None}
        if st == 200 and d and d.get("trades"):
            e["trades_account"]["velden"] = sorted(d["trades"][0].keys())
            e["trades_account"]["pagineren"] = pagineer({"account_index": idx}, max_pag=30)
        st, d = get("export", {"type": "trade", "account_index": idx})
        e["export"] = {"status": st, "fout": (d or {}).get("message") if st != 200 else None, "velden": sorted(d.keys()) if isinstance(d, dict) else None}
        st, d = get("accountInactiveOrders", {"account_index": idx, "limit": 10})
        e["accountInactiveOrders"] = {"status": st, "fout": (d or {}).get("message") if st != 200 else None}
        r[tag] = e
    # globale trades per markt (zonder account_index): bevat account-ids? hoe ver terug?
    if mkt:
        mid = mkt[0][0]
        st, d = get("trades", {"market_id": mid, "sort_by": "timestamp", "limit": 100})
        g = {"status": st, "n": len((d or {}).get("trades") or [])}
        if st == 200 and d and d.get("trades"):
            t0 = d["trades"][0]
            g["velden"] = sorted(t0.keys())
            g["heeft_account_ids"] = "ask_account_id" in t0 and "bid_account_id" in t0
            g["pagineren"] = pagineer({"market_id": mid}, max_pag=60)
        else:
            g["fout"] = (d or {}).get("message")
        r["trades_markt"] = g
        st, d = get("recentTrades", {"market_id": mid, "limit": 100})
        rt = {"status": st, "n": len((d or {}).get("trades") or [])}
        if st == 200 and d and d.get("trades"):
            rt["heeft_account_ids"] = "ask_account_id" in d["trades"][0]
        r["recentTrades"] = rt
    # ranglijst-achtige endpoints (gok; alleen status)
    for p in ["leaderboard", "leaderboards", "topAccounts", "accounts", "publicPools"]:
        st, d = get(p, {"limit": 10, "type": "all", "filter": "all", "index": 0})
        r[f"gok_{p}"] = {"status": st, "velden": sorted(d.keys())[:15] if isinstance(d, dict) else None}
    return r


def pagineer(params, max_pag):
    """Bladert terug met cursor; geeft oudste datum en aantal pagina's."""
    cursor, oudste, n, pag = None, None, 0, 0
    nieuwste = None
    for pag in range(max_pag):
        p = dict(params, sort_by="timestamp", limit=100)
        if cursor:
            p["cursor"] = cursor
        st, d = get("trades", p)
        if st != 200 or not d:
            return {"paginas": pag, "stop_status": st, "trades": n, "oudste": datum(oudste) if oudste else "", "nieuwste": datum(nieuwste) if nieuwste else ""}
        tr = d.get("trades") or []
        if not tr:
            break
        n += len(tr)
        ts = [ms(t.get("timestamp", 0)) for t in tr]
        oudste = min(ts + ([oudste] if oudste else []))
        nieuwste = max(ts + ([nieuwste] if nieuwste else []))
        cursor = d.get("next_cursor")
        if not cursor:
            break
    return {"paginas": pag + 1, "trades": n, "oudste": datum(oudste) if oudste else "", "nieuwste": datum(nieuwste) if nieuwste else "",
            "nog_meer": bool(cursor)}


# ---------- stap 1: websockets ----------

async def ws_account(idxs):
    import websockets
    out = {}
    for n, idx in enumerate(idxs, 1):
        e = {}
        for kanaal in [f"account_all/{idx}", f"account_all_trades/{idx}", f"user_stats/{idx}"]:
            naam = kanaal.split("/")[0]
            try:
                async with websockets.connect(WS, max_size=2**25, open_timeout=20) as w:
                    await w.send(json.dumps({"type": "subscribe", "channel": kanaal}))
                    berichten = []
                    eind = time.time() + 12
                    while time.time() < eind and len(berichten) < 3:
                        try:
                            m = json.loads(await asyncio.wait_for(w.recv(), timeout=eind - time.time()))
                        except asyncio.TimeoutError:
                            break
                        if m.get("type") in ("connected",):
                            continue
                        berichten.append(m)
                info = []
                for m in berichten:
                    i = {"type": m.get("type"), "velden": sorted(m.keys())}
                    if "trades" in m:
                        tr = m["trades"]
                        alle = [t for v in tr.values() for t in v] if isinstance(tr, dict) else (tr or [])
                        i["n_trades"] = len(alle)
                        if alle:
                            ts = [ms(t.get("timestamp", 0)) for t in alle]
                            i["trades_oudste"] = datum(min(ts))
                            i["trade_velden"] = sorted(alle[0].keys())
                    for k in ["total_trades_count", "monthly_trades_count", "weekly_trades_count", "daily_trades_count"]:
                        if k in m:
                            i[k] = m[k]
                    if "error" in m or m.get("type") == "error":
                        i["fout"] = str(m.get("error") or m)[:200]
                    info.append(i)
                e[naam] = info or "geen berichten"
            except Exception as ex:
                e[naam] = f"fout: {type(ex).__name__}: {str(ex)[:150]}"
        out[f"acc#{n}"] = e
    return out


async def ws_feed(mkt, minuten):
    """Luistert naar trade/{m} voor alle perp-markten: unieke accounts, makeraandeel."""
    import websockets
    per_acc = defaultdict(lambda: [0, 0, 0.0])  # trades, maker, usd
    stats = Counter()
    eind = time.time() + minuten * 60
    gezien = set()

    async def luister(groep):
        while time.time() < eind:
            try:
                async with websockets.connect(WS, max_size=2**25, open_timeout=20, ping_interval=20) as w:
                    for mid, _ in groep:
                        await w.send(json.dumps({"type": "subscribe", "channel": f"trade/{mid}"}))
                    while time.time() < eind:
                        try:
                            m = json.loads(await asyncio.wait_for(w.recv(), timeout=max(1, eind - time.time())))
                        except asyncio.TimeoutError:
                            break
                        stats[m.get("type", "?")] += 1
                        if not str(m.get("type", "")).startswith("update/trade"):
                            continue
                        for t in m.get("trades") or []:
                            tid = t.get("trade_id")
                            if tid in gezien:
                                continue
                            gezien.add(tid)
                            stats["trades"] += 1
                            ask, bid = t.get("ask_account_id"), t.get("bid_account_id")
                            maker_ask = bool(t.get("is_maker_ask"))
                            usd = float(t.get("usd_amount") or 0)
                            for acc, is_maker in [(ask, maker_ask), (bid, not maker_ask)]:
                                if acc is None:
                                    continue
                                a = per_acc[acc]
                                a[0] += 1
                                a[1] += int(is_maker)
                                a[2] += usd
            except Exception as ex:
                stats[f"fout_{type(ex).__name__}"] += 1
                await asyncio.sleep(3)

    groepen = [mkt[i:i + 50] for i in range(0, len(mkt), 50)]
    await asyncio.gather(*(luister(g) for g in groepen))
    n = len(per_acc)
    trades = sorted((v[0] for v in per_acc.values()), reverse=True)
    mm = [v for v in per_acc.values() if v[0] >= 50 and v[1] / v[0] >= 0.8]
    taker = [v for v in per_acc.values() if v[1] / max(1, v[0]) < 0.5]
    tot = sum(trades)
    return {"minuten": minuten, "markten": len(mkt), "unieke_accounts": n, "trades": stats["trades"],
            "account_trades_p50": trades[n // 2] if n else 0, "account_trades_max": trades[0] if n else 0,
            "aandeel_top10_accounts": round(sum(trades[:10]) / tot, 3) if tot else None,
            "mm_kandidaten(>=50 trades, >=80% maker)": len(mm),
            "accounts_vooral_taker(<50% maker)": len(taker),
            "berichttypes": dict(stats.most_common(12))}, list(per_acc.keys())


# ---------- main ----------

def main():
    res, uit = sys.argv[1], Path(sys.argv[2])
    uit.mkdir(parents=True, exist_ok=True)
    rapport = {"gestart": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
    st, mkt = markten()
    rapport["markten"] = {"status": st, "perp_actief": len(mkt), "symbolen": [s for _, s in mkt][:300]}
    print(f"markten: status {st}, {len(mkt)} perps", flush=True)

    vast5, pool, breed = lees_adressen(res)
    print(f"adressen: vast5 {len(vast5)}, pool {len(pool)}, breed {len(breed)}", flush=True)
    treffers, s0 = stap0(vast5, pool, breed, uit)
    rapport["stap0"] = {k: dict(v) if isinstance(v, Counter) else v for k, v in s0.items()}
    (uit / "rapport.json").write_text(json.dumps(rapport, indent=1, ensure_ascii=False))

    # testaccounts: eerst treffers, anders accounts uit de trade-feed (kort luisteren)
    test_idxs = [i for _, _, idxs in treffers for i in idxs][:2]
    feed, feed_accs = asyncio.run(ws_feed(mkt, FEED_MIN))
    rapport["feed"] = feed
    print(f"feed: {feed['unieke_accounts']} accounts in {FEED_MIN} min", flush=True)
    if len(test_idxs) < 3 and feed_accs:
        random.Random(2).shuffle(feed_accs)
        test_idxs += [a for a in feed_accs if a not in test_idxs][: 3 - len(test_idxs)]
    rapport["stap1_endpoints"] = endpoint_tests(test_idxs, mkt)
    rapport["stap1_ws"] = asyncio.run(ws_account(test_idxs))
    rapport["calls"] = {str(k): v for k, v in CALLS.items()}
    rapport["klaar"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    (uit / "rapport.json").write_text(json.dumps(rapport, indent=1, ensure_ascii=False))
    print("klaar; calls:", dict(CALLS), flush=True)


if __name__ == "__main__":
    main()
