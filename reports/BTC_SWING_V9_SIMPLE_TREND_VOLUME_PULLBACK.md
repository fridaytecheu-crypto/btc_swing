# BTC V9 — SIMPLE TREND + VOLUME PULLBACK: research report

Generated 2026-10-12 14:57 UTC · V9 9.0.0-simple-trend-volume-pullback · config `fe9fb2f28b74` · Bybit 1m dataset `92d27161c194` · frame 1322 days, 1903640 valid minutes · locked holdout: not read.

Paper research only. One deterministic BTCUSDT strategy (15m trend, 5m impulse + pullback, 1m breakout entry) with the pre-registered A/B/C/D volume ladder (`docs/BTC_V9_SIMPLE_TREND_VOLUME_PULLBACK_DESIGN.md`). Costs: TAKER entry and TAKER exits, fee 5.5 bps/side, half-spread 0.5 bps/side, slippage 1 bps (entry, target, time) / 3 bps (stop), Binance funding proxy at 8h marks. R = P&L / planned risk (qty x stop distance); primary exit B (+1.5R), time stop 120 min (progress < 0.5R), hard max 240 min, stop = pullback low - 0.1 ATR5m with a 0.50% minimum (closer stops rejected), risk 0.125%, one position.

## 1. The comparison table (ladder at anchors; primary exit B; conservative taker costs)

### TRAIN 2022-01-01 -> 2024-06-30

| variant | n | trades/day | win rate | gross R | cost R | net R | net R 95% CI (day-cluster) | PF | max DD | median hold (min) | LONG n (net R) | SHORT n (net R) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PRICE ONLY | 848 | 0.93 | 40.2% | 0.042 | 0.227 | -0.185 | [-0.26, -0.11] | 0.72 | 19.77% | 34 | 409 (-0.18) | 439 (-0.19) |
| + IMPULSE RVOL | 807 | 0.88 | 40.6% | 0.056 | 0.225 | -0.170 | [-0.25, -0.10] | 0.74 | 17.29% | 34 | 381 (-0.18) | 426 (-0.16) |
| + PULLBACK VOLUME | 593 | 0.65 | 39.6% | 0.043 | 0.227 | -0.185 | [-0.27, -0.09] | 0.72 | 14.32% | 35 | 279 (-0.19) | 314 (-0.18) |
| + TRIGGER VOLUME/FLOW | 539 | 0.59 | 40.3% | 0.035 | 0.215 | -0.180 | [-0.28, -0.08] | 0.72 | 12.71% | 40 | 255 (-0.17) | 284 (-0.19) |

### VALIDATION 1 2024-07-01 -> 2024-12-31

| variant | n | trades/day | win rate | gross R | cost R | net R | net R 95% CI (day-cluster) | PF | max DD | median hold (min) | LONG n (net R) | SHORT n (net R) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PRICE ONLY | 175 | 0.95 | 38.9% | 0.005 | 0.233 | -0.228 | [-0.39, -0.06] | 0.65 | 4.99% | 41 | 84 (-0.31) | 91 (-0.15) |
| + IMPULSE RVOL | 167 | 0.91 | 38.3% | 0.000 | 0.232 | -0.231 | [-0.40, -0.07] | 0.65 | 4.83% | 40 | 79 (-0.30) | 88 (-0.17) |
| + PULLBACK VOLUME | 113 | 0.61 | 41.6% | 0.109 | 0.236 | -0.126 | [-0.36, 0.07] | 0.79 | 1.98% | 41 | 54 (-0.31) | 59 (0.04) |
| + TRIGGER VOLUME/FLOW | 96 | 0.52 | 31.2% | -0.100 | 0.211 | -0.311 | [-0.50, -0.11] | 0.51 | 3.93% | 56 | 49 (-0.50) | 47 (-0.11) |
| FROZEN: FROZEN_B_IMPULSE_RVOL | 167 | 0.91 | 38.3% | 0.000 | 0.232 | -0.231 | [-0.40, -0.07] | 0.65 | 4.83% | 40 | 79 (-0.30) | 88 (-0.17) |
| FROZEN x1.5 costs | 167 | 0.91 | 37.1% | 0.000 | 0.348 | -0.348 | [-0.52, -0.18] | 0.53 | 7.26% | 40 | 79 (-0.42) | 88 (-0.29) |

### RECENT VALIDATION 2025-01-01 -> 2025-06-30

| variant | n | trades/day | win rate | gross R | cost R | net R | net R 95% CI (day-cluster) | PF | max DD | median hold (min) | LONG n (net R) | SHORT n (net R) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PRICE ONLY | 151 | 0.83 | 41.7% | 0.049 | 0.232 | -0.183 | [-0.35, -0.02] | 0.73 | 4.09% | 32 | 60 (-0.10) | 91 (-0.24) |
| + IMPULSE RVOL | 142 | 0.78 | 41.5% | 0.048 | 0.231 | -0.183 | [-0.36, -0.00] | 0.73 | 3.89% | 32 | 56 (-0.04) | 86 (-0.27) |
| + PULLBACK VOLUME | 85 | 0.47 | 41.2% | 0.048 | 0.223 | -0.175 | [-0.39, 0.04] | 0.72 | 2.95% | 42 | 37 (0.04) | 48 (-0.34) |
| + TRIGGER VOLUME/FLOW | 67 | 0.37 | 41.8% | 0.041 | 0.213 | -0.172 | [-0.40, 0.08] | 0.71 | 2.01% | 59 | 26 (-0.02) | 41 (-0.27) |
| FROZEN: FROZEN_B_IMPULSE_RVOL | 142 | 0.78 | 41.5% | 0.048 | 0.231 | -0.183 | [-0.36, -0.00] | 0.73 | 3.89% | 32 | 56 (-0.04) | 86 (-0.27) |
| FROZEN x1.5 costs | 142 | 0.78 | 39.4% | 0.048 | 0.346 | -0.299 | [-0.47, -0.12] | 0.60 | 5.80% | 32 | 56 (-0.16) | 86 (-0.39) |

### Cost decomposition per trade (bps of notional and R)

| variant | gross bps | fee bps | spread+slip bps | cost bps | net bps | gross R | fee R | spread+slip R | funding R | net R | median stop % |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TRAIN PRICE ONLY | 3.0 | 11.0 | 4.0 | 15.0 | -12.0 | 0.042 | 0.166 | 0.060 | -0.0003 | -0.185 | 0.63% |
| TRAIN + IMPULSE RVOL | 3.6 | 11.0 | 4.0 | 15.0 | -11.4 | 0.056 | 0.165 | 0.060 | -0.0002 | -0.170 | 0.64% |
| TRAIN + PULLBACK VOLUME | 3.8 | 11.0 | 4.0 | 15.0 | -11.2 | 0.043 | 0.167 | 0.060 | -0.0004 | -0.185 | 0.63% |
| TRAIN + TRIGGER VOLUME/FLOW | 2.9 | 11.0 | 4.0 | 15.0 | -12.1 | 0.035 | 0.158 | 0.057 | -0.0000 | -0.180 | 0.67% |
| validation_1 FROZEN | 1.5 | 11.0 | 4.0 | 15.0 | -13.5 | 0.000 | 0.170 | 0.062 | 0.0006 | -0.231 | 0.60% |
| recent_validation FROZEN | 2.6 | 11.0 | 4.0 | 15.0 | -12.4 | 0.048 | 0.169 | 0.062 | 0.0001 | -0.183 | 0.64% |

### Cost stress x1.5 (TRAIN ladder)

| variant | n | trades/day | win rate | gross R | cost R | net R | net R 95% CI (day-cluster) | PF | max DD | median hold (min) | LONG n (net R) | SHORT n (net R) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A_PRICE_ONLY x1.5 costs | 848 | 0.93 | 38.9% | 0.042 | 0.341 | -0.298 | [-0.38, -0.22] | 0.59 | 31.71% | 34 | 409 (-0.30) | 439 (-0.30) |
| B_IMPULSE_RVOL x1.5 costs | 807 | 0.88 | 39.4% | 0.056 | 0.338 | -0.282 | [-0.36, -0.21] | 0.61 | 28.57% | 34 | 381 (-0.29) | 426 (-0.27) |
| C_PULLBACK_VOLUME x1.5 costs | 593 | 0.65 | 39.0% | 0.043 | 0.341 | -0.298 | [-0.39, -0.20] | 0.59 | 22.62% | 35 | 279 (-0.31) | 314 (-0.29) |
| D_TRIGGER_FLOW x1.5 costs | 539 | 0.59 | 39.3% | 0.035 | 0.323 | -0.288 | [-0.39, -0.18] | 0.60 | 19.80% | 40 | 255 (-0.28) | 284 (-0.30) |

## 2. Funnel (distinct impulse legs per window; anchors of each ladder step)

### TRAIN

| stage | PRICE ONLY | + IMPULSE RVOL | + PULLBACK VOLUME | + TRIGGER VOLUME/FLOW |
|---|---|---|---|---|
| raw impulse legs (>= 0.5 ATR, any trend) | 87153 (95.56/day) | 87153 (95.56/day) | 87153 (95.56/day) | 87153 (95.56/day) |
| after 15m trend | 36896 (40.46/day) | 36896 (40.46/day) | 36896 (40.46/day) | 36896 (40.46/day) |
| after impulse >= anchor ATR | 36883 (40.44/day) | 36883 (40.44/day) | 36883 (40.44/day) | 36883 (40.44/day) |
| after impulse RVOL | 36883 (40.44/day) | 19540 (21.43/day) | 19540 (21.43/day) | 19540 (21.43/day) |
| after pullback (armed at least once) | 9759 (10.70/day) | 5955 (6.53/day) | 5955 (6.53/day) | 5955 (6.53/day) |
| after pullback-volume rule | 9759 (10.70/day) | 5955 (6.53/day) | 4521 (4.96/day) | 4521 (4.96/day) |
| after 1m trigger (breakout while armed) | 5730 (6.28/day) | 3609 (3.96/day) | 2705 (2.97/day) | 2705 (2.97/day) |
| after flow confirmation | 5730 (6.28/day) | 3609 (3.96/day) | 2705 (2.97/day) | 1973 (2.16/day) |
| skipped: position open | 601 (0.66/day) | 561 (0.62/day) | 339 (0.37/day) | 189 (0.21/day) |
| rejected: micro-stop | 6697 (7.34/day) | 3856 (4.23/day) | 2963 (3.25/day) | 1622 (1.78/day) |
| floored: micro-stop (floor mode only) | 0 (0.00/day) | 0 (0.00/day) | 0 (0.00/day) | 0 (0.00/day) |
| rejected: sizing | 0 (0.00/day) | 0 (0.00/day) | 0 (0.00/day) | 0 (0.00/day) |
| EXECUTED | 848 (0.93/day) | 807 (0.88/day) | 593 (0.65/day) | 539 (0.59/day) |

### validation_1

| stage | PRICE ONLY | + IMPULSE RVOL | + PULLBACK VOLUME | + TRIGGER VOLUME/FLOW |
|---|---|---|---|---|
| raw impulse legs (>= 0.5 ATR, any trend) | 19083 (103.71/day) | 19083 (103.71/day) | 19083 (103.71/day) | 19083 (103.71/day) |
| after 15m trend | 8241 (44.79/day) | 8241 (44.79/day) | 8241 (44.79/day) | 8241 (44.79/day) |
| after impulse >= anchor ATR | 8241 (44.79/day) | 8241 (44.79/day) | 8241 (44.79/day) | 8241 (44.79/day) |
| after impulse RVOL | 8241 (44.79/day) | 4405 (23.94/day) | 4405 (23.94/day) | 4405 (23.94/day) |
| after pullback (armed at least once) | 1965 (10.68/day) | 1184 (6.43/day) | 1184 (6.43/day) | 1184 (6.43/day) |
| after pullback-volume rule | 1965 (10.68/day) | 1184 (6.43/day) | 871 (4.73/day) | 871 (4.73/day) |
| after 1m trigger (breakout while armed) | 1134 (6.16/day) | 685 (3.72/day) | 499 (2.71/day) | 499 (2.71/day) |
| after flow confirmation | 1134 (6.16/day) | 685 (3.72/day) | 499 (2.71/day) | 346 (1.88/day) |
| skipped: position open | 134 (0.73/day) | 124 (0.67/day) | 75 (0.41/day) | 27 (0.15/day) |
| rejected: micro-stop | 1292 (7.02/day) | 704 (3.83/day) | 532 (2.89/day) | 283 (1.54/day) |
| floored: micro-stop (floor mode only) | 0 (0.00/day) | 0 (0.00/day) | 0 (0.00/day) | 0 (0.00/day) |
| rejected: sizing | 0 (0.00/day) | 0 (0.00/day) | 0 (0.00/day) | 0 (0.00/day) |
| EXECUTED | 175 (0.95/day) | 167 (0.91/day) | 113 (0.61/day) | 96 (0.52/day) |

### recent_validation

| stage | PRICE ONLY | + IMPULSE RVOL | + PULLBACK VOLUME | + TRIGGER VOLUME/FLOW |
|---|---|---|---|---|
| raw impulse legs (>= 0.5 ATR, any trend) | 19067 (105.34/day) | 19067 (105.34/day) | 19067 (105.34/day) | 19067 (105.34/day) |
| after 15m trend | 8299 (45.85/day) | 8299 (45.85/day) | 8299 (45.85/day) | 8299 (45.85/day) |
| after impulse >= anchor ATR | 8299 (45.85/day) | 8299 (45.85/day) | 8299 (45.85/day) | 8299 (45.85/day) |
| after impulse RVOL | 8299 (45.85/day) | 4274 (23.61/day) | 4274 (23.61/day) | 4274 (23.61/day) |
| after pullback (armed at least once) | 2112 (11.67/day) | 1194 (6.60/day) | 1194 (6.60/day) | 1194 (6.60/day) |
| after pullback-volume rule | 2112 (11.67/day) | 1194 (6.60/day) | 887 (4.90/day) | 887 (4.90/day) |
| after 1m trigger (breakout while armed) | 1293 (7.14/day) | 753 (4.16/day) | 562 (3.10/day) | 562 (3.10/day) |
| after flow confirmation | 1293 (7.14/day) | 753 (4.16/day) | 562 (3.10/day) | 381 (2.10/day) |
| skipped: position open | 112 (0.62/day) | 97 (0.54/day) | 58 (0.32/day) | 30 (0.17/day) |
| rejected: micro-stop | 1534 (8.48/day) | 829 (4.58/day) | 645 (3.56/day) | 348 (1.92/day) |
| floored: micro-stop (floor mode only) | 0 (0.00/day) | 0 (0.00/day) | 0 (0.00/day) | 0 (0.00/day) |
| rejected: sizing | 0 (0.00/day) | 0 (0.00/day) | 0 (0.00/day) | 0 (0.00/day) |
| EXECUTED | 151 (0.83/day) | 142 (0.78/day) | 85 (0.47/day) | 67 (0.37/day) |

## 3. Frequency distribution (executed trades per calendar day)

| window | variant | mean/day | median/day | % days 0 | % days 1-2 | % days 3-6 | % days > 6 | median hold |
|---|---|---|---|---|---|---|---|---|
| train | A_PRICE_ONLY | 0.93 | 0.0 | 54.1% | 34.8% | 10.3% | 0.9% | 34 |
| train | B_IMPULSE_RVOL | 0.88 | 0.0 | 55.7% | 34.0% | 9.4% | 0.9% | 34 |
| train | C_PULLBACK_VOLUME | 0.65 | 0.0 | 63.0% | 30.8% | 5.8% | 0.3% | 35 |
| train | D_TRIGGER_FLOW | 0.59 | 0.0 | 63.9% | 31.1% | 4.9% | 0.0% | 40 |
| validation_1 | A_PRICE_ONLY | 0.95 | 1.0 | 48.4% | 39.7% | 12.0% | 0.0% | 41 |
| validation_1 | B_IMPULSE_RVOL | 0.91 | 0.5 | 50.0% | 39.1% | 10.9% | 0.0% | 40 |
| validation_1 | C_PULLBACK_VOLUME | 0.61 | 0.0 | 58.7% | 35.3% | 6.0% | 0.0% | 41 |
| validation_1 | D_TRIGGER_FLOW | 0.52 | 0.0 | 62.0% | 34.8% | 3.3% | 0.0% | 56 |
| validation_1 | FROZEN_B_IMPULSE_RVOL | 0.91 | 0.5 | 50.0% | 39.1% | 10.9% | 0.0% | 40 |
| recent_validation | A_PRICE_ONLY | 0.83 | 0.0 | 59.7% | 30.4% | 9.9% | 0.0% | 32 |
| recent_validation | B_IMPULSE_RVOL | 0.78 | 0.0 | 60.8% | 29.3% | 9.9% | 0.0% | 32 |
| recent_validation | C_PULLBACK_VOLUME | 0.47 | 0.0 | 70.7% | 26.0% | 3.3% | 0.0% | 42 |
| recent_validation | D_TRIGGER_FLOW | 0.37 | 0.0 | 72.4% | 26.5% | 1.1% | 0.0% | 59 |
| recent_validation | FROZEN_B_IMPULSE_RVOL | 0.78 | 0.0 | 60.8% | 29.3% | 9.9% | 0.0% | 32 |

## 4. TRAIN half-year folds (net R per trade / trades per day)

| variant | 2022H1 | 2022H2 | 2023H1 | 2023H2 | 2024H1 | folds net > 0 |
|---|---|---|---|---|---|---|
| A_PRICE_ONLY | -0.20R / 1.7/d | -0.25R / 0.8/d | -0.15R / 0.7/d | 0.04R / 0.3/d | -0.20R / 1.1/d | 1/5 |
| B_IMPULSE_RVOL | -0.18R / 1.7/d | -0.24R / 0.7/d | -0.16R / 0.7/d | 0.05R / 0.3/d | -0.17R / 1.0/d | 1/5 |
| C_PULLBACK_VOLUME | -0.14R / 1.2/d | -0.22R / 0.6/d | -0.19R / 0.5/d | 0.06R / 0.3/d | -0.31R / 0.7/d | 1/5 |
| D_TRIGGER_FLOW | -0.04R / 1.0/d | -0.36R / 0.5/d | -0.17R / 0.5/d | 0.04R / 0.3/d | -0.35R / 0.6/d | 1/5 |

## 5. Pre-declared neighbourhoods (TRAIN only, one change at a time; never adopted)

### impulse_atr

| variant | n | trades/day | win rate | gross R | cost R | net R | net R 95% CI (day-cluster) | PF | max DD | median hold (min) | LONG n (net R) | SHORT n (net R) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A impulse_atr=0.8 | 848 | 0.93 | 40.2% | 0.042 | 0.227 | -0.185 | [-0.26, -0.11] | 0.72 | 19.77% | 34 | 409 (-0.18) | 439 (-0.19) |
| D impulse_atr=0.8 | 539 | 0.59 | 40.3% | 0.035 | 0.215 | -0.180 | [-0.28, -0.08] | 0.72 | 12.71% | 40 | 255 (-0.17) | 284 (-0.19) |
| A impulse_atr=1.0 | 848 | 0.93 | 40.2% | 0.042 | 0.227 | -0.185 | [-0.26, -0.11] | 0.72 | 19.77% | 34 | 409 (-0.18) | 439 (-0.19) |
| D impulse_atr=1.0 | 539 | 0.59 | 40.3% | 0.035 | 0.215 | -0.180 | [-0.28, -0.08] | 0.72 | 12.71% | 40 | 255 (-0.17) | 284 (-0.19) |
| A impulse_atr=1.2 | 848 | 0.93 | 40.2% | 0.042 | 0.227 | -0.185 | [-0.26, -0.11] | 0.72 | 19.77% | 34 | 409 (-0.18) | 439 (-0.19) |
| D impulse_atr=1.2 | 539 | 0.59 | 40.3% | 0.035 | 0.215 | -0.180 | [-0.28, -0.08] | 0.72 | 12.71% | 40 | 255 (-0.17) | 284 (-0.19) |

| variant | 2022H1 | 2022H2 | 2023H1 | 2023H2 | 2024H1 | folds net > 0 |
|---|---|---|---|---|---|---|
| A impulse_atr=0.8 | -0.20R / 1.7/d | -0.25R / 0.8/d | -0.15R / 0.7/d | 0.04R / 0.3/d | -0.20R / 1.1/d | 1/5 |
| D impulse_atr=0.8 | -0.04R / 1.0/d | -0.36R / 0.5/d | -0.17R / 0.5/d | 0.04R / 0.3/d | -0.35R / 0.6/d | 1/5 |
| A impulse_atr=1.0 | -0.20R / 1.7/d | -0.25R / 0.8/d | -0.15R / 0.7/d | 0.04R / 0.3/d | -0.20R / 1.1/d | 1/5 |
| D impulse_atr=1.0 | -0.04R / 1.0/d | -0.36R / 0.5/d | -0.17R / 0.5/d | 0.04R / 0.3/d | -0.35R / 0.6/d | 1/5 |
| A impulse_atr=1.2 | -0.20R / 1.7/d | -0.25R / 0.8/d | -0.15R / 0.7/d | 0.04R / 0.3/d | -0.20R / 1.1/d | 1/5 |
| D impulse_atr=1.2 | -0.04R / 1.0/d | -0.36R / 0.5/d | -0.17R / 0.5/d | 0.04R / 0.3/d | -0.35R / 0.6/d | 1/5 |

### impulse_rvol

| variant | n | trades/day | win rate | gross R | cost R | net R | net R 95% CI (day-cluster) | PF | max DD | median hold (min) | LONG n (net R) | SHORT n (net R) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B impulse_rvol=1.1 | 822 | 0.90 | 40.8% | 0.059 | 0.226 | -0.167 | [-0.25, -0.09] | 0.74 | 17.36% | 34 | 390 (-0.17) | 432 (-0.17) |
| B impulse_rvol=1.25 | 807 | 0.88 | 40.6% | 0.056 | 0.225 | -0.170 | [-0.25, -0.10] | 0.74 | 17.29% | 34 | 381 (-0.18) | 426 (-0.16) |
| B impulse_rvol=1.5 | 777 | 0.85 | 41.4% | 0.076 | 0.225 | -0.148 | [-0.23, -0.06] | 0.77 | 14.58% | 34 | 369 (-0.16) | 408 (-0.13) |

| variant | 2022H1 | 2022H2 | 2023H1 | 2023H2 | 2024H1 | folds net > 0 |
|---|---|---|---|---|---|---|
| B impulse_rvol=1.1 | -0.19R / 1.7/d | -0.23R / 0.8/d | -0.16R / 0.7/d | 0.06R / 0.3/d | -0.17R / 1.0/d | 1/5 |
| B impulse_rvol=1.25 | -0.18R / 1.7/d | -0.24R / 0.7/d | -0.16R / 0.7/d | 0.05R / 0.3/d | -0.17R / 1.0/d | 1/5 |
| B impulse_rvol=1.5 | -0.17R / 1.6/d | -0.20R / 0.7/d | -0.13R / 0.7/d | 0.07R / 0.3/d | -0.16R / 1.0/d | 1/5 |

### retrace

| variant | n | trades/day | win rate | gross R | cost R | net R | net R 95% CI (day-cluster) | PF | max DD | median hold (min) | LONG n (net R) | SHORT n (net R) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A retrace=0.2-0.6 | 861 | 0.94 | 40.5% | 0.052 | 0.227 | -0.176 | [-0.25, -0.10] | 0.73 | 19.07% | 34 | 415 (-0.17) | 446 (-0.18) |
| A retrace=0.25-0.6 | 848 | 0.93 | 40.2% | 0.042 | 0.227 | -0.185 | [-0.26, -0.11] | 0.72 | 19.77% | 34 | 409 (-0.18) | 439 (-0.19) |
| A retrace=0.25-0.7 | 1066 | 1.17 | 39.3% | 0.022 | 0.227 | -0.205 | [-0.27, -0.13] | 0.69 | 28.01% | 37 | 504 (-0.20) | 562 (-0.21) |

| variant | 2022H1 | 2022H2 | 2023H1 | 2023H2 | 2024H1 | folds net > 0 |
|---|---|---|---|---|---|---|
| A retrace=0.2-0.6 | -0.18R / 1.8/d | -0.25R / 0.8/d | -0.16R / 0.7/d | 0.04R / 0.3/d | -0.19R / 1.1/d | 1/5 |
| A retrace=0.25-0.6 | -0.20R / 1.7/d | -0.25R / 0.8/d | -0.15R / 0.7/d | 0.04R / 0.3/d | -0.20R / 1.1/d | 1/5 |
| A retrace=0.25-0.7 | -0.22R / 2.2/d | -0.27R / 1.1/d | -0.17R / 0.9/d | -0.10R / 0.4/d | -0.18R / 1.3/d | 0/5 |

### pb_vol_ratio

| variant | n | trades/day | win rate | gross R | cost R | net R | net R 95% CI (day-cluster) | PF | max DD | median hold (min) | LONG n (net R) | SHORT n (net R) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C pb_vol_ratio=0.6 | 399 | 0.44 | 40.6% | 0.061 | 0.229 | -0.168 | [-0.28, -0.06] | 0.74 | 8.72% | 34 | 186 (-0.21) | 213 (-0.13) |
| C pb_vol_ratio=0.75 | 593 | 0.65 | 39.6% | 0.043 | 0.227 | -0.185 | [-0.27, -0.09] | 0.72 | 14.32% | 35 | 279 (-0.19) | 314 (-0.18) |
| C pb_vol_ratio=0.9 | 692 | 0.76 | 40.2% | 0.053 | 0.228 | -0.175 | [-0.26, -0.09] | 0.73 | 15.79% | 34 | 329 (-0.21) | 363 (-0.14) |

| variant | 2022H1 | 2022H2 | 2023H1 | 2023H2 | 2024H1 | folds net > 0 |
|---|---|---|---|---|---|---|
| C pb_vol_ratio=0.6 | -0.06R / 0.8/d | -0.27R / 0.4/d | -0.24R / 0.3/d | 0.10R / 0.2/d | -0.32R / 0.5/d | 1/5 |
| C pb_vol_ratio=0.75 | -0.14R / 1.2/d | -0.22R / 0.6/d | -0.19R / 0.5/d | 0.06R / 0.3/d | -0.31R / 0.7/d | 1/5 |
| C pb_vol_ratio=0.9 | -0.12R / 1.4/d | -0.23R / 0.6/d | -0.22R / 0.6/d | 0.08R / 0.3/d | -0.28R / 0.9/d | 1/5 |

### trig_rvol

| variant | n | trades/day | win rate | gross R | cost R | net R | net R 95% CI (day-cluster) | PF | max DD | median hold (min) | LONG n (net R) | SHORT n (net R) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| D trig_rvol=1.0 | 556 | 0.61 | 40.1% | 0.037 | 0.218 | -0.181 | [-0.27, -0.08] | 0.72 | 13.12% | 40 | 263 (-0.18) | 293 (-0.18) |
| D trig_rvol=1.25 | 539 | 0.59 | 40.3% | 0.035 | 0.215 | -0.180 | [-0.28, -0.08] | 0.72 | 12.71% | 40 | 255 (-0.17) | 284 (-0.19) |
| D trig_rvol=1.5 | 515 | 0.56 | 40.0% | 0.030 | 0.214 | -0.183 | [-0.30, -0.09] | 0.72 | 12.24% | 40 | 246 (-0.18) | 269 (-0.18) |

| variant | 2022H1 | 2022H2 | 2023H1 | 2023H2 | 2024H1 | folds net > 0 |
|---|---|---|---|---|---|---|
| D trig_rvol=1.0 | -0.06R / 1.1/d | -0.32R / 0.5/d | -0.14R / 0.5/d | 0.04R / 0.2/d | -0.39R / 0.6/d | 1/5 |
| D trig_rvol=1.25 | -0.04R / 1.0/d | -0.36R / 0.5/d | -0.17R / 0.5/d | 0.04R / 0.3/d | -0.35R / 0.6/d | 1/5 |
| D trig_rvol=1.5 | -0.09R / 1.0/d | -0.36R / 0.5/d | -0.17R / 0.4/d | 0.17R / 0.3/d | -0.34R / 0.6/d | 1/5 |

### imbalance

| variant | n | trades/day | win rate | gross R | cost R | net R | net R 95% CI (day-cluster) | PF | max DD | median hold (min) | LONG n (net R) | SHORT n (net R) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| D imbalance=0.1 | 550 | 0.60 | 40.2% | 0.034 | 0.216 | -0.182 | [-0.28, -0.08] | 0.72 | 13.12% | 40 | 262 (-0.18) | 288 (-0.19) |
| D imbalance=0.2 | 539 | 0.59 | 40.3% | 0.035 | 0.215 | -0.180 | [-0.28, -0.08] | 0.72 | 12.71% | 40 | 255 (-0.17) | 284 (-0.19) |
| D imbalance=0.3 | 499 | 0.55 | 39.5% | 0.020 | 0.216 | -0.196 | [-0.31, -0.10] | 0.70 | 12.74% | 41 | 236 (-0.17) | 263 (-0.22) |

| variant | 2022H1 | 2022H2 | 2023H1 | 2023H2 | 2024H1 | folds net > 0 |
|---|---|---|---|---|---|---|
| D imbalance=0.1 | -0.05R / 1.1/d | -0.34R / 0.5/d | -0.20R / 0.5/d | 0.04R / 0.3/d | -0.35R / 0.7/d | 1/5 |
| D imbalance=0.2 | -0.04R / 1.0/d | -0.36R / 0.5/d | -0.17R / 0.5/d | 0.04R / 0.3/d | -0.35R / 0.6/d | 1/5 |
| D imbalance=0.3 | -0.09R / 1.0/d | -0.32R / 0.5/d | -0.25R / 0.4/d | 0.10R / 0.2/d | -0.32R / 0.6/d | 1/5 |

### delta_mode

| variant | n | trades/day | win rate | gross R | cost R | net R | net R 95% CI (day-cluster) | PF | max DD | median hold (min) | LONG n (net R) | SHORT n (net R) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| D delta=sign | 539 | 0.59 | 40.3% | 0.035 | 0.215 | -0.180 | [-0.28, -0.08] | 0.72 | 12.71% | 40 | 255 (-0.17) | 284 (-0.19) |
| D delta=median | 538 | 0.59 | 40.1% | 0.033 | 0.215 | -0.183 | [-0.28, -0.08] | 0.72 | 12.85% | 40 | 254 (-0.17) | 284 (-0.19) |

| variant | 2022H1 | 2022H2 | 2023H1 | 2023H2 | 2024H1 | folds net > 0 |
|---|---|---|---|---|---|---|
| D delta=sign | -0.04R / 1.0/d | -0.36R / 0.5/d | -0.17R / 0.5/d | 0.04R / 0.3/d | -0.35R / 0.6/d | 1/5 |
| D delta=median | -0.04R / 1.0/d | -0.37R / 0.5/d | -0.17R / 0.5/d | 0.04R / 0.3/d | -0.35R / 0.6/d | 1/5 |

### min_stop

| variant | n | trades/day | win rate | gross R | cost R | net R | net R 95% CI (day-cluster) | PF | max DD | median hold (min) | LONG n (net R) | SHORT n (net R) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A min_stop=0.4% | 1322 | 1.45 | 38.9% | 0.016 | 0.276 | -0.259 | [-0.32, -0.20] | 0.64 | 42.95% | 30 | 654 (-0.28) | 668 (-0.24) |
| D min_stop=0.4% | 786 | 0.86 | 39.2% | 0.020 | 0.258 | -0.239 | [-0.34, -0.15] | 0.66 | 23.81% | 37 | 378 (-0.25) | 408 (-0.23) |
| A min_stop=0.5% | 848 | 0.93 | 40.2% | 0.042 | 0.227 | -0.185 | [-0.26, -0.11] | 0.72 | 19.77% | 34 | 409 (-0.18) | 439 (-0.19) |
| D min_stop=0.5% | 539 | 0.59 | 40.3% | 0.035 | 0.215 | -0.180 | [-0.28, -0.08] | 0.72 | 12.71% | 40 | 255 (-0.17) | 284 (-0.19) |
| A min_stop=0.6% | 560 | 0.61 | 40.0% | 0.032 | 0.194 | -0.162 | [-0.26, -0.07] | 0.75 | 11.69% | 38 | 260 (-0.13) | 300 (-0.19) |
| D min_stop=0.6% | 385 | 0.42 | 39.5% | 0.011 | 0.188 | -0.177 | [-0.29, -0.06] | 0.72 | 8.63% | 42 | 177 (-0.12) | 208 (-0.23) |

| variant | 2022H1 | 2022H2 | 2023H1 | 2023H2 | 2024H1 | folds net > 0 |
|---|---|---|---|---|---|---|
| A min_stop=0.4% | -0.22R / 2.6/d | -0.41R / 1.4/d | -0.20R / 1.1/d | -0.16R / 0.6/d | -0.27R / 1.6/d | 0/5 |
| D min_stop=0.4% | -0.09R / 1.4/d | -0.45R / 0.8/d | -0.18R / 0.7/d | 0.01R / 0.4/d | -0.43R / 0.9/d | 1/5 |
| A min_stop=0.5% | -0.20R / 1.7/d | -0.25R / 0.8/d | -0.15R / 0.7/d | 0.04R / 0.3/d | -0.20R / 1.1/d | 1/5 |
| D min_stop=0.5% | -0.04R / 1.0/d | -0.36R / 0.5/d | -0.17R / 0.5/d | 0.04R / 0.3/d | -0.35R / 0.6/d | 1/5 |
| A min_stop=0.6% | -0.12R / 1.3/d | -0.26R / 0.5/d | -0.20R / 0.4/d | -0.14R / 0.2/d | -0.15R / 0.7/d | 0/5 |
| D min_stop=0.6% | -0.02R / 0.8/d | -0.42R / 0.4/d | -0.15R / 0.3/d | -0.02R / 0.1/d | -0.31R / 0.5/d | 0/5 |

### exit

| variant | n | trades/day | win rate | gross R | cost R | net R | net R 95% CI (day-cluster) | PF | max DD | median hold (min) | LONG n (net R) | SHORT n (net R) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A exit=A | 890 | 0.98 | 51.8% | 0.071 | 0.225 | -0.153 | [-0.21, -0.09] | 0.72 | 17.80% | 23 | 432 (-0.19) | 458 (-0.12) |
| D exit=A | 555 | 0.61 | 49.7% | 0.033 | 0.214 | -0.181 | [-0.26, -0.10] | 0.68 | 12.82% | 27 | 262 (-0.19) | 293 (-0.18) |
| A exit=B | 848 | 0.93 | 40.2% | 0.042 | 0.227 | -0.185 | [-0.26, -0.11] | 0.72 | 19.77% | 34 | 409 (-0.18) | 439 (-0.19) |
| D exit=B | 539 | 0.59 | 40.3% | 0.035 | 0.215 | -0.180 | [-0.28, -0.08] | 0.72 | 12.71% | 40 | 255 (-0.17) | 284 (-0.19) |
| A exit=C | 838 | 0.92 | 41.6% | 0.052 | 0.226 | -0.174 | [-0.24, -0.10] | 0.70 | 18.22% | 39 | 404 (-0.18) | 434 (-0.16) |
| D exit=C | 535 | 0.59 | 42.2% | 0.029 | 0.215 | -0.186 | [-0.27, -0.09] | 0.68 | 12.74% | 46 | 252 (-0.17) | 283 (-0.20) |

| variant | 2022H1 | 2022H2 | 2023H1 | 2023H2 | 2024H1 | folds net > 0 |
|---|---|---|---|---|---|---|
| A exit=A | -0.14R / 1.8/d | -0.24R / 0.8/d | -0.10R / 0.7/d | -0.03R / 0.4/d | -0.18R / 1.1/d | 0/5 |
| D exit=A | -0.11R / 1.1/d | -0.32R / 0.5/d | -0.21R / 0.5/d | 0.03R / 0.3/d | -0.25R / 0.7/d | 1/5 |
| A exit=B | -0.20R / 1.7/d | -0.25R / 0.8/d | -0.15R / 0.7/d | 0.04R / 0.3/d | -0.20R / 1.1/d | 1/5 |
| D exit=B | -0.04R / 1.0/d | -0.36R / 0.5/d | -0.17R / 0.5/d | 0.04R / 0.3/d | -0.35R / 0.6/d | 1/5 |
| A exit=C | -0.20R / 1.7/d | -0.25R / 0.8/d | -0.13R / 0.7/d | -0.02R / 0.3/d | -0.16R / 1.0/d | 0/5 |
| D exit=C | -0.10R / 1.0/d | -0.34R / 0.5/d | -0.19R / 0.5/d | -0.04R / 0.3/d | -0.26R / 0.6/d | 0/5 |

### time_stop

| variant | n | trades/day | win rate | gross R | cost R | net R | net R 95% CI (day-cluster) | PF | max DD | median hold (min) | LONG n (net R) | SHORT n (net R) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A time_stop=120 | 848 | 0.93 | 40.2% | 0.042 | 0.227 | -0.185 | [-0.26, -0.11] | 0.72 | 19.77% | 34 | 409 (-0.18) | 439 (-0.19) |
| D time_stop=120 | 539 | 0.59 | 40.3% | 0.035 | 0.215 | -0.180 | [-0.28, -0.08] | 0.72 | 12.71% | 40 | 255 (-0.17) | 284 (-0.19) |
| A time_stop=180 | 845 | 0.93 | 42.0% | 0.070 | 0.228 | -0.158 | [-0.24, -0.07] | 0.76 | 16.84% | 34 | 408 (-0.16) | 437 (-0.16) |
| D time_stop=180 | 537 | 0.59 | 42.5% | 0.058 | 0.216 | -0.159 | [-0.26, -0.05] | 0.76 | 11.31% | 40 | 254 (-0.14) | 283 (-0.18) |

| variant | 2022H1 | 2022H2 | 2023H1 | 2023H2 | 2024H1 | folds net > 0 |
|---|---|---|---|---|---|---|
| A time_stop=120 | -0.20R / 1.7/d | -0.25R / 0.8/d | -0.15R / 0.7/d | 0.04R / 0.3/d | -0.20R / 1.1/d | 1/5 |
| D time_stop=120 | -0.04R / 1.0/d | -0.36R / 0.5/d | -0.17R / 0.5/d | 0.04R / 0.3/d | -0.35R / 0.6/d | 1/5 |
| A time_stop=180 | -0.18R / 1.7/d | -0.25R / 0.8/d | -0.09R / 0.7/d | 0.06R / 0.3/d | -0.16R / 1.1/d | 1/5 |
| D time_stop=180 | -0.01R / 1.0/d | -0.38R / 0.5/d | -0.12R / 0.5/d | 0.05R / 0.3/d | -0.33R / 0.6/d | 1/5 |

### stop_floor

| variant | n | trades/day | win rate | gross R | cost R | net R | net R 95% CI (day-cluster) | PF | max DD | median hold (min) | LONG n (net R) | SHORT n (net R) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A floor min_stop=0.4% | 4389 | 4.81 | 34.1% | 0.009 | 0.346 | -0.337 | [-0.37, -0.30] | 0.52 | 185.02% | 57 | 2285 (-0.33) | 2104 (-0.34) |
| D floor min_stop=0.4% | 1687 | 1.85 | 35.0% | -0.001 | 0.323 | -0.324 | [-0.38, -0.27] | 0.55 | 68.33% | 49 | 868 (-0.33) | 819 (-0.32) |
| A floor min_stop=0.5% | 4258 | 4.67 | 32.2% | -0.000 | 0.284 | -0.284 | [-0.32, -0.25] | 0.55 | 150.96% | 79 | 2218 (-0.29) | 2040 (-0.28) |
| D floor min_stop=0.5% | 1666 | 1.83 | 33.7% | -0.006 | 0.272 | -0.278 | [-0.33, -0.22] | 0.58 | 58.02% | 68 | 860 (-0.30) | 806 (-0.26) |
| A floor min_stop=0.6% | 4132 | 4.53 | 31.9% | 0.009 | 0.238 | -0.229 | [-0.26, -0.20] | 0.58 | 118.37% | 112 | 2151 (-0.22) | 1981 (-0.24) |
| D floor min_stop=0.6% | 1644 | 1.80 | 33.9% | 0.015 | 0.233 | -0.217 | [-0.27, -0.16] | 0.63 | 45.43% | 88 | 848 (-0.22) | 796 (-0.21) |

| variant | 2022H1 | 2022H2 | 2023H1 | 2023H2 | 2024H1 | folds net > 0 |
|---|---|---|---|---|---|---|
| A floor min_stop=0.4% | -0.33R / 5.7/d | -0.35R / 4.7/d | -0.34R / 4.6/d | -0.31R / 4.3/d | -0.35R / 4.8/d | 0/5 |
| D floor min_stop=0.4% | -0.26R / 2.2/d | -0.39R / 1.7/d | -0.31R / 1.9/d | -0.27R / 1.6/d | -0.41R / 1.8/d | 0/5 |
| A floor min_stop=0.5% | -0.27R / 5.5/d | -0.31R / 4.5/d | -0.28R / 4.5/d | -0.24R / 4.2/d | -0.31R / 4.7/d | 0/5 |
| D floor min_stop=0.5% | -0.23R / 2.2/d | -0.39R / 1.7/d | -0.23R / 1.9/d | -0.22R / 1.6/d | -0.34R / 1.7/d | 0/5 |
| A floor min_stop=0.6% | -0.21R / 5.2/d | -0.26R / 4.4/d | -0.22R / 4.4/d | -0.20R / 4.1/d | -0.26R / 4.5/d | 0/5 |
| D floor min_stop=0.6% | -0.14R / 2.2/d | -0.29R / 1.6/d | -0.19R / 1.9/d | -0.19R / 1.6/d | -0.30R / 1.7/d | 0/5 |

### accel_gate

| variant | n | trades/day | win rate | gross R | cost R | net R | net R 95% CI (day-cluster) | PF | max DD | median hold (min) | LONG n (net R) | SHORT n (net R) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| D accel>=1.5 | 533 | 0.58 | 40.2% | 0.029 | 0.214 | -0.185 | [-0.28, -0.09] | 0.71 | 12.93% | 40 | 254 (-0.19) | 279 (-0.18) |

| variant | 2022H1 | 2022H2 | 2023H1 | 2023H2 | 2024H1 | folds net > 0 |
|---|---|---|---|---|---|---|
| D accel>=1.5 | -0.04R / 1.0/d | -0.36R / 0.5/d | -0.18R / 0.5/d | 0.01R / 0.2/d | -0.36R / 0.6/d | 1/5 |

## 6. Volume-acceleration diagnostic (TRAIN, full volume version D)

| accel quartile | range | n | net R |
|---|---|---|---|
| Q1 | 0.82 - 2.50 | 135 | -0.168 |
| Q2 | 2.51 - 3.80 | 134 | -0.203 |
| Q3 | 3.80 - 7.20 | 135 | -0.047 |
| Q4 | 7.23 - 208.15 | 135 | -0.302 |

| fold | n top quartile | net R top quartile | net R rest |
|---|---|---|---|
| 2022H1 | 46 | -0.422 | 0.080 |
| 2022H2 | 26 | -0.529 | -0.294 |
| 2023H1 | 22 | -0.082 | -0.195 |
| 2023H2 | 16 | 0.244 | -0.067 |
| 2024H1 | 25 | -0.389 | -0.344 |

Accel gate >= 1.5 on D: net R -0.185 (1 folds positive, 0.58/day). Promotion rule (>= +0.05 R vs D, >= 4 folds positive, >= 2.0/day): **NOT promoted (dropped)**.

## 7. Risk sensitivity (frozen specification, TRAIN)

| variant | n | trades/day | win rate | gross R | cost R | net R | net R 95% CI (day-cluster) | PF | max DD | median hold (min) | LONG n (net R) | SHORT n (net R) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| FROZEN_B_IMPULSE_RVOL risk=0.100% | 807 | 0.88 | 40.6% | 0.056 | 0.225 | -0.170 | [-0.25, -0.10] | 0.74 | 13.83% | 34 | 381 (-0.18) | 426 (-0.16) |
| FROZEN_B_IMPULSE_RVOL risk=0.125% | 807 | 0.88 | 40.6% | 0.056 | 0.225 | -0.170 | [-0.25, -0.10] | 0.74 | 17.29% | 34 | 381 (-0.18) | 426 (-0.16) |
| FROZEN_B_IMPULSE_RVOL risk=0.150% | 807 | 0.88 | 40.6% | 0.056 | 0.225 | -0.170 | [-0.25, -0.10] | 0.74 | 20.75% | 34 | 381 (-0.18) | 426 (-0.16) |

## 8. Selection and freeze (TRAIN only; design section 7)

Selected ladder step: **B_IMPULSE_RVOL** (qualified under the written rule: False; TRAIN net R -0.170). Frozen specification `FROZEN_B_IMPULSE_RVOL` (hash `c2a507a7e9ec`), run once per validation window. Accel promoted: False.

## 9. Answers

**1. Which conditions genuinely add edge** (TRAIN delta net R vs the previous step, confirmed only if positive on BOTH validation windows):

| rule | TRAIN delta net R | VAL1 delta | RECENT delta | TRAIN frequency cut | adds edge |
|---|---|---|---|---|---|
| + IMPULSE RVOL | 0.015 | -0.004 | -0.000 | 4.8% | no |
| + PULLBACK VOLUME | -0.015 | 0.105 | 0.008 | 26.5% | no |
| + TRIGGER VOLUME/FLOW | 0.004 | -0.185 | 0.003 | 9.1% | no |

**2. Which conditions only reduce trade count:** + IMPULSE RVOL, + PULLBACK VOLUME, + TRIGGER VOLUME/FLOW.

**3. Best simple specification (TRAIN rule):** FROZEN_B_IMPULSE_RVOL.

**4. Expected trades/day (mean of the two validation windows, frozen spec):** 0.85.

- recent_validation: n 142, 0.78/day, net R -0.183, PF 0.73, max DD 3.89% -> profitability FAIL, frequency FAIL
- validation_1: n 167, 0.91/day, net R -0.231, PF 0.65, max DD 4.83% -> profitability FAIL, frequency FAIL

**5. PASS/FAIL profitability (both windows, conservative costs):** **FAIL**.

**6. PASS/FAIL 3-6 trades/day (both windows):** **FAIL**.

## 10. Reading

- The price structure itself carries no edge at this horizon: PRICE ONLY earns 3.0 bps gross per trade on TRAIN against a 15.0 bps conservative round trip (net -0.185 R, PF 0.72), and no ladder step is positive in more than one TRAIN half-year or in either validation window.
- Frequency is destroyed by the stop rule, not by the volume filters: 5730 distinct PRICE-ONLY legs reach a 1m trigger on TRAIN, and 6697 trigger attempts (a leg can trigger more than once while armed) are rejected because the structural stop (pullback low - 0.1 ATR5m, entry after a 1m breakout) is closer than 0.50% (median structural stop about 0.2% of price, ATR5m about 0.13%); 848 trades execute. The 'impulse >= 1 ATR' rule removes almost nothing (36896 -> 36883 legs) because an 8-bar leg almost always spans more than one bar's ATR.
- Widening every stop to the minimum (pre-declared floor sensitivity, TRAIN) reaches the 3-6/day band but is worse, not better: A floor min_stop=0.4%: 4.8/day, gross 0.01R, cost 0.35R, net -0.34R; A floor min_stop=0.5%: 4.7/day, gross -0.00R, cost 0.28R, net -0.28R; A floor min_stop=0.6%: 4.5/day, gross 0.01R, cost 0.24R, net -0.23R. Costs of 0.23-0.35 R per trade on a 0.4-0.6% stop with ~0 gross are not recoverable by any exit or time stop studied.
- Volume rules (impulse RVOL, pullback contraction, trigger rvol / imbalance / delta) change net R by a few hundredths of an R in either direction and reduce trade count by 5-27% each; none adds edge on both validation windows. Volume acceleration does not help (top quartile is the worst).
- Classification C: V9 fails the owner's profitability and frequency criteria on both validation windows under the pre-registered specification and under every pre-declared neighbour. Nothing was tuned on validation; the locked holdout stays unspent.

STOPPED before the locked holdout (2025-07-01 -> 2026-09-30 not read). Nothing activated; no Bybit order; V5.1 untouched.
