"""Papier C (Lighter): top 5 Lighter-traders op papier volgen, elke 6 uur. Zelfde opzet als bt/papier2.py.

Regels (vooraf vastgelegd 4 okt 2026):
- €100 papierpotje per trader; inzet per trade = min(100 / K, 5% van het startkapitaal). K = p90 posities tegelijk.
- Alleen posities die de trader ná toevoegen vanuit plat opent (Lighter geeft de positie vóór elke fill).
- Alle munten tellen mee (ook alleen-Lighter); per trade gelabeld of hij op Kraken kan.
- Proportioneel kopiëren (sinds 4 okt 20:00, na kopiemethode-test): bijkopen en afbouwen naar verhouding meedoen.
  Factor f = inzet / typische grootste positie van de trader (mediaan laatste 60 d); eigen positie max 3x inzet.
  Kosten 0,16% per kant over alle omzet. Vergelijking in de kolom 'alleen 1e instap' (oude methode, vaste inzet).
- Geen stop per trade meer (die verkocht precies op het moment dat deze traders bijkopen); wel trader-/portefeuille-stops.
- Trader eruit bij: potje <= €80 | potje <= piek x 0,75 | 7 dagen geen fill | laatste 30 d (>= 15 trades) < -10% |
  > 10 posities open | accountwaarde < $1000 of < 50% van bij toevoegen. Vervanger: volgende uit de selectie, vers €100.
- Portefeuille-stop: waarde <= 85% van start -> alles stil.
Controlegroepen (vast, geen vervanging, geen trader-/portefeuille-stops, wel stop per trade):
- top20: de hele selectielijst (20); rnd20: 20 willekeurig uit dezelfde gefilterde pool (vaste seed).
Inzet voor alle groepen gelijk: min(100 / K, €25), zodat de % per groep vergelijkbaar zijn.
Gebruik: python -m lighter.papier <privé-map met selectie.json/state*.json> <openbare map> <kraken.json> [C|top20|rnd20]
Openbaar alleen afgekorte adressen; state.json (met account-indexen) blijft privé en wordt versleuteld opgeslagen.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd
import requests

from bt.p2_common import KOSTEN, trades_open
from lighter import api
from lighter.scan import fill_rij
from lighter.selectie import basis, kort, reconstrueer

NU = int(time.time() * 1000)
DAG, UUR, KW = api.DAG, 3_600_000, 15 * 60_000
POT, N_ACTIEF, TRADE_STOP, PORT_STOP, CAP = 100.0, 5, 0.10, 0.85, 25.0
KANT, MAX_X = 0.0016, 3.0
GROEPEN = {"C": {"n": 5, "vast": False, "state": "state.json", "map": ""},
           "top20": {"n": 20, "vast": True, "state": "state_top20.json", "map": "top20"},
           "rnd20": {"n": 20, "vast": True, "state": "state_rnd20.json", "map": "rnd20"}}
REGELS = {"stop": 0.20, "daling": 0.25, "inactief_d": 7, "vorm30": -0.10, "max_open": 10}
MELDINGEN = []
_cand = {}


def candles(m, res, start):
    """{t_ms: (o, h, l, c)} van start tot nu voor markt m (Lighter, max 500 per call)."""
    key = (m, res)
    if key in _cand:
        return _cand[key]
    stap = {"15m": KW, "1h": UUR}[res]
    out, t = {}, start - start % stap
    while t < NU:
        st, d = api.get("candles", {"market_id": m, "resolution": res, "start_timestamp": t,
                                    "end_timestamp": min(NU, t + 500 * stap), "count_back": 500})
        lijst = next((v for v in (d or {}).values() if isinstance(v, list)), []) if isinstance(d, dict) else []
        for c in lijst:
            ts = c.get("timestamp", c.get("t"))
            if ts is None:
                continue
            g = lambda *ks: next((float(c[k]) for k in ks if c.get(k) not in (None, "")), None)
            out[api.ms(ts)] = (g("open", "o"), g("high", "h"), g("low", "l"), g("close", "c"))
        t += 500 * stap
    _cand[key] = out
    return out


def fills_sinds(idx, vanaf):
    out, cursor = [], None
    for _ in range(40):
        st, tr, cursor = api.fills_pagina(idx, cursor)
        if st != 200 or not tr:
            break
        out += tr
        if min(api.ms(t["timestamp"]) for t in tr) < vanaf or not cursor:
            break
    rows = [r for r in (fill_rij(idx, t) for t in out) if r and r["ts"] >= vanaf]
    return pd.DataFrame(rows)


def stop_tijd(m, t, start):
    c = candles(m, "1h", start)
    eind = t.get("sluit") or NU
    grens = t["p1"] * (1 - TRADE_STOP) if t["dir"] > 0 else t["p1"] * (1 + TRADE_STOP)
    for h in range((t["open"] // UUR + 1) * UUR, eind, UUR):
        k = c.get(h)
        if k and ((t["dir"] > 0 and k[2] is not None and k[2] <= grens) or (t["dir"] < 0 and k[1] is not None and k[1] >= grens)):
            return h
    return None


def px_later(m, ts, start):
    k = candles(m, "15m", start).get(math.ceil((ts + UUR) / KW) * KW)
    return k[0] if k else None


def laatste_koers(m, start):
    c = candles(m, "15m", start)
    return c[max(c)][3] if c else None


def trades_prop(fl):
    """Plat -> plat met volledige omzet (bijkopen/afbouwen). Geeft (gesloten, open)."""
    st, uit = {}, []

    def nieuw(c, a, px, t):
        st[c] = {"dir": 1 if a > 0 else -1, "in_q": abs(a), "in_c": abs(a) * px, "uit_q": 0.0, "uit_c": 0.0,
                 "open": t, "px0": px, "max_c": abs(a) * px}
    for f in fl:
        c, s, a, px, t = f["coin"], f["start"], f["after"], f["px"], f["time"]
        if s == 0 and a != 0:
            nieuw(c, a, px, t)
            continue
        if c not in st:
            continue
        p = st[c]
        flip = a != 0 and a * s < 0
        if abs(a) > abs(s) and not flip:
            p["in_q"] += abs(a) - abs(s)
            p["in_c"] += (abs(a) - abs(s)) * px
            p["max_c"] = max(p["max_c"], abs(a) * px)
        else:
            q = abs(s) if (a == 0 or flip) else abs(s) - abs(a)
            p["uit_q"] += q
            p["uit_c"] += q * px
        if a == 0 or flip:
            uit.append({"coin": c, **p, "sluit": t})
            st.pop(c)
            if flip:
                nieuw(c, a, px, t)
    return uit, st


def typische_max(idx):
    """Mediaan van de grootste positie (USD) per trade over de laatste 60 dagen."""
    f = fills_sinds(idx, NU - 60 * DAG)
    if not len(f):
        return None
    fl, _ = reconstrueer(f, api.markten())
    dicht, _ = trades_prop(fl)
    v = [t["max_c"] for t in dicht]
    return float(np.median(v)) if v else None


def eur(t, k, mid=None):
    """PnL in € bij factor k (eigen = k x trader), incl. 0,16% per kant over de omzet."""
    gin = t["in_c"] / t["in_q"]
    gerealiseerd = t["dir"] * (t["uit_c"] - gin * t["uit_q"])
    q_open = t["in_q"] - t["uit_q"]
    open_deel = t["dir"] * (mid - gin) * q_open if (mid and q_open > 0) else 0.0
    return k * (gerealiseerd + open_deel) - KANT * k * (t["in_c"] + t["uit_c"])


def bereken(tr, sym, sym_inv, kraken, cap, start):
    f = fills_sinds(tr["idx"], tr["toegevoegd"])
    fl, _ = reconstrueer(f, sym) if len(f) else ([], 0)
    dicht, open_ = trades_prop(fl)
    stake = min(POT / tr["K"], cap)
    typ = tr.get("typ_max")
    rows = []
    for t in dicht + [{"coin": c, **t, "sluit": None} for c, t in open_.items()]:
        kr = basis(t["coin"]) in kraken or basis(t["coin"]) + "X" in kraken
        mid = None
        if t["sluit"] is None:
            m = sym_inv.get(t["coin"])
            mid = laatste_koers(m, start) if m is not None else None
        k = (stake / typ) if typ else stake / t["max_c"]
        if k * t["max_c"] > MAX_X * stake:
            k = MAX_X * stake / t["max_c"]
        p = eur(t, k, mid)
        guit = t["uit_c"] / t["uit_q"] if t["uit_q"] else (mid or t["px0"])
        eerste = stake * (t["dir"] * (guit / t["px0"] - 1) - 2 * KANT) if t["sluit"] is not None else 0.0
        rows.append({"coin": t["coin"], "dir": t["dir"], "open": t["open"], "sluit": t["sluit"], "kraken": kr,
                     "eigen_max_eur": round(k * t["max_c"], 2), "pnl_eur": p, "r": p / stake, "eerste_eur": eerste,
                     "status": "dicht" if t["sluit"] is not None else "open"})
    df = pd.DataFrame(rows)
    pnl = float(df.pnl_eur.sum()) if len(df) else 0.0
    eerste = float(df.eerste_eur.sum()) if len(df) else 0.0
    return {"df": df, "stake": stake, "pnl": pnl, "hand_pnl": eerste,
            "laatste_fill": int(f.ts.max()) if len(f) else 0}


def regels_check(tr, res):
    pot, df, r = POT + res["pnl"], res["df"], []
    if pot <= POT * (1 - REGELS["stop"]):
        r.append(f"potje {pot:.0f}")
    if pot <= tr["piek"] * (1 - REGELS["daling"]):
        r.append(f"daling vanaf piek {tr['piek']:.0f} -> {pot:.0f}")
    if NU - max(res["laatste_fill"], tr["toegevoegd"]) > REGELS["inactief_d"] * DAG:
        r.append(f"{REGELS['inactief_d']} dagen geen trade")
    if len(df):
        dicht = df[df.status == "dicht"]
        m30 = dicht[dicht.sluit >= NU - 30 * DAG]
        if len(m30) >= 15 and (m30.r * res["stake"]).sum() / POT < REGELS["vorm30"]:
            r.append("slechte vorm 30 d")
        if (df.status == "open").sum() > REGELS["max_open"]:
            r.append(f"> {REGELS['max_open']} posities open")
    st, acc = api.account(tr["idx"])
    if acc:
        av = float(acc.get("total_asset_value") or 0)
        if av < 1000 or (tr.get("av_start") and av < 0.5 * tr["av_start"]):
            r.append(f"account ${av:,.0f}")
    return r


def voeg_toe(state, lijst, n, vast=False):
    gebruikt = {t["idx"] for t in state["traders"]}
    for x in lijst:
        if sum(t["status"] == "actief" for t in state["traders"]) >= n:
            break
        if x["idx"] in gebruikt:
            continue
        st, acc = api.account(x["idx"])
        av = float(acc.get("total_asset_value") or 0) if acc else 0.0
        if av < 1000 and not vast:
            continue
        state["traders"].append({"idx": x["idx"], "l1": x["l1"], "K": x["K"], "toegevoegd": NU, "status": "actief",
                                 "pnl": 0.0, "hand_pnl": 0.0, "piek": POT, "av_start": av})
        if not vast:
            MELDINGEN.append(f"C: nieuw {kort(x['l1'])} (K={x['K']})")


def stop_trader(tr, reden):
    tr.update({"status": "gestopt", "reden": reden, "gestopt_op": NU})
    MELDINGEN.append(f"C: {kort(tr['l1'])} eruit ({reden}), resultaat €{tr['pnl']:+.2f}")


def main():
    prive, uit, kraken_p = sys.argv[1:4]
    groep = sys.argv[4] if len(sys.argv) > 4 else "C"
    cfg = GROEPEN[groep]
    uit = os.path.join(uit, cfg["map"]) if cfg["map"] else uit
    os.makedirs(uit, exist_ok=True)
    sel = json.load(open(f"{prive}/selectie.json"))
    lijst = sel["lijst"] if groep != "rnd20" else sel.get("random20", [])
    if groep == "top20":
        lijst = lijst[:20]
    kraken = set(json.load(open(kraken_p)))
    sym = api.markten()
    sym_inv = {v: k for k, v in sym.items()}
    pad = f"{prive}/{cfg['state']}"
    if os.path.exists(pad):
        state = json.load(open(pad))
    else:
        state = {"start": NU, "groep": groep, "gepauzeerd": False, "traders": []}
        voeg_toe(state, lijst, cfg["n"], cfg["vast"])
        state["kapitaal_start"] = max(1, len(state["traders"])) * POT
    state["cap"] = CAP
    alle = []
    if not state["gepauzeerd"]:
        for tr in state["traders"]:
            if tr["status"] != "actief":
                continue
            if not tr.get("typ_max"):
                try:
                    tr["typ_max"] = typische_max(tr["idx"])
                except Exception:  # noqa: BLE001
                    tr["typ_max"] = None
            try:
                res = bereken(tr, sym, sym_inv, kraken, state["cap"], state["start"])
            except Exception as exc:  # noqa: BLE001
                print("fout bij", kort(tr["l1"]), type(exc).__name__, flush=True)
                continue
            tr["pnl"], tr["hand_pnl"] = round(res["pnl"], 2), round(res["hand_pnl"], 2)
            tr["piek"] = max(tr["piek"], POT + tr["pnl"])
            df = res["df"]
            tr["trades_dicht"] = int((df.status == "dicht").sum()) if len(df) else 0
            tr["open"] = int((df.status == "open").sum()) if len(df) else 0
            tr["stake"] = round(res["stake"], 2)
            for row in df.to_dict("records"):
                alle.append({"trader": kort(tr["l1"]), **row})
            if not cfg["vast"]:
                reden = regels_check(tr, res)
                if reden:
                    stop_trader(tr, "; ".join(reden))
        if not cfg["vast"]:
            waarde = state["kapitaal_start"] + sum(t["pnl"] for t in state["traders"])
            if waarde <= PORT_STOP * state["kapitaal_start"]:
                for tr in state["traders"]:
                    if tr["status"] == "actief":
                        stop_trader(tr, "portefeuille-stop")
                state["gepauzeerd"] = True
                MELDINGEN.append(f"C: PORTEFEUILLE-STOP bij €{waarde:.0f}")
            else:
                voeg_toe(state, lijst, cfg["n"])
    json.dump(state, open(pad, "w"), indent=1)
    rapport(uit, state, alle, groep)


def rapport(d, state, alle, groep="C"):
    k = state["kapitaal_start"]
    pnl = sum(t["pnl"] for t in state["traders"])
    hand = sum(t.get("hand_pnl", 0) for t in state["traders"])
    dagen = (NU - state["start"]) / DAG
    naam = {"C": "Papier C (Lighter)", "top20": "Controle top 20 (Lighter)", "rnd20": "Controle random 20 (Lighter)"}[groep]
    md = [f"# {naam} — stand {pd.to_datetime(NU, unit='ms'):%Y-%m-%d %H:%M} UTC (dag {dagen:.1f})", "",
          f"Start €{k:.0f} → nu €{k + pnl:.2f} ({100 * pnl / k:+.1f}%), alleen 1e instap (oude methode): {100 * hand / k:+.1f}%"
          f"{' (GEPAUZEERD)' if state['gepauzeerd'] else ''}", "",
          "| Trader | K | Inzet | Status | Trades | Open | Resultaat | Alleen 1e instap | Reden |", "|---|---|---|---|---|---|---|---|---|"]
    for t in state["traders"]:
        md.append(f"| {kort(t['l1'])} | {t['K']} | €{t.get('stake', 0):.0f} | {t['status']} | {t.get('trades_dicht', 0)} | "
                  f"{t.get('open', 0)} | €{t['pnl']:+.2f} | €{t.get('hand_pnl', 0):+.2f} | {t.get('reden', '')} |")
    if alle:
        a = pd.DataFrame(alle)
        dicht = a[a.status == "dicht"]
        if len(dicht):
            md += ["", f"Gesloten trades: {len(dicht)}, winst-% {100 * (dicht.r > 0).mean():.0f}%, "
                   f"op Kraken te doen: {100 * dicht.kraken.mean():.0f}%"]
        a.drop(columns=[c for c in ("idx",) if c in a.columns]).to_csv(f"{d}/trades.csv", index=False)
    if MELDINGEN:
        md += ["", "## Gebeurtenissen deze run", *[f"- {m}" for m in MELDINGEN]]
    open(f"{d}/rapport.md", "w").write("\n".join(md) + "\n")
    hp = f"{d}/geschiedenis.csv"
    rij = pd.DataFrame([{"ts": NU, "waarde": round(k + pnl, 2), "pct": round(100 * pnl / k, 2),
                         "hand_pct": round(100 * hand / k, 2), "actief": sum(t["status"] == "actief" for t in state["traders"])}])
    (pd.concat([pd.read_csv(hp), rij]) if os.path.exists(hp) else rij).to_csv(hp, index=False)
    with open(f"{d}/gebeurtenissen.log", "a") as fh:
        for m in MELDINGEN:
            fh.write(f"{pd.to_datetime(NU, unit='ms'):%Y-%m-%d %H:%M} {m}\n")
    print("\n".join(md), flush=True)
    topic = os.environ.get("NTFY_TOPIC")
    dagelijks = pd.to_datetime(NU, unit="ms").hour == 6
    if groep == "C" and topic and (MELDINGEN or dagelijks):
        g = pd.read_csv(hp)
        oud = g[g.ts <= NU - 23 * UUR].tail(1)
        dag = (k + pnl) - float(oud.waarde.iloc[0]) if len(oud) else 0.0
        bericht = [f"C (Lighter): €{k + pnl:.0f} ({100 * pnl / k:+.1f}% totaal, {dag:+.2f} € 24u), alleen 1e instap {100 * hand / k:+.1f}%"]
        act = [t for t in state["traders"] if t["status"] == "actief"]
        if act:
            b, w = max(act, key=lambda t: t["pnl"]), min(act, key=lambda t: t["pnl"])
            bericht.append(f"beste {kort(b['l1'])} €{b['pnl']:+.1f}, slechtste {kort(w['l1'])} €{w['pnl']:+.1f}")
        if MELDINGEN:
            bericht += ["Wijzigingen:"] + MELDINGEN
        try:
            requests.post(f"https://ntfy.sh/{topic}", data="\n".join(bericht).encode(),
                          headers={"Title": "Kopieerbot papier C (Lighter)"}, timeout=20)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
