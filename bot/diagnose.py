"""Stap 1: waarom gingen potjes in de oude papieren bot (H3) naar $0?

Speelt de OUDE logica exact na voor de 5 slechtste wallets en logt per fill: munt, soort (perp/spot/hip3),
startpositie leider, gebruikte accountwaarde, k, eigen hefboom en equity. Plus per munt de winst/verlies.
Gebruik: python -m bot.diagnose <uitmap>
"""

from __future__ import annotations

import bisect
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd

from bot import hl

START = "2026-08-19"
CAP = 1000.0
EXEC_BP, FEE = 0.0005, 0.00045


def ms(day):
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def dstr(t):
    return datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def old_av_points(addr):
    data = hl.info({"type": "portfolio", "user": addr})
    pts = []
    for period, payload in data if isinstance(data, list) else []:
        if period in ("allTime", "month", "week", "day"):
            pts += [(int(t), float(v)) for t, v in payload.get("accountValueHistory", [])]
    return sorted(set(pts))


def old_av_at(pts, t, fallback):
    i = bisect.bisect_right(pts, (t, float("inf"))) - 1
    v = pts[i][1] if i >= 0 else (pts[0][1] if pts else None)
    return (v, "historie") if v and v > 1000 else (fallback, "fallback_mediaan")


def run(addr, w_sel, dead_day, out_rows, day_rows):
    s, e = ms(START), ms(dead_day) + hl.DAY - 1
    fl = hl.fills(addr, s, e)
    pts = old_av_points(addr)
    perp_pts, _ = hl.av_points(addr, perp_only=True)
    coins = sorted({f["coin"] for f in fl})
    closes = {c: {dstr(t): v[3] for t, v in hl.candles(c, s - hl.DAY, e, "1d").items()} for c in coins}
    cash, units, last_px, cash_c = CAP, {}, {}, {}
    eq_day, touched = CAP, set()
    by_day = {}
    for f in fl:
        by_day.setdefault(dstr(f["time"]), []).append(f)
    summary = {"address": addr, "median_av": w_sel["median_av"], "fills": len(fl),
               "per_soort": pd.Series([f["kind"] for f in fl]).value_counts().to_dict() if fl else {},
               "overgenomen_posities": 0, "max_hefboom": 0.0, "av_min": None, "av_max": None, "av_fallback_n": 0}
    avs = []
    for day in pd.date_range(START, dead_day).strftime("%Y-%m-%d"):
        for f in by_day.get(day, []):
            c = f["coin"]
            av, src = old_av_at(pts, f["time"], w_sel["median_av"])
            avs.append(av)
            summary["av_fallback_n"] += src != "historie"
            k = max(eq_day, 0.0) / av
            target = k * f["after"]
            inherited = c not in touched and abs(f["start"]) > 0
            summary["overgenomen_posities"] += inherited
            touched.add(c)
            delta = target - units.get(c, 0.0)
            if abs(delta) > 1e-12:
                exe = f["px"] * (1 + EXEC_BP if delta > 0 else 1 - EXEC_BP)
                flow = delta * exe + abs(delta) * exe * FEE
                cash -= flow
                cash_c[c] = cash_c.get(c, 0.0) - flow
                units[c] = units.get(c, 0.0) + delta
            last_px[c] = f["px"]
            gross = sum(abs(u) * last_px.get(cc, 0) for cc, u in units.items())
            eq_now = cash + sum(u * last_px.get(cc, 0) for cc, u in units.items())
            lev = gross / max(eq_day, 1e-9)
            summary["max_hefboom"] = max(summary["max_hefboom"], lev)
            out_rows.append({"address": addr, "time": f["time"], "day": day, "coin": c, "soort": f["kind"],
                             "dir": f["dir"], "start_leider": f["start"], "na_leider": f["after"], "px": f["px"],
                             "av_gebruikt": av, "av_bron": src,
                             "av_perp_interp": hl.av_at(perp_pts, f["time"]), "k": k, "doel": target,
                             "eigen_units": units.get(c, 0.0), "eigen_notional_munt": units.get(c, 0.0) * f["px"],
                             "overgenomen": inherited, "bruto_hefboom": lev, "equity_bij_fill": eq_now})
        for c in units:
            px = closes.get(c, {}).get(day)
            if px:
                last_px[c] = px
        eq_day = max(cash + sum(u * last_px.get(c, 0) for c, u in units.items()), 0.0)
        day_rows.append({"address": addr, "day": day, "equity_oud": eq_day,
                         "leider_av_eind": hl.av_at(perp_pts, ms(day) + hl.DAY - 1),
                         "leider_av_oud": old_av_at(pts, ms(day) + hl.DAY - 1, w_sel["median_av"])[0]})
    pnl_c = {c: cash_c.get(c, 0.0) + units.get(c, 0.0) * last_px.get(c, 0.0) for c in set(cash_c) | set(units)}
    summary["slechtste_munten"] = dict(sorted(pnl_c.items(), key=lambda x: x[1])[:5])
    summary["av_min"], summary["av_max"] = (min(avs), max(avs)) if avs else (None, None)
    summary["equity_eind"] = eq_day
    return summary


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    sel = {w["address"]: w for w in json.load(open("data/selection.json"))["wallets"]}
    led = pd.read_csv("data/h3_oud/ledger.csv")
    worst = led.groupby("address").equity.min().nsmallest(5).index
    dead = led[led.dead].groupby("address").day.min()
    rows, days, sums = [], [], []
    for a in worst:
        print("wallet", a, flush=True)
        sums.append(run(a, sel[a], dead.get(a, led.day.max()), rows, days))
    pd.DataFrame(rows).to_csv(f"{out}/per_fill.csv", index=False)
    pd.DataFrame(days).to_csv(f"{out}/per_dag.csv", index=False)
    json.dump(sums, open(f"{out}/samenvatting.json", "w"), indent=1, default=str)
    print(json.dumps(sums, indent=1, default=str))


if __name__ == "__main__":
    main()
