"""Kiezen op vaste inzet per trade (wat Mylan echt verdient).
Rendement per trade = richting x (gem. uitstapprijs / gem. instapprijs - 1) - 0,2% kosten (Kraken taker 2x + slippage).
Kiezen op de keuzeperiode (t/m 18-8-2026), eerlijk testen op 19-8 t/m gisteren.
Eisen: >= 1 trade laatste 7 d, >= 4 laatste 14 d, <= 3 trades/dag (30 d), mediane houdtijd >= 2 u, >= 70% trades in Kraken-munten,
>= 50 trades in de keuzeperiode. Rangorde: t-waarde van het gemiddelde rendement per trade in de keuzeperiode. Top 5.
Gebruik: python -m bt.vast_select <datamap> <h4-sim-map> <beurzen-map> <uitmap>
"""

from __future__ import annotations

import glob
import json
import os
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd

from bot import hl
from bt import data
from bt.engine import to_base

KOSTEN = 0.002
DATA_EIND = pd.Timestamp("2026-08-19").value // 10**6
NU = int(time.time() * 1000) // hl.DAY * hl.DAY
TOP = 5


def trades_pct(fl):
    """Per munt van plat naar plat: (coin, open_t, sluit_t, rendement_pct_na_kosten)."""
    st, out = {}, []
    for f in fl:
        c, s, a, px = f["coin"], f["start"], f["after"], f["px"]
        if s == 0 and a != 0:
            st[c] = {"dir": 1 if a > 0 else -1, "in_q": abs(a), "in_c": abs(a) * px, "uit_q": 0.0, "uit_c": 0.0, "t": f["time"]}
            continue
        if c not in st:
            continue                                    # positie van vóór de data: overslaan
        p = st[c]
        flip = a != 0 and a * s < 0
        if abs(a) > abs(s) and not flip:               # bijkopen
            p["in_q"] += abs(a) - abs(s)
            p["in_c"] += (abs(a) - abs(s)) * px
        else:                                          # afbouwen / sluiten / wisselen
            q = abs(s) if (a == 0 or flip) else abs(s) - abs(a)
            p["uit_q"] += q
            p["uit_c"] += q * px
        if a == 0 or flip:
            r = p["dir"] * ((p["uit_c"] / p["uit_q"]) / (p["in_c"] / p["in_q"]) - 1) - KOSTEN
            out.append((c, p["t"], f["time"], r))
            st.pop(c)
            if flip:
                st[c] = {"dir": 1 if a > 0 else -1, "in_q": abs(a), "in_c": abs(a) * px, "uit_q": 0.0, "uit_c": 0.0, "t": f["time"]}
    return out


def stats(tr):
    r = np.array([x[3] for x in tr])
    if len(r) == 0:
        return {"trades": 0}
    w, l = r[r > 0], r[r <= 0]
    return {"trades": len(r), "winst_pct_trades": round(100 * len(w) / len(r), 1),
            "gem_rendement_pct": round(100 * r.mean(), 2), "gem_winst_pct": round(100 * w.mean(), 2) if len(w) else 0.0,
            "gem_verlies_pct": round(100 * l.mean(), 2) if len(l) else 0.0,
            "t": round(float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))), 2) if len(r) > 2 and r.std() > 0 else 0.0,
            "som_pct": round(100 * r.sum(), 1)}


def main():
    d, sim_dir, venue_dir, out = sys.argv[1:5]
    os.makedirs(out, exist_ok=True)
    kraken = set(data.beurzen(venue_dir).get("kraken", []))
    st = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sim_dir}/**/stats_*.parquet", recursive=True)], ignore_index=True)
    cand = st[st.gesimuleerd.fillna(False).astype(bool)]
    print("kandidaten", len(cand), flush=True)
    # 1) nu actief en met de hand te volgen (API, laatste 30 dagen)
    live = {}
    for a in cand.address:
        fl = [f for f in hl.fills(a, DATA_EIND, NU) if f["kind"] != "spot"]
        r30 = [x for x in trades_pct(fl) if x[2] >= NU - 30 * hl.DAY]
        n7 = sum(x[2] >= NU - 7 * hl.DAY for x in r30)
        n14 = sum(x[2] >= NU - 14 * hl.DAY for x in r30)
        uren = [(x[2] - x[1]) / 3.6e6 for x in r30]
        kr = np.mean([":" not in x[0] and to_base(x[0]) in kraken for x in r30]) if r30 else 0
        ok = n7 >= 1 and n14 >= 4 and len(r30) / 30 <= 3 and uren and np.median(uren) >= 2 and kr >= 0.7 and len(fl) < 9900
        if ok:
            live[a] = fl
        print(a, "actief+handmatig" if ok else "af", flush=True)
    # 2) keuzeperiode uit de dataset + test uit de API
    ds_f = data.fills(d, set(live))
    rows = []
    for a, api in live.items():
        x = ds_f[ds_f.address == a]
        hist = [{"time": int(t), "coin": c, "start": s, "after": af, "px": p}
                for t, c, s, af, p in zip(x.ts, x.coin, x.start, x.after, x.px) if t < DATA_EIND]
        keuze = [t for t in trades_pct(hist) if ":" not in t[0]]
        test = [t for t in trades_pct(api) if t[1] >= DATA_EIND and ":" not in t[0]]
        k, te = stats(keuze), stats(test)
        uren = [(t[2] - t[1]) / 3.6e6 for t in keuze + test]
        munten = pd.Series([t[0] for t in keuze + test]).value_counts(normalize=True).head(4)
        rows.append({"address": a, **{f"keuze_{n}": v for n, v in k.items()}, **{f"test_{n}": v for n, v in te.items()},
                     "houdtijd_mediaan_uur": round(float(np.median(uren)), 1) if uren else None,
                     "munten": ", ".join(f"{c} {100 * v:.0f}%" for c, v in munten.items()),
                     "median_av": float(cand.set_index("address").median_av[a])})
    tab = pd.DataFrame(rows)
    tab = tab[tab.keuze_trades >= 50].sort_values("keuze_t", ascending=False)
    tab["gekozen"] = False
    tab.loc[tab.index[:TOP], "gekozen"] = True
    tab.to_csv(f"{out}/overzicht.csv", index=False)
    sel = tab[tab.gekozen]
    json.dump({"regels": "VAST", "gekozen_op": pd.Timestamp(NU, unit="ms").strftime("%Y-%m-%d"),
               "wallets": sel[["address", "median_av"]].to_dict("records")}, open(f"{out}/selection.json", "w"), indent=1)
    json.dump(sorted(kraken), open(f"{out}/kraken.json", "w"))
    pd.set_option("display.width", 250)
    print(tab.head(12).T.to_string())


if __name__ == "__main__":
    main()
