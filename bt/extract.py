"""Backtest stap A (parallel per deel van de dagbestanden): uit de Hugging Face-dataset halen
- uurkaarsen (open/hoog/laag/slot) per perp-munt uit alle taker-trades;
- alle perp-fills van de wallets in data/universe.parquet (HIP-3 en spot eruit).
Gebruik: python -m bt.extract <deel> <aantal> <uitmap>
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from huggingface_hub import HfApi, hf_hub_download

REPO = "craftify2221/hyperliquid-fills-raw"
COLS = ["address", "coin", "timestamp", "price", "size", "side", "start_position", "asset_class", "trade_id",
        "crossed", "is_liquidation"]
HOUR = 3_600_000


def files():
    return sorted(x.path for x in HfApi().list_repo_tree(REPO, repo_type="dataset", path_in_repo="fills")
                  if x.path.endswith(".parquet"))


def download(path, tmp):
    for attempt in range(6):
        try:
            return hf_hub_download(REPO, path, repo_type="dataset", local_dir=tmp)
        except Exception as exc:  # noqa: BLE001
            print("download fout", path, exc)
            time.sleep(20 * (attempt + 1))
    raise RuntimeError(path)


def process(local, wanted):
    pf = pq.ParquetFile(local)
    cols = [c for c in COLS if c in pf.schema_arrow.names]
    mk, ld = [], []
    f64 = pa.float64()
    for rg in range(pf.num_row_groups):
        t = pf.read_row_group(rg, columns=cols)
        t = t.filter(pc.not_equal(t["asset_class"], "spot"))
        t = t.filter(pc.invert(pc.match_substring(t["coin"], ":")))
        if t.num_rows == 0:
            continue
        t = t.set_column(t.schema.get_field_index("address"), "address", pc.utf8_lower(t["address"]))
        m = t.filter(pc.equal(t["crossed"], True))
        mk.append(pd.DataFrame({"coin": m["coin"].to_pandas(), "ts": pc.cast(m["timestamp"], pa.int64()).to_numpy(),
                                "price": pc.cast(m["price"], f64).to_numpy()}))
        s = t.filter(pc.is_in(t["address"], value_set=wanted))
        if s.num_rows:
            size = pc.cast(s["size"], f64)
            liq = pc.fill_null(s["is_liquidation"], False) if "is_liquidation" in s.column_names else pa.array([False] * s.num_rows)
            ld.append(pa.table({
                "address": s["address"], "coin": s["coin"], "ts": pc.cast(s["timestamp"], pa.int64()),
                "tid": pc.cast(s["trade_id"], pa.int64()) if "trade_id" in s.column_names else pa.array([0] * s.num_rows, pa.int64()),
                "px": pc.cast(s["price"], f64), "start": pc.cast(s["start_position"], f64),
                "signed": pc.if_else(pc.equal(s["side"], "buy"), size, pc.negate(size)),
                "maker": pc.invert(s["crossed"]), "liq": liq}).to_pandas())
    bars = None
    if mk:
        m = pd.concat(mk, ignore_index=True)
        m["hour"] = m.ts // HOUR * HOUR
        m = m.sort_values("ts", kind="stable")
        g = m.groupby(["coin", "hour"])
        bars = pd.DataFrame({"t_first": g.ts.first(), "o": g.price.first(), "h": g.price.max(), "l": g.price.min(),
                             "t_last": g.ts.last(), "c": g.price.last()}).reset_index()
    fills = pd.concat(ld, ignore_index=True) if ld else None
    return bars, fills


def main():
    shard, n, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    os.makedirs(out, exist_ok=True)
    wanted = pa.array(sorted(set(pd.read_parquet(os.environ.get("UNIVERSE", "data/universe.parquet")).address.str.lower())))
    mine = files()[shard::n]
    bars, fills = [], []
    for i, path in enumerate(mine):
        tmp = tempfile.mkdtemp()
        try:
            b, f = process(download(path, tmp), wanted)
            if b is not None:
                bars.append(b)
            if f is not None:
                fills.append(f)
            print(f"{i + 1}/{len(mine)} {path} fills={0 if f is None else len(f)}", flush=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(os.path.expanduser("~/.cache/huggingface"), ignore_errors=True)
    pd.concat(bars).to_parquet(f"{out}/bars_{shard}.parquet", index=False)
    pd.concat(fills).to_parquet(f"{out}/fills_{shard}.parquet", index=False)


if __name__ == "__main__":
    main()
