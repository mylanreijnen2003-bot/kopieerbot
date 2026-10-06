"""E47 swing-wallets op large/mid caps (vooraf vastgelegd 6 okt 2026, 14:15 NL).
Regels: claude/voorregistratie-e47-swing-wallets-2026-10-06.md (project). Kort:
- trades plat->plat (bot-basis: instap eerste fill, uitstap hun gem.), open trades gewaardeerd tegen laatste prijs;
- pool = >= 30 trades, mediane houdtijd 24-240 u en >= 50% van de trades 24-240 u, >= 80% in CMC-rang <= 100;
- G1 = pool + netto > 0 + >= 60% winstmaanden + positief zonder 3 beste trades; G2 = top 10 G1 op t-stat;
- hoofdtest kiezen t/m 28-2-2026, meten trades die openen 1-3 t/m 18-8-2026; knips 1-12/1-2/1-4/1-6, 120 d terug, 60 d vooruit.
Gebruik:
  python -m bt.swing prijzen <fillsmap> <uit.parquet>
  python -m bt.swing stats <deel> <aantal> <universe> <fillsmap> <prijzen.parquet> <cmc.csv> <uitmap>
  python -m bt.swing uitslag <statsmap> <prijzen.parquet> <uitmap>
"""

from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

DAG = 86_400_000
UUR = 3_600_000
KOSTEN = 0.0032
KOSTEN_HL = 0.0010
EIND = pd.Timestamp("2026-08-19").value // 10**6
START = pd.Timestamp("2025-07-28").value // 10**6


def ms(d):
    return pd.Timestamp(d).value // 10**6


# venster-id -> (kies_van, knip, meet_tot (open < meet_tot))
VENSTERS = {"hoofd": (START, ms("2026-03-01"), EIND)}
for _d in ["2025-12-01", "2026-02-01", "2026-04-01", "2026-06-01"]:
    _t = ms(_d)
    VENSTERS[f"knip_{_d}"] = (max(START, _t - 120 * DAG), _t, min(EIND, _t + 60 * DAG))

MIN_TRADES = 30
HOLD_MIN, HOLD_MAX = 24.0, 240.0
SWING_AANDEEL = 0.5
CAP_RANG, CAP_AANDEEL = 100, 0.8


def to_base(coin):
    return coin[1:] if coin.startswith("k") and coin[1:2].isupper() else coin


# ---------------------------------------------------------------- reconstructie

def trades(fl):
    """fl: lijst (ts, coin, start, after, px), gesorteerd. Geeft lijst dicts:
    coin, open, sluit (None = nog open op het eind), dir, eerste, gem_in, gem_uit (None als open)."""
    st, out = {}, []
    for ts, c, s, a, px in fl:
        if s == 0 and a != 0:
            st[c] = {"coin": c, "open": ts, "dir": 1 if a > 0 else -1, "eerste": px, "iq": abs(a), "ic": abs(a) * px,
                     "uq": 0.0, "uc": 0.0}
            continue
        if c not in st:
            continue
        p = st[c]
        flip = a != 0 and a * s < 0
        if abs(a) > abs(s) and not flip:
            p["iq"] += abs(a) - abs(s)
            p["ic"] += (abs(a) - abs(s)) * px
        else:
            q = abs(s) if (a == 0 or flip) else abs(s) - abs(a)
            p["uq"] += q
            p["uc"] += q * px
        if a == 0 or flip:
            out.append({"coin": c, "open": p["open"], "sluit": ts, "dir": p["dir"], "eerste": p["eerste"],
                        "gem_in": p["ic"] / p["iq"], "gem_uit": p["uc"] / p["uq"] if p["uq"] else px})
            st.pop(c)
            if flip:
                st[c] = {"coin": c, "open": ts, "dir": 1 if a > 0 else -1, "eerste": px, "iq": abs(a),
                         "ic": abs(a) * px, "uq": 0.0, "uc": 0.0}
    for p in st.values():
        out.append({"coin": p["coin"], "open": p["open"], "sluit": None, "dir": p["dir"], "eerste": p["eerste"],
                    "gem_in": p["ic"] / p["iq"], "gem_uit": None})
    return out


class Prijzen:
    def __init__(self, path):
        p = pd.read_parquet(path).sort_values(["coin", "uur"])
        self.d = {c: (x.uur.to_numpy(), x.px.to_numpy()) for c, x in p.groupby("coin")}

    def op(self, coin, t):
        if coin not in self.d:
            return None
        h, px = self.d[coin]
        i = np.searchsorted(h, t, side="right") - 1
        return float(px[i]) if i >= 0 else None


def gewaardeerd(tr, t_eval, prijzen):
    """Rendement (bruto) bot- en hun-basis + effectief sluitmoment; open op t_eval -> laatste prijs."""
    if tr["sluit"] is not None and tr["sluit"] < t_eval:
        uit, sl, mtm = tr["gem_uit"], tr["sluit"], False
    else:
        uit = prijzen.op(tr["coin"], t_eval)
        if uit is None:
            return None
        sl, mtm = t_eval, True
    return {"bot": tr["dir"] * (uit / tr["eerste"] - 1), "hun": tr["dir"] * (uit / tr["gem_in"] - 1), "sluit_eff": sl,
            "mtm": mtm}


def k90(o, c):
    ev = sorted([(x, 1) for x in o] + [(x, -1) for x in c])
    s, cs = 0, []
    for _, d in ev:
        s += d
        cs.append(s)
    return max(1, int(np.ceil(np.percentile(cs, 90)))) if cs else 1


# ---------------------------------------------------------------- CMC-rang point-in-time

class Rang:
    def __init__(self, path):
        c = pd.read_csv(path)
        c["t"] = pd.to_datetime(c.date).astype("int64") // 10**6
        c["symbol"] = c.symbol.str.upper()
        self.datums = np.array(sorted(c.t.unique()))
        self.r = {t: x.groupby("symbol")["rank"].min().to_dict() for t, x in c.groupby("t")}

    def rang(self, coin, t):
        i = np.searchsorted(self.datums, t, side="right") - 1
        if i < 0:
            i = 0
        return self.r[self.datums[i]].get(to_base(coin).upper(), 999)


# ---------------------------------------------------------------- stap prijzen

def prijzen(d, out):
    parts = []
    for p in sorted(glob.glob(f"{d}/**/fills_*.parquet", recursive=True)):
        pf = pq.ParquetFile(p)
        for rg in range(pf.num_row_groups):
            x = pf.read_row_group(rg, columns=["coin", "ts", "px"]).to_pandas()
            x = x[~x.coin.astype(str).str.startswith(("#", "@"))]
            x["uur"] = x.ts // UUR * UUR
            x = x.sort_values("ts", kind="stable").groupby(["coin", "uur"], as_index=False).agg(ts=("ts", "last"),
                                                                                                 px=("px", "last"))
            parts.append(x)
        print("prijzen", p, flush=True)
    a = pd.concat(parts).sort_values("ts", kind="stable").groupby(["coin", "uur"], as_index=False).last()
    # prijs geldig vanaf het einde van het uur (geen vooruitkijken binnen het uur)
    a["uur"] = a.uur + UUR
    a[["coin", "uur", "px"]].to_parquet(out, index=False)
    print("prijzen klaar", len(a), a.coin.nunique(), flush=True)


# ---------------------------------------------------------------- stap stats

def kies_stats(rows, k_van, knip, rang):
    """rows: trades (dict met open, sluit_eff, bot, hun, coin, dir) met open in [k_van, knip), gewaardeerd op knip."""
    df = pd.DataFrame(rows)
    hold = (df.sluit_eff - df.open) / UUR
    netto = df.bot - KOSTEN
    caps = np.array([rang.rang(c, o) for c, o in zip(df.coin, df.open)])
    maand = pd.to_datetime(df.sluit_eff.clip(upper=knip - 1), unit="ms").dt.strftime("%Y-%m")
    mnd = netto.groupby(maand).sum()
    srt = np.sort(netto.to_numpy())[::-1]
    sd = netto.std(ddof=1)
    return {"n": len(df), "n_open": int(df.mtm.sum()), "houdtijd_med_u": float(hold.median()),
            "swing_aandeel": float(((hold >= HOLD_MIN) & (hold <= HOLD_MAX)).mean()),
            "cap100_aandeel": float((caps <= CAP_RANG).mean()), "cap50_aandeel": float((caps <= 50).mean()),
            "cap_onbekend": float((caps == 999).mean()), "long_aandeel": float((df.dir > 0).mean()),
            "netto_som": float(netto.sum()), "gem_r": float(netto.mean()),
            "gem_r_hun": float((df.hun - KOSTEN).mean()),
            "tstat": float(netto.mean() / (sd / np.sqrt(len(df)))) if sd > 0 else 0.0,
            "winst_pct": float((netto > 0).mean()), "maanden": int(len(mnd)),
            "winstmaanden": float((mnd > 0).mean()), "zonder_top3": float(srt[3:].sum()),
            "K": k90(df.open.tolist(), df.sluit_eff.tolist()),
            "dagen_sinds_laatste": float((knip - df.open.max()) / DAG)}


def is_pool(s):
    return (s["n"] >= MIN_TRADES and HOLD_MIN <= s["houdtijd_med_u"] <= HOLD_MAX and s["swing_aandeel"] >= SWING_AANDEEL
            and s["cap100_aandeel"] >= CAP_AANDEEL)


def is_g1(s):
    return is_pool(s) and s["netto_som"] > 0 and s["winstmaanden"] >= 0.6 and s["zonder_top3"] > 0


def stats(shard, n, upath, d, ppath, cpath, out):
    import pyarrow as pa
    import pyarrow.dataset as ds
    os.makedirs(out, exist_ok=True)
    pr, rang = Prijzen(ppath), Rang(cpath)
    u = pd.read_parquet(upath).sort_values("address").iloc[shard::n]
    dset = ds.dataset(glob.glob(f"{d}/**/fills_*.parquet", recursive=True), format="parquet")
    srows, frows = [], []
    teller = {"wallets": 0, **{f"{w}_{k}": 0 for w in VENSTERS for k in ["n30", "swing", "pool", "g1"]}}
    for chunk in np.array_split(u.address.values, max(1, len(u) // 400)):
        t = dset.to_table(filter=ds.field("address").isin(pa.array(sorted(set(chunk)))),
                          columns=["address", "coin", "ts", "tid", "px", "start", "signed"])
        f = t.to_pandas().drop_duplicates(["address", "ts", "tid", "coin", "px", "signed"])
        f = f[(f.ts < EIND) & ~f.coin.astype(str).str.startswith(("#", "@"))]
        f = f.sort_values(["address", "ts", "tid"], kind="stable")
        f["after"] = f.start + f.signed
        for a, x in f.groupby("address"):
            teller["wallets"] += 1
            tr = trades(list(zip(x.ts.tolist(), x.coin.tolist(), x.start.tolist(), x.after.tolist(), x.px.tolist())))
            if len(tr) < MIN_TRADES:
                continue
            for w, (k_van, knip, meet_tot) in VENSTERS.items():
                kies = []
                for t_ in tr:
                    if k_van <= t_["open"] < knip:
                        g = gewaardeerd(t_, knip, pr)
                        if g:
                            kies.append({**t_, **g})
                if len(kies) < MIN_TRADES:
                    continue
                s = kies_stats(kies, k_van, knip, rang)
                teller[f"{w}_n30"] += 1
                if HOLD_MIN <= s["houdtijd_med_u"] <= HOLD_MAX and s["swing_aandeel"] >= SWING_AANDEEL:
                    teller[f"{w}_swing"] += 1
                pool = is_pool(s)
                teller[f"{w}_pool"] += pool
                teller[f"{w}_g1"] += is_g1(s)
                srows.append({"address": a, "venster": w, "pool": pool, "g1": is_g1(s), **s})
                if not pool:
                    continue
                for t_ in tr:
                    if knip <= t_["open"] < meet_tot:
                        g = gewaardeerd(t_, EIND, pr)
                        if g:
                            frows.append({"address": a, "venster": w, "coin": t_["coin"], "open": t_["open"],
                                          "sluit": g["sluit_eff"], "dir": t_["dir"], "bot": g["bot"], "hun": g["hun"],
                                          "mtm": g["mtm"], "rang": rang.rang(t_["coin"], t_["open"])})
        print(f"deel {shard}: {teller['wallets']} wallets, {len(srows)} stats, {len(frows)} vooruit", flush=True)
    pd.DataFrame(srows).to_parquet(f"{out}/stats_{shard}.parquet", index=False)
    pd.DataFrame(frows).to_parquet(f"{out}/vooruit_{shard}.parquet", index=False)
    json.dump(teller, open(f"{out}/teller_{shard}.json", "w"))


# ---------------------------------------------------------------- stap uitslag

def potje(v, k, kosten, stop=-0.20, col="bot"):
    """v: trades van één trader (DataFrame), op sluitvolgorde; potje-rendement met stop."""
    tot = 0.0
    for r in (v.sort_values("sluit")[col] - kosten).to_numpy():
        tot += r / k
        if tot <= stop:
            return stop
    return tot


def groep_tabel(st, vt, kosten, col="bot"):
    """Per trader in de pool: potje-% in de meetperiode (0 als geen trades)."""
    res = []
    g = dict(tuple(vt.groupby("address")))
    for _, s in st.iterrows():
        v = g.get(s.address)
        if v is None or len(v) == 0:
            res.append({"address": s.address, "g1": s.g1, "tstat": s.tstat, "potje": 0.0, "n_vooruit": 0,
                        "gem_r_vooruit": np.nan, "long_potje": 0.0, "short_potje": 0.0})
            continue
        res.append({"address": s.address, "g1": s.g1, "tstat": s.tstat, "potje": potje(v, s.K, kosten, col=col),
                    "n_vooruit": len(v), "gem_r_vooruit": float((v[col] - kosten).mean()),
                    "long_potje": potje(v[v.dir > 0], s.K, kosten, col=col),
                    "short_potje": potje(v[v.dir < 0], s.K, kosten, col=col)})
    return pd.DataFrame(res)


def spearman(x, y):
    from scipy.stats import spearmanr
    r = spearmanr(x, y)
    return float(r.correlation), float(r.pvalue)


def kort(a):
    return a[:6] + "…" + a[-4:]


def uitslag(sdir, ppath, out):
    os.makedirs(out, exist_ok=True)
    st = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sdir}/**/stats_*.parquet", recursive=True)])
    vt = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sdir}/**/vooruit_*.parquet", recursive=True)])
    tel = {}
    for p in glob.glob(f"{sdir}/**/teller_*.json", recursive=True):
        for k, v in json.load(open(p)).items():
            tel[k] = tel.get(k, 0) + v
    pr = Prijzen(ppath)
    rng = np.random.default_rng(47)
    L = ["# E47 swing-wallets op large/mid caps — uitslag", "",
         "Regels: `claude/voorregistratie-e47-swing-wallets-2026-10-06.md` (project). Bot-basis, 0,32% kosten "
         "(0,10% = HL-route, info). Potje-% = som netto r / K met stop −20%; trader zonder trades = 0%.", "",
         "## Trechter (wallets)", "", f"Wallets in universum met fills: {tel.get('wallets', 0)}", "",
         "| Venster | ≥ 30 trades | + swing (1–10 d) | + caps (pool) | G1 consistent |", "|---|---|---|---|---|"]
    for w in VENSTERS:
        L.append(f"| {w} | {tel.get(w + '_n30', 0)} | {tel.get(w + '_swing', 0)} | {tel.get(w + '_pool', 0)} | "
                 f"{tel.get(w + '_g1', 0)} |")
    uit = {}
    for kosten, label in [(KOSTEN, "0,32%"), (KOSTEN_HL, "0,10%")]:
        for w in VENSTERS:
            s = st[(st.venster == w) & st.pool].copy()
            v = vt[vt.venster == w]
            if len(s) == 0:
                continue
            gt = groep_tabel(s, v, kosten)
            g1 = gt[gt.g1]
            g2 = g1.sort_values("tstat", ascending=False).head(10)
            pool_gem = gt.potje.mean()
            perc = np.nan
            if len(g1) >= 1 and len(gt) > len(g1):
                rnd = np.array([gt.potje.to_numpy()[rng.choice(len(gt), len(g1), replace=False)].mean()
                                for _ in range(2000)])
                perc = float((rnd < g1.potje.mean()).mean() * 100)
            uit[(label, w)] = {"gt": gt, "g1": g1, "g2": g2, "pool_gem": pool_gem, "perc": perc}
    # ---- hoofdtest
    h = uit.get(("0,32%", "hoofd"))
    L += ["", "## Hoofdtest (kiezen t/m 28-2-2026, meten trades die openen 1-3 t/m 18-8-2026)", ""]
    crit = {}
    if h is None:
        L.append("Geen pool in het hoofdvenster.")
    else:
        gt, g1, g2 = h["gt"], h["g1"], h["g2"]
        sp_in = gt[gt.n_vooruit >= 10]
        s_h = st[(st.venster == "hoofd") & st.pool].set_index("address")
        if len(sp_in) >= 10:
            rho, p = spearman(s_h.loc[sp_in.address, "gem_r"].to_numpy(), sp_in.gem_r_vooruit.to_numpy())
        else:
            rho, p = np.nan, np.nan
        crit["1 persistentie (Spearman > 0, p < 0,05)"] = bool(rho > 0 and p < 0.05)
        btc_v = pr.op("BTC", ms("2026-03-01")), pr.op("BTC", EIND)
        L += ["| Groep | n | gem. potje-% | mediaan | % positief | gem. long-deel | gem. short-deel |",
              "|---|---|---|---|---|---|---|"]
        for naam, x in [("G1 consistent", g1), ("G2 top 10 t-stat", g2), ("pool", gt),
                        ("pool − G1", gt[~gt.g1])]:
            if len(x):
                L.append(f"| {naam} | {len(x)} | {100 * x.potje.mean():+.1f} | {100 * x.potje.median():+.1f} | "
                         f"{100 * (x.potje > 0).mean():.0f}% | {100 * x.long_potje.mean():+.1f} | "
                         f"{100 * x.short_potje.mean():+.1f} |")
        if btc_v[0] and btc_v[1]:
            L.append(f"\nBTC aanhouden zelfde periode: {100 * (btc_v[1] / btc_v[0] - 1):+.1f}%")
        L.append(f"\nPersistentie: Spearman {rho:+.3f} (p {p:.3f}, n {len(sp_in)} pool-traders met ≥ 10 trades in meetperiode)")
        L.append(f"G1 vs willekeurig uit pool: percentiel {h['perc']:.0f}")
        zonder2 = g1.sort_values("potje", ascending=False).iloc[2:].potje.mean() if len(g1) > 2 else np.nan
        L.append(f"G1 zonder 2 beste traders: {100 * zonder2:+.1f}%")
        crit["2 G1 ≥ 10, > 0, > pool, percentiel ≥ 95"] = bool(len(g1) >= 10 and g1.potje.mean() > 0
                                                                and g1.potje.mean() > h["pool_gem"] and h["perc"] >= 95)
        crit["3 G1 positief zonder 2 beste"] = bool(zonder2 > 0)
        # regime: maandrendement G1 per maand (zonder stop), BTC-maand op/neer
        vh = vt[(vt.venster == "hoofd") & vt.address.isin(g1.address)].copy()
        if len(vh):
            kmap = s_h.K.to_dict()
            vh["r_k"] = (vh.bot - KOSTEN) / vh.address.map(kmap)
            vh["maand"] = pd.to_datetime(vh.sluit, unit="ms").dt.strftime("%Y-%m")
            per = vh.groupby(["maand", "address"]).r_k.sum().unstack(fill_value=0.0)
            per = per.reindex(columns=g1.address, fill_value=0.0)
            gm = per.mean(axis=1)
            br = {}
            for m_ in gm.index:
                b0 = pr.op("BTC", ms(m_ + "-01"))
                b1 = pr.op("BTC", min(EIND, ms(str((pd.Timestamp(m_ + "-01") + pd.offsets.MonthBegin(1)).date()))))
                br[m_] = (b1 / b0 - 1) if b0 and b1 else np.nan
            br = pd.Series(br)
            L += ["", "| Maand | BTC | G1 gem. (r/K) |", "|---|---|---|"]
            for m_ in gm.index:
                L.append(f"| {m_} | {100 * br[m_]:+.1f}% | {100 * gm[m_]:+.2f}% |")
            op, neer = gm[br > 0].mean(), gm[br <= 0].mean()
            L.append(f"\nG1 gem. per maand: BTC stijgend {100 * op:+.2f}% ({int((br > 0).sum())} mnd), "
                     f"dalend {100 * neer:+.2f}% ({int((br <= 0).sum())} mnd)")
            crit["5 positief in stijgende én dalende BTC-maanden"] = bool(op > 0 and neer > 0)
        else:
            crit["5 positief in stijgende én dalende BTC-maanden"] = False
        # G1-lijst (afgekort)
        L += ["", "### G1 (afgekort; volledige adressen versleuteld in `g1_hoofd.csv`)", "",
              "| Wallet | trades kies | houdtijd u | caps ≤ 100 | winst-% | winstmnd | gem r % | t | potje meet % | trades meet |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        g1s = s_h.loc[g1.address].join(g1.set_index("address")[["potje", "n_vooruit"]]).sort_values("tstat",
                                                                                                    ascending=False)
        for a, r in g1s.iterrows():
            L.append(f"| {kort(a)} | {r.n} | {r.houdtijd_med_u:.0f} | {100 * r.cap100_aandeel:.0f}% | "
                     f"{100 * r.winst_pct:.0f}% | {100 * r.winstmaanden:.0f}% | {100 * r.gem_r:+.2f} | {r.tstat:.1f} | "
                     f"{100 * r.potje:+.1f} | {r.n_vooruit} |")
        g1s.reset_index().to_csv(f"{out}/g1_hoofd.csv", index=False)
    # ---- knips
    L += ["", "## Robuustheid: knips (120 d terug, 60 d vooruit)", "",
          "| Knip | pool | G1 | G1 gem. potje-% | pool gem. | percentiel | G1 > pool |", "|---|---|---|---|---|---|---|"]
    gehaald = 0
    for w in VENSTERS:
        if w == "hoofd":
            continue
        k = uit.get(("0,32%", w))
        if k is None:
            L.append(f"| {w[5:]} | 0 | 0 | – | – | – | nee |")
            continue
        ok = len(k["g1"]) >= 5 and k["g1"].potje.mean() > k["pool_gem"]
        gehaald += ok
        g1m = 100 * k["g1"].potje.mean() if len(k["g1"]) else np.nan
        L.append(f"| {w[5:]} | {len(k['gt'])} | {len(k['g1'])} | {g1m:+.1f} | {100 * k['pool_gem']:+.1f} | "
                 f"{k['perc']:.0f} | {'ja' if ok else 'nee'} |")
    crit["4 G1 > pool op ≥ 3 van 4 knips"] = gehaald >= 3
    # ---- HL-kosten info
    L += ["", "## Info: zelfde met 0,10% kosten (kopiëren op Hyperliquid zelf)", "",
          "| Venster | G1 gem. potje-% | pool gem. | percentiel |", "|---|---|---|---|"]
    for w in VENSTERS:
        k = uit.get(("0,10%", w))
        if k is not None and len(k["g1"]):
            L.append(f"| {w} | {100 * k['g1'].potje.mean():+.1f} | {100 * k['pool_gem']:+.1f} | {k['perc']:.0f} |")
    # ---- info achteraf (niet in GO): hun eigen instap (gem. instapprijs = proportioneel kopiëren), 0,10% kosten
    L += ["", "## Info achteraf (niet in GO): hun eigen instapprijs, 0,10% kosten", "",
          "Proportioneel kopiëren (bijkopen meedoen) benadert hun eigen rendement. Selectie blijft op bot-basis.", "",
          "| Venster | G1 n | G1 gem. potje-% | G1 % positief | pool gem. | percentiel | Spearman (hun r) |",
          "|---|---|---|---|---|---|---|"]
    for w in VENSTERS:
        s_ = st[(st.venster == w) & st.pool].copy()
        if len(s_) == 0:
            continue
        gt = groep_tabel(s_, vt[vt.venster == w], KOSTEN_HL, col="hun")
        g1 = gt[gt.g1]
        perc = float((np.array([gt.potje.to_numpy()[rng.choice(len(gt), len(g1), replace=False)].mean()
                                for _ in range(2000)]) < g1.potje.mean()).mean() * 100) if 0 < len(g1) < len(gt) else np.nan
        sp_ = gt[gt.n_vooruit >= 10]
        rho_ = spearman(s_.set_index("address").loc[sp_.address, "gem_r_hun"].to_numpy(),
                        sp_.gem_r_vooruit.to_numpy())[0] if len(sp_) >= 10 else np.nan
        L.append(f"| {w} | {len(g1)} | {100 * g1.potje.mean():+.1f} | {100 * (g1.potje > 0).mean():.0f}% | "
                 f"{100 * gt.potje.mean():+.1f} | {perc:.0f} | {rho_:+.3f} |")
    # ---- oordeel
    n_g1 = len(h["g1"]) if h else 0
    L += ["", "## Oordeel", ""]
    for k_, v_ in crit.items():
        L.append(f"- {k_}: {'gehaald' if v_ else 'NIET gehaald'}")
    if n_g1 < 10:
        oordeel = "ONBEOORDEELBAAR (G1 < 10 traders)"
    else:
        oordeel = "GO" if crit and all(crit.values()) else "NO-GO"
    L += ["", f"**{oordeel}**"]
    open(f"{out}/report.md", "w").write("\n".join(L) + "\n")
    st.to_parquet(f"{out}/stats.parquet", index=False)
    print("\n".join(L))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "prijzen":
        prijzen(sys.argv[2], sys.argv[3])
    elif cmd == "stats":
        stats(int(sys.argv[2]), int(sys.argv[3]), *sys.argv[4:9])
    elif cmd == "uitslag":
        uitslag(*sys.argv[2:5])
