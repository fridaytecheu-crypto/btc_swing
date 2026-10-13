# BTC V11 — FAST MULTI-FACTOR INTRADAY: pre-registered historical experiment

Generated 2026-10-13T08:40:14.151170+00:00 · V11 11.0.0-fast-multifactor · config `74472b51dab0` (strategy `a324dbcb0394`) · code `eabd2a5475ce-dirty` · Bybit 1m dataset `92d27161c194` · windows TRAIN 2022-01-01 -> 2024-07-01, VALIDATION 2024-07-01 -> 2025-07-01 · locked holdout: not read (every load capped at 2025-07-01).

ONE frozen specification (docs/BTC_V11_FAST_MULTIFACTOR_DESIGN.md), run once on each window; nothing selected, nothing tuned. Historically testable groups: trend, momentum, volatility, volume, location, derivatives (Binance OI/funding proxy). FORWARD-ONLY (not in this run, never backfilled): microstructure (order book, live taker windows, allLiquidation); the score is renormalised over the available groups (available weight 85 of 100). Costs: taker 5.5 bps/side, half-spread 0.5 bps/side, slippage 1 bps (3 bps on stops), Binance funding at 8-h marks; NET is the headline.

## 0. Classification: **C** — no historical net edge: validation net R <= 0 or PF <= 1 after primary costs

## 1. Headline (NET after primary costs)

| window | n | trades/day | win rate | gross R | cost R | NET R | PF | total net R | max DD | hold p50/p75/p90 (min) |
|---|---|---|---|---|---|---|---|---|---|---|
| TRAIN | 2445 | 2.68 | 40.2% | -0.016 | 0.366 | -0.381 | 0.22 | -932.4 | 139.86% | 20 / 42 / 60 |
| VALIDATION | 974 | 2.67 | 43.0% | -0.051 | 0.364 | -0.415 | 0.19 | -404.2 | 60.81% | 20 / 46 / 60 |
| TRAIN x1.5 costs | 2445 | 2.68 | 17.5% | -0.016 | 0.549 | -0.564 | 0.10 | -1379.6 | 206.95% | 20 / 42 / 60 |
| VALIDATION x1.5 costs | 974 | 2.67 | 16.7% | -0.051 | 0.545 | -0.597 | 0.08 | -581.3 | 87.30% | 20 / 46 / 60 |

## 2. Required metrics

| metric | TRAIN | VALIDATION |
|---|---|---|
| total trades | 2445 | 974 |
| trades / calendar day | 2.68 | 2.67 |
| trades / active day | 2.97 | 3.04 |
| median trades / day | 2.0 | 3.0 |
| zero-trade days | 88 | 45 |
| zero-trade day share | 0.10 | 0.12 |
| LONG | 1247 | 493 |
| SHORT | 1198 | 481 |
| win rate | 0.402 | 0.430 |
| gross R / trade | -0.016 | -0.051 |
| net R / trade | -0.381 | -0.415 |
| cost R / trade | 0.366 | 0.364 |
| avg winner R | 0.271 | 0.232 |
| avg loser R | -0.820 | -0.903 |
| payoff ratio | 0.33 | 0.26 |
| profit factor | 0.22 | 0.19 |
| total net R | -932.4 | -404.2 |
| total net USDT | -6993.0 | -3031.4 |
| max DD USDT | 6993.0 | 3040.5 |
| max DD % of 5000 | 139.86 | 60.81 |
| max DD R | 932.4 | 405.4 |
| median hold min | 20 | 20 |
| p75 hold | 42 | 46 |
| p90 hold | 60 | 60 |
| mean hold | 28 | 28 |
| cost USDT / trade | 2.744 | 2.727 |
| gross USDT / trade | -0.117 | -0.386 |
| net USDT / trade | -2.860 | -3.112 |
| cost % of gross edge | n/a | n/a |
| mean leverage | 1.88 | 1.85 |
| mean margin USDT | 1019 | 1020 |
| median stop % | 0.35 | 0.35 |
| MFE R mean | 0.67 | 0.62 |
| MAE R mean | -0.48 | -0.53 |
| skipped while open | 1513 | 534 |
| family A / B / C | {'A': 1405, 'B': 995, 'C': 45} | {'A': 515, 'B': 438, 'C': 21} |
| exits | {'BE': 609, 'STALL': 783, 'STOP': 367, 'TIME': 450, 'TRAIL': 236} | {'BE': 278, 'STALL': 269, 'STOP': 172, 'TIME': 181, 'TRAIL': 74} |
| funnel counters | {'candidates': 140652, 'armed': 8437, 'triggers': 16680, 'executed': 2445, 'blocked': 1852, 'skipped_while_open': 1513} | {'candidates': 52380, 'armed': 3221, 'triggers': 6759, 'executed': 974, 'blocked': 624, 'skipped_while_open': 534} |
| blockers | {'POSITION_OPEN': 1513, 'STOP_TOO_WIDE_FOR_V11': 408, 'NO_VALID_STRUCTURAL_STOP': 2} | {'POSITION_OPEN': 534, 'STOP_TOO_WIDE_FOR_V11': 117} |
| family funnel (armed/triggers/executed) | {'A': {'armed': 4330, 'triggers': 13739, 'executed': 1405}, 'B': {'armed': 4013, 'triggers': 2360, 'executed': 995}, 'C': {'armed': 94, 'triggers': 581, 'executed': 45}} | {'A': {'armed': 1581, 'triggers': 5323, 'executed': 515}, 'B': {'armed': 1591, 'triggers': 1117, 'executed': 438}, 'C': {'armed': 49, 'triggers': 319, 'executed': 21}} |
| score dist long p50/p90/p99 | [52.4, 70.6, 83.5] | [52.1, 69.7, 83.2] |
| share of minutes with a side >= candidate / entry | 0.390 / 0.114 | 0.354 / 0.095 |

## 3. Breakdowns — TRAIN

**Setup family**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| A | 1405 | -0.378 | 34.9% | 0.21 | -531.0 | 20 |
| B | 995 | -0.387 | 47.5% | 0.24 | -384.7 | 20 |
| C | 45 | -0.370 | 44.4% | 0.23 | -16.7 | 20 |

**Side**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| LONG | 1247 | -0.371 | 39.3% | 0.23 | -462.4 | 20 |
| SHORT | 1198 | -0.392 | 41.2% | 0.21 | -470.1 | 20 |

**Family x side**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| A-LONG | 717 | -0.386 | 33.3% | 0.19 | -276.9 | 20 |
| A-SHORT | 688 | -0.369 | 36.5% | 0.23 | -254.1 | 20 |
| B-LONG | 506 | -0.347 | 47.6% | 0.28 | -175.6 | 20 |
| B-SHORT | 489 | -0.428 | 47.4% | 0.19 | -209.1 | 20 |
| C-LONG | 24 | -0.409 | 41.7% | 0.22 | -9.8 | 20 |
| C-SHORT | 21 | -0.326 | 47.6% | 0.26 | -6.8 | 20 |

**Trend regime (struct15 at decision)**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| struct_up | 982 | -0.393 | 36.9% | 0.20 | -386.2 | 20 |
| struct_down | 978 | -0.361 | 40.6% | 0.24 | -353.0 | 20 |
| struct_neutral | 485 | -0.398 | 46.2% | 0.23 | -193.2 | 20 |

**Volatility regime (ATR5 tercile at decision)**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| low_vol | 875 | -0.391 | 36.6% | 0.18 | -342.5 | 20 |
| mid_vol | 996 | -0.403 | 39.2% | 0.20 | -401.1 | 20 |
| high_vol | 574 | -0.329 | 47.6% | 0.32 | -188.8 | 20 |

**Hour of day (UTC)**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| 00-07 | 1232 | -0.379 | 39.6% | 0.21 | -466.6 | 20 |
| 08-15 | 754 | -0.387 | 40.8% | 0.22 | -292.2 | 20 |
| 16-23 | 459 | -0.378 | 40.7% | 0.25 | -173.7 | 20 |

**Exit reason**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| BE | 609 | 0.092 | 99.8% | 1807.80 | 55.9 | 14 |
| STALL | 783 | -0.652 | 0.0% | 0.00 | -510.5 | 20 |
| STOP | 367 | -1.425 | 0.0% | 0.00 | -522.9 | 15 |
| TIME | 450 | -0.170 | 30.9% | 0.54 | -76.6 | 60 |
| TRAIL | 236 | 0.516 | 100.0% | inf | 121.7 | 30 |

## 3. Breakdowns — VALIDATION

**Setup family**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| A | 515 | -0.378 | 38.8% | 0.20 | -194.6 | 20 |
| B | 438 | -0.451 | 48.2% | 0.19 | -197.6 | 20 |
| C | 21 | -0.570 | 38.1% | 0.11 | -12.0 | 20 |

**Side**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| LONG | 493 | -0.380 | 42.6% | 0.21 | -187.6 | 20 |
| SHORT | 481 | -0.450 | 43.5% | 0.18 | -216.6 | 20 |

**Family x side**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| A-LONG | 281 | -0.363 | 38.4% | 0.21 | -102.0 | 23 |
| A-SHORT | 234 | -0.396 | 39.3% | 0.20 | -92.6 | 20 |
| B-LONG | 203 | -0.402 | 48.3% | 0.22 | -81.6 | 20 |
| B-SHORT | 235 | -0.494 | 48.1% | 0.17 | -116.0 | 17 |
| C-LONG | 9 | -0.437 | 44.4% | 0.20 | -3.9 | 27 |
| C-SHORT | 12 | -0.670 | 33.3% | 0.05 | -8.0 | 20 |

**Trend regime (struct15 at decision)**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| struct_up | 406 | -0.364 | 42.6% | 0.22 | -147.8 | 20 |
| struct_down | 391 | -0.387 | 44.8% | 0.21 | -151.4 | 20 |
| struct_neutral | 177 | -0.594 | 40.1% | 0.12 | -105.1 | 20 |

**Volatility regime (ATR5 tercile at decision)**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| low_vol | 291 | -0.394 | 39.9% | 0.17 | -114.5 | 20 |
| mid_vol | 419 | -0.416 | 42.7% | 0.20 | -174.5 | 20 |
| high_vol | 264 | -0.436 | 47.0% | 0.20 | -115.2 | 20 |

**Hour of day (UTC)**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| 00-07 | 483 | -0.389 | 45.3% | 0.20 | -187.9 | 20 |
| 08-15 | 303 | -0.468 | 38.6% | 0.16 | -141.7 | 20 |
| 16-23 | 188 | -0.396 | 44.1% | 0.22 | -74.5 | 20 |

**Exit reason**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| BE | 278 | 0.094 | 100.0% | inf | 26.2 | 16 |
| STALL | 269 | -0.721 | 0.0% | 0.00 | -194.1 | 20 |
| STOP | 172 | -1.429 | 0.0% | 0.00 | -245.8 | 13 |
| TIME | 181 | -0.158 | 37.0% | 0.53 | -28.5 | 60 |
| TRAIL | 74 | 0.513 | 100.0% | inf | 38.0 | 35 |

## 4. The six questions

1. **Is 3-8/day reachable?** TRAIN 2.68/day (2.97 per active day, 9.6% zero days), VALIDATION 2.67/day; skipped while a position was open: 1513 / 534. No under the frozen rules.
2. **Gross edge or turnover?** gross R per trade TRAIN -0.016, VALIDATION -0.051; costs 0.366 R per trade (n/a% of gross on TRAIN).
3. **Does each setup contribute?** TRAIN A: -0.378 R (n 1405), B: -0.387 R (n 995), C: -0.370 R (n 45); VALIDATION A: -0.378 R (n 515), B: -0.451 R (n 438), C: -0.570 R (n 21).
4. **Winners vs losers and costs?** avg winner 0.27 R vs avg loser -0.82 R (payoff 0.33), win rate 40.2% on TRAIN; VALIDATION payoff 0.26, win rate 43.0%.
5. **Positive in validation?** VALIDATION net R -0.415, PF 0.19, total -404.2 R; TRAIN net R -0.381, PF 0.22. No.
6. **Hold time 10-60 min?** median 20 / p75 42 / p90 60 min on TRAIN, 20 / 46 / 60 on VALIDATION (hard cap 60). Yes.

No threshold, length, weight, stop or target was changed after this result (design section 11). The locked holdout was not read.
