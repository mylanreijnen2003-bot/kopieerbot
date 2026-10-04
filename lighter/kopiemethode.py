"""Lighter: welke kopieermethode houdt de edge vast?

Per trade van de gekozen traders (testmaanden, knips 1-6..1-9):
- eerste:  alleen de eerste instap kopiëren, vaste inzet (zo rekent papier A/B/C nu)
- gem:     instap = gemiddelde instap van de trader (alsof je zijn bijkopen meedoet), rendement op totaal ingelegd
- max:     bijkopen naar verhouding meedoen; rendement op de GROOTSTE positie (= wat je potje moet kunnen dragen)
Kosten 0,16% per kant over alle omzet. Plus: hoeveel groter wordt de positie dan de eerste instap (martingale-risico).

Gebruik: python -m lighter.kopiemethode <datamap met part-*> <kraken.json> <uit openbaar>
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import pandas as pd

from lighter import api
from lighter.edge import KNIPS, KOSTEN_LEIDER, kenmerken, ms_dag, test_uitkomst
from lighter.robuust import CONFIGS, kies
from lighter.selectie import lees, reconstrueer

NU = int(time.time() * 1000)
KANT = 0.0016
RNG = np.random.default_rng(7)


def trades_vol(fl):
    st, out = {}, []

    def nieuw(c, a, px, t):
        st[c] = {"dir": 1 if a > 0 else -1, "in_q": abs(a), "in_c": abs(a) * px, "uit_q": 0.0, "uit_c": 0.0,
                 "t": t, "px0": px, "eerste_c": abs(a) * px, "max_c": abs(a) * px, "bij": 0}
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
            p["bij"] += 1
            p["max_c"] = max(p["max_c"], abs(a) * px)
        else:
            q = abs(s) if (a == 0 or flip) else abs(s) - abs(a)
            p["uit_q"] += q
            p["uit_c"] += q * px
        if a == 0 or flip:
            gin, guit = p["in_c"] / p["in_q"], p["uit_c"] / p["uit_q"]
            pnl = p["dir"] * (guit - gin) * p["uit_q"]
            omzet = p["in_c"] + p["uit_c"]
            out.append({"coin": c, "dir": p["dir"], "open": p["t"], "sluit": t, "px0": p["px0"], "gin": gin, "guit": guit,
                        "r": p["dir"] * (guit / gin - 1) - KOSTEN_LEIDER, "r_bruto": p["dir"] * (guit / gin - 1),
                        "r_eerste": p["dir"] * (guit / p["px0"] - 1) - 2 * KANT,
                        "r_gem": p["dir"] * (guit / gin - 1) - 2 * KANT,
                        "r_max": (pnl - KANT * omzet) / p["max_c"],
                        "groei": p["max_c"] / p["eerste_c"], "bijkopen": p["bij"]})
            st.pop(c)
            if flip:
                nieuw(c, a, px, t)
    return out


def main():
    d, kraken_p, uit = sys.argv[1:4]
    os.makedirs(uit, exist_ok=True)
    kraken = set(json.load(open(kraken_p)))
    scan, fills, pnl, accs, meta = lees(d)
    sym = api.markten()
    fills = fills[~fills.kind.fillna("perp").astype(str).str.lower().str.contains("spot")]
    pnl_g = {i: g for i, g in pnl.groupby("idx")} if len(pnl) else {}
    T, F = {}, {}
    for idx, f in fills.groupby("idx"):
        fl, _ = reconstrueer(f, sym)
        tr = trades_vol(fl)
        if len(tr) >= 20:
            T[idx], F[idx] = pd.DataFrame(tr), f
    grenzen = [ms_dag(k) for k in KNIPS] + [NU]
    rijen = []
    for i, K in enumerate(KNIPS):
        rows = []
        for idx, t in T.items():
            k = kenmerken(idx, t, F[idx], pnl_g.get(idx), grenzen[i], kraken)
            if k:
                k.update(test_uitkomst(t, grenzen[i], grenzen[i + 1], k["p90"]))
                rows.append(k)
        fold = pd.DataFrame(rows).reset_index(drop=True)
        for naam, c in CONFIGS.items():
            for top in (5, 10):
                for rk, r in enumerate(kies(fold, {**c, "N": top}).itertuples()):
                    t = T[r.idx]
                    tt = t[(t.open >= grenzen[i]) & (t.open < grenzen[i + 1]) & (t.sluit <= NU)]
                    for x in tt.to_dict("records"):
                        rijen.append({"config": naam, "groep": f"top{top}", "knip": K, "idx": r.idx, "rang": rk,
                                      "K": max(1, r.p90), **{k: x[k] for k in ("r_eerste", "r_gem", "r_max", "groei", "bijkopen", "sluit")}})
        print(f"knip {K} klaar", flush=True)
    df = pd.DataFrame(rijen)
    df = df[(df.groep == "top10") | (df.rang < 5)]
    samen = []
    for (naam, groep), g in df.groupby(["config", "groep"]):
        g = g[g.rang < (5 if groep == "top5" else 10)]
        rij = {"config": naam, "groep": groep, "trades": len(g),
               "bijkoop_trades_pct": round(100 * (g.bijkopen > 0).mean(), 1),
               "groei_mediaan": round(float(g.groei.median()), 2), "groei_p90": round(float(g.groei.quantile(0.9)), 1),
               "groei_max": round(float(g.groei.max()), 1)}
        for m in ("r_eerste", "r_gem", "r_max"):
            rij[f"{m}_gem_pct"] = round(100 * g[m].mean(), 3)
            rij[f"{m}_slechtste_pct"] = round(100 * g[m].min(), 1)
            # potje per trader-maand: onderzoeksmaat (som / K) en papiermaat (inzet min(100/K, 25) op €100)
            tm = g.groupby(["knip", "idx"]).apply(lambda x: x[m].sum() / x.K.iloc[0])
            pm = g.groupby(["knip", "idx"]).apply(lambda x: (x[m] * min(100 / x.K.iloc[0], 25)).sum() / 100)
            rij[f"{m}_potje_mnd_pct"] = round(100 * tm.mean(), 1)
            rij[f"{m}_papier_mnd_pct"] = round(100 * pm.mean(), 1)
            rij[f"{m}_trader_maanden_winst_pct"] = round(100 * (tm > 0).mean(), 1)
        samen.append(rij)
    s = pd.DataFrame(samen)
    s.to_csv(f"{uit}/kopiemethode.csv", index=False)
    print(s.T.to_string(), flush=True)
    # per groei-klasse: waar komt de winst vandaan
    df["groei_klasse"] = pd.cut(df.groei, [0, 1.01, 2, 4, 1e9], labels=["geen bijkoop", "tot 2x", "2-4x", "> 4x"])
    gk = df[df.rang < 5].groupby(["config", "groei_klasse"], observed=True).agg(
        trades=("r_max", "size"), r_eerste_pct=("r_eerste", "mean"), r_max_pct=("r_max", "mean"),
        slechtste_max_pct=("r_max", "min")).reset_index()
    for k in ("r_eerste_pct", "r_max_pct", "slechtste_max_pct"):
        gk[k] = (100 * gk[k]).round(2)
    gk.to_csv(f"{uit}/groei_klassen.csv", index=False)
    print(gk.to_string(), flush=True)


if __name__ == "__main__":
    main()
