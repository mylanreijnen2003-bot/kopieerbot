"""Lighter edge-test: walk-forward selectie, parametergevoeligheid, baselines en uitvoeringssimulatie.

Gebruik: python -m lighter.edge <datamap met part-*> <kraken.json> <uit openbaar>

A. Walk-forward: kiezen op knip K (data < K), meten op trades geopend in [K, volgende knip).
   Knips 1-6, 1-7, 1-8 (ontwerp) en 1-9 (holdout, alleen ter controle; niet gebruikt om te kiezen).
B. Raster van instellingen (min. accountwaarde, max. posities tegelijk, handmatig-eis, rangschikking, N traders).
C. Baselines: N willekeurig uit dezelfde geschikte pool, alle geschikten, en ongefilterde actieve pool.
D. Uitvoering voor gekozen traders (Lighter 1m-candles als prijspad): markt na 1/5/15/60 min,
   limiet op de prijs van de leider met 1/5/15 min wachten (daarna markt of overslaan), spread-scenario's,
   funding, stop per trader -20%.
Rendement per trade = richting x (uit/in - 1) - kosten. Potje-% = som / p90 gelijktijdige posities (uit keuzeperiode).
Openbaar alleen afgekorte adressen en aggregaten.
"""
from __future__ import annotations

import itertools
import json
import os
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd

from lighter import api
from lighter.selectie import basis, equity_op, kort, lees, reconstrueer

DAG = api.DAG
NU = int(time.time() * 1000)
KNIPS = ["2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01"]
ONTWERP = KNIPS[:3]
HOLDOUT = KNIPS[3]
RNG = np.random.default_rng(11)
KOSTEN_LEIDER = 0.002          # zoals tot nu: 0,2% per trade (heen + terug)


def ms_dag(s):
    return int(pd.Timestamp(s).value // 10**6)


# ---------------------------------------------------------------- trades met richting

def trades_dir(fl):
    """Per munt plat -> plat. Geeft dicts met richting, tijden, eerste fill-prijs, gem. in/uit en rendement."""
    st, out = {}, []
    for f in fl:
        c, s, a, px, t = f["coin"], f["start"], f["after"], f["px"], f["time"]
        if s == 0 and a != 0:
            st[c] = {"dir": 1 if a > 0 else -1, "in_q": abs(a), "in_c": abs(a) * px, "uit_q": 0.0, "uit_c": 0.0,
                     "t": t, "px0": px}
            continue
        if c not in st:
            continue
        p = st[c]
        flip = a != 0 and a * s < 0
        if abs(a) > abs(s) and not flip:
            p["in_q"] += abs(a) - abs(s)
            p["in_c"] += (abs(a) - abs(s)) * px
        else:
            q = abs(s) if (a == 0 or flip) else abs(s) - abs(a)
            p["uit_q"] += q
            p["uit_c"] += q * px
        if a == 0 or flip:
            gin, guit = p["in_c"] / p["in_q"], p["uit_c"] / p["uit_q"]
            out.append({"coin": c, "dir": p["dir"], "open": p["t"], "sluit": t, "px0": p["px0"], "gin": gin,
                        "guit": guit, "r_bruto": p["dir"] * (guit / gin - 1)})
            st.pop(c)
            if flip:
                st[c] = {"dir": 1 if a > 0 else -1, "in_q": abs(a), "in_c": abs(a) * px, "uit_q": 0.0, "uit_c": 0.0,
                         "t": t, "px0": px}
    return out


def p90_gelijktijdig(open_t, sluit_t):
    if not len(open_t):
        return 0
    ev = sorted([(o, 1) for o in open_t] + [(s, -1) for s in sluit_t])
    n, op = 0, []
    for _, d in ev:
        n += d
        if d == 1:
            op.append(n)
    return int(np.percentile(op, 90))


# ---------------------------------------------------------------- kenmerken op een knipdatum

def kenmerken(idx, tr: pd.DataFrame, fl: pd.DataFrame, p, K, kraken):
    k = tr[tr.sluit < K]
    if len(k) < 20:
        return None
    r = k.r.values
    eerste = int(fl.ts.min())
    kf = fl[fl.ts < K]
    f30 = kf[kf.ts >= K - 30 * DAG]
    dagen = max(1, f30.ts.floordiv(DAG).nunique())
    mnd = pd.Series(r, index=pd.to_datetime(k.sluit, unit="ms").dt.strftime("%Y-%m")).groupby(level=0).sum()
    p90 = p90_gelijktijdig(k.open.values, k.sluit.values)
    pot = max(1, p90)
    r90 = k[k.sluit >= K - 90 * DAG].r.values
    kr = np.mean([basis(c) in kraken or basis(c) + "X" in kraken for c in k.coin])
    eq = equity_op(p, K)
    pnl_keuze = None
    if p is not None and len(p):
        q = p[p.ts <= K].sort_values("ts")
        a = q[q.ts <= eerste]
        if len(q):
            pnl_keuze = float(q.trade_pnl.iloc[-1] - (a.trade_pnl.iloc[-1] if len(a) else 0.0))
    return {
        "idx": idx, "n": len(r), "gem_r": r.mean(), "som": r.sum(),
        "t": r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 2 and r.std() > 0 else 0.0,
        "winst_pct": (r > 0).mean(), "winstmnd_frac": float((mnd > 0).mean()), "maanden": len(mnd),
        "p90": p90, "potje": r.sum() / pot, "potje_3m": r90.sum() / pot,
        "potje_mnd": r.sum() / pot / max(1, len(mnd)),
        "historie_d": (K - eerste) / DAG,
        "tpd30": ((k.sluit >= K - 30 * DAG).sum()) / 30,
        "houd_u": float(np.median((k.sluit - k.open) / 3.6e6)),
        "maker": kf.maker.mean() if len(kf) else 0.0,
        "fpd30": len(f30) / dagen,
        "t7": int((k.sluit >= K - 7 * DAG).sum()), "t14": int((k.sluit >= K - 14 * DAG).sum()),
        "equity": eq if eq is not None else 0.0, "pnl_keuze": pnl_keuze if pnl_keuze is not None else -1.0,
        "kraken": kr,
    }


def test_uitkomst(tr: pd.DataFrame, K, Keind, pot):
    t = tr[(tr.open >= K) & (tr.open < Keind) & (tr.sluit <= NU)]
    return {"test_n": len(t), "test_som": t.r.sum(), "test_potje": t.r.sum() / max(1, pot)}


# ---------------------------------------------------------------- selectie-raster

BASIS = lambda d: (d.historie_d >= 90) & (d.n >= 100) & (d.gem_r > 0) & (d.pnl_keuze > 0) & (d.t7 >= 1) & (d.t14 >= 4) \
    & (d.maker < 0.8) & (d.fpd30 <= 150)
HANDMATIG = lambda d: (d.tpd30 <= 3) & (d.houd_u >= 2)

RASTER = {
    "min_equity": [0, 1000, 5000, 10000, 25000],
    "max_pos": [5, 10, 20, 999],
    "handmatig": [True, False],
    "rang": ["potje", "potje_3m", "gem_r", "t", "winstmnd_frac"],
    "N": [5, 10, 20],
}


def selecteer(d: pd.DataFrame, cfg):
    m = BASIS(d) & (d.equity >= cfg["min_equity"]) & (d.p90 <= cfg["max_pos"])
    if cfg["handmatig"]:
        m &= HANDMATIG(d)
    g = d[m]
    sleutel = [cfg["rang"], "potje"] if cfg["rang"] != "potje" else ["potje"]
    return g.sort_values(sleutel, ascending=False), g


def beoordeel_cfg(folds, cfg):
    """folds: {knip: DataFrame kenmerken+test}. Geeft per knip echte en willekeurige uitkomst."""
    out = {}
    for K, d in folds.items():
        g_sorted, g = selecteer(d, cfg)
        n = cfg["N"]
        if len(g) < n:
            out[K] = {"geschikt": len(g), "echt": None}
            continue
        top = g_sorted.head(n)
        echt = float(top.test_potje.mean())
        tp = g.test_potje.values
        rnd = np.array([tp[RNG.choice(len(tp), size=n, replace=False)].mean() for _ in range(500)])
        out[K] = {"geschikt": len(g), "echt": echt, "winnaars": int((top.test_potje > 0).sum()),
                  "willekeurig": float(rnd.mean()), "pctl": float((rnd < echt).mean() * 100),
                  "alle_geschikt": float(tp.mean())}
    return out


# ---------------------------------------------------------------- uitvoeringssimulatie

def candle_rij(c):
    """Lighter-candle -> (t_ms, o, h, l, c); veldnamen flexibel."""
    def g(*ks):
        for k in ks:
            if k in c and c[k] not in (None, ""):
                return float(c[k])
        return None
    t = g("timestamp", "t", "start_timestamp", "time")
    return (api.ms(t) if t else None, g("open", "o"), g("high", "h"), g("low", "l"), g("close", "c"))


class Prijzen:
    def __init__(self):
        self.cache = {}         # (market, blok) -> DataFrame
        self.calls = 0

    def blok(self, m, t):
        b = t // (480 * 60000)
        key = (m, b)
        if key not in self.cache:
            start = b * 480 * 60000
            st, d = api.get("candles", {"market_id": m, "resolution": "1m", "start_timestamp": start,
                                        "end_timestamp": start + 480 * 60000, "count_back": 500})
            self.calls += 1
            lijst = []
            if isinstance(d, dict):
                for v in d.values():
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        lijst = v
                        break
            rows = [candle_rij(c) for c in lijst]
            df = pd.DataFrame([r for r in rows if r[0]], columns=["t", "o", "h", "l", "c"]).sort_values("t") \
                if rows else pd.DataFrame(columns=["t", "o", "h", "l", "c"])
            self.cache[key] = df.set_index("t") if len(df) else df
        return self.cache[key]

    def venster(self, m, t0, t1):
        dfs = [self.blok(m, t) for t in range(t0 - t0 % (480 * 60000), t1 + 1, 480 * 60000)]
        dfs = [x for x in dfs if len(x)]
        if not dfs:
            return pd.DataFrame(columns=["o", "h", "l", "c"])
        d = pd.concat(dfs)
        d = d[~d.index.duplicated()]
        return d[(d.index >= t0 - 60000) & (d.index <= t1)]

    def koers(self, m, t):
        """Slotkoers van de minuut waarin t valt (of laatst bekende daarvoor)."""
        d = self.venster(m, t - 30 * 60000, t)
        if not len(d):
            return None
        d = d[d.index <= t - t % 60000]
        return float(d.c.iloc[-1]) if len(d) else None


VERTRAGING_MIN = [0, 1, 5, 15, 60]          # 0 = binnen dezelfde minuut (~1-5 s): prijs leider
LIMIET_MIN = [1, 5, 15]
HALF_SPREAD = [0.0002, 0.0005, 0.0010]
TAKER, MAKER = 0.0005, 0.0002
FUNDING_8U = 0.0001


def simuleer(trade, m, P: Prijzen):
    """Geeft dict variant -> netto rendement (None = overgeslagen) voor één trade. Kosten per kant apart."""
    d, t_in, t_uit = trade["dir"], trade["open"], trade["sluit"]
    gin, guit, px0 = trade["gin"], trade["guit"], trade["px0"]
    ref_in, ref_uit = P.koers(m, t_in), P.koers(m, t_uit)
    if not ref_in or not ref_uit:
        return None
    uren = (t_uit - t_in) / 3.6e6
    res = {}

    def prijs_na(t, ref, lead, minuten):
        if minuten == 0:
            return lead
        k = P.koers(m, t + minuten * 60000)
        return lead * k / ref if k else None

    for hs in HALF_SPREAD:
        taker = TAKER + hs
        for f in (0.0, FUNDING_8U):
            fund = f * uren / 8
            # markt in en uit met vertraging v
            for v in VERTRAGING_MIN:
                pin, puit = prijs_na(t_in, ref_in, gin, v), prijs_na(t_uit, ref_uit, guit, v)
                if pin and puit:
                    res[("markt", v, hs, f)] = d * (puit / pin - 1) - 2 * taker - fund
            # limiet-entry op eerste fill-prijs leider, wacht w min (vanaf volgende minuut, prijs moet er doorheen)
            puit1 = prijs_na(t_uit, ref_uit, guit, 1)
            if not puit1:
                continue
            for w in LIMIET_MIN:
                c = P.venster(m, t_in - t_in % 60000 + 60000, t_in + w * 60000)
                gevuld = len(c) and ((d > 0 and (c.l < px0).any()) or (d < 0 and (c.h > px0).any()))
                if gevuld:
                    pin = gin * 1.0           # instap op limiet = prijs leider (gemiddeld)
                    r = d * (puit1 / pin - 1) - MAKER - taker - fund
                    res[("limiet_markt", w, hs, f)] = r
                    res[("limiet_skip", w, hs, f)] = r
                else:
                    pin = prijs_na(t_in, ref_in, gin, w)
                    res[("limiet_markt", w, hs, f)] = d * (puit1 / pin - 1) - 2 * taker - fund if pin else None
                    res[("limiet_skip", w, hs, f)] = None
                res[("gevuld", w, hs, f)] = bool(gevuld)
    return res


# ---------------------------------------------------------------- main

def main():
    d, kraken_p, uit = sys.argv[1:4]
    os.makedirs(uit, exist_ok=True)
    kraken = set(json.load(open(kraken_p)))
    scan, fills, pnl, accs, meta = lees(d)
    sym = api.markten()
    sym_inv = {v: k for k, v in sym.items()}
    fills = fills[~fills.kind.fillna("perp").astype(str).str.lower().str.contains("spot")]
    pnl_g = {i: g for i, g in pnl.groupby("idx")} if len(pnl) else {}
    print(f"accounts met fills: {fills.idx.nunique()}", flush=True)

    # 1) trades per account
    T, F = {}, {}
    for idx, f in fills.groupby("idx"):
        fl, _ = reconstrueer(f, sym)
        tr = trades_dir(fl)
        if len(tr) >= 20:
            t = pd.DataFrame(tr)
            t["r"] = t.r_bruto - KOSTEN_LEIDER
            T[idx], F[idx] = t, f
    print(f"accounts met >= 20 trades: {len(T)}", flush=True)

    # 2) kenmerken + testuitkomst per knip
    folds = {}
    grenzen = [ms_dag(k) for k in KNIPS] + [NU]
    for i, K in enumerate(KNIPS):
        Km, Ke = grenzen[i], grenzen[i + 1]
        rows = []
        for idx, t in T.items():
            k = kenmerken(idx, t, F[idx], pnl_g.get(idx), Km, kraken)
            if k:
                k.update(test_uitkomst(t, Km, Ke, k["p90"]))
                rows.append(k)
        folds[K] = pd.DataFrame(rows)
        dd = folds[K]
        print(f"knip {K}: {len(dd)} accounts, basis-geschikt {int(BASIS(dd).sum())}", flush=True)

    # 3) baselines per knip
    baselines = {}
    for K, dd in folds.items():
        actief = dd[(dd.t7 >= 1) & (dd.test_n > 0)]
        b = BASIS(dd) & (dd.test_n >= 0)
        baselines[K] = {
            "actief_ongefilterd": {"n": len(actief), "potje_gem": round(float(actief.test_potje.mean()) * 100, 2),
                                   "winstgevend_pct": round(float((actief.test_potje > 0).mean()) * 100, 1)},
            "basis_eisen": {"n": int(b.sum()), "potje_gem": round(float(dd[b].test_potje.mean()) * 100, 2),
                            "winstgevend_pct": round(float((dd[b].test_potje > 0).mean()) * 100, 1)},
        }

    # 4) raster
    rijen = []
    for combo in itertools.product(*RASTER.values()):
        cfg = dict(zip(RASTER.keys(), combo))
        res = beoordeel_cfg(folds, cfg)
        rij = dict(cfg)
        ontw = [res[K]["echt"] for K in ONTWERP if res[K]["echt"] is not None]
        rij["ontwerp_folds"] = len(ontw)
        rij["ontwerp_gem_pct"] = round(100 * np.mean(ontw), 2) if ontw else None
        rij["ontwerp_min_pct"] = round(100 * np.min(ontw), 2) if ontw else None
        rij["ontwerp_pctl_gem"] = round(np.mean([res[K]["pctl"] for K in ONTWERP if res[K]["echt"] is not None]), 0) if ontw else None
        for K in KNIPS:
            r = res[K]
            rij[f"{K}_geschikt"] = r["geschikt"]
            rij[f"{K}_pct"] = round(100 * r["echt"], 2) if r["echt"] is not None else None
            rij[f"{K}_willek_pct"] = round(100 * r["willekeurig"], 2) if r["echt"] is not None else None
            rij[f"{K}_pctl"] = round(r["pctl"]) if r["echt"] is not None else None
            rij[f"{K}_winnaars"] = r.get("winnaars")
        rijen.append(rij)
    raster = pd.DataFrame(rijen)
    raster.to_csv(f"{uit}/raster.csv", index=False)
    # robuuste keuze: alle 3 ontwerp-folds aanwezig, sorteer op minimum, dan gemiddelde
    kandid = raster[raster.ontwerp_folds >= 2].sort_values(["ontwerp_min_pct", "ontwerp_gem_pct"], ascending=False)
    beste = kandid.head(10)
    standaard = raster[(raster.min_equity == 5000) & (raster.max_pos == 10) & (raster.handmatig) & (raster.rang == "potje")
                       & (raster.N == 5)]

    # 5) uitvoeringssimulatie voor gekozen traders (standaard + beste robuuste instelling), alle knips
    keuzes = {"standaard": dict(min_equity=5000, max_pos=10, handmatig=True, rang="potje", N=10)}
    if len(beste):
        b0 = beste.iloc[0]
        keuzes["beste_ontwerp"] = {k: (b0[k].item() if hasattr(b0[k], "item") else b0[k]) for k in RASTER}
    P = Prijzen()
    sim_rijen, trader_rijen = [], []
    t_start = time.time()
    for naam, cfg in keuzes.items():
        for i, K in enumerate(KNIPS):
            Km, Ke = grenzen[i], grenzen[i + 1]
            g_sorted, _ = selecteer(folds[K], cfg)
            top = g_sorted.head(cfg["N"])
            for r in top.itertuples():
                t = T[r.idx]
                tt = t[(t.open >= Km) & (t.open < Ke) & (t.sluit <= NU)]
                pot = max(1, r.p90)
                trader_rijen.append({"keuze": naam, "knip": K, "adres": kort((accs.get(int(r.idx)) or {}).get("l1")),
                                     "keuze_potje_pct": round(100 * r.potje, 1), "p90": r.p90,
                                     "equity_knip": round(r.equity), "test_trades": len(tt),
                                     "test_potje_leider_pct": round(100 * tt.r.sum() / pot, 1), "kraken_pct": round(100 * r.kraken)})
                for tr in tt.to_dict("records"):
                    m = sym_inv.get(tr["coin"])
                    if m is None or time.time() - t_start > 4 * 3600:
                        continue
                    s = simuleer(tr, m, P)
                    if not s:
                        continue
                    base = {"keuze": naam, "knip": K, "idx": r.idx, "pot": pot, "r_leider": tr["r"], "coin": tr["coin"],
                            "uren": (tr["sluit"] - tr["open"]) / 3.6e6}
                    for (var, par, hs, f), v in s.items():
                        sim_rijen.append({**base, "variant": var, "par": par, "half_spread": hs, "funding": f, "r": v})
            print(f"sim {naam} {K}: candle-calls {P.calls}", flush=True)
    sim = pd.DataFrame(sim_rijen)
    pd.DataFrame(trader_rijen).to_csv(f"{uit}/gekozen_traders.csv", index=False)

    # samenvatting uitvoering: per keuze, variant, par, spread, funding
    samen = []
    if len(sim):
        rr = sim[sim.variant != "gevuld"]
        for key, g in rr.groupby(["keuze", "variant", "par", "half_spread", "funding"]):
            gg = g.dropna(subset=["r"])
            per_trader = gg.groupby(["knip", "idx"]).apply(lambda x: x.r.astype(float).sum() / x.pot.iloc[0])
            # stop -20%: per trader per knip cumulatief, daarna niet meer volgen
            def met_stop(x):
                cum, tot = 0.0, 0.0
                for v in x.r.astype(float) / x.pot.iloc[0]:
                    tot += v
                    if tot <= -0.20:
                        return tot
                return tot
            per_trader_stop = gg.groupby(["knip", "idx"]).apply(met_stop)
            samen.append({"keuze": key[0], "variant": key[1], "par_min": key[2], "half_spread_pct": key[3] * 100,
                          "funding": key[4], "trades": len(gg), "overgeslagen": int(g.r.isna().sum()),
                          "gem_r_pct": round(100 * gg.r.astype(float).mean(), 3) if len(gg) else None,
                          "potje_per_trader_maand_pct": round(100 * per_trader.mean(), 2) if len(per_trader) else None,
                          "potje_met_stop_pct": round(100 * per_trader_stop.mean(), 2) if len(per_trader_stop) else None,
                          "trader_maanden_winst_pct": round(100 * (per_trader > 0).mean(), 1) if len(per_trader) else None})
        gev = sim[sim.variant == "gevuld"]
        if len(gev):
            # adverse selection: rendement leider op gevulde vs niet-gevulde limieten
            x = gev[(gev.half_spread == HALF_SPREAD[1]) & (gev.funding == 0)]
            for (kz, w), g in x.groupby(["keuze", "par"]):
                f = g.r.astype(bool)
                samen.append({"keuze": kz, "variant": "limiet_vulkans", "par_min": w, "trades": len(g),
                              "vulkans_pct": round(100 * f.mean(), 1),
                              "leider_r_gevuld_pct": round(100 * g[f].r_leider.mean(), 3) if f.any() else None,
                              "leider_r_niet_gevuld_pct": round(100 * g[~f].r_leider.mean(), 3) if (~f).any() else None})
    pd.DataFrame(samen).to_csv(f"{uit}/uitvoering.csv", index=False)

    uitslag = {
        "knips": KNIPS, "holdout": HOLDOUT, "nu": pd.Timestamp(NU, unit="ms").strftime("%Y-%m-%d"),
        "accounts_met_20_trades": len(T),
        "baselines": baselines,
        "standaard_instelling": standaard.to_dict("records"),
        "beste_robuust_ontwerp_top10": beste.to_dict("records"),
        "raster_combinaties": len(raster),
        "raster_aandeel_ontwerp_positief": round(float((raster.ontwerp_min_pct > 0).mean()) * 100, 1),
        "raster_holdout_positief_pct": round(float((raster[f"{HOLDOUT}_pct"] > 0).mean()) * 100, 1),
        "raster_holdout_boven_willekeurig_pct": round(float((raster[f"{HOLDOUT}_pctl"] > 50).mean()) * 100, 1),
        "beste_ontwerp_holdout": beste[[f"{HOLDOUT}_pct", f"{HOLDOUT}_willek_pct", f"{HOLDOUT}_pctl"]].to_dict("records"),
        "candle_calls": P.calls,
        "api_calls": {str(k): v for k, v in api.CALLS.items()},
    }
    json.dump(uitslag, open(f"{uit}/uitslag.json", "w"), indent=1, default=float, ensure_ascii=False)
    print("klaar", json.dumps({k: uitslag[k] for k in ("accounts_met_20_trades", "raster_aandeel_ontwerp_positief",
                                                     "raster_holdout_positief_pct", "candle_calls")}), flush=True)


if __name__ == "__main__":
    main()
