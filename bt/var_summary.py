"""Varianten: samenvatting per groep (gem. per €100 voor top 5 en top 10). Gebruik: python -m bt.var_summary <map>"""
import glob
import sys

import pandas as pd

d = sys.argv[1]
t = pd.concat([pd.read_csv(p) for p in glob.glob(f"{d}/*_top15.csv")])
rows = []
for g, x in t.groupby("groep", sort=False):
    for n in (5, 10):
        h = x.head(n)
        rows.append({"groep": g, "top": n, **{c: round(h[c].mean(), 1) for c in
                     ["test_bot", "test_bot_zonder_kosten", "test_hun_prijs", "test_hand_1u"]},
                     "gestopt": int(((h.test_trades == 0) | (h.accountwaarde_nu < 100)).sum())})
s = pd.DataFrame(rows)
s.to_csv(f"{d}/samenvatting.csv", index=False)
print(s.to_string(index=False))
