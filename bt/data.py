"""Inladen van de backtestdata (uitvoer van bt.extract en bt.prep)."""

from __future__ import annotations

import glob
import json

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

TRAIN = (pd.Timestamp("2025-07-28").value // 10**6, pd.Timestamp("2026-03-01").value // 10**6)
TEST = (pd.Timestamp("2026-03-01").value // 10**6, pd.Timestamp("2026-08-19").value // 10**6)


def bars(d):
    b = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{d}/**/bars_*.parquet", recursive=True)], ignore_index=True)
    b = b.sort_values("t_first")
    g = b.groupby(["coin", "hour"])
    first = g.o.first()
    b = b.sort_values("t_last")
    g = b.groupby(["coin", "hour"])
    agg = pd.DataFrame({"o": first, "h": g.h.max(), "l": g.l.min(), "c": g.c.last()}).reset_index()
    out = {}
    for coin, x in agg.groupby("coin"):
        out[coin] = dict(zip(x.hour.tolist(), zip(x.o.tolist(), x.h.tolist(), x.l.tolist(), x.c.tolist())))
    return out


def funding(d):
    paths = glob.glob(f"{d}/**/funding.parquet", recursive=True)
    if not paths:
        return {}
    f = pd.read_parquet(paths[0])
    return {c: dict(zip(x.hour.tolist(), x.rate.tolist())) for c, x in f.groupby("coin")}


def av(d):
    paths = glob.glob(f"{d}/**/av.parquet", recursive=True)
    if not paths:
        return {}
    a = pd.read_parquet(paths[0])
    a["t"] = pd.to_datetime(a.date).astype("int64") // 10**6 + 86_400_000 - 1
    return {addr: (x.sort_values("t").t.to_numpy(), x.sort_values("t").account_value.to_numpy())
            for addr, x in a.groupby("address")}


def beurzen(d):
    paths = glob.glob(f"{d}/**/beurzen.json", recursive=True)
    return json.load(open(paths[0])) if paths else {}


def fills(d, addrs):
    dset = ds.dataset(glob.glob(f"{d}/**/fills_*.parquet", recursive=True), format="parquet")
    t = dset.to_table(filter=ds.field("address").isin(pa.array(sorted(addrs))))
    f = t.to_pandas().drop_duplicates(["address", "ts", "tid", "coin", "px", "signed"])
    f = f.sort_values(["address", "ts", "tid"], kind="stable")
    f["after"] = f.start + f.signed
    return f


def as_list(f):
    return list(zip(f.ts.tolist(), f.coin.tolist(), f.start.tolist(), f.after.tolist(), f.px.tolist()))


def curve_stats(eq):
    eq = np.asarray(eq, float)
    if len(eq) < 2:
        return {}
    curve = np.concatenate([[1000.0], eq])
    r = np.diff(curve) / np.where(curve[:-1] > 0, curve[:-1], np.nan)
    r = r[~np.isnan(r)]
    sd = r.std(ddof=1) if len(r) > 2 else 0
    return {"rendement": float(eq[-1] / 1000 - 1), "gem": float(r.mean()) if len(r) else 0.0,
            "sharpe": float(r.mean() / sd * np.sqrt(365)) if sd > 0 else 0.0,
            "maxdd": float((curve / np.maximum.accumulate(curve) - 1).min())}
