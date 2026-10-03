"""Vooraf vastgelegde varianten (zie BACKTEST.md)."""

import math

from bt.engine import Cfg


def variants(venues: dict) -> dict[str, Cfg]:
    kr = frozenset(venues["kraken"]) if venues.get("kraken") else None
    bv = frozenset(venues["bitvavo"]) if venues.get("bitvavo") else None
    return {
        "H4": Cfg("H4: Kraken, 2x, stop -20%", lev=2.0, stop=0.80, coins=kr),
        "P": Cfg("P: Kraken, 2x, stop -35%", lev=2.0, stop=0.65, coins=kr),
        "K_stop_geen": Cfg("Kraken, 2x, geen stop", lev=2.0, stop=None, coins=kr),
        "K_stop20": Cfg("Kraken, 2x, stop -20%", lev=2.0, stop=0.80, coins=kr),
        "K_stop50": Cfg("Kraken, 2x, stop -50%", lev=2.0, stop=0.50, coins=kr),
        "K_lev1": Cfg("Kraken, 1x, stop -35%", lev=1.0, stop=0.65, coins=kr),
        "K_lev5": Cfg("Kraken, 5x, stop -35% (niet toegestaan particulier)", lev=5.0, stop=0.65, coins=kr),
        "B_bitvavo": Cfg("Bitvavo spot: alleen long, 1x, stop -35%", lev=1.0, stop=0.65, funding=False,
                         long_only=True, coins=bv),
        "H_ref": Cfg("Hyperliquid referentie: alle munten, geen plafond, geen stop", lev=math.inf, stop=None,
                     fee=0.00045, coins=None),
    }
