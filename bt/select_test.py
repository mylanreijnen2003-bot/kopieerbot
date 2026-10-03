"""Backtest stap C: wallets kiezen op TRAIN (t/m feb 2026), dan eerlijk testen op TEST (mrt t/m 18 aug 2026).
Gebruik: python -m bt.select_test <datamap> <train-uitvoermap> <uitmap>
"""

from __future__ import annotations

import glob
import json
import math
import os
import sys

import numpy as np
import pandas as pd

from bt import data
from bt.engine import run
from bt.variants import variants


def nw_t(x, lag=5):
    x = np.asarray(x, float)
    n = len(x)
    if n < 10:
        return float("nan")
    e = x - x.mean()
    s = (e @ e) / n
    for k in range(1, lag + 1):
        s += 2 * (1 - k / (lag + 1)) * (e[k:] @ e[:-k]) / n
    return float(x.mean() / math.sqrt(s / n)) if s > 0 else float("nan")


def portfolio(eqs: np.ndarray, btc_ret):
    """eqs: wallets x dagen (equity per potje)."""
    n = eqs.shape[0]
    curve = np.concatenate([[1000.0 * n], eqs.sum(axis=0)])
    r = np.diff(curve) / curve[:-1]
    excl = np.sort(r)[:-2] if len(r) > 3 else r
    res = {"wallets": int(n), "rendement": round(float(curve[-1] / curve[0] - 1), 4),
           "gem_dag": round(float(r.mean()), 5), "nw_t": round(nw_t(r), 2),
           "zonder_2_beste_dagen": round(float(excl.mean()), 5),
           "max_drawdown": round(float((curve / np.maximum.accumulate(curve) - 1).min()), 4),
           "pct_potjes_winst": round(float((eqs[:, -1] > 1000).mean()), 3),
           "btc": None if btc_ret is None else round(btc_ret, 4)}
    res["GO"] = bool(res["gem_dag"] > 0 and res["nw_t"] >= 3 and res["zonder_2_beste_dagen"] > 0
                     and res["max_drawdown"] >= -0.20 and (btc_ret is None or res["rendement"] > btc_ret))
    return res, curve


def main():
    d, tr_dir, out = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(out, exist_ok=True)
    st = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{tr_dir}/**/stats_*.parquet", recursive=True)], ignore_index=True)
    sim = st[st.gesimuleerd.fillna(False).astype(bool)]
    elig = sim[(sim.gekopieerd >= 50) & (~sim.gestopt.astype(bool))].sort_values("sharpe", ascending=False)
    n_q = max(1, min(100, len(elig) // 5))
    sets = {"kwintiel": list(elig.address[:n_q]), "top10": list(elig.address[:10]), "top30": list(elig.address[:30]),
            "breed": list(elig.address[elig.gem > 0]), "controle_onderste_kwintiel": list(elig.address[-n_q:])}
    union = sorted(set().union(*sets.values()))
    print("trechter", {"universum": len(st), "gesimuleerd": len(sim), "geschikt": len(elig)}, {k: len(v) for k, v in sets.items()}, flush=True)

    bars, fund, av, venues = data.bars(d), data.funding(d), data.av(d), data.beurzen(d)
    var = variants(venues)
    f_all = data.fills(d, set(union))
    f_all = f_all[(f_all.ts >= data.TEST[0]) & (f_all.ts < data.TEST[1])]
    med = st.set_index("address").median_av
    eq = {k: {} for k in var}
    per_wallet = []
    for i, a in enumerate(union):
        fl = data.as_list(f_all[f_all.address == a])
        av_t, av_v = av.get(a, ([], []))
        for k, cfg in var.items():
            e, p = run(cfg, fl, bars, fund, av_t, av_v, med[a], *data.TEST)
            eq[k][a] = e
            if k == "P":
                per_wallet.append({"address": a, "eind": round(e[-1], 1), "gestopt": p.dead, "reden": p.reden,
                                   "gekopieerd": p.copied, "max_hefboom": round(p.max_lev, 2),
                                   "funding": round(p.fund, 1), "kosten": round(p.cost, 1)})
        if i % 25 == 0:
            print(f"test {i}/{len(union)}", flush=True)

    btc = bars.get("BTC", {})
    hrs = sorted(h for h in btc if data.TEST[0] - 86_400_000 <= h < data.TEST[1])
    btc_ret = btc[hrs[-1]][3] / btc[hrs[0]][3] - 1 if len(hrs) > 1 else None
    table, curves = [], {}
    for s, addrs in sets.items():
        for k, cfg in var.items():
            if not addrs:
                continue
            res, curve = portfolio(np.array([eq[k][a] for a in addrs]), btc_ret)
            table.append({"selectie": s, "variant": k, "omschrijving": cfg.name, **res})
            curves[f"{s}|{k}"] = curve
    tab = pd.DataFrame(table)
    prim = tab[(tab.selectie == "kwintiel") & (tab.variant == "P")].iloc[0].to_dict()
    uitslag = {"vastgelegd": "BACKTEST.md", "train": "2025-07-28 t/m 2026-02-28", "test": "2026-03-01 t/m 2026-08-18",
               "trechter": {"universum": len(st), "gesimuleerd": len(sim), "geschikt": len(elig),
                            **{f"set_{k}": len(v) for k, v in sets.items()}},
               "beurzen": {k: len(v) for k, v in venues.items()}, "btc_test": btc_ret,
               "hoofdtoets": prim, "oordeel": "GO" if prim["GO"] else "NO-GO",
               "n_combinaties_GO": int(tab.GO.sum()), "n_combinaties": len(tab)}
    json.dump(uitslag, open(f"{out}/uitslag.json", "w"), indent=1, default=str)
    tab.to_csv(f"{out}/tabel.csv", index=False)
    pd.DataFrame(per_wallet).merge(elig[["address", "sharpe", "rendement", "maxdd", "fills_per_dag_mediaan", "maker_aandeel",
                                         "liquidaties"]].rename(columns=lambda c: c if c == "address" else f"train_{c}"),
                                   on="address", how="left").to_csv(f"{out}/per_wallet_test_P.csv", index=False)
    pd.DataFrame(curves).to_csv(f"{out}/curves.csv", index=False)
    elig.to_csv(f"{out}/train_geschikt.csv", index=False)
    print(json.dumps(uitslag, indent=1, default=str))
    print(tab.to_string(index=False))


if __name__ == "__main__":
    main()
