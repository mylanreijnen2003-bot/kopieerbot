"""V6: activistische 13D-meldingen (zie kansen/VOORREGISTRATIE.md). Lokaal draaien (SEC + Yahoo werken vanaf thuis).

PowerShell:
  $env:SEC_USER_AGENT = "Voornaam Achternaam email@adres.nl"
  py -m pip install yfinance pandas requests
  py -m kansen.activist13d uit\\13d

SEC eist een User-Agent met naam + e-mail. Max 10 verzoeken per seconde; wij doen er ~4.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics as st
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ACTIVISTEN = [
    "ELLIOTT", "STARBOARD", "ICAHN", "TRIAN", "VALUEACT", "THIRD POINT", "JANA PARTNERS", "PERSHING SQUARE",
    "ENGAGED CAPITAL", "LAND & BUILDINGS", "ANCORA", "LEGION PARTNERS", "SACHEM HEAD", "CORVEX", "BLACKWELLS",
    "BARINGTON", "POLITAN", "IRENIC", "MANTLE RIDGE", "CEVIAN", "ENGINE CAPITAL", "IMPACTIVE", "INCLUSIVE CAPITAL",
    "FRONTFOUR", "MARCATO", "SARISSA", "BLUE HARBOUR", "DRIVER MANAGEMENT",
    "CAAS CAPITAL", "AMBINA", "PALOGIC", "STARBOARD VALUE", "BRADLEY RADOFF", "JCP INVESTMENT", "MILLSTREET",
    "STADIUM CAPITAL", "SCOPIA", "LONE STAR VALUE", "SIMCOE", "OUTERBRIDGE",
]
FORMS = {"SC 13D", "SCHEDULE 13D"}
TRAIN = (2015, 2021)
HOLD = (20, 60, 120)
KOST = 0.002


def ua():
    u = os.environ.get("SEC_USER_AGENT", "")
    if "@" not in u:
        sys.exit("Zet eerst $env:SEC_USER_AGENT = \"Naam email@adres\"")
    return {"User-Agent": u}


def sec(url, cache: Path, binary=False):
    p = cache / re.sub(r"[^A-Za-z0-9._-]", "_", url.split("://")[1])
    if p.exists():
        return p.read_bytes() if binary else p.read_text(encoding="utf-8", errors="ignore")
    for i in range(6):
        time.sleep(0.25)
        r = requests.get(url, headers=ua(), timeout=60)
        if r.status_code == 200:
            p.write_bytes(r.content)
            return r.content if binary else r.text
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"SEC faalt: {url} {r.status_code}")


def is_act(name: str) -> bool:
    n = name.upper()
    return any(a in n for a in ACTIVISTEN)


def index_rows(year: int, q: int, cache: Path):
    txt = sec(f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{q}/form.idx", cache)
    out = []
    for line in txt.splitlines():
        for f in FORMS:
            if line.startswith(f + " ") and not line.startswith(f + "/A"):
                rest = line[len(f):].strip()
                m = re.match(r"(.+?)\s{2,}(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(\S+)$", rest)
                if m:
                    out.append({"form": f, "name": m.group(1).strip(), "cik": int(m.group(2)), "date": m.group(3), "file": m.group(4)})
    return out


def subject_from_header(file: str, cache: Path):
    txt = sec(f"https://www.sec.gov/Archives/{file}", cache)[:6000]
    m = re.search(r"SUBJECT COMPANY:.*?CENTRAL INDEX KEY:\s*(\d+)", txt, re.S)
    f = re.search(r"FILED BY:.*?COMPANY CONFORMED NAME:\s*(.+)", txt, re.S)
    return (int(m.group(1)) if m else None), (f.group(1).strip() if f else "")


def tstat(xs):
    return st.mean(xs) / (st.stdev(xs) / math.sqrt(len(xs))) if len(xs) > 2 and st.stdev(xs) > 0 else float("nan")


def main(out: str):
    import yfinance as yf
    out = Path(out)
    cache = out / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    tick = json.loads(sec("https://www.sec.gov/files/company_tickers.json", cache))
    cik2t = {int(v["cik_str"]): v["ticker"] for v in tick.values()}
    now = pd.Timestamp.now()
    rows = []
    for y in range(TRAIN[0], now.year + 1):
        for q in range(1, 5):
            if pd.Timestamp(year=y, month=3 * q - 2, day=1) > now:
                continue
            rows += index_rows(y, q, cache)
        print(f"{y}: {len(rows)} 13D-regels", flush=True)
    df = pd.DataFrame(rows)
    ev = []
    for file, g in df.groupby("file"):
        acts = g[g.name.map(is_act)]
        if acts.empty:
            continue
        subj = g[~g.name.map(is_act)]
        if len(subj) == 1:
            scik, filer = int(subj.cik.iloc[0]), acts.name.iloc[0]
        else:
            scik, filer = subject_from_header(file, cache)
        if scik:
            ev.append({"date": g.date.iloc[0], "subject": scik, "filer": re.sub(r"[^A-Z ]", "", filer.upper())[:25], "file": file})
    ev = pd.DataFrame(ev).sort_values("date").drop_duplicates(["subject", "filer"])
    ev["ticker"] = ev.subject.map(cik2t)
    print(f"events: {len(ev)}, met ticker: {ev.ticker.notna().sum()}", flush=True)
    bench = {b: yf.download(b, start="2014-06-01", auto_adjust=True, progress=False) for b in ("SPY", "IWM")}
    res = []
    for e in ev[ev.ticker.notna()].itertuples():
        t = e.ticker.replace(".", "-")
        try:
            px = yf.download(t, start="2014-06-01", auto_adjust=True, progress=False)
        except Exception:
            continue
        if px is None or px.empty:
            continue
        if isinstance(px.columns, pd.MultiIndex):
            px.columns = px.columns.get_level_values(0)
        d0 = pd.Timestamp(e.date)
        after = px.index[px.index > d0]
        if not len(after):
            continue
        i = px.index.get_loc(after[0])
        pre = px.iloc[max(0, i - 20):i]
        if len(pre) < 10 or (pre.Close * pre.Volume).mean() < 2e6 or pre.Close.iloc[-1] < 3:
            continue
        r = {"date": e.date, "ticker": e.ticker, "filer": e.filer, "test": int(e.date[:4]) > TRAIN[1]}
        for h in HOLD:
            if i + h - 1 >= len(px):
                continue
            a, b = px.index[i], px.index[i + h - 1]
            rs = px.Close.iloc[i + h - 1] / px.Open.iloc[i] - 1
            for bn, bp in bench.items():
                if isinstance(bp.columns, pd.MultiIndex):
                    bp.columns = bp.columns.get_level_values(0)
                if a in bp.index and b in bp.index:
                    rb = bp.Close.loc[b] / bp.Open.loc[a] - 1
                    r[f"ab{h}_{bn}"] = float(rs - rb - KOST)
        res.append(r)
    R = pd.DataFrame(res)
    R.to_csv(out / "events.csv", index=False)
    L = ["# V6 Activistische 13D-meldingen — uitslag", "",
         f"13D-events van activisten: {len(ev)}; met huidige ticker: {int(ev.ticker.notna().sum())}; na filters met koers: {len(R)}.",
         "Let op: alleen nog genoteerde bedrijven hebben een ticker (overnames/faillissementen ontbreken).", "",
         "| Periode | Horizon | n | Gem. vs SPY | t | Winstgevend | Gem. vs IWM |", "|---|---|---|---|---|---|---|"]
    for naam, m in (("Train 2015–2021", ~R.test), ("Test 2022–nu", R.test)):
        for h in HOLD:
            c = f"ab{h}_SPY"
            xs = R.loc[m, c].dropna().tolist() if c in R else []
            xi = R.loc[m, f"ab{h}_IWM"].dropna().tolist() if f"ab{h}_IWM" in R else []
            if xs:
                L.append(f"| {naam} | {h} d | {len(xs)} | {100 * st.mean(xs):+.2f}% | {tstat(xs):.2f} | "
                         f"{100 * sum(x > 0 for x in xs) / len(xs):.0f}% | {100 * st.mean(xi):+.2f}% |")
    te = R.loc[R.test, "ab60_SPY"].dropna().tolist() if "ab60_SPY" in R else []
    go = bool(te) and st.mean(te) > 0.01 and tstat(te) >= 2
    L += ["", f"- **Oordeel: {'GO' if go else 'NO-GO'}** (eis: test 60 d gem. > 1% netto vs SPY en t ≥ 2)"]
    (out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main(sys.argv[1])
