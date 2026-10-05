# BTC Swing V1 — Phase 2.4: SHORT REGIME ELIGIBILITY (CONTROL vs NO_NEW_SHORT_IN_TREND_DOWN)

Generated 2026-10-05 12:37 UTC · period 2022-01-01 00:00 -> 2025-01-01 00:00 UTC · CONTROL result hash `92ebe5d7fc65` · variant result hash `870c556507e7` · code `1ce2bd88f0c6`

**Central question.** Does preventing new SHORT entries once BTC is already in a TREND_DOWN regime remove structurally late short entries and produce a more stable out-of-sample swing strategy?

Paper/backtest only. No live trading, no authenticated exchange access, no real money. 2025+ data untouched and not inspected.

## 1. Hypothesis

Phase 2 (frozen defaults) showed LONG expectancy positive in both chronological segments, SHORT expectancy deteriorating materially in 2024 (+0.10R -> -0.26R), trades entered in the TREND_DOWN regime at -0.23R, BREAKDOWN_SHORT (which originates from RANGE / BREAKOUT_REGIME / LOW_VOLATILITY) the only SHORT family positive in both segments, and TREND_PULLBACK_SHORT negative in both. Pre-registered hypothesis H4: opening a new SHORT after the market is already classified TREND_DOWN is systematically too late; preventing new SHORT entries in TREND_DOWN (uniformly, all SHORT families, entry eligibility only) improves combined and 2024 net expectancy, SHORT expectancy and profit factor without materially worsening drawdown, through a coherent removed population rather than a few outliers, and without damaging LONG performance.

## 2. Baseline verification

| check | value |
|---|---|
| Phase 2 validation result hash | `92ebe5d7fc65fc978ba4d3d222723e30c31d1db26e74d6e6585c786528c4ea56` |
| CONTROL result hash (this run) | `92ebe5d7fc65fc978ba4d3d222723e30c31d1db26e74d6e6585c786528c4ea56` |
| identical | yes |
| row-level check vs persisted Phase 2 trades | identical: 275 trades, 89 columns compared |
| CONTROL trades / net expectancy | 275 / 0.081R |
| config hash CONTROL / variant | `5bfc1a7a7f3c` / `ecb9d8a9060a` (differ only in `experiment.block_short_in_trend_down`) |

## 3. Exact variant

- `experiment.block_short_in_trend_down: true`. At the decision bar where a SHORT episode's entry trigger fires, the engine reads the PIT regime of that bar (completed 1d/4h bars only). If it is TREND_DOWN the trade is not opened and the episode ends `REGIME_BLOCKED:TREND_DOWN` (cooldown as for an invalidation). Otherwise nothing differs from CONTROL.
- Uniform across TREND_PULLBACK_SHORT, RESISTANCE_REJECTION_SHORT, MOMENTUM_CONTINUATION_SHORT and BREAKDOWN_SHORT; family definitions and eligibility tables unchanged; LONG logic untouched; open positions never closed by a regime change; detection, confirmation, entry geometry, stops, TP1/TP2, breakeven, trailing, sizing, leverage, costs and max hold frozen; all other experiment switches at their CONTROL defaults.
- Because TREND_PULLBACK_SHORT is only eligible in TREND_DOWN, the rule removes essentially all of its entries; that is a consequence of the uniform regime rule, not a family-selection choice, and the family was not modified.

## 4. PIT audit

- bar visible iff close_time <= t; the regime used by the rule is the PIT regime at the trigger decision bar (completed 1d/4h bars only); fill at the next 5m open. Scope: entry eligibility only; open positions are never closed by a regime change.
- Deterministic rerun: CONTROL yes, variant yes.
- Truncation audit (variant): decisions up to 2023-07-03T00:00 identical with later data removed: yes (157825 rows).

## 5. Overall results (combined 2022-01 -> 2024-12)

| metric | CONTROL | NO_NEW_SHORT_IN_TREND_DOWN |
|---|---|---|
| trades | 275 | 206 |
| episodes | 533 | 642 |
| setup->entry | 51.6% | 32.1% |
| exp. R (net) | 0.081 | 0.184 |
| exp. R (gross) | 0.226 | 0.335 |
| t | 0.92 | 1.72 |
| PF | 1.14 | 1.32 |
| win rate | 47.3% | 49.0% |
| avg win R | 1.421 | 1.540 |
| avg loss R | -1.121 | -1.119 |
| median R | -1.043 | -0.677 |
| MFE R | 2.074 | 2.301 |
| MAE R | -0.862 | -0.832 |
| max DD | -8.96% | -6.48% |
| Sharpe | 0.66 | 1.29 |
| median hold h | 22.6 | 25.9 |
| fees | -1211 | -914 |
| slippage | -697 | -516 |
| funding | -117 | -145 |
| net P&L | 1133 | 1940 |
| net return | 11.33% | 19.40% |

| metric | CONTROL | NO_NEW_SHORT_IN_TREND_DOWN |
|---|---|---|
| LONG trades / SHORT trades | 158 / 117 | 158 / 48 |
| Sortino (daily marks) | 0.94 | 1.94 |
| t-stat of mean R | 0.92 | 1.72 |
| episodes REGIME_BLOCKED | 0 | 123 |

## 6. dev_2022_2023

| metric | CONTROL | NO_NEW_SHORT_IN_TREND_DOWN |
|---|---|---|
| trades | 168 | 112 |
| episodes | 323 | 419 |
| setup->entry | 52.0% | 26.7% |
| exp. R (net) | 0.128 | 0.288 |
| exp. R (gross) | 0.277 | 0.445 |
| t | 1.12 | 1.91 |
| PF | 1.22 | 1.53 |
| win rate | 48.8% | 51.8% |
| avg win R | 1.451 | 1.613 |
| avg loss R | -1.134 | -1.135 |
| median R | -1.041 | 0.360 |
| MFE R | 2.114 | 2.446 |
| MAE R | -0.895 | -0.864 |
| max DD | -3.56% | -3.70% |
| Sharpe | 0.97 | 1.76 |
| median hold h | 22.4 | 23.8 |
| fees | -799 | -543 |
| slippage | -456 | -301 |
| funding | -25 | -49 |
| net P&L | 1092 | 1645 |
| net return | 10.92% | 16.45% |

Sides in dev_2022_2023:

| side | CONTROL | NO_NEW_SHORT_IN_TREND_DOWN |
|---|---|---|
| LONG | n=85, R=0.159, PF=1.27, win=47% | n=85, R=0.159, PF=1.27, win=47% |
| SHORT | n=83, R=0.096, PF=1.17, win=51% | n=27, R=0.695, PF=2.73, win=67% |

## 7. val_2024

| metric | CONTROL | NO_NEW_SHORT_IN_TREND_DOWN |
|---|---|---|
| trades | 107 | 94 |
| episodes | 210 | 223 |
| setup->entry | 51.0% | 42.2% |
| exp. R (net) | 0.007 | 0.061 |
| exp. R (gross) | 0.145 | 0.205 |
| t | 0.05 | 0.41 |
| PF | 1.01 | 1.10 |
| win rate | 44.9% | 45.7% |
| avg win R | 1.368 | 1.440 |
| avg loss R | -1.101 | -1.102 |
| median R | -1.054 | -1.060 |
| MFE R | 2.010 | 2.129 |
| MAE R | -0.810 | -0.793 |
| max DD | -9.89% | -7.49% |
| Sharpe | 0.10 | 0.52 |
| median hold h | 24.9 | 26.5 |
| fees | -412 | -371 |
| slippage | -240 | -215 |
| funding | -92 | -96 |
| net P&L | 42 | 295 |
| net return | 0.42% | 2.95% |

Sides in val_2024:

| side | CONTROL | NO_NEW_SHORT_IN_TREND_DOWN |
|---|---|---|
| LONG | n=73, R=0.132, PF=1.23, win=47% | n=73, R=0.132, PF=1.23, win=47% |
| SHORT | n=34, R=-0.261, PF=0.60, win=41% | n=21, R=-0.186, PF=0.71, win=43% |

## 8. LONG vs SHORT (combined)

| side | CONTROL | NO_NEW_SHORT_IN_TREND_DOWN |
|---|---|---|
| LONG | n=158, R=0.146, PF=1.25, win=47% | n=158, R=0.146, PF=1.25, win=47% |
| SHORT | n=117, R=-0.008, PF=0.98, win=48% | n=48, R=0.310, PF=1.60, win=56% |

## 9. Removed trades (CONTROL SHORT trades whose episode ends REGIME_BLOCKED in the variant)

- CONTROL SHORT trades: 117; removed by the rule: 60; kept (traded in both arms): 48; lost to sequencing or other causes: 9 (UNMATCHED=9).
- Regime at the trigger bar of the removed trades: TREND_DOWN=60.

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| removed SHORT trades | 60 | 43% | -0.185 | -1.054 | -11.1 | 0.71 | 1.46 | -0.89 | 31.3 | 57% | -568 |
| kept SHORT trades (traded in both arms) | 48 | 56% | 0.310 | 0.467 | 14.9 | 1.60 | 2.76 | -0.78 | 33.2 | 31% | 752 |

By family:

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| RESISTANCE_REJECTION_SHORT | 23 | 48% | -0.047 | -1.043 | -1.1 | 0.92 | 1.63 | -0.82 | 32.8 | 39% | -54 |
| TREND_PULLBACK_SHORT | 37 | 41% | -0.270 | -1.056 | -10.0 | 0.60 | 1.35 | -0.93 | 30.3 | 68% | -513 |

By segment:

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| dev_2022_2023 | 49 | 45% | -0.135 | -1.056 | -6.6 | 0.78 | 1.53 | -0.88 | 31.8 | 61% | -341 |
| val_2024 | 11 | 36% | -0.405 | -1.043 | -4.5 | 0.42 | 1.11 | -0.93 | 28.8 | 36% | -227 |

By regime at detection:

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TREND_DOWN | 60 | 43% | -0.185 | -1.054 | -11.1 | 0.71 | 1.46 | -0.89 | 31.3 | 57% | -568 |

By family and segment:

| family | segment | n | mean R | sum R | sum P&L |
|---|---|---|---|---|---|
| RESISTANCE_REJECTION_SHORT | dev_2022_2023 | 17 | 0.072 | 1.2 | 64 |
| RESISTANCE_REJECTION_SHORT | val_2024 | 6 | -0.386 | -2.3 | -118 |
| TREND_PULLBACK_SHORT | dev_2022_2023 | 32 | -0.245 | -7.9 | -405 |
| TREND_PULLBACK_SHORT | val_2024 | 5 | -0.429 | -2.1 | -109 |

Every removed trade (60; also in `removed_short_trades.parquet`):

| family | entry (UTC) | segment | regime det./trigger | R | P&L | MFE R | MAE R | hold h | exit | target first |
|---|---|---|---|---|---|---|---|---|---|---|
| TREND PULLBACK SHORT | 2022-01-02 08:35 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.08 | -54 | 0.14 | -1.00 | 8.5 | STOP | no |
| TREND PULLBACK SHORT | 2022-01-05 05:20 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 1.69 | 85 | 2.67 | -0.54 | 38.5 | TRAIL | yes |
| RESISTANCE REJECTION SHORT | 2022-01-12 01:15 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.08 | -55 | 0.26 | -1.18 | 10.5 | STOP | no |
| TREND PULLBACK SHORT | 2022-01-15 12:10 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.10 | -55 | 0.58 | -1.11 | 7.9 | STOP | yes |
| TREND PULLBACK SHORT | 2022-01-19 15:50 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.05 | -53 | 0.29 | -1.09 | 23.1 | STOP | no |
| TREND PULLBACK SHORT | 2022-02-21 18:05 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 1.33 | 68 | 2.47 | -0.67 | 70.0 | TRAIL | yes |
| TREND PULLBACK SHORT | 2022-04-17 07:35 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 1.57 | 80 | 3.42 | -0.58 | 33.7 | TRAIL | yes |
| RESISTANCE REJECTION SHORT | 2022-04-19 22:50 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.09 | -55 | 0.22 | -1.02 | 11.7 | STOP | no |
| RESISTANCE REJECTION SHORT | 2022-04-20 14:25 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.08 | -55 | 0.98 | -1.24 | 19.9 | STOP | no |
| TREND PULLBACK SHORT | 2022-04-24 20:20 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 0.50 | 25 | 2.47 | -0.44 | 20.4 | TRAIL | yes |
| RESISTANCE REJECTION SHORT | 2022-04-26 14:20 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 0.66 | 33 | 2.96 | -0.20 | 23.2 | TRAIL | yes |
| TREND PULLBACK SHORT | 2022-04-27 18:40 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.08 | -55 | 0.40 | -1.01 | 6.5 | STOP | no |
| TREND PULLBACK SHORT | 2022-04-29 08:35 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 1.55 | 79 | 2.91 | -0.11 | 54.8 | TRAIL | yes |
| TREND PULLBACK SHORT | 2022-05-02 23:50 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.04 | -53 | 1.13 | -1.08 | 40.6 | STOP | yes |
| RESISTANCE REJECTION SHORT | 2022-05-04 17:35 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.08 | -55 | 0.42 | -1.09 | 1.3 | STOP | no |
| TREND PULLBACK SHORT | 2022-08-22 05:35 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.07 | -54 | 1.09 | -1.07 | 58.6 | STOP | yes |
| RESISTANCE REJECTION SHORT | 2022-08-24 21:35 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 0.53 | 27 | 1.81 | -0.44 | 39.0 | TRAIL | yes |
| TREND PULLBACK SHORT | 2022-08-29 08:35 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.07 | -54 | 0.10 | -1.06 | 5.6 | STOP | no |
| RESISTANCE REJECTION SHORT | 2022-08-29 20:20 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.08 | -54 | 0.08 | -1.08 | 8.3 | STOP | no |
| TREND PULLBACK SHORT | 2022-08-31 22:20 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 2.37 | 120 | 4.02 | -0.79 | 159.4 | TRAIL | yes |
| TREND PULLBACK SHORT | 2022-09-08 17:30 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.10 | -56 | 0.18 | -2.11 | 10.0 | STOP | no |
| RESISTANCE REJECTION SHORT | 2022-09-17 19:45 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 1.94 | 99 | 6.90 | -0.58 | 48.0 | TRAIL | yes |
| TREND PULLBACK SHORT | 2022-09-20 08:10 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.06 | -53 | 1.26 | -1.44 | 33.8 | STOP | yes |
| TREND PULLBACK SHORT | 2022-09-25 18:20 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.09 | -55 | 1.10 | -1.04 | 14.6 | STOP | yes |
| RESISTANCE REJECTION SHORT | 2022-09-26 16:25 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.08 | -54 | 0.18 | -1.18 | 8.8 | STOP | yes |
| TREND PULLBACK SHORT | 2022-10-10 00:30 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 0.47 | 24 | 1.59 | -0.62 | 12.1 | TRAIL | yes |
| TREND PULLBACK SHORT | 2022-10-11 13:55 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 0.57 | 29 | 2.84 | -0.40 | 50.8 | TRAIL | yes |
| TREND PULLBACK SHORT | 2022-10-16 17:15 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.33 | -70 | 0.22 | -2.55 | 2.3 | STOP | no |
| RESISTANCE REJECTION SHORT | 2022-10-22 16:40 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.23 | -64 | 0.98 | -1.33 | 25.2 | STOP | no |
| TREND PULLBACK SHORT | 2022-11-25 13:35 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.13 | -57 | 0.27 | -1.15 | 11.8 | STOP | no |
| RESISTANCE REJECTION SHORT | 2022-12-19 01:45 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 1.56 | 80 | 4.42 | -0.85 | 25.0 | TRAIL | yes |
| RESISTANCE REJECTION SHORT | 2022-12-20 17:20 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 0.79 | 40 | 1.38 | -0.58 | 240.0 | TIME_LIMIT | no |
| TREND PULLBACK SHORT | 2023-01-03 22:45 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.17 | -60 | 0.01 | -1.44 | 4.4 | STOP | no |
| RESISTANCE REJECTION SHORT | 2023-05-21 05:15 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 0.86 | 44 | 2.59 | -0.04 | 32.3 | TRAIL | yes |
| RESISTANCE REJECTION SHORT | 2023-05-23 08:10 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 2.21 | 112 | 4.07 | -0.37 | 52.0 | TRAIL | yes |
| TREND PULLBACK SHORT | 2023-05-26 09:50 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.22 | -63 | 0.45 | -1.18 | 4.3 | STOP | no |
| TREND PULLBACK SHORT | 2023-06-08 08:35 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 1.00 | 50 | 1.60 | -0.59 | 80.2 | TRAIL | yes |
| TREND PULLBACK SHORT | 2023-06-14 05:35 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.30 | -68 | 0.72 | -1.03 | 7.3 | STOP | yes |
| TREND PULLBACK SHORT | 2023-08-22 01:00 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 1.33 | 68 | 4.69 | -0.18 | 23.1 | TRAIL | yes |
| TREND PULLBACK SHORT | 2023-08-25 14:10 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.16 | -59 | -0.00 | -1.19 | 0.2 | STOP | no |
| TREND PULLBACK SHORT | 2023-08-26 16:35 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.26 | -65 | 0.40 | -1.02 | 21.8 | STOP | yes |
| TREND PULLBACK SHORT | 2023-08-27 23:35 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 0.36 | 19 | 1.54 | -0.21 | 12.6 | TRAIL | yes |
| TREND PULLBACK SHORT | 2023-09-03 22:55 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 0.68 | 35 | 1.74 | -0.72 | 40.4 | TRAIL | yes |
| TREND PULLBACK SHORT | 2023-09-06 04:45 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 0.43 | 22 | 2.12 | -0.36 | 13.2 | TRAIL | yes |
| RESISTANCE REJECTION SHORT | 2023-09-08 10:10 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 0.52 | 27 | 1.97 | -0.73 | 89.2 | TRAIL | yes |
| RESISTANCE REJECTION SHORT | 2023-09-12 18:45 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.14 | -58 | 0.02 | -1.17 | 0.6 | STOP | no |
| RESISTANCE REJECTION SHORT | 2023-09-24 19:15 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | 1.02 | 52 | 2.11 | -0.05 | 20.5 | TRAIL | yes |
| TREND PULLBACK SHORT | 2023-09-26 06:15 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.18 | -61 | 1.13 | -1.07 | 29.0 | STOP | yes |
| TREND PULLBACK SHORT | 2023-09-27 19:20 | dev_2022_2023 | TREND_DOWN/TREND_DOWN | -1.22 | -63 | 0.25 | -1.00 | 5.0 | STOP | no |
| TREND PULLBACK SHORT | 2024-06-26 16:45 | val_2024 | TREND_DOWN/TREND_DOWN | -1.02 | -51 | 1.11 | -1.24 | 101.8 | STOP | yes |
| TREND PULLBACK SHORT | 2024-07-07 11:20 | val_2024 | TREND_DOWN/TREND_DOWN | 0.55 | 28 | 2.43 | -0.30 | 21.5 | TRAIL | yes |
| RESISTANCE REJECTION SHORT | 2024-07-10 13:40 | val_2024 | TREND_DOWN/TREND_DOWN | -1.04 | -52 | 0.44 | -1.15 | 22.9 | STOP | no |
| TREND PULLBACK SHORT | 2024-07-13 04:25 | val_2024 | TREND_DOWN/TREND_DOWN | -1.09 | -55 | 0.01 | -1.23 | 5.6 | STOP | no |
| RESISTANCE REJECTION SHORT | 2024-07-13 17:30 | val_2024 | TREND_DOWN/TREND_DOWN | -1.17 | -60 | 0.59 | -1.33 | 5.4 | STOP | no |
| RESISTANCE REJECTION SHORT | 2024-08-14 01:50 | val_2024 | TREND_DOWN/TREND_DOWN | -1.10 | -56 | 0.11 | -1.30 | 10.7 | STOP | no |
| RESISTANCE REJECTION SHORT | 2024-08-18 05:55 | val_2024 | TREND_DOWN/TREND_DOWN | 0.85 | 43 | 1.73 | -0.81 | 31.4 | TRAIL | no |
| TREND PULLBACK SHORT | 2024-08-30 21:20 | val_2024 | TREND_DOWN/TREND_DOWN | 0.54 | 27 | 1.51 | -0.38 | 71.6 | TRAIL | yes |
| RESISTANCE REJECTION SHORT | 2024-09-03 13:50 | val_2024 | TREND_DOWN/TREND_DOWN | 1.29 | 65 | 2.67 | -0.03 | 24.9 | TRAIL | no |
| TREND PULLBACK SHORT | 2024-09-08 06:20 | val_2024 | TREND_DOWN/TREND_DOWN | -1.13 | -58 | 1.09 | -1.37 | 16.1 | STOP | yes |
| RESISTANCE REJECTION SHORT | 2024-09-09 04:40 | val_2024 | TREND_DOWN/TREND_DOWN | -1.14 | -58 | 0.49 | -1.11 | 4.5 | STOP | no |

## 10. Winners sacrificed vs losses avoided

| metric | value |
|---|---|
| winners removed / losers removed | 26 / 34 |
| positive R removed | 27.2 |
| negative R avoided | 38.3 |
| net R effect of removal (avoided - removed) | 11.1 |
| winners P&L removed / losers P&L avoided (USDT) | 1380 / 1948 |
| net P&L effect of removal (USDT) | 568 |
| median removed R | -1.054 |
| removed R quantiles q10/q25/q50/q75/q90 | -1.18/-1.10/-1.05/0.71/1.55 |
| share of avoided negative R from the worst 3 trades | 10% |
| share of removed positive R from the best 3 trades | 24% |
| mean removed R excluding the worst 3 | -0.126 |

## 11. Remaining SHORT population

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| CONTROL all SHORT | 117 | 48% | -0.008 | -1.043 | -0.9 | 0.98 | 1.95 | -0.88 | 31.5 | 44% | -54 |
| NO_NEW_SHORT_IN_TREND_DOWN remaining SHORT | 48 | 56% | 0.310 | 0.467 | 14.9 | 1.60 | 2.76 | -0.78 | 33.2 | 31% | 752 |

- Variant max-drawdown window (48 trades, -792 USDT): SHORT contribution -152 USDT, LONG contribution -640 USDT.

Remaining SHORT by regime at trigger:

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| RANGE | 48 | 56% | 0.310 | 0.467 | 14.9 | 1.60 | 2.76 | -0.78 | 33.2 | 31% | 752 |

## 12. Setup-family breakdown

| family | CONTROL | ZONE_ENTRY |
|---|---|---|
| BREAKDOWN_SHORT | n=20, R=0.768, PF=2.96, win=65%, MFE=4.58, MAE=-0.57, acct=7.49% | n=20, R=0.768, PF=2.96, win=65%, MFE=4.58, MAE=-0.57, acct=7.24% |
| BREAKOUT_LONG | n=20, R=0.261, PF=1.44, win=50%, MFE=3.15, MAE=-0.82, acct=2.75% | n=20, R=0.261, PF=1.44, win=50%, MFE=3.15, MAE=-0.82, acct=2.69% |
| MOMENTUM_CONTINUATION_LONG | n=32, R=-0.053, PF=0.91, win=44%, MFE=1.33, MAE=-0.88, acct=-0.96% | n=32, R=-0.053, PF=0.91, win=44%, MFE=1.33, MAE=-0.88, acct=-1.04% |
| RESISTANCE_REJECTION_SHORT | n=55, R=-0.071, PF=0.88, win=47%, MFE=1.49, MAE=-0.95, acct=-1.72% | n=28, R=-0.018, PF=0.96, win=50%, MFE=1.45, MAE=-0.94, acct=0.09% |
| SUPPORT_RECLAIM_LONG | n=53, R=0.012, PF=1.02, win=43%, MFE=1.84, MAE=-0.87, acct=0.48% | n=53, R=0.012, PF=1.02, win=43%, MFE=1.84, MAE=-0.87, acct=0.51% |
| TREND_PULLBACK_LONG | n=53, R=0.358, PF=1.65, win=51%, MFE=2.62, MAE=-0.81, acct=9.26% | n=53, R=0.358, PF=1.65, win=51%, MFE=2.62, MAE=-0.81, acct=8.74% |
| TREND_PULLBACK_SHORT | n=42, R=-0.295, PF=0.56, win=40%, MFE=1.31, MAE=-0.94, acct=-5.91% | n=0 |

SHORT families, CONTROL vs variant:

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BREAKDOWN_SHORT | 20 | 65% | 0.768 | 0.467 | 15.4 | 2.96 | 4.58 | -0.57 | 24.7 | 25% | 789 |
| RESISTANCE_REJECTION_SHORT | 55 | 47% | -0.071 | -1.054 | -3.9 | 0.88 | 1.49 | -0.95 | 35.7 | 38% | -210 |
| TREND_PULLBACK_SHORT | 42 | 40% | -0.295 | -1.054 | -12.4 | 0.56 | 1.31 | -0.94 | 29.2 | 60% | -633 |

(variant)

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BREAKDOWN_SHORT | 20 | 65% | 0.768 | 0.467 | 15.4 | 2.96 | 4.58 | -0.57 | 24.7 | 25% | 789 |
| RESISTANCE_REJECTION_SHORT | 28 | 50% | -0.018 | -0.271 | -0.5 | 0.96 | 1.45 | -0.94 | 39.3 | 36% | -37 |

### Families in dev_2022_2023

| family | CONTROL | ZONE_ENTRY |
|---|---|---|
| BREAKDOWN_SHORT | n=9, R=1.651, PF=7.83, win=78%, MFE=5.98, MAE=-0.55, acct=7.26% | n=9, R=1.651, PF=7.83, win=78%, MFE=5.98, MAE=-0.55, acct=7.03% |
| BREAKOUT_LONG | n=11, R=0.783, PF=2.40, win=55%, MFE=4.66, MAE=-0.80, acct=4.31% | n=11, R=0.783, PF=2.40, win=55%, MFE=4.66, MAE=-0.80, acct=4.15% |
| MOMENTUM_CONTINUATION_LONG | n=14, R=-0.401, PF=0.44, win=36%, MFE=1.11, MAE=-0.93, acct=-2.73% | n=14, R=-0.401, PF=0.44, win=36%, MFE=1.11, MAE=-0.93, acct=-2.68% |
| RESISTANCE_REJECTION_SHORT | n=39, R=0.072, PF=1.13, win=54%, MFE=1.68, MAE=-0.97, acct=1.42% | n=18, R=0.216, PF=1.43, win=61%, MFE=1.68, MAE=-0.97, acct=2.03% |
| SUPPORT_RECLAIM_LONG | n=29, R=0.269, PF=1.52, win=52%, MFE=2.07, MAE=-0.86, acct=3.69% | n=29, R=0.269, PF=1.52, win=52%, MFE=2.07, MAE=-0.86, acct=3.53% |
| TREND_PULLBACK_LONG | n=31, R=0.088, PF=1.14, win=45%, MFE=2.04, MAE=-0.88, acct=1.54% | n=31, R=0.088, PF=1.14, win=45%, MFE=2.04, MAE=-0.88, acct=1.49% |
| TREND_PULLBACK_SHORT | n=35, R=-0.277, PF=0.59, win=40%, MFE=1.31, MAE=-0.94, acct=-4.69% | n=0 |

### Families in val_2024

| family | CONTROL | ZONE_ENTRY |
|---|---|---|
| BREAKDOWN_SHORT | n=11, R=0.045, PF=1.09, win=55%, MFE=3.44, MAE=-0.58, acct=0.23% | n=11, R=0.045, PF=1.09, win=55%, MFE=3.44, MAE=-0.58, acct=0.22% |
| BREAKOUT_LONG | n=9, R=-0.377, PF=0.41, win=44%, MFE=1.29, MAE=-0.85, acct=-1.55% | n=9, R=-0.377, PF=0.41, win=44%, MFE=1.29, MAE=-0.85, acct=-1.46% |
| MOMENTUM_CONTINUATION_LONG | n=18, R=0.218, PF=1.44, win=50%, MFE=1.50, MAE=-0.84, acct=1.77% | n=18, R=0.218, PF=1.44, win=50%, MFE=1.50, MAE=-0.84, acct=1.64% |
| RESISTANCE_REJECTION_SHORT | n=16, R=-0.419, PF=0.45, win=31%, MFE=1.03, MAE=-0.91, acct=-3.15% | n=10, R=-0.440, PF=0.44, win=30%, MFE=1.04, MAE=-0.88, acct=-1.94% |
| SUPPORT_RECLAIM_LONG | n=24, R=-0.300, PF=0.60, win=33%, MFE=1.56, MAE=-0.87, acct=-3.21% | n=24, R=-0.300, PF=0.60, win=33%, MFE=1.56, MAE=-0.87, acct=-3.01% |
| TREND_PULLBACK_LONG | n=22, R=0.740, PF=2.63, win=59%, MFE=3.44, MAE=-0.71, acct=7.73% | n=22, R=0.740, PF=2.63, win=59%, MFE=3.44, MAE=-0.71, acct=7.25% |
| TREND_PULLBACK_SHORT | n=7, R=-0.382, PF=0.37, win=43%, MFE=1.28, MAE=-0.92, acct=-1.22% | n=0 |

## 13. Single-slot sequencing effects (LONG control check and effect decomposition)

| metric | value |
|---|---|
| LONG trades CONTROL / variant | 158 / 158 |
| LONG trades paired on identical detection: identical R / different R | 158 / 0 |
| LONG trades only in CONTROL (slot taken by a short in the variant, or shifted) | 0 (P&L 0 USDT, mean R n/a) |
| LONG trades only in the variant (freed slots) | 0 (P&L 0 USDT, mean R n/a) |
| LONG expectancy CONTROL / variant | 0.146 / 0.146 |
| LONG profit factor CONTROL / variant | 1.25 / 1.25 |

Decomposition of the net P&L change:

| component | USDT |
|---|---|
| total net P&L change (variant - CONTROL) | 807 |
| direct effect: removed TREND_DOWN SHORT trades (P&L they had in CONTROL, sign reversed) | 568 |
| secondary effect: freed slots / shifted sequence | 239 |
| variant-only trades (new, enabled by freed slots) | n=0, P&L 0;  |
| CONTROL-only trades not blocked by the rule (lost to sequencing) | n=9, P&L -239; SHORT: n=9, -239 USDT, -0.521R |

## 14. MFE / MAE

| metric | CONTROL | NO_NEW_SHORT_IN_TREND_DOWN |
|---|---|---|
| mean MFE (R) / mean MAE (R) | 2.074 / -0.862 | 2.301 / -0.832 |
| worst MAE (R) | -6.768 | -6.768 |
| realised mean winner (R) | 1.421 | 1.540 |
| counterfactual 1R before initial stop | 56.4% | 57.3% |
| counterfactual 1.5R before initial stop | 46.5% | 48.5% |
| counterfactual 2R before initial stop | 33.5% | 35.4% |
| counterfactual 3R before initial stop | 16.4% | 18.9% |

## 15. Costs (USDT, combined)

| component | CONTROL | NO_NEW_SHORT_IN_TREND_DOWN |
|---|---|---|
| trades | 275 | 206 |
| gross P&L before slippage | 3158 | 3515 |
| slippage | -697 | -516 |
| fees | -1211 | -914 |
| funding | -117 | -145 |
| net P&L | 1133 | 1940 |
| gross expectancy (R) / net expectancy (R) | 0.226 / 0.081 | 0.335 / 0.185 |
| cost drag per trade (R) | 0.145 | 0.150 |

Fewer trades is not counted as an improvement by itself; the per-trade expectancy and profit factor above carry the comparison.

## 16. Drawdown / equity (sequential, one position, fixed research equity for sizing)

| metric | CONTROL | NO_NEW_SHORT_IN_TREND_DOWN |
|---|---|---|
| final equity | 11133.31 | 11939.95 |
| total return / CAGR | 11.33% / 3.64% | 19.40% / 6.09% |
| max drawdown (trade curve / daily mtm) | -8.96% / -8.74% | -6.48% / -6.27% |
| Sharpe / Sortino (daily marks) | 0.66 / 0.94 | 1.29 / 1.94 |
| longest losing streak | 5 | 5 |
| trades / week | 1.76 | 1.32 |

Null benchmark (geometry-matched, as in Phase 2):

| series | n | mean R | win rate | strategy - null (R) | z | P(null >= strat) |
|---|---|---|---|---|---|---|
| CONTROL strategy | 275 | 0.081 | 47.3% |  |  |  |
| CONTROL null (time-matched) | 5500 | -0.058 | 41.0% | 0.138 | z=1.99 | 0% |
| CONTROL null (regime-matched) | 5500 | -0.038 | 41.9% | 0.119 | z=1.49 | 10% |
| NO_NEW_SHORT_IN_TREND_DOWN strategy | 206 | 0.184 | 49.0% |  |  |  |
| NO_NEW_SHORT_IN_TREND_DOWN null (time-matched) | 4120 | -0.068 | 40.9% | 0.253 | z=2.10 | 5% |
| NO_NEW_SHORT_IN_TREND_DOWN null (regime-matched) | 4120 | -0.030 | 41.9% | 0.215 | z=2.47 | 5% |

Regime at entry — CONTROL:

| cell | n | win rate | exp. R | exp. acct | PF | median R | mean hold h | reliable |
|---|---|---|---|---|---|---|---|---|
| BREAKOUT_REGIME | 6 | 50.0% | 0.042 | 0.02% | 1.07 | -0.069 | 70.1 | no |
| LOW_VOLATILITY | 3 | 66.7% | 0.980 | 0.50% | 3.17 | 1.149 | 16.0 | no |
| RANGE | 80 | 52.5% | 0.211 | 0.11% | 1.38 | 0.416 | 30.6 | yes |
| TREND_DOWN | 69 | 42.0% | -0.229 | -0.11% | 0.65 | -1.056 | 30.3 | yes |
| TREND_UP | 117 | 46.2% | 0.153 | 0.07% | 1.26 | -1.056 | 38.0 | yes |

Regime at entry — NO_NEW_SHORT_IN_TREND_DOWN:

| cell | n | win rate | exp. R | exp. acct | PF | median R | mean hold h | reliable |
|---|---|---|---|---|---|---|---|---|
| BREAKOUT_REGIME | 6 | 50.0% | 0.042 | 0.01% | 1.07 | -0.069 | 70.1 | no |
| LOW_VOLATILITY | 3 | 66.7% | 0.980 | 0.49% | 3.17 | 1.149 | 16.0 | no |
| RANGE | 80 | 52.5% | 0.211 | 0.11% | 1.38 | 0.416 | 30.6 | yes |
| TREND_UP | 117 | 46.2% | 0.153 | 0.07% | 1.26 | -1.056 | 38.0 | yes |

## 17. Uncertainty

- Standard error of mean R: CONTROL 0.088 (n=275), variant 0.107 (n=206); 95% intervals CONTROL [-0.091, 0.252], variant [-0.025, 0.394]. Both intervals include zero.
- The variant differs from CONTROL by removing 60 trades (mean -0.185R, t = -1.24) plus sequencing effects; the removed population's own t-statistic is the direct evidence that the regime rule targets a negative-expectancy group.
- 2024: CONTROL 0.007R (n=107) vs variant 0.061R (n=94); SHORT trades in 2024 fall to n=21, so the remaining 2024 SHORT estimate is weak.
- The hypothesis was motivated by the Phase 2 regime table on the same window; this run therefore confirms the in-sample observation under a pre-declared rule but is NOT out-of-sample evidence. Only an untouched 2025+ run can be.
- Single instrument, single 3-year window, one regime classifier with frozen thresholds; the regime label itself is a model output.

## 18. Final recommendation

Pre-declared criteria (from the hypothesis; thresholds stated so the reader can disagree):

| criterion | met | evidence |
|---|---|---|
| 1. combined net expectancy improves materially (> +0.05R over CONTROL) | yes | 0.081 -> 0.184 |
| 2. 2024 expectancy improves materially (> +0.05R over CONTROL 2024) | yes | 0.007 -> 0.061 |
| 3. SHORT expectancy improves | yes | -0.008 -> 0.310 |
| 4. profit factor improves | yes | 1.14 -> 1.32 |
| 5. drawdown does not materially worsen (<= 1.25x CONTROL + 1pt) | yes | 8.96% -> 6.48% |
| 6. coherent removed population (n >= 20, >= 55% losers, worst-3 < 35% of avoided R, still negative without the worst 3) | yes | n=60, losers 34, worst-3 share 10%, mean R without worst 3 -0.126 |
| 7. LONG not materially damaged by sequencing (LONG expectancy change >= -0.05R, identical R on paired LONGs) | yes | LONG 0.146 -> 0.146; paired LONGs with different R: 0 |


- **Classification: A — ADOPTABLE FOR UNTOUCHED VALIDATION.** all seven pre-declared criteria are met; the removed population is a coherent negative-expectancy group, the gain survives costs and sequencing, and LONG is unaffected. The only legitimate next step is one owner-approved confirmatory run on the untouched 2025+ window with `block_short_in_trend_down: true` and nothing else changed. A single run, decided in advance, no iteration afterwards.
- Removed population: 60 TREND_DOWN SHORT entries, mean -0.185R (median -1.054R, t = -1.24): 26 winners worth 27.2R sacrificed, 34 losers worth 38.3R avoided, net 11.1R / 568 USDT.
- Decomposition of the net P&L change (807 USDT): direct effect of the removed trades 568 USDT, secondary sequencing effect 239 USDT (0 new trades from freed slots, 9 CONTROL trades lost to shifted sequencing).
- Caveat that applies regardless of the classification: this hypothesis was derived from the Phase 2 regime table on the same 2022-2024 window, so it is a confirmation of an in-sample pattern under a pre-declared rule, not out-of-sample evidence. 2025+ stays untouched until the owner pre-registers the confirmatory run. No live trading. No tuning. The frozen default remains `block_short_in_trend_down: false`.

## Appendix — frozen configuration (CONTROL; the variant differs only in `experiment.block_short_in_trend_down`)

```yaml
backtest:
  fill_rule: next_bar_open
  information_mode: MARKET_AS_OF
  same_bar_stop_and_target: stop_first
  warmup_days: 60
costs:
  apply_funding: true
  default_funding_interval_hours: 8
  entry_slippage_bps: 2.0
  maker_fee_bps: 2.0
  stop_slippage_bps: 5.0
  taker_fee_bps: 5.0
  use_maker_for_targets: false
data:
  base_timeframe: 5m
  ingest_funding: true
  ingest_mark_price: true
  ingest_metrics: true
  ingest_native_timeframes:
  - 1h
  - 4h
  - 1d
  ingest_premium_index: true
  ingest_spot: false
  latency_minutes: 0
  provider: binance_vision
episode:
  anchor_tolerance_atr: 0.25
  cooldown_bars_after_close: 12
  cooldown_bars_after_invalidation: 6
  dedupe_same_anchor: true
  entry_ready_timeout_bars: 24
  max_concurrent_positions: 1
  watch_timeout_bars: 96
exits:
  breakeven_after_tp1: true
  evaluate_r_levels:
  - 1.0
  - 1.5
  - 2.0
  - 3.0
  max_hold_hours: 240
  regime_exit: false
  tp1_frac: 0.4
  tp1_r: 1.5
  tp2_frac: 0.3
  tp2_r: 3.0
  trail_atr_buffer: 0.5
  trail_method: structure_atr
  trail_tf: 1h
experiment:
  block_short_in_trend_down: false
  entry_mode: CONFIRMED_TRIGGER
indicators:
  atr_period: 14
  donchian_period: 20
  ema_fast: 20
  ema_slow: 50
  ema_trend: 200
  min_bars:
    15m: 220
    1d: 60
    1h: 220
    4h: 220
    5m: 220
  swing_k: 3
instrument:
  market: binance_um_perp
  quote: USDT
  spot_symbol: BTCUSDT
  symbol: BTCUSDT
regime:
  breakout_atr_expansion: 1.25
  breakout_lookback_bars: 3
  high_vol_atr_pct: 0.055
  low_vol_atr_pct: 0.018
  range_ema_band_atr: 1.0
  trend_slope_bars: 5
research:
  min_cell_n: 20
  sharpe_periods_per_year: 365
risk:
  allowed_leverage:
  - 1.0
  - 2.0
  - 3.0
  - 5.0
  - 10.0
  compounding: false
  initial_equity: 10000.0
  liquidation_atr_tf: 4h
  maintenance_margin_rate: 0.005
  margin_cap_frac: 0.25
  max_leverage: 10.0
  min_liquidation_distance_atr: 3.0
  min_stop_to_liquidation_ratio: 3.0
  risk_per_trade: 0.005
setups:
  breakout:
    confirm_tf: 15m
    entry_tf: 5m
    level_tf: 4h
    max_extension_atr: 2.0
    max_stop_atr: 3.0
    min_expansion: 1.2
    min_stop_atr: 0.5
    setup_tf: 1h
    stop_atr_buffer: 0.5
    zone_inner_atr: 0.25
    zone_outer_atr: 0.75
  eligible_regimes:
    BREAKDOWN_SHORT:
    - BREAKOUT_REGIME
    - RANGE
    - LOW_VOLATILITY
    BREAKOUT_LONG:
    - BREAKOUT_REGIME
    - RANGE
    - LOW_VOLATILITY
    MOMENTUM_CONTINUATION_LONG:
    - TREND_UP
    - BREAKOUT_REGIME
    MOMENTUM_CONTINUATION_SHORT:
    - TREND_DOWN
    - BREAKOUT_REGIME
    RESISTANCE_REJECTION_SHORT:
    - RANGE
    - TREND_DOWN
    SUPPORT_RECLAIM_LONG:
    - RANGE
    - TREND_UP
    TREND_PULLBACK_LONG:
    - TREND_UP
    TREND_PULLBACK_SHORT:
    - TREND_DOWN
  enabled:
  - TREND_PULLBACK_LONG
  - TREND_PULLBACK_SHORT
  - BREAKOUT_LONG
  - BREAKDOWN_SHORT
  - SUPPORT_RECLAIM_LONG
  - RESISTANCE_REJECTION_SHORT
  - MOMENTUM_CONTINUATION_LONG
  - MOMENTUM_CONTINUATION_SHORT
  momentum_continuation:
    confirm_tf: 15m
    entry_tf: 5m
    flag_max_age_bars: 3
    flag_max_atr: 0.6
    flag_min_atr: 0.2
    impulse_close_location: 0.7
    impulse_min_atr: 1.5
    impulse_tf: 4h
    max_stop_atr: 4.0
    min_stop_atr: 0.5
    setup_tf: 1h
    stop_atr_buffer: 0.5
  support_reclaim:
    confirm_tf: 15m
    entry_tf: 5m
    level_max_age_bars: 90
    level_tf: 4h
    max_stop_atr: 3.0
    min_stop_atr: 0.5
    setup_tf: 1h
    stop_atr_buffer: 0.5
    sweep_min_atr: 0.1
    sweep_window_bars: 6
    zone_inner_atr: 0.25
    zone_outer_atr: 0.75
  trend_pullback:
    confirm_tf: 15m
    entry_tf: 5m
    max_stop_atr: 4.0
    min_pullback_atr: 1.0
    min_stop_atr: 0.75
    setup_tf: 1h
    stop_atr_buffer: 0.5
    structural_target_lookback: 50
    trend_tf: 4h
    zone_lower_ema: ema_slow
    zone_pad_atr: 0.25
    zone_upper_ema: ema_fast
strategy_name: btc_leveraged_swing_v1
```
