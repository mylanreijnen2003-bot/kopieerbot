"""Papier C (Lighter): top 5 Lighter-traders op papier volgen, elke 6 uur. Zelfde opzet als bt/papier2.py.

Regels (vooraf vastgelegd 4 okt 2026):
- €100 papierpotje per trader; inzet per trade = min(100 / K, 5% van het startkapitaal). K = p90 posities tegelijk.
- Alleen posities die de trader ná toevoegen vanuit plat opent (Lighter geeft de positie vóór elke fill).
- Alle munten tellen mee (ook alleen-Lighter); per trade gelabeld of hij op Kraken kan.
- Bot-basis: instap = eerste fill van de trader, uitstap = zijn gem. uitstapprijs, 0,32% kosten per rondje.
  Daarnaast 'met de hand': in- en uitstap 1 uur later (15m-candles), zelfde kosten.
- Stop per trade: -10% tegen de instap (uurcandles) -> eruit op -10%.
- Trader eruit bij: potje <= €80 | potje <= piek x 0,75 | 7 dagen geen fill | laatste 30 d (>= 15 trades) < -10% |
  > 10 posities open | accountwaarde < $1000 of < 50% van bij toevoegen. Vervanger: volgende uit de selectie, vers €100.
- Portefeuille-stop: waarde <= 85% van start -> alles stil.
Gebruik: python -m lighter.papier <privé-map met selectie.json/state.json> <openbare map> <kraken.json>
Openbaar alleen afgekorte adressen; state.json (met account-indexen) blijft privé en wordt versleuteld opgeslagen.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

import pandas as pd
import requests

from bt.p2_common import KOSTEN, trades_open
from lighter import api
from lighter.scan import fill_rij
from lighter.selectie import basis, kort, reconstrueer

NU = int(time.time() * 1000)
DAG, UUR, KW = api.DAG, 3_600_000, 15 * 60_000
POT, N_ACTIEF, TRADE_STOP, PORT_STOP = 100.0, 5, 0.10, 0.85
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


def bereken(tr, sym, sym_inv, kraken, cap, start):
    f = fills_sinds(tr["idx"], tr["toegevoegd"])
    fl, _ = reconstrueer(f, sym) if len(f) else ([], 0)
    dicht, open_ = trades_open(fl, tr["toegevoegd"])
    stake = min(POT / tr["K"], cap)
    rows = []
    for t in dicht:
        m = sym_inv.get(t["coin"])
        kr = basis(t["coin"]) in kraken or basis(t["coin"]) + "X" in kraken
        r, gestopt = t["dir"] * (t["po"] / t["p1"] - 1) - KOSTEN, False
        if m is not None and stop_tijd(m, t, start) is not None:
            r, gestopt = -TRADE_STOP - KOSTEN, True
        a = px_later(m, t["open"], start) if m is not None else None
        b = px_later(m, t["sluit"], start) if m is not None else None
        hand = t["dir"] * (b / a - 1) - KOSTEN if (a and b and t["sluit"] + UUR < NU) else None
        rows.append({**t, "kraken": kr, "r": r, "stop": gestopt, "hand_r": hand, "status": "dicht"})
    for c, t in open_.items():
        m = sym_inv.get(c)
        kr = basis(c) in kraken or basis(c) + "X" in kraken
        mid = laatste_koers(m, start) if m is not None else None
        r = t["dir"] * (mid / t["p1"] - 1) - KOSTEN if mid else 0.0
        gestopt = False
        if m is not None and stop_tijd(m, {**t, "sluit": None}, start) is not None:
            r, gestopt = -TRADE_STOP - KOSTEN, True
        rows.append({"coin": c, **t, "sluit": None, "kraken": kr, "r": r, "stop": gestopt, "hand_r": None, "status": "open"})
    df = pd.DataFrame(rows)
    pnl = float((df.r * stake).sum()) if len(df) else 0.0
    hand = float((df.hand_r.dropna() * stake).sum()) if len(df) else 0.0
    return {"df": df, "stake": stake, "pnl": pnl, "hand_pnl": hand,
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


def voeg_toe(state, sel):
    gebruikt = {t["idx"] for t in state["traders"]}
    for x in sel["lijst"]:
        if sum(t["status"] == "actief" for t in state["traders"]) >= N_ACTIEF:
            break
        if x["idx"] in gebruikt:
            continue
        st, acc = api.account(x["idx"])
        av = float(acc.get("total_asset_value") or 0) if acc else 0.0
        if av < 1000:
            continue
        state["traders"].append({"idx": x["idx"], "l1": x["l1"], "K": x["K"], "toegevoegd": NU, "status": "actief",
                                 "pnl": 0.0, "hand_pnl": 0.0, "piek": POT, "av_start": av})
        MELDINGEN.append(f"C: nieuw {kort(x['l1'])} (K={x['K']})")


def stop_trader(tr, reden):
    tr.update({"status": "gestopt", "reden": reden, "gestopt_op": NU})
    MELDINGEN.append(f"C: {kort(tr['l1'])} eruit ({reden}), resultaat €{tr['pnl']:+.2f}")


def main():
    prive, uit, kraken_p = sys.argv[1:4]
    os.makedirs(uit, exist_ok=True)
    sel = json.load(open(f"{prive}/selectie.json"))
    kraken = set(json.load(open(kraken_p)))
    sym = api.markten()
    sym_inv = {v: k for k, v in sym.items()}
    pad = f"{prive}/state.json"
    if os.path.exists(pad):
        state = json.load(open(pad))
    else:
        state = {"start": NU, "kapitaal_start": N_ACTIEF * POT, "cap": 0.05 * N_ACTIEF * POT, "gepauzeerd": False,
                 "traders": []}
        voeg_toe(state, sel)
    alle = []
    if not state["gepauzeerd"]:
        for tr in state["traders"]:
            if tr["status"] != "actief":
                continue
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
            reden = regels_check(tr, res)
            if reden:
                stop_trader(tr, "; ".join(reden))
        waarde = state["kapitaal_start"] + sum(t["pnl"] for t in state["traders"])
        if waarde <= PORT_STOP * state["kapitaal_start"]:
            for tr in state["traders"]:
                if tr["status"] == "actief":
                    stop_trader(tr, "portefeuille-stop")
            state["gepauzeerd"] = True
            MELDINGEN.append(f"C: PORTEFEUILLE-STOP bij €{waarde:.0f}")
        else:
            voeg_toe(state, sel)
    json.dump(state, open(pad, "w"), indent=1)
    rapport(uit, state, alle)


def rapport(d, state, alle):
    k = state["kapitaal_start"]
    pnl = sum(t["pnl"] for t in state["traders"])
    hand = sum(t.get("hand_pnl", 0) for t in state["traders"])
    dagen = (NU - state["start"]) / DAG
    md = [f"# Papier C (Lighter) — stand {pd.to_datetime(NU, unit='ms'):%Y-%m-%d %H:%M} UTC (dag {dagen:.1f})", "",
          f"Start €{k:.0f} → nu €{k + pnl:.2f} ({100 * pnl / k:+.1f}%), met de hand 1 u later: {100 * hand / k:+.1f}%"
          f"{' (GEPAUZEERD)' if state['gepauzeerd'] else ''}", "",
          "| Trader | K | Inzet | Status | Trades | Open | Resultaat | Met de hand | Reden |", "|---|---|---|---|---|---|---|---|---|"]
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
    if topic and (MELDINGEN or dagelijks):
        g = pd.read_csv(hp)
        oud = g[g.ts <= NU - 23 * UUR].tail(1)
        dag = (k + pnl) - float(oud.waarde.iloc[0]) if len(oud) else 0.0
        bericht = [f"C (Lighter): €{k + pnl:.0f} ({100 * pnl / k:+.1f}% totaal, {dag:+.2f} € 24u), met de hand {100 * hand / k:+.1f}%"]
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
