# live/ — papierfase kopieerbot

Volgt live de fills van 5 Hyperliquid-traders en doet ze op papier na tegen Kraken Futures bid/ask. Geen orders, geen API-sleutels.

- Instellingen: `.env` in de repo-root (zie `.env.example`, staat in `.gitignore`).
- Start: `python -m live.main` · dry-run: `python -m live.main --replay 24` · rapport nu: `python -m live.main --rapport`
- Tests: `python -m pytest live/tests`
- Logs: `live/data/kopie_trades.csv` (eigen uitvoering) en `live/data/trader_prijzen.csv` (zelfde regels op de prijzen van de trader, zonder vertraging, spread en fee). `state.json` = potjes en posities.

Regels: inzet per nieuwe positie = potje / K; blootstelling max `MAX_LEVERAGE` × potje; alleen posities vanaf plat of na richtingwissel; bijkopen/afbouwen naar verhouding; kopen tegen ask, verkopen tegen bid, 0,05% fee per kant; potje op `TRADER_STOP_PCT` → alles sluiten en trader pauzeren (blijft gepauzeerd tot `state.json` wordt aangepast).
Extra statussen: `te laat` (openen > 5 min na de fill, bv. na een herstart; sluiten gebeurt altijd), `max hefboom` (bijkopen/openen geblokkeerd door het 2×-plafond) en `geen prijs`. Bij `te klein` staat in `min_potje` het potje waarbij die order het Kraken-minimum haalt.
Rapport: trade = plat tot plat (of richtingwissel); vervangregel (7 dagen geen trade, account < $100, potje −20%) wordt per trader gemeld. Funding wordt niet meegenomen.
Replay gebruikt Kraken 1m-kaarsen + de huidige spread, dus geeft een benadering.
