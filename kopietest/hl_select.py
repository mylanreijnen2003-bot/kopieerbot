"""Hyperliquid-selectie voor kopiëren op Hyperliquid zelf (proportioneel, kosten ±5 bps -> je volgt hun accountrendement).
Maatstaf = accountrendement van de trader (portfolio-API: pnl incl. open posities, stortingen eruit), niet winst per trade.
  shard <uit> <i> <n> : portfolio ophalen voor een deel van de pool
  kies  <uit> <shardmappen...> : kiezen op data t/m 18-8 (knip), testen 19-8 t/m nu; daarna dezelfde regel op vandaag
Pool: results/praktisch/alle.csv (actief 19-7 t/m 18-8, >= 100 trades, >= 3 mnd; geen winst-eis) + results/recent/alle_winstgevend.csv
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import pandas as pd

from bot import hl

DAG = 86_400_000
NU = int(time.time() * 1000)
KNIP = int(pd.Timestamp("2026-08-19").value // 10**6)
MODUS, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)


def pool(res="res"):
    p = pd.read_csv(f"{res}/praktisch/alle.csv")
    m = p.laatste_trade.max()
    p = p[(p.laatste_trade >= m - 30 * DAG) & (p.hun_trades >= 100) & (p.hun_maanden >= 3)]
    r = pd.read_csv(f"{res}/recent/alle_winstgevend.csv")
    adr = sorted(set(p.address) | set(r.address))
    tr = dict(zip(p.address, p.hun_trades))
    return adr, tr


def reeks(per, sleutel):
    pts = {}
    keys = ("perpAllTime", "perpMonth", "perpWeek") if "perpAllTime" in per else ("allTime", "month", "week")
    for k in keys:
        for t, v in per.get(k, {}).get(sleutel, []):
            pts[int(t)] = float(v)
    return sorted(pts.items())


def interp(pts, t):
    return hl.av_at(pts, t) if pts else None


def kenmerken(av, pnl, T):
    """Maandrendementen (pnl-verschil / gem. equity) per kalendermaand tot T, plus risico."""
    av = [(t, v) for t, v in av if t <= T]
    pnl = [(t, v) for t, v in pnl if t <= T]
    if len(av) < 3 or len(pnl) < 3:
        return None
    start = next((t for t, v in av if v >= 100), None)
    if start is None:
        return None
    grenzen = [start]
    m = pd.Timestamp(start, unit="ms").to_period("M")
    while True:
        m += 1
        e = int(m.to_timestamp().value // 10**6)
        if e >= T:
            break
        grenzen.append(e)
    grenzen.append(T)
    mnd = {}
    for a, b in zip(grenzen[:-1], grenzen[1:]):
        eq = [v for t, v in av if a <= t <= b] or [interp(av, a)]
        geq = float(np.mean(eq))
        if geq <= 50:
            continue
        mnd[pd.Timestamp(a, unit="ms").strftime("%Y-%m")] = (interp(pnl, b) - interp(pnl, a)) / geq
    if not mnd:
        return None
    laatste = list(mnd.items())[-6:]
    l3 = [v for _, v in list(mnd.items())[-3:]]
    e90 = [v for t, v in av if t >= T - 90 * DAG]
    p90 = [(t, v) for t, v in pnl if t >= T - 90 * DAG]
    geq90 = float(np.mean(e90)) if e90 else 0
    dd = 0.0
    if p90 and geq90 > 0:
        top = -1e18
        for _, v in p90:
            top = max(top, v)
            dd = min(dd, (v - top) / geq90)
    act = [v for t, v in pnl if t >= T - 14 * DAG]
    return {"dagen": (T - start) / DAG, "maanden": len(mnd), "verliesmaanden_6": sum(v < 0 for _, v in laatste),
            "gem_maand_6": float(np.mean([v for _, v in laatste])), "som_3": float(np.sum(l3)), "gem_maand_3": float(np.mean(l3)),
            "slechtste_maand_6": float(min(v for _, v in laatste)), "dd_90": dd, "equity_gem_90": geq90,
            "equity_min_90": float(min(e90)) if e90 else 0, "actief_14d": len(set(round(x, 2) for x in act)) > 1,
            "mnd": json.dumps({k: round(100 * v, 2) for k, v in mnd.items()})}


def main_shard(i, n):
    adr, tr = pool()
    deel = adr[i::n]
    lim = int(os.environ.get("LIMIT", "0"))
    if lim:
        deel = deel[:lim]
    rows = []
    t0 = time.time()
    for k, a in enumerate(deel):
        if time.time() - t0 > 5.3 * 3600:
            break
        try:
            per = dict(hl.info({"type": "portfolio", "user": a}, weight=20))
        except Exception as e:  # noqa: BLE001
            print("fout", a[:10], e)
            continue
        av, pnl = reeks(per, "accountValueHistory"), reeks(per, "pnlHistory")
        r = {"address": a, "trades_hist": tr.get(a)}
        ki = kenmerken(av, pnl, KNIP)
        nu = kenmerken(av, pnl, NU)
        if ki:
            r.update({f"k_{x}": v for x, v in ki.items()})
            # test: van knip tot nu
            eq = [v for t, v in av if t >= KNIP] or [interp(av, KNIP)]
            geq = float(np.mean(eq)) if eq and eq[0] else 0
            if geq > 50:
                r["test_rend"] = (interp(pnl, NU) - interp(pnl, KNIP)) / geq
                r["test_equity_min"] = float(min(eq))
        if nu:
            r.update({f"n_{x}": v for x, v in nu.items()})
        rows.append(r)
        if k % 200 == 0:
            print(i, k, "/", len(deel), round((time.time() - t0) / 60), "min", flush=True)
            pd.DataFrame(rows).to_csv(f"{OUT}/portfolio.csv", index=False)
    pd.DataFrame(rows).to_csv(f"{OUT}/portfolio.csv", index=False)


def geschikt(d, p):
    return d[(d[f"{p}dagen"] >= 90) & (d[f"{p}equity_gem_90"] >= 1000) & (d[f"{p}equity_min_90"] >= 0.2 * d[f"{p}equity_gem_90"])
             & (d[f"{p}som_3"] > 0) & (d[f"{p}dd_90"] > -0.4) & (d[f"{p}actief_14d"] == True) & (d.trades_hist.fillna(100) >= 100)]  # noqa: E712


def rang(g, p, regel):
    if regel == "mylan":     # minste verliesmaanden, dan hoogste gem. maandrendement
        return g.sort_values([f"{p}verliesmaanden_6", f"{p}gem_maand_6"], ascending=[True, False])
    if regel == "risico":    # rendement per eenheid daling
        return g.assign(_s=g[f"{p}gem_maand_3"] / (g[f"{p}dd_90"].abs() + 0.05)).sort_values("_s", ascending=False)
    return g.sort_values(f"{p}gem_maand_3", ascending=False)   # 'max': hoogste rendement laatste 3 mnd


def main_kies(mappen):
    d = pd.concat([pd.read_csv(f"{m}/portfolio.csv") for m in mappen if os.path.exists(f"{m}/portfolio.csv")], ignore_index=True)
    d.to_csv(f"{OUT}/portfolio_alle.csv.gz", index=False)
    uit = {"pool": len(d), "met_historie_knip": int(d.k_dagen.notna().sum()) if "k_dagen" in d else 0}
    k = geschikt(d[d.k_dagen.notna() & d.test_rend.notna()], "k_")
    uit["geschikt_knip"] = len(k)
    uit["basis_test"] = {"gem_pct": round(100 * k.test_rend.mean(), 2), "mediaan_pct": round(100 * k.test_rend.median(), 2),
                         "winst_aandeel": round(float((k.test_rend > 0).mean()), 3)}
    rng = np.random.default_rng(42)
    rnd = [k.test_rend.sample(10, random_state=int(s)).mean() for s in rng.integers(0, 10**6, 200)] if len(k) >= 10 else []
    uit["willekeurig_10_test"] = {"gem_pct": round(100 * float(np.mean(rnd)), 2), "p10_pct": round(100 * float(np.percentile(rnd, 10)), 2),
                                  "p90_pct": round(100 * float(np.percentile(rnd, 90)), 2)} if rnd else None
    for regel in ("mylan", "risico", "max"):
        for n in (5, 10, 20):
            s = rang(k, "k_", regel).head(n)
            uit[f"test_{regel}_{n}"] = {"gem_pct": round(100 * s.test_rend.mean(), 2), "mediaan_pct": round(100 * s.test_rend.median(), 2),
                                        "winst_aandeel": round(float((s.test_rend > 0).mean()), 2),
                                        "slechtste_pct": round(100 * s.test_rend.min(), 2)}
    k.to_csv(f"{OUT}/knip_geschikt.csv", index=False)
    n = geschikt(d[d.n_dagen.notna()], "n_")
    uit["geschikt_vandaag"] = len(n)
    for regel in ("mylan", "risico", "max"):
        uit[f"vandaag_{regel}"] = rang(n, "n_", regel).address.head(30).tolist()
    n.to_csv(f"{OUT}/vandaag_geschikt.csv", index=False)
    json.dump(uit, open(f"{OUT}/uitslag.json", "w"), indent=1, default=str)
    print(json.dumps({x: v for x, v in uit.items() if not x.startswith("vandaag_")}, indent=1))


if __name__ == "__main__":
    if MODUS == "shard":
        main_shard(int(sys.argv[3]), int(sys.argv[4]))
    else:
        main_kies(sys.argv[3:])
