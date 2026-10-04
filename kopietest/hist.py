"""Kopietest, historisch deel: hoe handelen de traders en hoeveel kan een bot ervan nadoen?
Per trader (Hyperliquid + Orderly), laatste 30 dagen:
  - fills: maker/taker, fee-tarief van de trader, fills per trade, bijkopen (DCA)
  - orders: ordertypes, TP/SL/trigger, reduce-only, annuleringen, wachttijd tussen order en fill
  - open orders nu (zie je TP/SL van anderen?)
  - 'edge': brutowinst van de trader per verhandelde dollar (bps) -> ruimte voor kopieerkosten
  - maker-spiegel: zou jouw limietorder op dezelfde prijs ook vullen? (1m-candles, prijs moet er dóór)
  - minimale ordergrootte: welk deel van de fills valt weg bij kapitaal C (proportioneel kopiëren)
Gebruik: python -m kopietest.hist <uitmap>
"""
from __future__ import annotations

import collections
import json
import os
import sys
import time
import traceback

OUT = sys.argv[1]
os.makedirs(f"{OUT}/voorbeelden", exist_ok=True)
sys.argv = ["x", "kopietest", OUT]          # orderly.select leest argv
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bot import hl  # noqa: E402
from orderly import probe as P  # noqa: E402
from orderly import select as S  # noqa: E402

P.OUT = OUT
S.MAXDAGEN = 31
DAG = 86_400_000
NU = int(time.time() * 1000)
VAN = NU - 30 * DAG
KAPITAAL = [500, 2000, 10000]
HORIZON_MIN = [0, 1, 5, 30]
LAT_S = {"hl": 2, "orderly": 4}          # tijd tot jouw spiegelorder in het boek staat
FEES = {"hl": (1.5, 4.5), "orderly": (3.0, 6.0)}   # jouw maker/taker in bps (HL tier 0; WOOFi Pro tier 1)
log = P.log


def bewaar(naam, obj):
    json.dump(obj, open(f"{OUT}/voorbeelden/{naam}.json", "w"), indent=1, default=str)


# ------------------------------------------------------------------ Hyperliquid
def hl_data(a):
    raw = []
    for f in hl._window(a, VAN, NU):
        raw.append(f)
    seen, fl = set(), []
    for f in raw:
        k = (f.get("tid"), f.get("oid"), f.get("time"))
        if k in seen or hl.kind(f["coin"]) != "perp":
            continue
        seen.add(k)
        sz = float(f["sz"])
        sgn = 1 if f["side"] == "B" else -1
        st = float(f.get("startPosition") or 0)
        fl.append({"t": int(f["time"]), "coin": f["coin"], "sym": f["coin"], "side": sgn, "sz": sz, "px": float(f["px"]),
                   "maker": not bool(f.get("crossed")), "oid": str(f.get("oid")), "start": st, "after": round(st + sgn * sz, 10),
                   "fee": float(f.get("fee") or 0), "liq": bool(f.get("liquidation"))})
    fl.sort(key=lambda x: (x["t"], x["oid"]))
    orders = hl.info({"type": "historicalOrders", "user": a}, weight=20) or []
    od = {}
    for o in orders:
        x = o.get("order", {})
        od[str(x.get("oid"))] = {"created": int(x.get("timestamp") or 0), "type": x.get("orderType"), "tif": x.get("tif"),
                                 "trigger": bool(x.get("isTrigger")), "tpsl": bool(x.get("isPositionTpsl")),
                                 "reduce": bool(x.get("reduceOnly")), "status": o.get("status"), "px": float(x.get("limitPx") or 0),
                                 "coin": x.get("coin"), "side": x.get("side")}
    onbekend = sorted({f["oid"] for f in fl if f["maker"] and f["oid"] not in od})
    import random
    random.seed(1)
    for oid in random.sample(onbekend, min(400, len(onbekend))):
        try:
            j = hl.info({"type": "orderStatus", "user": a, "oid": int(oid)}, weight=3)
            x = (j.get("order") or {}).get("order") or {}
            if x:
                od[oid] = {"created": int(x.get("timestamp") or 0), "type": x.get("orderType"), "tif": x.get("tif"),
                           "trigger": bool(x.get("isTrigger")), "tpsl": bool(x.get("isPositionTpsl")), "reduce": bool(x.get("reduceOnly")),
                           "status": (j.get("order") or {}).get("status"), "px": float(x.get("limitPx") or 0), "coin": x.get("coin"),
                           "side": x.get("side")}
        except Exception:  # noqa: BLE001
            pass
    openo = hl.info({"type": "frontendOpenOrders", "user": a}, weight=20) or []
    stt = hl.info({"type": "clearinghouseState", "user": a}, weight=2) or {}
    av = float((stt.get("marginSummary") or {}).get("accountValue") or 0)
    lev = [float((p.get("position") or {}).get("leverage", {}).get("value") or 0) for p in stt.get("assetPositions") or []]
    pts, _ = hl.av_points(a)
    return fl, od, openo, {"account_value": av, "lev_open": lev, "n_open_pos": len(stt.get("assetPositions") or [])}, pts


def hl_candles(coins):
    out = {}
    for c in coins:
        out[c] = hl.candles(c, NU - int(3.4 * DAG), NU, "1m")
    return out


# ------------------------------------------------------------------ Orderly
def ord_orders(a, st, en, diepte=0):
    rr, _ = P.rows_cursor(P.q("historicalOrders", address=a, start_time=int(st), end_time=int(en), limit=500))
    if len(rr) < 500 or en - st < 600_000 or diepte > 10:
        return rr
    m = (st + en) // 2
    return ord_orders(a, st, m, diepte + 1) + ord_orders(a, m + 1, en, diepte + 1)


def ord_data(a):
    acc = P.rows_cursor(P.q("accounts", address=a))[0]
    if not acc:
        raise RuntimeError("geen Orderly-account")
    staat = S.signed_state(P.q("accountState", address=a).get("data") or {})
    fl = []
    per = {}
    if len(acc) <= 1:
        per[acc[0]["account_id"] if acc else ""] = S.historie({"address": a})[0]
    else:
        for x in acc:
            per[x["account_id"]] = S.historie({"address": a, "account_id": x["account_id"], "broker_id": x["broker_id"]})[0]
    for acc_id, tr in per.items():
        tr = sorted(tr, key=lambda t: (int(t["executed_timestamp"]), int(t["id"]) if str(t["id"]).isdigit() else 0))
        f2, _ = S.naar_fills(tr, acc_id, staat.get(acc_id, {}).get("pos", {}))
        for f, t in zip(f2, tr):            # naar_fills sorteert op dezelfde sleutel -> 1-op-1
            q = abs(f["after"] - f["start"])
            fl.append({"t": f["time"], "coin": f["coin"], "sym": f["coin"].split("|")[1], "side": 1 if f["after"] > f["start"] else -1,
                       "sz": q, "px": f["px"], "maker": f["maker"], "oid": str(t.get("order_id")), "start": f["start"], "after": f["after"],
                       "fee": float(t.get("fee") or 0), "liq": t.get("order_id") is None})
    fl = [f for f in fl if f["t"] >= VAN]
    fl.sort(key=lambda x: (x["t"], x["oid"]))
    od = {}
    for o in ord_orders(a, VAN - 2 * DAG, NU):
        od[str(o.get("order_id"))] = {"created": int(o.get("created_time") or 0), "type": o.get("type"), "tif": None,
                                      "trigger": o.get("trigger_price") is not None, "tpsl": "TP" in str(o.get("type")) or "SL" in str(o.get("type")),
                                      "reduce": bool(o.get("reduce_only")), "status": o.get("status"), "px": float(o.get("price") or 0),
                                      "coin": o.get("symbol"), "side": o.get("side")}
    openo = P.rows_cursor(P.q("openOrders", address=a))[0]
    av = sum(v["av"] for v in staat.values())
    dw, _ = P.alle("userDepositsWithdrawals", max_pag=5, address=a)
    pts = equity_terug(fl, av, dw)
    lev = [float(p.get("leverage") or 0) for v in staat.values() for p in v["pos"].values()]
    return fl, od, openo, {"account_value": av, "lev_open": lev, "n_open_pos": sum(len(v["pos"]) for v in staat.values())}, pts


def equity_terug(fl, e_nu, dw):
    """Equity terugrekenen vanaf nu: E(t) = E_nu - gerealiseerde winst na t + fees na t - netto stortingen na t."""
    pos, ev = {}, []
    for f in fl:
        c = f["coin"]
        q, avg = pos.get(c, (f["start"], f["px"]))
        d = f["after"] - f["start"]
        pnl = 0.0
        if q != 0 and q * d < 0:                       # afbouwen
            dicht = min(abs(d), abs(q))
            pnl = dicht * (f["px"] - avg) * (1 if q > 0 else -1)
        nq = f["after"]
        if nq != 0 and (q == 0 or q * nq < 0):
            avg = f["px"]
        elif abs(nq) > abs(q):
            avg = (abs(q) * avg + (abs(nq) - abs(q)) * f["px"]) / abs(nq)
        pos[c] = (nq, avg)
        ev.append((f["t"], pnl - f["fee"]))
    for x in dw:
        if str(x.get("status")).upper() == "COMPLETED":
            amt = float(x.get("amount") or 0) * (1 if str(x.get("side")).lower() == "deposit" else -1)
            ev.append((int(x.get("created_time") or 0), ("dep", amt)))
    ev.sort(key=lambda x: x[0])
    pts, e = [], e_nu
    for t, v in reversed(ev):
        if t < VAN:
            break
        pts.append((t, e))
        e -= v[1] if isinstance(v, tuple) else v
    pts.append((VAN, e))
    return sorted(pts)


def ord_candles(syms):
    out = {}
    for s in syms:
        rr, _ = P.alle("candles", max_pag=12, symbol=s, interval="1m", start_time=VAN - DAG, limit=5000)
        out[s] = {int(r["timestamp"]): (float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])) for r in rr}
    return out


# ------------------------------------------------------------------ analyse (gezamenlijk)
def trades_dollar(fl):
    """Plat->plat per munt met dollars. Trades die al open stonden bij de start van het venster: overslaan."""
    st, out = {}, []
    for f in fl:
        c, s, a, px = f["coin"], f["start"], f["after"], f["px"]
        if s == 0 and a != 0:
            st[c] = {"dir": 1 if a > 0 else -1, "iq": abs(a), "ic": abs(a) * px, "uq": 0.0, "uc": 0.0, "t": f["t"],
                     "eerste": abs(a), "max": abs(a), "fills": 1, "bij": 0}
            continue
        if c not in st:
            continue
        p = st[c]
        p["fills"] += 1
        flip = a != 0 and a * s < 0
        if abs(a) > abs(s) and not flip:
            p["iq"] += abs(a) - abs(s)
            p["ic"] += (abs(a) - abs(s)) * px
            p["bij"] += 1
            p["max"] = max(p["max"], abs(a))
        else:
            q = abs(s) if (a == 0 or flip) else abs(s) - abs(a)
            p["uq"] += q
            p["uc"] += q * px
        if a == 0 or flip:
            pnl = p["dir"] * (p["uc"] - p["ic"] * p["uq"] / p["iq"])
            out.append({"coin": c, "open": p["t"], "sluit": f["t"], "pnl": pnl, "notional": p["ic"] + p["uc"],
                        "r": p["dir"] * ((p["uc"] / p["uq"]) / (p["ic"] / p["iq"]) - 1), "fills": p["fills"], "bij": p["bij"],
                        "max_x": p["max"] / p["eerste"]})
            st.pop(c)
            if flip:
                st[c] = {"dir": 1 if a > 0 else -1, "iq": abs(a), "ic": abs(a) * px, "uq": 0.0, "uc": 0.0, "t": f["t"],
                         "eerste": abs(a), "max": abs(a), "fills": 1, "bij": 0}
    return out, st


def av_at(pts, t):
    return hl.av_at(pts, t) if pts else None


def mirror(fl, od, cand, venue):
    """Maker-spiegel: jouw post-only order op de prijs van de trader, LAT_S na zijn order. Vult als de prijs er dóór gaat."""
    rows = []
    lat = LAT_S[venue] * 1000
    for f in fl:
        if not f["maker"]:
            continue
        o = od.get(f["oid"])
        c = cand.get(f["sym"]) or {}
        if not o or not c or not o["created"]:
            continue
        ks = sorted(c)
        if not ks or f["t"] < ks[0] or f["t"] > ks[-1] + 60_000:
            continue
        tp = o["created"] + lat
        lead = (f["t"] - o["created"]) / 1000
        r = {"venue": venue, "sym": f["sym"], "t": f["t"], "side": f["side"], "px": f["px"], "notional": f["sz"] * f["px"],
             "lead_s": lead, "kan_spiegelen": f["t"] > tp, "trigger": o["trigger"], "type": o["type"], "tif": o["tif"]}
        if f["t"] > tp:
            def door(start, eind):
                for k in ks:
                    if start <= k <= eind:
                        _, hi, lo, _ = c[k]
                        if (f["side"] > 0 and lo < f["px"]) or (f["side"] < 0 and hi > f["px"]):
                            return True
                return False
            ruim = tp // 60_000 * 60_000                  # minuut waarin jouw order komt (kan iets te ruim zijn)
            streng = ruim + 60_000                        # pas vanaf de volgende hele minuut
            for h in HORIZON_MIN:
                eind = f["t"] + h * 60_000
                r[f"vult_{h}m"] = door(ruim, eind)
                r[f"vult_{h}m_streng"] = door(streng, eind)
            # niet gevuld na 5 min -> alsnog nemen (taker): koers na 5 min vs zijn prijs
            k5 = (f["t"] + 5 * 60_000) // 60_000 * 60_000
            if k5 in c:
                r["achterstand_5m_bps"] = f["side"] * (c[k5][3] / f["px"] - 1) * 1e4
        rows.append(r)
    return rows


def analyse(a, venue, groep, fl, od, openo, stt, pts, cand, minnot):
    tr, open_ = trades_dollar(fl)
    notional = sum(f["sz"] * f["px"] for f in fl)
    mk = [f for f in fl if f["maker"]]
    fee_mk = sum(f["fee"] for f in mk) / max(1e-9, sum(f["sz"] * f["px"] for f in mk)) * 1e4 if mk else None
    tk = [f for f in fl if not f["maker"]]
    fee_tk = sum(f["fee"] for f in tk) / max(1e-9, sum(f["sz"] * f["px"] for f in tk)) * 1e4 if tk else None
    # open trades meewaarderen tegen laatste prijs
    mtm = 0.0
    for c, p in open_.items():
        sym = c.split("|")[-1] if venue == "orderly" else c
        cc = cand.get(sym) or {}
        last = cc[max(cc)][3] if cc else None
        if last:
            q = p["iq"] - p["uq"]
            mtm += p["dir"] * (p["uc"] + q * last - p["ic"])
    pnl = sum(t["pnl"] for t in tr) + mtm
    tr_not = sum(t["notional"] for t in tr) + sum(p["ic"] * 2 for p in open_.values())
    used = fl
    lead = []
    types = collections.Counter()
    for f in fl:
        o = od.get(f["oid"])
        if o:
            types[(o["type"], o["tif"], "trigger" if o["trigger"] else "", "reduce" if o["reduce"] else "")] += 1
            if f["maker"] and o["created"]:
                lead.append((f["t"] - o["created"]) / 1000)
    ost = collections.Counter(o["status"] for o in od.values())
    mins = {}
    for C in KAPITAAL:
        weg, weg_n = 0, 0.0
        for f in fl:
            E = av_at(pts, f["t"]) or stt["account_value"] or None
            if not E or E <= 0:
                continue
            n = f["sz"] * f["px"] * C / E
            if n < minnot(f["sym"]):
                weg += 1
                weg_n += f["sz"] * f["px"]
        mins[f"te_klein_{C}_pct"] = round(100 * weg / max(1, len(fl)), 1)
        mins[f"te_klein_{C}_notional_pct"] = round(100 * weg_n / max(1e-9, notional), 1)
    r = np.array([t["r"] for t in tr]) if tr else np.array([])
    q = lambda v, p: round(float(np.percentile(v, p)), 1) if len(v) else None  # noqa: E731
    return {
        "address": a, "venue": venue, "groep": groep, "fills": len(fl), "trades": len(tr), "open_trades": len(open_),
        "dagen_actief": len({f["t"] // DAG for f in fl}), "trades_per_dag": round(len(tr) / 30, 2),
        "maker_pct_fills": round(100 * len(mk) / max(1, len(fl)), 1),
        "maker_pct_notional": round(100 * sum(f["sz"] * f["px"] for f in mk) / max(1e-9, notional), 1),
        "fee_maker_bps": round(fee_mk, 2) if fee_mk is not None else None, "fee_taker_bps": round(fee_tk, 2) if fee_tk is not None else None,
        "notional_30d": round(notional), "pnl_30d_bruto": round(pnl + sum(f["fee"] for f in used)), "pnl_30d_netto": round(pnl),
        "edge_bps_bruto": round((pnl + sum(f["fee"] for f in used)) / max(1e-9, tr_not) * 1e4, 2),
        "edge_bps_netto": round(pnl / max(1e-9, tr_not) * 1e4, 2),
        "winst_pct": round(100 * (r > 0).mean(), 1) if len(r) else None, "gem_r_pct": round(100 * r.mean(), 3) if len(r) else None,
        "fills_per_trade_med": q([t["fills"] for t in tr], 50), "bijkoop_pct": round(100 * np.mean([t["bij"] > 0 for t in tr]), 1) if tr else None,
        "max_x_med": q([t["max_x"] for t in tr], 50), "max_x_p90": q([t["max_x"] for t in tr], 90), "max_x_max": q([t["max_x"] for t in tr], 100),
        "houdtijd_med_min": q([(t["sluit"] - t["open"]) / 60000 for t in tr], 50),
        "lead_med_s": q(lead, 50), "lead_p10_s": q(lead, 10), "lead_p90_s": q(lead, 90),
        "lead_onder_lat_pct": round(100 * np.mean([x < LAT_S[venue] for x in lead]), 1) if lead else None,
        "orders_bekend_pct": round(100 * sum(f["oid"] in od for f in fl) / max(1, len(fl)), 1),
        "ordertypes": json.dumps({"|".join(str(x) for x in k): v for k, v in types.most_common(8)}),
        "orderstatus": json.dumps(dict(ost.most_common(6))), "cancel_ratio": round((ost.get("canceled", 0) + ost.get("CANCELLED", 0)) / max(1, len(od)), 3),
        "open_orders_nu": len(openo), "open_orders_trigger": sum(bool(o.get("isTrigger") or o.get("trigger_price") or o.get("isPositionTpsl")) for o in openo),
        "account_value": round(stt["account_value"]), "n_open_pos": stt["n_open_pos"], "lev_open_max": max(stt["lev_open"]) if stt["lev_open"] else None,
        "liq_fills": sum(f["liq"] for f in fl) if venue == "hl" else None, **mins,
    }


def main():
    tr = pd.read_csv(os.path.join(os.path.dirname(__file__), "traders.csv"))
    if os.environ.get("PROEF"):
        tr = pd.concat([tr[tr.venue == "hl"].head(1), tr[tr.venue == "orderly"].head(1)])
    ms, _ = P.rows_cursor(P.q("marketSummary"))
    ord_min = {m["symbol"]: float(m.get("min_notional") or 10) for m in ms}
    rows, mir, alle_fills = [], [], []
    for a, venue, groep in zip(tr.address, tr.venue, tr.groep):
        try:
            if venue == "hl":
                fl, od, openo, stt, pts = hl_data(a)
                cand = hl_candles(sorted({f["coin"] for f in fl if f["t"] >= NU - 3.4 * DAG}))
                minnot = lambda s: 10.0  # noqa: E731
            else:
                fl, od, openo, stt, pts = ord_data(a)
                cand = ord_candles(sorted({f["sym"] for f in fl}))
                minnot = lambda s: ord_min.get(s, 10.0)  # noqa: E731
            bewaar(f"open_{venue}_{a[:8]}", openo[:20])
            bewaar(f"orders_{venue}_{a[:8]}", list(od.items())[:15])
            row = analyse(a, venue, groep, fl, od, openo, stt, pts, cand, minnot)
            m = mirror(fl, od, cand, venue)
            for x in m:
                x["address"] = a
            mir += m
            if m:
                d = pd.DataFrame(m)
                d2 = d[d.kan_spiegelen]
                row["spiegel_n"] = len(d)
                row["spiegel_kan_pct"] = round(100 * d.kan_spiegelen.mean(), 1)
                for h in HORIZON_MIN:
                    if f"vult_{h}m" in d2 and len(d2):
                        row[f"spiegel_vult_{h}m_pct"] = round(100 * d2[f"vult_{h}m"].mean(), 1)
                if "achterstand_5m_bps" in d2 and len(d2):
                    nv = d2[~d2.vult_5m.astype(bool)] if "vult_5m" in d2 else d2
                    row["achterstand_5m_bps_gem"] = round(float(nv.achterstand_5m_bps.mean()), 2) if len(nv) else None
            rows.append(row)
            for f in fl:
                alle_fills.append({"address": a, "venue": venue, **{k: f[k] for k in ("t", "sym", "side", "sz", "px", "maker", "oid", "start", "after", "fee")},
                                   "lead_s": (f["t"] - od[f["oid"]]["created"]) / 1000 if f["oid"] in od and od[f["oid"]]["created"] else None})
            log("klaar", venue, a[:10], {k: row.get(k) for k in ("fills", "trades", "maker_pct_fills", "edge_bps_bruto", "lead_med_s", "spiegel_vult_0m_pct")})
        except Exception:  # noqa: BLE001
            log("fout", venue, a[:10], traceback.format_exc()[-1500:])
        pd.DataFrame(rows).to_csv(f"{OUT}/traders.csv", index=False)
    pd.DataFrame(mir).to_csv(f"{OUT}/spiegel.csv.gz", index=False)
    pd.DataFrame(alle_fills).to_csv(f"{OUT}/fills.csv.gz", index=False)


if __name__ == "__main__":
    main()
