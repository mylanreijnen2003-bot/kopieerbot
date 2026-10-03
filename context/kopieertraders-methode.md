# Methode: winstgevende traders vinden en kopiëren (elke chain) — stand 3 okt 2026 (avond)

Kort, alleen wat nodig is. Kopie van het projectdocument; volledige walletadressen staan hier bewust niet in.

## Doel en regels van Mylan
- Doel: geld verdienen. 5 traders volgen; uitvoeren via Kraken (perps) of Bitvavo (spot).
- Elke trader een **even groot potje**. Inzet per trade = potje ÷ K (aantal posities dat die trader meestal tegelijk open heeft, 90e percentiel). Vaste inzet.
- Universum: ≥ 3 maanden geschiedenis, ≥ 2 trades/week, ≥ 30–100 afgeronde trades; market makers en > 150 fills/dag eruit.
- Kiezen op data t/m een knipdatum, eerlijk testen op de periode erna. Rekenen met de prijs ná vertraging, niet de prijs van de trader.
- Uitvoering: Kraken Futures (MiFID, 0,02%/0,05%), retail max **2× hefboom** (ESMA). Bitvavo alleen spot long.

## Begrippen
- Fill: (deel)uitvoering van een order. Trade: positie van plat (0) tot weer plat, of richtingwissel.
- Winst-% trades: aandeel trades met winst; zegt niets over grootte.
- Scalper: trader die posities minuten tot een paar uur houdt en per trade weinig verdient.

## Stappen (nieuwe chain of nieuwe selectie)
1. Databron checken: fills per wallet (tijd, munt, prijs, grootte, richting, startpositie), PnL-historie, wallets vinden.
2. Scan alle wallets (`bt/scan.py`, `bt/scan_combine.py`).
3. Trades reconstrueren (`bt/vast_select.py` → `trades_pct`, `bt/delay_test.py` → `trades_detail`): plat→plat, rendement − 0,2% kosten.
4. Potje-statistieken (`bt/brede_stats.py`), kiezen (`bt/brede_select.py`).
5. Controles: open posities / echte accountwinst (`bt/check_open.py`), vertraging (`bt/delay_test.py`).
6. Papieren test vooruit, daarna klein met echt geld.

## Lessen
- **Vertraging is beslissend.** Scalpers verliezen ~90% van hun winst als je 15 min later instapt (top 5, 19-8 t/m 2-10: +€1.141 → +€18 op €500). Met de hand alleen traders met lange houdtijd; scalpers alleen met een bot (seconden).
- Ook met een bot: kosten (fee + spread) per trade zijn vaak bijna net zo groot als hun winst per trade. Meten in de papierfase.
- Selectie op gerealiseerde winst kan misleiden → open posities / accountwinst checken.
- "Beste ooit" kiezen werkt niet: van 138 "bewezen winnaars" bleef na de data 44% winstgevend.
- Alleen nieuwe posities vanuit plat kopiëren (halve kopie gaf potjes van $0).
- Hefboom begrenzen (2×), stop per trader (−20%).
- Activiteit eisen: veel gekozen traders stoppen kort daarna.
- Winst-% ≠ geld; proportioneel kopiëren ≠ vaste inzet.
- Kleine selecties zijn ruis: pas na weken papier oordelen.

## Waar alles staat
- Resultaten: branch `results` (`brede/`, `ronde3/`, `paper*/`).
- Live papierbot: `live/` (spec: `context/kopieerbot-live-bot.md`).
