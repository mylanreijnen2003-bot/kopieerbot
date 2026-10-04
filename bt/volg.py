"""Volg-meldingen: elke ~5 min nieuwe fills van gevolgde traders -> ntfy-bericht om met de hand na te doen.
Lijst: <map>/volg.json  {"traders": [{"address": ..., "pot": 50, "K": 3, "naam": "..."}]}
Staat: <map>/volg_state.json (laatste verwerkte fill per trader, open posities met instapprijs).
Per munt per run samengevat: OPEN / BIJKOOP / AFBOUW / SLUIT / OMDRAAI. Posities van vóór het volgen: alleen ter info.
Gebruik: python -m bt.volg <map> <kraken.json>
"""

from __future__ import annotations

import json
import os
import sys
import time

import requests

from bot import hl
from bt.engine import to_base

NU = int(time.time() * 1000)


def kraken_sym(coin):
    b = to_base(coin)
    return "PF_XBTUSD" if b == "BTC" else f"PF_{b}USD"


def stuur(titel, tekst, prio="default"):
    topic = os.environ.get("NTFY_TOPIC")
    print(titel, "|", tekst, flush=True)
    if topic:
        try:
            requests.post(f"https://ntfy.sh/{topic}", data=tekst.encode(),
                          headers={"Title": titel, "Priority": prio, "Tags": "chart_with_upwards_trend"}, timeout=20)
        except Exception:  # noqa: BLE001
            pass


def main():
    d, kpad = sys.argv[1:3]
    kraken = set(json.load(open(kpad)))
    cfg = json.load(open(f"{d}/volg.json"))
    spad = f"{d}/volg_state.json"
    state = json.load(open(spad)) if os.path.exists(spad) else {}
    for t in cfg["traders"]:
        a = t["address"].lower()
        naam = t.get("naam") or a[:6] + "…" + a[-4:]
        inzet = round(t["pot"] / max(1, t["K"]), 2)
        st = state.get(a)
        if st is None:
            ch = hl.info({"type": "clearinghouseState", "user": a})
            oud = [p["position"]["coin"] for p in ch.get("assetPositions", []) if float(p["position"].get("szi", 0)) != 0]
            state[a] = {"laatste": NU, "pos": {c: {"oud": True} for c in oud}}
            stuur(f"Volgen gestart: {naam}", f"Inzet per trade ~€{inzet:.0f} (pot €{t['pot']:.0f} / {t['K']}). "
                  f"Open posities van vóór nu ({', '.join(oud) or 'geen'}) niet kopiëren.")
            continue
        fl = [f for f in hl.fills(a, st["laatste"] + 1, NU) if f["kind"] == "perp"]
        if not fl:
            continue
        per = {}
        for f in fl:
            per.setdefault(f["coin"], []).append(f)
        for coin, fs in per.items():
            s0, s1 = fs[0]["start"], fs[-1]["after"]
            q = sum(abs(f["signed"]) for f in fs)
            px = sum(abs(f["signed"]) * f["px"] for f in fs) / q if q else fs[-1]["px"]
            pos = st["pos"].get(coin)
            kr = to_base(coin) in kraken
            sym = kraken_sym(coin) if kr else f"{coin} (niet op Kraken: overslaan)"
            if s0 == 0 and s1 != 0 or (s0 != 0 and s1 != 0 and s0 * s1 < 0):
                kant = "LONG" if s1 > 0 else "SHORT"
                soort = "OMDRAAI" if s0 != 0 else "OPEN"
                st["pos"][coin] = {"oud": False, "dir": 1 if s1 > 0 else -1, "px": px, "open": fs[0]["time"]}
                extra = ""
                if pos and not pos.get("oud") and soort == "OMDRAAI":
                    r = pos["dir"] * (px / pos["px"] - 1)
                    extra = f" Vorige positie gesloten: {100 * r:+.1f}%."
                stuur(f"{naam}: {soort} {kant} {to_base(coin)}",
                      f"Prijs {px:.6g}. Doe na: {kant} {sym} voor ~€{inzet:.0f}, geen hefboom.{extra}", "high")
            elif s1 == 0 and s0 != 0:
                st["pos"].pop(coin, None)
                if pos and not pos.get("oud"):
                    r = pos["dir"] * (px / pos["px"] - 1)
                    stuur(f"{naam}: SLUIT {to_base(coin)} ({100 * r:+.1f}%)",
                          f"Prijs {px:.6g}. Doe na: sluit je {sym}-positie helemaal.", "high")
                else:
                    stuur(f"{naam}: sluit oude positie {to_base(coin)}", "Was van vóór het volgen: niets doen.", "low")
            elif abs(s1) > abs(s0):
                if pos and not pos.get("oud"):
                    stuur(f"{naam}: bijkoop {to_base(coin)}", f"Prijs {px:.6g}. Niets doen (je inzet staat vast).", "low")
            elif abs(s1) < abs(s0):
                if pos and not pos.get("oud"):
                    deel = 100 * (abs(s0) - abs(s1)) / abs(s0)
                    stuur(f"{naam}: afbouw {to_base(coin)} {deel:.0f}%",
                          f"Prijs {px:.6g}. Doe na: verkoop ~{deel:.0f}% van je {sym}-positie.", "default")
        st["laatste"] = max(f["time"] for f in fl)
    json.dump(state, open(spad, "w"), indent=1)


if __name__ == "__main__":
    main()
