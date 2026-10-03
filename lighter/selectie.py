"""Lighter: kiezen op vaste inzet per trade (stap 3-7), met Mylans criteria.

Rendement per trade = richting x (gem. uitstap / gem. instap - 1) - 0,2% kosten. Alleen posities vanaf plat.
Keuzeperiode t/m KNIP, test = trades geopend vanaf KNIP t/m nu. Alleen de test telt.
Eisen (op de keuzeperiode): >= 3 mnd fills-historie, >= 100 trades, gem. netto rendement/trade > 0,
dag-PnL incl. ongerealiseerd (Lighter `pnl`, gecorrigeerd voor stortingen) > 0, actief (>= 1 trade in 7 d, >= 4 in 14 d),
<= 3 trades/dag (30 d), mediane houdtijd >= 2 u, geen market maker (< 80% maker-fills, <= 150 fills/dag).
Rangschikken op som van netto rendementen per trade (= totaal % bij vaste inzet). Winst-% alleen informatie.
Kraken is geen eis: per trader het aandeel trades in munten met een Kraken-perp vs. alleen op Lighter.

Gebruik: python -m lighter.selectie <datamap met part-*> <kraken.json> <uit openbaar> <uit privé>
Openbaar: alleen afgekorte adressen. Privé (wordt versleuteld): volledige adressen en indexen.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import time

import numpy as np
import pandas as pd

from bt.vast_select import maanden, stats, trades_pct
from lighter import api

DAG = api.DAG
KNIP = int(pd.Timestamp("2026-08-01").value // 10**6)
NU = int(time.time() * 1000) // DAG * DAG
TOP = 5
RNG = np.random.default_rng(7)


def kort(a):
    return f"{a[:6]}…{a[-4:]}" if isinstance(a, str) and len(a) > 12 else "onbekend"


def basis(sym: str) -> str:
    s = re.sub(r"^(1000000|1000|1M|k)(?=[A-Z])", "", sym or "")
    return s.replace("USD", "") if s.endswith("USD") and len(s) > 6 else s


def lees(d):
    parts = sorted(glob.glob(f"{d}/**/scan.parquet", recursive=True))
    roots = [os.path.dirname(p) for p in parts]
    scan = pd.concat([pd.read_parquet(f"{r}/scan.parquet") for r in roots], ignore_index=True)
    fills = pd.concat([pd.read_parquet(f"{r}/fills.parquet") for r in roots if os.path.getsize(f"{r}/fills.parquet") > 0
                       and len(pd.read_parquet(f"{r}/fills.parquet"))], ignore_index=True)
    pnl = [pd.read_parquet(f"{r}/pnl.parquet") for r in roots]
    pnl = pd.concat([p for p in pnl if len(p)], ignore_index=True) if any(len(p) for p in pnl) else pd.DataFrame()
    accs, meta = {}, []
    for r in roots:
        accs.update({int(k): v for k, v in json.load(open(f"{r}/accounts.json")).items()})
        meta.append(json.load(open(f"{r}/meta.json")))
    return scan, fills, pnl, accs, meta


def reconstrueer(f: pd.DataFrame, sym: dict):
    """Fills van één account -> fills met start/after (signed), alleen vanaf een bewezen plat moment per markt."""
    f = f.sort_values(["ts", "tid"], kind="stable")
    loop, uit, mis = {}, [], 0
    for r in f.itertuples(index=False):
        s = r.size if r.koper else -r.size
        m = r.market_id
        voor = r.pos_voor
        if voor is not None and not np.isnan(voor) and abs(voor) < 1e-12:
            loop[m] = 0.0                              # bewezen plat
        if m not in loop:
            continue
        if voor is not None and not np.isnan(voor) and abs(abs(loop[m]) - abs(voor)) > 1e-6 * max(1, abs(voor)):
            mis += 1                                   # klopt niet met Lighter's eigen positie: opnieuw beginnen
            loop.pop(m)
            continue
        start = loop[m]
        after = start + s
        if abs(after) < 1e-12:
            after = 0.0
        uit.append({"time": int(r.ts), "coin": sym.get(m, str(m)), "start": start, "after": after, "px": r.px})
        loop[m] = after
    return uit, mis


def gelijktijdig_p90(tr):
    if not tr:
        return 0
    ev = sorted([(t[1], 1) for t in tr] + [(t[2], -1) for t in tr])
    n, op_open = 0, []
    for _, d in ev:
        n += d
        if d == 1:
            op_open.append(n)
    return int(np.percentile(op_open, 90))


def pnl_delta(p: pd.DataFrame, van, tot):
    if p is None or not len(p):
        return None
    p = p.sort_values("ts")
    corr = p.trade_pnl.values
    t = p.ts.values
    a = corr[t <= van]
    b = corr[t <= tot]
    if not len(b):
        return None
    return float(b[-1] - (a[-1] if len(a) else 0.0))


def pnl_maanden(p: pd.DataFrame, van, tot):
    if p is None or not len(p):
        return {}
    p = p[(p.ts >= van - DAG) & (p.ts <= tot)].sort_values("ts")
    if len(p) < 2:
        return {}
    s = pd.Series(p.trade_pnl.values, index=pd.to_datetime(p.ts, unit="ms").dt.strftime("%Y-%m"))
    eind = s.groupby(level=0).last()
    return {k: round(float(v), 0) for k, v in eind.diff().dropna().items()}


def beoordeel(idx, tr, fl_acc, p_acc, knip, kraken):
    """Statistieken en eisen per account op een knipdatum."""
    keuze = [t for t in tr if t[2] < knip]
    test = [t for t in tr if t[1] >= knip]
    if not keuze:
        return None
    eerste = int(fl_acc.ts.min())
    kf = fl_acc[fl_acc.ts < knip]
    f30 = kf[kf.ts >= knip - 30 * DAG]
    dagen_actief = max(1, f30.ts.floordiv(DAG).nunique())
    r30 = [t for t in keuze if t[2] >= knip - 30 * DAG]
    uren = [(t[2] - t[1]) / 3.6e6 for t in keuze]
    k = stats(keuze)
    kr = np.mean([basis(t[0]) in kraken or basis(t[0]) + "X" in kraken for t in keuze])
    rij = {
        "idx": idx,
        "historie_dagen": round((knip - eerste) / DAG),
        **{f"keuze_{n}": v for n, v in k.items()},
        "keuze_pnl_dag_usd": pnl_delta(p_acc, eerste, knip),
        "keuze_maker_pct": round(100 * kf.maker.mean(), 1) if len(kf) else None,
        "fills_per_dag_30d": round(len(f30) / dagen_actief, 1),
        "trades_7d": sum(t[2] >= knip - 7 * DAG for t in keuze),
        "trades_14d": sum(t[2] >= knip - 14 * DAG for t in keuze),
        "trades_per_dag_30d": round(len(r30) / 30, 2),
        "houdtijd_mediaan_uur": round(float(np.median(uren)), 1),
        "gelijktijdig_p90": gelijktijdig_p90(keuze),
        "kraken_pct": round(100 * kr, 0),
        "lighter_only_pct": round(100 * (1 - kr), 0),
        "keuze_maanden": json.dumps({m: round(v, 1) for m, v in maanden(keuze).items()}),
        "munten": ", ".join(f"{c} {100 * v:.0f}%" for c, v in pd.Series([t[0] for t in keuze]).value_counts(normalize=True).head(4).items()),
    }
    rij["eis_historie"] = rij["historie_dagen"] >= 90
    rij["eis_100"] = k["trades"] >= 100
    rij["eis_rendement"] = k.get("gem_rendement_pct", -1) > 0
    rij["eis_pnl_dag"] = (rij["keuze_pnl_dag_usd"] or 0) > 0
    rij["eis_actief"] = rij["trades_7d"] >= 1 and rij["trades_14d"] >= 4
    rij["eis_handmatig"] = rij["trades_per_dag_30d"] <= 3 and rij["houdtijd_mediaan_uur"] >= 2
    rij["eis_geen_mm"] = (rij["keuze_maker_pct"] or 0) < 80 and rij["fills_per_dag_30d"] <= 150
    rij["geschikt"] = all(rij[c] for c in rij if c.startswith("eis_"))
    if test:
        te = stats(test)
        rij.update({f"test_{n}": v for n, v in te.items()})
        rij["test_maanden"] = json.dumps({m: round(v, 1) for m, v in maanden(test).items()})
    else:
        rij["test_trades"] = 0
        rij["test_som_pct"] = 0.0
    rij["test_pnl_dag_usd"] = pnl_delta(p_acc, knip, NU)
    rij["test_pnl_dag_maanden"] = json.dumps(pnl_maanden(p_acc, knip, NU))
    return rij


TRECHTER = ["eis_historie", "eis_100", "eis_rendement", "eis_pnl_dag", "eis_actief", "eis_handmatig", "eis_geen_mm"]


def trechter(tab):
    out, m = {"accounts_met_trades": int(len(tab))}, pd.Series(True, index=tab.index)
    for c in TRECHTER:
        m &= tab[c]
        out[c] = int(m.sum())
    return out


def overzicht(tr, fl_acc, p_acc, acc, nu):
    """Extra kolommen voor de lijst van vandaag: % per maand (vaste inzet), 3 en 6 mnd, dalingen, posities."""
    eerste = int(fl_acc.ts.min())
    maand_nu = pd.Timestamp(nu, unit="ms").to_period("M")
    maanden_lijst = [(maand_nu - k).strftime("%Y-%m") for k in range(6, -1, -1)]
    start_maand = pd.Timestamp(eerste, unit="ms").strftime("%Y-%m")
    per_m = maanden(tr)
    out = {"data_vanaf": pd.Timestamp(eerste, unit="ms").strftime("%Y-%m-%d"),
           "laatste_trade": pd.Timestamp(max(t[2] for t in tr), unit="ms").strftime("%Y-%m-%d")}
    for m in maanden_lijst:
        out[f"pct_{m}"] = round(float(per_m.get(m, 0.0)), 1) if m >= start_maand else None
    for naam, dagen in (("3m", 91), ("6m", 182)):
        r = [t for t in tr if t[2] >= nu - dagen * DAG]
        rr = np.array([t[3] for t in r])
        out[f"som_{naam}_pct"] = round(100 * rr.sum(), 1) if len(rr) else 0.0
        out[f"winstgevend_{naam}"] = bool(len(rr) and rr.sum() > 0)
        out[f"volledig_{naam}"] = eerste <= nu - dagen * DAG
        out[f"trades_{naam}"] = len(rr)
        out[f"gem_rendement_{naam}_pct"] = round(100 * rr.mean(), 2) if len(rr) else None
        out[f"winst_pct_trades_{naam}"] = round(100 * (rr > 0).mean(), 1) if len(rr) else None
        out[f"trader_pnl_{naam}_usd"] = round(pnl_delta(p_acc, nu - dagen * DAG, nu) or 0)
    r6 = sorted([t for t in tr if t[2] >= nu - 182 * DAG], key=lambda t: t[2])
    cum = np.cumsum([100 * t[3] for t in r6]) if r6 else np.array([0.0])
    out["max_daling_6m_pct"] = round(float((cum - np.maximum.accumulate(np.r_[0, cum])[1:]).min()), 1)
    out["slechtste_trade_6m_pct"] = round(100 * min(t[3] for t in r6), 1) if r6 else None
    mnd_data = [m for m in maanden_lijst if out[f"pct_{m}"] is not None and (maand_nu - 6).strftime("%Y-%m") <= m < maand_nu.strftime("%Y-%m")]
    out["verliesmaanden_6m"] = sum(1 for m in mnd_data if out[f"pct_{m}"] < 0)
    eq = (acc or {}).get("equity") or 0
    ps = (acc or {}).get("posities") or []
    out["equity_usd"] = round(eq)
    out["open_posities"] = len(ps)
    upnl = sum(float(p.get("unrealized_pnl") or 0) for p in ps)
    out["ongerealiseerd_usd"] = round(upnl)
    out["hefboom_nu"] = round(sum(abs(float(p.get("position_value") or 0)) for p in ps) / eq, 2) if eq else None
    return out


def kies(tab, zonder=()):
    if not len(tab):
        return tab.assign(gekozen=pd.Series(dtype=bool), keuze_som_pct=pd.Series(dtype=float), test_som_pct=pd.Series(dtype=float))
    eisen = [c for c in TRECHTER if c not in zonder]
    g = tab[tab[eisen].all(axis=1)].sort_values("keuze_som_pct", ascending=False)
    g = g.assign(gekozen=False)
    g.loc[g.index[:TOP], "gekozen"] = True
    return g


def main():
    d, kraken_p, uit, prive = sys.argv[1:5]
    os.makedirs(uit, exist_ok=True)
    os.makedirs(prive, exist_ok=True)
    kraken = set(json.load(open(kraken_p)))
    scan, fills, pnl, accs, meta = lees(d)
    sym = api.markten()
    fills = fills[~fills.kind.fillna("perp").astype(str).str.lower().str.contains("spot")]
    pnl_g = {i: g for i, g in pnl.groupby("idx")} if len(pnl) else {}

    dekking = {"slices": len(meta), "gescand": int(sum(m["gescand"] for m in meta)),
               "slices_niet_af": sum(1 for m in meta if m.get("gestopt_bij")),
               "bestaand": int((scan.status == 200).sum()), "met_fills": int((scan.n > 0).sum()),
               "kandidaten": int(scan.get("kandidaat", pd.Series(dtype=bool)).fillna(False).sum()),
               "druk_overgeslagen": int(scan.get("druk", pd.Series(dtype=bool)).fillna(False).sum()),
               "fill_limiet_geraakt": int(scan.get("limiet_geraakt", pd.Series(dtype=bool)).fillna(False).sum()),
               "calls": {}}
    for m in meta:
        for k, v in m["calls"].items():
            dekking["calls"][k] = dekking["calls"].get(k, 0) + v
    print("dekking", json.dumps(dekking), flush=True)

    rijen_knip, rijen_nu, alle_trades, mis_tot = [], [], [], 0
    for idx, f in fills.groupby("idx"):
        fl, mis = reconstrueer(f, sym)
        mis_tot += mis
        tr = trades_pct(fl)
        if not tr:
            continue
        for t in tr:
            alle_trades.append((idx, *t))
        p = pnl_g.get(idx)
        a = beoordeel(idx, tr, f, p, KNIP, kraken)
        if a:
            rijen_knip.append(a)
        b = beoordeel(idx, tr, f, p, NU, kraken)
        if b:
            b.update(overzicht(tr, f, p, accs.get(int(idx)), NU))
            rijen_nu.append(b)
    dekking["positie_mismatches"] = mis_tot

    knip = pd.DataFrame(rijen_knip)
    nu = pd.DataFrame(rijen_nu)
    for t in (knip, nu):
        t["adres"] = [kort((accs.get(int(i)) or {}).get("l1")) for i in t.idx]
        t["l1"] = [(accs.get(int(i)) or {}).get("l1") for i in t.idx]

    # ---- stap 5-6: kiezen op knipdatum, meten in de test ----
    gk = kies(knip)
    top = gk[gk.gekozen]
    test_som = gk.test_som_pct.fillna(0).values
    rnd = [test_som[RNG.choice(len(test_som), size=min(TOP, len(test_som)), replace=False)].mean()
           for _ in range(2000)] if len(test_som) >= TOP else []
    uitslag = {
        "knip": "2026-08-01", "test_tot": pd.Timestamp(NU, unit="ms").strftime("%Y-%m-%d"),
        "trechter_knip": trechter(knip) if len(knip) else {},
        "top5_test_som_pct_gem_per_trader": round(float(top.test_som_pct.fillna(0).mean()), 1) if len(top) else None,
        "top5_test_gem_rendement_pct": round(float(top.test_gem_rendement_pct.fillna(0).mean()), 2) if "test_gem_rendement_pct" in top and len(top) else None,
        "top5_met_test_trades": int((top.test_trades.fillna(0) > 0).sum()) if len(top) else 0,
        "geschikt_alle_test_som_pct_gem": round(float(np.mean(test_som)), 1) if len(test_som) else None,
        "willekeurig5_test_som_pct_gem": round(float(np.mean(rnd)), 1) if rnd else None,
        "top5_percentiel_vs_willekeurig5": round(float(100 * np.mean(np.array(rnd) < top.test_som_pct.fillna(0).mean())), 0) if rnd else None,
    }

    # ---- vandaag: zelfde regel op alle data t/m nu + open posities ----
    gn = kies(nu)
    top_nu = gn[gn.gekozen].copy()
    pos = []
    for r in top_nu.itertuples():
        acc = accs.get(int(r.idx)) or {}
        eq = acc.get("equity") or 0
        ps = acc.get("posities") or []
        bruto = sum(abs(float(p.get("position_value") or 0)) for p in ps)
        upnl = sum(float(p.get("unrealized_pnl") or 0) for p in ps)
        pos.append({"adres": r.adres, "equity_usd": round(eq), "open_posities": len(ps), "ongerealiseerd_usd": round(upnl),
                    "ongerealiseerd_pct_equity": round(100 * upnl / eq, 1) if eq else None,
                    "hefboom": round(bruto / eq, 2) if eq else None,
                    "munten": ", ".join(sorted({str(p.get("symbol")) for p in ps}))})
    uitslag["trechter_nu"] = trechter(nu) if len(nu) else {}
    uitslag["dekking"] = dekking

    kol = ["adres", "historie_dagen", "keuze_trades", "keuze_som_pct", "keuze_gem_rendement_pct", "keuze_winst_pct_trades",
           "keuze_verliesmaanden", "keuze_maanden", "keuze_pnl_dag_usd", "trades_per_dag_30d", "houdtijd_mediaan_uur",
           "gelijktijdig_p90", "kraken_pct", "lighter_only_pct", "keuze_maker_pct", "munten",
           "test_trades", "test_som_pct", "test_gem_rendement_pct", "test_winst_pct_trades", "test_verliesmaanden",
           "test_maanden", "test_pnl_dag_usd", "test_pnl_dag_maanden", "gekozen"]
    kk = [c for c in kol if c in gk.columns]
    gk.head(30)[kk].to_csv(f"{uit}/knip_top30.csv", index=False)
    vk = ["adres", "data_vanaf", "laatste_trade", "keuze_trades", "keuze_som_pct"] + \
        [c for c in nu.columns if c.startswith("pct_20")] + \
        ["som_3m_pct", "winstgevend_3m", "volledig_3m", "trades_3m", "gem_rendement_3m_pct", "winst_pct_trades_3m",
         "som_6m_pct", "winstgevend_6m", "volledig_6m", "trades_6m", "gem_rendement_6m_pct", "winst_pct_trades_6m",
         "verliesmaanden_6m", "max_daling_6m_pct", "slechtste_trade_6m_pct", "trader_pnl_3m_usd", "trader_pnl_6m_usd",
         "equity_usd", "open_posities", "ongerealiseerd_usd", "hefboom_nu", "trades_per_dag_30d", "houdtijd_mediaan_uur",
         "gelijktijdig_p90", "kraken_pct", "lighter_only_pct", "keuze_maker_pct", "munten", "eis_handmatig", "gekozen"]
    gn.head(30)[[c for c in vk if c in gn.columns]].to_csv(f"{uit}/vandaag_top30.csv", index=False)
    gb = kies(nu, zonder=("eis_handmatig",))
    gb.head(30)[[c for c in vk if c in gb.columns]].to_csv(f"{uit}/vandaag_top30_bot.csv", index=False)
    uitslag["trechter_nu_bot(zonder handmatig-eis)"] = int(len(gb))
    pd.DataFrame(pos).to_csv(f"{uit}/vandaag_top5_posities.csv", index=False)
    json.dump(uitslag, open(f"{uit}/uitslag.json", "w"), indent=1, ensure_ascii=False)

    # privé (wordt versleuteld): volledige adressen/indexen
    knip.drop(columns=["adres"]).to_parquet(f"{prive}/knip_alle.parquet")
    nu.drop(columns=["adres"]).to_parquet(f"{prive}/nu_alle.parquet")
    json.dump({"knip_top5": top[["idx", "l1"]].to_dict("records"), "vandaag_top5": top_nu[["idx", "l1"]].to_dict("records")},
              open(f"{prive}/selectie.json", "w"), indent=1, default=int)
    pd.DataFrame(alle_trades, columns=["idx", "coin", "open", "sluit", "rendement"]).to_parquet(f"{prive}/trades.parquet")
    scan[["idx", "status", "n"]].assign(l1=[(accs.get(int(i)) or {}).get("l1") for i in scan.idx]).query("n > 0").to_parquet(
        f"{prive}/pool.parquet")
    print("uitslag", json.dumps({k: v for k, v in uitslag.items() if k != "dekking"}), flush=True)


if __name__ == "__main__":
    main()
