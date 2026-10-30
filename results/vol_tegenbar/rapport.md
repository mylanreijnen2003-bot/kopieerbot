# F3-8 kopieerbot: vol-inzet en instap na tegen-candle (overlay op G)

Run 2026-10-30 12:39 UTC, commit 0668272c2c61, voorregistratie `docs/prereg-vol-tegenbar.md@sha1:ba7a4671a4e0`. 20 perioden van 14 dagen vanaf 2025-10-20. Duur 3404 s.


Controle: G (rot3.simuleer, basis/consistent/buffer/84) = 5.69 %/mnd (rotatie3: 5.69; marge 0.3 pp) -> geslaagd. K0 zonder funding wijkt per periode maximaal 0.0001 pp af van rot3.


G-traders: 108 verschillend; G-trades in de vooruitperioden: 3017.


## Lat 'naar papier' (met funding, 0,32 %)

| variant | 1 %/mnd >= basis + 0,5 | 2 beter in >= 12 van 20 | 3 slechtste en max DD <= 1 pp slechter | 4 bij 0,64 % >= basis | 5 skip <= 50 % | trades | skip | oordeel | t (info) | reality check p (info) |
|---|---|---|---|---|---|---|---|---|---|---|
| K1 tegen K0 | [NEE] | [NEE] | [JA] | [NEE] |  | 3017 | 0.0 % | NEE | -2.80 | 0.997 |
| K2 tegen K0-B | [JA] | [JA] | [JA] | [NEE] | [NEE] | 1465 | 51.4 % | NEE | 3.47 | 0.001 |
| K3 tegen K1-B | [JA] | [JA] | [JA] | [NEE] | [NEE] | 1465 | 51.4 % | NEE | 3.39 | 0.001 |


## Kerncijfers per variant

| variant | kosten | funding | %/mnd | positief (van 20) | slechtste % | max DD % | totaal % | trades | gem. netto % per trade | skips |
|---|---|---|---|---|---|---|---|---|---|---|
| K0 | 0.32 % | ja | 5.35 | 12 | -5.11 | -6.72 | 54.4 | 3017 | 0.043 | - |
| K0 | 0.32 % | nee | 5.69 | 13 | -5.13 | -5.96 | 59.4 | 3017 | 0.063 | - |
| K0 | 0.64 % | ja | -0.87 | 6 | -6.53 | -28.16 | -10.9 | 3017 | -0.277 | - |
| K0 | 0.64 % | nee | -0.53 | 6 | -6.55 | -25.86 | -8.1 | 3017 | -0.257 | - |
| K1 | 0.32 % | ja | 3.38 | 10 | -4.95 | -7.01 | 30.1 | 3017 | 0.043 | - |
| K1 | 0.32 % | nee | 3.72 | 10 | -4.41 | -6.07 | 34.3 | 3017 | 0.063 | - |
| K1 | 0.64 % | ja | -2.58 | 4 | -8.15 | -37.81 | -23.8 | 3017 | -0.277 | - |
| K1 | 0.64 % | nee | -2.26 | 4 | -8.15 | -35.86 | -21.5 | 3017 | -0.257 | - |
| K2 | 0.32 % | ja | 1.64 | 9 | -5.41 | -13.36 | 14.8 | 1465 | -0.099 | geen 5m 336, geen tegen-candle 15, koppeling 41, trader eerder dicht 1160 |
| K2 | 0.32 % | nee | 1.90 | 9 | -4.71 | -11.78 | 17.7 | 1465 | -0.065 | geen 5m 336, geen tegen-candle 15, koppeling 41, trader eerder dicht 1160 |
| K2 | 0.64 % | ja | -0.81 | 6 | -6.74 | -19.48 | -8.1 | 1465 | -0.419 | geen 5m 336, geen tegen-candle 15, koppeling 41, trader eerder dicht 1160 |
| K2 | 0.64 % | nee | -0.54 | 6 | -6.05 | -17.93 | -5.7 | 1465 | -0.385 | geen 5m 336, geen tegen-candle 15, koppeling 41, trader eerder dicht 1160 |
| K3 | 0.32 % | ja | 0.11 | 8 | -4.19 | -13.62 | 0.2 | 1465 | -0.099 | geen 5m 336, geen tegen-candle 15, koppeling 41, trader eerder dicht 1160 |
| K3 | 0.32 % | nee | 0.37 | 8 | -3.65 | -12.13 | 2.6 | 1465 | -0.065 | geen 5m 336, geen tegen-candle 15, koppeling 41, trader eerder dicht 1160 |
| K3 | 0.64 % | ja | -2.18 | 6 | -5.19 | -22.24 | -18.9 | 1465 | -0.419 | geen 5m 336, geen tegen-candle 15, koppeling 41, trader eerder dicht 1160 |
| K3 | 0.64 % | nee | -1.94 | 6 | -5.06 | -20.76 | -17.0 | 1465 | -0.385 | geen 5m 336, geen tegen-candle 15, koppeling 41, trader eerder dicht 1160 |
| K0-B | 0.32 % | ja | 0.29 | 8 | -5.72 | -18.04 | 1.5 | 1904 | -0.128 | geen 5m 336, koppeling 41, trader eerder dicht 736 |
| K0-B | 0.32 % | nee | 0.57 | 8 | -5.02 | -16.45 | 4.3 | 1904 | -0.101 | geen 5m 336, koppeling 41, trader eerder dicht 736 |
| K0-B | 0.64 % | ja | -2.95 | 6 | -6.83 | -30.22 | -24.7 | 1904 | -0.448 | geen 5m 336, koppeling 41, trader eerder dicht 736 |
| K0-B | 0.64 % | nee | -2.66 | 7 | -6.12 | -28.77 | -22.5 | 1904 | -0.421 | geen 5m 336, koppeling 41, trader eerder dicht 736 |
| K1-B | 0.32 % | ja | -1.09 | 8 | -4.52 | -19.89 | -10.3 | 1904 | -0.128 | geen 5m 336, koppeling 41, trader eerder dicht 736 |
| K1-B | 0.32 % | nee | -0.81 | 7 | -4.52 | -18.38 | -8.0 | 1904 | -0.101 | geen 5m 336, koppeling 41, trader eerder dicht 736 |
| K1-B | 0.64 % | ja | -4.13 | 5 | -6.28 | -34.46 | -32.4 | 1904 | -0.448 | geen 5m 336, koppeling 41, trader eerder dicht 736 |
| K1-B | 0.64 % | nee | -3.86 | 5 | -6.27 | -33.06 | -30.6 | 1904 | -0.421 | geen 5m 336, koppeling 41, trader eerder dicht 736 |


## Per periode (met funding, 0,32 %; % per 14 dagen)

| start | K0 | K1 | K2 | K3 | K0-B | K1-B | BTC (info) | sigma_ref |
|---|---|---|---|---|---|---|---|---|
| 2025-10-20 | 33.20 | 30.85 | -2.23 | -2.28 | -1.94 | -1.99 | -3.58 | 0.0481 |
| 2025-11-03 | 6.11 | 4.56 | -0.19 | -0.28 | -2.72 | -2.67 | -13.38 | 0.0528 |
| 2025-11-17 | 2.21 | 1.91 | 5.09 | 3.54 | 3.67 | 2.56 | -6.56 | 0.0581 |
| 2025-12-01 | -2.26 | -2.16 | -1.17 | -1.42 | -1.71 | -1.80 | 0.24 | 0.0573 |
| 2025-12-15 | -3.80 | -3.60 | -3.76 | -3.65 | -4.42 | -4.52 | 0.93 | 0.0543 |
| 2025-12-29 | 1.64 | 1.80 | -0.66 | 0.10 | -2.19 | -1.26 | 4.61 | 0.0504 |
| 2026-01-12 | -0.09 | 0.02 | -5.41 | -4.19 | -5.72 | -4.31 | -3.23 | 0.0462 |
| 2026-01-26 | 0.90 | -1.30 | -0.98 | -2.75 | -1.24 | -2.92 | -20.62 | 0.0428 |
| 2026-02-09 | -2.09 | -1.72 | -2.11 | -1.51 | -3.14 | -2.33 | -7.79 | 0.0397 |
| 2026-02-23 | 0.65 | -0.16 | 0.78 | -0.08 | 0.88 | 0.02 | 5.81 | 0.0445 |
| 2026-03-09 | 2.62 | 2.22 | 2.93 | 2.37 | 2.61 | 2.12 | 3.61 | 0.0505 |
| 2026-03-23 | -0.25 | -0.88 | -0.76 | -1.25 | -0.55 | -1.13 | -2.88 | 0.0482 |
| 2026-04-06 | 2.12 | 0.39 | -0.74 | -1.97 | -1.93 | -3.48 | 10.15 | 0.0438 |
| 2026-04-20 | 4.98 | 4.65 | 5.59 | 4.51 | 4.93 | 3.71 | 5.28 | 0.0428 |
| 2026-05-04 | -5.11 | -3.94 | 0.25 | 1.47 | -0.25 | 0.99 | -3.53 | 0.0410 |
| 2026-05-18 | 5.76 | 1.60 | 6.76 | 2.52 | 5.23 | 1.33 | -7.24 | 0.0369 |
| 2026-06-01 | 2.26 | -2.01 | 0.67 | -3.20 | 1.44 | -2.59 | -7.17 | 0.0385 |
| 2026-06-15 | 7.14 | 5.89 | 7.07 | 5.79 | 7.19 | 5.94 | -9.15 | 0.0409 |
| 2026-06-29 | -4.91 | -4.95 | 5.37 | 4.93 | 4.01 | 3.92 | 3.49 | 0.0438 |
| 2026-07-13 | -1.90 | -2.08 | -1.44 | -1.58 | -1.47 | -1.63 | 2.25 | 0.0457 |


BTC (info): gemiddeld -5.30 %/mnd over dezelfde perioden.


Vol-factor (K1/K3): gemiddeld 0.89, mediaan 0.80, aandeel op 0,5 11 %, op 2 4 %, zonder sigma (factor 1) 12 %.
