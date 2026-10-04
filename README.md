# kopieerbot

Papieren kopieerbot op Hyperliquid-wallets. Geen echt geld, geen orders.

- `live/`: live papierfase kopieerbot (Hyperliquid -> Kraken Futures), zie `live/README.md`.
- `bot/` code: `hl.py` (API), `sim.py` (regels), `paper.py` (dagelijkse run), `report.py` (tussenstand/oordeel), `diagnose.py` (analyse oude bug).
- `REGELS.md`: vooraf vastgelegde test H3b.
- `data/`: wallet-selectie en archief van de oude test H3.
- Resultaten: branch `results` (`paper/`, `diagnose/`).
- Workflows: `paper.yml` dagelijks 05:23 UTC, `diagnose.yml` eenmalig.

## Privacy (AVG): volledige wallet-adressen alleen versleuteld

De repo is openbaar. Bestanden met volledige wallet-adressen staan alleen versleuteld als `<naam>.kluis`
(sleutel: secret `DATA_KEY`, in workflows als env `KLUIS_KEY`). Zie `bt/kluis.py`.
- Na het klaarzetten van `res`: `python3 bt/kluis.py open res`; vóór `git add`: `python3 ../bt/kluis.py dicht .`
- Bestanden van branch results lezen: `python3 bt/kluis.py show <pad>` (niet `git show`).
- Rapporten (.md/.log/.txt) en meldingen: alleen afgekorte adressen (`0x2555…34b1`). Geen volledige adressen in
  bestandsnamen of commitberichten. Logs in Actions worden via `bt/__init__.py` automatisch afgekort.
- `*.enc` is van de Lighter-papierbot (eigen sleutel `LIGHTER_DATA_KEY`) en blijft zoals het is.
