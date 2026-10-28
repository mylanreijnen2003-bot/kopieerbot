# Voorregistratie F3 actie 8: kopieerbot vol-sizing + instap na tegen-bar (28-10-2026)

Vastgelegd vóór de run. Niet aanpassen na het zien van resultaten.
Type: overlay-toets op de bestaande G-regel (telt mee in de N van de kopieerbot, + 3).

## Vraag
Verbetert vol-sizing, of wachten op één 5m-tegen-candle vóór de instap, het resultaat van het kopiëren van groep G?

## Basis (ongewijzigd)
- G-regel uit rotatie3: kandidaten van de laatste 84 dagen, top 10 op consistent rendement, buffer top 20, herbalans elke 14 dagen. Dezelfde 20 periodes van 14 dagen (okt 2025 – jul 2026), dezelfde kosten (0,32% per trade), bot-basis (eerste fill, hele inzet).
- Controle: K0 moet het rotatie3-resultaat (+5,7%/mnd) binnen 0,3 procentpunt reproduceren.
- Hyperliquid-funding per uur bij alle varianten; ook zonder rapporteren.

## Varianten
- K0: gelijke inzet, directe instap (basis).
- K1: vol-inzet, directe instap.
- K2: gelijke inzet, instap na tegen-candle.
- K3: vol-inzet, instap na tegen-candle.

## Vol-inzet
- Inzet = basisinzet × clip(σ_ref / σ_dag; 0,5; 2).
- σ_dag = standaardafwijking van het dagrendement van de munt over de 30 dagen tot en met t−1.
- σ_ref = de mediaan van σ_dag over alle trades van de kandidaten in de 84 dagen vóór de herbalans.
- De factor ligt vast bij de eerste fill; bijkopen volgen de basisregel. Max 2× per potje.

## Tegen-candle
- Prijsbron: 5m-candles van Binance USDⓈ-M (data.binance.vision).
- Na de eerste fill van de trader (t): de eerste afgesloten 5m-candle die begint ná t en tegen zijn richting gaat (long: slot < open; short: slot > open) → instap op de open van de volgende 5m-candle.
- Geen tegen-candle binnen 30 minuten (6 candles), of de trader sluit eerder → skip.
- Exit zoals de basis. Een munt zonder Binance-perp → skip "geen 5m".
- Eerlijke vergelijking: K0-B / K1-B = basis met instap op de open van de eerste 5m-candle ná t. K2 tegen K0-B, K3 tegen K1-B.

## Niet getest
Tradestop −1,5 σ (tradestops vernietigen het resultaat bij deze traders), max 3 posities, pauze na 5 verliezen, funding-waarschuwing, slippage-weekrapport (dat is voor de live-bot-spec).

## Lat "naar papier" (per variant tegen zijn vergelijkingsbasis, dezelfde 20 periodes)
1. Gemiddeld netto %/mnd ≥ basis + 0,5 procentpunt.
2. Beter dan de basis in ≥ 12 van de 20 periodes.
3. Slechtste periode en max drawdown niet meer dan 1 procentpunt slechter dan de basis.
4. Bij dubbele kosten (0,64%) nog steeds ≥ de basis.
5. K2/K3: overgeslagen trades ≤ 50%.
- ≥ 100 trades nodig, anders ONBEOORDEELBAAR.
- Alleen ter informatie: t, reality check, BTC.
- GO → schaduwgroep naast G in papier2, oordeel samen met G (december). NEE → G blijft zoals hij is.

## Telling
N kopieerbot + 3.

## Uitvoering (verduidelijking vóór de run, geen nieuwe regels)
- G = rotatie3-variant universum "basis", score "consistent", wissel "buffer", L = 84 d (in `results/rotatie3/samenvatting.csv`: +5,69 %/mnd). De selectie wordt opnieuw berekend met de ongewijzigde code `bt/rot3.py` (stats + simuleer) op de fills van run 37197767731 (`rot-part-*`, `rot-uni`) en `bt-prep-beurzen` van run 37124999519. De K0-controle is zonder funding (zoals rotatie3).
- Per periode en per gekozen trader: potje-rendement = som van inzet × r / K over de trades die in [T, T+14 d) openen, in sluitvolgorde, stop bij −20 % (`rot_stats.vooruit`); periode-rendement = gemiddelde over de 10 gekozen traders; %/mnd = gemiddelde × 30,44 / 14. r = richting × (exit / instap − 1) − kosten − richting × som van de uurfunding tussen instap en exit.
- Funding: Hyperliquid fundingHistory per munt (uurtarief); een long betaalt bij een positief tarief, een short ontvangt. Elk uur-tijdstip met instap ≤ tijdstip < exit telt.
- Vol-inzet: σ_dag uit Hyperliquid-dagslotkoersen (candleSnapshot 1d) van dag t−30 … t−1 (t = UTC-datum van de eerste fill), minstens 20 dagrendementen, anders factor 1. σ_ref = mediaan van σ_dag over alle trades die in [T−84 d, T) openden van alle traders die op T door het universum- en consistent-filter komen (de gerangschikte lijst, niet alleen de top 10). De factor vermenigvuldigt r van die trade.
- Muntkoppeling Binance: kXXX → 1000XXXUSDT, anders XXXUSDT, daarna 1000XXXUSDT; een symbool telt als gekoppeld als de 5m-klines bestaan én de prijs na herschaling met een macht van 10 binnen 25 % van de eerste fill ligt; anders skip "geen 5m" of "koppeling".
- Instap K0-B/K1-B: open van de eerste 5m-candle die begint ná t. K2/K3: candles i = 1 … 6 na t; de eerste met slot tegen de richting; instap op de open van candle i + 1; die instap moet vóór de exit van de trader liggen, anders skip ("trader eerder dicht"); geen tegen-candle in de 6 → skip ("geen tegen-candle"). Exit = gemiddelde uitstapprijs van de trader (bot-basis).
- Een overgeslagen trade telt in het potje als 0. Skip-aandeel = overgeslagen / alle G-trades in de vooruitperioden (alle redenen).
- Lat-punt 3: max drawdown op de samengestelde reeks van de 20 periode-rendementen. Lat-punt 4: variant bij 0,64 % ≥ basis bij 0,32 % (gemiddeld %/mnd; de strengere lezing). Lat-oordeel met funding.
- Info: t = gepaarde t-toets op de 20 periodeverschillen; reality check = 10.000 keer per periode willekeurig variant en basis omwisselen, p = aandeel met een gemiddeld verschil ≥ het gemeten verschil; BTC = rendement BTC per periode (Hyperliquid-dagslot).
- Privacy: adressen alleen afgekort (0x1234…abcd) in rapport en log; data met adressen alleen in de gitignored map `data/rot3_artifacts/` en `data/vol_tegenbar/`.
