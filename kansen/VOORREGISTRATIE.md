# Kansen-tests — vooraf vastgelegd (4 okt 2026, vóór het zien van uitslagen)

Repo is publiek: uitslagen met afgekorte adressen; volledige lijsten alleen versleuteld (secret `LIGHTER_DATA_KEY`).

## V1 Hyperliquid-vaults: blijven winnende vaults winnen?
- Universum: alle vaults uit `stats-data.hyperliquid.xyz/Mainnet/vaults`, inclusief gesloten. HLP (Hyperliquidity Provider) en zijn child-vaults alleen als benchmark.
- Data: `vaultDetails` → `portfolio.allTime` (`accountValueHistory`, `pnlHistory`). Rendement per stukje = Δpnl / accountValue vorige punt (stortingen tellen niet als winst), daarna geketend.
- Vormingsmomenten: 1 okt 2024, 1 jan, 1 apr, 1 jul, 1 okt 2025, 1 jan, 1 apr, 1 jul 2026 (8 vensters). Houden: 3 maanden.
- Basispool op moment t: leeftijd ≥ 182 dagen en accountwaarde ≥ $50k.
- Geschikt: basispool + ≥ 4 van de laatste 6 maanden positief + grootste daling laatste 6 maanden > −30%.
- Keuze: top 10 op rendement laatste 6 maanden, gelijk gewogen. Minder dan 10 geschikt → alle geschikte.
- Rendement voor jou: positief vensterrendement × 0,9 (10% winstdeling leader). Vault gesloten of geen data meer → rendement vanaf dat punt 0.
- Vergelijking: mediaan van de basispool (zelfde winstdeling) en HLP.
- **GO** als alle drie: top 10 verslaat de mediaan in ≥ 70% van de vensters; gemiddeld vensterrendement top 10 > HLP; t-stat van (top 10 − mediaan) over de vensters ≥ 2.

## V2 Nado-traders / V3 GMX v2-traders / V5 Velocity (ex-Drift)
Zelfde regels als Lighter (akkoord Mylan 4 okt):
- Keuze op data t/m 1-8-2026, test 2-8-2026 t/m vandaag.
- Eisen: ≥ 3 maanden historie, ≥ 100 trades, gemiddeld netto rendement per trade > 0 (na 0,1% kosten per kant), actief laatste 14 dagen vóór de knip, ≤ 3 trades per dag, mediane houdtijd ≥ 2 uur, geen market maker (> 50% maker of > 150 fills/dag), ≤ 10 posities tegelijk (p90).
- Trade = positie per munt van plat naar plat (of richtingwissel). Rendement = richting × (gem. uitstap / gem. instap − 1) − 0,2%.
- Rangschikken op potje-% = som netto rendement per trade ÷ p90 aantal gelijktijdige posities. Top 5.
- **GO** als: top 5 gemiddeld positief in de test, ≥ 3 van 5 positief, en beter dan de mediaan van alle geschikte traders.
- Velocity: knip en test op Drift-historie (keuze t/m 31-12-2025, test 1-1 t/m 31-3-2026, vóór de hack); live pas na ~3 maanden nieuwe historie.

## V4 Short vóór grote token-unlocks
- Events: unlock ≥ 10% van de circulerende voorraad op één dag, token < 2 jaar oud bij de unlock, perp op Kraken Futures met koershistorie.
- Trade: short op slot dag −30, sluiten op slot dag +1. Tegelijk long BTC met dezelfde inzet (hedge). Kosten 0,1% per kant per been, funding meenemen als beschikbaar, anders 0,01% per 8 uur aannemen.
- Train: unlocks 2023–2025; test: 2026 t/m vandaag.
- **GO** als in de test: gemiddeld netto rendement per event > 1%, t ≥ 2, ≥ 55% van de events winstgevend. Minder dan 20 test-events → alleen indicatief.

## V6 Activistische 13D-meldingen (aandelen, toegevoegd 4 okt vóór data)
- Events: eerste "SC 13D" of "SCHEDULE 13D" (geen /A) van een filer uit de vaste activistenlijst in `kansen/activist13d.py`, per bedrijf maximaal 1 per filer.
- Bron: SEC EDGAR full-index (form.idx per kwartaal), ticker via SEC `company_tickers.json` (alleen nog genoteerde bedrijven: overlevingsbias, zie uitslag), koersen via Yahoo (yfinance, lokaal).
- Instap: openingskoers van de eerste handelsdag ná de meldingsdatum. Houden 20, 60 en 120 handelsdagen. Abnormaal = aandeel − SPY (ook IWM gerapporteerd). Kosten 0,2% per rondje.
- Filter: gemiddelde dagomzet 20 dagen vóór instap ≥ $2 mln en koers ≥ $3.
- Train 2015–2021, test 2022 t/m vandaag.
- **GO** als in de test: gem. netto abnormaal rendement over 60 dagen > 1% en t ≥ 2.
