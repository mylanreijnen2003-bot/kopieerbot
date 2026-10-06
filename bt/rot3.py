"""Rotatie-test 3: rendement, maar alleen consistent rendement; wisselen per trader (vooraf vastgelegd 6 okt 2026, 12:50 NL).

Vraag (Mylan): top 10 op rendement, maar zonder gelukstreffers (1-5 trades van +100% tellen niet), en een trader pas
wisselen als hij slechter wordt dan de rest. Werkt dat beter dan alleen de ranglijst?

Data: zelfde universum en fills als `rotatie` (run 37197767731), bot-basis (instap eerste fill, uitstap hun gem.,
0,32% kosten), keuzemomenten elke 14 d vanaf 20-10-2025, vooruit 14 d, potje-stop -20% binnen de periode, N = 10.

Stap `stats` (per deel): per trader, per T, per L (42/84 d) met >= 30 trades in [T-L, T):
  per_maand_pct      = som netto r / K / maanden                         (gewoon rendement)
  cons_maand_pct     = idem zonder de 3 beste trades                      (consistent rendement)
  top3_aandeel       = winst van de 3 beste trades / totale winst van winnende trades
  winstweken_pct     = aandeel weken (met trades) met netto plus
  max_trade_pct      = beste trade
  + winst_pct, gem_r_pct, houdtijd, K, kraken_pct, fills_per_dag, dagen_sinds_laatste, v14_pct (vooruit).

Stap `kies`: varianten = universum x score x wisselregel x L.
  universum  basis  : fills/dag <= 150, K <= 5, >= 70% Kraken, laatste trade <= 7 d, per_maand > 0
             streng : basis + houdtijd >= 12 u + gem. >= 1% per trade (= groep F-filter)
  score      rendement   : per_maand_pct (oude regel)
             consistent  : cons_maand_pct, alleen als top3_aandeel <= 0,5 en winstweken >= 60% en cons > 0
             constantheid: winst_pct (groep F)
  wissel     lijst      : elke 14 d top 10, wie uit de top 10 valt gaat eruit (oude regel)
             buffer     : blijft zolang hij in de top 20 staat (en door de filters komt)
             eruit      : lijst + eruit bij potje sinds instap <= -20%, -25% vanaf piek, of geen data/actief
             buffer+eruit
  Lege plekken: aanvullen van boven af met wie nog niet in de groep zit.
Uitkomst per variant: gem. %/mnd, % positieve perioden, slechtste periode, totaal, wissels per periode, en
dezelfde cijfers voor 'alle' (gemiddelde van het universum). Oordeel: consistent+buffer/eruit is pas beter dan
rendement-lijst als het in beide L's en beide universa beter is. Telt 48 varianten (meervoudig testen!).
Gebruik: python -m bt.rot3 stats <deel> <aantal> <universe> <fillsmap> <beurzenmap> <uit> | kies <statsmap> <uit>
"""

from __future__ import annotations

import glob
import os
import sys

import numpy as np
import pandas as pd

from bt import data
from bt.bot_stats import KOSTEN, bot_trades
from bt.engine import to_base
from bt.rot_stats import DAG, EIND, TS, k90, vooruit

LS = [42, 84]
H = 14
N = 10


def stats(shard, n, upath, d, vdir, out):
    os.makedirs(out, exist_ok=True)
    kraken = set(data.beurzen(vdir).get("kraken", []))
    u = pd.read_parquet(upath).sort_values("address").iloc[shard::n]
    rows = []
    for chunk in np.array_split(u.address.values, max(1, len(u) // 400)):
        f = data.fills(d, set(chunk))
        f = f[f.ts < EIND]
        for a, x in f.groupby("address"):
            fl = [{"time": int(t), "coin": c, "start": s, "after": af, "px": p}
                  for t, c, s, af, p in zip(x.ts, x.coin, x.start, x.after, x.px)
                  if not str(c).startswith(("#", "@"))]
            bt_ = bot_trades(fl)
            if len(bt_) < 30:
                continue
            o = np.array([t[1] for t in bt_], dtype=np.int64)
            c = np.array([t[2] for t in bt_], dtype=np.int64)
            r = np.array([t[3] * (t[6] / t[4] - 1) - KOSTEN for t in bt_])
            kr = np.array([to_base(t[0]) in kraken for t in bt_])
            fts = x.ts.values
            for T in TS:
                for L in LS:
                    m = (o >= T - L * DAG) & (c < T)
                    nn = int(m.sum())
                    if nn < 30:
                        continue
                    lo, lc, lr = o[m], c[m], r[m]
                    k = k90(lo, lc)
                    mnd = L / 30.44
                    srt = np.sort(lr)[::-1]
                    pos = lr[lr > 0].sum()
                    wk = pd.Series(lr).groupby((lc - (T - L * DAG)) // (7 * DAG)).sum()
                    fm = fts[(fts >= T - L * DAG) & (fts < T)]
                    fwd = (o >= T) & (o < T + H * DAG)
                    idx = np.argsort(c[fwd])
                    tot, gestopt = vooruit(r[fwd][idx], k)
                    rows.append({
                        "address": a, "T": T, "L": L, "n": nn, "K": k,
                        "per_maand_pct": round(100 * lr.sum() / k / mnd, 3),
                        "cons_maand_pct": round(100 * srt[3:].sum() / k / mnd, 3),
                        "top3_aandeel": round(float(srt[:3][srt[:3] > 0].sum() / pos), 3) if pos > 0 else 1.0,
                        "winstweken_pct": round(100 * float((wk > 0).mean()), 1),
                        "max_trade_pct": round(100 * float(srt[0]), 2),
                        "winst_pct": round(100 * (lr > 0).mean(), 1), "gem_r_pct": round(100 * lr.mean(), 3),
                        "houdtijd_uur": round(float(np.median((lc - lo) / 3.6e6)), 2),
                        "kraken_pct": round(100 * kr[m].mean(), 1),
                        "fills_per_dag": float(pd.Series(fm // DAG).value_counts().median()) if len(fm) else 0.0,
                        "dagen_sinds_laatste": round((T - lc.max()) / DAG, 1),
                        "v14_pct": round(100 * tot, 3), "v14_n": int(fwd.sum()), "v14_stop": gestopt})
        print(f"{len(rows)} rijen", flush=True)
    pd.DataFrame(rows).to_parquet(f"{out}/r3_{shard}.parquet", index=False)


def universum(g, naam):
    b = g[(g.fills_per_dag <= 150) & (g.K <= 5) & (g.kraken_pct >= 70) & (g.dagen_sinds_laatste <= 7)
          & (g.per_maand_pct > 0)]
    if naam == "streng":
        b = b[(b.houdtijd_uur >= 12) & (b.gem_r_pct >= 1.0)]
    return b


def rangorde(b, score):
    if score == "rendement":
        return b.sort_values("per_maand_pct", ascending=False)
    if score == "consistent":
        b = b[(b.top3_aandeel <= 0.5) & (b.winstweken_pct >= 60) & (b.cons_maand_pct > 0)]
        return b.sort_values("cons_maand_pct", ascending=False)
    return b.sort_values("winst_pct", ascending=False)


def simuleer(s, uni, score, wissel, L):
    held, potje, piek = [], {}, {}
    rij = []
    sL = s[s.L == L]
    for T in sorted(sL["T"].unique()):
        g = sL[sL["T"] == T].set_index("address")
        b = rangorde(universum(g.reset_index(), uni), score)
        lijst = list(b.address)
        rank = {a: i for i, a in enumerate(lijst)}
        grens = 20 if "buffer" in wissel else N
        nieuw, uit = [], 0
        for a in held:
            weg = a not in rank or rank[a] >= grens
            if "eruit" in wissel and not weg:
                weg = potje[a] <= 0.80 or potje[a] <= 0.75 * piek[a] or a not in g.index
            if weg:
                uit += 1
                potje.pop(a, None)
                piek.pop(a, None)
            else:
                nieuw.append(a)
        for a in lijst:
            if len(nieuw) >= N:
                break
            if a not in nieuw:
                nieuw.append(a)
                potje[a], piek[a] = 1.0, 1.0
        held = nieuw
        rets = [float(g.loc[a, "v14_pct"]) if a in g.index else 0.0 for a in held]
        for a, x in zip(held, rets):
            potje[a] *= 1 + x / 100
            piek[a] = max(piek[a], potje[a])
        alle = universum(g.reset_index(), uni).v14_pct.mean()
        rij.append({"T": T, "n": len(held), "top_pct": float(np.mean(rets)) if rets else 0.0,
                    "alle_pct": float(alle) if alle == alle else 0.0, "wissels": uit})
    df = pd.DataFrame(rij)
    pm = 30.44 / H
    return {"universum": uni, "score": score, "wissel": wissel, "L": L, "perioden": len(df),
            "gem_gekozen": round(df.n.mean(), 1),
            "per_maand_pct": round(df.top_pct.mean() * pm, 2),
            "positief_pct": round(100 * (df.top_pct > 0).mean()),
            "slechtste_pct": round(df.top_pct.min(), 2),
            "totaal_pct": round(100 * (np.prod(1 + df.top_pct / 100) - 1), 1),
            "wissels_per_periode": round(df.wissels.iloc[1:].mean(), 2),
            "alle_per_maand_pct": round(df.alle_pct.mean() * pm, 2)}, df


def kies(sdir, out):
    os.makedirs(out, exist_ok=True)
    s = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sdir}/**/r3_*.parquet", recursive=True)], ignore_index=True)
    sam, per = [], []
    for uni in ["basis", "streng"]:
        for score in ["rendement", "consistent", "constantheid"]:
            for wissel in ["lijst", "buffer", "eruit", "buffer+eruit"]:
                for L in LS:
                    r, df = simuleer(s, uni, score, wissel, L)
                    sam.append(r)
                    per.append(df.assign(universum=uni, score=score, wissel=wissel, L=L))
    sd = pd.DataFrame(sam)
    sd.to_csv(f"{out}/samenvatting.csv", index=False)
    pd.concat(per).to_csv(f"{out}/perioden.csv", index=False)
    pd.set_option("display.width", 200)
    print(sd.to_string(index=False))
    print("\nGemiddeld per score x wissel (over universa en L):")
    print(sd.groupby(["score", "wissel"])[["per_maand_pct", "positief_pct", "slechtste_pct"]].mean().round(2).to_string())


if __name__ == "__main__":
    if sys.argv[1] == "stats":
        stats(int(sys.argv[2]), int(sys.argv[3]), *sys.argv[4:8])
    else:
        kies(*sys.argv[2:4])
