"""Analyse van trader E (0x2555…34b1): wanneer stapt hij in en uit? Basis voor een eigen swingsysteem.

Stap 1 (deze analyse, beschrijvend): alle trades (API, max 10.000 fills), per instap de toestand van de munt en
de markt (1u-kaarsen Hyperliquid + funding) en dezelfde maten op alle uren van dezelfde munten (basislijn).
Zo zien we waarin zijn instappen afwijken van een willekeurig moment. Plus: uitstapgedrag (MFE/MAE, verkoopt hij
na een vaste winst, na een top, na tijd?). En per kenmerk: doen willekeurige momenten met dat kenmerk het daarna
ook goed (= is het kenmerk zelf de edge, of is het zijn keuze)?
Niets hiervan is een test met GO-regels; een eigen systeem wordt daarna apart vooraf vastgelegd.
Gebruik: python -m bt.e_analyse <adres-bestand.json> <uit>
"""

from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import pandas as pd

from bot import hl
from bt.bot_stats import KOSTEN, bot_trades
from bt.p2_common import trades_open

NU = int(time.time() * 1000)
UUR, DAG = 3_600_000, 86_400_000
EXTRA = ["BTC", "ETH", "SOL", "HYPE", "XRP", "DOGE", "AVAX", "LINK", "SUI", "ENA", "AAVE", "NEAR", "TAO", "PUMP",
         "WLD", "ARB", "OP", "INJ", "TIA", "SEI", "APT", "LTC", "BNB", "ADA", "ONDO", "PENDLE", "CRV", "RENDER"]


def kaarsen(coin, s, e, iv="1h"):
    out, cur = {}, int(s)
    while cur < e:
        try:
            data = hl.info({"type": "candleSnapshot", "req": {"coin": coin, "interval": iv, "startTime": cur,
                                                               "endTime": int(e)}}, weight=25)
        except Exception as exc:  # noqa: BLE001
            print("geen kaarsen", coin, exc, flush=True)
            break
        if not isinstance(data, list) or not data:
            break
        for c in data:
            out[int(c["t"])] = (float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"]), float(c["v"]))
        nxt = max(int(c["t"]) for c in data) + 1
        if nxt <= cur or len(data) < 4000:
            break
        cur = nxt
    if not out:
        return None
    df = pd.DataFrame.from_dict(out, orient="index", columns=["o", "h", "l", "c", "v"]).sort_index()
    df.index = df.index.astype("int64")
    return df


def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def kenmerken(df):
    """Per uur (aan het slot van dat uur) de toestand van de munt. Alles alleen uit het verleden."""
    c = df.c
    k = pd.DataFrame(index=df.index)
    for h, naam in [(4, "r4u"), (24, "r24u"), (72, "r3d"), (168, "r7d"), (720, "r30d")]:
        k[naam] = 100 * (c / c.shift(h) - 1)
    hi7 = df.h.rolling(168).max()
    lo7 = df.l.rolling(168).min()
    k["van_top7d"] = 100 * (c / hi7 - 1)
    k["plek_7d"] = (c - lo7) / (hi7 - lo7)
    k["vs_ma20d"] = 100 * (c / c.rolling(480).mean() - 1)
    k["vs_ma50d"] = 100 * (c / c.rolling(1200).mean() - 1)
    k["rsi_1u"] = rsi(c)
    c4 = c.iloc[3::4]
    k["rsi_4u"] = rsi(c4).reindex(k.index).ffill()
    cd = c.iloc[23::24]
    k["rsi_1d"] = rsi(cd).reindex(k.index).ffill()
    usd = df.v * c
    k["vol_x"] = usd.rolling(24).sum() / (usd.rolling(168).sum() / 7)
    k["volat_7d"] = 100 * c.pct_change().rolling(168).std() * np.sqrt(24)
    for h in [24, 48, 72, 120]:
        k[f"vooruit_{h}u"] = 100 * (c.shift(-h) / c - 1)
    return k


def bij(k, t):
    """Kenmerken op het laatste volle uur vóór t."""
    i = k.index.searchsorted(t - UUR, side="right") - 1
    return k.iloc[i] if i >= 0 else None


def main():
    adr_file, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    adr = json.load(open(adr_file))["adres"]
    fl = [f for f in hl.fills(adr, NU - 400 * DAG, NU) if f["kind"] == "perp" and not str(f["coin"]).startswith(("#", "@"))]
    print("fills", len(fl), "vanaf", pd.to_datetime(min(f["time"] for f in fl), unit="ms"), flush=True)
    bt_ = bot_trades(fl)
    _, open_ = trades_open(fl, 0)
    tr = pd.DataFrame(bt_, columns=["coin", "open", "sluit", "dir", "p1", "pv", "po", "eerste_deel", "bijkopen"])
    tr["r_pct"] = 100 * (tr.dir * (tr.po / tr.p1 - 1) - KOSTEN)
    tr["houd_u"] = (tr.sluit - tr.open) / UUR
    # posities in porties: hoeveel fills per instap/uitstap
    print("trades", len(tr), "open nu", len(open_), flush=True)

    t0 = int(tr.open.min()) - 70 * DAG
    munten = sorted(set(tr.coin) | set(open_) | set(EXTRA))
    K, KK = {}, {}
    for m in munten:
        df = kaarsen(m, t0, NU)
        if df is None or len(df) < 300:
            continue
        K[m] = df
        KK[m] = kenmerken(df)
    btcd = kaarsen("BTC", t0 - 200 * DAG, NU, "1d")
    btc_ma140 = btcd.c.rolling(140).mean()
    print("kaarsen voor", len(K), "munten", flush=True)

    # funding per munt (uur)
    fund = {}
    for m in set(tr.coin) & set(K):
        try:
            fund[m] = hl.funding(m, t0, NU)
        except Exception:  # noqa: BLE001
            fund[m] = {}

    rij = []
    for t in tr.itertuples():
        if t.coin not in KK:
            continue
        a = bij(KK[t.coin], t.open)
        b = bij(KK["BTC"], t.open) if "BTC" in KK else None
        if a is None:
            continue
        df = K[t.coin]
        seg = df[(df.index >= t.open - UUR) & (df.index <= t.sluit)]
        mfe = 100 * t.dir * ((seg.h.max() if t.dir > 0 else seg.l.min()) / t.p1 - 1) if len(seg) else np.nan
        mae = 100 * t.dir * ((seg.l.min() if t.dir > 0 else seg.h.max()) / t.p1 - 1) if len(seg) else np.nan
        uit = bij(KK[t.coin], t.sluit + UUR)
        piek_na = seg.h.max() if t.dir > 0 else seg.l.min()
        f = fund.get(t.coin, {})
        fr = [v for h, v in f.items() if t.open - 24 * UUR <= h <= t.open]
        bd = btcd[btcd.index <= t.open - DAG]
        rec = {"coin": t.coin, "richting": "long" if t.dir > 0 else "short",
               "open": pd.to_datetime(t.open, unit="ms"), "uur_utc": pd.to_datetime(t.open, unit="ms").hour,
               "weekdag": pd.to_datetime(t.open, unit="ms").day_name(), "houd_u": round(t.houd_u, 1),
               "r_pct": round(t.r_pct, 2), "bijkopen": t.bijkopen, "eerste_deel": round(t.eerste_deel, 2),
               "mfe_pct": round(mfe, 2), "mae_pct": round(mae, 2),
               "uit_vs_piek_pct": round(100 * t.dir * (t.po / piek_na - 1), 2) if len(seg) else np.nan,
               "funding_24u_bp": round(1e4 * float(np.mean(fr)), 3) if fr else np.nan,
               "btc_boven_ma140": bool(bd.c.iloc[-1] > btc_ma140.reindex(bd.index).iloc[-1]) if len(bd) else None,
               "cluster_2u": int(((tr.open - t.open).abs() <= 2 * UUR).sum() - 1)}
        for kol in a.index:
            if not kol.startswith("vooruit"):
                rec[kol] = round(float(a[kol]), 3) if a[kol] == a[kol] else np.nan
        if b is not None:
            for kol in ["r24u", "r3d", "r7d", "van_top7d", "rsi_1d"]:
                rec[f"btc_{kol}"] = round(float(b[kol]), 3) if b[kol] == b[kol] else np.nan
        if uit is not None:
            for kol in ["r24u", "rsi_1u", "rsi_4u", "plek_7d"]:
                rec[f"uit_{kol}"] = round(float(uit[kol]), 3) if uit[kol] == uit[kol] else np.nan
        rij.append(rec)
    E = pd.DataFrame(rij)
    E.to_csv(f"{out}/trades_kenmerken.csv", index=False)

    # basislijn: alle uren van dezelfde munten in dezelfde periode
    per = (int(tr.open.min()), int(tr.open.max()))
    basis = []
    for m in set(E.coin):
        k = KK[m]
        k = k[(k.index >= per[0]) & (k.index <= per[1])].copy()
        k["coin"] = m
        basis.append(k)
    B = pd.concat(basis)
    kol = ["r4u", "r24u", "r3d", "r7d", "r30d", "van_top7d", "plek_7d", "vs_ma20d", "vs_ma50d", "rsi_1u", "rsi_4u",
           "rsi_1d", "vol_x", "volat_7d"]
    L = E[E.richting == "long"]
    vgl = []
    for c in kol:
        b = B[c].dropna()
        e = L[c].dropna()
        if not len(e):
            continue
        pct = float(np.mean([(b < x).mean() for x in e]))  # gem. percentiel van zijn instappen in de basislijn
        vgl.append({"kenmerk": c, "mediaan_E": round(e.median(), 2), "mediaan_basis": round(b.median(), 2),
                    "gem_percentiel_E": round(100 * pct, 0), "n_E": len(e)})
    V = pd.DataFrame(vgl)
    V.to_csv(f"{out}/instap_vs_basis.csv", index=False)

    # is het kenmerk zelf de edge? vooruit 48/72 u per kwintiel, alle munten (incl. EXTRA), zelfde periode
    alle = []
    for m, k in KK.items():
        k = k[(k.index >= per[0]) & (k.index <= per[1])].copy()
        k["coin"] = m
        alle.append(k)
    A = pd.concat(alle)
    kw = []
    for c in kol:
        x = A[[c, "vooruit_48u", "vooruit_72u"]].dropna()
        if len(x) < 500:
            continue
        x["q"] = pd.qcut(x[c].rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
        g = x.groupby("q", observed=True)[["vooruit_48u", "vooruit_72u"]].mean().round(2)
        for q, r in g.iterrows():
            kw.append({"kenmerk": c, "kwintiel": int(q), "vooruit_48u": r.vooruit_48u, "vooruit_72u": r.vooruit_72u})
    Q = pd.DataFrame(kw)
    Q.to_csv(f"{out}/kwintielen_alle_munten.csv", index=False)

    # rapport
    regels = [f"# Analyse trader E (0x2555…34b1) — {pd.Timestamp.utcnow():%Y-%m-%d %H:%M} UTC", "",
              f"Trades: {len(tr)} (data vanaf {pd.to_datetime(tr.open.min(), unit='ms'):%Y-%m-%d}), "
              f"open nu: {len(open_)}. Basislijn = alle uren van dezelfde munten in dezelfde periode.", "",
              "## Algemeen", "",
              f"- Long: {100 * (E.richting == 'long').mean():.0f}% · winst-% {100 * (E.r_pct > 0).mean():.0f}% · "
              f"gem. {E.r_pct.mean():.2f}% · mediaan {E.r_pct.median():.2f}% per trade (bot-basis, na 0,32%)",
              f"- Houdtijd mediaan {E.houd_u.median():.0f} u (p25 {E.houd_u.quantile(.25):.0f}, p75 {E.houd_u.quantile(.75):.0f})",
              f"- Bijkopen: {100 * (E.bijkopen > 0).mean():.0f}% van de trades, eerste fill = {E.eerste_deel.median():.0%} van de positie (mediaan)",
              f"- Cluster: mediaan {E.cluster_2u.median():.0f} andere trades binnen 2 u; {100 * (E.cluster_2u >= 3).mean():.0f}% in een mandje van ≥ 4",
              f"- Uitstap: MFE mediaan {E.mfe_pct.median():.1f}%, MAE mediaan {E.mae_pct.median():.1f}%, "
              f"verkoopt mediaan {E.uit_vs_piek_pct.median():.1f}% onder de hoogste koers tijdens de trade",
              f"- BTC boven 140d-gemiddelde bij instap: {100 * E.btc_boven_ma140.mean():.0f}% van de trades",
              f"- Funding (24u voor instap) mediaan {E.funding_24u_bp.median():.2f} bp/u", "",
              "## Instap vs willekeurig moment (longs)", "",
              "Gem. percentiel 50 = niet anders dan willekeurig; < 30 = hij koopt bij lage waarden, > 70 = bij hoge.", "",
              V.to_markdown(index=False), "",
              "## Werkt het kenmerk zelf? Vooruit-rendement per kwintiel (alle munten, zelfde periode, geen kosten)", "",
              Q.pivot(index="kenmerk", columns="kwintiel", values="vooruit_72u").round(2).to_markdown(), "",
              "## Uur en dag van instap", "",
              E.groupby("uur_utc").size().to_frame("trades").T.to_markdown(), "",
              E.groupby("weekdag").size().to_frame("trades").T.to_markdown(), "",
              "## Per munt", "",
              E.groupby("coin").agg(trades=("r_pct", "size"), gem_r=("r_pct", "mean"), long=("richting", lambda s: (s == "long").mean()))
              .round(2).sort_values("trades", ascending=False).head(25).to_markdown(), ""]
    open(f"{out}/rapport.md", "w").write("\n".join(regels))
    print("\n".join(regels))


if __name__ == "__main__":
    main()
