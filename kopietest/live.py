"""Kopietest, live deel: meet wat een bot écht zou krijgen.
Hyperliquid (websocket): per fill van een trader -> hoe snel zie je hem (userFills vs publieke trades-feed),
  en welke prijs krijg jij als taker na 0/1/2/5/10/30/60 s (bid/ask uit de bbo-feed).
  Per rustende order van een trader (orderUpdates) -> zou jouw spiegelorder op dezelfde prijs vullen?
Orderly (REST-polling): idem met polling van trades / openOrders / orderbook / marketTrades.
Gebruik: python -m kopietest.live <uitmap>   (env DUUR_MIN, standaard 300)
"""
from __future__ import annotations

import asyncio
import collections
import json
import os
import sys
import time

OUT = sys.argv[1]
os.makedirs(f"{OUT}/voorbeelden", exist_ok=True)
sys.argv = ["x", "live", OUT]
import pandas as pd  # noqa: E402
import requests  # noqa: E402
import websockets  # noqa: E402

from orderly import probe as P  # noqa: E402

P.OUT = OUT
P.GEWICHT.update({"orderbook": 1, "marketTrades": 1})
HL_WS = "wss://api.hyperliquid.xyz/ws"
HL_INFO = "https://api.hyperliquid.xyz/info"
DUUR = float(os.environ.get("DUUR_MIN", "300")) * 60
VERTRAGING = [0, 1, 2, 5, 10, 30, 60]
START = time.time()
log = P.log


def nu():
    return time.time()


class Boek:
    """bid/ask-historie per munt (laatste 200 s)."""

    def __init__(self):
        self.h = collections.defaultdict(collections.deque)

    def zet(self, coin, t, bid, ask):
        d = self.h[coin]
        d.append((t, bid, ask))
        while d and d[0][0] < t - 200:
            d.popleft()

    def op(self, coin, t):
        d = self.h.get(coin)
        if not d:
            return None
        best = None
        for x in d:
            if x[0] <= t:
                best = x
            else:
                break
        return best or d[0]


class Rec:
    def __init__(self):
        self.files = {}

    def w(self, naam, obj):
        f = self.files.get(naam)
        if f is None:
            f = self.files[naam] = open(f"{OUT}/{naam}.jsonl", "a")
        f.write(json.dumps(obj, default=str) + "\n")
        f.flush()

    def close(self):
        for f in self.files.values():
            f.close()


REC = Rec()
BOEK = Boek()
TRADES = collections.defaultdict(collections.deque)      # coin -> (t, px, sz)
PEND = []                                                # te evalueren fills
MIRROR = {}                                              # (venue, oid) -> dict


def evalueer(venue, fill, t_zien):
    """Plan evaluatie: taker-prijs na x s na het zien van de fill."""
    PEND.append((t_zien + max(VERTRAGING) + 1, venue, fill, t_zien))


def verwerk_pend():
    rest = []
    for due, venue, f, t_zien in PEND:
        if nu() < due:
            rest.append((due, venue, f, t_zien))
            continue
        r = {**f, "venue": venue, "t_zien": t_zien, "vertraging_zien_s": round(t_zien - f["t"] / 1000, 3)}
        b0 = BOEK.op(f["coin"], f["t"] / 1000)
        if b0:
            r["spread_bps_bij_fill"] = round((b0[2] - b0[1]) / ((b0[2] + b0[1]) / 2) * 1e4, 3)
        for d in VERTRAGING:
            b = BOEK.op(f["coin"], t_zien + d)
            if b:
                prijs = b[2] if f["side"] > 0 else b[1]                    # taker: koop op ask, verkoop op bid
                r[f"slip_{d}s_bps"] = round(f["side"] * (prijs / f["px"] - 1) * 1e4, 3)
                r[f"mid_{d}s_bps"] = round(f["side"] * (((b[1] + b[2]) / 2) / f["px"] - 1) * 1e4, 3)
        REC.w("fills_eval", r)
    PEND[:] = rest


def mirror_check(coin, t, px):
    """Een publieke trade op coin: vult hij een van onze hypothetische spiegelorders?"""
    for k, m in list(MIRROR.items()):
        if m["coin"] != coin or m.get("klaar") or t < m["t_plaats"]:
            continue
        door = (m["side"] > 0 and px < m["px"]) or (m["side"] < 0 and px > m["px"])
        raak = (m["side"] > 0 and px <= m["px"]) or (m["side"] < 0 and px >= m["px"])
        if raak and "t_raak" not in m:
            m["t_raak"] = t
        if door and "t_door" not in m:
            m["t_door"] = t


def mirror_sluit(force=False):
    for k, m in list(MIRROR.items()):
        if force or (m.get("t_eind_leider") and nu() > m["t_eind_leider"] + 300):
            b = BOEK.op(m["coin"], m["t_eind_leider"] + 300) if m.get("t_eind_leider") else None
            if b:
                m["koers_5m_na_eind_bps"] = round(m["side"] * (((b[1] + b[2]) / 2) / m["px"] - 1) * 1e4, 3)
            REC.w("spiegel", m)
            MIRROR.pop(k)


# ------------------------------------------------------------------ Hyperliquid
HL_COINS = set()
TELLER = collections.Counter()
HL_WS_CONN = {}


async def hl_markt(coins):
    """Eén verbinding voor bbo + trades van alle munten."""
    while nu() - START < DUUR:
        try:
            async with websockets.connect(HL_WS, ping_interval=20, max_size=2**24) as ws:
                HL_WS_CONN["markt"] = ws
                asyncio.create_task(ping(ws))
                for c in sorted(coins | HL_COINS):
                    await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "bbo", "coin": c}}))
                    await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": c}}))
                    HL_COINS.add(c)
                eerste = set()
                while nu() - START < DUUR:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 60))
                    ch = msg.get("channel")
                    if ch not in eerste and ch in ("bbo", "trades", "l2Book"):
                        eerste.add(ch)
                        json.dump(msg, open(f"{OUT}/voorbeelden/hl_{ch}.json", "w"), default=str)
                    TELLER[ch] += 1
                    if ch in ("bbo", "trades") and TELLER["l2_aan"] == 0 and TELLER["bbo"] == 0 and nu() - START > 60:
                        TELLER["l2_aan"] = 1
                        log("geen bbo-feed: over op l2Book")
                        for c in sorted(HL_COINS):
                            await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "l2Book", "coin": c}}))
                    if ch == "l2Book":
                        d = msg["data"]
                        lv = d.get("levels") or [[], []]
                        if lv[0] and lv[1]:
                            BOEK.zet(d["coin"], nu(), float(lv[0][0]["px"]), float(lv[1][0]["px"]))
                    if ch == "bbo":
                        d = msg["data"]
                        bb = d.get("bbo") or [None, None]
                        if bb[0] and bb[1]:
                            BOEK.zet(d["coin"], nu(), float(bb[0]["px"]), float(bb[1]["px"]))
                    elif ch == "trades":
                        for x in msg["data"]:
                            t = int(x["time"]) / 1000
                            mirror_check(x["coin"], t, float(x["px"]))
                            users = [u.lower() for u in (x.get("users") or [])]
                            for u in users:
                                if u in HL_LEIDERS:
                                    REC.w("hl_trades_feed_leider", {"user": u, "coin": x["coin"], "t": int(x["time"]), "t_zien": nu(),
                                                                    "px": float(x["px"]), "sz": float(x["sz"]), "tid": x.get("tid")})
        except Exception as e:  # noqa: BLE001
            log("hl markt fout", repr(e)[:200])
            await asyncio.sleep(3)


async def hl_sub_coin(c):
    if c in HL_COINS:
        return
    HL_COINS.add(c)
    ws = HL_WS_CONN.get("markt")
    if ws:
        try:
            await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "bbo", "coin": c}}))
            await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": c}}))
            if TELLER["l2_aan"]:
                await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "l2Book", "coin": c}}))
        except Exception:  # noqa: BLE001
            pass


HL_LEIDERS = set()


async def ping(ws):
    """Hyperliquid sluit een verbinding na 60 s zonder bericht van ons ('Inactive')."""
    try:
        while True:
            await asyncio.sleep(30)
            await ws.send(json.dumps({"method": "ping"}))
    except Exception:  # noqa: BLE001
        return


async def hl_leider(a):
    while nu() - START < DUUR:
        try:
            async with websockets.connect(HL_WS, ping_interval=20, max_size=2**24) as ws:
                asyncio.create_task(ping(ws))
                await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "userFills", "user": a}}))
                await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "orderUpdates", "user": a}}))
                while nu() - START < DUUR:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 90))
                    ch = msg.get("channel")
                    t_zien = nu()
                    TELLER["leider_" + str(ch)] += 1
                    if ch == "userFills":
                        d = msg["data"]
                        if d.get("isSnapshot"):
                            continue
                        for f in d.get("fills", []):
                            if ":" in f["coin"] or f["coin"].startswith("@"):
                                continue
                            await hl_sub_coin(f["coin"])
                            sgn = 1 if f["side"] == "B" else -1
                            fill = {"address": a, "coin": f["coin"], "t": int(f["time"]), "side": sgn, "px": float(f["px"]),
                                    "sz": float(f["sz"]), "maker": not f.get("crossed"), "oid": f.get("oid"),
                                    "start": float(f.get("startPosition") or 0), "dir": f.get("dir"), "liq": bool(f.get("liquidation"))}
                            REC.w("hl_fills", {**fill, "t_zien": t_zien})
                            evalueer("hl", fill, t_zien)
                            m = MIRROR.get(("hl", str(f.get("oid"))))
                            if m:
                                m.setdefault("t_leider_fill", int(f["time"]) / 1000)
                    elif ch == "orderUpdates":
                        for u in msg["data"]:
                            o = u.get("order", {})
                            if ":" in str(o.get("coin")) or str(o.get("coin")).startswith("@"):
                                continue
                            REC.w("hl_orders", {"address": a, "t_zien": t_zien, "status": u.get("status"), **o})
                            k = ("hl", str(o.get("oid")))
                            if u.get("status") == "open" and k not in MIRROR and o.get("limitPx"):
                                await hl_sub_coin(o["coin"])
                                MIRROR[k] = {"venue": "hl", "address": a, "oid": o.get("oid"), "coin": o["coin"],
                                             "side": 1 if o.get("side") == "B" else -1, "px": float(o["limitPx"]), "sz": float(o.get("sz") or 0),
                                             "t_order": int(o.get("timestamp") or 0) / 1000, "t_zien": t_zien, "t_plaats": t_zien + 0.5,
                                             "type": o.get("orderType"), "tif": o.get("tif"), "trigger": o.get("isTrigger"),
                                             "reduce": o.get("reduceOnly")}
                            elif k in MIRROR and u.get("status") != "open":
                                MIRROR[k]["status_leider"] = u.get("status")
                                MIRROR[k]["t_eind_leider"] = t_zien
        except Exception as e:  # noqa: BLE001
            log("hl leider fout", a[:10], repr(e)[:200])
            await asyncio.sleep(3)


# ------------------------------------------------------------------ Orderly
def ord_boek(sym):
    j = P.q("orderbook", symbol=sym, max_level=1)
    d = j.get("data") or {}
    if "orderbook" not in os.listdir(f"{OUT}/voorbeelden"):
        json.dump(j, open(f"{OUT}/voorbeelden/orderbook", "w"), default=str)

    def best(x):
        if not x:
            return None
        e = x[0]
        return float(e["price"] if isinstance(e, dict) else e[0])
    bid, ask = best(d.get("bids")), best(d.get("asks"))
    if bid and ask:
        BOEK.zet(sym, nu(), bid, ask)
    return bid, ask


def ord_loop(leiders):
    gezien, open_gezien = set(), {}
    laatste_mt = {}
    volgende = {a: START + i * 0.5 for i, a in enumerate(leiders)}
    volgende_oo = {a: START + 2 + i for i, a in enumerate(leiders)}
    boek_plan = []
    while nu() - START < DUUR:
        t = nu()
        for a in leiders:
            if t >= volgende[a]:
                volgende[a] = t + 3
                rr, _ = P.rows_cursor(P.q("trades", address=a, limit=200))
                t_zien = nu()
                for x in rr:
                    if x["id"] in gezien:
                        continue
                    gezien.add(x["id"])
                    if t_zien - START < 10:             # eerste ronde = historie
                        continue
                    fill = {"address": a, "coin": x["symbol"], "t": int(x["executed_timestamp"]), "side": 1 if x["side"] == "BUY" else -1,
                            "px": float(x["executed_price"]), "sz": float(x["executed_quantity"]), "maker": bool(x.get("is_maker")),
                            "oid": x.get("order_id")}
                    REC.w("ord_fills", {**fill, "t_zien": t_zien})
                    ord_boek(x["symbol"])
                    for d in (1, 2, 5, 10, 30, 60):
                        boek_plan.append((t_zien + d, x["symbol"]))
                    evalueer("orderly", fill, t_zien)
                    m = MIRROR.get(("orderly", str(x.get("order_id"))))
                    if m:
                        m.setdefault("t_leider_fill", int(x["executed_timestamp"]) / 1000)
            if t >= volgende_oo[a]:
                volgende_oo[a] = t + 15
                oo, _ = P.rows_cursor(P.q("openOrders", address=a))
                t_zien = nu()
                nu_open = set()
                for o in oo:
                    k = ("orderly", str(o.get("order_id") or o.get("algo_order_id")))
                    nu_open.add(k)
                    if k not in open_gezien:
                        open_gezien[k] = o
                        REC.w("ord_orders", {"address": a, "t_zien": t_zien, **o})
                        if o.get("price") and k not in MIRROR:
                            MIRROR[k] = {"venue": "orderly", "address": a, "oid": k[1], "coin": o["symbol"], "side": 1 if o["side"] == "BUY" else -1,
                                         "px": float(o["price"]), "sz": float(o.get("quantity") or 0), "t_order": int(o.get("created_time") or 0) / 1000,
                                         "t_zien": t_zien, "t_plaats": t_zien + 1, "type": o.get("type"), "trigger": o.get("trigger_price"),
                                         "reduce": o.get("reduce_only")}
                for k, m in MIRROR.items():
                    if m["venue"] == "orderly" and m["address"] == a and k not in nu_open and not m.get("t_eind_leider"):
                        m["t_eind_leider"] = t_zien
                        m["status_leider"] = "weg"
        # spiegelorders: marketTrades van hun munten
        syms = {m["coin"] for m in MIRROR.values() if m["venue"] == "orderly"}
        for s in syms:
            if t - laatste_mt.get(s, 0) >= 3:
                laatste_mt[s] = t
                rr, _ = P.rows_cursor(P.q("marketTrades", symbol=s, limit=200))
                for x in rr:
                    mirror_check(s, int(x["executed_timestamp"]) / 1000, float(x["executed_price"]))
        # geplande orderboek-snapshots
        rest = []
        for due, s in boek_plan:
            if nu() >= due:
                ord_boek(s)
            else:
                rest.append((due, s))
        boek_plan[:] = rest
        time.sleep(0.2)


async def onderhoud():
    while nu() - START < DUUR + 70:
        verwerk_pend()
        mirror_sluit()
        if int(nu() - START) % 600 < 1:
            log("loopt", round((nu() - START) / 60), "min, spiegel open", len(MIRROR), dict(TELLER))
        await asyncio.sleep(1)


async def main():
    if os.environ.get("TRADERS_JSON"):
        # papier-HL-traders, verdeeld over runners (max 10 traders per IP voor userFills)
        alle = [t["address"] for t in json.load(open(os.environ["TRADERS_JSON"]))["traders"]]
        i, n = int(os.environ.get("SHARD", "0")), int(os.environ.get("NSHARD", "1"))
        hl_l, ord_l = alle[i::n], []
    else:
        tr = pd.read_csv(os.path.join(os.path.dirname(__file__), "traders.csv"))
        hl_l = [a for a, v in zip(tr.address, tr.venue) if v == "hl"]
        ord_l = [a for a, v in zip(tr.address, tr.venue) if v == "orderly"]
    HL_LEIDERS.update(a.lower() for a in hl_l)
    coins = set()
    for a in hl_l:
        try:
            fl = requests.post(HL_INFO, json={"type": "userFillsByTime", "user": a, "startTime": int((nu() - 7 * 86400) * 1000)}, timeout=30).json()
            coins |= {f["coin"] for f in fl if ":" not in f["coin"] and not f["coin"].startswith("@")}
        except Exception as e:  # noqa: BLE001
            log("coins fout", repr(e)[:100])
    log("start live", len(hl_l), "HL", len(ord_l), "Orderly, munten", len(coins))
    taken = [hl_markt(coins), onderhoud(), asyncio.to_thread(ord_loop, ord_l)] + [hl_leider(a) for a in hl_l]
    await asyncio.gather(*taken)
    verwerk_pend()
    mirror_sluit(force=True)
    REC.close()
    log("klaar live")


if __name__ == "__main__":
    asyncio.run(main())
