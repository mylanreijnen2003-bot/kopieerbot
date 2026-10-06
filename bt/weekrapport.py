"""Wekelijks tussenrapport papier v2 (zondagavond) naar de telefoon en naar results/papier2/weekrapport.md.
Per groep: resultaat sinds start, omgerekend per maand, laatste 7 dagen, vs BTC en vs controlegroep D,
toets aan de live-eisen (positief, >= 3%/mnd beter dan BTC en beter dan D), wijzigingen deze week.
Gebruik: python -m bt.weekrapport <map>
"""

import json
import os
import sys
import time

import pandas as pd
import requests

NU = int(time.time() * 1000)
DAG = 86_400_000
NAMEN = {"A": "A max rendement", "B": "B laag risico", "C": "C controle top 30", "D": "D controle willekeurig",
         "E": "E 0x2555 Bitvavo", "F": "F constantheid", "G": "G consistent rendement"}


def main():
    d = sys.argv[1]
    g = pd.read_csv(f"{d}/geschiedenis.csv")
    state = json.load(open(f"{d}/state.json"))
    regels, md = [], [f"# Weekrapport papier v2 — {pd.to_datetime(NU, unit='ms'):%Y-%m-%d}", ""]
    laatste = g.sort_values("ts").groupby("versie").tail(1).set_index("versie")
    d_pm = None
    for v in ["A", "B", "C", "D", "E", "F", "G"]:
        if v not in laatste.index:
            continue
        x = g[g.versie == v].sort_values("ts")
        start = state["versies"][v].get("start", state["start"])
        dagen = max(1.0, (NU - start) / DAG)
        pct, btc = float(laatste.loc[v, "pct"]), float(laatste.loc[v, "btc_pct"])
        oud = x[x.ts <= NU - 7 * DAG].tail(1)
        week = pct - float(oud.pct.iloc[0]) if len(oud) else pct
        pm, btc_pm = pct / dagen * 30.44, btc / dagen * 30.44
        if v == "D":
            d_pm = pm
        rows = {"v": v, "pm": pm, "btc_pm": btc_pm, "pct": pct, "week": week, "dagen": dagen,
                "actief": int(laatste.loc[v, "actief"])}
        regels.append(rows)
    tekst = []
    for r in regels:
        eisen = ""
        if r["v"] in ("A", "B", "E", "F", "G"):
            ok = [r["pct"] > 0, r["pm"] - r["btc_pm"] >= 3, d_pm is None or r["pm"] > d_pm]
            eisen = " ✅ haalt live-eisen" if all(ok) else f" ({sum(ok)}/3 eisen)"
        lijn = (f"{NAMEN[r['v']]}: {r['pct']:+.1f}% totaal ({r['pm']:+.1f}%/mnd), week {r['week']:+.1f}%, "
                f"BTC {r['btc_pm']:+.1f}%/mnd{eisen}")
        tekst.append(lijn)
        md.append(f"- {lijn} — dag {r['dagen']:.0f}, {r['actief']} actief")
    log = f"{d}/gebeurtenissen.log"
    wijz = []
    if os.path.exists(log):
        grens = pd.to_datetime(NU - 7 * DAG, unit="ms")
        for regel in open(log):
            try:
                if pd.to_datetime(regel[:16]) >= grens and ("eruit" in regel or "STOP" in regel):
                    wijz.append(regel[17:].strip())
            except Exception:  # noqa: BLE001
                pass
    md += ["", "## Eruit deze week", *(f"- {w}" for w in wijz or ["niemand"])]
    md += ["", "Live-eisen: positief na kosten, >= 3%/mnd beter dan BTC, beter dan controlegroep D. Oordeel na 4-8 weken."]
    open(f"{d}/weekrapport.md", "w").write("\n".join(md))
    print("\n".join(md))
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        bericht = "\n".join(tekst + ([f"Eruit deze week: {len(wijz)}"] + wijz[:6] if wijz else ["Eruit deze week: niemand"]))
        requests.post(f"https://ntfy.sh/{topic}", data=bericht.encode(), headers={"Title": "Kopieerbot: weekrapport"},
                      timeout=20)


if __name__ == "__main__":
    main()
