"""Gedeelde selectie voor V2/V3/V5 (zie kansen/VOORREGISTRATIE.md): Mylans criteria, knip en eerlijke test.

Invoer per account: trades = [(coin, open_ms, close_ms, netto_rendement)], fills_ts = lijst fill-tijden,
maker_frac (of None), equity (of None).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from bt.vast_select import maanden, stats

DAG = 86_400_000
KNIP = int(pd.Timestamp("2026-08-01").value // 10**6)
TOP = 5


def kort(a: str) -> str:
    a = str(a)
    return f"{a[:6]}…{a[-4:]}" if len(a) > 12 else a


def p90_gelijktijdig(tr):
    if not tr:
        return 0
    ev = sorted([(t[1], 1) for t in tr] + [(t[2], -1) for t in tr], key=lambda x: (x[0], x[1]))
    n, open_n = 0, []
    for _, d in ev:
        n += d
        if d == 1:
            open_n.append(n)
    return int(np.percentile(open_n, 90))


def beoordeel(acc, tr, fills_ts, maker, knip, eind):
    keuze = [t for t in tr if t[2] < knip]
    test = [t for t in tr if knip <= t[1] < eind]
    if not keuze or not len(fills_ts):
        return None
    ft = np.asarray(fills_ts)
    eerste = int(ft.min())
    f30 = ft[(ft < knip) & (ft >= knip - 30 * DAG)]
    dagen = max(1, len(np.unique(f30 // DAG)))
    r30 = [t for t in keuze if t[2] >= knip - 30 * DAG]
    uren = [(t[2] - t[1]) / 3.6e6 for t in keuze]
    k = stats(keuze)
    pot = max(1, p90_gelijktijdig(keuze))
    rij = {"account": acc, "historie_dagen": round((knip - eerste) / DAG), "keuze_trades": k["trades"],
           "keuze_gem_pct": k.get("gem_rendement_pct", 0), "keuze_winst_pct_trades": k.get("winst_pct_trades", 0),
           "keuze_som_pct": k.get("som_pct", 0), "keuze_verliesmaanden": k.get("verliesmaanden", 0),
           "maker_pct": None if maker is None else round(100 * maker, 1),
           "fills_per_dag_30d": round(len(f30) / dagen, 1),
           "trades_7d": sum(t[2] >= knip - 7 * DAG for t in keuze), "trades_14d": sum(t[2] >= knip - 14 * DAG for t in keuze),
           "trades_per_dag_30d": round(len(r30) / 30, 2), "houdtijd_uur": round(float(np.median(uren)), 1),
           "p90_posities": pot, "keuze_potje_pct": round(k.get("som_pct", 0) / pot, 1),
           "munten": ", ".join(f"{c} {100 * v:.0f}%" for c, v in
                               pd.Series([t[0] for t in keuze]).value_counts(normalize=True).head(3).items())}
    rij["eis_historie"] = rij["historie_dagen"] >= 90
    rij["eis_100"] = rij["keuze_trades"] >= 100
    rij["eis_rendement"] = rij["keuze_gem_pct"] > 0
    rij["eis_actief"] = rij["trades_7d"] >= 1 and rij["trades_14d"] >= 4
    rij["eis_handmatig"] = rij["trades_per_dag_30d"] <= 3 and rij["houdtijd_uur"] >= 2
    rij["eis_geen_mm"] = (maker is None or maker <= 0.5) and rij["fills_per_dag_30d"] <= 150
    rij["eis_posities"] = pot <= 10
    rij["geschikt"] = all(v for c, v in rij.items() if c.startswith("eis_"))
    te = stats(test) if test else {"trades": 0, "som_pct": 0.0}
    rij["test_trades"] = te["trades"]
    rij["test_som_pct"] = te.get("som_pct", 0.0)
    rij["test_potje_pct"] = round(te.get("som_pct", 0.0) / pot, 1)
    rij["test_gem_pct"] = te.get("gem_rendement_pct")
    return rij


EISEN = ["eis_historie", "eis_100", "eis_rendement", "eis_actief", "eis_handmatig", "eis_geen_mm", "eis_posities"]


def rapport(naam: str, data: dict, resdir: str, privdir: str, notities: list[str] = ()):
    """data: account -> {"trades":[...], "fills_ts":[...], "maker": float|None}"""
    nu = int(time.time() * 1000)
    Path(resdir).mkdir(parents=True, exist_ok=True)
    Path(privdir).mkdir(parents=True, exist_ok=True)
    rows = [r for a, d in data.items() if (r := beoordeel(a, d["trades"], d["fills_ts"], d.get("maker"), KNIP, nu))]
    tab = pd.DataFrame(rows)
    L = [f"# {naam} — uitslag ({pd.Timestamp(nu, unit='ms'):%Y-%m-%d})", "", *notities, "",
         f"Accounts met trades vóór de knip (1-8-2026): {len(tab)}", "", "| Eis | Over |", "|---|---|"]
    if not len(tab):
        (Path(resdir) / "report.md").write_text("\n".join(L) + "\nGeen data.\n", encoding="utf-8")
        return
    m = pd.Series(True, index=tab.index)
    for c in EISEN:
        m &= tab[c]
        L.append(f"| {c} | {int(m.sum())} |")
    g = tab[tab.geschikt].sort_values("keuze_potje_pct", ascending=False)
    top = g.head(TOP)
    L += ["", f"Geschikt op de knip: **{len(g)}**", ""]
    if len(top):
        mean_top = top.test_potje_pct.mean()
        med_all = g.test_potje_pct.median()
        pos = int((top.test_potje_pct > 0).sum())
        rng = np.random.default_rng(7)
        sims = [g.test_potje_pct.sample(min(TOP, len(g)), random_state=int(rng.integers(1e9))).mean() for _ in range(2000)]
        pct = 100 * np.mean(np.array(sims) < mean_top)
        go = mean_top > 0 and pos >= 3 and mean_top > med_all
        L += ["| # | Account | Keuze trades | Keuze gem./trade | Potje keuze | Houdtijd | Pos p90 | Test trades | Test potje |",
              "|---|---|---|---|---|---|---|---|---|"]
        for j, r in enumerate(top.itertuples(), 1):
            L.append(f"| {j} | {kort(r.account)} | {r.keuze_trades} | {r.keuze_gem_pct:+.2f}% | {r.keuze_potje_pct:+.1f}% | "
                     f"{r.houdtijd_uur} u | {r.p90_posities} | {r.test_trades} | {r.test_potje_pct:+.1f}% |")
        L += ["", f"- Top 5 test (potje-%, 1-8 t/m nu): gem. {mean_top:+.1f}%, {pos} van {len(top)} positief",
              f"- Mediaan alle geschikten: {med_all:+.1f}%; top 5 zit op percentiel {pct:.0f} van willekeurige 5-tallen",
              f"- **Oordeel: {'GO' if go else 'NO-GO'}** (eis: gem. > 0, ≥ 3 van 5 positief, > mediaan)", ""]
    # beschrijvend (niet vooraf vastgelegd): zelfde regel op eerdere knips, telkens 2 maanden test
    L += ["Robuustheid (beschrijvend): zelfde regel op eerdere knips, test 2 maanden", "",
          "| Knip | Geschikt | Top 5 gem. | Top 5 positief | Mediaan geschikt |", "|---|---|---|---|---|"]
    for k in ("2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01", "2026-07-01"):
        kn = int(pd.Timestamp(k).value // 10**6)
        rr = [r for a, d in data.items() if (r := beoordeel(a, d["trades"], d["fills_ts"], d.get("maker"), kn, kn + 61 * DAG))]
        tk = pd.DataFrame(rr)
        if not len(tk) or not tk.geschikt.any():
            L.append(f"| {k} | 0 | – | – | – |")
            continue
        gk = tk[tk.geschikt].sort_values("keuze_potje_pct", ascending=False)
        t5 = gk.head(TOP)
        L.append(f"| {k} | {len(gk)} | {t5.test_potje_pct.mean():+.1f}% | {int((t5.test_potje_pct > 0).sum())} van {len(t5)} | "
                 f"{gk.test_potje_pct.median():+.1f}% |")
    L.append("")
    # keuze vandaag: zelfde regel met knip = nu
    rows2 = [r for a, d in data.items() if (r := beoordeel(a, d["trades"], d["fills_ts"], d.get("maker"), nu, nu))]
    t2 = pd.DataFrame(rows2)
    if len(t2):
        g2 = t2[t2.geschikt].sort_values("keuze_potje_pct", ascending=False)
        L += [f"## Volgbaar vandaag: {len(g2)}", "", "| # | Account | Trades | Gem./trade | Winst-% | Potje-% | Verliesmnd | Houdtijd | Munten |",
              "|---|---|---|---|---|---|---|---|---|"]
        for j, r in enumerate(g2.head(15).itertuples(), 1):
            L.append(f"| {j} | {kort(r.account)} | {r.keuze_trades} | {r.keuze_gem_pct:+.2f}% | {r.keuze_winst_pct_trades}% | "
                     f"{r.keuze_potje_pct:+.1f}% | {r.keuze_verliesmaanden} | {r.houdtijd_uur} u | {r.munten} |")
        g2.to_csv(Path(privdir) / "volgbaar_vandaag.csv", index=False)
    (Path(resdir) / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    tab.assign(account=tab.account.map(kort)).to_csv(Path(resdir) / "alle_knip.csv", index=False)
    tab.to_csv(Path(privdir) / "alle_knip.csv", index=False)
    print(f"{naam}: {len(tab)} accounts, {len(g)} geschikt")
