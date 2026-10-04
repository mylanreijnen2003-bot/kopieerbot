# Kopieerbot live: alles wat de bot nodig heeft (stand 3 okt 2026, 22:45)

Doel: automatisch, binnen seconden, de trades van 5 Hyperliquid-traders nadoen op **Kraken Futures** (perps), eerst op papier.
Zie ook `context/kopieertraders-methode.md` (methode en lessen).
**Volledige adressen staan alleen in `.env`** (`TRADERS=adres:K,adres:K,...`), nooit in de repo (openbaar).

## Waarom automatisch
- Getest op 19-8 t/m 2-10 (keuze op data t/m 18-8), €100 per trader, top 5:
  - op hun prijzen (bot, seconden): gem. **+€228 per €100** (€500 → ~€1.641)
  - 15 min later (handmatig): gem. −€7 per €100
- De traders zijn **scalpers**: houdtijd 0,5–2 u, ~99% winst-%, +0,3–1% per trade, 4–40 trades/dag. Elke minuut en elke 0,1% kosten telt. Verwachting na echte vertraging + spread: eerder de helft tot twee derde van +€228. De papieren fase moet dat meten.

## Fasering
1. **Papier (2–4 weken):** bot volgt live, plaatst géén orders, rekent met echte Kraken bid/ask op het moment van het signaal (+ taker-fee). Logt alles.
2. **Klein live:** pas als papier na 2–4 weken duidelijk positief is na kosten en vertraging. Klein bedrag, zelfde regels.
3. Elke maand traderlijst herzien (regel hieronder).

## Traders (startlijst, uit `results/brede/top30.csv`, branch `results`)
Plek 5 van de top 30 (`0xccee…51b5`) is inactief → vervangen door de volgende actieve (`0xb36f…d02c`).
| # | Adres (kort) | K | Trades/dag | Houdtijd | Winst/trade | Account |
|---|---|---|---|---|---|---|
| 1 | 0xc3b1…4b11 | 1 | 7,6 | 1,0 u | 1,13% | $198k |
| 2 | 0xbee5…5665 | 1 | 11,8 | 0,5 u | 0,74% | $1k |
| 3 | 0xdc89…f196 | 1 | 4,4 | 0,7 u | 0,66% | $14k |
| 4 | 0xa1b6…4f04 | 6 | 39,7 | 1,6 u | 0,79% | $505k |
| 5 | 0xb36f…d02c | 1 | 5,4 | 1,3 u | 0,63% | $1,2k |
- K = aantal posities dat de trader meestal tegelijk open heeft (90e percentiel).
- Niet de lijst uit `handmatig2_top30` gebruiken (dat was de handmatige route en is afgevallen).
- Ronde 3 (selectie met ≥ 1 uur vertraging, ≥ 30 trades) loopt nog; uitslag in `results/ronde3/`.
- Vervangregel: trader 7 dagen geen trade, account < $100, of eigen papieren potje −20% → vervangen door de volgende actieve in `results/brede/top30.csv`.

## Kopieerregels
- Elke trader een **gelijk potje** (bijv. €100 op papier). Inzet per trade = potje ÷ K. Vaste inzet, niet de grootte of hefboom van de trader.
- Inzet onder het Kraken-minimum → signaal "te klein": loggen, en per trader het minimale potje berekenen.
- **Alleen nieuwe posities vanuit plat** kopiëren (startPosition = 0, of richtingwissel). Posities die de trader al had bij de start: negeren tot hij plat is.
- Volgen: openen, bijkopen (naar verhouding, binnen het potje), afbouwen (naar verhouding), sluiten, richtingwissel.
- Alleen munten die als perp op Kraken staan. Munt-mapping: Hyperliquid `kPEPE` → `PEPE` (k-prefix = ×1000, let op de eenheid), `BTC` = Kraken `XBT` (`PF_XBTUSD`). Niet op Kraken → overslaan en loggen.
- Geen HIP-3 (munten met `:` erin) en geen spot (`@…`).
- **Max 2× hefboom** per potje (wettelijke grens voor particulieren, ESMA).
- **Stop per trader:** potje −20% → trader pauzeren en melden.
- Orders: market of IOC-limit (iets door de spread heen). Altijd reduce-only bij afbouwen/sluiten.

## Databron: Hyperliquid (gratis, geen sleutel)
- Websocket `wss://api.hyperliquid.xyz/ws`, per trader:
  `{"method":"subscribe","subscription":{"type":"userFills","user":"0x…"}}`
  - Eerste bericht is een snapshot (`isSnapshot: true`) → negeren.
  - Velden per fill: `coin`, `px`, `sz`, `side` (B = koop, A = verkoop), `time`, `startPosition`, `dir`, `closedPnl`, `fee`, `tid`, `oid`, `crossed`.
  - Positie na fill = startPosition + (sz als B, −sz als A).
  - Eén order kan in veel fills komen: groeperen per (trader, munt) binnen ~1–2 s, dan één kopie-order.
- Herverbinden met backoff; ping elke ~30 s. Backup: elke minuut `POST https://api.hyperliquid.xyz/info` `{"type":"userFillsByTime","user":…,"startTime":…}`.
- Bestaande code: `bot/hl.py` (API), `bot/sim.py` (schone start, frac, 2×), `bt/delay_test.py` (`trades_detail`).

## Uitvoering: Kraken Futures (MiFID, NL toegestaan)
- REST `https://futures.kraken.com/derivatives/api/v3`. Publiek: `/tickers`, `/instruments`. Privé: `/sendorder`, `/openpositions`, `/accounts`, `/leveragepreferences`.
- Auth: API-key + `Authent`-header (HMAC-SHA512). **Controleer de actuele Kraken-docs.**
- Websocket prijzen: `wss://futures.kraken.com/ws/v1` (feed `ticker`).
- Kosten: 0,02% maker / 0,05% taker + spread. Papierfase: alleen publieke endpoints.

## Draaien
- Mylans **OVH-VPS**: systemd-service, automatisch herstarten.
- Repo openbaar → nooit sleutels, ntfy-topic of volledige adressen committen. Alles in `.env`.
- Meldingen via **ntfy** (`POST https://ntfy.sh/<geheim-topic>`): elke kopie-trade (trader kort, munt, richting, prijs trader vs eigen prijs, vertraging s), stop/pauze, verbinding weg > 2 min, dagoverzicht.

## Wat de papierfase meet
- Per trade: tijd trader, tijd eigen order, vertraging (s), prijs trader, eigen prijs (Kraken bid/ask), slippage %, fee, resultaat.
- Per trader en totaal: winst €, winst-%, gem. vertraging, gem. slippage, gemiste trades (niet op Kraken / te klein).
- GO voor live: na ≥ 2 weken en ≥ 100 trades papier positief na alle kosten, en gem. slippage + fees < de helft van hun gem. winst per trade.

## Werkafspraken Mylan
- Eerst conclusie + plan, pas bouwen na akkoord. Kort, alleen stappen. Vaktermen uitleggen.
- Bij commando's die hij zelf moet doen: tijdsinschatting en kant-en-klare commando's.
