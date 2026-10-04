"""Gemiddelde equity per trader over de laatste 30 d (HL: portfolio; Orderly: terugrekenen uit fills + stortingen).
Gebruik: python -m kopietest.eq <uit> <fills.csv.gz>"""
import json
import os
import sys

OUT, FILLS = sys.argv[1], sys.argv[2]
os.makedirs(f"{OUT}/voorbeelden", exist_ok=True)
sys.argv = ["x", OUT]
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from kopietest import hist as H  # noqa: E402

tr = pd.read_csv(os.path.join(os.path.dirname(__file__), "traders.csv"))
fl = pd.read_csv(FILLS)
rows = []
for a, v in zip(tr.address, tr.venue):
    try:
        if v == "hl":
            j = H.hl.info({"type": "portfolio", "user": a}, weight=20)
            per = dict(j)
            m = per.get("perpMonth") or per.get("month") or {}
            av = [(int(t), float(x)) for t, x in m.get("accountValueHistory", [])]
            pnl = [(int(t), float(x)) for t, x in m.get("pnlHistory", [])]
            e = np.mean([x for _, x in av]) if av else None
            p = pnl[-1][1] - pnl[0][1] if len(pnl) > 1 else None
            rows.append({"address": a, "venue": v, "equity_gem": e, "equity_min": min(x for _, x in av) if av else None,
                         "equity_max": max(x for _, x in av) if av else None, "pnl_maand_portfolio": p,
                         "dagen": (av[-1][0] - av[0][0]) / 864e5 if len(av) > 1 else None})
        else:
            x = fl[fl.address == a].sort_values("t")
            f2 = [{"t": int(r.t), "coin": r.sym, "px": r.px, "start": r.start, "after": r.after, "fee": r.fee} for r in x.itertuples()]
            st = H.S.signed_state(H.P.q("accountState", address=a).get("data") or {})
            e_nu = sum(s["av"] for s in st.values())
            dw, _ = H.P.alle("userDepositsWithdrawals", max_pag=5, address=a)
            pts = H.equity_terug(f2, e_nu, dw)
            ts = np.linspace(H.VAN, H.NU, 200)
            ev = [H.av_at(pts, t) for t in ts]
            rows.append({"address": a, "venue": v, "equity_gem": float(np.mean(ev)), "equity_min": float(min(ev)), "equity_max": float(max(ev)),
                         "stortingen_30d": sum(float(d.get("amount") or 0) * (1 if str(d.get("side")).lower() == "deposit" else -1)
                                               for d in dw if int(d.get("created_time") or 0) >= H.VAN), "dagen": 30})
    except Exception as e:  # noqa: BLE001
        rows.append({"address": a, "venue": v, "fout": repr(e)[:200]})
    H.log(rows[-1])
pd.DataFrame(rows).to_csv(f"{OUT}/equity.csv", index=False)
