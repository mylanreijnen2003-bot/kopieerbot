"""Robuustheidstest selectieregels op meerdere knipdatums (alleen bestaande ruwe portfolio-data, geen API).
Per knip: kiezen op data t/m knip, meten 45 dagen daarna, stop -25% per trader.
Let op: de pool is gekozen op activiteit in jul-aug/okt -> vroege knips zijn te rooskleurig (overlevers). Vergelijk regels onderling.
Gebruik: python -m kopietest.multiknip <ruw-map> <portfolio_alle.csv.gz> <uit>"""
import glob
import gzip
import json
import os
import sys

import numpy as np
import pandas as pd
import requests

from kopietest import hl_ruw_analyse as A

DAG = A.DAG
KNIPS = ["2026-05-01", "2026-06-01", "2026-07-01", "2026-08-01", "2026-08-19"]
H = 45
STOP = -0.25


def main():
    ruw, alle, uit = sys.argv[1:4]
    os.makedirs(uit, exist_ok=True)
    th = dict(pd.read_csv(alle)[["address", "trades_hist"]].values)
    reeksen = {}
    for f in glob.glob(f"{ruw}/*.jsonl.gz"):
        for line in gzip.open(f, "rt"):
            w = json.loads(line)
            df = A.reeks(w)
            if df is not None:
                reeksen[w["address"]] = df
    res, regels_md = {}, []
    for knip in KNIPS:
        T = int(pd.Timestamp(knip).value // 10**6)
        rows = []
        for a, df in reeksen.items():
            k = A.kenmerken(df, T)
            if not k:
                continue
            r, dd = A.test(df, T, T + H * DAG)
            if r is None:
                continue
            rows.append({"address": a, "trades_hist": th.get(a), **{f"k_{x}": v for x, v in k.items()}, "r": r, "dd": dd})
        t = pd.DataFrame(rows)
        g = A.geschikt(t, "k_")
        g = g.assign(rs=np.where(g.dd <= STOP, STOP, g.r))
        uitk = {"geschikt": len(g), "alle": round(100 * g.rs.mean(), 1), "alle_mediaan": round(100 * g.rs.median(), 1)}
        rng = np.random.default_rng(7)
        rnd = [g.rs.sample(20, random_state=int(s)).mean() for s in rng.integers(0, 10**6, 500)] if len(g) >= 20 else [np.nan]
        uitk["willekeurig20"] = round(100 * float(np.nanmean(rnd)), 1)
        uitk["willekeurig20_p90"] = round(100 * float(np.nanpercentile(rnd, 90)), 1)
        for regel in ("mylan", "risico", "max"):
            for n in (10, 20, 30):
                s = A.rang(g, "k_", regel).head(n)
                uitk[f"{regel}{n}"] = round(100 * s.rs.mean(), 1)
                uitk[f"{regel}{n}_winst"] = round(float((s.rs > 0).mean()), 2)
        top20 = A.rang(g, "k_", "mylan").head(20)
        uitk["mylan20_top5_90d"] = round(100 * top20.sort_values("k_rend_90", ascending=False).head(5).rs.mean(), 1)
        res[knip] = uitk
        print(knip, uitk, flush=True)
    json.dump(res, open(f"{uit}/uitslag.json", "w"), indent=1)
    kol = [("alle", "alle geschikt"), ("willekeurig20", "20 willekeurig"), ("mylan10", "regel Mylan 10"), ("mylan20", "regel Mylan 20"),
           ("mylan30", "regel Mylan 30"), ("mylan20_top5_90d", "top 5 (90 d) uit Mylan 20"), ("risico20", "risico 20"), ("max20", "hoogste rend. 20")]
    regels_md = ["# Selectieregels op 5 knipdatums (45 d na knip, stop -25%)", "", "| Groep | " + " | ".join(KNIPS) + " | gem |",
                 "|---|" + "---|" * (len(KNIPS) + 1)]
    for k_, naam in kol:
        vals = [res[x].get(k_) for x in KNIPS]
        regels_md.append(f"| {naam} | " + " | ".join(f"{v:+.1f}%" if v == v and v is not None else "–" for v in vals)
                         + f" | {np.nanmean([v for v in vals if v is not None]):+.1f}% |")
    regels_md += ["", "Let op: pool gekozen op activiteit jul-aug/okt -> vroege knips te rooskleurig (overlevers). Vergelijk regels onderling."]
    open(f"{uit}/rapport.md", "w").write("\n".join(regels_md) + "\n")
    print("\n".join(regels_md))
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        tekst = "Test selectieregels (5 knips, 45 d):\n" + "\n".join(
            f"{naam}: {np.nanmean([res[x].get(k_) for x in KNIPS if res[x].get(k_) is not None]):+.1f}%" for k_, naam in kol)
        try:
            requests.post(f"https://ntfy.sh/{topic}", data=tekst.encode(), headers={"Title": "Kopieerbot: test selectieregels"}, timeout=20)
        except Exception as e:  # noqa: BLE001
            print("ntfy faalt", e)


if __name__ == "__main__":
    main()
