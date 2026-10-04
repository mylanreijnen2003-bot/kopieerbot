"""Test 2 (vooraf vastgelegd 4 okt 17:20): rotatie 'constantheid' zoals Mylan handelt.
Zelfde keuzemomenten als bt.rot_stats (elke 14 d, okt 2025 - jul 2026), terugkijken 42/84 d, alleen traders met
>= 30 trades, mediaan houdtijd >= 12 u, gem. >= 1% per trade (Kraken-basis) in de terugkijkperiode.
Vooruit-varianten (potje -20% stop, inzet = potje / K):
  kr0  Kraken, long+short, 0,32% kosten, geen tradestop     kr10 idem met tradestop -10%
  bv0  Bitvavo: alleen longs in Bitvavo-munten, 0,60% kosten, geen tradestop
  bv20 idem tradestop -20%                                   bv10 idem tradestop -10%
Stappen: `bars <bt-part-map> <uit>` | `stats <deel> <aantal> <universe> <fillsmap> <bars.parquet> <beurzenmap> <uit>`
         | `kies <statsmap> <uit>`
"""

from __future__ import annotations

import glob
import os
import sys

import numpy as np
import pandas as pd

from bt import data
from bt.bot_stats import bot_trades
from bt.engine import to_base
from bt.rot_stats import DAG, EIND, TS, k90

UUR = 3_600_000
LS, HS = [42, 84], [14, 28]
# naam: (soort, kosten, tradestop, hefboom, potjestop)
VAR = {"kr0": ("kr", 0.0032, None, 1, 0.20), "kr10": ("kr", 0.0032, 0.10, 1, 0.20),
       "bv0": ("bv", 0.0060, None, 1, 0.20), "bv20": ("bv", 0.0060, 0.20, 1, 0.20), "bv10": ("bv", 0.0060, 0.10, 1, 0.20)}
if os.environ.get("HEFBOOM"):
    # 2x: liquidatie bij 50% koers tegen (hele inzet van die trade weg); potjestop -20% of -40%
    VAR = {"kr0": ("kr", 0.0032, None, 1, 0.20), "kr0_2x_p20": ("kr", 0.0032, None, 2, 0.20),
           "kr0_2x_p40": ("kr", 0.0032, None, 2, 0.40), "kr0_1x_p40": ("kr", 0.0032, None, 1, 0.40)}


def bars(src, out):
    os.makedirs(out, exist_ok=True)
    b = pd.concat([pd.read_parquet(p, columns=["coin", "hour", "h", "l"])
                   for p in glob.glob(f"{src}/**/bars_*.parquet", recursive=True)], ignore_index=True)
    b = b.groupby(["coin", "hour"], as_index=False).agg(h=("h", "max"), l=("l", "min"))
    b.to_parquet(f"{out}/ohlc.parquet", index=False)
    print(len(b), b.coin.nunique())


def stats(shard, n, upath, d, bpath, vdir, out):
    os.makedirs(out, exist_ok=True)
    v = data.beurzen(vdir)
    kraken, bitvavo = set(v.get("kraken", [])), set(v.get("bitvavo", []))
    b = pd.read_parquet(bpath)
    bars_ = {c: (x.hour.to_numpy(np.int64), x.h.to_numpy(), x.l.to_numpy()) for c, x in b.sort_values("hour").groupby("coin")}

    def gestopt(coin, o, c, p1, dr, s):
        if coin not in bars_:
            return False
        h, hi, lo = bars_[coin]
        i, j = np.searchsorted(h, (o // UUR + 1) * UUR), np.searchsorted(h, c)
        if j <= i:
            return False
        return bool((lo[i:j] <= p1 * (1 - s)).any()) if dr > 0 else bool((hi[i:j] >= p1 * (1 + s)).any())

    u = pd.read_parquet(upath).sort_values("address").iloc[shard::n]
    rows = []
    for chunk in np.array_split(u.address.values, max(1, len(u) // 400)):
        f = data.fills(d, set(chunk))
        f = f[f.ts < EIND]
        for a, x in f.groupby("address"):
            fl = [{"time": int(t), "coin": c, "start": s_, "after": af, "px": p}
                  for t, c, s_, af, p in zip(x.ts, x.coin, x.start, x.after, x.px)]
            bt_ = bot_trades(fl)
            if len(bt_) < 30:
                continue
            co = np.array([t[0] for t in bt_])
            o = np.array([t[1] for t in bt_], dtype=np.int64)
            c = np.array([t[2] for t in bt_], dtype=np.int64)
            dr = np.array([t[3] for t in bt_])
            p1 = np.array([t[4] for t in bt_])
            po = np.array([t[6] for t in bt_])
            raw = dr * (po / p1 - 1)
            base = np.array([to_base(z) for z in co])
            kr = np.isin(base, list(kraken))
            bv = np.isin(base, list(bitvavo)) & (dr > 0)
            fts = x.ts.values
            for T in TS:
                for L in LS:
                    m = (o >= T - L * DAG) & (c < T)
                    nn = int(m.sum())
                    if nn < 30:
                        continue
                    lr = raw[m] - 0.0032
                    hold = float(np.median((c[m] - o[m]) / 3.6e6))
                    if hold < 12 or lr.mean() < 0.01:
                        continue
                    k = k90(o[m], c[m])
                    fm = fts[(fts >= T - L * DAG) & (fts < T)]
                    rec = {"address": a, "T": T, "L": L, "n": nn, "K": k, "houdtijd_uur": round(hold, 1),
                           "gem_r_pct": round(100 * lr.mean(), 3), "winst_pct": round(100 * (lr > 0).mean(), 1),
                           "per_maand_pct": round(100 * lr.sum() / k / (L / 30.44), 2),
                           "kraken_pct": round(100 * kr[m].mean(), 1), "bv_pct": round(100 * bv[m].mean(), 1),
                           "fills_per_dag": float(pd.Series(fm // DAG).value_counts().median()) if len(fm) else 0.0,
                           "dagen_sinds_laatste": round((T - c[m].max()) / DAG, 1)}
                    if os.environ.get("HEFBOOM") and L != 84:
                        continue
                    for H in HS:
                        fw = np.where((o >= T) & (o < T + H * DAG))[0]
                        fw = fw[np.argsort(c[fw])]
                        for naam, (soort, kost, stop, lev, pstop) in VAR.items():
                            mag = kr if soort == "kr" else bv
                            tot = 0.0
                            for i in fw:
                                if not mag[i]:
                                    continue
                                r = raw[i]
                                if stop and gestopt(co[i], o[i], c[i], p1[i], dr[i], stop):
                                    r = -stop
                                if lev > 1 and gestopt(co[i], o[i], c[i], p1[i], dr[i], 1.0 / lev):
                                    r = -1.0 / lev
                                tot += lev * (r - kost) / k
                                if tot <= -pstop:
                                    tot = -pstop
                                    break
                            rec[f"v{H}_{naam}"] = round(100 * tot, 3)
                    rows.append(rec)
        print(f"{len(rows)} rijen", flush=True)
    pd.DataFrame(rows).to_parquet(f"{out}/bv_{shard}.parquet", index=False)


def kies(sdir, out):
    os.makedirs(out, exist_ok=True)
    from bot import hl
    btc = {t: v[0] for t, v in hl.candles("BTC", TS[0] - DAG, EIND + DAG, "1d").items()}

    def bret(T, H):
        a, b = btc.get(T), btc.get(T + H * DAG)
        return (b / a - 1) * 100 if a and b else np.nan
    s = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sdir}/**/bv_*.parquet", recursive=True)], ignore_index=True)
    basis = s[(s.per_maand_pct > 0) & (s.K <= 5) & (s.fills_per_dag <= 150) & (s.dagen_sinds_laatste <= 7)]
    sam = []
    for naam in VAR:
        ok = basis[basis.bv_pct >= 50] if naam.startswith("bv") else basis[basis.kraken_pct >= 70]
        for L in ([84] if os.environ.get("HEFBOOM") else LS):
            for H in HS:
                ts = [t for t in (TS if H == 14 else TS[::2]) if t + H * DAG <= EIND]
                for N in [5, 10, 20]:
                    top, alle, br = [], [], []
                    for T in ts:
                        br.append(bret(T, H))
                        g = ok[(ok["T"] == T) & (ok["L"] == L)].sort_values(["winst_pct", "per_maand_pct"], ascending=False)
                        top.append(g.head(N)[f"v{H}_{naam}"].mean() if len(g) else 0.0)
                        alle.append(g[f"v{H}_{naam}"].mean() if len(g) else 0.0)
                    top, alle, br = np.nan_to_num(np.array(top)), np.nan_to_num(np.array(alle)), np.array(br)
                    op, neer = br > 0, br <= 0
                    pm = 30.44 / H
                    sam.append({"variant": naam, "L": L, "H": H, "N": N, "perioden": len(ts),
                                "top_per_maand_pct": round(top.mean() * pm, 2),
                                "top_positief_pct": round(100 * (top > 0).mean()),
                                "top_slechtste_pct": round(top.min(), 2),
                                "top_totaal_pct": round(100 * (np.prod(1 + top / 100) - 1), 1),
                                "alle_per_maand_pct": round(alle.mean() * pm, 2),
                                "btc_stijgend_n": int(op.sum()),
                                "top_btc_stijgend_pct": round(top[op].mean(), 2) if op.any() else None,
                                "top_btc_dalend_pct": round(top[neer].mean(), 2) if neer.any() else None,
                                "btc_in_stijgende_pct": round(np.nanmean(br[op]), 2) if op.any() else None,
                                "alle_btc_stijgend_pct": round(alle[op].mean(), 2) if op.any() else None})
    sd = pd.DataFrame(sam)
    sd.to_csv(f"{out}/samenvatting.csv", index=False)
    print(sd.to_string(index=False))


if __name__ == "__main__":
    c = sys.argv[1]
    if c == "bars":
        bars(*sys.argv[2:4])
    elif c == "stats":
        stats(int(sys.argv[2]), int(sys.argv[3]), *sys.argv[4:9])
    else:
        kies(*sys.argv[2:4])
