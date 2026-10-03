# H3b: papieren kopieerbot, vooraf vastgelegd (3 okt 2026)

Vervangt H3 (ongeldig door fouten, archief in `data/h3_oud/`). Na de start niets meer aanpassen.

## Wallets
- Dezelfde 60 als H3 (`data/selection.json`, gekozen op data t/m 18-8-2026).

## Regels
- Per wallet een potje van $1.000, start 19-8-2026 00:00 UTC.
- Alleen perps. Spot en HIP-3 (aandelen-perps) overslaan.
- Schone start: posities die de leider al had, niet overnemen. Een munt pas kopiëren als de leider daarin vanuit plat (0) opent of van richting wisselt.
- Positie = (eigen equity / perps-accountwaarde leider) × positie leider. Accountwaarde ≤ $1.000 of onbekend: mediane accountwaarde uit de selectie.
- Hefboomplafond: totale positiewaarde ≤ 2 × eigen equity. Afbouwen gaat naar verhouding.
- Uitvoering: fillprijs leider ± 5 bp, plus 4,5 bp fee.
- Funding per uur meegenomen.
- Elk uur risico-check op de slechtste prijs van dat uur:
  - equity < 50% van de benodigde marge: gedwongen sluiten
  - equity ≤ $800: stop, potje dicht
  - sluiten gebeurt tegen die slechtste prijs.
- Wallets met ≥ 9.900 fills in één ophaalronde: vlag "onvolledig", tellen niet mee.

## Oordeel na ≥ 90 dagen (~17 nov 2026)
Portefeuille = som van alle potjes. GO als alles klopt:
- gemiddeld dagrendement > 0
- Newey-West t ≥ 3
- positief zonder de 2 beste dagen
- max drawdown ≥ −20%
