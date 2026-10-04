"""Stap 7 + DCA-check voor de gekozen wallets: open posities nu, en per trade hoeveel er is bijgekocht.
Gebruik: python -m orderly.check5 <uit> <adres> [<adres> ...]"""
import json
import sys

OUT, ADR = sys.argv[1], sys.argv[2:]
sys.argv = ["x", "check", OUT]
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from orderly import probe as P  # noqa: E402
from orderly import select as S  # noqa: E402

rows, pos = [], []
for a in ADR:
    acc = P.rows_cursor(P.q("accounts", address=a))[0]
    staat = S.signed_state(P.q("accountState", address=a).get("data") or {})
    for k, v in staat.items():
        for s, p in v["pos"].items():
            pos.append({"address": a, "broker": v["broker"], "symbol": s, "munt": P.munt(s), "richting": "long" if p["q"] > 0 else "short",
                        "notional": p.get("notional"), "upnl": p.get("unrealized_pnl"), "lev": p.get("leverage"),
                        "gem_prijs": p.get("average_open_price"), "mark": p.get("mark_price"), "account_value": v["av"]})
    per = {}
    if len(acc) == 1:
        per[acc[0]["account_id"]] = S.historie({"address": a})[0]
    else:
        for x in acc:
            per[x["account_id"]] = S.historie({"address": a, "account_id": x["account_id"], "broker_id": x["broker_id"]})[0]
    for acc_id, tr in per.items():
        fl, _ = S.naar_fills(tr, acc_id, staat.get(acc_id, {}).get("pos", {}))
        open_ = {}
        for f in fl:
            c, s, af = f["coin"], f["start"], f["after"]
            if s == 0 and af != 0:
                open_[c] = {"t": f["time"], "eerste": abs(af), "max": abs(af), "fills": 1, "bij": 0}
                continue
            if c not in open_:
                continue
            o = open_[c]
            o["fills"] += 1
            if abs(af) > abs(s) and af * s > 0:
                o["bij"] += 1
            o["max"] = max(o["max"], abs(af))
            if af == 0 or af * s < 0:
                rows.append({"address": a, "coin": c, "open": o["t"], "sluit": f["time"], "fills": o["fills"], "bijkopen": o["bij"],
                             "max_x_eerste": round(o["max"] / o["eerste"], 2)})
                open_.pop(c)
                if af * s < 0:
                    open_[c] = {"t": f["time"], "eerste": abs(af), "max": abs(af), "fills": 1, "bij": 0}
    P.log("klaar", a[:10])
d = pd.DataFrame(rows)
d.to_csv(f"{OUT}/dca_trades.csv", index=False)
pd.DataFrame(pos).to_csv(f"{OUT}/open_posities.csv", index=False)
sam = d.groupby("address").agg(trades=("fills", "size"), fills_mediaan=("fills", "median"), bijkopen_mediaan=("bijkopen", "median"),
                                pct_met_bijkopen=("bijkopen", lambda x: round(100 * (x > 0).mean(), 1)),
                                max_x_mediaan=("max_x_eerste", "median"), max_x_p90=("max_x_eerste", lambda x: float(np.percentile(x, 90))),
                                max_x_max=("max_x_eerste", "max"))
sam.to_csv(f"{OUT}/dca_samenvatting.csv")
P.log(sam.to_string())
