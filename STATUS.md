# STATUS — kopieerbot

**Doel:** Hyperliquid-traders vinden van wie de trades na kopieervertraging en kosten winstgevend blijven, en die kopiëren (Kraken Futures / Bitvavo) — eerst op papier, later eventueel met een klein bedrag.

**Status:** papier (geen echt geld, geen orders).

**Wat draait waar:**
- VPS: systemd `kopieerbot.service` (`python -m live.main`, papier) en `hlflow-collector.service`
- GitHub Actions (actief, gepland): `papier2`, `papier-hl`, `papier2-f-select`, `slippage-meting`, `volg`, `weekrapport`; uit: `paper`, `paper-h4`, `papier-nado`, `lighter-papier`, `papier2-select`
- Lokaal: analyses in `reverse/` en `hlflow/`

**Laatste uitslag:** 2026-10-25 — papier2 dag 21: controlegroep D −2,0 %, BTC −5,3 % in dezelfde periode; papier-hl herstart 24 okt (−2,4 % na 1,4 dag).

**F3-8 (2026-10-30, branch `claude/vol-tegenbar`):** vol-inzet en tegen-candle-instap als overlay op groep G: K1, K2 en K3 alle drie NEE (lat 4, de 0,64 %-kosten, faalt overal; K2/K3 ook skip 51 % > 50 %). K0 met funding 5,35 %/mnd (zonder 5,69, gelijk aan rotatie3); K0-B (instap op de eerste 5m-open na de fill) maar 0,29 %/mnd: de G-winst zit vrijwel helemaal in instappen op de prijs van de trader zelf. N +3. Rapport: `results/vol_tegenbar/rapport.md`.

**Volgende actie:** papier2 en papier-hl laten doorlopen; 90-dagenoordeel H3b/H4 rond 17 nov 2026; besluit over oude walletadressen in de git-geschiedenis (repo is openbaar).

**Belangrijkste bestanden:**
- `README.md`, `REGELS.md`, `BACKTEST.md`
- `context/kopieerbot-live-bot.md`, `context/kopieertraders-methode.md`
- `bt/engine.py`, `bot/hl.py`, `live/engine.py`
