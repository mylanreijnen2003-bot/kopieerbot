"""Volledig profiel van de live-traders: datasetfills (28-7-2025 t/m 18-8-2026) + API-fills (19-8 t/m gisteren).
Per maand: trades, winst-%, gerealiseerde winst, houdtijd, munten. Plus eigen accountcurve (API) en kopieercurve.
Gebruik: python -m bt.profiel <datamap> <h4-sim-map> <live-map>
"""

from __future__ import annotations

import glob
import json
import sys
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

from bot import hl
from bt import data
from bt.engine import to_base

DATA_EIND = pd.Timestamp("2026-08-19").value // 10**6


def maand(t):
    return pd.Timestamp(t, unit="ms").strftime("%Y-%m")


def trade_lijst(fl):
    """fl: dicts met time, coin, start, after, px, fee. Geeft afgeronde trades (coin, open_t, sluit_t, winst_usd)."""
    acc, t_open, out = defaultdict(float), {}, []
    for f in fl:
        c, s, a, px = f["coin"], f["start"], f["after"], f["px"]
        if s == 0:
            t_open[c] = f["time"]
        if s != 0 and a * s < 0:                       # richtingwissel: eerst sluiten, dan nieuw openen
            out.append((c, t_open.get(c, f["time"]), f["time"], acc[c] + s * px - f.get("fee", 0.0)))
            acc[c], t_open[c] = -a * px, f["time"]
            continue
        acc[c] += -(a - s) * px - f.get("fee", 0.0)
        if s != 0 and a == 0:
            out.append((c, t_open.get(c, f["time"]), f["time"], acc.pop(c)))
            t_open.pop(c, None)
    return out


def main():
    d, sim_dir, live = sys.argv[1], sys.argv[2], sys.argv[3]
    sel = json.load(open(f"{live}/selection.json"))["wallets"]
    kraken = set(json.load(open(f"{live}/kraken.json")))
    addrs = [w["address"] for w in sel]
    ds_f = data.fills(d, set(addrs))
    curves = pd.concat([pd.read_parquet(p) for p in glob.glob(f"{sim_dir}/**/curves_*.parquet", recursive=True)])
    curves = curves[curves.address.isin(addrs)]
    eind = int(pd.Timestamp.now(tz="UTC").normalize().value // 10**6)
    rows, prof = [], {}
    for a in addrs:
        x = ds_f[ds_f.address == a]
        fl = [{"time": int(t), "coin": c, "start": s, "after": af, "px": p, "fee": 0.0, "kind": "perp", "signed": af - s}
              for t, c, s, af, p in zip(x.ts, x.coin, x.start, x.after, x.px) if t < DATA_EIND]
        api = [f for f in hl.fills(a, DATA_EIND, eind) if f["kind"] != "spot"]
        fl += api
        fl.sort(key=lambda f: f["time"])
        tr = trade_lijst(fl)
        df = pd.DataFrame(tr, columns=["coin", "open", "sluit", "winst"])
        df["maand"] = df.sluit.map(maand)
        df["uren"] = (df.sluit - df.open) / 3_600_000
        df["kraken"] = df.coin.map(lambda c: ":" not in c and to_base(c) in kraken)
        for m, g in df.groupby("maand"):
            rows.append({"address": a, "maand": m, "trades": len(g), "winst_pct": round(100 * (g.winst > 0).mean(), 1),
                         "winst_usd": round(g.winst.sum(), 0), "houdtijd_mediaan_uur": round(g.uren.median(), 1),
                         "kraken_pct_trades": round(100 * g.kraken.mean(), 1),
                         "munten": ", ".join(f"{c}" for c, _ in Counter(g.coin).most_common(3)),
                         "bron": "dataset" if m <= "2026-08" else "API"})
        # eigen account (API portfolio)
        port = dict(hl.info({"type": "portfolio", "user": a}))
        av = port.get("perpAllTime", port.get("allTime", {})).get("accountValueHistory", [])
        pnl = port.get("perpAllTime", port.get("allTime", {})).get("pnlHistory", [])
        pnl_s = pd.Series({int(t): float(v) for t, v in pnl}).sort_index()
        av_s = pd.Series({int(t): float(v) for t, v in av}).sort_index()
        # kopieercurve keuzeperiode (stop -20%, Kraken, 2x)
        c = curves[curves.address == a].sort_values("dag")
        eq = c.equity.to_numpy()
        days = pd.date_range("2025-07-28", periods=len(eq), freq="D")
        cm = pd.Series(eq, index=days).resample("ME").last()
        prof[a] = {
            "trades_totaal": len(df), "winst_pct_totaal": round(100 * (df.winst > 0).mean(), 1) if len(df) else None,
            "winst_usd_totaal": round(df.winst.sum(), 0), "houdtijd_mediaan_uur": round(df.uren.median(), 1) if len(df) else None,
            "trades_per_week": round(len(df) / max((fl[-1]["time"] - fl[0]["time"]) / (7 * hl.DAY), 1), 1) if fl else 0,
            "eerste_trade": maand(fl[0]["time"]) if fl else None, "laatste_trade": pd.Timestamp(fl[-1]["time"], unit="ms").strftime("%Y-%m-%d") if fl else None,
            "accountwaarde_nu": round(av_s.iloc[-1], 0) if len(av_s) else None,
            "pnl_alltime_usd": round(pnl_s.iloc[-1], 0) if len(pnl_s) else None,
            "pnl_sinds_19aug_usd": round(pnl_s.iloc[-1] - pnl_s[pnl_s.index < DATA_EIND].iloc[-1], 0) if (pnl_s.index < DATA_EIND).any() else None,
            "kopie_keuzeperiode_eind": round(float(eq[-1]), 1) if len(eq) else None,
            "kopie_keuzeperiode_gestopt_op": None,
            "kopie_per_maand_eind": {k.strftime("%Y-%m"): round(float(v), 1) for k, v in cm.items()},
        }
        print(a, prof[a], flush=True)
    pd.DataFrame(rows).to_csv(f"{live}/per_maand.csv", index=False)
    json.dump(prof, open(f"{live}/profiel.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
