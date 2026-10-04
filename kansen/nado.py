"""V2: Nado-traders (perps op Ink), via de openbare archive-indexer.

python -m kansen.nado pool <out>                       # actieve subaccounts via steekproef van marktbrede matches
python -m kansen.nado fetch <shard> <n> <pooldir> <out>  # alle perp-fills per subaccount (max MAXF)
python -m kansen.nado analyse <datadir> <res> <priv>

Pool: elke 6 uur 1 uur aan matches van alle markten, in de 3 weken vóór de knip en de laatste 3 weken.
Een trader met >= 4 trades in 14 dagen valt vrijwel zeker in de steekproef (>= 8 fills, kans op missen < 25%^...).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

A = "https://archive.prod.nado.xyz/v1"
G = "https://gateway.prod.nado.xyz/v1"
X18 = 1e18
MAXF = 20_000
KOSTEN = 0.002
KNIP_S = int(pd.Timestamp("2026-08-01").timestamp())
_next = [0.0]


def post(body, gap=0.35, tries=8):
    for i in range(tries):
        w = _next[0] - time.time()
        if w > 0:
            time.sleep(w)
        _next[0] = time.time() + gap
        try:
            r = requests.post(A, json=body, timeout=90)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(5 * (i + 1))
                continue
            print("nado", r.status_code, r.text[:200])
            return None
        except requests.RequestException:
            time.sleep(5 * (i + 1))
    return None


def perps() -> dict:
    s = requests.get(f"{G}/symbols", timeout=60).json()
    return {int(x["product_id"]): x["symbol"] for x in s if x.get("type") == "perp"}


def pool(out: str):
    prods = perps()
    print("perp-markten:", len(prods))
    now = int(time.time())
    windows = [(KNIP_S - 21 * 86400, KNIP_S), (now - 21 * 86400, now)]
    seen = {}
    nreq = 0
    for a, b in windows:
        for h in range(b, a, -6 * 3600):          # elk 6e uur: matches in [h-3600, h]
            idx = None
            while True:
                q = {"product_ids": list(prods), "limit": 500, "max_time": h}
                if idx is not None:
                    q = {"product_ids": list(prods), "limit": 500, "idx": str(idx)}
                d = post({"matches": q})
                nreq += 1
                if not d or not d.get("matches"):
                    break
                ts = {int(t["submission_idx"]): int(t["timestamp"]) for t in d.get("txs", [])}
                stop = False
                for m in d["matches"]:
                    t = ts.get(int(m["submission_idx"]), h)
                    if t < h - 3600:
                        stop = True
                        continue
                    s = m["order"]["sender"]
                    seen[s] = seen.get(s, 0) + 1
                if stop or len(d["matches"]) < 500:
                    break
                idx = min(int(m["submission_idx"]) for m in d["matches"]) - 1
    Path(out).mkdir(parents=True, exist_ok=True)
    json.dump(seen, open(Path(out) / "pool.json", "w"))
    print(f"pool: {len(seen)} subaccounts, {nreq} verzoeken")


SUBKEY = ["subaccounts"]


def subq(sub):
    return {"subaccounts": [sub]} if SUBKEY[0] == "subaccounts" else {"subaccount": sub}


def kies_subkey(sub, prods):
    for k in ("subaccounts", "subaccount"):
        SUBKEY[0] = k
        d = post({"matches": {**subq(sub), "product_ids": list(prods), "limit": 5}})
        if d and d.get("matches") and all(m["order"]["sender"] == sub for m in d["matches"]):
            print("subaccount-filter:", k)
            return
    raise RuntimeError("geen werkend subaccount-filter")


def naam(sub: str) -> str:
    try:
        n = bytes.fromhex(sub[42:]).rstrip(b"\0").decode(errors="ignore")
    except ValueError:
        n = ""
    return sub[:42] + ("/" + n if n and n != "default" else "")


def fills_of(sub: str, prods: dict):
    rows, idx, n = [], None, 0
    while True:
        q = {**subq(sub), "product_ids": list(prods), "limit": 500}
        if idx is not None:
            q["idx"] = str(idx)
        d = post({"matches": q})
        if not d or not d.get("matches"):
            break
        ts = {int(t["submission_idx"]): int(t["timestamp"]) for t in d.get("txs", [])}
        for m in d["matches"]:
            pb = ((m.get("pre_balance") or {}).get("base") or {}).get("perp")
            qb = ((m.get("post_balance") or {}).get("base") or {}).get("perp")
            if not pb or not qb:
                continue
            bf = float(m["base_filled"]) / X18
            if bf == 0:
                continue
            rows.append({"ts": ts.get(int(m["submission_idx"]), 0) * 1000, "idx": int(m["submission_idx"]),
                         "pid": int(pb["product_id"]), "start": float(pb["balance"]["amount"]) / X18,
                         "after": float(qb["balance"]["amount"]) / X18,
                         "px": abs(float(m["quote_filled"]) / float(m["base_filled"])),
                         "maker": not bool(m.get("is_taker")), "d": m.get("digest")})
        n += len(d["matches"])
        if len(d["matches"]) < 500 or n >= MAXF:
            break
        idx = min(int(m["submission_idx"]) for m in d["matches"]) - 1
    return rows, n >= MAXF


def fetch(shard: int, nsh: int, pooldir: str, out: str):
    prods = perps()
    seen = json.load(open(Path(pooldir) / "pool.json"))
    subs = sorted(seen)
    mine = [s for i, s in enumerate(subs) if i % nsh == shard]
    Path(out).mkdir(parents=True, exist_ok=True)
    allr, capped = [], []
    if mine:
        kies_subkey(mine[0], prods)
    t0 = time.time()
    for k, s in enumerate(mine):
        rows, cap = fills_of(s, prods)
        if cap:
            capped.append(s)
        for r in rows:
            r["sub"] = s
        allr += rows
        if k % 200 == 0:
            print(f"shard {shard}: {k}/{len(mine)}, {len(allr)} fills, {time.time()-t0:.0f}s", flush=True)
    pd.DataFrame(allr).to_parquet(Path(out) / f"nado-{shard}.parquet")
    json.dump({"capped": capped, "symbols": {str(k): v for k, v in prods.items()}}, open(Path(out) / f"meta-{shard}.json", "w"))
    print(f"shard {shard} klaar: {len(mine)} subs, {len(allr)} fills, {len(capped)} afgekapt")


def analyse(datadir: str, res: str, priv: str):
    from bt.vast_select import trades_pct
    from kansen.kies import rapport
    files = sorted(Path(datadir).rglob("nado-*.parquet"))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    meta = [json.load(open(f)) for f in sorted(Path(datadir).rglob("meta-*.json"))]
    sym = {int(k): v for m in meta for k, v in m["symbols"].items()}
    capped = {s for m in meta for s in m["capped"]}
    df = df.drop_duplicates(["sub", "d", "idx"]).sort_values(["sub", "ts", "idx"])
    data = {}
    for sub, g in df.groupby("sub", sort=False):
        if sub in capped or len(g) < 100:
            continue
        fl = [{"time": int(r.ts), "coin": sym.get(int(r.pid), str(r.pid)), "start": round(r.start, 12),
               "after": round(r.after, 12), "px": r.px} for r in g.itertuples(index=False)]
        tr = trades_pct(fl)
        if len(tr) < 100:
            continue
        data[naam(sub)] = {"trades": tr, "fills_ts": g.ts.values, "maker": float(g.maker.mean())}
    noot = [f"Bron: Nado archive-indexer, {df['sub'].nunique():,} actieve subaccounts uit steekproef, {len(df):,} perp-fills. "
            f"Afgekapt (> {MAXF:,} fills, vrijwel zeker market makers): {len(capped)}. Kosten 0,2% per trade."]
    rapport("V2 Nado-traders", data, res, priv, noot)


if __name__ == "__main__":
    c = sys.argv[1]
    if c == "pool":
        pool(sys.argv[2])
    elif c == "fetch":
        fetch(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5])
    else:
        analyse(*sys.argv[2:5])
