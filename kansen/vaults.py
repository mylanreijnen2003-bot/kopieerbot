"""V1: Hyperliquid-vaults persistentie (zie kansen/VOORREGISTRATIE.md).

python -m kansen.vaults fetch <shard> <n_shards> <outdir>   # ruwe data per shard (jsonl.gz)
python -m kansen.vaults analyse <datadir> <resdir> <privdir> # uitslag (publiek) + volledige lijst (privé)
"""

from __future__ import annotations

import gzip
import json
import math
import statistics as st
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from bot.hl import info

DAY = 86_400_000
LIST_URL = "https://stats-data.hyperliquid.xyz/Mainnet/vaults"
FORM = ["2024-10-01", "2025-01-01", "2025-04-01", "2025-07-01", "2025-10-01", "2026-01-01", "2026-04-01",
        "2026-07-01", "2026-10-01"]  # laatste = einde laatste venster
MIN_AGE = 182 * DAY
MIN_TVL = 50_000
TOPN = 10
SHARE = 0.9


def ms(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def short(a: str) -> str:
    return a[:6] + "…" + a[-4:] if a else ""


# ---------- ophalen ----------

def vault_list() -> list[dict]:
    for i in range(5):
        try:
            r = requests.get(LIST_URL, timeout=120)
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(10 * (i + 1))
    raise RuntimeError("vaultlijst niet op te halen")


def summ(v: dict) -> dict:
    return v.get("summary") or v


def fetch(shard: int, nsh: int, out: str):
    vs = vault_list()
    vs = sorted(vs, key=lambda v: summ(v).get("vaultAddress") or "")
    mine = [v for i, v in enumerate(vs) if i % nsh == shard]
    Path(out).mkdir(parents=True, exist_ok=True)
    n_ok = 0
    t0 = time.time()
    with gzip.open(Path(out) / f"vaults-{shard}.jsonl.gz", "wt") as f:
        for v in mine:
            s = summ(v)
            a = s.get("vaultAddress")
            if not a:
                continue
            try:
                d = info({"type": "vaultDetails", "vaultAddress": a}, weight=20)
            except Exception:
                d = None
            port = {}
            if isinstance(d, dict):
                for per, p in d.get("portfolio") or []:
                    if per in ("allTime", "month"):
                        port[per] = {"av": p.get("accountValueHistory") or [], "pnl": p.get("pnlHistory") or []}
            rec = {"addr": a, "name": s.get("name"), "closed": bool(s.get("isClosed") or (d or {}).get("isClosed")),
                   "rel": (s.get("relationship") or {}).get("type"), "create": s.get("createTimeMillis"),
                   "tvl": s.get("tvl"), "commission": (d or {}).get("leaderCommission"),
                   "allow": (d or {}).get("allowDeposits"), "port": port, "ok": isinstance(d, dict)}
            n_ok += rec["ok"]
            f.write(json.dumps(rec) + "\n")
    print(f"shard {shard}: {len(mine)} vaults, {n_ok} met details, {time.time()-t0:.0f}s")


# ---------- analyse ----------

class Series:
    def __init__(self, rec: dict):
        p = rec["port"].get("allTime") or {}
        av = {int(t): float(x) for t, x in p.get("av", [])}
        pn = {int(t): float(x) for t, x in p.get("pnl", [])}
        m = rec["port"].get("month") or {}
        for t, x in m.get("av", []):  # fijnere data laatste maand toevoegen
            av.setdefault(int(t), float(x))
        for t, x in m.get("pnl", []):
            pn.setdefault(int(t), float(x))
        ts = sorted(set(av) & set(pn))
        self.t, self.av, self.idx = [], [], []
        idx = 1.0
        for i, t in enumerate(ts):
            if i > 0:
                prev = self.av[-1]
                r = (pn[t] - pn[ts[i - 1]]) / prev if prev > 1.0 else 0.0
                r = max(-1.0, min(r, 5.0))
                idx *= 1 + r
            self.t.append(t)
            self.av.append(av[t])
            self.idx.append(idx)
        self.closed = rec["closed"]
        self.first = self.t[0] if self.t else None
        self.last = self.t[-1] if self.t else None
        self.create = int(rec["create"]) if rec.get("create") else self.first

    def _pos(self, t):
        # laatste punt <= t
        lo, hi = 0, len(self.t) - 1
        if not self.t or t < self.t[0]:
            return None
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.t[mid] <= t:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def idx_at(self, t):
        i = self._pos(t)
        return None if i is None else self.idx[i]

    def av_at(self, t):
        i = self._pos(t)
        if i is None:
            return None
        if self.closed and t > self.last + 7 * DAY:
            return 0.0
        return self.av[i]

    def ret(self, a, b):
        x, y = self.idx_at(a), self.idx_at(b)
        if x is None or y is None or x <= 0:
            return None
        return y / x - 1

    def maxdd(self, a, b):
        base = self.idx_at(a)
        if base is None:
            return None
        peak, dd = base, 0.0
        for t, v in zip(self.t, self.idx):
            if a < t <= b:
                peak = max(peak, v)
                dd = min(dd, v / peak - 1)
        return dd


def net(r):
    return r * SHARE if r > 0 else r


def tstat(xs):
    if len(xs) < 2:
        return float("nan")
    sd = st.stdev(xs)
    return st.mean(xs) / (sd / math.sqrt(len(xs))) if sd > 0 else float("nan")


def evaluate(series: dict, t: int):
    pool, elig = [], []
    for a, s in series.items():
        if s.create is None or s.create > t - MIN_AGE:
            continue
        av = s.av_at(t)
        if av is None or av < MIN_TVL:
            continue
        r6 = s.ret(t - MIN_AGE, t)
        if r6 is None:
            continue
        pool.append(a)
        months = [s.ret(t - (k + 1) * 30 * DAY, t - k * 30 * DAY) for k in range(6)]
        if any(m is None for m in months):
            continue
        dd = s.maxdd(t - MIN_AGE, t)
        if sum(m > 0 for m in months) >= 4 and dd is not None and dd > -0.30:
            elig.append((r6, a))
    elig.sort(reverse=True)
    return pool, [a for _, a in elig[:TOPN]], len(elig)


def analyse(datadir: str, resdir: str, privdir: str):
    recs = []
    for f in sorted(Path(datadir).rglob("vaults-*.jsonl.gz")):
        with gzip.open(f, "rt") as fh:
            recs += [json.loads(l) for l in fh if l.strip()]
    Path(resdir).mkdir(parents=True, exist_ok=True)
    Path(privdir).mkdir(parents=True, exist_ok=True)
    hlp = [r for r in recs if (r.get("name") or "").lower().startswith("hyperliquidity provider")]
    hlp_addr = hlp[0]["addr"] if hlp else None
    series, meta = {}, {}
    n_closed = sum(r["closed"] for r in recs)
    for r in recs:
        if r["addr"] == hlp_addr or r.get("rel") == "child":
            continue
        s = Series(r)
        if len(s.t) >= 3:
            series[r["addr"]] = s
            meta[r["addr"]] = r
    hs = Series(hlp[0]) if hlp else None
    pts = [len(s.t) for s in series.values()]
    lines = ["# V1 Hyperliquid-vaults — uitslag", "",
             f"Vaults in lijst: {len(recs)} (gesloten: {n_closed}); met bruikbare historie: {len(series)}; "
             f"mediaan punten per vault: {st.median(pts) if pts else 0}", "",
             "| Venster | Basispool | Geschikt | Top 10 gem. | Top 10 mediaan | Pool mediaan | HLP | Top10 − mediaan |",
             "|---|---|---|---|---|---|---|---|"]
    rows = []
    deciles = []
    for i in range(len(FORM) - 1):
        t, e = ms(FORM[i]), ms(FORM[i + 1])
        pool, top, n_el = evaluate(series, t)
        pr = [net(series[a].ret(t, e) or 0.0) for a in pool]
        tr = [net(series[a].ret(t, e) or 0.0) for a in top]
        h = net(hs.ret(t, e)) if hs and hs.ret(t, e) is not None else float("nan")
        if not pool or not tr:
            lines.append(f"| {FORM[i]} | {len(pool)} | {n_el} | – | – | – | {h:+.1%} | – |")
            continue
        pm = st.median(pr)
        tm = st.mean(tr)
        rows.append((FORM[i], tm, pm, h))
        lines.append(f"| {FORM[i]} | {len(pool)} | {n_el} | {tm:+.1%} | {st.median(tr):+.1%} | {pm:+.1%} | {h:+.1%} | "
                     f"{tm - pm:+.1%} |")
        # beschrijvend: kwintielen op 6m-rendement in de basispool
        r6 = sorted(((series[a].ret(t - MIN_AGE, t) or 0.0), net(series[a].ret(t, e) or 0.0)) for a in pool)
        k = len(r6) // 5
        if k >= 3:
            deciles.append([st.mean([y for _, y in r6[j * k:(j + 1) * k]]) for j in range(5)])
    ex = [tm - pm for _, tm, pm, _ in rows]
    hit = sum(x > 0 for x in ex)
    avg_top = st.mean([tm for _, tm, _, _ in rows]) if rows else float("nan")
    hv = [h for *_, h in rows if not math.isnan(h)]
    avg_hlp = st.mean(hv) if hv else float("nan")
    t = tstat(ex)
    go1 = rows and hit / len(rows) >= 0.70
    go2 = avg_top > avg_hlp
    go3 = t >= 2
    lines += ["", f"- Top 10 verslaat mediaan in {hit} van {len(rows)} vensters ({'ja' if go1 else 'nee'}, eis ≥ 70%)",
              f"- Gem. vensterrendement top 10 {avg_top:+.1%} vs HLP {avg_hlp:+.1%} ({'ja' if go2 else 'nee'})",
              f"- t-stat (top 10 − mediaan) {t:.2f} ({'ja' if go3 else 'nee'}, eis ≥ 2)",
              f"- **Oordeel: {'GO' if (go1 and go2 and go3) else 'NO-GO'}**", ""]
    if deciles:
        lines += ["Beschrijvend: gem. 3-mnd-rendement per kwintiel van het 6-mnd-rendement (laag → hoog), gemiddeld over vensters:",
                  "", "| K1 | K2 | K3 | K4 | K5 |", "|---|---|---|---|---|",
                  "| " + " | ".join(f"{st.mean(c):+.1%}" for c in zip(*deciles)) + " |", ""]
    # keuze vandaag
    now = int(time.time() * 1000)
    pool, top, n_el = evaluate(series, now)
    lines += [f"## Keuze vandaag ({datetime.now(timezone.utc):%Y-%m-%d}): {n_el} geschikt uit basispool {len(pool)}", "",
              "| # | Vault | Adres | 6-mnd | Accountwaarde | Open voor stortingen |", "|---|---|---|---|---|---|"]
    priv = []
    for j, a in enumerate(top, 1):
        s, m = series[a], meta[a]
        r6 = s.ret(now - MIN_AGE, now) or 0.0
        lines.append(f"| {j} | {m.get('name')} | {short(a)} | {r6:+.1%} | ${s.av_at(now):,.0f} | {m.get('allow')} |")
        priv.append({"rank": j, "name": m.get("name"), "addr": a, "r6": r6, "av": s.av_at(now), "allow": m.get("allow")})
    (Path(resdir) / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (Path(privdir) / "keuze_vandaag.json").write_text(json.dumps(priv, indent=1), encoding="utf-8")
    print(f"analyse klaar: {len(rows)} vensters, hit {hit}, t {t:.2f}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "fetch":
        fetch(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4])
    else:
        analyse(sys.argv[2], sys.argv[3], sys.argv[4])
