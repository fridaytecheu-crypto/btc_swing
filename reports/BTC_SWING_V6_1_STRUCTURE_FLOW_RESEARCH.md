# BTC V6.1 STRUCTURE + FLOW INTRADAY — research report (development + validation; holdout NOT run)

**>= 2 trades/day: FAIL. Net edge: FAIL. Recommendation: DO NOT spend the locked holdout: frequency and net edge both fail.**

Config `btc_swing_v6_1_structure_flow` hash `028cc86200064b3c…`, code `aa3150e92211-dirty`, research hash `449a76157ec42f67…`, run 2026-10-12T08:05:08.512132+00:00. Paper research only: no exchange client exists in this track; V5/V5.1/V6 untouched; the locked holdout 2025-07 -> 2026-09 was neither downloaded nor evaluated.

## 0. Data, venues, availability

| frame | first row | last close | rows (dev+val) | valid rows | days with invalid rows | OI coverage |
|---|---|---|---|---|---|---|
| bybit | 2021-11-17 00:00 | 2025-07-01 00:00 | 367775 | 100.00% | 1 | 99.9% |
| binance | 2021-11-17 00:00 | 2025-07-01 00:00 | 367775 | 99.99% | 6 | 99.9% |

Venue mismatch: price/volume/aggressor flow are Bybit-native in the primary frame; open interest, funding and premium are Binance USDT-M series in BOTH frames (Bybit history not reachable); the Binance frame uses Binance klines + aggTrades flow. Primary = Bybit-native price/volume/aggressor flow from the Bybit public trade archive (manifest hash `92d27161c194…`); Binance frame = robustness comparison. Regime share (Bybit frame): {'RANGE': 0.28350353000504286, 'TREND_DOWN': 0.3429174020843839, 'TREND_UP': 0.37194276348966215}.

## 1. Family results independently (validation, frozen exits/entry; development below)


**validation**

| stream | n | trades/cal.day | net R mean | boot 95% CI | net R sum | PF | win | gross R | fee R | slip R | cost/gross | med stop bps | med hold min | max DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BREAKOUT_RETEST_CONTINUATION [structure_only] | 34 | 0.09 | -0.142 | -0.612 / 0.338 | -4.8 | 0.79 | 26.5% | 0.010 | -0.118 | -0.034 | 13.5% | 93 | 75 | 1.0% |
| BREAKOUT_RETEST_CONTINUATION [structure_flow] | 22 | 0.06 | -0.216 | -0.682 / 0.407 | -4.8 | 0.65 | 18.2% | -0.063 | -0.116 | -0.036 | 16.3% | 92 | 85 | 0.9% |
| IMPULSE_CONSOLIDATION_BREAK [structure_only] | 0 | 0.00 | - | - / - | - | - | - | - | - | - | - | - | - | - |
| IMPULSE_CONSOLIDATION_BREAK [structure_flow] | 0 | 0.00 | - | - / - | - | - | - | - | - | - | - | - | - | - |
| LIQUIDITY_SWEEP_REVERSAL [structure_only] | 59 | 0.16 | -0.318 | -0.503 / -0.143 | -18.8 | 0.36 | 45.8% | -0.180 | -0.109 | -0.028 | 21.4% | 96 | 40 | 2.9% |
| LIQUIDITY_SWEEP_REVERSAL [structure_flow] | 37 | 0.10 | -0.308 | -0.542 / -0.070 | -11.4 | 0.34 | 45.9% | -0.164 | -0.114 | -0.031 | 23.9% | 95 | 50 | 1.8% |
| RANGE_EDGE_MEAN_REVERSION [structure_only] | 19 | 0.05 | -0.378 | -0.788 / 0.014 | -7.2 | 0.40 | 36.8% | -0.220 | -0.126 | -0.033 | 19.1% | 84 | 60 | 1.4% |
| RANGE_EDGE_MEAN_REVERSION [structure_flow] | 16 | 0.04 | -0.448 | -0.813 / -0.060 | -7.2 | 0.27 | 31.2% | -0.287 | -0.126 | -0.035 | 22.6% | 86 | 68 | 1.1% |
| TREND_PULLBACK_RECLAIM [structure_only] | 42 | 0.12 | -0.396 | -0.647 / -0.127 | -16.7 | 0.34 | 31.0% | -0.251 | -0.114 | -0.032 | 19.3% | 94 | 48 | 2.5% |
| TREND_PULLBACK_RECLAIM [structure_flow] | 45 | 0.12 | -0.273 | -0.517 / -0.019 | -12.3 | 0.45 | 33.3% | -0.129 | -0.116 | -0.029 | 21.0% | 94 | 45 | 2.0% |

**development**

| stream | n | trades/cal.day | net R mean | boot 95% CI | net R sum | PF | win | gross R | fee R | slip R | cost/gross | med stop bps | med hold min | max DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BREAKOUT_RETEST_CONTINUATION [structure_only] | 88 | 0.10 | -0.357 | -0.566 / -0.147 | -31.4 | 0.46 | 34.1% | -0.216 | -0.108 | -0.032 | 15.2% | 101 | 72 | 5.0% |
| BREAKOUT_RETEST_CONTINUATION [structure_flow] | 57 | 0.06 | -0.418 | -0.655 / -0.163 | -23.8 | 0.39 | 33.3% | -0.279 | -0.106 | -0.032 | 15.2% | 105 | 75 | 3.7% |
| IMPULSE_CONSOLIDATION_BREAK [structure_only] | 0 | 0.00 | - | - / - | - | - | - | - | - | - | - | - | - | - |
| IMPULSE_CONSOLIDATION_BREAK [structure_flow] | 0 | 0.00 | - | - / - | - | - | - | - | - | - | - | - | - | - |
| LIQUIDITY_SWEEP_REVERSAL [structure_only] | 250 | 0.27 | -0.256 | -0.360 / -0.141 | -64.0 | 0.52 | 38.4% | -0.114 | -0.108 | -0.034 | 18.0% | 98 | 240 | 10.1% |
| LIQUIDITY_SWEEP_REVERSAL [structure_flow] | 188 | 0.21 | -0.265 | -0.397 / -0.134 | -49.7 | 0.50 | 37.8% | -0.123 | -0.108 | -0.034 | 18.5% | 98 | 240 | 8.2% |
| RANGE_EDGE_MEAN_REVERSION [structure_only] | 32 | 0.04 | -0.041 | -0.389 / 0.341 | -1.3 | 0.92 | 43.8% | 0.112 | -0.119 | -0.034 | 16.3% | 87 | 218 | 1.3% |
| RANGE_EDGE_MEAN_REVERSION [structure_flow] | 32 | 0.04 | -0.190 | -0.552 / 0.197 | -6.1 | 0.67 | 37.5% | -0.038 | -0.117 | -0.035 | 16.3% | 87 | 158 | 1.3% |
| TREND_PULLBACK_RECLAIM [structure_only] | 142 | 0.16 | -0.250 | -0.433 / -0.038 | -35.5 | 0.59 | 35.9% | -0.108 | -0.110 | -0.033 | 15.5% | 97 | 162 | 6.2% |
| TREND_PULLBACK_RECLAIM [structure_flow] | 148 | 0.16 | -0.220 | -0.394 / -0.030 | -32.6 | 0.62 | 39.2% | -0.075 | -0.112 | -0.033 | 16.0% | 92 | 172 | 5.6% |

## 2. Structure-only vs structure + flow (declared rule: flow kept only if it improves net R on BOTH windows)

| family | dev Δ net R (flow - structure) | dev trades ratio | val Δ net R | val trades ratio | final |
|---|---|---|---|---|---|
| BREAKOUT_RETEST_CONTINUATION | -0.061 | 0.65 | -0.074 | 0.65 | STRUCTURE ONLY |
| IMPULSE_CONSOLIDATION_BREAK | - | - | - | - | STRUCTURE ONLY |
| LIQUIDITY_SWEEP_REVERSAL | -0.009 | 0.75 | 0.010 | 0.63 | STRUCTURE ONLY |
| RANGE_EDGE_MEAN_REVERSION | -0.149 | 1.00 | -0.069 | 0.84 | STRUCTURE ONLY |
| TREND_PULLBACK_RECLAIM | 0.030 | 1.04 | 0.123 | 1.07 | KEEP FLOW |

## 3. Maker / taker entry comparison (combined families, frozen exits)


**development**

| entry mode | n | trades/day | maker fills | unfilled | setup gone | gross R | fee R | slip R | net R | PF | cost/stop med |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TAKER | 512 | 0.56 | 0.0% | 0 | 0 | -0.079 | -0.109 | -0.027 | -0.215 | 0.54 | 0.16 |
| MAKER | 1065 | 1.17 | 100.0% | 25 | 0 | -0.093 | -0.100 | -0.024 | -0.218 | 0.57 | 0.14 |
| MAKER_FIRST | 1085 | 1.19 | 98.1% | 0 | 4 | -0.094 | -0.101 | -0.025 | -0.220 | 0.56 | 0.14 |

**validation**

| entry mode | n | trades/day | maker fills | unfilled | setup gone | gross R | fee R | slip R | net R | PF | cost/stop med |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TAKER | 147 | 0.40 | 0.0% | 0 | 0 | -0.122 | -0.116 | -0.030 | -0.268 | 0.51 | 0.16 |
| MAKER | 367 | 1.01 | 100.0% | 7 | 0 | -0.034 | -0.105 | -0.025 | -0.165 | 0.68 | 0.15 |
| MAKER_FIRST | 372 | 1.02 | 98.4% | 0 | 1 | -0.033 | -0.106 | -0.025 | -0.164 | 0.68 | 0.15 |

Entry mode after development: TAKER (declared: MAKER_FIRST replaces TAKER only if it improves net expectancy on development without reducing trades/day by more than 10% (confirmed on validation before adoption)); final: TAKER.

## 4. Stop-distance and cost analysis

Cost-share grid (development, combined; primary θ = 0.20; θ = 0 means no filter):

| θ (cost/stop max) | primary | n | trades/day | net R mean | net R sum | PF | med stop bps | cost/gross | triggers refused |
|---|---|---|---|---|---|---|---|---|---|
| 0.15 | no | 241 | 0.26 | -0.181 | -43.7 | 0.58 | 127 | 16.2% | 4236 |
| 0.20 | yes | 512 | 0.56 | -0.215 | -110.3 | 0.54 | 98 | 19.4% | 3927 |
| 0.25 | no | 836 | 0.92 | -0.251 | -210.2 | 0.51 | 81 | 22.5% | 3571 |
| 0.30 | no | 1192 | 1.31 | -0.273 | -324.9 | 0.50 | 71 | 25.4% | 3150 |
| 0.00 | no | 3844 | 4.21 | -0.558 | -2145.7 | 0.32 | 37 | 57.2% | 0 |

Minimum-stop grid (development, combined):

| min stop % (0 = structural) | n | trades/day | net R mean | PF | med stop bps | cost/med stop | med hold | max DD |
|---|---|---|---|---|---|---|---|---|
| 0.00 | 512 | 0.56 | -0.215 | 0.54 | 98 | 15.3% | 55 | 16.9% |
| 0.50 | 512 | 0.56 | -0.215 | 0.54 | 98 | 15.3% | 55 | 16.9% |
| 0.75 | 512 | 0.56 | -0.215 | 0.54 | 98 | 15.3% | 55 | 16.9% |
| 1.00 | 2407 | 2.64 | -0.174 | 0.53 | 101 | 14.9% | 100 | 62.8% |
| 1.25 | 2400 | 2.63 | -0.158 | 0.52 | 121 | 12.4% | 110 | 57.1% |

Natural stop-distance buckets (final specification, Bybit validation):

| bucket | n | net R mean | PF | med stop bps | cost/stop med | fee+slip R | med hold |
|---|---|---|---|---|---|---|---|
| 0-0.5% | 0 | - | - | - | - | - | - |
| 0.5-0.75% | 0 | - | - | - | - | - | - |
| 0.75-1% | 93 | -0.358 | 0.41 | 82 | 0.19 | 0.167 | 45 |
| 1-1.25% | 32 | -0.057 | 0.87 | 111 | 0.14 | 0.126 | 70 |
| >1.25% | 22 | -0.198 | 0.56 | 154 | 0.10 | 0.089 | 45 |

Cost distributions (final specification, Bybit validation):

| population | n | cost bps p25/p50/p75 | stop bps p25/p50/p75 | cost/stop p25/p50/p75 | cost/target p25/p50/p75 |
|---|---|---|---|---|---|
| all_triggers | 2150 | 15.0 / 15.0 / 15.0 | 25.4 / 35.3 / 51.4 | 0.3 / 0.4 / 0.6 | 0.2 / 0.3 / 0.5 |
| executed_trades | 147 | 15.2 / 15.2 / 15.2 | 80.5 / 93.9 / 111.6 | 0.1 / 0.2 / 0.2 | 0.1 / 0.2 / 0.3 |

## 5. Hold-time distribution (final specification, Bybit validation)

| hold-time distribution | value |
|---|---|
| median / mean / p10 / p90 (min) | 50 / 78 / 10 / 132 |
| <30m / 30-60m / 1-2h / 2-4h / 4-6h / 6-8h / >8h | 30.6% / 25.2% / 21.1% / 17.0% / 2.7% / 2.0% / 1.4% |
| time-stop exits / time-limit exits | 10.9% / 1.4% |

Exit variant x time-stop grid (development; declared selection):

| variant | time stop h | n | trades/cal.day | net R mean | net R/day | PF | win | med hold | max DD | positive folds | time-stop exits |
|---|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 2 | 453 | 0.50 | -0.246 | -0.122 | 0.54 | 36.9% | 120 | 17.0% | 0 | 20.3% |
| P1 | 4 | 445 | 0.49 | -0.265 | -0.129 | 0.54 | 37.5% | 185 | 17.9% | 0 | 7.9% |
| P1 | 6 | 445 | 0.49 | -0.275 | -0.134 | 0.53 | 38.0% | 185 | 18.7% | 0 | 3.8% |
| P2 | 2 | 451 | 0.49 | -0.252 | -0.125 | 0.55 | 35.7% | 120 | 17.6% | 0 | 20.4% |
| P2 | 4 | 443 | 0.49 | -0.269 | -0.131 | 0.56 | 35.9% | 195 | 18.4% | 0 | 7.9% |
| P2 | 6 | 443 | 0.49 | -0.279 | -0.136 | 0.55 | 36.3% | 195 | 19.1% | 0 | 3.8% |
| S | 2 | 512 | 0.56 | -0.215 | -0.121 | 0.54 | 41.8% | 55 | 16.9% | 0 | 18.4% |
| S | 4 | 508 | 0.56 | -0.234 | -0.130 | 0.54 | 43.7% | 55 | 18.1% | 0 | 7.7% |
| S | 6 | 508 | 0.56 | -0.238 | -0.132 | 0.54 | 44.3% | 55 | 18.5% | 0 | 3.5% |
| T | 2 | 451 | 0.49 | -0.300 | -0.148 | 0.52 | 27.9% | 120 | 20.6% | 0 | 20.4% |
| T | 4 | 443 | 0.49 | -0.320 | -0.155 | 0.53 | 27.8% | 195 | 21.4% | 0 | 7.9% |
| T | 6 | 443 | 0.49 | -0.331 | -0.161 | 0.52 | 28.0% | 195 | 22.3% | 0 | 3.8% |

Chosen on development: variant **S**, time stop **2 h** (declared: among (variant, time stop) with >= 2.0 trades/calendar day AND net R positive in >= 4 of 6 development folds, the highest net R per day; else the highest net R per day of all (flagged)); frequency+fold requirement met on development: False.

## 6. Frequency distribution (final specification, Bybit validation)

| frequency (calendar-day statistics; active days are NOT used) | value |
|---|---|
| calendar days / executed trades | 365 / 147 |
| mean / median trades per CALENDAR day | 0.40 / 0.0 |
| % days 0 / 1 / 2 / 3-4 / 5-6 / >6 | 74.8% / 17.5% / 3.3% / 4.1% / 0.3% / 0.0% |
| max trades in a day | 6 |
| mean / median hours between entries | 54.1 / 17.0 |
| mean / median holding time (min) | 78 / 50 |
| weekday / weekend trades per day | 0.52 / 0.11 |
| session share Asia / Europe / US | 23.8% / 31.3% / 44.9% |
| by side / by family | {'LONG': 68, 'SHORT': 79} / {'BREAKOUT_RETEST_CONTINUATION': 29, 'LIQUIDITY_SWEEP_REVERSAL': 56, 'RANGE_EDGE_MEAN_REVERSION': 18, 'TREND_PULLBACK_RECLAIM': 44} |
| >= 2.0 trades per calendar day | FAIL |

| one-position rule | value |
|---|---|
| valid triggers refused: position open / cooldown / cost share | 96 / 4 / 1903 |
| suppressed per day / suppression share | 0.27 / 40.5% |
| counterfactual of position-open refusals: mean net R / median hold | -0.140 / 58 min |
| sequential mode: trades/day, net R mean | 0.42, -0.259 |
| independent mode: trades/day, net R mean | 5.64, -0.517 |

## 7. Net expectancy and profit factor (final specification)

| stream | n | trades/cal.day | net R mean | boot 95% CI | net R sum | PF | win | gross R | fee R | slip R | cost/gross | med stop bps | med hold min | max DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| binance_development | 521 | 0.57 | -0.191 | -0.257 / -0.123 | -99.7 | 0.56 | 41.7% | -0.057 | -0.107 | -0.027 | 20.6% | 100 | 60 | 15.0% |
| binance_validation | 131 | 0.36 | -0.277 | -0.441 / -0.119 | -36.3 | 0.49 | 34.4% | -0.130 | -0.116 | -0.031 | 19.3% | 91 | 45 | 5.7% |
| bybit_development | 512 | 0.56 | -0.215 | -0.291 / -0.141 | -110.3 | 0.54 | 41.8% | -0.079 | -0.109 | -0.027 | 19.4% | 98 | 55 | 16.9% |
| bybit_validation | 147 | 0.40 | -0.268 | -0.414 / -0.105 | -39.4 | 0.51 | 37.4% | -0.122 | -0.116 | -0.030 | 18.6% | 94 | 50 | 6.0% |

Validation null (regime/hour-matched, same stops/exits/costs): real -0.268R vs null -0.241R, edge -0.027R, z -0.31.


By family / side / 1h regime / exit reason (Bybit validation):

| family | n | trades/day | net R mean | net R sum | PF | win | med hold |
|---|---|---|---|---|---|---|---|
| BREAKOUT_RETEST_CONTINUATION | 29 | 0.08 | -0.102 | -2.9 | 0.85 | 27.6% | 75 |
| LIQUIDITY_SWEEP_REVERSAL | 56 | 0.15 | -0.327 | -18.3 | 0.35 | 44.6% | 40 |
| RANGE_EDGE_MEAN_REVERSION | 18 | 0.05 | -0.391 | -7.0 | 0.41 | 38.9% | 55 |
| TREND_PULLBACK_RECLAIM | 44 | 0.12 | -0.253 | -11.1 | 0.47 | 34.1% | 45 |

| side | n | net R mean | PF | win |
|---|---|---|---|---|
| LONG | 68 | -0.133 | 0.73 | 42.6% |
| SHORT | 79 | -0.385 | 0.35 | 32.9% |

| 1h regime (-1 down / 0 range / 1 up) | n | net R mean | PF |
|---|---|---|---|
| -1 | 75 | -0.330 | 0.45 |
| 0 | 39 | -0.287 | 0.48 |
| 1 | 33 | -0.106 | 0.75 |

| exit reason | n | net R mean | med hold |
|---|---|---|---|
| BREAKEVEN | 9 | -0.137 | 125 |
| STOP | 63 | -1.163 | 40 |
| TARGET | 57 | 0.699 | 30 |
| TIME_LIMIT | 2 | -0.251 | 480 |
| TIME_STOP | 16 | -0.269 | 120 |

Risk grid (Bybit validation; R results identical across risk levels):

| risk/trade | primary | net return | max DD | max DD (R) | worst day |
|---|---|---|---|---|---|
| 0.100% | no | -3.9% | 4.0% | 40.3 | -0.3% |
| 0.125% | no | -4.9% | 5.0% | 40.3 | -0.4% |
| 0.150% | yes | -5.9% | 6.0% | 40.3 | -0.5% |
| 0.200% | no | -7.9% | 8.1% | 40.3 | -0.7% |
| 0.250% | no | -9.9% | 10.1% | 40.3 | -0.9% |

With the daily budget overlay: n=147, net R mean -0.268, max DD 6.0%.

## 8. Walk-forward stability


bybit_development: fold 1: -34.2R (n=145, 0.95/d) | fold 2: -12.1R (n=131, 0.86/d) | fold 3: -24.4R (n=80, 0.53/d) | fold 4: -6.4R (n=30, 0.20/d) | fold 5: -18.0R (n=51, 0.34/d) | fold 6: -15.1R (n=75, 0.49/d)

bybit_validation: fold 1: -18.2R (n=75, 0.41/d) | fold 2: -21.2R (n=72, 0.39/d)

binance_development: fold 1: -32.9R (n=156, 1.03/d) | fold 2: -14.3R (n=143, 0.94/d) | fold 3: -18.2R (n=77, 0.51/d) | fold 4: -7.2R (n=25, 0.16/d) | fold 5: -11.2R (n=44, 0.29/d) | fold 6: -15.7R (n=76, 0.50/d)

binance_validation: fold 1: -15.7R (n=65, 0.36/d) | fold 2: -20.6R (n=66, 0.36/d)


Family threshold grids (development, family alone; stability only):

| family | parameter | value | default | n | trades/day | net R mean | PF |
|---|---|---|---|---|---|---|---|
| TREND_PULLBACK_RECLAIM | impulse_min_atr15 | 1.25 | no | 168 | 0.18 | -0.224 | 0.54 |
| TREND_PULLBACK_RECLAIM | impulse_min_atr15 | 1.50 | yes | 173 | 0.19 | -0.227 | 0.53 |
| TREND_PULLBACK_RECLAIM | impulse_min_atr15 | 2.00 | no | 171 | 0.19 | -0.217 | 0.55 |
| BREAKOUT_RETEST_CONTINUATION | flow_rel_vol | 1.00 | no | 92 | 0.10 | -0.267 | 0.61 |
| BREAKOUT_RETEST_CONTINUATION | flow_rel_vol | 1.20 | yes | 92 | 0.10 | -0.267 | 0.61 |
| BREAKOUT_RETEST_CONTINUATION | flow_rel_vol | 1.50 | no | 92 | 0.10 | -0.267 | 0.61 |
| LIQUIDITY_SWEEP_REVERSAL | sweep_min_atr15 | 0.00 | no | 267 | 0.29 | -0.234 | 0.44 |
| LIQUIDITY_SWEEP_REVERSAL | sweep_min_atr15 | 0.05 | yes | 261 | 0.29 | -0.243 | 0.43 |
| LIQUIDITY_SWEEP_REVERSAL | sweep_min_atr15 | 0.15 | no | 239 | 0.26 | -0.230 | 0.44 |
| RANGE_EDGE_MEAN_REVERSION | edge_zone | 0.15 | no | 32 | 0.04 | -0.001 | 1.00 |
| RANGE_EDGE_MEAN_REVERSION | edge_zone | 0.20 | yes | 34 | 0.04 | -0.035 | 0.92 |
| RANGE_EDGE_MEAN_REVERSION | edge_zone | 0.25 | no | 34 | 0.04 | -0.036 | 0.91 |
| IMPULSE_CONSOLIDATION_BREAK | impulse_min_atr15 | 1.50 | no | 2 | 0.00 | 0.454 | 6.29 |
| IMPULSE_CONSOLIDATION_BREAK | impulse_min_atr15 | 2.00 | yes | 0 | 0.00 | - | - |
| IMPULSE_CONSOLIDATION_BREAK | impulse_min_atr15 | 2.50 | no | 0 | 0.00 | - | - |

## 9. Bybit-native vs Binance (identical final specification)

| stream | n | trades/cal.day | net R mean | boot 95% CI | net R sum | PF | win | gross R | fee R | slip R | cost/gross | med stop bps | med hold min | max DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bybit_development | 512 | 0.56 | -0.215 | -0.291 / -0.141 | -110.3 | 0.54 | 41.8% | -0.079 | -0.109 | -0.027 | 19.4% | 98 | 55 | 16.9% |
| binance_development | 521 | 0.57 | -0.191 | -0.257 / -0.123 | -99.7 | 0.56 | 41.7% | -0.057 | -0.107 | -0.027 | 20.6% | 100 | 60 | 15.0% |
| bybit_validation | 147 | 0.40 | -0.268 | -0.414 / -0.105 | -39.4 | 0.51 | 37.4% | -0.122 | -0.116 | -0.030 | 18.6% | 94 | 50 | 6.0% |
| binance_validation | 131 | 0.36 | -0.277 | -0.441 / -0.119 | -36.3 | 0.49 | 34.4% | -0.130 | -0.116 | -0.031 | 19.3% | 91 | 45 | 5.7% |

## 10. Exact combined candidate

| element | value |
|---|---|
| families | TREND_PULLBACK_RECLAIM, LIQUIDITY_SWEEP_REVERSAL, BREAKOUT_RETEST_CONTINUATION, IMPULSE_CONSOLIDATION_BREAK, RANGE_EDGE_MEAN_REVERSION |
| flow confirmation per family | {'BREAKOUT_RETEST_CONTINUATION': False, 'IMPULSE_CONSOLIDATION_BREAK': False, 'LIQUIDITY_SWEEP_REVERSAL': False, 'RANGE_EDGE_MEAN_REVERSION': False, 'TREND_PULLBACK_RECLAIM': True} |
| exit variant / time stop | S (full exit at the family's structural target; breakeven after +1R) / 2 h (MFE < 0.5R), hard cap 8 h |
| entry mode | TAKER |
| cost-share eligibility | round-trip cost / stop <= 0.2 |
| stops | structural, floor 1.0 ATR15, cap 3.0 ATR15 |
| risk / overlay | 0.15% of 5000 USDT; 5 full-risk losses or 0.75% daily stop |
| one position / cooldowns | yes / 3-3-12 bars |

## 11-13. Gate and recommendation (Bybit validation, final specification)

| criterion | value | passed |
|---|---|---|
| FREQUENCY: mean trades per calendar day >= 2.0 | 0.403 | FAIL |
| NET EDGE: net expectancy after costs > 0.00R | -0.268 | FAIL |
| NET EDGE: profit factor after costs > 1.00 | 0.510 | FAIL |
| preferred: profit factor > 1.10 | 0.510 | FAIL |
| not one isolated period: net R positive in >= 4 of 6 development folds | 0 | FAIL |
| not one isolated period: net R positive in >= 1 of 2 validation halves | 0 | FAIL |
| bootstrap 95% lower bound of net R > 0 | -0.414 | FAIL |
| drawdown: max DD < 10% of equity at the primary risk | 0.060 | PASS |
| no single family > 80% of net profit | - | PASS |

**11. >= 2 trades/calendar day: FAIL.**  **12. Net edge after costs: FAIL.**  **13. Locked holdout: DO NOT spend the locked holdout: frequency and net edge both fail.** The harness stopped before the holdout; spending it needs the owner's explicit decision and a separate run.


## 14. Reading of the result

- **Scientific question (design section 2): NOT supported.** With structure first, location second and flow as confirmation, the final specification shows gross expectancy -0.122R per trade BEFORE costs on Bybit validation (development -0.079R): the structural setups as declared have no gross edge, so costs (0.146R per trade) are not the binding problem this time. The regime/hour-matched null loses about as much (-0.241R), and the Binance frame reproduces the result (-0.277R), so this is not a venue artefact.
- **Frequency vs cost eligibility are in direct conflict.** The pre-registered cost-share rule (round trip <= 20% of the stop, i.e. stops of >= ~75 bps at 15 bps round trip) leaves 0.40 trades per calendar day; without the filter the same families fire 4.21/day on development at -0.558R. Forcing a 1% minimum stop reaches 2.64/day at -0.174R. Every point of the grid is negative: more trades only multiply the loss.
- **Flow confirmation** improved net expectancy on both windows only for TREND_PULLBACK_RECLAIM; for the other families it removed trades without improving expectancy. Flow is not the missing ingredient.
- **Maker entries** fill at the limit in nearly every case under the 1-tick-through rule and raise the trade count by lowering the cost share (more triggers pass the eligibility rule), but net expectancy stays negative on development, so the declared rule kept TAKER. The fill model ignores queue position and assumes a fill whenever the bar trades through the limit, which is optimistic; even so maker entries do not create an edge.
- **One-position rule** is not the throttle: 96 valid triggers were refused for an open position against 1903 refused by the cost-share rule; the independent-episode diagnostic reaches 5.64/day at -0.517R.
- **Least bad family on validation:** BREAKOUT_RETEST_CONTINUATION (n=29, -0.102R, PF 0.85); IMPULSE_CONSOLIDATION_BREAK produced no eligible trade at its declared thresholds (its consolidation stops are too tight for the cost rule). No family is positive on either window.
- **What would be a NEW hypothesis (needs a new pre-registration; nothing here was tuned):** (1) the only sub-populations with PF near 1 are stops >= 1% and TREND_UP longs — a lower-frequency swing geometry, not an intraday engine; (2) a 2-slot design cannot help while the per-trade edge is negative; (3) the research venue question is closed: Bybit-native and Binance results coincide; (4) with five generations of structural/flow rules negative after costs on 5m-1h horizons, the remaining untested mechanisms are prospective-only (liquidation stream) or require a different cost structure (maker-only market making is a different product).
