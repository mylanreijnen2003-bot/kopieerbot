"""Slippage-samenvatting over alle live-metingen (results/kopietest/live-*/s*/fills_eval.jsonl).
Per munt: mediaan spread en taker-slippage t.o.v. de prijs van de trader na 1/2/5 s. Gebruik: python -m kopietest.slip_samenvatting <results-map>"""
import glob
import json
import os
import sys

import pandas as pd
import requests

R = sys.argv[1]
rows = []
for p in glob.glob(f"{R}/kopietest/live-*/**/fills_eval.jsonl", recursive=True):
    rows += [json.loads(x) for x in open(p) if x.strip()]
if not rows:
    print("geen metingen")
    sys.exit(0)
d = pd.DataFrame(rows)
cols = [c for c in ("spread_bps_bij_fill", "slip_1s_bps", "slip_2s_bps", "slip_5s_bps") if c in d]
per = d.groupby("coin")[cols].median().round(2).assign(fills=d.groupby("coin").size()).sort_values("fills", ascending=False)
tot = d[cols].median().round(2)
os.makedirs(f"{R}/kopietest/slippage", exist_ok=True)
per.to_csv(f"{R}/kopietest/slippage/per_munt.csv")
md = ["# Slippage bij kopiëren (taker, t.o.v. prijs trader)", "", f"Fills gemeten: {len(d)}, munten: {d.coin.nunique()}",
      "", "Mediaan alle fills: " + ", ".join(f"{c} {v}" for c, v in tot.items()), "", per.head(40).to_markdown()]
open(f"{R}/kopietest/slippage/rapport.md", "w").write("\n".join(md) + "\n")
print("\n".join(md))
topic = os.environ.get("NTFY_TOPIC")
if topic and os.environ.get("BERICHT"):
    requests.post(f"https://ntfy.sh/{topic}", data=(f"Slippage-meting: {len(d)} fills, mediaan 2 s: {tot.get('slip_2s_bps')} bps, spread {tot.get('spread_bps_bij_fill')} bps").encode(),
                  headers={"Title": "Kopieerbot: slippage"}, timeout=20)
