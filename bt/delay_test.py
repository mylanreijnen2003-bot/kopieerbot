"""Vertragingstoets top 30 (brede scan): wat blijft over als je X minuten na de trader in- en uitstapt?
5 min: 5m-kaarsen (API geeft ~17 dagen terug). 15 min: 15m-kaarsen (~52 dagen). Kosten 0,2% per trade.
Gebruik: python -m bt.delay_test <top30.csv> <check_open.csv> <uit.csv>
"""

import sys
import time

import numpy as np
import pandas as pd

from bot import hl

EIND = pd.Timestamp("2026-08-19").value // 10**6
NU = int(time.time() * 1000) // hl.DAY * hl.DAY
KOSTEN = 0.002


def trades_detail(fl):
    """Per munt plat->plat: (coin, open_t, sluit_t, richting, gem_instap, gem_uitstap)."""
    st, out = {}, []
    for f in fl:
        c, s, a, px = f["coin"], f["start"], f["after"], f["px"]
        if s == 0 and a != 0:
            st[c] = {"dir": 1 if a > 0 else -1, "iq": abs(a), "ic": abs(a) * px, "uq": 0.0, "uc": 0.0, "t": f["time"]}
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
            out.append((c, p["t"], f["time"], p["dir"], p["ic"] / p["iq"], p["uc"] / p["uq"]))
            st.pop(c)
            if flip:
                st[c] = {"dir": 1 if a > 0 else -1, "iq": abs(a), "ic": abs(a) * px, "uq": 0.0, "uc": 0.0, "t": f["time"]}
    return out


def kaarsen(coin, interval, cache):
    if (coin, interval) not in cache:
        mins = {"5m": 5, "15m": 15}[interval]
        c = hl.candles(coin, NU - 4900 * mins * 60_000, NU, interval)
        t = np.array(sorted(c), dtype=np.int64)
        cache[(coin, interval)] = (t, np.array([c[x][0] for x in t]))
    return cache[(coin, interval)]


def prijs_na(coin, ts, delay_min, interval, cache):
    t, o = kaarsen(coin, interval, cache)
    if len(t) == 0 or ts < t[0]:
        return None
    i = np.searchsorted(t, ts + delay_min * 60_000)
    return float(o[i]) if i < len(t) else None


def main():
    top = pd.read_csv(sys.argv[1]).merge(pd.read_csv(sys.argv[2])[["address", "accountwaarde_nu"]], on="address")
    cache, rows = {}, []
    for _, w in top.iterrows():
        fl = [f for f in hl.fills(w.address, EIND, NU) if f["kind"] == "perp"]
        tr = [t for t in trades_detail(fl) if t[1] >= EIND]
        k = int(w.K)
        res = {"address": w.address, "K": k, "trades_test": len(tr),
               "trades_per_dag": round(len(tr) / ((NU - EIND) / hl.DAY), 1), "accountwaarde_nu": w.accountwaarde_nu}
        for naam, d, iv in (("5min", 5, "5m"), ("15min", 15, "15m")):
            orig, vert = [], []
            for coin, o, c, richting, pin, pout in tr:
                a, b = prijs_na(coin, o, d, iv, cache), prijs_na(coin, c, d, iv, cache)
                if a is None or b is None:
                    continue
                orig.append(richting * (pout / pin - 1) - KOSTEN)
                vert.append(richting * (b / a - 1) - KOSTEN)
            res[f"{naam}_trades"] = len(orig)
            res[f"{naam}_gem_r_origineel_pct"] = round(100 * np.mean(orig), 3) if orig else None
            res[f"{naam}_gem_r_vertraagd_pct"] = round(100 * np.mean(vert), 3) if vert else None
            res[f"{naam}_winst_pct_vertraagd"] = round(100 * np.mean(np.array(vert) > 0), 1) if vert else None
            res[f"{naam}_euro_origineel_op_100"] = round(100 / k * sum(orig), 2)
            res[f"{naam}_euro_vertraagd_op_100"] = round(100 / k * sum(vert), 2)
        rows.append(res)
        print(res, flush=True)
    pd.DataFrame(rows).to_csv(sys.argv[3], index=False)


if __name__ == "__main__":
    main()
