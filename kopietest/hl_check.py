"""Controle van de kandidaten uit hl_select (vandaag): laatste 30 d fills -> trades, liquidaties, bijkopen, edge, drukte.
Gebruik: python -m kopietest.hl_check <uit> <uitslag.json>"""
import json
import os
import sys

OUT, UITSLAG = sys.argv[1], sys.argv[2]
os.makedirs(f"{OUT}/voorbeelden", exist_ok=True)
sys.argv = ["x", OUT]
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from kopietest import hist as H  # noqa: E402

u = json.load(open(UITSLAG))
adr = list(dict.fromkeys(u["vandaag_mylan"][:25] + u["vandaag_risico"][:25] + u["vandaag_max"][:15]))
rows = []
for a in adr:
    try:
        raw = H.hl._window(a, H.VAN, H.NU)
        fl, seen = [], set()
        for f in raw:
            k = (f.get("tid"), f.get("oid"), f.get("time"))
            if k in seen:
                continue
            seen.add(k)
            sz, sgn, st = float(f["sz"]), 1 if f["side"] == "B" else -1, float(f.get("startPosition") or 0)
            fl.append({"t": int(f["time"]), "coin": f["coin"], "sym": f["coin"], "side": sgn, "sz": sz, "px": float(f["px"]),
                       "maker": not f.get("crossed"), "oid": str(f.get("oid")), "start": st, "after": round(st + sgn * sz, 10),
                       "fee": float(f.get("fee") or 0), "liq": bool(f.get("liquidation"))})
        fl.sort(key=lambda x: (x["t"], x["oid"]))
        tr, open_ = H.trades_dollar(fl)
        stt = H.hl.info({"type": "clearinghouseState", "user": a}, weight=2) or {}
        pos = stt.get("assetPositions") or []
        av = float((stt.get("marginSummary") or {}).get("accountValue") or 0)
        ntl = float((stt.get("marginSummary") or {}).get("totalNtlPos") or 0)
        notional = sum(f["sz"] * f["px"] for f in fl)
        pnl = sum(t["pnl"] for t in tr)
        trn = sum(t["notional"] for t in tr)
        rows.append({"address": a, "fills_30d": len(fl), "fills_per_dag": round(len(fl) / 30, 1), "trades_30d": len(tr),
                     "liq_fills_30d": sum(f["liq"] for f in fl), "hip3_pct": round(100 * np.mean([":" in f["coin"] for f in fl]), 1) if fl else None,
                     "maker_pct": round(100 * np.mean([f["maker"] for f in fl]), 1) if fl else None,
                     "edge_bps": round((pnl + sum(f["fee"] for f in fl)) / trn * 1e4, 1) if trn else None,
                     "winst_pct": round(100 * np.mean([t["pnl"] > 0 for t in tr]), 1) if tr else None,
                     "bijkoop_max_x_p90": round(float(np.percentile([t["max_x"] for t in tr], 90)), 1) if tr else None,
                     "houdtijd_med_uur": round(float(np.median([(t["sluit"] - t["open"]) / 3.6e6 for t in tr])), 1) if tr else None,
                     "account_nu": round(av), "hefboom_nu": round(ntl / av, 1) if av > 0 else None, "posities_nu": len(pos),
                     "munten": ", ".join(pd.Series([f["coin"] for f in fl]).value_counts().head(4).index) if fl else "",
                     "laatste_fill_dagen": round((H.NU - fl[-1]["t"]) / 86_400_000, 1) if fl else None})
        H.log(rows[-1])
    except Exception as e:  # noqa: BLE001
        H.log("fout", a[:10], repr(e)[:200])
pd.DataFrame(rows).to_csv(f"{OUT}/check.csv", index=False)
