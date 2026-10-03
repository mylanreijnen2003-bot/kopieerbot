"""H4b: top 5 traders die echt te kopiëren zijn (regels in H4b.md) + overzicht per trader.
Kiezen alleen op data t/m 18-8-2026. Cijfers van na 18-8 ('deze maand') zijn alleen ter info.
Gebruik: python -m bt.h4b <h4-sim-map> <beurzen-map> <uitmap>
"""

from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter

import pandas as pd

from bot import hl
from bt import data
from bt.engine import to_base

TOP = 5
PRE = (pd.Timestamp("2026-06-24").value // 10**6, pd.Timestamp("2026-08-19").value // 10**6)   # laatste 8 weken
MAAND = (pd.Timestamp("2026-09-03").value // 10**6, pd.Timestamp("2026-10-03").value // 10**6)  # laatste 30 dagen
MIN_ACTIEVE_DAGEN_8W = 8
MIN_KRAKEN = 0.70


def trades(fl):
    """Afgeronde trades: per munt van plat naar plat (of richtingwissel); winst = som closedPnl - fees."""
    acc, out = {}, []
    for f in fl:
        c = f["coin"]
        acc[c] = acc.get(c, 0.0) + f["pnl"] - f["fee"]
        if f["start"] != 0 and (f["after"] == 0 or f["after"] * f["start"] < 0):
            out.append((c, acc.pop(c)))
    return out


def profiel(fl, kraken, t0, t1):
    fl = [f for f in fl if t0 <= f["time"] < t1 and f["kind"] != "spot"]
    tr = trades(fl)
    dagen = {f["time"] // hl.DAY for f in fl}
    notional = Counter()
    for f in fl:
        notional[to_base(f["coin"]) if f["kind"] == "perp" else f["coin"]] += abs(f["signed"]) * f["px"]
    tot = sum(notional.values()) or 1.0
    op_kraken = sum(v for c, v in notional.items() if c in kraken) / tot
    weken = (t1 - t0) / (7 * hl.DAY)
    return {"fills": len(fl), "actieve_dagen": len(dagen), "trades": len(tr),
            "trades_per_week": round(len(tr) / weken, 1),
            "winst_pct_trades": round(100 * sum(p > 0 for _, p in tr) / len(tr), 1) if tr else None,
            "pnl_usd": round(sum(p for _, p in tr), 0),
            "aandeel_kraken_pct": round(100 * op_kraken, 1),
            "munten": ", ".join(f"{c} {100 * v / tot:.0f}%" for c, v in notional.most_common(4))}


def main():
    sim_dir, venue_dir, out = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(out, exist_ok=True)
    kraken = set(data.beurzen(venue_dir).get("kraken", []))
    st = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sim_dir}/**/stats_*.parquet", recursive=True)], ignore_index=True)
    sim = st[st.gesimuleerd.fillna(False).astype(bool)]
    elig = sim[(sim.gekopieerd >= 50) & (~sim.gestopt.astype(bool))].sort_values("sharpe", ascending=False)
    rows, gekozen = [], []
    for _, w in elig.iterrows():
        fl = hl.fills(w.address, PRE[0], MAAND[1])
        pre, maand = profiel(fl, kraken, *PRE), profiel(fl, kraken, *MAAND)
        ok = pre["actieve_dagen"] >= MIN_ACTIEVE_DAGEN_8W and pre["aandeel_kraken_pct"] >= 100 * MIN_KRAKEN
        kies = ok and len(gekozen) < TOP
        if kies:
            gekozen.append(w)
        rows.append({"address": w.address, "gekozen": kies, "geschikt_h4b": ok, "sharpe_keuze": round(w.sharpe, 2),
                     "rendement_kopie_keuze_pct": round(100 * w.rendement, 1), "maxdd_keuze_pct": round(100 * w.maxdd, 1),
                     "trades_keuze": int(w.trades), "fills_per_dag_mediaan": w.fills_per_dag_mediaan,
                     **{f"8w_{k}": v for k, v in pre.items()}, **{f"maand_{k}": v for k, v in maand.items()}})
        print(w.address, "gekozen" if kies else ("geschikt" if ok else "af"), pre["actieve_dagen"], pre["aandeel_kraken_pct"], flush=True)
    tab = pd.DataFrame(rows)
    tab.to_csv(f"{out}/overzicht.csv", index=False)
    sel = tab[tab.gekozen]
    json.dump({"regels": "H4b", "data_tot": "2026-08-18", "start_papier": "2026-08-19",
               "trechter": {"h4_geschikt": int(len(elig)), "h4b_geschikt": int(tab.geschikt_h4b.sum())},
               "wallets": [{"address": a, "median_av": float(st.set_index("address").median_av[a])} for a in sel.address]},
              open(f"{out}/selection.json", "w"), indent=1)
    json.dump(sorted(kraken), open(f"{out}/kraken.json", "w"))
    print(sel.T.to_string())


if __name__ == "__main__":
    main()
