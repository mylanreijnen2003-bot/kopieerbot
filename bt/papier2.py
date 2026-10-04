"""Papier v2: twee versies (A max rendement, B laag risico) op papier volgen, elke 6 uur.
Regels (vooraf vastgelegd 4 okt 14:45):
- €100 papierpotje per trader; inzet per trade = min(100 / K, 5% van het startkapitaal van de versie).
- Alleen posities die de trader na toevoegen vanuit plat opent; alleen munten die als perp op Kraken staan.
- Bot-basis: instap = eerste fill van de trader, uitstap = zijn gem. uitstapprijs, 0,32% kosten per rondje.
- Stop per trade: -10% tegen de instap (uurcandles) -> eruit op -10%.
- Trader eruit bij: potje <= 100*(1-stop) | potje <= piek*(1-daling) | geen fill in X dagen | laatste 30 d (>= 15 trades)
  onder grens | mediaan houdtijd laatste 20 trades < 1 u of > 50 trades/week | > 5 posities open of > 30% niet-Kraken
  (laatste 20) | accountwaarde < $1000 of < 50% van bij toevoegen. Vervanger: volgende uit de nieuwste selectie, vers €100.
- Herbalans: A elke 14 d (niet in top 16 -> eruit), B elke 30 d (niet in top 10 -> eruit).
- Portefeuille-stop: waarde <= 85% van start -> alles stil.
Gebruik: python -m bt.papier2 <map met selectie.json/kraken.json; state.json wordt hier bijgehouden>
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

from bot import hl
from bt.engine import to_base
from bt.p2_common import DAG, KOSTEN, UUR, trades_open

NU = int(time.time() * 1000)
REGELS = {"A": {"stop": 0.20, "daling": 0.25, "inactief_d": 7, "vorm30": -0.10, "herbalans_d": 14, "top_houden": 16},
          "B": {"stop": 0.15, "daling": 0.20, "inactief_d": 14, "vorm30": -0.05, "herbalans_d": 30, "top_houden": 10},
          "C": None, "D": None}
NAMEN = {"A": "A — max rendement", "B": "B — laag risico", "C": "C — controle: recent top 30, geen regels",
         "D": "D — controle: willekeurig 30, geen regels"}
CAP_CONTROLE = 40.0
POT = 100.0
TRADE_STOP = 0.10
PORT_STOP = 0.85
_c1, _c15 = {}, {}
MELDINGEN = []


def kort(a):
    return a[:6] + "…" + a[-4:]


def candles(cache, coin, start, iv):
    if coin not in cache:
        cache[coin] = hl.candles(coin, start - DAG, NU + UUR, iv)
    return cache[coin]


def stop_tijd(coin, t, start):
    """Eerste uur waarin de positie -10% tegen ging (na het instapuur), of None."""
    c = candles(_c1, coin, start, "1h")
    eind = t.get("sluit", NU)
    h0 = (t["open"] // UUR + 1) * UUR
    grens = t["p1"] * (1 - TRADE_STOP) if t["dir"] > 0 else t["p1"] * (1 + TRADE_STOP)
    for h in range(h0, eind, UUR):
        k = c.get(h)
        if not k:
            continue
        if (t["dir"] > 0 and k[2] <= grens) or (t["dir"] < 0 and k[1] >= grens):
            return h
    return None


def px_later(coin, ts, start):
    c = candles(_c15, coin, start, "15m")
    k = c.get(math.ceil((ts + UUR) / (15 * 60_000)) * 15 * 60_000)
    return k[0] if k else None


def bereken(tr, mids, kraken, cap, start):
    """Herberekent één actieve trader vanaf toevoegen. Geeft dict met pnl, open, trades."""
    fl = [f for f in hl.fills(tr["address"], tr["toegevoegd"], NU) if f["kind"] == "perp"]
    dicht, open_ = trades_open(fl, tr["toegevoegd"])
    stake = min(POT / tr["K"], cap)
    rows = []
    for t in dicht:
        kr = to_base(t["coin"]) in kraken
        r = t["dir"] * (t["po"] / t["p1"] - 1) - KOSTEN
        gestopt = False
        if kr:
            h = stop_tijd(t["coin"], t, start)
            if h is not None:
                r, gestopt = -TRADE_STOP - KOSTEN, True
        a, b = px_later(t["coin"], t["open"], start), px_later(t["coin"], t["sluit"], start)
        hand = t["dir"] * (b / a - 1) - KOSTEN if (kr and a and b and t["sluit"] + UUR < NU) else None
        rows.append({**t, "kraken": kr, "r": r if kr else 0.0, "stop": gestopt, "hand_r": hand, "status": "dicht"})
    for c, t in open_.items():
        kr = to_base(c) in kraken
        mid = mids.get(c)
        r = t["dir"] * (mid / t["p1"] - 1) - KOSTEN if (kr and mid) else 0.0
        gestopt = False
        if kr and stop_tijd(c, t, start) is not None:
            r, gestopt = -TRADE_STOP - KOSTEN, True
        rows.append({"coin": c, **t, "sluit": None, "kraken": kr, "r": r if kr else 0.0, "stop": gestopt,
                     "hand_r": None, "status": "open"})
    df = pd.DataFrame(rows)
    pnl = float((df.r * stake).sum()) if len(df) else 0.0
    hand = float((df.hand_r.dropna() * stake).sum()) if len(df) and "hand_r" in df else 0.0
    return {"fills": fl, "df": df, "stake": stake, "pnl": pnl, "hand_pnl": hand,
            "laatste_fill": max([f["time"] for f in fl], default=0)}


def regels_check(v, tr, res, regels):
    pot = POT + res["pnl"]
    df = res["df"]
    dicht = df[df.status == "dicht"].sort_values("sluit") if len(df) else df
    r = []
    if pot <= POT * (1 - regels["stop"]):
        r.append(f"potje {pot:.0f}")
    if pot <= tr["piek"] * (1 - regels["daling"]):
        r.append(f"daling vanaf piek {tr['piek']:.0f} -> {pot:.0f}")
    ref = max(res["laatste_fill"], tr["toegevoegd"])
    if NU - ref > regels["inactief_d"] * DAG:
        r.append(f"{regels['inactief_d']} dagen geen trade")
    if len(dicht):
        m30 = dicht[dicht.sluit >= NU - 30 * DAG]
        if len(m30) >= 15 and (m30.r * res["stake"]).sum() / POT < regels["vorm30"]:
            r.append("slechte vorm 30 d")
        l20 = dicht.tail(20)
        if len(l20) >= 10 and ((l20.sluit - l20.open) / UUR).median() < 1:
            r.append("houdtijd < 1 u")
        if len(l20) >= 10 and (~l20.kraken).mean() > 0.30:
            r.append("> 30% niet op Kraken")
        n14 = (dicht.open >= NU - 14 * DAG).sum()
        if n14 / 2 > 50:
            r.append("> 50 trades/week")
    if len(df) and (df.status == "open").sum() > 5:
        r.append("> 5 posities open")
    try:
        av = float(hl.info({"type": "clearinghouseState", "user": tr["address"]}).get("marginSummary", {}).get("accountValue", 0))
        if av < 1000 or (tr.get("av_start") and av < 0.5 * tr["av_start"]):
            r.append(f"account ${av:,.0f}")
    except Exception:  # noqa: BLE001
        pass
    return r


def lijst(sel, v):
    return [x["address"] for x in sel[v]["lijst"]], {x["address"]: max(1, int(x["K"])) for x in sel[v]["lijst"]}


def voeg_toe(vs, sel, v, n):
    order, ks = lijst(sel, v)
    gebruikt = {t["address"] for t in vs["traders"]}
    for a in order:
        if sum(t["status"] == "actief" for t in vs["traders"]) >= n:
            break
        if a in gebruikt:
            continue
        try:
            av = float(hl.info({"type": "clearinghouseState", "user": a}).get("marginSummary", {}).get("accountValue", 0))
        except Exception:  # noqa: BLE001
            av = 0.0
        vs["traders"].append({"address": a, "K": ks[a], "toegevoegd": NU, "status": "actief", "pnl": 0.0,
                              "hand_pnl": 0.0, "piek": POT, "av_start": av})
        if v in ("A", "B"):
            MELDINGEN.append(f"{v}: nieuw {kort(a)} (K={ks[a]})")


def stop_trader(v, tr, reden):
    tr.update({"status": "gestopt", "reden": reden, "gestopt_op": NU})
    MELDINGEN.append(f"{v}: {kort(tr['address'])} eruit ({reden}), resultaat €{tr['pnl']:+.2f}")


def main():
    d = sys.argv[1]
    sel = json.load(open(f"{d}/selectie.json"))
    kraken = set(json.load(open(f"{d}/kraken.json")))
    if os.path.exists(f"{d}/controle.json"):
        sel.update(json.load(open(f"{d}/controle.json")))
    pad = f"{d}/state.json"
    mids = {k: float(v) for k, v in hl.info({"type": "allMids"}, weight=2).items()}
    if os.path.exists(pad):
        state = json.load(open(pad))
    else:
        state = {"start": NU, "btc_start": mids.get("BTC"), "versies": {}}
        for v in ["A", "B"]:
            n = sel[v]["n_actief"]
            state["versies"][v] = {"kapitaal_start": n * POT, "cap": 0.05 * n * POT, "gepauzeerd": False,
                                   "laatste_herbalans": NU, "traders": []}
            voeg_toe(state["versies"][v], sel, v, n)
    for v in ["C", "D"]:
        if v in sel and v not in state["versies"]:
            n = sel[v]["n_actief"]
            state["versies"][v] = {"kapitaal_start": n * POT, "cap": CAP_CONTROLE, "gepauzeerd": False,
                                   "laatste_herbalans": NU, "traders": [], "start": NU}
            voeg_toe(state["versies"][v], sel, v, n)
            MELDINGEN.append(f"{v}: controlegroep gestart met {n} traders")
    alle_trades = []
    for v, vs in state["versies"].items():
        regels, n = REGELS[v], sel[v]["n_actief"]
        if vs["gepauzeerd"]:
            continue
        if regels is None:
            for tr in vs["traders"]:
                try:
                    res = bereken(tr, mids, kraken, vs["cap"], state["start"])
                except Exception as exc:  # noqa: BLE001
                    print(v, kort(tr["address"]), "fout", exc, flush=True)
                    continue
                tr["pnl"], tr["hand_pnl"] = round(res["pnl"], 2), round(res["hand_pnl"], 2)
                tr["trades_dicht"] = int((res["df"].status == "dicht").sum()) if len(res["df"]) else 0
                tr["open"] = int((res["df"].status == "open").sum()) if len(res["df"]) else 0
                tr["stake"] = round(res["stake"], 2)
                for _, row in res["df"].iterrows():
                    alle_trades.append({"versie": v, "trader": kort(tr["address"]), **row.to_dict()})
            continue
        if NU - vs["laatste_herbalans"] >= regels["herbalans_d"] * DAG and sel["gemaakt"] > vs["laatste_herbalans"]:
            top = set(lijst(sel, v)[0][:regels["top_houden"]])
            for tr in vs["traders"]:
                if tr["status"] == "actief" and tr["address"] not in top:
                    stop_trader(v, tr, "herbalans: niet meer in top")
            vs["laatste_herbalans"] = NU
        for tr in vs["traders"]:
            if tr["status"] != "actief":
                continue
            try:
                res = bereken(tr, mids, kraken, vs["cap"], state["start"])
            except Exception as exc:  # noqa: BLE001
                print(v, kort(tr["address"]), "fout", exc, flush=True)
                continue
            tr["pnl"], tr["hand_pnl"] = round(res["pnl"], 2), round(res["hand_pnl"], 2)
            tr["piek"] = max(tr["piek"], POT + tr["pnl"])
            tr["trades_dicht"] = int((res["df"].status == "dicht").sum()) if len(res["df"]) else 0
            tr["open"] = int((res["df"].status == "open").sum()) if len(res["df"]) else 0
            tr["stake"] = round(res["stake"], 2)
            for _, row in res["df"].iterrows():
                alle_trades.append({"versie": v, "trader": kort(tr["address"]), **row.to_dict()})
            reden = regels_check(v, tr, res, regels)
            if reden:
                stop_trader(v, tr, "; ".join(reden))
        waarde = vs["kapitaal_start"] + sum(t["pnl"] for t in vs["traders"])
        if waarde <= PORT_STOP * vs["kapitaal_start"]:
            for tr in vs["traders"]:
                if tr["status"] == "actief":
                    stop_trader(v, tr, "portefeuille-stop")
            vs["gepauzeerd"] = True
            MELDINGEN.append(f"{v}: PORTEFEUILLE-STOP bij €{waarde:.0f}")
        else:
            voeg_toe(vs, sel, v, n)
    json.dump(state, open(pad, "w"), indent=1)
    rapport(d, state, mids, alle_trades)


def rapport(d, state, mids, alle_trades):
    btc = 100 * (mids.get("BTC", 0) / state["btc_start"] - 1) if state.get("btc_start") else 0.0
    dagen = (NU - state["start"]) / DAG
    regels_md = [f"# Papier v2 — stand {pd.to_datetime(NU, unit='ms'):%Y-%m-%d %H:%M} UTC (dag {dagen:.1f})", "",
                 f"BTC sinds start: {btc:+.1f}%", ""]
    hist = []
    for v, vs in state["versies"].items():
        pnl = sum(t["pnl"] for t in vs["traders"])
        hand = sum(t.get("hand_pnl", 0) for t in vs["traders"])
        k = vs["kapitaal_start"]
        naam = NAMEN[v]
        regels_md += [f"## Versie {naam}{' (GEPAUZEERD)' if vs['gepauzeerd'] else ''}",
                      f"Start €{k:.0f} → nu €{k + pnl:.2f} ({100 * pnl / k:+.1f}%), met de hand 1 u later: {100 * hand / k:+.1f}%", "",
                      "| Trader | K | Inzet | Status | Trades | Open | Resultaat | Met de hand | Reden |", "|---|---|---|---|---|---|---|---|---|"]
        for t in vs["traders"]:
            regels_md.append(f"| {kort(t['address'])} | {t['K']} | €{t.get('stake', 0):.0f} | {t['status']} | {t.get('trades_dicht', 0)} | "
                             f"{t.get('open', 0)} | €{t['pnl']:+.2f} | €{t.get('hand_pnl', 0):+.2f} | {t.get('reden', '')} |")
        regels_md.append("")
        hist.append({"ts": NU, "versie": v, "waarde": round(k + pnl, 2), "pct": round(100 * pnl / k, 2),
                     "hand_pct": round(100 * hand / k, 2), "btc_pct": round(btc, 2),
                     "actief": sum(t["status"] == "actief" for t in vs["traders"])})
    if MELDINGEN:
        regels_md += ["## Gebeurtenissen deze run", *[f"- {m}" for m in MELDINGEN], ""]
    open(f"{d}/rapport.md", "w").write("\n".join(regels_md))
    hp = f"{d}/geschiedenis.csv"
    oud = [pd.read_csv(hp)] if os.path.exists(hp) else []
    pd.concat(oud + [pd.DataFrame(hist)]).to_csv(hp, index=False)
    if alle_trades:
        pd.DataFrame(alle_trades).to_csv(f"{d}/trades.csv", index=False)
    with open(f"{d}/gebeurtenissen.log", "a") as fh:
        for m in MELDINGEN:
            fh.write(f"{pd.to_datetime(NU, unit='ms'):%Y-%m-%d %H:%M} {m}\n")
    print("\n".join(regels_md))
    topic = os.environ.get("NTFY_TOPIC")
    dagelijks = pd.to_datetime(NU, unit="ms").hour == 6
    if topic and (MELDINGEN or dagelijks):
        try:
            requests.post(f"https://ntfy.sh/{topic}", data=dagbericht(d, state, hist, alle_trades, btc, dagelijks).encode(),
                          headers={"Title": "Kopieerbot papier: dagupdate" if dagelijks else "Kopieerbot papier: wijziging"},
                          timeout=20)
        except Exception:  # noqa: BLE001
            pass


def dagbericht(d, state, hist, alle_trades, btc, dagelijks):
    """Kort bericht: per versie waarde, vandaag, trades laatste 24 u, beste/slechtste trader; plus wijzigingen."""
    regels = []
    tr = pd.DataFrame(alle_trades)
    g = pd.read_csv(f"{d}/geschiedenis.csv") if os.path.exists(f"{d}/geschiedenis.csv") else pd.DataFrame()
    for h in hist:
        v, vs = h["versie"], state["versies"][h["versie"]]
        oud = g[(g.versie == v) & (g.ts <= NU - 23 * UUR)].tail(1) if len(g) else g
        dag = h["waarde"] - float(oud.waarde.iloc[0]) if len(oud) else 0.0
        regel = f"{v}: €{h['waarde']:.0f} ({h['pct']:+.1f}% totaal, {dag:+.2f} € 24u)"
        if len(tr) and "sluit" in tr:
            x = tr[(tr.versie == v) & (tr.status == "dicht") & (tr.sluit >= NU - 24 * UUR) & (tr.kraken)]
            if len(x):
                regel += f" | {len(x)} trades, {100 * (x.r > 0).mean():.0f}% winst"
            o = tr[(tr.versie == v) & (tr.status == "open") & (tr.kraken)]
            regel += f" | {len(o)} open"
        act = [t for t in vs["traders"] if t["status"] == "actief"]
        if act:
            b = max(act, key=lambda t: t["pnl"])
            w = min(act, key=lambda t: t["pnl"])
            regel += f" | beste {kort(b['address'])} €{b['pnl']:+.1f}, slechtste {kort(w['address'])} €{w['pnl']:+.1f}"
        regels.append(regel)
    regels.append(f"BTC sinds start {btc:+.1f}%")
    if MELDINGEN:
        regels += ["Wijzigingen:"] + MELDINGEN
    return "\n".join(regels)


if __name__ == "__main__":
    main()
