"""Munt-mapping Hyperliquid -> Kraken Futures en ordergrootte per instrument."""

from __future__ import annotations

import math

import requests

INSTRUMENTS_URL = "https://futures.kraken.com/derivatives/api/v3/instruments"


def fetch_instruments() -> dict[str, dict]:
    """{symbool: {"step": kleinste ordergrootte in munten, "contract": contractgrootte}} voor verhandelbare perps."""
    data = requests.get(INSTRUMENTS_URL, timeout=30).json()
    out = {}
    for i in data.get("instruments", []):
        sym = i.get("symbol", "")
        if not sym.startswith("PF_") or not i.get("tradeable", True):
            continue
        contract = float(i.get("contractSize") or 1)
        prec = int(i.get("contractValueTradePrecision") or 0)
        out[sym] = {"step": contract * 10.0 ** (-prec), "contract": contract}
    return out


def classify(coin: str) -> str:
    if ":" in coin:
        return "hip3"
    if coin.startswith("@") or "/" in coin:
        return "spot"
    return "perp"


def map_coin(coin: str, instruments: dict) -> tuple[str, float] | None:
    """(Kraken-symbool, vermenigvuldiger voor hoeveelheid) of None als de munt niet op Kraken staat.
    kPEPE = 1000 PEPE: hoeveelheid x1000, prijs /1000."""
    base, mult = coin, 1.0
    if coin.startswith("k") and coin[1:2].isupper():
        base, mult = coin[1:], 1000.0
    if base == "BTC":
        base = "XBT"
    sym = f"PF_{base}USD"
    return (sym, mult) if sym in instruments else None


def round_down(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    return math.floor(qty / step + 1e-9) * step
