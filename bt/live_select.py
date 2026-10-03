"""Live-selectie (vanaf 3-10-2026): 5 traders die nu actief zijn en met de hand na te doen zijn.
Kandidaten: alle H4-gesimuleerde wallets (100+ trades, kopieerbaar) met positieve Sharpe, op volgorde van Sharpe.
Eisen (laatste 30 dagen): >= 1 trade in de laatste 7 dagen, >= 4 in de laatste 14 dagen, gemiddeld <= 3 trades per dag,
mediane houdtijd >= 2 uur, >= 70% volume in Kraken-munten.
Gebruik: python -m bt.live_select <h4-sim-map> <beurzen-map> <uitmap>
"""

from __future__ import annotations

import glob
import json
import os
import sys
import time

import numpy as np
import pandas as pd

from bot import hl
from bt import data
from bt.h4b import profiel

TOP = 5
NU = int(time.time() * 1000) // hl.DAY * hl.DAY


def houdtijden(fl):
    open_t, out = {}, []
    for f in fl:
        c = f["coin"]
        if f["start"] == 0 or (f["after"] != 0 and f["after"] * f["start"] < 0):
            open_t[c] = f["time"]
        elif f["after"] == 0 and c in open_t:
            out.append((f["time"] - open_t.pop(c)) / 3_600_000)
    return out


def main():
    sim_dir, venue_dir, out = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(out, exist_ok=True)
    kraken = set(data.beurzen(venue_dir).get("kraken", []))
    st = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sim_dir}/**/stats_*.parquet", recursive=True)], ignore_index=True)
    sim = st[st.gesimuleerd.fillna(False).astype(bool) & (st.sharpe > 0)].sort_values("sharpe", ascending=False)
    rows, n = [], 0
    for _, w in sim.iterrows():
        fl = hl.fills(w.address, NU - 30 * hl.DAY, NU)
        m30 = profiel(fl, kraken, NU - 30 * hl.DAY, NU)
        m14 = profiel(fl, kraken, NU - 14 * hl.DAY, NU)
        m7 = profiel(fl, kraken, NU - 7 * hl.DAY, NU)
        h = houdtijden([f for f in fl if f["kind"] == "perp"])
        med_h = float(np.median(h)) if h else None
        ok = (m7["trades"] >= 1 and m14["trades"] >= 4 and m30["trades"] / 30 <= 3 and med_h is not None
              and med_h >= 2 and m30["aandeel_kraken_pct"] >= 70 and len(fl) < 9900)
        kies = ok and n < TOP
        n += kies
        rows.append({"address": w.address, "gekozen": kies, "ok": ok, "sharpe_hist": round(w.sharpe, 2),
                     "kopie_rendement_hist_pct": round(100 * w.rendement, 1), "maxdd_hist_pct": round(100 * w.maxdd, 1),
                     "trades_30d": m30["trades"], "trades_per_dag_30d": round(m30["trades"] / 30, 2), "trades_7d": m7["trades"],
                     "winst_pct_30d": m30["winst_pct_trades"], "pnl_30d_usd": m30["pnl_usd"],
                     "houdtijd_mediaan_uur": None if med_h is None else round(med_h, 1),
                     "kraken_pct_30d": m30["aandeel_kraken_pct"], "munten_30d": m30["munten"], "median_av": w.median_av})
        print(w.address, "GEKOZEN" if kies else ("ok" if ok else "af"), flush=True)
        if n >= TOP and len(rows) >= 60:
            break
    tab = pd.DataFrame(rows)
    tab.to_csv(f"{out}/overzicht.csv", index=False)
    sel = tab[tab.gekozen]
    json.dump({"regels": "LIVE", "gekozen_op": pd.Timestamp(NU, unit="ms").strftime("%Y-%m-%d"),
               "wallets": sel[["address", "median_av"]].to_dict("records")}, open(f"{out}/selection.json", "w"), indent=1)
    json.dump(sorted(kraken), open(f"{out}/kraken.json", "w"))
    print(sel.T.to_string())


if __name__ == "__main__":
    main()
