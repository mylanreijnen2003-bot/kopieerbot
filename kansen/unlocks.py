"""V4: short vóór grote token-unlocks, met BTC-long als afdekking (zie kansen/VOORREGISTRATIE.md).

python -m kansen.unlocks <res> <cache>

Unlockdata: DefiLlama emissions-datasets (gratis bestanden). Koersen en funding: Kraken Futures (PF_<SYM>USD).
Event = dag waarop het ontgrendelde aantal met >= 10% stijgt t.o.v. het al ontgrendelde aantal (benadering circulerend),
token < 2 jaar oud (eerste ontgrendeling), perp op Kraken met koershistorie vanaf dag -30.
"""

from __future__ import annotations

import calendar
import json
import math
import statistics as st
import sys
import time
from pathlib import Path

import requests

DS = "https://defillama-datasets.llama.fi"
KF = "https://futures.kraken.com"
DAY = 86400
DREMPEL = 0.10
KOST = 0.001          # per kant per been
FUND_DEFAULT = 0.0001 / 8  # per uur, als funding ontbreekt (kost voor de short)
TEST_START = 1767225600   # 2026-01-01
TRAIN_START = 1672531200  # 2023-01-01


def get(url, tries=5):
    for i in range(tries):
        try:
            r = requests.get(url, timeout=90)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except (requests.RequestException, ValueError):
            pass
        time.sleep(3 * (i + 1))
    return None


def cached(cache: Path, name: str, url: str):
    p = cache / (name.replace("/", "_") + ".json")
    if p.exists():
        return json.loads(p.read_text())
    d = get(url)
    p.write_text(json.dumps(d))
    return d


def schedule(d) -> dict:
    """dag (unix, 00:00) -> totaal ontgrendeld (alle categorieën)."""
    tot = {}
    for cat in ((d or {}).get("documentedData") or {}).get("data") or []:
        for x in cat.get("data") or []:
            t = int(x["timestamp"]) // DAY * DAY
            tot[t] = tot.get(t, 0.0) + float(x.get("unlocked") or 0)
    return dict(sorted(tot.items()))


def events(tot: dict):
    days = list(tot)
    if len(days) < 2:
        return []
    first = next((t for t in days if tot[t] > 0), None)
    out = []
    for a, b in zip(days, days[1:]):
        if tot[a] <= 0:
            continue
        inc = tot[b] - tot[a]
        if inc / tot[a] >= DREMPEL:
            out.append({"day": b, "pct": inc / tot[a], "age_days": (b - first) / DAY if first is not None else None})
    return out


def candles(sym: str, a: int, b: int, cache: Path):
    d = cached(cache, f"kc_{sym}_{a}", f"{KF}/api/charts/v1/trade/{sym}/1d?from={a}&to={b}")
    return {int(c["time"]) // 1000 // DAY * DAY: float(c["close"]) for c in (d or {}).get("candles") or []}


def funding(sym: str, cache: Path):
    d = cached(cache, f"kf_{sym}", f"{KF}/derivatives/api/v4/historicalfundingrates?symbol={sym}")
    out = {}
    for r in (d or {}).get("rates") or []:
        t = calendar.timegm(time.strptime(r["timestamp"][:19], "%Y-%m-%dT%H:%M:%S"))
        out[t] = float(r.get("relativeFundingRate") or 0)
    return out


def fund_sum(f: dict, a: int, b: int):
    xs = [v for t, v in f.items() if a <= t < b]
    return sum(xs) if xs else None


def tstat(xs):
    if len(xs) < 3 or st.stdev(xs) == 0:
        return float("nan")
    return st.mean(xs) / (st.stdev(xs) / math.sqrt(len(xs)))


def main(res: str, cache: str):
    res, cache = Path(res), Path(cache)
    res.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    ins = (get(f"{KF}/derivatives/api/v3/instruments") or {}).get("instruments") or []
    kr = {i["symbol"][3:-3]: i["symbol"] for i in ins if str(i.get("symbol", "")).startswith("PF_") and i["symbol"].endswith("USD")}
    cg = get("https://api.coingecko.com/api/v3/coins/list") or []
    gsym = {c["id"]: c["symbol"].upper() for c in cg}
    protos = get(f"{DS}/emissionsProtocolsList") or []
    print(f"protocollen {len(protos)}, kraken-perps {len(kr)}, coingecko {len(gsym)}")
    now = int(time.time())
    btc = candles("PF_XBTUSD", TRAIN_START - 60 * DAY, now, cache)
    fbtc = funding("PF_XBTUSD", cache)
    rows, skipped = [], {"geen_symbool": 0, "niet_op_kraken": 0, "te_oud": 0, "geen_koers": 0, "toekomst": 0}
    for p in protos:
        d = cached(cache, f"em_{p}", f"{DS}/emissions/{p}")
        if not d:
            continue
        gid = d.get("gecko_id")
        sym = gsym.get(gid) if gid else None
        if not sym:
            skipped["geen_symbool"] += len(events(schedule(d)))
            continue
        ks = kr.get(sym) or kr.get("1000" + sym)
        for e in events(schedule(d)):
            t = e["day"]
            if t + DAY > now:
                skipped["toekomst"] += 1
                continue
            if t < TRAIN_START:
                continue
            if not ks:
                skipped["niet_op_kraken"] += 1
                continue
            if e["age_days"] is None or e["age_days"] > 730:
                skipped["te_oud"] += 1
                continue
            px = candles(ks, t - 40 * DAY, t + 3 * DAY, cache)
            a, b = t - 30 * DAY, t + DAY
            if a not in px or b not in px or a not in btc or b not in btc:
                skipped["geen_koers"] += 1
                continue
            r_short = -(px[b] / px[a] - 1)
            r_btc = btc[b] / btc[a] - 1
            fs = fund_sum(funding(ks, cache), a, b)
            fb = fund_sum(fbtc, a, b)
            f_short = fs if fs is not None else -FUND_DEFAULT * 24 * 31
            f_long = -(fb if fb is not None else FUND_DEFAULT * 24 * 31)
            net = r_short + r_btc + f_short + f_long - 4 * KOST
            rows.append({"proto": p, "sym": sym, "day": time.strftime("%Y-%m-%d", time.gmtime(t)), "unlock_pct": round(100 * e["pct"], 1),
                         "short_pct": round(100 * r_short, 2), "btc_pct": round(100 * r_btc, 2),
                         "funding_pct": round(100 * (f_short + f_long), 2), "netto_pct": round(100 * net, 2),
                         "test": t >= TEST_START})
    tr = [r["netto_pct"] for r in rows if not r["test"]]
    te = [r["netto_pct"] for r in rows if r["test"]]
    L = ["# V4 Short vóór grote unlocks — uitslag", "",
         f"Events gevonden: {len(rows)} (train {len(tr)}, test {len(te)}). Overgeslagen: {json.dumps(skipped)}", "",
         "| Periode | n | Gem. netto | Mediaan | Winstgevend | t |", "|---|---|---|---|---|---|"]
    for naam, xs in (("Train 2023–2025", tr), ("Test 2026", te)):
        if xs:
            L.append(f"| {naam} | {len(xs)} | {st.mean(xs):+.2f}% | {st.median(xs):+.2f}% | "
                     f"{100 * sum(x > 0 for x in xs) / len(xs):.0f}% | {tstat(xs):.2f} |")
    go = len(te) >= 1 and st.mean(te) > 1 and tstat(te) >= 2 and sum(x > 0 for x in te) / len(te) >= 0.55
    L += ["", f"- **Oordeel: {'GO' if go else 'NO-GO'}**" + (" (minder dan 20 test-events: alleen indicatief)" if len(te) < 20 else ""),
          "", "| Token | Dag | Unlock | Short | BTC | Funding | Netto | Test |", "|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: r["day"]):
        L.append(f"| {r['sym']} | {r['day']} | {r['unlock_pct']}% | {r['short_pct']:+.1f}% | {r['btc_pct']:+.1f}% | "
                 f"{r['funding_pct']:+.2f}% | {r['netto_pct']:+.1f}% | {'ja' if r['test'] else ''} |")
    # komende events (komende 45 dagen) die aan de regel voldoen
    L += ["", "## Komende unlocks (≥ 10%, < 2 jaar, op Kraken)", ""]
    for p in protos:
        d = json.loads((cache / f"em_{p}.json").read_text()) if (cache / f"em_{p}.json").exists() else None
        if not d:
            continue
        sym = gsym.get(d.get("gecko_id") or "")
        ks = sym and (kr.get(sym) or kr.get("1000" + sym))
        for e in events(schedule(d)):
            if ks and now < e["day"] < now + 45 * DAY and (e["age_days"] or 9e9) <= 730:
                L.append(f"- {sym} ({ks}): {time.strftime('%Y-%m-%d', time.gmtime(e['day']))}, +{100 * e['pct']:.0f}%")
    (res / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (res / "events.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"klaar: {len(rows)} events")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
