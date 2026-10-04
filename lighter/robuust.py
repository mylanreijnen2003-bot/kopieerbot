"""Lighter robuustheid: (1) geluk of echt (reality check met permutaties, incl. de zoektocht over het raster),
(2) hoe lang blijft een gekozen trader goed (maand 1/2/3 na selectie, rangcorrelatie),
(3) staartrisico: stop -10% met echte uurkoersen (doorschieten) + Monte Carlo van maand-uitkomsten en dalingen.

Gebruik: python -m lighter.robuust <datamap met part-*> <kraken.json> <uit openbaar>
Openbaar alleen aggregaten.
"""
from __future__ import annotations

import itertools
import json
import os
import sys
import time

import numpy as np
import pandas as pd

from lighter import api
from lighter.edge import (BASIS, HANDMATIG, KNIPS, KOSTEN_LEIDER, ONTWERP, RASTER, kenmerken, ms_dag, test_uitkomst,
                          trades_dir)
from lighter.selectie import lees, reconstrueer

DAG = api.DAG
NU = int(time.time() * 1000)
RNG = np.random.default_rng(2026)
STOPS = [None, 0.10, 0.20, 0.30, 0.50]
N_PERM = 1000
MAAND = 30 * DAG
CONFIGS = {
    "meeste_winst": dict(min_equity=1000, max_pos=10, handmatig=False, rang="potje_3m", N=5),
    "veilig": dict(min_equity=5000, max_pos=10, handmatig=True, rang="potje_3m", N=5),
    "standaard": dict(min_equity=5000, max_pos=10, handmatig=True, rang="potje", N=5),
}


def laad(d, kraken):
    scan, fills, pnl, accs, meta = lees(d)
    sym = api.markten()
    fills = fills[~fills.kind.fillna("perp").astype(str).str.lower().str.contains("spot")]
    pnl_g = {i: g for i, g in pnl.groupby("idx")} if len(pnl) else {}
    T, F = {}, {}
    for idx, f in fills.groupby("idx"):
        fl, _ = reconstrueer(f, sym)
        tr = trades_dir(fl)
        if len(tr) >= 20:
            t = pd.DataFrame(tr)
            t["r"] = t.r_bruto - KOSTEN_LEIDER
            T[idx], F[idx] = t, f
    grenzen = [ms_dag(k) for k in KNIPS] + [NU]
    folds = {}
    for i, K in enumerate(KNIPS):
        rows = []
        for idx, t in T.items():
            k = kenmerken(idx, t, F[idx], pnl_g.get(idx), grenzen[i], kraken)
            if k:
                k.update(test_uitkomst(t, grenzen[i], grenzen[i + 1], k["p90"]))
                rows.append(k)
        folds[K] = pd.DataFrame(rows).reset_index(drop=True)
        print(f"knip {K}: {len(rows)} accounts", flush=True)
    return T, folds, grenzen, sym


# ------------------------------------------------------------------ 1. geluk of echt

def volgordes(d):
    """Per (min_eq, max_pos, handmatig, rang): rij-indexen van geschikte accounts, gesorteerd. Onafhankelijk van test."""
    basis = BASIS(d).values
    hand = HANDMATIG(d).values
    out = {}
    for me, mp, h in itertools.product(RASTER["min_equity"], RASTER["max_pos"], RASTER["handmatig"]):
        m = basis & (d.equity.values >= me) & (d.p90.values <= mp)
        if h:
            m &= hand
        idx = np.where(m)[0]
        for rang in RASTER["rang"]:
            sleutel = [rang, "potje"] if rang != "potje" else ["potje"]
            o = d.iloc[idx].sort_values(sleutel, ascending=False, kind="stable").index.values
            out[(me, mp, h, rang)] = o
    return out, np.where(basis)[0]


def scores(ords, tp):
    """{(me,mp,h,rang,N): gem. test van top N of nan}"""
    s = {}
    for key, o in ords.items():
        v = tp[o[:20]]
        cs = np.cumsum(v)
        for n in RASTER["N"]:
            s[key + (n,)] = cs[n - 1] / n if len(o) >= n else np.nan
    return s


def statistieken(sc_per_fold):
    keys = list(sc_per_fold[KNIPS[0]].keys())
    M = np.array([[sc_per_fold[K][k] for K in KNIPS] for k in keys])        # configs x folds
    ontw = M[:, :3]
    geldig = (~np.isnan(ontw)).sum(axis=1) >= 2          # zelfde als edge: minstens 2 ontwerp-knips met genoeg traders
    min_ontw = np.where(geldig, np.min(np.where(np.isnan(ontw), np.inf, ontw), axis=1), -np.inf)
    gem_alle = np.where((~np.isnan(M)).sum(axis=1) >= 3, np.nanmean(np.where(np.isnan(M), np.nan, M), axis=1), -np.inf)
    best = int(np.argmax(min_ontw))
    return {"max_min_ontwerp": float(min_ontw.max()), "holdout_van_beste": float(M[best, 3]),
            "max_gem_alle": float(gem_alle.max()), "beste_cfg": keys[best], "M": M, "keys": keys}


def reality_check(folds):
    ords, basis_rij = {}, {}
    for K, d in folds.items():
        ords[K], basis_rij[K] = volgordes(d)
    echt_sc = {K: scores(ords[K], folds[K].test_potje.values) for K in KNIPS}
    echt = statistieken(echt_sc)
    keys = echt["keys"]
    cfg_idx = {naam: keys.index((c["min_equity"], c["max_pos"], c["handmatig"], c["rang"], c["N"])) for naam, c in CONFIGS.items()}
    uit = {"echt": {k: echt[k] for k in ("max_min_ontwerp", "holdout_van_beste", "max_gem_alle")},
           "echt_beste_cfg": dict(zip(["min_equity", "max_pos", "handmatig", "rang", "N"], echt["beste_cfg"])),
           "configs_echt_gem_per_fold": {n: [round(100 * float(x), 2) for x in echt["M"][i]] for n, i in cfg_idx.items()}}
    for nul in ("alles", "binnen_basis"):
        null_max, null_gem, null_cfg = [], [], {n: [] for n in CONFIGS}
        for _ in range(N_PERM):
            sc = {}
            for K, d in folds.items():
                tp = d.test_potje.values.copy()
                if nul == "alles":
                    tp = RNG.permutation(tp)
                else:
                    b = basis_rij[K]
                    tp[b] = RNG.permutation(tp[b])
                sc[K] = scores(ords[K], tp)
            st = statistieken(sc)
            null_max.append(st["max_min_ontwerp"])
            null_gem.append(st["max_gem_alle"])
            for n, i in cfg_idx.items():
                null_cfg[n].append(np.nanmean(st["M"][i]))
        null_max, null_gem = np.array(null_max), np.array(null_gem)
        uit[nul] = {
            "p_zoektocht_min_ontwerp": float((null_max >= echt["max_min_ontwerp"]).mean()),
            "p_zoektocht_gem_alle": float((null_gem >= echt["max_gem_alle"]).mean()),
            "nul_beste_min_ontwerp_p50_p95_pct": [round(100 * float(np.percentile(null_max, q)), 2) for q in (50, 95)],
            "nul_beste_gem_alle_p50_p95_pct": [round(100 * float(np.percentile(null_gem, q)), 2) for q in (50, 95)],
            "per_config_p": {n: float((np.array(v) >= np.nanmean(echt["M"][cfg_idx[n]])).mean()) for n, v in null_cfg.items()},
            "per_config_nul_gem_pct": {n: round(100 * float(np.nanmean(v)), 2) for n, v in null_cfg.items()},
        }
        print(f"reality check {nul}: {json.dumps(uit[nul])}", flush=True)
    uit["echt"] = {k: round(100 * v, 2) for k, v in uit["echt"].items()}
    return uit


# ------------------------------------------------------------------ 2. houdbaarheid

def kies(d, c):
    m = BASIS(d) & (d.equity >= c["min_equity"]) & (d.p90 <= c["max_pos"])
    if c["handmatig"]:
        m &= HANDMATIG(d)
    sleutel = [c["rang"], "potje"] if c["rang"] != "potje" else ["potje"]
    return d[m].sort_values(sleutel, ascending=False).head(c["N"])


def houdbaarheid(T, folds, grenzen):
    rijen, corr = [], []
    for i, K in enumerate(KNIPS[:3]):
        Km = grenzen[i]
        d = folds[K]
        b = d[BASIS(d)]
        if len(b) > 10:
            corr.append({"knip": K, "n": len(b),
                         "spearman_potje3m_vs_volgende_maand": round(float(b.potje_3m.rank().corr(b.test_potje.rank())), 3),
                         "spearman_potje_vs_volgende_maand": round(float(b.potje.rank().corr(b.test_potje.rank())), 3)})
        for naam, c in CONFIGS.items():
            for r in kies(d, c).itertuples():
                t, pot = T[r.idx], max(1, r.p90)
                for j in (1, 2, 3):
                    a, e = Km + (j - 1) * MAAND, Km + j * MAAND
                    if e > NU:
                        continue
                    w = t[(t.open >= a) & (t.open < e) & (t.sluit <= NU)]
                    rijen.append({"config": naam, "knip": K, "maand_na": j, "trades": len(w),
                                  "potje": w.r.sum() / pot, "actief": len(w) > 0})
    df = pd.DataFrame(rijen)
    samen = df.groupby(["config", "maand_na"]).agg(trader_maanden=("potje", "size"), potje_gem_pct=("potje", "mean"),
                                                  potje_mediaan_pct=("potje", "median"),
                                                  winstgevend_pct=("potje", lambda x: (x > 0).mean()),
                                                  nog_actief_pct=("actief", "mean")).reset_index()
    for c in ("potje_gem_pct", "potje_mediaan_pct", "winstgevend_pct", "nog_actief_pct"):
        samen[c] = (100 * samen[c]).round(1)
    return samen, corr


# ------------------------------------------------------------------ 3. staartrisico

def uurkoersen(m, start, cache):
    if m in cache:
        return cache[m]
    out, t = {}, start - start % 3_600_000
    while t < NU:
        st, d = api.get("candles", {"market_id": m, "resolution": "1h", "start_timestamp": t,
                                    "end_timestamp": min(NU, t + 500 * 3_600_000), "count_back": 500})
        lijst = next((v for v in (d or {}).values() if isinstance(v, list)), []) if isinstance(d, dict) else []
        for c in lijst:
            ts = c.get("timestamp", c.get("t"))
            g = lambda *ks: next((float(c[k]) for k in ks if c.get(k) not in (None, "")), None)
            if ts is not None:
                out[api.ms(ts)] = (g("open", "o"), g("high", "h"), g("low", "l"), g("close", "c"))
        t += 500 * 3_600_000
    cache[m] = out
    return out


def met_stop(tr, c, stop=0.10):
    """Rendement met stop -10% op uurkoersen; doorschieten: open van het uur voorbij de stop = uitvoerprijs."""
    d, p1 = tr["dir"], tr["px0"]
    grens = p1 * (1 - stop) if d > 0 else p1 * (1 + stop)
    for h in range((tr["open"] // 3_600_000 + 1) * 3_600_000, tr["sluit"], 3_600_000):
        k = c.get(h)
        if not k or None in k:
            continue
        o, hi, lo, _ = k
        if d > 0 and lo <= grens:
            uit = min(o, grens)
            return d * (uit / p1 - 1) - 0.0032, True, uit != grens
        if d < 0 and hi >= grens:
            uit = max(o, grens)
            return d * (uit / p1 - 1) - 0.0032, True, uit != grens
    return d * (tr["guit"] / p1 - 1) - 0.0032, False, False


def staartrisico(T, folds, grenzen, sym):
    sym_inv = {v: k for k, v in sym.items()}
    cache, rijen = {}, []
    start = grenzen[0] - 5 * DAG
    for naam, c in CONFIGS.items():
        for i, K in enumerate(KNIPS):
            Km, Ke = grenzen[i], grenzen[i + 1]
            for r in kies(folds[K], {**c, "N": 10}).itertuples():
                t = T[r.idx]
                for tr in t[(t.open >= Km) & (t.open < Ke) & (t.sluit <= NU)].to_dict("records"):
                    m = sym_inv.get(tr["coin"])
                    if m is None:
                        continue
                    c1h = uurkoersen(m, start, cache)
                    for stop in STOPS:
                        if stop is None:
                            rs, gestopt, door = tr["guit"] / tr["px0"] * 0 + tr["dir"] * (tr["guit"] / tr["px0"] - 1) - 0.0032, False, False
                        else:
                            rs, gestopt, door = met_stop(tr, c1h, stop)
                        rijen.append({"config": naam, "stop": str(stop), "knip": K, "idx": r.idx, "pot": max(1, r.p90),
                                      "sluit": tr["sluit"], "r_zonder": tr["r"] - 0.0012,
                                      "r_met": rs, "gestopt": gestopt, "doorgeschoten": door})
    df = pd.DataFrame(rijen)
    stop_sam = df.groupby(["config", "stop"]).agg(trades=("r_met", "size"), gestopt_pct=("gestopt", "mean"),
                                        doorgeschoten_van_gestopt=("doorgeschoten", "sum"),
                                        slechtste_met_stop_pct=("r_met", "min"), slechtste_zonder_pct=("r_zonder", "min"),
                                        gem_met_pct=("r_met", "mean"), gem_zonder_pct=("r_zonder", "mean")).reset_index()
    for k in ("gestopt_pct", "slechtste_met_stop_pct", "slechtste_zonder_pct", "gem_met_pct", "gem_zonder_pct"):
        stop_sam[k] = (100 * stop_sam[k]).round(2)
    # Monte Carlo: portefeuille van 5 potjes; elk potje = willekeurige waargenomen trader-maand (top 10 per knip)
    # potje per trader-maand per stopniveau
    tm = df.assign(bijdrage=df.r_met * np.minimum(100 / df.pot, 25) / 100).groupby(["config", "stop", "knip", "idx"]).bijdrage.sum()
    stop_maand = tm.groupby(level=[0, 1]).agg(["mean", "median", lambda x: (x > 0).mean(), "min"]).reset_index()
    stop_maand.columns = ["config", "stop", "potje_gem", "potje_mediaan", "winstgevend", "slechtste_trader_maand"]
    for k in ("potje_gem", "potje_mediaan", "winstgevend", "slechtste_trader_maand"):
        stop_maand[k] = (100 * stop_maand[k]).round(1)
    stop_sam = stop_sam.merge(stop_maand, on=["config", "stop"])
    mc = {}
    for naam, stop in itertools.product(CONFIGS, ["None", "0.3"]):
        x = df[(df.config == naam) & (df.stop == stop)]
        maanden = [g.sort_values("sluit") for _, g in x.groupby(["knip", "idx"])]
        if len(maanden) < 5:
            continue
        paden = [((g.r_met * np.minimum(100 / g.pot, 25)).cumsum().values / 100.0) for g in maanden]
        eind, dalingen = [], []
        for _ in range(5000):
            kies5 = RNG.integers(0, len(paden), 5)
            # gelijk verdeeld: elk potje 1/5; paden op een gemeenschappelijke as van 100 stappen
            as_ = np.zeros(101)
            for j in kies5:
                p = paden[j]
                if len(p):
                    as_ += np.interp(np.linspace(0, 1, 101), np.linspace(0, 1, len(p) + 1), np.r_[0, p]) / 5
            eq = 1 + as_
            eind.append(eq[-1] - 1)
            dalingen.append(float((eq / np.maximum.accumulate(eq) - 1).min()))
        eind, dalingen = np.array(eind), np.array(dalingen)
        mc[f"{naam}_stop_{stop}"] = {"trader_maanden": len(paden),
                    "maand_p5_p50_p95_pct": [round(100 * float(np.percentile(eind, q)), 1) for q in (5, 50, 95)],
                    "kans_verliesmaand_pct": round(100 * float((eind < 0).mean()), 1),
                    "daling_p50_p95_pct": [round(100 * float(np.percentile(dalingen, q)), 1) for q in (50, 5)],
                    "kans_daling_gt_20_pct": round(100 * float((dalingen < -0.20).mean()), 1),
                    "kans_daling_gt_30_pct": round(100 * float((dalingen < -0.30).mean()), 1)}
    return stop_sam, mc


def main():
    d, kraken_p, uit = sys.argv[1:4]
    os.makedirs(uit, exist_ok=True)
    kraken = set(json.load(open(kraken_p)))
    T, folds, grenzen, sym = laad(d, kraken)
    rc = reality_check(folds) if not os.environ.get("ZONDER_RC") else "overgeslagen (zie eerdere run)"
    hb, corr = houdbaarheid(T, folds, grenzen)
    hb.to_csv(f"{uit}/houdbaarheid.csv", index=False)
    st, mc = staartrisico(T, folds, grenzen, sym)
    st.to_csv(f"{uit}/stop.csv", index=False)
    uitslag = {"reality_check": rc, "rangcorrelatie": corr, "monte_carlo": mc, "n_perm": N_PERM,
               "uitleg": {"alles": "testuitkomsten gehusseld over alle accounts per knip (toetst filters + rangschikking + zoektocht)",
                          "binnen_basis": "alleen gehusseld binnen accounts die aan de basiseisen voldoen (toetst extra filters + rangschikking + zoektocht)",
                          "p": "aandeel gehusselde werelden waarin de beste regel minstens zo goed scoort als de echte; < 0,05 = waarschijnlijk geen geluk"},
               "api_calls": {str(k): v for k, v in api.CALLS.items()}}
    json.dump(uitslag, open(f"{uit}/uitslag.json", "w"), indent=1, default=str, ensure_ascii=False)
    print(json.dumps({k: uitslag[k] for k in ("monte_carlo", "rangcorrelatie")}, default=str), flush=True)
    print(hb.to_string(), flush=True)
    print(st.to_string(), flush=True)


if __name__ == "__main__":
    main()
