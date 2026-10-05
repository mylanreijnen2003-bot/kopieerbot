"""Papier-HL: traders proportioneel kopiëren op Hyperliquid zelf (zoals de bot het live zou doen), op papier.
Elke run (elke 6 u via GitHub Actions): nieuwe fills van elke trader ophalen en per fill nadoen:
  doelpositie = positie trader na fill x (jouw potje / zijn equity op dat moment)
  uitvoering  = zijn prijs + 2 bps slippage (gemeten 0-1 bps na 0,3 s) + 4,5 bps taker-fee
  minimale order $10: kleinere verschillen worden opgespaard tot ze >= $10 zijn (sluiten mag altijd)
  schone start: posities die de trader al had bij de start worden overgeslagen tot hij plat is
  hefboom-plafond: totale positie max 10x je potje; stop per trader: potje -25% -> alles dicht, trader op pauze
  funding: elk uur over open posities (long betaalt bij positieve rate), vanaf 5-10-2026 ±18:30 UTC
Gebruik: python -m kopietest.papierhl <map>   (map bevat selectie.json; state.json/ geschiedenis.csv/ trades.csv/ rapport.md)
"""
from __future__ import annotations

import json
import os
import sys
import time

import pandas as pd
import requests

from bot import hl

MAP = sys.argv[1]
NU = int(time.time() * 1000)
SLIP = 0.0002
FEE = 0.00045
MIN_ORDER = 10.0
MAX_HEFBOOM = 10.0
STOP = 0.75


_FUND = {}


def fund_rates(c):
    if c not in _FUND:
        try:
            _FUND[c] = hl.funding(c, NU - 3 * 86_400_000, NU)
        except Exception:  # noqa: BLE001
            _FUND[c] = {}
    return _FUND[c]


def accrue(s, tot, mids):
    """Funding over de open posities van s['fund_t'] tot `tot` (per heel uur). Long betaalt bij positieve rate."""
    van = s.setdefault("fund_t", NU)
    if tot <= van:
        return
    for c, (q, avg) in s["pos"].items():
        px = mids.get(c, avg)
        for h, rate in fund_rates(c).items():
            if van < h <= tot:
                pay = -q * px * rate
                s["kas"] += pay
                s["funding"] = s.get("funding", 0.0) + pay
    s["fund_t"] = tot


def laad(naam, standaard):
    p = f"{MAP}/{naam}"
    return json.load(open(p)) if os.path.exists(p) else standaard


def kort(a):
    return f"{a[:6]}…{a[-4:]}"


def main():
    sel = laad("selectie.json", None)
    st = laad("state.json", {"start": NU, "traders": {}})
    mids = {k: float(v) for k, v in hl.info({"type": "allMids"}, weight=2).items()}
    nieuw = []
    for t in sel["traders"]:
        a = t["address"]
        if a not in st["traders"]:
            s = st["traders"][a] = {"potje": sel["potje"], "start_potje": sel["potje"], "kas": 0.0, "pos": {}, "skip": {},
                                    "laatste": NU, "actief": True, "fees": 0.0, "trades": 0, "overgeslagen_klein": 0,
                                    "geplafonneerd": 0, "start_ms": NU, "fund_t": NU, "funding": 0.0}
            # schone start: wat de trader nu al open heeft, overslaan tot hij plat is
            stt = hl.info({"type": "clearinghouseState", "user": a}, weight=2) or {}
            for p in stt.get("assetPositions") or []:
                if float(p["position"]["szi"]) != 0:
                    s["skip"][p["position"]["coin"]] = True
            continue
        s = st["traders"][a]
        try:
            fl = hl.fills(a, s["laatste"] + 1, NU)
            pts, _ = hl.av_points(a)
        except Exception as e:  # noqa: BLE001
            print("fout", kort(a), e)
            continue
        for f in fl:
            if f["kind"] == "spot":
                continue
            accrue(s, f["time"], mids)
            c = f["coin"]
            if s["skip"].get(c):                         # positie van vóór de start: pas meedoen als hij plat is
                if f["after"] == 0:
                    s["skip"].pop(c)
                continue
            if not s["actief"]:
                continue
            E = hl.av_at(pts, f["time"]) or 0
            q, avg = s["pos"].get(c, [0.0, 0.0])
            eigen = s["potje"] + s["kas"] + sum(p[0] * (mids.get(k, p[1]) - p[1]) for k, p in s["pos"].items())
            doel = 0.0 if f["after"] == 0 or E <= 0 else f["after"] * max(eigen, 0) / E
            # hefboom-plafond
            bruto = sum(abs(p[0]) * mids.get(k, p[1]) for k, p in s["pos"].items() if k != c) + abs(doel) * f["px"]
            if bruto > MAX_HEFBOOM * max(eigen, 1):
                rest = max(0.0, MAX_HEFBOOM * max(eigen, 1) - (bruto - abs(doel) * f["px"]))
                doel = (1 if doel > 0 else -1) * rest / f["px"]
                s["geplafonneerd"] += 1
            d = doel - q
            if d == 0:
                continue
            if abs(d) * f["px"] < MIN_ORDER and doel != 0:
                s["overgeslagen_klein"] += 1
                continue
            kant = 1 if d > 0 else -1
            px = f["px"] * (1 + kant * SLIP)
            fee = abs(d) * px * FEE
            pnl = 0.0
            if q != 0 and q * d < 0:                       # afbouwen
                dicht = min(abs(d), abs(q))
                pnl = dicht * (px - avg) * (1 if q > 0 else -1)
            nq = q + d
            if abs(nq) < 1e-12:
                nq = 0.0
            if nq != 0 and (q == 0 or q * nq < 0):
                avg = px
            elif abs(nq) > abs(q):
                avg = (abs(q) * avg + (abs(nq) - abs(q)) * px) / abs(nq)
            s["kas"] += pnl - fee
            s["fees"] += fee
            if nq == 0:
                s["pos"].pop(c, None)
                s["trades"] += 1
            else:
                s["pos"][c] = [nq, avg]
            nieuw.append({"tijd": f["time"], "trader": kort(a), "munt": c, "hoeveelheid": round(d, 8), "prijs": round(px, 6),
                          "fee": round(fee, 4), "winst": round(pnl, 4), "positie_na": round(nq, 8), "zijn_prijs": f["px"]})
        if fl:
            s["laatste"] = max(f["time"] for f in fl)
        accrue(s, NU, mids)
        # stop per trader
        eq = s["potje"] + s["kas"] + sum(p[0] * (mids.get(k, p[1]) - p[1]) for k, p in s["pos"].items())
        s["equity"] = eq
        if s["actief"] and eq < STOP * s["start_potje"]:
            for k, p in list(s["pos"].items()):
                px = mids.get(k, p[1])
                s["kas"] += p[0] * (px - p[1]) - abs(p[0]) * px * FEE
            s["pos"] = {}
            s["actief"] = False
            s["equity"] = s["potje"] + s["kas"]
            nieuw.append({"tijd": NU, "trader": kort(a), "munt": "STOP", "hoeveelheid": 0, "prijs": 0, "fee": 0, "winst": 0, "positie_na": 0})
        # vergelijking: accountrendement van de trader zelf sinds de start
        pnl_pts = []
        try:
            per = dict(hl.info({"type": "portfolio", "user": a}, weight=20))
            for k in ("perpMonth",):                    # één venster: pnl begint per venster bij 0
                pnl_pts += [(int(x), float(y)) for x, y in per.get(k, {}).get("pnlHistory", [])]
            pnl_pts = sorted(set(pnl_pts))
            E0 = hl.av_at(pts, s["start_ms"]) or 0
            if pnl_pts and E0 > 0:
                s["trader_rend_pct"] = round(100 * (hl.av_at(pnl_pts, NU) - hl.av_at(pnl_pts, s["start_ms"])) / E0, 2)
        except Exception:  # noqa: BLE001
            pass
        s["open_posities"] = len(s["pos"])
    st["bijgewerkt"] = NU
    json.dump(st, open(f"{MAP}/state.json", "w"), indent=1)
    if nieuw:
        pd.DataFrame(nieuw).to_csv(f"{MAP}/trades.csv", mode="a", header=not os.path.exists(f"{MAP}/trades.csv"), index=False)
    rij = {"tijd": pd.Timestamp(NU, unit="ms").strftime("%Y-%m-%d %H:%M")}
    tot, start_tot = 0.0, 0.0
    for a, s in st["traders"].items():
        rij[kort(a)] = round(s.get("equity", s["potje"]), 2)
        tot += s.get("equity", s["potje"])
        start_tot += s["start_potje"]
    rij["totaal"] = round(tot, 2)
    tot_f = sum(s.get("funding", 0.0) for s in st["traders"].values())
    rij["funding"] = round(tot_f, 2)
    gp = f"{MAP}/geschiedenis.csv"   # kolommen kunnen wijzigen (traders erbij): alles inlezen en herschrijven
    oud = [pd.read_csv(gp, on_bad_lines="skip")] if os.path.exists(gp) else []
    pd.concat(oud + [pd.DataFrame([rij])], ignore_index=True).to_csv(gp, index=False)
    # groepen (bv. top 5): totaal per groep
    groep_regels, groep_tekst = [], []
    for naam, leden in (sel.get("groepen") or {}).items():
        gt = sum(st["traders"][x].get("equity", st["traders"][x]["potje"]) for x in leden if x in st["traders"])
        gs = sum(st["traders"][x]["start_potje"] for x in leden if x in st["traders"])
        if gs:
            groep_regels.append(f"- Groep **{naam}** ({len(leden)}): ${gt:,.0f} van ${gs:,.0f} ({100 * (gt / gs - 1):+.1f}%)")
            groep_tekst.append(f"{naam}: {100 * (gt / gs - 1):+.1f}%")
    # rapport
    dagen = max((NU - st["start"]) / 86_400_000, 1e-9)
    regels = [f"# Papier-HL (proportioneel kopiëren op Hyperliquid zelf)", "",
              f"Start {pd.Timestamp(st['start'], unit='ms'):%d-%m %H:%M} UTC, bijgewerkt {pd.Timestamp(NU, unit='ms'):%d-%m %H:%M} UTC ({dagen:.1f} dagen).",
              f"**Totaal: ${tot:,.0f} van ${start_tot:,.0f} ({100 * (tot / start_tot - 1):+.1f}%)**",
              f"Funding (vanaf 5-10 ±18:30 UTC): ${tot_f:+,.2f} — zonder funding: ${tot - tot_f:,.0f} ({100 * ((tot - tot_f) / start_tot - 1):+.1f}%)",
              *groep_regels, "",
              "| Trader | Potje | Rendement | Trader zelf | Trades | Open | Fees | Funding | Te klein | Status |", "|---|---|---|---|---|---|---|---|---|---|"]
    for a, s in sorted(st["traders"].items(), key=lambda x: -x[1].get("equity", 0)):
        e = s.get("equity", s["potje"])
        regels.append(f"| {kort(a)} | ${e:,.0f} | {100 * (e / s['start_potje'] - 1):+.1f}% | {s.get('trader_rend_pct', '–')}% | {s['trades']} | "
                      f"{s.get('open_posities', 0)} | ${s['fees']:.2f} | ${s.get('funding', 0.0):+.2f} | {s['overgeslagen_klein']} | {'actief' if s['actief'] else 'gestopt'} |")
    open(f"{MAP}/rapport.md", "w").write("\n".join(regels) + "\n")
    print("\n".join(regels))
    # dagbericht naar telefoon (ntfy), één keer per dag rond 06-08 UTC
    topic = os.environ.get("NTFY_TOPIC")
    uur = pd.Timestamp(NU, unit="ms").hour
    if topic and (os.environ.get("FORCEER_BERICHT") or 6 <= uur < 8):
        top = sorted(st["traders"].items(), key=lambda x: -x[1].get("equity", 0))
        tekst = f"Papier-HL: ${tot:,.0f} ({100 * (tot / start_tot - 1):+.1f}%) na {dagen:.1f} d (funding ${tot_f:+,.0f})\n" + "".join(x + "\n" for x in groep_tekst) + "\n".join(
            f"{kort(a)}: {100 * (s.get('equity', s['potje']) / s['start_potje'] - 1):+.1f}%{'' if s['actief'] else ' (gestopt)'}" for a, s in top)
        try:
            requests.post(f"https://ntfy.sh/{topic}", data=tekst.encode(), headers={"Title": "Papier-HL dagupdate"}, timeout=20)
        except Exception as e:  # noqa: BLE001
            print("ntfy faalt", e)


if __name__ == "__main__":
    main()
