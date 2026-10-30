"""F3-8 run: K0-K3, K0-B, K1-B bij 0,32 % en 0,64 % kosten, met en zonder funding (docs/prereg-vol-tegenbar.md).

Stappen (elk met cache in data/vol_tegenbar/, gitignored; bevat volledige adressen):
  stats   bt.rot3 stats (ongewijzigd) over alle 12 delen van de rotatie-fills
  kies    rot3.simuleer (G = basis/consistent/buffer/84) -> controle +5,69 %/mnd; G-selectie per T
  trades  bot-trades van de gekozen traders en van alle kandidaten (voor sigma_ref)
  prijzen Hyperliquid dagslot + funding, Binance 5m
  reken   varianten, lat, rapport (results/vol_tegenbar/rapport.md, alleen afgekorte adressen)

Gebruik: python -m overlay.run [--parallel 3]
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from bt import data as btdata
from bt import rot3
from bt.bot_stats import bot_trades
from bt.rot_stats import DAG, EIND, TS

from . import data as od
from .kern import (H, M5, funding_r, g_selectie, instap_eerste_5m, instap_tegen, kandidaten, koppel, perioden_rendement,
                   potje_pct, schaal, sigma_dag, sigma_ref, vol_factor)

REPO = Path(__file__).resolve().parents[1]
ART = Path(os.environ.get("VT_ART", r"C:\Users\mylan\kopieerbot\data\rot3_artifacts"))
WERK = Path(os.environ.get("VT_WERK", REPO / "data" / "vol_tegenbar"))
CACHE = WERK / "cache"
UIT = REPO / "results" / "vol_tegenbar"
PREREG = REPO / "docs" / "prereg-vol-tegenbar.md"
EXPERIMENTS = REPO / "experiments.jsonl"

G_REF = 5.69
G_TOL = 0.3
KOSTEN = (0.0032, 0.0064)
BASIS_KOSTEN = 0.0032
VARIANTEN = {  # code: (vol-inzet, instap)
    "K0": (False, "direct"), "K1": (True, "direct"), "K2": (False, "tegen"), "K3": (True, "tegen"),
    "K0-B": (False, "5m"), "K1-B": (True, "5m")}
VERGELIJK = {"K1": "K0", "K2": "K0-B", "K3": "K1-B"}
SEED = 42


def kort(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}"


# ------------------------------------------------------------------------------------- stappen

def stap_stats(par: int, log=print):
    out = WERK / "r3"
    out.mkdir(parents=True, exist_ok=True)
    delen = sorted(glob.glob(str(ART / "rot-part-*" / "**" / "fills_*.parquet"), recursive=True))
    if len(delen) < 12 or glob.glob(str(ART / "*.bezig")):
        raise SystemExit(f"rot-part-* nog niet compleet ({len(delen)} van 12 fills-bestanden); eerst stap 0 afmaken")
    todo = [i for i in range(12) if not (out / f"r3_{i}.parquet").exists()]
    lopend = []
    t0 = time.time()
    while todo or lopend:
        while todo and len(lopend) < par:
            i = todo.pop(0)
            fh = open(WERK / f"stats_{i}.log", "w")
            p = subprocess.Popen([sys.executable, "-m", "bt.rot3", "stats", str(i), "12",
                                  str(ART / "rot-uni" / "universe.parquet"), str(ART), str(ART / "bt-prep-beurzen"),
                                  str(out)], cwd=REPO, stdout=fh, stderr=subprocess.STDOUT)
            lopend.append((i, p, fh))
        for i, p, fh in list(lopend):
            if p.poll() is not None:
                fh.close()
                lopend.remove((i, p, fh))
                if p.returncode != 0:
                    raise SystemExit(f"rot3 stats deel {i} faalde (zie {WERK / f'stats_{i}.log'})")
                log(f"  stats deel {i} klaar ({time.time() - t0:.0f} s)")
        time.sleep(5)


def laad_stats() -> pd.DataFrame:
    return pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(str(WERK / "r3" / "r3_*.parquet")))],
                     ignore_index=True)


def stap_trades(adressen, log=print) -> pd.DataFrame:
    p = WERK / "trades.parquet"
    if p.exists():
        return pd.read_parquet(p)
    rij = []
    lijst = sorted(adressen)
    for chunk in np.array_split(np.array(lijst), max(1, len(lijst) // 400)):
        f = btdata.fills(str(ART), set(chunk))
        f = f[f.ts < EIND]
        for a, x in f.groupby("address"):
            fl = [{"time": int(t), "coin": c, "start": s, "after": af, "px": px}
                  for t, c, s, af, px in zip(x.ts, x.coin, x.start, x.after, x.px)
                  if not str(c).startswith(("#", "@"))]
            for t in bot_trades(fl):
                rij.append({"address": a, "coin": t[0], "o": int(t[1]), "c": int(t[2]), "dir": int(t[3]),
                            "first": float(t[4]), "exit": float(t[6])})
        log(f"  trades: {len(rij)} na {len(set(r['address'] for r in rij))} adressen")
    d = pd.DataFrame(rij)
    d.to_parquet(p, index=False)
    return d


# ------------------------------------------------------------------------------------- rekenen

def maanden(t_ms):
    a = pd.Timestamp(t_ms, unit="ms")
    b = a + pd.Timedelta(hours=1)
    return sorted({a.strftime("%Y-%m"), b.strftime("%Y-%m")})


def run(par: int, log=print):
    t0 = time.time()
    log("Stap stats (bt.rot3, ongewijzigd) ...")
    stap_stats(par, log)
    s = laad_stats()
    r_g, df_g = rot3.simuleer(s, "basis", "consistent", "buffer", 84)
    log(f"Controle G: {r_g['per_maand_pct']:.2f} %/mnd (rotatie3: {G_REF:.2f})")
    if abs(r_g["per_maand_pct"] - G_REF) > G_TOL:
        raise SystemExit(f"K0 reproduceert rotatie3-G niet binnen {G_TOL} pp: {r_g['per_maand_pct']:.2f}; gestopt")
    held = g_selectie(s)
    kand = {T: kandidaten(s, T) for T in held}
    s84 = s[s.L == 84].set_index(["address", "T"])
    adressen = set().union(*held.values()) | set().union(*kand.values())
    log(f"G: {len(set().union(*held.values()))} verschillende traders over {len(held)} perioden; "
        f"kandidaten totaal {len(adressen)}")
    log_experimenten(log)

    log("Stap trades ...")
    tr = stap_trades(adressen, log)
    fwd = []
    for T, hs in held.items():
        for a in hs:
            x = tr[(tr.address == a) & (tr.o >= T) & (tr.o < T + H * DAG)]
            fwd.append(x.assign(T=T, K=int(s84.loc[(a, T), "K"])))
    fwd = pd.concat(fwd, ignore_index=True)
    log(f"  {len(fwd)} G-trades in de vooruitperioden")

    log("Stap prijzen (Hyperliquid dagslot + funding, Binance 5m) ...")
    start = min(held) - 130 * DAG
    eind = EIND + 30 * DAG
    ref = pd.concat([tr[(tr.address.isin(kand[T])) & (tr.o >= T - 84 * DAG) & (tr.o < T)].assign(Tref=T)
                     for T in held], ignore_index=True)
    munten = sorted(set(fwd.coin) | set(ref.coin) | {"BTC"})
    dag = {}
    for i, c in enumerate(munten):
        dag[c] = od.hl_dag(CACHE, c, start, eind)
        if (i + 1) % 50 == 0:
            log(f"  dagslot {i + 1}/{len(munten)}")
    fund = {c: od.hl_funding(CACHE, c, min(held), eind) for c in sorted(set(fwd.coin))}
    sym = {}
    for c in sorted(set(fwd.coin)):
        for m in sorted({mm for t in fwd.loc[fwd.coin == c, "o"] for mm in maanden(t)}):
            sym[(c, m)] = koppel(c, lambda n, m=m: od.bn_5m(CACHE, n, m) is not None)
    log(f"  prijzen klaar ({time.time() - t0:.0f} s)")
    return reken(held, s84, fwd, ref, dag, fund, sym, r_g, df_g, t0, log)


def reken(held, s84, fwd, ref, dag, fund, sym, r_g, df_g, t0, log=print):
    """Varianten, controle en rapport; alle prijsdata staat dan al in de cache."""
    # sigma per trade (memo per munt en datum)
    memo = {}

    def sig(c, t):
        k = (c, pd.Timestamp(t, unit="ms").normalize())
        if k not in memo:
            memo[k] = sigma_dag(dag.get(c, pd.Series(dtype=float)), t)
        return memo[k]

    ref["sigma"] = [sig(c, t) for c, t in zip(ref.coin, ref.o)]
    s_ref = {T: sigma_ref(ref[ref.Tref == T], T) for T in held}
    fwd["sigma"] = [sig(c, t) for c, t in zip(fwd.coin, fwd.o)]
    fwd["factor"] = [vol_factor(s_ref[T], x) for T, x in zip(fwd["T"], fwd.sigma)]

    # instap per opzet
    def k5(c, t):
        delen = [od.bn_5m(CACHE, sym[(c, m)], m) for m in maanden(t) if sym.get((c, m))]
        delen = [d for d in delen if d is not None]
        return pd.concat(delen).sort_index() if delen else None

    inst = {"direct": [], "5m": [], "tegen": []}
    for r in fwd.itertuples(index=False):
        inst["direct"].append((r.o, r.first))
        if not sym.get((r.coin, maanden(r.o)[0])):
            inst["5m"].append("geen 5m")
            inst["tegen"].append("geen 5m")
            continue
        kk = k5(r.coin, r.o)
        i0 = kk.index.searchsorted(r.o, side="right") if kk is not None else 0
        f = schaal(r.first, float(kk["open"].iloc[i0])) if kk is not None and i0 < len(kk) else None
        if f is None:
            inst["5m"].append("koppeling")
            inst["tegen"].append("koppeling")
            continue
        for naam, fn in (("5m", lambda: instap_eerste_5m(kk, r.o, r.c)),
                         ("tegen", lambda: instap_tegen(kk, r.o, r.dir, r.c))):
            x = fn()
            inst[naam].append(x if isinstance(x, str) else (x[0], x[1] * f))

    res = {}
    for v, (vol, opzet) in VARIANTEN.items():
        for kost in KOSTEN:
            for met_f in (True, False):
                rets, rr, skips, gen = [], [], {}, 0
                for T, hs in held.items():
                    per = []
                    for a in hs:
                        m = (fwd["T"] == T) & (fwd.address == a)
                        lst = []
                        for j in np.flatnonzero(m.to_numpy()):
                            x = inst[opzet][j]
                            if isinstance(x, str):
                                skips[x] = skips.get(x, 0) + 1
                                continue
                            q = fwd.iloc[j]
                            r = q.dir * (q.exit / x[1] - 1) - kost
                            if met_f:
                                r += funding_r(fund.get(q.coin), x[0], q.c, q.dir)
                            lst.append((q.c, r * (q.factor if vol else 1.0)))
                            rr.append(r)
                            gen += 1
                        per.append(potje_pct(lst, int(s84.loc[(a, T), "K"]))[0])
                    rets.append(float(np.mean(per)) if per else 0.0)
                res[(v, kost, met_f)] = {"rets": rets, **perioden_rendement(rets), "trades": gen, "skips": skips,
                                         "gem_trade_pct": 100 * float(np.mean(rr)) if rr else np.nan}
        log(f"  {v}: {res[(v, BASIS_KOSTEN, True)]['per_maand_pct']:.2f} %/mnd (met funding, 0,32 %)")

    # controle op tradeniveau: K0 zonder funding = v14_pct van rot3
    k0 = res[("K0", BASIS_KOSTEN, False)]
    verschil = float(np.max(np.abs(np.array(k0["rets"]) - df_g.top_pct.to_numpy())))
    btc = dag["BTC"]
    btc_p = [100 * (btc.asof(pd.Timestamp(T + H * DAG, unit="ms")) / btc.asof(pd.Timestamp(T, unit="ms")) - 1)
             for T in held]
    schrijf(res, held, fwd, r_g, verschil, btc_p, s_ref, time.time() - t0)
    log(f"Rapport: {UIT / 'rapport.md'} ({time.time() - t0:.0f} s)")
    return res


# ------------------------------------------------------------------------------------- lat en rapport

def lat(res, v, b):
    V, B = res[(v, BASIS_KOSTEN, True)], res[(b, BASIS_KOSTEN, True)]
    V2 = res[(v, 0.0064, True)]
    alle = V["trades"] + sum(V["skips"].values())
    skip = sum(V["skips"].values()) / alle if alle else np.nan
    p = {"1 %/mnd >= basis + 0,5": V["per_maand_pct"] >= B["per_maand_pct"] + 0.5,
         "2 beter in >= 12 van 20": int(sum(x > y for x, y in zip(V["rets"], B["rets"]))) >= 12,
         "3 slechtste en max DD <= 1 pp slechter": (V["slechtste_pct"] >= B["slechtste_pct"] - 1
                                                   and V["max_dd_pct"] >= B["max_dd_pct"] - 1),
         "4 bij 0,64 % >= basis": V2["per_maand_pct"] >= B["per_maand_pct"]}
    if v in ("K2", "K3"):
        p["5 skip <= 50 %"] = skip <= 0.5
    if V["trades"] < 100:
        oordeel = "ONBEOORDEELBAAR"
    else:
        oordeel = "GO" if all(p.values()) else "NEE"
    d = np.array(V["rets"]) - np.array(B["rets"])
    t = float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d)))) if d.std(ddof=1) > 0 else np.nan
    rng = np.random.default_rng(SEED)
    tekens = rng.choice([-1, 1], size=(10_000, len(d)))
    rc = float(((tekens * d).mean(axis=1) >= d.mean()).mean())
    return p, oordeel, skip, int(sum(x > y for x, y in zip(V["rets"], B["rets"]))), t, rc


def fmt(x, d=2):
    return "-" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{d}f}"


def tabel(rijen, kol):
    return ("| " + " | ".join(kol) + " |\n|" + "---|" * len(kol) + "\n"
            + "".join("| " + " | ".join(str(r.get(k, "")) for k in kol) + " |\n" for r in rijen))


def schrijf(res, held, fwd, r_g, verschil, btc_p, s_ref, duur):
    UIT.mkdir(parents=True, exist_ok=True)
    L = []
    w = L.append
    w("# F3-8 kopieerbot: vol-inzet en instap na tegen-candle (overlay op G)\n")
    w(f"Run {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC, commit {_commit()}, voorregistratie "
      f"`docs/prereg-vol-tegenbar.md@sha1:{_sha(PREREG)}`. 20 perioden van 14 dagen vanaf 2025-10-20. Duur {duur:.0f} s.\n")
    w(f"\nControle: G (rot3.simuleer, basis/consistent/buffer/84) = {r_g['per_maand_pct']:.2f} %/mnd "
      f"(rotatie3: {G_REF:.2f}; marge {G_TOL} pp) -> geslaagd. K0 zonder funding wijkt per periode maximaal "
      f"{verschil:.4f} pp af van rot3.\n")
    w(f"\nG-traders: {len(set().union(*held.values()))} verschillend; G-trades in de vooruitperioden: {len(fwd)}.\n")
    w("\n## Lat 'naar papier' (met funding, 0,32 %)\n")
    rij = []
    for v, b in VERGELIJK.items():
        p, oordeel, skip, beter, t, rc = lat(res, v, b)
        rij.append({"variant": f"{v} tegen {b}", **{k: "[JA]" if x else "[NEE]" for k, x in p.items()},
                    "trades": res[(v, BASIS_KOSTEN, True)]["trades"], "skip": f"{100 * skip:.1f} %",
                    "oordeel": oordeel, "t (info)": fmt(t), "reality check p (info)": fmt(rc, 3)})
    kol = ["variant", "1 %/mnd >= basis + 0,5", "2 beter in >= 12 van 20", "3 slechtste en max DD <= 1 pp slechter",
           "4 bij 0,64 % >= basis", "5 skip <= 50 %", "trades", "skip", "oordeel", "t (info)", "reality check p (info)"]
    w(tabel(rij, kol))
    w("\n## Kerncijfers per variant\n")
    rij = []
    for (v, kost, met_f), x in res.items():
        rij.append({"variant": v, "kosten": f"{100 * kost:.2f} %", "funding": "ja" if met_f else "nee",
                    "%/mnd": fmt(x["per_maand_pct"]), "positief (van 20)": x["positief"],
                    "slechtste %": fmt(x["slechtste_pct"]), "max DD %": fmt(x["max_dd_pct"]),
                    "totaal %": fmt(x["totaal_pct"], 1), "trades": x["trades"],
                    "gem. netto % per trade": fmt(x["gem_trade_pct"], 3),
                    "skips": ", ".join(f"{k} {n}" for k, n in sorted(x["skips"].items())) or "-"})
    w(tabel(rij, list(rij[0].keys())))
    w("\n## Per periode (met funding, 0,32 %; % per 14 dagen)\n")
    rij = []
    for i, T in enumerate(held):
        rij.append({"start": f"{pd.Timestamp(T, unit='ms'):%Y-%m-%d}",
                    **{v: fmt(res[(v, BASIS_KOSTEN, True)]["rets"][i]) for v in VARIANTEN},
                    "BTC (info)": fmt(btc_p[i]), "sigma_ref": fmt(s_ref[T], 4)})
    w(tabel(rij, list(rij[0].keys())))
    w(f"\nBTC (info): gemiddeld {fmt(float(np.mean(btc_p)) * 30.44 / H)} %/mnd over dezelfde perioden.\n")
    w("\nVol-factor (K1/K3): " + f"gemiddeld {fwd.factor.mean():.2f}, mediaan {fwd.factor.median():.2f}, "
      f"aandeel op 0,5 {100 * (fwd.factor <= 0.5).mean():.0f} %, op 2 {100 * (fwd.factor >= 2).mean():.0f} %, "
      f"zonder sigma (factor 1) {100 * fwd.sigma.isna().mean():.0f} %.\n")
    (UIT / "rapport.md").write_text("\n".join(L), encoding="utf-8")


def _sha(p):
    return hashlib.sha1(p.read_bytes()).hexdigest()[:12]


def _commit():
    try:
        return subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:
        return "onbekend"


def log_experimenten(log=print):
    bestaand = set()
    if EXPERIMENTS.exists():
        for raw in EXPERIMENTS.read_text(encoding="utf-8").splitlines():
            try:
                bestaand.add(json.loads(raw).get("variant"))
            except ValueError:
                pass
    nieuw = [v for v in VARIANTEN if f"F3-8-{v}" not in bestaand]
    if not nieuw:
        log("Herhaalrun: de regels staan al in experiments.jsonl; niets toegevoegd.")
        return
    nu = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(EXPERIMENTS, "a", encoding="utf-8") as fh:
        for v in nieuw:
            telt = v in VERGELIJK
            fh.write(json.dumps({"variant": f"F3-8-{v}", "type": "overlay-toets" if telt else "vergelijkingsbasis",
                                 "telt_in_N": telt, "N_increment": 1 if telt else 0, "basis": "G (rotatie3 basis/consistent/buffer/84)",
                                 "vergelijk_met": VERGELIJK.get(v), "regels": VARIANTEN[v],
                                 "rules": f"docs/prereg-vol-tegenbar.md@sha1:{_sha(PREREG)}", "commit": _commit(),
                                 "kosten": list(KOSTEN), "perioden": 20, "verdict": None,
                                 "uitkomst": "results/vol_tegenbar/rapport.md", "logged_at": nu}) + "\n")
    log(f"experiments.jsonl: {len(nieuw)} regels toegevoegd (K1-K3 tellen in N).")


def main(argv=None):
    ap = argparse.ArgumentParser(description="F3-8 overlay op G (eenmalig)")
    ap.add_argument("--parallel", type=int, default=3)
    a = ap.parse_args(argv)
    run(a.parallel)


if __name__ == "__main__":
    main()
