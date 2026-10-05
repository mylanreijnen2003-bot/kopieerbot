"""Papier volgen Nado: top 10 traders uit de V2-test, vaste inzet, draait op GitHub Actions.

  python -m kansen.papier_nado <map>      # map met selectie.json; schrijft state.json, rapport.md, geschiedenis.csv, trades.csv

Regels (zelfde als de V2-test, zodat het vergelijkbaar blijft):
- potje 100 per trader; inzet per trade = potje / K (K = posities die hij meestal tegelijk open heeft, p90)
- alleen posities die ná de start vanaf plat openen (posities van vóór de start worden overgeslagen tot hij plat is)
- kosten 0,2% per trade (fee + slippage), rendement per trade uit bt.vast_select.trades_pct
- trader stopt als zijn potje onder -20% komt
- open posities worden NIET meegewaardeerd (Nado geeft geen gratis markprijs per product): alleen gesloten trades
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

from bt.vast_select import trades_pct
from kansen import nado as N

MAP = Path(sys.argv[1])
NU = int(time.time() * 1000)


def kort(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}"


def fills_sinds(sub: str, prods: dict, start_ms: int):
    """Alle perp-fills van dit subaccount met ts >= start_ms (nieuwste eerst ophalen, stoppen bij oudere)."""
    rows, idx = [], None
    for _ in range(40):
        q = {**N.subq(sub), "product_ids": list(prods), "limit": 500}
        if idx is not None:
            q["idx"] = str(idx)
        d = N.post({"matches": q})
        if not d or not d.get("matches"):
            break
        ts = {int(t["submission_idx"]): int(t["timestamp"]) for t in d.get("txs", [])}
        oudste = None
        for m in d["matches"]:
            t = ts.get(int(m["submission_idx"]), 0) * 1000
            oudste = t if oudste is None else min(oudste, t)
            pb = ((m.get("pre_balance") or {}).get("base") or {}).get("perp")
            qb = ((m.get("post_balance") or {}).get("base") or {}).get("perp")
            if not pb or not qb or float(m["base_filled"]) == 0:
                continue
            rows.append({"time": t, "idx": int(m["submission_idx"]), "d": m.get("digest"),
                         "coin": prods.get(int(pb["product_id"]), str(pb["product_id"])),
                         "start": round(float(pb["balance"]["amount"]) / N.X18, 12),
                         "after": round(float(qb["balance"]["amount"]) / N.X18, 12),
                         "px": abs(float(m["quote_filled"]) / float(m["base_filled"]))})
        if len(d["matches"]) < 500 or (oudste is not None and oudste < start_ms):
            break
        idx = min(int(m["submission_idx"]) for m in d["matches"]) - 1
    uniek = {(r["d"], r["idx"]): r for r in rows if r["time"] >= start_ms}
    return sorted(uniek.values(), key=lambda r: (r["time"], r["idx"]))


def bouw_selectie(csv: str, top: int = 10):
    """Selectie uit volgbaar_vandaag.csv (ontsleuteld uit kansen/nado/priv.tgz.enc): top N op potje-%."""
    d = pd.read_csv(csv).sort_values("keuze_potje_pct", ascending=False).head(top)
    traders = []
    for r in d.itertuples(index=False):
        adres, _, nm = str(r.account).partition("/")
        traders.append({"account": str(r.account), "sub": adres.lower() + (nm or "default").encode().hex().ljust(24, "0"),
                        "K": max(1, int(r.p90_posities)), "keuze_potje_pct": float(r.keuze_potje_pct),
                        "keuze_gem_pct": float(r.keuze_gem_pct), "houdtijd_uur": float(r.houdtijd_uur)})
    MAP.mkdir(parents=True, exist_ok=True)
    json.dump({"gemaakt": pd.Timestamp(NU, unit="ms").strftime("%Y-%m-%d"), "venue": "nado", "potje": 100.0,
               "kosten": 0.002, "stop_pct": -20.0,
               "regel": f"volgbaar vandaag, top {top} op potje-% (keuzeperiode); eisen zie kansen/VOORREGISTRATIE.md",
               "traders": traders}, open(MAP / "selectie.json", "w"), indent=1)
    print(f"selectie: {len(traders)} traders")


def main():
    sel = json.load(open(MAP / "selectie.json"))
    st = json.load(open(MAP / "state.json")) if (MAP / "state.json").exists() else {"start": NU, "traders": {}}
    potje, stop = float(sel.get("potje", 100)), float(sel.get("stop_pct", -20))
    prods = N.perps()
    if sel["traders"]:
        N.kies_subkey(sel["traders"][0]["sub"], prods)
    rijen, regels = [], []
    tot = 0.0
    for t in sel["traders"]:
        sub, K = t["sub"], max(1, int(t["K"]))
        s = st["traders"].setdefault(sub, {"account": t["account"], "K": K, "start_ms": st["start"]})
        try:
            fl = fills_sinds(sub, prods, s["start_ms"])
        except Exception as exc:  # noqa: BLE001
            print("fout", kort(sub), repr(exc)[:200], flush=True)
            fl = None
        if fl is None:
            s["fout"] = True
            tot += s.get("equity", potje)
            continue
        s.pop("fout", None)
        tr = trades_pct(fl)                                   # (coin, open, sluit, rendement na 0,2% kosten)
        inzet = potje / K
        eq, gestopt, n = potje, None, 0
        for c, o, sl, r in tr:
            if gestopt:
                break
            eq += r * inzet
            n += 1
            rijen.append({"tijd": sl, "trader": kort(sub), "munt": c, "open": o, "rendement_pct": round(100 * r, 3),
                          "winst": round(r * inzet, 3), "potje_na": round(eq, 2)})
            if 100 * (eq / potje - 1) <= stop:
                gestopt = sl
        laatste = {}
        for f in fl:
            laatste[f["coin"]] = f["after"]
        open_nu = sum(1 for v in laatste.values() if abs(v) > 1e-12)
        s.update(equity=eq, trades=n, gestopt=gestopt, open_posities=open_nu,
                 laatste_fill=max((f["time"] for f in fl), default=None), fills=len(fl))
        tot += eq
    st["bijgewerkt"] = NU
    json.dump(st, open(MAP / "state.json", "w"), indent=1)
    if rijen:
        pd.DataFrame(sorted(rijen, key=lambda x: x["tijd"])).to_csv(MAP / "trades.csv", index=False)
    start_tot = potje * len(sel["traders"])
    dagen = max((NU - st["start"]) / 86_400_000, 1e-9)
    rij = {"tijd": pd.Timestamp(NU, unit="ms").strftime("%Y-%m-%d %H:%M"), "totaal": round(tot, 2)}
    for sub, s in st["traders"].items():
        rij[kort(sub)] = round(s.get("equity", potje), 2)
    pd.DataFrame([rij]).to_csv(MAP / "geschiedenis.csv", mode="a", header=not (MAP / "geschiedenis.csv").exists(), index=False)
    regels = ["# Papier Nado (top 10 uit de V2-test, vaste inzet)", "",
              f"Start {pd.Timestamp(st['start'], unit='ms'):%d-%m %H:%M} UTC, bijgewerkt {pd.Timestamp(NU, unit='ms'):%d-%m %H:%M} UTC ({dagen:.1f} dagen).",
              f"**Totaal: {tot:,.1f} van {start_tot:,.0f} ({100 * (tot / start_tot - 1):+.2f}%)**", "",
              "Alleen gesloten trades; open posities zijn niet meegewaardeerd. Kosten 0,2% per trade.", "",
              "| Trader | Potje | Rendement | Trades | Inzet/trade | K | Open | Laatste fill | Status |", "|---|---|---|---|---|---|---|---|---|"]
    for sub, s in sorted(st["traders"].items(), key=lambda x: -x[1].get("equity", 0)):
        e = s.get("equity", potje)
        lf = f"{pd.Timestamp(s['laatste_fill'], unit='ms'):%d-%m %H:%M}" if s.get("laatste_fill") else "–"
        status = "gestopt" if s.get("gestopt") else ("fout" if s.get("fout") else "actief")
        regels.append(f"| {kort(sub)} | {e:.1f} | {100 * (e / potje - 1):+.2f}% | {s.get('trades', 0)} | "
                      f"{potje / max(1, s['K']):.1f} | {s['K']} | {s.get('open_posities', 0)} | {lf} | {status} |")
    (MAP / "rapport.md").write_text("\n".join(regels) + "\n")
    print("\n".join(regels))
    topic = os.environ.get("NTFY_TOPIC")
    if topic and (os.environ.get("FORCEER_BERICHT") or 6 <= pd.Timestamp(NU, unit="ms").hour < 8):
        tekst = f"Papier Nado: {100 * (tot / start_tot - 1):+.2f}% na {dagen:.1f} d\n" + "\n".join(
            f"{kort(sub)}: {100 * (s.get('equity', potje) / potje - 1):+.1f}%{'' if not s.get('gestopt') else ' (gestopt)'}"
            for sub, s in sorted(st["traders"].items(), key=lambda x: -x[1].get("equity", 0)))
        try:
            requests.post(f"https://ntfy.sh/{topic}", data=tekst.encode(), headers={"Title": "Papier Nado dagupdate"}, timeout=20)
        except Exception as exc:  # noqa: BLE001
            print("ntfy faalt", exc)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "--selectie":
        bouw_selectie(sys.argv[3])
    else:
        main()
