"""Lokale analyse van ruwe portfolio-reeksen (hl_select ruw): tijdgewogen rendement (stortingen eruit).
r_i = (perp-pnl_i - perp-pnl_{i-1}) / equity_{i-1}, equity = totale accountwaarde (perp + spot), anders perp.
Kiezen op data t/m 18-8, testen 19-8 t/m nu. Gebruik: python -m kopietest.hl_ruw_analyse <ruw-map> <portfolio_alle.csv.gz> <uit>"""
import glob
import gzip
import json
import os
import sys

import numpy as np
import pandas as pd

DAG = 86_400_000
KNIP = int(pd.Timestamp("2026-08-19").value // 10**6)


def _interp(pts, t):
    xs = [x for x, _ in pts]
    return float(np.interp(t, xs, [y for _, y in pts]))


def reeks(w):
    """Equity (accountwaarde, totaal of perp: hoogste) en pnl-stappen uit perp-vensters zonder dat de vensters door elkaar lopen."""
    eqp = {}
    for k in ("allTime", "month", "week", "perpAllTime", "perpMonth", "perpWeek"):
        for t, v in w.get(f"{k}_acc", []):
            eqp[t] = max(eqp.get(t, 0.0), v)
    at, mo, we = (sorted(w.get(f"{k}_pnl", [])) for k in ("perpAllTime", "perpMonth", "perpWeek"))
    if len(at) < 3 or len(eqp) < 3:
        return None
    m0 = mo[0][0] if len(mo) >= 2 else None
    w0 = we[0][0] if len(we) >= 2 else None
    stukken = []
    eind_at = m0 if m0 else at[-1][0]
    pts = [p for p in at if p[0] < eind_at] + ([(eind_at, _interp(at, eind_at))] if m0 else [])
    stukken.append(pts)
    if m0:
        eind_mo = w0 if w0 and w0 > m0 else mo[-1][0]
        stukken.append([p for p in mo if p[0] < eind_mo] + ([(eind_mo, _interp(mo, eind_mo))] if w0 and w0 > m0 else []))
        if w0 and w0 > m0:
            stukken.append(we)
    ts, dp = [], []
    for st in stukken:
        for (t0, p0), (t1, p1) in zip(st[:-1], st[1:]):
            if t1 > t0:
                ts.append((t0, t1))
                dp.append(p1 - p0)
    if not ts:
        return None
    ex = sorted(eqp.items())
    rows, cum = [], 0.0
    for (t0, t1), d in zip(ts, dp):
        e0 = _interp(ex, t0)
        cum += d
        rows.append((t1, _interp(ex, t1), cum, d / e0 if e0 > 50 else 0.0))
    df = pd.DataFrame(rows, columns=["t", "e", "pnl", "r"]).drop_duplicates("t").sort_values("t")
    return df


def kenmerken(df, T, van=None):
    d = df[(df.t <= T) & ((df.t >= van) if van else True)]
    if len(d) < 3:
        return None
    start = d[d.e >= 100].t.min()
    if pd.isna(start):
        return None
    d = d[d.t >= start]
    groei = np.cumprod(1 + np.clip(d.r.values, -0.99, None))
    d = d.assign(g=groei, m=pd.to_datetime(d.t, unit="ms").dt.strftime("%Y-%m"))
    mnd = (d.groupby("m").g.last() / pd.concat([pd.Series([1.0]), d.groupby("m").g.last()[:-1]]).values) - 1
    l90 = d[d.t >= T - 90 * DAG]
    g90 = l90.g / l90.g.iloc[0] if len(l90) else pd.Series([1.0])
    dd = float((g90 / g90.cummax() - 1).min()) if len(g90) else 0.0
    last6 = mnd.tail(6)
    return {"dagen": (T - start) / DAG, "maanden": len(mnd), "verliesmaanden_6": int((last6 < 0).sum()),
            "gem_maand_6": float(last6.mean()), "rend_90": float(g90.iloc[-1] - 1) if len(g90) else 0.0, "dd_90": dd,
            "max_stap": float(np.abs(l90.r).max()) if len(l90) else 0.0,
            "equity_gem_90": float(l90.e.mean()) if len(l90) else 0.0, "equity_min_90": float(l90.e.min()) if len(l90) else 0.0,
            "actief_14d": bool((d[d.t >= T - 14 * DAG].pnl.diff().abs() > 0).any()),
            "mnd": json.dumps({k: round(100 * v, 1) for k, v in mnd.items()})}


def test(df, T0, T1):
    d = df[(df.t > T0) & (df.t <= T1)]
    if len(d) < 2:
        return None, None
    g = np.cumprod(1 + np.clip(d.r.values, -0.99, None))
    return float(g[-1] - 1), float((g / np.maximum.accumulate(g) - 1).min())


def geschikt(t, p):
    return t[(t[f"{p}dagen"] >= 90) & (t[f"{p}equity_gem_90"] >= 1000) & (t[f"{p}equity_min_90"] >= 0.2 * t[f"{p}equity_gem_90"])
             & (t[f"{p}rend_90"] > 0) & (t[f"{p}dd_90"] > -0.4) & (t[f"{p}actief_14d"]) & (t[f"{p}max_stap"] < 0.5)
             & (t.trades_hist.fillna(100) >= 100)]


def rang(g, p, regel):
    if regel == "mylan":
        return g.sort_values([f"{p}verliesmaanden_6", f"{p}gem_maand_6"], ascending=[True, False])
    if regel == "risico":
        return g.assign(_s=g[f"{p}rend_90"] / (g[f"{p}dd_90"].abs() + 0.05)).sort_values("_s", ascending=False)
    return g.sort_values(f"{p}rend_90", ascending=False)


def main():
    ruw, alle, uit = sys.argv[1:4]
    os.makedirs(uit, exist_ok=True)
    th = pd.read_csv(alle)[["address", "trades_hist"]]
    rows = []
    nu = 0
    for f in glob.glob(f"{ruw}/*.jsonl.gz"):
        for line in gzip.open(f, "rt"):
            w = json.loads(line)
            df = reeks(w)
            if df is None:
                continue
            nu = max(nu, int(df.t.max()))
            r = {"address": w["address"]}
            k = kenmerken(df, KNIP)
            if k:
                r.update({f"k_{x}": v for x, v in k.items()})
                r["test_rend"], r["test_dd"] = test(df, KNIP, int(df.t.max()))
            n = kenmerken(df, int(df.t.max()))
            if n:
                r.update({f"n_{x}": v for x, v in n.items()})
            rows.append(r)
    t = pd.DataFrame(rows).merge(th, on="address", how="left")
    t.to_csv(f"{uit}/ruw_kenmerken.csv.gz", index=False)
    res = {"wallets": len(t)}
    k = geschikt(t[t.k_dagen.notna() & t.test_rend.notna()], "k_")
    res["geschikt_knip"] = len(k)
    res["basis"] = {"gem": round(100 * k.test_rend.mean(), 1), "mediaan": round(100 * k.test_rend.median(), 1),
                    "winst_aandeel": round(float((k.test_rend > 0).mean()), 2)}
    for regel in ("mylan", "risico", "max"):
        for n_ in (5, 10, 20):
            s = rang(k, "k_", regel).head(n_)
            res[f"{regel}_{n_}"] = {"gem": round(100 * s.test_rend.mean(), 1), "mediaan": round(100 * s.test_rend.median(), 1),
                                    "winst_aandeel": round(float((s.test_rend > 0).mean()), 2), "slechtste": round(100 * s.test_rend.min(), 1),
                                    "dd_gem": round(100 * s.test_dd.mean(), 1)}
    k.to_csv(f"{uit}/knip_geschikt.csv", index=False)
    v = geschikt(t[t.n_dagen.notna()], "n_")
    res["geschikt_vandaag"] = len(v)
    v.to_csv(f"{uit}/vandaag_geschikt.csv", index=False)
    json.dump(res, open(f"{uit}/uitslag.json", "w"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
