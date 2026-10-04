"""Op welke uren (NL-tijd) opent en sluit een trader posities? Laatste X dagen, ook apart voor Bitvavo-kopieerbaar.
Gebruik: python -m bt.uren <adres> <dagen> <bitvavo.json> <uitmap>
"""

import json
import os
import sys
import time

import pandas as pd

from bot import hl
from bt.engine import to_base
from bt.p2_common import DAG, trades_open


def main():
    adr, dagen, bvpad, out = sys.argv[1].lower(), int(sys.argv[2]), sys.argv[3], sys.argv[4]
    os.makedirs(out, exist_ok=True)
    bitvavo = set(json.load(open(bvpad)))
    nu = int(time.time() * 1000)
    fl = [f for f in hl.fills(adr, nu - (dagen + 30) * DAG, nu) if f["kind"] == "perp"]
    dicht, open_ = trades_open(fl, nu - dagen * DAG)
    rows = [{"soort": "open", "t": t["open"], "bv": t["dir"] > 0 and to_base(t["coin"]) in bitvavo} for t in dicht]
    rows += [{"soort": "sluit", "t": t["sluit"], "bv": t["dir"] > 0 and to_base(t["coin"]) in bitvavo} for t in dicht]
    rows += [{"soort": "open", "t": t["open"], "bv": t["dir"] > 0 and to_base(c) in bitvavo} for c, t in open_.items()]
    df = pd.DataFrame(rows)
    df["nl"] = pd.to_datetime(df.t, unit="ms", utc=True).dt.tz_convert("Europe/Amsterdam")
    df["uur"], df["dag"] = df.nl.dt.hour, df.nl.dt.day_name()
    tab = df.pivot_table(index="uur", columns="soort", values="t", aggfunc="count", fill_value=0)
    tab["bitvavo_open"] = df[(df.soort == "open") & df.bv].groupby("uur").size()
    tab["bitvavo_sluit"] = df[(df.soort == "sluit") & df.bv].groupby("uur").size()
    tab = tab.reindex(range(24), fill_value=0).fillna(0).astype(int)
    tab.to_csv(f"{out}/uren.csv")
    nacht = df[df.bv & df.uur.between(0, 6)]
    print(tab.to_string())
    print("Bitvavo-acties totaal", int(df.bv.sum()), "waarvan 00-07u NL", len(nacht),
          f"({100 * len(nacht) / max(1, int(df.bv.sum())):.0f}%)")


if __name__ == "__main__":
    main()
