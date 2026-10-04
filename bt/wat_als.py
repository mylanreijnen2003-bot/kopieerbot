"""Wat-als: één trader X dagen gevolgd met een vast bedrag (bot-basis, eerste fill).
Varianten: Kraken perps (0,32% kosten, long+short, stop -10% per trade) en Bitvavo spot (alleen longs, alleen munten op
Bitvavo, 0,60% kosten per rondje: 2x 0,25% taker + spread). Inzet per trade = pot / K. Per maand en totaal.
Gebruik: python -m bt.wat_als <adres> <dagen> <pot> <beurzenmap> <uitmap>
"""

import glob
import json
import os
import sys
import time

import numpy as np
import pandas as pd

from bot import hl
from bt.engine import to_base
from bt.p2_common import DAG, UUR, k90, trades_open


def main():
    adr, dagen, pot, vdir, out = sys.argv[1].lower(), int(sys.argv[2]), float(sys.argv[3]), sys.argv[4], sys.argv[5]
    os.makedirs(out, exist_ok=True)
    b = json.load(open(glob.glob(f"{vdir}/**/beurzen.json", recursive=True)[0]))
    kraken, bitvavo = set(b.get("kraken", [])), set(b.get("bitvavo", []))
    nu = int(time.time() * 1000)
    start = nu - dagen * DAG
    fl = [f for f in hl.fills(adr, start - 30 * DAG, nu) if f["kind"] == "perp"]
    dicht, open_ = trades_open(fl, start)
    df = pd.DataFrame(dicht)
    k = k90(df.open, df.sluit)
    stake = pot / k
    cache = {}

    def stop(t):
        if t.coin not in cache:
            cache[t.coin] = hl.candles(t.coin, start - DAG, nu, "1h")
        c = cache[t.coin]
        g = t.p1 * (0.9 if t.dir > 0 else 1.1)
        for h in range((int(t.open) // UUR + 1) * UUR, int(t.sluit), UUR):
            x = c.get(h)
            if x and ((t.dir > 0 and x[2] <= g) or (t.dir < 0 and x[1] >= g)):
                return True
        return False

    df["base"] = df.coin.map(to_base)
    df["kraken"] = df.base.isin(kraken)
    df["bitvavo"] = df.base.isin(bitvavo)
    df["r_hun"] = df.dir * (df.po / df.pv - 1)
    df["r_bot"] = df.dir * (df.po / df.p1 - 1)
    df["stop"] = [stop(t) if t.kraken else False for t in df.itertuples()]
    df["kr_r"] = np.where(df.kraken, np.where(df["stop"], -0.10, df.r_bot) - 0.0032, 0.0)
    df["bv_r"] = np.where(df.bitvavo & (df.dir > 0), df.r_bot - 0.0060, 0.0)
    df["maand"] = pd.to_datetime(df.sluit, unit="ms").dt.strftime("%Y-%m")
    df = df.sort_values("sluit")
    res = {"trades": len(df), "K": k, "inzet_per_trade": round(stake, 2),
           "long_pct": round(100 * (df.dir > 0).mean(), 1), "op_kraken_pct": round(100 * df.kraken.mean(), 1),
           "op_bitvavo_pct": round(100 * df.bitvavo.mean(), 1),
           "bitvavo_bruikbaar_pct": round(100 * (df.bitvavo & (df.dir > 0)).mean(), 1),
           "houdtijd_mediaan_uur": round(float(((df.sluit - df.open) / UUR).median()), 1),
           "trades_gestopt": int(df["stop"].sum()), "open_posities_nu": len(open_)}
    for naam, col in [("kraken", "kr_r"), ("bitvavo", "bv_r"), ("hun_prijs_zonder_kosten", "r_hun")]:
        eq = pot + (df[col] * stake).cumsum()
        dd = float((eq / np.maximum.accumulate(np.concatenate([[pot], eq.values]))[1:] - 1).min())
        res[f"{naam}_eind"] = round(float(eq.iloc[-1]), 2)
        res[f"{naam}_maxdaling_pct"] = round(100 * dd, 1)
    m = df.groupby("maand").agg(trades=("r_bot", "size"), kraken_eur=("kr_r", lambda s: round(float((s * stake).sum()), 2)),
                                bitvavo_eur=("bv_r", lambda s: round(float((s * stake).sum()), 2)))
    munten = df.groupby("base").agg(trades=("r_bot", "size"), long_pct=("dir", lambda s: round(100 * (s > 0).mean())),
                                    kraken=("kraken", "first"), bitvavo=("bitvavo", "first"),
                                    kraken_eur=("kr_r", lambda s: round(float((s * stake).sum()), 2))).sort_values("trades", ascending=False)
    json.dump(res, open(f"{out}/samenvatting.json", "w"), indent=1)
    m.to_csv(f"{out}/per_maand.csv")
    munten.to_csv(f"{out}/per_munt.csv")
    print(json.dumps(res, indent=1)); print(m.to_string()); print(munten.head(25).to_string())


if __name__ == "__main__":
    main()
