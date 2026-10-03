# Strenge backtest kopieerbot, vooraf vastgelegd (3 okt 2026, geschiktheid aangepast 15:25 vóór enige uitkomst)

Na de start niets aanpassen. Wat niet in dit document staat, is beschrijvend.

## Data
- Hyperliquid-fills 28-7-2025 t/m 18-8-2026 (Hugging Face `craftify2221/hyperliquid-fills-raw`).
- Universum: de 1.151 wallets uit de train-pool zonder market makers (`data/universe.parquet`). Die pool is alleen op train-data bepaald.
- Train: 28-7-2025 t/m 28-2-2026. Test: 1-3-2026 t/m 18-8-2026.
- Accountwaarde: wekelijkse snapshots uit de dataset. Funding: Hyperliquid-API. Uurkaarsen: uit de taker-trades.
- Beurslijsten (Kraken-perps, Bitvavo-spot): stand van vandaag.

## Kopieerregels
Gelijk aan H3b (`REGELS.md`):
- alleen perps, geen HIP-3
- schone start bij het begin van elk venster
- positie = eigen equity / accountwaarde leider × positie leider
- elk uur een risico-check op de slechtste prijs.

## Wallets kiezen (alleen train)
- Geschikt:
  - ≥ 100 afgeronde trades (positie terug naar 0 of van richting gewisseld), ongeacht hoe lang de wallet actief is
  - ≥ 30 actieve dagen (genoeg dagrendementen om te meten)
  - mediaan ≤ 150 fills per actieve dag
  - ≥ 50 gekopieerde fills
  - potje in train niet gestopt
- Rangorde: Sharpe (rendement gedeeld door beweeglijkheid) van de dagrendementen van het kopieerpotje in variant P.
- Sets:
  - kwintiel: beste 20%, max 100 (**hoofdtoets**)
  - top 10
  - top 30
  - breed: alle geschikte met positief gemiddelde
  - geverifieerd: geschikte wallets die in train ook de strenge toets haalden (≥ 6 mnd, ≥ 60% winstmaanden, geen winst uit één maand/dag)
  - controle: onderste 20%

## Afwijking (3 okt 16:30, vóór enige uitkomst)
- Funding niet meegenomen: het ophalen duurde > 75 min. In H3b was funding klein (−$39 op 54 potjes).

## Varianten (test)
- **P (hoofdtoets)**: alleen munten op Kraken, max 2× hefboom, stop −35%, kosten 5 bp slippage + 5 bp fee, funding aan.
- Gevoeligheid:
  - stop geen / −20% / −50%
  - hefboom 1× / 5×
  - Bitvavo spot (alleen long, 1×, munten op Bitvavo)
  - Hyperliquid-referentie (alle munten, geen plafond, geen stop)

## Portefeuille en oordeel
- Elke gekozen wallet krijgt een eigen potje van $1.000 op 1-3-2026. Portefeuille = som van de potjes.
- **GO** alleen als de hoofdtoets (kwintiel × P) aan alles voldoet:
  - gemiddeld dagrendement > 0
  - Newey-West t ≥ 3
  - positief zonder de 2 beste dagen
  - max drawdown ≥ −20%
  - rendement hoger dan BTC aanhouden in dezelfde periode
- Andere combinaties zijn alleen gevoeligheid. Eén losse GO daartussen is geen bewijs: er worden 40 combinaties getest.
- Bij GO: daarna eerst een papieren test vooruit vanaf 19-8-2026 met dezelfde regels, vóór echt geld.
