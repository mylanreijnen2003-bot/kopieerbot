"""Papier volgen: top 5 Nado + top 5 GMX (uit de kansen-tests), lokaal in PowerShell.

  py -m kansen.papier start    # eenmalig: kiest de traders, start het papieren potje (sleutel nodig)
  py -m kansen.papier update   # elke dag/week: haalt nieuwe trades op en schrijft uit\\papier\\rapport.md

Regels: elke trader een potje van 100; inzet per trade = 100 / K (K = posities die hij meestal tegelijk open heeft).
Alleen posities die ná de start vanaf plat openen. Kosten 0,2% per trade. Trader stopt bij potje −20%.
Oordeel na 90 dagen: groep positief, beter dan BTC, en winst niet van één trader.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tarfile
import time
from pathlib import Path

import pandas as pd
import requests

RAW = "https://raw.githubusercontent.com/mylanreijnen2003-bot/kopieerbot/results/kansen/{}/priv.tgz.enc"
UIT = Path("uit/papier")
TOP = 5
STOP = -20.0


def ontsleutel(blob: bytes, wachtwoord: str) -> bytes:
    """Zelfde formaat als `openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt`."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    assert blob[:8] == b"Salted__", "onbekend bestandsformaat"
    salt, data = blob[8:16], blob[16:]
    km = hashlib.pbkdf2_hmac("sha256", wachtwoord.encode(), salt, 200_000, 48)
    d = Cipher(algorithms.AES(km[:32]), modes.CBC(km[32:])).decryptor()
    out = d.update(data) + d.finalize()
    return out[:-out[-1]]


def selectie(venue: str, key: str) -> pd.DataFrame:
    lokaal = Path(f"uit/{venue}/priv/volgbaar_vandaag.csv")
    if lokaal.exists():   # eigen lokale run: geen sleutel nodig
        print(f"{venue}: lokale selectie {lokaal}")
        return pd.read_csv(lokaal).sort_values("keuze_potje_pct", ascending=False).head(TOP)
    if len(key) < 10:
        sys.exit(f'{venue}: geen lokale selectie. Zet $env:KANSEN_KEY (waarde van GitHub-secret LIGHTER_DATA_KEY) '
                 f'of draai eerst: py -m kansen.{venue} ... (zie uitleg)')
    r = requests.get(RAW.format(venue), timeout=120)
    r.raise_for_status()
    tgz = ontsleutel(r.content, key)
    with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as t:
        naam = next(m for m in t.getnames() if m.endswith("volgbaar_vandaag.csv"))
        df = pd.read_csv(t.extractfile(naam))
    return df.sort_values("keuze_potje_pct", ascending=False).head(TOP)


def nado_sub(acc: str) -> str:
    adres, _, naam = acc.partition("/")
    naam = naam or "default"
    return adres.lower() + naam.encode().hex().ljust(24, "0")


def start():
    key = os.environ.get("KANSEN_KEY", "")
    UIT.mkdir(parents=True, exist_ok=True)
    nu = int(time.time() * 1000)
    traders = []
    for venue in ("nado", "gmx"):
        for r in selectie(venue, key).itertuples():
            traders.append({"venue": venue, "account": r.account, "K": int(max(1, r.p90_posities)),
                            "keuze_potje_pct": float(r.keuze_potje_pct), "munten": r.munten})
    btc = btc_prijs()
    state = {"start_ms": nu, "btc_start": btc, "traders": traders}
    (UIT / "state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")
    print(f"Gestart {time.strftime('%Y-%m-%d %H:%M')}: {len(traders)} traders, BTC ${btc:,.0f}")
    for t in traders:
        print(f"  {t['venue']:5} {t['account'][:10]}…  K={t['K']}  {t['munten']}")


def btc_prijs() -> float:
    j = requests.get("https://futures.kraken.com/derivatives/api/v3/tickers/PF_XBTUSD", timeout=60).json()
    return float(j["ticker"]["last"])


def trades_nado(acc: str, start_ms: int):
    from bt.vast_select import trades_pct
    from kansen import nado
    prods = nado.perps()
    nado.SUBKEY[0] = "subaccounts"
    rows, _ = nado.fills_of(nado_sub(acc), prods)
    sym = prods
    fl = [{"time": r["ts"], "coin": sym.get(r["pid"], str(r["pid"])), "start": round(r["start"], 12),
           "after": round(r["after"], 12), "px": r["px"]} for r in sorted(rows, key=lambda r: (r["ts"], r["idx"]))
          if r["ts"] >= start_ms]
    return trades_pct(fl), 0


def trades_gmx(acc: str, start_ms: int):
    from kansen import gmx
    rows, ts = [], start_ms // 1000
    while True:
        q = (f'{{ tradeActions(limit: 1000, orderBy: [timestamp_ASC, id_ASC], where: {{ account_eq: "{acc}", '
             f'eventName_eq: "OrderExecuted", timestamp_gt: {ts} }}) {{ id account marketAddress isLong orderType '
             f'sizeDeltaUsd basePnlUsd pnlUsd timestamp }} }}')
        b = gmx.gql(q)["tradeActions"]
        rows += b
        if len(b) < 1000:
            break
        ts = max(int(x["timestamp"]) for x in b) - 1
    if not rows:
        return [], 0
    df = pd.DataFrame(rows).drop_duplicates("id")
    df["orderType"] = df.orderType.astype(int)
    df = df.sort_values(["timestamp", "id"])
    namen = gmx.markt_namen()
    tr = [(namen.get(c[:8].lower(), c[:8]) + c[8:], a, b, r) for c, a, b, r in gmx.trades(df)]
    return tr, 0


def update():
    st = json.loads((UIT / "state.json").read_text(encoding="utf-8"))
    s0 = st["start_ms"]
    dagen = (time.time() * 1000 - s0) / 86_400_000
    btc = btc_prijs()
    btc_pct = 100 * (btc / st["btc_start"] - 1)
    rijen = []
    for t in st["traders"]:
        try:
            tr, open_n = (trades_nado if t["venue"] == "nado" else trades_gmx)(t["account"], s0)
        except Exception as e:
            print(f"  {t['account'][:10]}…: ophalen mislukt ({e!r:.80})")
            tr, open_n = [], 0
        pot, gestopt, n = 0.0, False, 0
        for c, a, b, r in sorted(tr, key=lambda x: x[2]):
            pot += 100 * r / t["K"]
            n += 1
            if pot <= STOP:
                gestopt = True
                break
        winst = sum(1 for x in tr[:n] if x[3] > 0)
        rijen.append({"venue": t["venue"], "trader": t["account"][:6] + "…" + t["account"][-4:], "trades": n,
                      "winst_pct_trades": round(100 * winst / n) if n else None, "potje_pct": round(pot, 1),
                      "gestopt": gestopt})
    df = pd.DataFrame(rijen)
    L = [f"# Papier Nado + GMX — dag {dagen:.0f} van 90 ({time.strftime('%Y-%m-%d %H:%M')})", "",
         "| Groep | Potje gem. | Positief | Trades |", "|---|---|---|---|"]
    for v in ("nado", "gmx"):
        g = df[df.venue == v]
        L.append(f"| {v.upper()} top 5 | {g.potje_pct.mean():+.1f}% | {int((g.potje_pct > 0).sum())} van {len(g)} | {int(g.trades.sum())} |")
    L.append(f"| Samen | {df.potje_pct.mean():+.1f}% | {int((df.potje_pct > 0).sum())} van {len(df)} | {int(df.trades.sum())} |")
    L += [f"| BTC vasthouden | {btc_pct:+.1f}% | | |", "", "| Venue | Trader | Trades | Winst-% | Potje | Gestopt |",
          "|---|---|---|---|---|---|"]
    for r in rijen:
        L.append(f"| {r['venue']} | {r['trader']} | {r['trades']} | {r['winst_pct_trades'] or '–'} | {r['potje_pct']:+.1f}% | "
                 f"{'ja' if r['gestopt'] else ''} |")
    beste = df.potje_pct.max() if len(df) else 0
    L += ["", f"Winst zonder beste trader: {df.potje_pct.drop(df.potje_pct.idxmax()).mean():+.1f}%" if len(df) > 1 else "",
          "", "Oordeel na 90 dagen: GO als groep > 0, > BTC en positief zonder de beste trader."]
    (UIT / "rapport.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    hist = UIT / "geschiedenis.csv"
    df.assign(datum=time.strftime("%Y-%m-%d"), btc_pct=round(btc_pct, 1)).to_csv(hist, mode="a", header=not hist.exists(), index=False)
    print("\n".join(L))


if __name__ == "__main__":
    {"start": start, "update": update}[sys.argv[1]]()
