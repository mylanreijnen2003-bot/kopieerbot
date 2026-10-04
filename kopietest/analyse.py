"""Kopietest-analyse: combineer historisch (hist) en live (live) tot kopie-rendement per trader per methode.
Gebruik: python -m kopietest.analyse <map met kopietest-hist en kopietest-live> <uit.csv>

Model (per verhandelde dollar van de trader, 'bps' = 0,01%):
  edge_bruto       = winst trader vóór fees per verhandelde $ (uit hist)
  kosten A (taker na d s)    = jouw taker-fee + gemeten slippage t.o.v. zijn prijs na d s (live, mediaan)
  kosten B (spiegel + taker) = voor zijn limiet-fills: P(vult) x maker-fee + (1-P) x (taker-fee + achterstand)
                               voor zijn markt-fills: als A
  kopie-edge = edge_bruto - kosten ; rendement per maand = kopie-edge x omloop (verhandeld / equity per 30 d)
"""
import json
import os
import sys

import numpy as np
import pandas as pd

D = sys.argv[1]
FEES = {"hl": (1.5, 4.5), "orderly": (3.0, 6.0)}
VERTR = [0, 1, 2, 5, 10, 30, 60]


def jl(p):
    if not os.path.exists(p):
        return pd.DataFrame()
    return pd.DataFrame([json.loads(x) for x in open(p) if x.strip()])


def main():
    h = pd.read_csv(f"{D}/kopietest-hist/traders.csv")
    sp = pd.read_csv(f"{D}/kopietest-hist/spiegel.csv.gz") if os.path.exists(f"{D}/kopietest-hist/spiegel.csv.gz") else pd.DataFrame()
    ev = jl(f"{D}/kopietest-live/fills_eval.jsonl")
    lsp = jl(f"{D}/kopietest-live/spiegel.jsonl")
    rows = []
    for _, r in h.iterrows():
        v = r.venue
        mk_fee, tk_fee = FEES[v]
        e = ev[(ev.address == r.address)] if len(ev) else ev
        ev_v = ev[ev.venue == v] if len(ev) else ev
        out = {"address": r.address, "venue": v, "groep": r.groep, "edge_bruto_bps": r.edge_bps_bruto, "edge_netto_bps": r.edge_bps_netto,
               "maker_pct": r.maker_pct_notional, "trades_30d": r.trades, "pnl_30d": r.pnl_30d_netto, "account": r.account_value,
               "live_fills": len(e)}
        omloop = r.notional_30d / r.account_value if r.account_value and r.account_value > 0 else np.nan
        out["omloop_x"] = round(omloop, 1)
        out["trader_pm_pct"] = round(r.pnl_30d_netto / r.account_value * 100, 1) if r.account_value else None
        for d in VERTR:
            col = f"slip_{d}s_bps"
            bron = e if len(e) >= 20 and col in e else ev_v
            if len(bron) and col in bron:
                # slippage hangt af van of de trader maker of taker was
                sm = bron[bron.maker == True][col].median() if (bron.maker == True).any() else np.nan  # noqa: E712
                st = bron[bron.maker == False][col].median() if (bron.maker == False).any() else np.nan  # noqa: E712
                mp = (r.maker_pct_notional or 0) / 100
                slip = np.nansum([mp * sm if not np.isnan(sm) else 0, (1 - mp) * st if not np.isnan(st) else 0])
                kost = tk_fee + slip
                out[f"A_{d}s_kosten_bps"] = round(kost, 2)
                out[f"A_{d}s_pm_pct"] = round((r.edge_bps_bruto - kost) / 1e4 * omloop * 100, 1) if omloop == omloop else None
        # B: spiegel op limiet-fills (hist, 1m-candles), anders taker na 2 s
        s = sp[sp.address == r.address] if len(sp) else sp
        if len(s) and "vult_5m" in s:
            s2 = s[s.kan_spiegelen == True]  # noqa: E712
            for h_, col in (("0m", "vult_0m_streng"), ("5m", "vult_5m_streng"), ("5m_ruim", "vult_5m")):
                p = s2[col].mean() if len(s2) else np.nan
                ach = s2[~s2[col].astype(bool)].achterstand_5m_bps.mean() if "achterstand_5m_bps" in s2 and (~s2[col].astype(bool)).any() else 0
                a2 = out.get("A_2s_kosten_bps", tk_fee)
                mp = (r.maker_pct_notional or 0) / 100
                kan = s.kan_spiegelen.mean()
                kost_mk = kan * (p * mk_fee + (1 - p) * (tk_fee + (ach if ach == ach else 0))) + (1 - kan) * a2
                kost = mp * kost_mk + (1 - mp) * a2
                out[f"B_{h_}_vulkans_pct"] = round(100 * p, 1) if p == p else None
                out[f"B_{h_}_achterstand_bps"] = round(ach, 1) if ach == ach else None
                out[f"B_{h_}_kosten_bps"] = round(kost, 2)
                out[f"B_{h_}_pm_pct"] = round((r.edge_bps_bruto - kost) / 1e4 * omloop * 100, 1) if omloop == omloop else None
        if len(lsp):
            l2 = lsp[(lsp.address == r.address)]
            if len(l2):
                out["live_spiegel_orders"] = len(l2)
                out["live_spiegel_door_pct"] = round(100 * l2.t_door.notna().mean(), 1) if "t_door" in l2 else None
                out["live_spiegel_raak_pct"] = round(100 * l2.t_raak.notna().mean(), 1) if "t_raak" in l2 else None
        for c in ("te_klein_2000_notional_pct", "te_klein_10000_notional_pct", "max_x_p90", "max_x_max", "liq_fills", "lead_med_s"):
            out[c] = r.get(c)
        rows.append(out)
    t = pd.DataFrame(rows)
    t.to_csv(sys.argv[2], index=False)
    pd.set_option("display.width", 300)
    pd.set_option("display.max_columns", 60)
    print(t.T.to_string())


if __name__ == "__main__":
    main()
