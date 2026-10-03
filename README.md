# kopieerbot

Papieren kopieerbot op Hyperliquid-wallets. Geen echt geld, geen orders.

- `live/`: live papierfase kopieerbot (Hyperliquid -> Kraken Futures), zie `live/README.md`.
- `bot/` code: `hl.py` (API), `sim.py` (regels), `paper.py` (dagelijkse run), `report.py` (tussenstand/oordeel), `diagnose.py` (analyse oude bug).
- `REGELS.md`: vooraf vastgelegde test H3b.
- `data/`: wallet-selectie en archief van de oude test H3.
- Resultaten: branch `results` (`paper/`, `diagnose/`).
- Workflows: `paper.yml` dagelijks 05:23 UTC, `diagnose.yml` eenmalig.
