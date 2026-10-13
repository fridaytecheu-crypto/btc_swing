# BTC V11.1 — COST-AWARE HIGH-CONVICTION INTRADAY: pre-registered historical experiment

Generated 2026-10-13T12:10:07.991388+00:00 · V11.1 11.1.0-cost-aware · config `57f038a1ae57` (strategy `b1c55b9b968d`, V11 base `d85f839c97e5`) · code `131a3d70a202-dirty` · Bybit 1m dataset `92d27161c194` · windows TRAIN 2022-01-01 -> 2024-07-01, VALIDATION 2024-07-01 -> 2025-07-01 · locked holdout: not read (every load capped at 2025-07-01).

ONE frozen specification (docs/BTC_V11_1_COST_AWARE_INTRADAY_DESIGN.md): V11 information groups and formulas unchanged; CHANGE #1 entry quality (>= 4 independent directional groups with sub-score >= 0.6 and margin >= 0.2, no trend contradiction, tightened A/B/C requirements, failed-breakout rejection); CHANGE #2 stop bounds 0.55% floor / 1.0% cap with the cost filter (expected 14.0 bps round trip, reject above 0.25R). Exits, risk (7.5 USDT), leverage ladder and taker costs identical to V11. NET is the headline. Nothing was tuned after the run.

## 0. Classification: **C** — validation net R <= 0 or PF <= 1 after primary costs: STOP, no shadow runner

Validation primary (net > 0 and PF > 1.05): False · train primary: False · secondary: {'gross_r_gt_0.10': False, 'cost_r_lt_0.25': True, 'trades_per_day_ge_1.5': False, 'median_hold_le_45': True} · family share of validation net R: {'A': 'n/a', 'B': 'n/a', 'C': 'n/a'} · shadow justified: False.

## 1. Headline (NET after primary costs)

| window | n | trades/day | win rate | gross R | cost R | NET R | PF | total net R | max DD | hold p50/p75/p90 (min) |
|---|---|---|---|---|---|---|---|---|---|---|
| TRAIN | 64 | 0.07 | 37.5% | -0.036 | 0.204 | -0.240 | 0.40 | -15.4 | 2.81% | 20 / 45 / 60 |
| VALIDATION | 36 | 0.10 | 38.9% | 0.003 | 0.204 | -0.201 | 0.36 | -7.2 | 1.09% | 26 / 60 / 60 |
| TRAIN x1.5 costs | 64 | 0.07 | 31.2% | -0.036 | 0.306 | -0.342 | 0.26 | -21.9 | 3.66% | 20 / 45 / 60 |
| VALIDATION x1.5 costs | 36 | 0.10 | 30.6% | 0.003 | 0.305 | -0.303 | 0.20 | -10.9 | 1.63% | 26 / 60 / 60 |

## 2. V11 vs V11.1 (same windows, same data, same exits and costs)

**VALIDATION**

| metric | V11 | V11.1 | change |
|---|---|---|---|
| trades / day | 2.67 | 0.10 | -2.57 |
| total trades | 974 | 36 | -938 |
| gross R / trade | -0.051 | 0.003 | 0.054 |
| cost R / trade | 0.364 | 0.204 | -0.160 |
| net R / trade | -0.415 | -0.201 | 0.214 |
| profit factor | 0.19 | 0.36 | 0.16 |
| win rate | 0.430 | 0.389 | -0.041 |
| median hold min | 20 | 26 | 6 |
| median stop % | 0.35 | 0.74 | 0.39 |
| cost USDT / trade | 2.727 | 1.527 | -1.200 |
| max DD % of 5000 | 60.81 | 1.09 | -59.72 |
| total net R | -404.2 | -7.2 | 397.0 |

**TRAIN**

| metric | V11 | V11.1 | change |
|---|---|---|---|
| trades / day | 2.68 | 0.07 | -2.61 |
| total trades | 2445 | 64 | -2381 |
| gross R / trade | -0.016 | -0.036 | -0.020 |
| cost R / trade | 0.366 | 0.204 | -0.162 |
| net R / trade | -0.381 | -0.240 | 0.141 |
| profit factor | 0.22 | 0.40 | 0.18 |
| win rate | 0.402 | 0.375 | -0.027 |
| median hold min | 20 | 20 | 0 |
| median stop % | 0.35 | 0.70 | 0.35 |
| cost USDT / trade | 2.744 | 1.528 | -1.215 |
| max DD % of 5000 | 139.86 | 2.81 | -137.05 |
| total net R | -932.4 | -15.4 | 917.1 |

## 3. Required metrics

| metric | TRAIN | VALIDATION |
|---|---|---|
| total trades | 64 | 36 |
| trades / calendar day | 0.07 | 0.10 |
| trades / active day | 1.05 | 1.12 |
| zero-trade days | 851 | 333 |
| zero-trade day share | 0.93 | 0.91 |
| median hold min | 20 | 26 |
| p75 hold | 45 | 60 |
| p90 hold | 60 | 60 |
| win rate | 0.375 | 0.389 |
| avg winner R | 0.428 | 0.286 |
| avg loser R | -0.640 | -0.511 |
| gross R / trade | -0.036 | 0.003 |
| cost R / trade | 0.204 | 0.204 |
| net R / trade | -0.240 | -0.201 |
| profit factor | 0.40 | 0.36 |
| total net R | -15.4 | -7.2 |
| max DD USDT | 140.6 | 54.4 |
| max DD % of 5000 | 2.81 | 1.09 |
| max DD R | 18.8 | 7.3 |
| LONG | 33 | 21 |
| SHORT | 31 | 15 |
| cost USDT / trade | 1.528 | 1.527 |
| median stop % | 0.70 | 0.74 |
| mean leverage | 1.20 | 1.19 |
| mean evidence groups (executed) | 4.47 | 4.39 |
| skipped while open (one-position) | 36 | 44 |
| exits (STOP / TP-ladder BE / TRAIL runner / STALL / TIME) | {'BE': 13, 'STALL': 32, 'STOP': 4, 'TIME': 9, 'TRAIL': 6} | {'BE': 6, 'STALL': 13, 'STOP': 4, 'TIME': 11, 'TRAIL': 2} |
| family A / B / C | {'A': 64, 'B': 0, 'C': 0} | {'A': 36, 'B': 0, 'C': 0} |
| funnel counters | {'candidates': 140652, 'armed': 6260, 'triggers': 21185, 'executed': 64, 'blocked': 594, 'skipped_while_open': 36} | {'candidates': 52380, 'armed': 2475, 'triggers': 8578, 'executed': 36, 'blocked': 258, 'skipped_while_open': 44} |
| decision blockers (COST_TOO_HIGH, STOP_TOO_WIDE_V11_1, one-position, sizing) | {'COST_TOO_HIGH': 543, 'STOP_TOO_WIDE_V11_1': 36, 'POSITION_OPEN': 36} | {'COST_TOO_HIGH': 227, 'STOP_TOO_WIDE_V11_1': 16, 'POSITION_OPEN': 44} |
| quality rejections at trigger (evidence, contradiction, family rules, failed breakout) | {'FAILED_BREAKOUT_DETECTED': 2136, 'TREND_CONTRADICTION': 489, 'A_POOR_REWARD_ROOM': 427, 'INSUFFICIENT_EVIDENCE': 1629, 'A_VWAP_POSITION': 998, 'B_RVOL_NOT_RISING': 325, 'A_NO_CLEAR_REGIME': 308, 'B_NOT_DECISIVE': 111, 'A_NO_REACCELERATION': 402, 'B_NO_MOMENTUM_EXPANSION': 160, 'A_NO_DIRECTIONAL_FLOW': 139, 'A_OPPOSING_FLOW': 28, 'FAILED_BREAKOUT': 344, 'A_STRUCTURE_INVALIDATED': 1, 'B_NO_DIRECTIONAL_FLOW': 15, 'B_ABSORPTION': 30, 'C_NO_EXTENSION': 103} | {'B_NO_MOMENTUM_EXPANSION': 63, 'FAILED_BREAKOUT_DETECTED': 863, 'INSUFFICIENT_EVIDENCE': 512, 'FAILED_BREAKOUT': 162, 'A_NO_REACCELERATION': 145, 'A_POOR_REWARD_ROOM': 120, 'B_RVOL_NOT_RISING': 151, 'A_OPPOSING_FLOW': 7, 'A_NO_DIRECTIONAL_FLOW': 43, 'TREND_CONTRADICTION': 217, 'C_NO_EXTENSION': 53, 'A_NO_CLEAR_REGIME': 72, 'A_VWAP_POSITION': 421, 'B_NOT_DECISIVE': 44, 'B_ABSORPTION': 11, 'B_NO_DIRECTIONAL_FLOW': 5} |
| evidence group frequency among executed | {'trend': 63, 'momentum': 60, 'volume': 58, 'location': 63, 'derivatives': 42, 'microstructure': 0} | {'trend': 36, 'momentum': 35, 'volume': 33, 'location': 35, 'derivatives': 19, 'microstructure': 0} |
| family funnel (armed/triggers/executed) | {'A': {'armed': 2650, 'triggers': 17162, 'executed': 64}, 'B': {'armed': 3528, 'triggers': 3146, 'executed': 0}, 'C': {'armed': 82, 'triggers': 877, 'executed': 0}} | {'A': {'armed': 1047, 'triggers': 6543, 'executed': 36}, 'B': {'armed': 1382, 'triggers': 1553, 'executed': 0}, 'C': {'armed': 46, 'triggers': 482, 'executed': 0}} |
| share of minutes with score >= entry / with evidence too | 0.114 / 0.074 | 0.095 / 0.061 |

## 4. Breakdowns — TRAIN

**Setup family**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| A | 64 | -0.240 | 37.5% | 0.40 | -15.4 | 20 |
| B | 0 | n/a | n/a | n/a | n/a | n/a |
| C | 0 | n/a | n/a | n/a | n/a | n/a |

**Side**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| LONG | 33 | -0.272 | 30.3% | 0.35 | -9.0 | 20 |
| SHORT | 31 | -0.205 | 45.2% | 0.46 | -6.4 | 20 |

**Family x side**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| A-LONG | 33 | -0.272 | 30.3% | 0.35 | -9.0 | 20 |
| A-SHORT | 31 | -0.205 | 45.2% | 0.46 | -6.4 | 20 |
| B-LONG | 0 | n/a | n/a | n/a | n/a | n/a |
| B-SHORT | 0 | n/a | n/a | n/a | n/a | n/a |
| C-LONG | 0 | n/a | n/a | n/a | n/a | n/a |
| C-SHORT | 0 | n/a | n/a | n/a | n/a | n/a |

**Trend regime at decision**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| struct_up | 33 | -0.272 | 30.3% | 0.35 | -9.0 | 20 |
| struct_down | 31 | -0.205 | 45.2% | 0.46 | -6.4 | 20 |
| struct_neutral | 0 | n/a | n/a | n/a | n/a | n/a |

**Volatility regime**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| low_vol | 16 | -0.345 | 31.2% | 0.18 | -5.5 | 20 |
| mid_vol | 24 | -0.221 | 41.7% | 0.45 | -5.3 | 20 |
| high_vol | 24 | -0.188 | 37.5% | 0.51 | -4.5 | 20 |

**Hour bucket (UTC)**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| 00-07 | 36 | -0.167 | 41.7% | 0.51 | -6.0 | 20 |
| 08-15 | 13 | -0.373 | 30.8% | 0.28 | -4.8 | 20 |
| 16-23 | 15 | -0.300 | 33.3% | 0.31 | -4.5 | 20 |

**Exit reason**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| BE | 13 | 0.140 | 100.0% | inf | 1.8 | 22 |
| STALL | 32 | -0.598 | 0.0% | 0.00 | -19.1 | 20 |
| STOP | 4 | -1.207 | 0.0% | 0.00 | -4.8 | 49 |
| TIME | 9 | 0.187 | 55.6% | 2.02 | 1.7 | 60 |
| TRAIL | 6 | 0.850 | 100.0% | inf | 5.1 | 48 |

## 4. Breakdowns — VALIDATION

**Setup family**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| A | 36 | -0.201 | 38.9% | 0.36 | -7.2 | 26 |
| B | 0 | n/a | n/a | n/a | n/a | n/a |
| C | 0 | n/a | n/a | n/a | n/a | n/a |

**Side**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| LONG | 21 | -0.066 | 47.6% | 0.71 | -1.4 | 52 |
| SHORT | 15 | -0.389 | 26.7% | 0.09 | -5.8 | 20 |

**Family x side**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| A-LONG | 21 | -0.066 | 47.6% | 0.71 | -1.4 | 52 |
| A-SHORT | 15 | -0.389 | 26.7% | 0.09 | -5.8 | 20 |
| B-LONG | 0 | n/a | n/a | n/a | n/a | n/a |
| B-SHORT | 0 | n/a | n/a | n/a | n/a | n/a |
| C-LONG | 0 | n/a | n/a | n/a | n/a | n/a |
| C-SHORT | 0 | n/a | n/a | n/a | n/a | n/a |

**Trend regime at decision**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| struct_up | 21 | -0.066 | 47.6% | 0.71 | -1.4 | 52 |
| struct_down | 15 | -0.389 | 26.7% | 0.09 | -5.8 | 20 |
| struct_neutral | 0 | n/a | n/a | n/a | n/a | n/a |

**Volatility regime**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| low_vol | 5 | -0.327 | 40.0% | 0.18 | -1.6 | 60 |
| mid_vol | 17 | -0.158 | 47.1% | 0.46 | -2.7 | 31 |
| high_vol | 14 | -0.207 | 28.6% | 0.32 | -2.9 | 20 |

**Hour bucket (UTC)**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| 00-07 | 16 | -0.220 | 31.2% | 0.25 | -3.5 | 20 |
| 08-15 | 9 | -0.138 | 33.3% | 0.55 | -1.2 | 60 |
| 16-23 | 11 | -0.223 | 54.5% | 0.35 | -2.5 | 31 |

**Exit reason**

| bucket | n | net R | win rate | PF | total net R | median hold |
|---|---|---|---|---|---|---|
| BE | 6 | 0.144 | 100.0% | inf | 0.9 | 21 |
| STALL | 13 | -0.374 | 0.0% | 0.00 | -4.9 | 20 |
| STOP | 4 | -1.260 | 0.0% | 0.00 | -5.0 | 54 |
| TIME | 11 | 0.049 | 54.5% | 1.41 | 0.5 | 60 |
| TRAIL | 2 | 0.640 | 100.0% | inf | 1.3 | 44 |

## 5. The six questions

1. **Did wider stops materially lower cost/R?** VALIDATION cost per trade 0.364R (V11, median stop 0.35%) -> 0.204R (V11.1, median stop 0.74%), a change of -0.160R; TRAIN 0.366R -> 0.204R. In USDT: 2.727 -> 1.527 per trade.
2. **Did stricter entry quality improve gross R?** VALIDATION gross -0.051R -> 0.003R (change 0.054R); TRAIN -0.016R -> -0.036R. Mean confirming groups among executed trades: 4.39 (validation).
3. **Did both improve net expectancy?** VALIDATION net -0.415R -> -0.201R (change 0.214R), PF 0.19 -> 0.36; TRAIN net -0.381R -> -0.240R. Net positive on validation: False.
4. **How much frequency was sacrificed?** VALIDATION 2.67/day -> 0.10/day (96% fewer trades); TRAIN 2.68 -> 0.07. Quality rejections at trigger: {'B_NO_MOMENTUM_EXPANSION': 63, 'FAILED_BREAKOUT_DETECTED': 863, 'INSUFFICIENT_EVIDENCE': 512, 'FAILED_BREAKOUT': 162, 'A_NO_REACCELERATION': 145, 'A_POOR_REWARD_ROOM': 120, 'B_RVOL_NOT_RISING': 151, 'A_OPPOSING_FLOW': 7, 'A_NO_DIRECTIONAL_FLOW': 43, 'TREND_CONTRADICTION': 217, 'C_NO_EXTENSION': 53, 'A_NO_CLEAR_REGIME': 72, 'A_VWAP_POSITION': 421, 'B_NOT_DECISIVE': 44, 'B_ABSORPTION': 11, 'B_NO_DIRECTIONAL_FLOW': 5}; decision blockers: {'COST_TOO_HIGH': 227, 'STOP_TOO_WIDE_V11_1': 16, 'POSITION_OPEN': 44}.
5. **Which family improved most?** VALIDATION net R by family (V11 -> V11.1): A: -0.201R on 36 trades; B: n/aR on 0 trades; C: n/aR on 0 trades (V11: A -0.378R / B -0.451R / C -0.570R). TRAIN: A: -0.240R on 64; B: n/aR on 0; C: n/aR on 0 (V11: A -0.378R / B -0.387R / C -0.370R).
6. **Is any apparent improvement robust across TRAIN and VALIDATION?** Net positive on both windows: False. Cost/R change TRAIN -0.162R vs VALIDATION -0.160R; gross change TRAIN -0.020R vs VALIDATION 0.054R; x1.5 costs: TRAIN net -0.342R, VALIDATION net -0.303R.

Classification C means STOP: no V11.1 shadow runner, no V11.2, no tuning. Any further step needs a new owner pre-registration.
