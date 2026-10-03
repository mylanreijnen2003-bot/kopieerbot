"""Brede scan stap 1 (per deel van de dagbestanden): per wallet per dag tellingen over ALLE wallets.
Gebruik: python -m bt.scan <deel> <aantal> <uitmap>
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

from bt.extract import download, files

COLS = ["address", "coin", "timestamp", "size", "side", "start_position", "asset_class", "crossed"]


def process(local):
    pf = pq.ParquetFile(local)
    cols = [c for c in COLS if c in pf.schema_arrow.names]
    parts = []
    for rg in range(pf.num_row_groups):
        t = pf.read_row_group(rg, columns=cols)
        t = t.filter(pc.not_equal(t["asset_class"], "spot"))
        t = t.filter(pc.invert(pc.match_substring(t["coin"], ":")))
        if t.num_rows == 0:
            continue
        d = pd.DataFrame({"address": pc.utf8_lower(t["address"]).to_pandas(),
                          "ts": pc.cast(t["timestamp"], "int64").to_numpy(),
                          "start": pc.cast(t["start_position"], "float64").to_numpy(),
                          "size": pc.cast(t["size"], "float64").to_numpy(),
                          "buy": pc.equal(t["side"], "buy").to_numpy(zero_copy_only=False),
                          "maker": pc.invert(t["crossed"]).to_numpy(zero_copy_only=False)})
        after = d.start + d["size"].where(d.buy, -d["size"])
        d["close"] = ((after == 0) & (d.start != 0)) | (d.start * after < 0)
        d["day"] = d.ts // 86_400_000
        parts.append(d.groupby(["address", "day"]).agg(fills=("ts", "size"), trades=("close", "sum"),
                                                      maker=("maker", "sum")).reset_index())
    if not parts:
        return None
    return pd.concat(parts).groupby(["address", "day"], as_index=False).sum()


def main():
    shard, n, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    os.makedirs(out, exist_ok=True)
    res = []
    for i, path in enumerate(files()[shard::n]):
        tmp = tempfile.mkdtemp()
        try:
            r = process(download(path, tmp))
            if r is not None:
                res.append(r)
            print(f"{i + 1} {path}", flush=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(os.path.expanduser("~/.cache/huggingface"), ignore_errors=True)
    pd.concat(res).to_parquet(f"{out}/scan_{shard}.parquet", index=False)


if __name__ == "__main__":
    main()
