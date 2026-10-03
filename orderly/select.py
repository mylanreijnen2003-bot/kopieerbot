"""Orderly stap 2-7: kandidaten, trades reconstrueren, filters, kiezen, eerlijke test op knipdatum, open posities.
Gebruik: python -m orderly.select pool <uit> | shard <uit> <pool_lijst.csv> <i> <n> | kies <uit> <shardmap...>   (env LIMIT=n: proef)

Rendement per trade = richting x (gem. uitstap / gem. instap - 1) - 0,2% kosten (zelfde als bt/vast_select.py).
Mark-to-market (MTM): elke trade wordt per dag gewaardeerd op de slotkoers (dag-candles), open trades tellen mee.
Potje: inzet per trade = potje / p90 van het aantal tegelijk open posities -> maandrendement op het potje.
Regel Mylan: >= 90 d historie, >= 100 trades, winst-% > 50, MTM-totaal > 0, nu actief, met de hand te volgen;
  sorteren op minste verliesmaanden (MTM), dan hoogste rendement per maand op het potje. Top 5.
Regel methode (vergelijking): winst-% > 50 -> top 30 op gem. rendement/trade -> 5 met meeste winstmaanden.
"""
from __future__ import annotations

import collections
import json
import os
import sys
import time

import numpy as np
import pandas as pd

from bt.vast_select import KOSTEN, trades_pct
from orderly import probe as P

MODUS = sys.argv[1]                 # pool | shard | kies
OUT = sys.argv[2]
os.makedirs(f"{OUT}/voorbeelden", exist_ok=True)
P.OUT = OUT
DAG = P.DAG
NU = P.NU
KNIP = int(pd.Timestamp("2026-08-19").value // 10**6)
MAXDAGEN = 300
LIMIT = int(os.environ.get("LIMIT", "0"))
START = time.time()
MAX_SEC = 5.2 * 3600
HIER = os.path.dirname(__file__)
log = P.log


# ---------------------------------------------------------------- data ophalen
def venster(kw, st, en, diepte=0):
    """Alle trades in [st, en] (archief, en <= nu-24u). Vol (2.000) -> venster halveren."""
    rr, _ = P.rows_cursor(P.q("trades", start_time=int(st), end_time=int(en), limit=2000, **kw))
    if len(rr) < 2000:
        return rr, False
    if en - st < 3_600_000 or diepte > 12:
        return rr, True
    m = (st + en) // 2
    a, fa = venster(kw, st, m, diepte + 1)
    b, fb = venster(kw, m + 1, en, diepte + 1)
    return a + b, fa or fb


def historie(kw):
    """Realtime (24 u) + archief in vensters van 30 d terug tot MAXDAGEN of 4 lege vensters op rij."""
    rt, _ = P.alle("trades", max_pag=10, limit=1000, **kw)
    alles, afgekapt, leeg, en = list(rt), False, 0, NU - DAG - 60_000
    while en > NU - MAXDAGEN * DAG and leeg < 4:
        st = en - 30 * DAG
        rr, f = venster(kw, st, en)
        alles += rr
        afgekapt |= f
        leeg = leeg + 1 if not rr else 0
        en = st - 1
    uniek = {str(t["id"]): t for t in alles}
    return list(uniek.values()), afgekapt


def pool():
    p = pd.read_csv(os.path.join(HIER, "pool_probe1.csv"))
    adr = dict(zip(p.address, p.bron))
    for sb in ["notional", "volume_7d", "volume_30d", "pnl_7d", "pnl_30d", "trade_count_24h"]:
        for x in P.alle("topAddresses", max_pag=1, sort_by=sb, limit=200)[0]:
            adr.setdefault(x["address"], "top_nieuw")
    for x in P.alle("platformPositions", max_pag=40, limit=5000)[0]:
        adr.setdefault(x["address"], "posities_nieuw")
    ms, _ = P.rows_cursor(P.q("marketSummary"))
    for m in ms:
        for x in P.rows_cursor(P.q("marketTrades", symbol=m["symbol"], limit=1000))[0]:
            adr.setdefault(x["address"], "markttrades_nieuw")
    log("pool", len(adr))
    return adr, ms


def voorfilter(adressen):
    """Fase A: 1 realtime-call + 1 venster nu-14d..nu-1d + 1 venster rond de knipdatum. Goedkoop: 15 weight."""
    pad = f"{OUT}/voorfilter.csv"
    if os.path.exists(pad) and time.time() - os.path.getmtime(pad) < 20 * 3600:
        oud = pd.read_csv(pad)
        if set(adressen) <= set(oud.address):
            log("voorfilter hergebruikt", len(oud))
            return oud
    rows = []
    for i, a in enumerate(adressen):
        rt, _ = P.alle("trades", max_pag=3, address=a, limit=1000)
        w, _ = P.rows_cursor(P.q("trades", address=a, start_time=NU - 14 * DAG, end_time=NU - DAG, limit=2000))
        k, _ = P.rows_cursor(P.q("trades", address=a, start_time=KNIP - 14 * DAG, end_time=KNIP, limit=2000))
        ts = [int(t["executed_timestamp"]) for t in rt + w]
        rows.append({"address": a, "fills_24u": len(rt), "fills_14d": len(set(str(t["id"]) for t in rt + w)),
                     "fills_7d": sum(t >= NU - 7 * DAG for t in ts), "fills_knip_14d": len(k),
                     "maker_pct_14d": round(100 * np.mean([bool(t.get("is_maker")) for t in rt + w]), 1) if rt + w else None})
        if i % 250 == 0:
            log("voorfilter", i, "/", len(adressen), round((time.time() - START) / 60), "min")
            pd.DataFrame(rows).to_csv(pad, index=False)
    df = pd.DataFrame(rows)
    df.to_csv(pad, index=False)
    return df


def candles(sym, cache={}):
    if sym not in cache:
        rr, _ = P.alle("candles", max_pag=2, symbol=sym, interval="1d", start_time=NU - (MAXDAGEN + 40) * DAG, limit=1000)
        d = pd.DataFrame(rr)
        if len(d):
            d = d.astype({"open": float, "high": float, "low": float, "close": float}).set_index("timestamp").sort_index()
        cache[sym] = d
    return cache[sym]


# ---------------------------------------------------------------- reconstructie
def signed_state(st):
    """accountState -> {account_id: {symbol: positie-dict}} met getekende hoeveelheid."""
    out = {}
    for a in (st.get("accounts") or [st]):
        pos = {}
        for p in a.get("positions") or []:
            q = float(p.get("position_qty") or 0)
            if str(p.get("side", "")).upper() == "SHORT" and q > 0:
                q = -q
            if q:
                pos[p["symbol"]] = {**p, "q": q}
        out[a.get("account_id") or ""] = {"pos": pos, "av": float(a.get("account_value") or 0),
                                          "upnl": float(a.get("total_unrealized_pnl") or 0), "broker": a.get("broker_id")}
    return out


def naar_fills(tr, acc, staat):
    """Fills -> start/after per symbool. Startpositie = huidige positie - som(fills): exact als de fills compleet zijn.
    Een positie van vóór de data wordt door trades_pct overgeslagen tot hij plat is (methode-regel)."""
    tr = sorted(tr, key=lambda t: (int(t["executed_timestamp"]), int(t["id"]) if str(t["id"]).isdigit() else 0))
    net = collections.defaultdict(float)
    for t in tr:
        net[t["symbol"]] += float(t["executed_quantity"]) * (1 if str(t["side"]).upper() == "BUY" else -1)
    pos = {s: round(staat.get(s, {}).get("q", 0.0) - n, 8) for s, n in net.items()}
    voor = {s: v for s, v in pos.items() if v != 0}
    voor.update({s: p["q"] for s, p in staat.items() if s not in net})        # positie zonder fills in de data
    fl = []
    for t in tr:
        s = t["symbol"]
        qty = float(t["executed_quantity"]) * (1 if str(t["side"]).upper() == "BUY" else -1)
        a = round(pos[s] + qty, 8)
        if a == 0:
            a = 0.0
        fl.append({"time": int(t["executed_timestamp"]), "coin": f"{acc[-6:]}|{s}", "start": pos[s], "after": a,
                   "px": float(t["executed_price"]), "maker": bool(t.get("is_maker"))})
        pos[s] = a
    return fl, voor


def trades_vol(fl):
    """Zelfde wiskunde als trades_pct, maar met instap/uitstap/richting en de nog open trades."""
    st, out, skip = {}, [], 0
    for f in fl:
        c, s, a, px = f["coin"], f["start"], f["after"], f["px"]
        if s == 0 and a != 0:
            st[c] = {"dir": 1 if a > 0 else -1, "in_q": abs(a), "in_c": abs(a) * px, "uit_q": 0.0, "uit_c": 0.0, "t": f["time"]}
            continue
        if c not in st:
            skip += 1
            continue
        p = st[c]
        flip = a != 0 and a * s < 0
        if abs(a) > abs(s) and not flip:
            p["in_q"] += abs(a) - abs(s)
            p["in_c"] += (abs(a) - abs(s)) * px
        else:
            q = abs(s) if (a == 0 or flip) else abs(s) - abs(a)
            p["uit_q"] += q
            p["uit_c"] += q * px
        if a == 0 or flip:
            inp, uit = p["in_c"] / p["in_q"], p["uit_c"] / p["uit_q"]
            out.append({"coin": c, "open": p["t"], "sluit": f["time"], "r": p["dir"] * (uit / inp - 1) - KOSTEN,
                        "dir": p["dir"], "in_px": inp, "uit_px": uit})
            st.pop(c)
            if flip:
                st[c] = {"dir": 1 if a > 0 else -1, "in_q": abs(a), "in_c": abs(a) * px, "uit_q": 0.0, "uit_c": 0.0, "t": f["time"]}
    for c, p in st.items():
        out.append({"coin": c, "open": p["t"], "sluit": None, "r": None, "dir": p["dir"], "in_px": p["in_c"] / p["in_q"], "uit_px": None})
    trades_vol.skip = skip
    return out


# ---------------------------------------------------------------- mark-to-market
def prijs(sym, t, kolom="close"):
    d = candles(sym)
    if not len(d):
        return None
    d = d[d.index <= t - DAG + 1] if t < NU else d          # slot van de laatste volle dag vóór t
    return float(d[kolom].iloc[-1]) if len(d) else None


def waarde(tr, t, mark):
    """MTM-waarde van één trade op moment t (fractie van de inzet)."""
    if t < tr["open"]:
        return 0.0
    if tr["sluit"] is not None and tr["sluit"] <= t:
        return tr["r"]
    sym = tr["coin"].split("|")[1]
    px = mark.get(tr["coin"]) if t >= NU else None
    if px is None:
        px = prijs(sym, t)
    if px is None:
        px = tr["in_px"]
    return tr["dir"] * (px / tr["in_px"] - 1) - KOSTEN


def mae(tr):
    """Slechtste tussentijdse stand (dag-high/low) tijdens de trade."""
    d = candles(tr["coin"].split("|")[1])
    if not len(d):
        return None
    eind = tr["sluit"] or NU
    d = d[(d.index >= tr["open"] - DAG) & (d.index <= eind)]
    if not len(d):
        return None
    ext = d.low.min() if tr["dir"] > 0 else d.high.max()
    return tr["dir"] * (ext / tr["in_px"] - 1) - KOSTEN


def p90_tegelijk(trs, t0, t1):
    ev = sorted([(x["open"], 1) for x in trs if x["open"] < t1 and (x["sluit"] or NU) > t0] +
                [(x["sluit"] or NU, -1) for x in trs if x["open"] < t1 and (x["sluit"] or NU) > t0])
    n, rij = 0, []
    for _, d in ev:
        n += d
        if d > 0:
            rij.append(n)
    return max(1.0, float(np.percentile(rij, 90))) if rij else 1.0


def maandgrenzen(t0, t1):
    g = [t0]
    m = pd.Timestamp(t0, unit="ms").to_period("M")
    while True:
        m += 1
        e = int(m.to_timestamp().value // 10**6)
        if e >= t1:
            break
        g.append(e)
    return g + [t1]


def periode(trs, fills, t0, t1, mark, eerste):
    """Statistieken over [t0, t1): gesloten trades, MTM per maand, potje-rendement, activiteit op t1."""
    # alleen trades die in de periode vanuit plat zijn geopend (een kopieerder erft geen oude posities)
    gesl = [x for x in trs if x["sluit"] is not None and x["open"] >= t0 and x["sluit"] < t1]
    r = np.array([x["r"] for x in gesl]) if gesl else np.array([])
    actief = [x for x in trs if t0 <= x["open"] < t1]
    g = maandgrenzen(t0, t1)
    mnd = {}
    for a, b in zip(g[:-1], g[1:]):
        mnd[pd.Timestamp(a, unit="ms").strftime("%Y-%m")] = sum(waarde(x, b, mark) - waarde(x, a, mark) for x in actief)
    p90 = p90_tegelijk(trs, t0, t1)
    tot = sum(mnd.values())
    dagen = (t1 - max(t0, eerste)) / DAG
    r30 = [x for x in trs if x["sluit"] is not None and t1 - 30 * DAG <= x["sluit"] < t1]
    f30 = [f for f in fills if t1 - 30 * DAG <= f["time"] < t1]
    uren = [(x["sluit"] - x["open"]) / 3.6e6 for x in gesl]
    munten = pd.Series([x["coin"].split("|")[1] for x in gesl])
    maes = [x["mae"] for x in gesl if x.get("mae") is not None]
    return {
        "trades": len(r), "winst_pct": round(100 * (r > 0).mean(), 1) if len(r) else None,
        "gem_r_pct": round(100 * r.mean(), 2) if len(r) else None,
        "gem_winst_pct": round(100 * r[r > 0].mean(), 2) if (r > 0).any() else None,
        "gem_verlies_pct": round(100 * r[r <= 0].mean(), 2) if (r <= 0).any() else None,
        "t": round(float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))), 2) if len(r) > 2 and r.std() > 0 else None,
        "maanden": len(mnd), "verliesmaanden": int(sum(v < 0 for v in mnd.values())),
        "winstmaanden_pct": round(100 * np.mean([v > 0 for v in mnd.values()]), 1) if mnd else None,
        "mtm_som_pct": round(100 * tot, 1), "p90_tegelijk": round(p90, 1),
        "potje_totaal_pct": round(100 * tot / p90, 1), "potje_pm_pct": round(100 * tot / p90 / max(dagen / 30.44, 0.5), 2),
        "historie_dagen": round((t1 - eerste) / DAG), "trades_7d": sum(x["sluit"] >= t1 - 7 * DAG for x in r30),
        "trades_14d": sum(x["sluit"] >= t1 - 14 * DAG for x in r30), "trades_per_dag_30d": round(len(r30) / 30, 2),
        "fills_per_dag_30d": round(len(f30) / 30, 1), "maker_pct_30d": round(100 * np.mean([f["maker"] for f in f30]), 1) if f30 else None,
        "houdtijd_mediaan_uur": round(float(np.median(uren)), 1) if uren else None,
        "kraken_pct": round(100 * munten.map(lambda s: P.munt(s) in P.KRAKEN).mean(), 1) if len(munten) else None,
        "mae_slechtst_pct": round(100 * min(maes), 1) if maes else None,
        "mae_mediaan_pct": round(100 * float(np.median(maes)), 1) if maes else None,
        "mnd": {k: round(100 * v, 2) for k, v in mnd.items()},
    }


# ---------------------------------------------------------------- per wallet
def wallet(a):
    acc = P.rows_cursor(P.q("accounts", address=a))[0]
    if not acc:
        return None
    staat = signed_state(P.q("accountState", address=a).get("data") or {})
    if len(acc) == 1:
        acc_id = acc[0].get("account_id") or ""
        tr, afg = historie({"address": a})
        per_acc = {acc_id: tr}
    else:
        per_acc, afg = {}, False
        for x in acc:
            tr, f = historie({"address": a, "account_id": x["account_id"], "broker_id": x["broker_id"]})
            per_acc[x["account_id"]], afg = tr, afg or f
    fills, voor, trs, mark, skip = [], {}, [], {}, 0
    for acc_id, tr in per_acc.items():
        pos = staat.get(acc_id, {}).get("pos", {})
        fl, v = naar_fills(tr, acc_id, pos)
        fills += fl
        voor.update({f"{acc_id[-6:]}|{s}": q for s, q in v.items()})
        tv = trades_vol(fl)
        skip += trades_vol.skip
        # controle: zelfde rendementen als bt/vast_select.trades_pct
        ref = trades_pct(fl)
        assert len(ref) == sum(x["sluit"] is not None for x in tv) and all(abs(x[3] - y["r"]) < 1e-9 for x, y in zip(ref, [t for t in tv if t["sluit"] is not None]))
        trs += tv
        for s, p in pos.items():
            if p.get("mark_price"):
                mark[f"{acc_id[-6:]}|{s}"] = float(p["mark_price"])
    if not fills:
        return {"address": a, "accounts": len(acc), "fills": 0}
    eerste = min(f["time"] for f in fills)
    trs.sort(key=lambda x: x["open"])
    for x in trs:
        x["mae"] = mae(x)
    return {"address": a, "accounts": len(acc), "brokers": ",".join(sorted({str(x.get("broker_id")) for x in acc})),
            "fills": len(fills), "afgekapt": afg, "eerste_fill": eerste, "voorposities": len(voor),
            "open_nu": sum(x["sluit"] is None for x in trs), "dekking_pct": round(100 * (1 - skip / len(fills)), 1),
            "voor": json.dumps({k: round(v, 6) for k, v in voor.items()}),
            "dubbel": int(pd.DataFrame([(f["time"], f["coin"], f["px"], f["start"]) for f in fills]).duplicated().sum()),
            "account_value": sum(v["av"] for v in staat.values()), "upnl": sum(v["upnl"] for v in staat.values()),
            "_trs": trs, "_fills": fills, "_mark": mark, "_staat": staat}


# ---------------------------------------------------------------- kiezen
def geschikt(s, min_dagen, min_trades):
    return (s["historie_dagen"] >= min_dagen and s["trades"] >= min_trades and (s["winst_pct"] or 0) > 50 and s["mtm_som_pct"] > 0
            and s["trades_7d"] >= 1 and s["trades_14d"] >= 4 and s["trades_per_dag_30d"] <= 3
            and (s["houdtijd_mediaan_uur"] or 0) >= 2 and s["fills_per_dag_30d"] <= 150 and (s["maker_pct_30d"] or 0) < 90
            and (s["gem_r_pct"] or 0) > 0 and s["p90_tegelijk"] <= 15
            and (s["mae_slechtst_pct"] if s["mae_slechtst_pct"] == s["mae_slechtst_pct"] and s["mae_slechtst_pct"] is not None else -99) > -50)


def kies(tab):
    """tab: rijen die 'geschikt' zijn. Geeft (regel_mylan, regel_methode) als lijsten adressen."""
    if not len(tab):
        return [], []
    m = tab.sort_values(["verliesmaanden", "potje_pm_pct"], ascending=[True, False]).address.head(5).tolist()
    me = tab.sort_values("gem_r_pct", ascending=False).head(30)
    me = me.sort_values(["winstmaanden_pct", "potje_pm_pct"], ascending=False).address.head(5).tolist()
    return m, me


def main_pool():
    adr, _ = pool()
    pd.DataFrame({"address": list(adr), "bron": list(adr.values())}).to_csv(f"{OUT}/pool_lijst.csv", index=False)


def main_shard(lijst_pad, i, n):
    p = pd.read_csv(lijst_pad).sort_values("address")
    adr = dict(zip(p.address, p.bron))
    lijst = p.address.tolist()[i::n]
    if LIMIT:
        lijst = lijst[:LIMIT]
    log("shard", i, "van", n, "adressen", len(lijst))
    vf = voorfilter(lijst)
    vf["bron"] = vf.address.map(adr)
    nu_actief = (vf.fills_7d >= 1) & (vf.fills_14d >= 4)
    door = vf[(nu_actief | (vf.fills_knip_14d >= 4)) & (vf.fills_24u < 3000) & (vf.fills_14d < 2000)
              & ~((vf.maker_pct_14d.fillna(0) >= 90) & (vf.fills_14d > 200))]
    log("voorfilter klaar", len(vf), "door", len(door), "(nu actief", int(nu_actief.sum()), ", actief rond knip",
        int((vf.fills_knip_14d >= 4).sum()), ")")
    rows, alle_trades, oudste = [], [], NU
    for k, a in enumerate(door.address):
        if time.time() - START > MAX_SEC:
            log("tijdslimiet: gestopt bij", k); break
        try:
            w = wallet(a)
        except Exception as e:  # noqa: BLE001
            log("fout wallet", a[:10], repr(e)[:300]); continue
        if not w or not w.get("fills"):
            continue
        trs, fills, mark = w.pop("_trs"), w.pop("_fills"), w.pop("_mark")
        staat = w.pop("_staat")
        oudste = min(oudste, w["eerste_fill"])
        nu_s = periode(trs, fills, w["eerste_fill"], NU, mark, w["eerste_fill"])
        k_s = periode(trs, fills, w["eerste_fill"], KNIP, mark, w["eerste_fill"]) if w["eerste_fill"] < KNIP else None
        t_s = periode(trs, fills, KNIP, NU, mark, KNIP) if w["eerste_fill"] < KNIP else None
        rows.append({**w, "eerste_fill": str(pd.Timestamp(w["eerste_fill"], unit="ms").date()),
                     **{f"nu_{x}": v for x, v in nu_s.items()},
                     **({f"keuze_{x}": v for x, v in k_s.items()} if k_s else {}),
                     **({f"test_{x}": v for x, v in t_s.items()} if t_s else {}),
                     "open_posities": json.dumps([{"symbol": sy, "q": pp["q"], "notional": pp.get("notional"),
                                                   "upnl": pp.get("unrealized_pnl"), "lev": pp.get("leverage"), "broker": v["broker"]}
                                                  for v in staat.values() for sy, pp in v["pos"].items()])})
        for x in trs:
            alle_trades.append({"address": a, **{c: x[c] for c in ("coin", "open", "sluit", "r", "dir", "in_px", "uit_px", "mae")}})
        if k % 25 == 0:
            log("wallets", k, "/", len(door), round((time.time() - START) / 60), "min")
            pd.DataFrame(rows).to_csv(f"{OUT}/wallets.csv", index=False)
    pd.DataFrame(rows).to_csv(f"{OUT}/wallets.csv", index=False)
    pd.DataFrame(alle_trades).to_csv(f"{OUT}/trades.csv.gz", index=False)
    json.dump({"pool": len(lijst), "voorfilter_door": len(door), "wallets": len(rows), "oudste": int(oudste)},
              open(f"{OUT}/shard.json", "w"))
    log("shard klaar", len(rows), "oudste fill", pd.Timestamp(oudste, unit="ms"))


def main_kies(mappen):
    import ast
    tab = pd.concat([pd.read_csv(f"{m}/wallets.csv") for m in mappen if os.path.exists(f"{m}/wallets.csv")], ignore_index=True)
    tr = pd.concat([pd.read_csv(f"{m}/trades.csv.gz") for m in mappen if os.path.exists(f"{m}/trades.csv.gz")], ignore_index=True)
    sh = [json.load(open(f"{m}/shard.json")) for m in mappen if os.path.exists(f"{m}/shard.json")]
    for c in tab.columns:
        if c.endswith("_mnd"):
            tab[c] = tab[c].map(lambda x: ast.literal_eval(x) if isinstance(x, str) else x)
    tab.to_csv(f"{OUT}/wallets.csv", index=False)
    tr.to_csv(f"{OUT}/trades.csv.gz", index=False)
    oudste = min(x["oudste"] for x in sh) if sh else NU
    besch = (KNIP - oudste) / DAG
    min_dagen_k = 90 if besch >= 95 else max(30, int(besch) - 5)
    min_tr_k = 100 if min_dagen_k == 90 else int(round(100 * min_dagen_k / 90))
    uit = {"knip": "2026-08-19", "oudste_data": str(pd.Timestamp(oudste, unit="ms").date()), "shards": len(sh),
           "pool": sum(x["pool"] for x in sh), "voorfilter_door": sum(x["voorfilter_door"] for x in sh),
           "wallets_met_fills": len(tab), "test_eisen": {"min_dagen": min_dagen_k, "min_trades": min_tr_k}}

    def sub(df, pre):
        return df.rename(columns=lambda c: c[len(pre):] if c.startswith(pre) else (c if c in ("address",) else "_" + c))

    if "keuze_trades" in tab:
        kt = tab[tab.keuze_trades.notna()].copy()
        ks = sub(kt, "keuze_")
        ok = [geschikt(r, min_dagen_k, min_tr_k) for _, r in ks.iterrows()]
        kt, ks = kt[ok], ks[ok]
        uit["test_geschikt"] = len(kt)
        m, me = kies(ks)
        for naam, sel in (("regel_mylan", m), ("regel_methode", me)):
            s = kt.set_index("address").loc[sel] if sel else kt.iloc[:0]
            uit[naam] = {"gekozen": sel, **{f"test_{c}": s[f"test_{c}"].tolist() for c in
                         ("trades", "winst_pct", "gem_r_pct", "verliesmaanden", "potje_pm_pct", "potje_totaal_pct")},
                         "keuze_potje_pm_pct": s["keuze_potje_pm_pct"].tolist(),
                         "test_potje_pm_pct_gem": round(float(s.test_potje_pm_pct.mean()), 2) if len(s) else None}
        uit["basis_alle_geschikt_test_potje_pm_pct"] = {"gem": round(float(kt.test_potje_pm_pct.mean()), 2) if len(kt) else None,
                                                         "mediaan": round(float(kt.test_potje_pm_pct.median()), 2) if len(kt) else None,
                                                         "aandeel_winst": round(float((kt.test_potje_totaal_pct > 0).mean()), 2) if len(kt) else None}
        kt.to_csv(f"{OUT}/test_geschikt.csv", index=False)

    ns = sub(tab, "nu_")
    for md, mt in ((90, 100), (60, 60)):
        okv = [geschikt(r, md, mt) for _, r in ns.iterrows()]
        if sum(okv) >= 5 or md == 60:
            break
    g, gs = tab[okv], ns[okv]
    uit["vandaag_eisen"] = {"min_dagen": md, "min_trades": mt, "geschikt": len(g)}
    m, me = kies(gs)
    uit["vandaag_regel_mylan"], uit["vandaag_regel_methode"] = m, me
    g.to_csv(f"{OUT}/vandaag_geschikt.csv", index=False)
    mp = []
    for a in dict.fromkeys(m + me):
        x = tr[tr.address == a]
        for sy, v in x.coin.str.split("|").str[1].value_counts(normalize=True).items():
            mu = P.munt(sy)
            mp.append({"address": a, "symbol": sy, "munt": mu, "aandeel_pct": round(100 * v, 1),
                       "kopieer_op": "Kraken" if mu in P.KRAKEN else "Orderly-frontend"})
    pd.DataFrame(mp).to_csv(f"{OUT}/munten_gekozen.csv", index=False)
    json.dump(uit, open(f"{OUT}/uitslag.json", "w"), indent=1, default=str)
    log("klaar", json.dumps(uit, default=str)[:4000])


def main():
    if MODUS == "pool":
        main_pool()
    elif MODUS == "shard":
        main_shard(sys.argv[3], int(sys.argv[4]), int(sys.argv[5]))
    else:
        main_kies(sys.argv[3:])


if __name__ == "__main__":
    main()
