"""Tussenstand en (na >= 90 dagen) oordeel H3b. Zelfde GO-regels als H3 (zie REGELS.md).
Gebruik: python -m bot.report <map>
"""

from __future__ import annotations

import json
import math
import sys

import numpy as np
import pandas as pd

from bot import hl
from bot.paper import ms


def nw_t(x, lag=5):
    """Newey-West t: t-waarde die rekening houdt met samenhang tussen opeenvolgende dagen."""
    x = np.asarray(x, float)
    n = len(x)
    if n < 10:
        return float("nan")
    e = x - x.mean()
    s = (e @ e) / n
    for k in range(1, lag + 1):
        s += 2 * (1 - k / (lag + 1)) * (e[k:] @ e[:-k]) / n
    return float(x.mean() / math.sqrt(s / n)) if s > 0 else float("nan")


def main():
    d = sys.argv[1]
    led = pd.read_csv(f"{d}/ledger.csv")
    state = json.load(open(f"{d}/state.json"))
    bad = {a for a, w in state["wallets"].items() if w.get("onvolledig")}
    use = led[~led.address.isin(bad)]
    n_w = use.address.nunique()
    eq = use.groupby("day").equity.sum().sort_index()
    start = sim_cap = 1000.0 * n_w
    curve = pd.concat([pd.Series({"start": start}), eq])
    ret = curve.pct_change().dropna()
    dd = float((curve / curve.cummax() - 1).min())
    btc = hl.candles("BTC", ms(eq.index[0]) - 2 * hl.DAY, ms(eq.index[-1]) + hl.DAY, "1d")
    btc = {pd.Timestamp(t, unit="ms").strftime("%Y-%m-%d"): v[3] for t, v in btc.items()}
    prev = (pd.Timestamp(eq.index[0]) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    btc_ret = btc[eq.index[-1]] / btc[prev] - 1 if eq.index[-1] in btc and prev in btc else None
    last = use[use.day == eq.index[-1]].set_index("address")
    pots = {a: w["pot"] for a, w in state["wallets"].items() if a not in bad}
    redenen = pd.Series([p["reden"] for p in pots.values() if p["dead"]]).value_counts().to_dict()
    days = len(ret)
    res = {
        "regels": state.get("regels", "H3b"), "periode": [eq.index[0], eq.index[-1]], "dagen": days, "wallets": int(n_w),
        "uitgesloten_onvolledig": len(bad), "totaal_rendement": round(float(eq.iloc[-1] / sim_cap - 1), 4),
        "max_drawdown": round(dd, 4), "gem_dagrendement": round(float(ret.mean()), 5), "nw_t": round(nw_t(ret.values), 2),
        "zonder_2_beste_dagen": round(float(ret.drop(ret.nlargest(2).index).mean()), 5) if days > 3 else None,
        "btc_zelfde_periode": None if btc_ret is None else round(btc_ret, 4),
        "gestopte_wallets": int(last.dead.sum()), "stopredenen": redenen,
        "wallets_in_winst": int((last.equity > 1000).sum()), "laagste_potje": round(float(last.equity.min()), 1),
        "max_hefboom_ooit": round(max(p["max_hefboom"] for p in pots.values()), 2),
        "kosten_totaal": round(sum(p["kosten"] for p in pots.values()), 1),
        "funding_totaal": round(sum(p["funding"] for p in pots.values()), 1),
        "fills_gekopieerd": sum(p["gekopieerd"] for p in pots.values()),
        "fills_overgeslagen": {k: sum(p["overgeslagen"][k] for p in pots.values()) for k in ("spot", "hip3", "geerfd")},
        "beste5": last.equity.nlargest(5).round(1).to_dict(), "slechtste5": last.equity.nsmallest(5).round(1).to_dict(),
    }
    if days < 90:
        res["oordeel"] = f"TUSSENSTAND ({days} van 90 dagen) - beschrijvend, niets aanpassen"
    else:
        go = res["gem_dagrendement"] > 0 and res["nw_t"] >= 3 and (res["zonder_2_beste_dagen"] or -1) > 0 and dd >= -0.20
        res["oordeel"] = "GO" if go else "NO-GO"
    json.dump(res, open(f"{d}/rapport.json", "w"), indent=1, default=str)
    eq.rename("equity").to_csv(f"{d}/portefeuille.csv")
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
