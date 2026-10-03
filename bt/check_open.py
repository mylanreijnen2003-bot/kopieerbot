"""Controle top 30 brede scan: echte accountwinst (incl. open posities) sinds 19-8 vs gesloten-trade-winst.
Gebruik: python -m bt.check_open <top30.csv> <uit.csv>
"""

import sys

import pandas as pd

from bot import hl

EIND = pd.Timestamp("2026-08-19").value // 10**6


def main():
    top = pd.read_csv(sys.argv[1])
    rows = []
    for a in top.address:
        ch = hl.info({"type": "clearinghouseState", "user": a})
        ms = ch.get("marginSummary", {})
        pos = [p["position"] for p in ch.get("assetPositions", [])]
        upnl = sum(float(p.get("unrealizedPnl", 0)) for p in pos)
        ntl = sum(abs(float(p.get("positionValue", 0))) for p in pos)
        port = dict(hl.info({"type": "portfolio", "user": a}))
        pe = port.get("perpAllTime", port.get("allTime", {}))
        pnl = pd.Series({int(t): float(v) for t, v in pe.get("pnlHistory", [])}).sort_index()
        av = pd.Series({int(t): float(v) for t, v in pe.get("accountValueHistory", [])}).sort_index()
        voor = pnl[pnl.index < EIND]
        av_voor = av[av.index < EIND]
        rows.append({"address": a, "accountwaarde_nu": float(ms.get("accountValue", 0)), "open_posities": len(pos),
                     "open_waarde": round(ntl, 0), "ongerealiseerd": round(upnl, 0),
                     "pnl_alltime": round(pnl.iloc[-1], 0) if len(pnl) else None,
                     "pnl_sinds_19aug": round(pnl.iloc[-1] - voor.iloc[-1], 0) if len(voor) else None,
                     "accountwaarde_18aug": round(av_voor.iloc[-1], 0) if len(av_voor) else None,
                     "vol_30d": float(dict(port).get("month", {}).get("vlm", 0) or 0)})
        print(rows[-1], flush=True)
    pd.DataFrame(rows).to_csv(sys.argv[2], index=False)


if __name__ == "__main__":
    main()
