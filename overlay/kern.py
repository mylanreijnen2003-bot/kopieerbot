"""F3-8 overlay op groep G (docs/prereg-vol-tegenbar.md): vol-inzet en instap na een 5m-tegen-candle.

Alles in ms (UTC). Selectie en potje-rekening komen ongewijzigd uit bt/rot3.py en bt/rot_stats.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bt import rot3
from bt.rot_stats import vooruit

M5 = 300_000
UUR = 3_600_000
DAG = 86_400_000
H = 14
TEGEN_N = 6
VOL_MIN, VOL_MAX = 0.5, 2.0
SIGMA_DAGEN = 30
SIGMA_MIN = 20
REF_DAGEN = 84


# ------------------------------------------------------------------------------- G-selectie

def g_selectie(s: pd.DataFrame, uni="basis", score="consistent", wissel="buffer", L=84) -> dict:
    """Zelfde wisselregel als rot3.simuleer; geeft per T de gekozen traders (volgorde zoals rot3)."""
    held, potje, piek, out = [], {}, {}, {}
    sL = s[s.L == L]
    for T in sorted(sL["T"].unique()):
        g = sL[sL["T"] == T].set_index("address")
        b = rot3.rangorde(rot3.universum(g.reset_index(), uni), score)
        lijst = list(b.address)
        rank = {a: i for i, a in enumerate(lijst)}
        grens = 20 if "buffer" in wissel else rot3.N
        nieuw = []
        for a in held:
            weg = a not in rank or rank[a] >= grens
            if "eruit" in wissel and not weg:
                weg = potje[a] <= 0.80 or potje[a] <= 0.75 * piek[a] or a not in g.index
            if weg:
                potje.pop(a, None)
                piek.pop(a, None)
            else:
                nieuw.append(a)
        for a in lijst:
            if len(nieuw) >= rot3.N:
                break
            if a not in nieuw:
                nieuw.append(a)
                potje[a], piek[a] = 1.0, 1.0
        held = nieuw
        for a in held:
            x = float(g.loc[a, "v14_pct"]) if a in g.index else 0.0
            potje[a] *= 1 + x / 100
            piek[a] = max(piek[a], potje[a])
        out[int(T)] = list(held)
    return out


def kandidaten(s: pd.DataFrame, T: int, uni="basis", score="consistent", L=84) -> list:
    """Alle traders die op T door het universum- en score-filter komen (de gerangschikte lijst)."""
    g = s[(s.L == L) & (s["T"] == T)]
    return list(rot3.rangorde(rot3.universum(g, uni), score).address)


def potje_pct(trades: list, k: int) -> tuple[float, bool]:
    """trades = [(sluit_t, r_effectief), ...]; potje-rendement in % met de rot_stats-stop."""
    if not trades:
        return 0.0, False
    tr = sorted(trades, key=lambda x: x[0])
    tot, gestopt = vooruit(np.array([x[1] for x in tr]), k)
    return 100 * tot, gestopt


def perioden_rendement(rets) -> dict:
    """rets = periode-rendementen in % (gemiddelde over de gekozen traders)."""
    r = np.asarray(rets, float)
    eq = np.cumprod(1 + r / 100)
    piek = np.maximum.accumulate(np.r_[1.0, eq])[1:]
    return {"per_maand_pct": float(r.mean() * 30.44 / H), "positief": int((r > 0).sum()),
            "slechtste_pct": float(r.min()), "max_dd_pct": float(100 * (eq / piek - 1).min()),
            "totaal_pct": float(100 * (eq[-1] - 1))}


# ------------------------------------------------------------------------------- vol-inzet

def vol_factor(s_ref: float, s_dag: float) -> float:
    if not (np.isfinite(s_ref) and np.isfinite(s_dag)) or s_dag <= 0:
        return 1.0
    return float(np.clip(s_ref / s_dag, VOL_MIN, VOL_MAX))


def sigma_dag(slot: pd.Series, t: int) -> float:
    """Std van de dagrendementen over de 30 dagen t-30 .. t-1 (t = UTC-datum van de fill).
    `slot`: dagslotkoersen, index = datum (tz-naive, 00:00). Minder dan 20 rendementen -> NaN."""
    d = pd.Timestamp(t, unit="ms").normalize()
    c = slot[(slot.index >= d - pd.Timedelta(days=SIGMA_DAGEN + 1)) & (slot.index <= d - pd.Timedelta(days=1))]
    c = c.asfreq("D") if len(c) else c
    r = c.pct_change().dropna()
    r = r[r.index >= d - pd.Timedelta(days=SIGMA_DAGEN)]
    return float(r.std(ddof=1)) if len(r) >= SIGMA_MIN else float("nan")


def sigma_ref(trades: pd.DataFrame, T: int) -> float:
    """Mediaan van sigma (kolom `sigma`) over trades met open_t in [T-84 d, T)."""
    m = (trades["open_t"] >= T - REF_DAGEN * DAG) & (trades["open_t"] < T)
    x = trades.loc[m, "sigma"].dropna()
    return float(x.median()) if len(x) else float("nan")


# ------------------------------------------------------------------------------- 5m-instap

def instap_eerste_5m(k5: pd.DataFrame, t: int, exit_t: int):
    """Open van de eerste 5m-candle die begint ná t (K0-B/K1-B); (tijd, prijs) of een skipreden."""
    idx = k5.index.to_numpy()
    i = np.searchsorted(idx, t, side="right")
    if i >= len(idx):
        return "geen 5m"
    if idx[i] >= exit_t:
        return "trader eerder dicht"
    return int(idx[i]), float(k5["open"].iloc[i])


def instap_tegen(k5: pd.DataFrame, t: int, richting: int, exit_t: int):
    """Eerste afgesloten 5m-candle die begint ná t en tegen de richting gaat (long: slot < open; short:
    slot > open), binnen 6 candles; instap op de open van de volgende candle. Anders een skipreden."""
    idx = k5.index.to_numpy()
    o, c = k5["open"].to_numpy(float), k5["close"].to_numpy(float)
    i0 = np.searchsorted(idx, t, side="right")
    for i in range(i0, min(i0 + TEGEN_N, len(idx))):
        if idx[i] != idx[i0] + (i - i0) * M5:
            break                                     # gat in de 5m-reeks: niet verder zoeken
        if idx[i] + M5 > exit_t:
            return "trader eerder dicht"
        tegen = c[i] < o[i] if richting > 0 else c[i] > o[i]
        if tegen:
            if i + 1 >= len(idx):
                return "geen 5m"
            if idx[i + 1] >= exit_t:
                return "trader eerder dicht"
            return int(idx[i + 1]), float(o[i + 1])
    return "geen tegen-candle"


# ------------------------------------------------------------------------------- funding en koppeling

def funding_r(rates: pd.Series | None, instap_t: int, exit_t: int, richting: int) -> float:
    """Funding als rendement: long betaalt bij positief tarief, short ontvangt; uren instap <= tijd < exit."""
    if rates is None or not len(rates):
        return 0.0
    idx = rates.index.to_numpy()
    a, b = np.searchsorted(idx, instap_t, side="left"), np.searchsorted(idx, exit_t, side="left")
    return float(-richting * rates.to_numpy(float)[a:b].sum())


def koppel(coin: str, bestaat) -> str | None:
    """Hyperliquid-munt -> Binance USDT-M-symbool (kPEPE -> 1000PEPEUSDT); None = geen perp."""
    namen = []
    if len(coin) > 1 and coin[0] == "k" and coin[1:].isupper():
        namen.append(f"1000{coin[1:]}USDT")
    namen += [f"{coin}USDT", f"1000{coin}USDT", f"1000000{coin}USDT"]
    for n in namen:
        if bestaat(n):
            return n
    return None


def schaal(hl_px: float, bn_px: float) -> float | None:
    """Macht van 10 waarmee de Binance-prijs naar de Hyperliquid-schaal gaat; None bij > 25 % verschil."""
    if not (hl_px > 0 and bn_px > 0):
        return None
    e = round(np.log10(hl_px / bn_px))
    f = 10.0 ** e
    return f if abs(hl_px / (bn_px * f) - 1) <= 0.25 else None
