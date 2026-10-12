"""Swingsysteem H "trend + kleine terugval" — vooraf vastgelegd 12 okt 2026, 16:50 NL (afgeleid uit analyse trader E).

Data: Hyperliquid 4u-kaarsen (max 5000 = ~830 dagen) van alle perps die ook op Kraken Futures staan.
Dagbalk = 6 x 4u (UTC-dag). Signaal op dagslot, instap op de open van de eerste 4u-kaars van de dag erna.

Universum per dag: >= 60 dagen historie, gem. dagvolume 30 d >= $5 mln (HL), munt op Kraken (`kraken.json`).
Instapsignaal long (alles op het dagslot):
  1. 30d-rendement in percentiel 60-80 van het universum die dag (sterk, niet de sterkste)
  2. slot > 50-daags gemiddelde
  3. RSI(14) dag tussen 50 en 65
  4. 3d-rendement < 0 (kleine terugval)
  5. BTC-dagslot > 140-daags gemiddelde
Uitvoering:
  - Max 5 posities; plek = 1/5 van het vermogen bij instap. Eerst halve plek; tweede helft bij -3% onder de
    instapprijs (binnen 5 dagen). Meer signalen dan plekken: hoogste 30d-percentiel eerst.
  - Uit: noodstop -12% onder de gemiddelde instap; zodra de koers +5% boven de gem. instap heeft gestaan:
    meeschuivende stop 5% onder de hoogste koers sinds instap; anders na 10 dagen op het slot van de 4u-kaars.
    Binnen één 4u-kaars eerst de stop (laag), dan de nieuwe piek (hoog) — voorzichtig.
  - Zelfde munt pas 3 dagen na uitstap opnieuw. Kosten 0,16% per kant (0,32% per rondje). Funding niet meegerekend.
Toets (oordeel, de regels zijn afgeleid uit E's trades van mrt-okt 2026):
  - Schone periode = start data t/m 28-2-2026. GO als: >= 100 trades; CAGR > 0 en > BTC+140d-filter;
    Sharpe (dag) > BTC+140d-filter; winst ook zonder de 5 beste trades; beide helften van de schone periode > 0;
    percentiel >= 95 tegen 200 willekeurige varianten (zelfde aantal instappen per dag, willekeurige munt uit het
    universum met BTC-filter, zelfde uitstapregels).
  - Mrt 2026 - nu apart gerapporteerd (besmet: daaruit afgeleid).
  - Beschrijvend (geen GO): zonder terugval-eis (4), zonder BTC-filter (5), zonder tweede helft, alleen tijdstop.
Gebruik: python -m bt.swing_h <kraken.json> <uit>
"""

from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import pandas as pd

from bot import hl
from bt.engine import to_base

UUR4, DAG = 4 * 3_600_000, 86_400_000
NU = int(time.time() * 1000)
KNIP = pd.Timestamp("2026-03-01")
KOST = 0.0016
STABIEL = {"USDC", "USDT", "DAI", "USDE", "FDUSD", "PYUSD", "USD1", "USDH"}


def kaarsen(coin, s, e, iv="4h"):
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
    df.index = pd.to_datetime(df.index, unit="ms")
    return df


def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def dag(df):
    g = df.resample("1D")
    d = pd.DataFrame({"o": g.o.first(), "h": g.h.max(), "l": g.l.min(), "c": g.c.last(),
                      "usd": (df.v * df.c).resample("1D").sum(), "n": g.c.count()})
    d = d[d.n == 6]
    d["r30"] = d.c / d.c.shift(30) - 1
    d["r3"] = d.c / d.c.shift(3) - 1
    d["ma50"] = d.c.rolling(50).mean()
    d["rsi"] = rsi(d.c)
    d["vol30"] = d.usd.rolling(30).mean()
    d["nhist"] = np.arange(len(d))
    return d


def signalen(D, btc_ok, var):
    """Per dag: lijst (munt, prioriteit) die aan de regels voldoet. Signaal op dag d -> instap dag d+1."""
    rows = []
    for m, d in D.items():
        x = d[(d.nhist >= 60) & (d.vol30 >= 5e6)].copy()
        x["coin"] = m
        rows.append(x[["coin", "c", "r30", "r3", "ma50", "rsi"]])
    A = pd.concat(rows)
    A["pct"] = A.groupby(level=0).r30.rank(pct=True) * 100
    ok = A.pct.between(60, 80) & (A.c > A.ma50) & A.rsi.between(50, 65)
    if var != "zonder_terugval":
        ok &= A.r3 < 0
    if var != "zonder_btc":
        ok &= A.index.map(lambda t: bool(btc_ok.get(t, False)))
    S = A[ok]
    uni = A[A.index.map(lambda t: var == "zonder_btc" or bool(btc_ok.get(t, False)))]
    sig = {t: list(g.sort_values("pct", ascending=False).coin) for t, g in S.groupby(level=0)}
    pool = {t: list(g.coin) for t, g in uni.groupby(level=0)}
    return sig, pool


def simuleer(K, sig, var, tot=None):
    """Portefeuille over 4u-kaarsen. sig: {dag: [munten]} (instap de dag erna)."""
    tijden = sorted(set().union(*[set(k.index) for k in K.values()]))
    if tot is not None:
        tijden = [t for t in tijden if t < tot]
    cash, pos, laatst_uit, trades, eq = 1.0, {}, {}, [], []
    vandaag = None
    for t in tijden:
        d = t.normalize()
        if d != vandaag:
            vandaag = d
            for m in sig.get(d - pd.Timedelta(days=1), []):
                if len(pos) >= 5:
                    break
                if m in pos or (m in laatst_uit and d - laatst_uit[m] < pd.Timedelta(days=3)):
                    continue
                k = K[m]
                if t not in k.index:
                    continue
                waarde = cash + sum(p["q"] * K[x].c.asof(t) for x, p in pos.items())
                plek = waarde / 5
                deel = min(plek if var == "zonder_tweede_helft" else plek / 2, cash)
                if deel <= 0:
                    break
                px = k.o.at[t]
                q = deel * (1 - KOST) / px
                cash -= deel
                pos[m] = {"q": q, "p1": px, "gem": px, "inleg": deel, "plek": plek, "t0": t, "piek": px,
                          "bij": var == "zonder_tweede_helft", "aan": False}
        for m in list(pos):
            k, p = K[m], pos[m]
            if t not in k.index:
                continue
            o, h, l, c = k.o.at[t], k.h.at[t], k.l.at[t], k.c.at[t]
            if not p["bij"] and t - p["t0"] <= pd.Timedelta(days=5) and l <= p["p1"] * 0.97:
                px = min(o, p["p1"] * 0.97)
                deel = p["plek"] / 2
                if cash >= deel * 0.999:
                    p["q"] += deel * (1 - KOST) / px
                    p["inleg"] += deel
                    cash -= deel
                    p["gem"] = p["inleg"] / (p["q"] / (1 - KOST))
                p["bij"] = True
            uit = None
            if var != "alleen_tijd":
                stop = p["gem"] * 0.88
                if p["aan"]:
                    stop = max(stop, p["piek"] * 0.95)
                if l <= stop:
                    uit = min(o, stop)
            if uit is None and t - p["t0"] >= pd.Timedelta(days=10):
                uit = c
            if uit is None:
                p["piek"] = max(p["piek"], h)
                if p["piek"] >= p["gem"] * 1.05:
                    p["aan"] = True
                continue
            opbr = p["q"] * uit * (1 - KOST)
            cash += opbr
            trades.append({"coin": m, "in": p["t0"], "uit": t, "r_pct": 100 * (opbr / p["inleg"] - 1),
                           "dagen": (t - p["t0"]) / pd.Timedelta(days=1), "bijgekocht": p["inleg"] > p["plek"] * 0.75})
            laatst_uit[m] = t.normalize()
            del pos[m]
        eq.append((t, cash + sum(pp["q"] * K[x].c.asof(t) for x, pp in pos.items())))
    E = pd.Series(dict(eq)).resample("1D").last().dropna()
    return E, pd.DataFrame(trades)


def maten(E, T, van, tot):
    e = E[(E.index >= van) & (E.index < tot)]
    if len(e) < 30:
        return {}
    e = e / e.iloc[0]
    r = e.pct_change().dropna()
    jaren = (e.index[-1] - e.index[0]).days / 365.25
    t = T[(T["in"] >= van) & (T["in"] < tot)] if len(T) else T
    zonder5 = t.sort_values("r_pct").iloc[:-5].r_pct.sum() if len(t) > 5 else np.nan
    return {"trades": len(t), "totaal_pct": round(100 * (e.iloc[-1] - 1), 1),
            "cagr_pct": round(100 * (e.iloc[-1] ** (1 / jaren) - 1), 1) if jaren > 0 else np.nan,
            "sharpe": round(r.mean() / r.std() * np.sqrt(365), 2) if r.std() > 0 else np.nan,
            "max_daling_pct": round(100 * (e / e.cummax() - 1).min(), 1),
            "winst_pct": round(100 * (t.r_pct > 0).mean(), 0) if len(t) else np.nan,
            "gem_trade_pct": round(t.r_pct.mean(), 2) if len(t) else np.nan,
            "som_zonder_top5": round(zonder5, 1) if zonder5 == zonder5 else np.nan}


def main():
    kjson, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    kraken = set(json.load(open(kjson)))
    meta = hl.info({"type": "meta"}, weight=20)
    munten = [u["name"] for u in meta["universe"] if not u.get("isDelisted") and to_base(u["name"]) in kraken
              and to_base(u["name"]) not in STABIEL]
    if "BTC" not in munten:
        munten.append("BTC")
    print("munten HL ∩ Kraken:", len(munten), flush=True)
    K, D = {}, {}
    for m in munten:
        k = kaarsen(m, NU - 840 * DAG, NU)
        if k is None or len(k) < 400:
            continue
        K[m], D[m] = k, dag(k)
    btc = D["BTC"]
    btc_ok = (btc.c > btc.c.rolling(140).mean()).to_dict()
    start = min(d.index[60] for d in D.values() if len(d) > 60)
    print("munten met data:", len(K), "start", start.date(), flush=True)

    res, alle_t = [], {}
    for var in ["hoofd", "zonder_terugval", "zonder_btc", "zonder_tweede_helft", "alleen_tijd"]:
        sig, pool = signalen(D, btc_ok, var)
        E, T = simuleer(K, sig, var)
        alle_t[var] = (E, T, sig, pool)
        for naam, van, tot in [("schoon", start, KNIP), ("schoon_h1", start, start + (KNIP - start) / 2),
                               ("schoon_h2", start + (KNIP - start) / 2, KNIP), ("besmet_mrt26_nu", KNIP, pd.Timestamp.now()),
                               ("alles", start, pd.Timestamp.now())]:
            res.append({"variant": var, "periode": naam, **maten(E, T, van, tot)})
        print(var, "klaar", len(T), "trades", flush=True)

    # BTC-benchmarks
    bc = btc.c
    bf = (bc.pct_change() * (bc > bc.rolling(140).mean()).shift(1).astype(float)).fillna(0)
    BH = bc / bc.iloc[0]
    BF = (1 + bf).cumprod()
    for naam, van, tot in [("schoon", start, KNIP), ("besmet_mrt26_nu", KNIP, pd.Timestamp.now()), ("alles", start, pd.Timestamp.now())]:
        res.append({"variant": "BTC aanhouden", "periode": naam, **maten(BH, pd.DataFrame(columns=["in", "r_pct"]), van, tot)})
        res.append({"variant": "BTC + 140d-filter", "periode": naam, **maten(BF, pd.DataFrame(columns=["in", "r_pct"]), van, tot)})
    R = pd.DataFrame(res)
    R.to_csv(f"{out}/samenvatting.csv", index=False)

    # willekeurige controle (hoofdvariant, schone periode)
    E, T, sig, pool = alle_t["hoofd"]
    rng = np.random.default_rng(12)
    rand = []
    for s in range(200):
        rs = {}
        for d, ms in sig.items():
            p = pool.get(d, [])
            if p:
                rs[d] = list(rng.choice(p, size=min(len(ms), len(p)), replace=False))
        Er, Tr = simuleer(K, rs, "hoofd", tot=KNIP + pd.Timedelta(days=11))
        m = maten(Er, Tr, start, KNIP)
        rand.append(m.get("totaal_pct", np.nan))
        if s % 20 == 0:
            print("willekeurig", s, flush=True)
    rand = np.array(rand)
    echt = R[(R.variant == "hoofd") & (R.periode == "schoon")].totaal_pct.iloc[0]
    perc = round(100 * np.nanmean(rand < echt), 0)
    T.to_csv(f"{out}/trades_hoofd.csv", index=False)
    E.to_csv(f"{out}/vermogen_hoofd.csv")

    h = R[(R.variant == "hoofd")].set_index("periode")
    bf_s = R[(R.variant == "BTC + 140d-filter") & (R.periode == "schoon")].iloc[0]
    go = {
        ">= 100 trades": h.loc["schoon", "trades"] >= 100,
        "CAGR > 0 en > BTC+filter": h.loc["schoon", "cagr_pct"] > max(0, bf_s.cagr_pct),
        "Sharpe > BTC+filter": h.loc["schoon", "sharpe"] > bf_s.sharpe,
        "winst zonder top-5": h.loc["schoon", "som_zonder_top5"] > 0,
        "beide helften > 0": h.loc["schoon_h1", "totaal_pct"] > 0 and h.loc["schoon_h2", "totaal_pct"] > 0,
        "percentiel >= 95 vs willekeurig": perc >= 95,
    }
    regels = [f"# Swingsysteem H — uitslag {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC", "",
              f"Munten: {len(K)} (HL ∩ Kraken), data vanaf {start.date()}. Schone periode t/m 28-2-2026.", "",
              "## Oordeel: " + ("**GO**" if all(go.values()) else "**NO-GO**"), "",
              *[f"- {'✅' if v else '❌'} {k}" for k, v in go.items()],
              f"- Willekeurige controle (200x): mediaan {np.nanmedian(rand):.1f}%, echt {echt:.1f}%, percentiel {perc:.0f}", "",
              "## Alle cijfers", "", R.to_markdown(index=False), "",
              "## Hoofdvariant per munt (schone periode)", "",
              (T[T["in"] < KNIP].groupby("coin").r_pct.agg(["size", "mean"]).round(2).sort_values("size", ascending=False)
               .head(20).to_markdown() if len(T) else "geen trades"), ""]
    open(f"{out}/rapport.md", "w").write("\n".join(regels))
    print("\n".join(regels))


if __name__ == "__main__":
    main()
