# BTC V6 ACTIVE INTRADAY — research report (Phases 2-4)

**V6 ACTIVE-INTRADAY OBJECTIVE NOT ACHIEVED** — classification on validation: **C** (A robust and suitable, B promising, C reject). Bottleneck summary: frequency NOT met and NO net edge after costs.

Config `btc_swing_v6_active_intraday` hash `9d4ef909f4a3d2e5…`, code `2c4e51a99a73-dirty`, research hash `7537e73fd3b71183…`, run 2026-10-12T06:39:14.768231+00:00. Paper research only: this track has no exchange client; nothing here placed an order or touched V5/V5.1.

## A. Data and windows

| item | value |
|---|---|
| research venue (history) | binance_um_perp (Binance Vision archives: 5m klines, aggTrades 5m flow, 5-min metrics/OI, funding, premium index) |
| forward venue (shadow, later) | bybit_linear_perp |
| frame rows / valid rows / flow rows from aggTrades | 380736 / 380684 / 376651 |
| development | 2022-01-01 00:00 -> 2024-07-01 00:00 (912 d) |
| validation | 2024-07-01 00:00 -> 2025-07-01 00:00 (365 d) |
| once-only OOS | 2025-07-01 00:00 -> 2026-10-01 00:00 (457 d) — NOT RUN (unspent) |
| liquidation families D/E | forward-only (no historical liquidation data exists); not part of any statistic below |
| data hashes | funding 6e651f5f48…, metrics 069b2df5ae…, perp_klines_5m 5e5242ac3e…, premium_index_5m 456dbe868f… |

Every feature is PIT at the 5m close (completed 15m/1h/4h bars only, metrics/funding/premium by observation time, same-time-of-day baselines from previous days only); gap rows are never observations.

## B. Families and frozen specification

| family | Stage A (event) | Stage B (entry) | stop |
|---|---|---|---|
| A MOMENTUM_CONTINUATION | 15m close beyond the 4h 5m high/low, rel. volume >= 1.50, 15m imbalance and 1h CVD slope with the break, OI up, 1h trend not opposed, extension <= 3.0 ATR5 | 5m close back in [level, level + 1.0 ATR5] or a higher-low/lower-high within 2 h; invalid on a close 0.5 ATR5 beyond the level | 15m structure since the event |
| B PULLBACK_CONTINUATION | 1h move >= 1.50 ATR1h with 1h imbalance and OI confirming | retrace 30-60% on contracting volume (rel. vol <= 1.0), 5m imbalance back with the trend and a reclaim of the 15m EMA21 or a 5m swing; invalid beyond 75% | pullback extreme |
| C FAILED_BREAKOUT_REVERSAL | new 24h extreme with rel. volume >= 1.50 and |5m imbalance| >= 0.20, back inside within 3 bars with CVD slope or OI not confirming | 5m close beyond the breakout bar's opposite extreme; invalid on a close beyond the sweep extreme | sweep extreme |
| F FLOW_OI_IMPULSE | 15m return >= 1.00 ATR15, rel. volume >= 1.50, |15m imbalance| >= 0.15, CVD slope with the move, |OI change| >= 1.0x its same-time baseline and rising, premium not crowded (|z| <= 2.0) | first 5m close beyond a <= 2-bar pause within 6 bars; longer pauses invalidate | 15m impulse origin |

Common rules: stop = farther of structure and 1.0 ATR(15m), capped at 3.0 ATR(15m); one position, no pyramiding/averaging; episode max age 24 bars, entry window 6 bars; cooldowns 6 / 3 / 12 bars (close / invalidation / two consecutive losses in a family-side); priority MOMENTUM_CONTINUATION, FLOW_OI_IMPULSE, PULLBACK_CONTINUATION, FAILED_BREAKOUT_REVERSAL; time cap 8 h; fills at the next 5m open; costs taker 5.5 bps per side, slippage 1.0/3.0 bps (entry/stop), half-spread 0.05 USDT, funding at settlements; risk 0.15% of 5000 USDT reference equity, frozen leverage ladder [1.0, 2.0, 3.0, 5.0, 10.0] (margin cap 25%).

**Frozen after development:** exit variant **B** (TP1 +0.75R 30% / TP2 +1.5R 30% / runner on the 15m structure trail), breakeven after TP1 = False. Selection rule: pre-declared: among variants with >= 2.0 trades per calendar day on development, the highest net R per day; if none qualifies, the highest net R per day of all (flagged). Frequency requirement met on development by the chosen variant: False.

## C. REQUIRED frequency report — validation, frozen specification

| frequency (calendar-day statistics first) | value |
|---|---|
| calendar days in window | 365 |
| executed trades | 654 |
| mean trades per CALENDAR day | 1.79 |
| median trades per calendar day | 2.0 |
| % days with 0 / 1 / 2 / 3-6 / >6 trades | 10.7% / 29.0% / 37.3% / 23.0% / 0.0% |
| max trades in a day | 5 |
| active days (pre-registered definition) / share | 175 / 47.9% |
| mean trades per ACTIVE day | 1.93 |
| mean trades per quiet day | 1.66 |
| share of active days inside the 3-6 target | 24.0% |
| mean / median hours between entries | 13.4 / 10.8 |
| mean / median holding time (min) | 199 / 165 |
| share held > 4 h / time-limit exits | 35.9% / 13.0% |
| weekday / weekend trades per day | 1.91 / 1.49 |
| session share (Asia / Europe / US) | 27.7% / 40.4% / 32.0% |
| by side | {'LONG': 352, 'SHORT': 302} |
| by family | {'FAILED_BREAKOUT_REVERSAL': 194, 'FLOW_OI_IMPULSE': 58, 'MOMENTUM_CONTINUATION': 364, 'PULLBACK_CONTINUATION': 38} |
| HARD minimum >= 2.0 trades per calendar day | NOT MET |

| year | trades | trades/day |
|---|---|---|
| 2024 | 312 | 1.70 |
| 2025 | 342 | 1.89 |

### C.2 Funnel (validation)

| funnel (per day) | value |
|---|---|
| Stage A events (all families, both sides) | 11.04 |
| first-in-cluster events | 5.57 |
| episodes opened | 5.50 |
| episodes that became ACTIONABLE | 2.85 |
| triggers (taken + refused) | 4.28 |
| refused by slot / cooldown / budget / risk | 2.49 |
| executed trades | 1.79 |
| Stage B conversion (actionable / episodes) | 51.9% |
| trigger -> trade | 41.8% |

## D. Per-family results (validation, net of primary costs; development folds for the fold criterion)

| stream | n | trades/day | net R mean | boot 95% CI | net R sum | PF | win | gross R | fee R | slip R | fund R | cost/gross | med stop bps | med hold min | max DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| FAILED_BREAKOUT_REVERSAL | 347 | 0.95 | -0.226 | -0.353 / -0.088 | -78.6 | 0.63 | 42.7% | 0.068 | -0.228 | -0.067 | 0.001 | 30.7% | 55 | 190 | 12.3% |
| FLOW_OI_IMPULSE | 97 | 0.27 | -0.135 | -0.312 / 0.053 | -13.1 | 0.73 | 44.3% | 0.057 | -0.149 | -0.043 | -0.000 | 23.2% | 77 | 260 | 2.1% |
| MOMENTUM_CONTINUATION | 428 | 1.17 | -0.389 | -0.501 / -0.274 | -166.4 | 0.46 | 33.9% | -0.018 | -0.287 | -0.083 | -0.001 | 39.5% | 43 | 120 | 25.8% |
| PULLBACK_CONTINUATION | 53 | 0.15 | -0.162 | -0.522 / 0.264 | -8.6 | 0.76 | 47.2% | 0.368 | -0.419 | -0.110 | -0.001 | 45.2% | 30 | 100 | 2.6% |

**FAILED_BREAKOUT_REVERSAL** — criteria: FAIL; cost flag (round trip > 25% of median stop): True

| criterion | value | passed |
|---|---|---|
| net expectancy (validation, net of costs) > 0.00R | -0.226 | FAIL |
| bootstrap 95% lower bound > -0.02R | -0.353 | FAIL |
| profit factor > 1.10 | 0.635 | FAIL |
| trades/day >= 0.50 | 0.951 | PASS |
| cost share of gross < 60% | 0.307 | PASS |
| positive in >= 2 of 3 development folds | 0 | FAIL |

null (regime- and hour-matched, k=20): real -0.226R vs null -0.350R, edge +0.124R, z 2.05

| side | n | trades/day | net R mean | PF | win | med hold | med stop bps |
|---|---|---|---|---|---|---|---|
| LONG | 157 | 0.43 | -0.309 | 0.51 | 43.9% | 190 | 63 |
| SHORT | 190 | 0.52 | -0.158 | 0.74 | 41.6% | 195 | 53 |

**FLOW_OI_IMPULSE** — criteria: FAIL; cost flag (round trip > 25% of median stop): False

| criterion | value | passed |
|---|---|---|
| net expectancy (validation, net of costs) > 0.00R | -0.135 | FAIL |
| bootstrap 95% lower bound > -0.02R | -0.312 | FAIL |
| profit factor > 1.10 | 0.727 | FAIL |
| trades/day >= 0.50 | 0.266 | FAIL |
| cost share of gross < 60% | 0.232 | PASS |
| positive in >= 2 of 3 development folds | 0 | FAIL |

null (regime- and hour-matched, k=20): real -0.135R vs null -0.216R, edge +0.081R, z 0.80

| side | n | trades/day | net R mean | PF | win | med hold | med stop bps |
|---|---|---|---|---|---|---|---|
| LONG | 50 | 0.14 | -0.184 | 0.63 | 46.0% | 278 | 78 |
| SHORT | 47 | 0.13 | -0.083 | 0.83 | 42.6% | 260 | 76 |

**MOMENTUM_CONTINUATION** — criteria: FAIL; cost flag (round trip > 25% of median stop): True

| criterion | value | passed |
|---|---|---|
| net expectancy (validation, net of costs) > 0.00R | -0.389 | FAIL |
| bootstrap 95% lower bound > -0.02R | -0.501 | FAIL |
| profit factor > 1.10 | 0.459 | FAIL |
| trades/day >= 0.50 | 1.173 | PASS |
| cost share of gross < 60% | 0.395 | PASS |
| positive in >= 2 of 3 development folds | 0 | FAIL |

null (regime- and hour-matched, k=20): real -0.389R vs null -0.374R, edge -0.015R, z -0.24

| side | n | trades/day | net R mean | PF | win | med hold | med stop bps |
|---|---|---|---|---|---|---|---|
| LONG | 246 | 0.67 | -0.475 | 0.36 | 32.9% | 120 | 40 |
| SHORT | 182 | 0.50 | -0.273 | 0.61 | 35.2% | 120 | 47 |

**PULLBACK_CONTINUATION** — criteria: FAIL; cost flag (round trip > 25% of median stop): True

| criterion | value | passed |
|---|---|---|
| net expectancy (validation, net of costs) > 0.00R | -0.162 | FAIL |
| bootstrap 95% lower bound > -0.02R | -0.522 | FAIL |
| profit factor > 1.10 | 0.756 | FAIL |
| trades/day >= 0.50 | 0.145 | FAIL |
| cost share of gross < 60% | 0.452 | PASS |
| positive in >= 2 of 3 development folds | 0 | FAIL |

null (regime- and hour-matched, k=20): real -0.162R vs null -0.529R, edge +0.367R, z 1.91

| side | n | trades/day | net R mean | PF | win | med hold | med stop bps |
|---|---|---|---|---|---|---|---|
| LONG | 30 | 0.08 | -0.068 | 0.90 | 46.7% | 85 | 30 |
| SHORT | 23 | 0.06 | -0.284 | 0.55 | 47.8% | 135 | 35 |

### D.2 Development per family (same statistics, development window)

| stream | n | trades/day | net R mean | boot 95% CI | net R sum | PF | win | gross R | fee R | slip R | fund R | cost/gross | med stop bps | med hold min | max DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| FAILED_BREAKOUT_REVERSAL | 554 | 0.61 | -0.214 | -0.292 / -0.130 | -118.6 | 0.62 | 46.4% | 0.024 | -0.184 | -0.055 | 0.002 | 26.5% | 70 | 280 | 18.1% |
| FLOW_OI_IMPULSE | 156 | 0.17 | -0.067 | -0.254 / 0.107 | -10.5 | 0.87 | 46.8% | 0.144 | -0.165 | -0.047 | 0.001 | 21.8% | 85 | 252 | 2.3% |
| MOMENTUM_CONTINUATION | 853 | 0.94 | -0.415 | -0.493 / -0.339 | -354.0 | 0.44 | 41.9% | -0.040 | -0.288 | -0.086 | -0.001 | 37.7% | 47 | 80 | 53.5% |
| PULLBACK_CONTINUATION | 86 | 0.09 | -0.591 | -0.935 / -0.193 | -50.8 | 0.40 | 31.4% | -0.055 | -0.408 | -0.126 | -0.001 | 46.2% | 34 | 58 | 8.4% |

Development stability folds (net R sum per fold):

| family | fold 1 | fold 2 | fold 3 | fold 4 | fold 5 | fold 6 |
|---|---|---|---|---|---|---|
| FAILED_BREAKOUT_REVERSAL | -35.6 (n=103) | -20.0 (n=99) | -16.8 (n=72) | -9.6 (n=86) | -25.6 (n=89) | -11.0 (n=105) |
| FLOW_OI_IMPULSE | -0.2 (n=29) | -1.7 (n=18) | -9.8 (n=21) | 2.0 (n=18) | 4.6 (n=26) | -5.3 (n=44) |
| MOMENTUM_CONTINUATION | -40.5 (n=158) | -40.9 (n=160) | -39.0 (n=107) | -75.0 (n=110) | -97.1 (n=158) | -61.5 (n=160) |
| PULLBACK_CONTINUATION | -2.1 (n=16) | -11.5 (n=11) | -10.5 (n=13) | -9.4 (n=16) | -7.7 (n=11) | -9.5 (n=19) |

## E. Combined V6 (all historical families)

| stream | n | trades/day | net R mean | boot 95% CI | net R sum | PF | win | gross R | fee R | slip R | fund R | cost/gross | med stop bps | med hold min | max DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| development, default variant | 1329 | 1.46 | -0.339 | -0.403 / -0.278 | -450.7 | 0.50 | 43.2% | -0.005 | -0.257 | -0.077 | -0.000 | 34.6% | 54 | 115 | 67.8% |
| development, frozen variant | 1295 | 1.42 | -0.320 | -0.388 / -0.254 | -414.6 | 0.52 | 35.8% | 0.015 | -0.259 | -0.076 | -0.001 | 36.3% | 53 | 160 | 62.7% |
| VALIDATION, frozen | 654 | 1.79 | -0.303 | -0.396 / -0.207 | -198.2 | 0.55 | 37.9% | 0.044 | -0.269 | -0.077 | -0.000 | 36.0% | 48 | 165 | 31.0% |
| validation, frozen + daily budget overlay | 654 | 1.79 | -0.303 | -0.396 / -0.207 | -198.2 | 0.55 | 37.9% | 0.044 | -0.269 | -0.077 | -0.000 | 36.0% | 48 | 165 | 31.0% |

Validation null (regime/hour-matched, same stops/exits/costs): real -0.303R vs null -0.331R, edge +0.028R, z 0.59.


Validation by year / regime / side / session:

| year | n | trades/day | net R sum | net R mean | PF |
|---|---|---|---|---|---|
| 2024 | 312 | 0.85 | -72.1 | -0.231 | 0.65 |
| 2025 | 342 | 0.94 | -126.1 | -0.369 | 0.46 |

| regime (1h trend x 30d vol tercile) | n | net R sum | net R mean | PF |
|---|---|---|---|---|
| DOWN_LOWVOL | 65 | -11.0 | -0.169 | 0.72 |
| DOWN_MIDVOL | 66 | -27.7 | -0.419 | 0.40 |
| DOWN_HIGHVOL | 71 | -0.5 | -0.007 | 0.99 |
| FLAT_LOWVOL | 58 | -9.6 | -0.165 | 0.79 |
| FLAT_MIDVOL | 46 | -19.9 | -0.433 | 0.41 |
| FLAT_HIGHVOL | 56 | -25.3 | -0.452 | 0.39 |
| UP_LOWVOL | 122 | -61.8 | -0.507 | 0.37 |
| UP_MIDVOL | 99 | -27.4 | -0.276 | 0.53 |
| UP_HIGHVOL | 68 | -13.7 | -0.201 | 0.64 |
| UNCLEAR | 3 | -1.4 | -0.467 | 0.01 |

| side | n | net R sum | net R mean | PF | win |
|---|---|---|---|---|---|
| LONG | 352 | -132.1 | -0.375 | 0.46 | 37.2% |
| SHORT | 302 | -66.0 | -0.219 | 0.67 | 38.7% |

| UTC session | n | net R sum | net R mean | PF |
|---|---|---|---|---|
| Asia 00-08 | 181 | -45.4 | -0.251 | 0.60 |
| Europe 08-16 | 264 | -122.8 | -0.465 | 0.39 |
| US 16-24 | 209 | -30.0 | -0.144 | 0.76 |

| exit reason | n | net R mean | med hold min |
|---|---|---|---|
| STOP | 333 | -1.240 | 45 |
| TIME_LIMIT | 85 | 1.418 | 480 |
| TRAIL | 236 | 0.399 | 270 |

## F. One-position rule and the sequential / independent diagnostics


**development**

| item | value |
|---|---|
| trades taken (baseline) | 1295 |
| valid triggers refused because a position was open | 1459 |
| valid triggers refused by the cooldown after close | 84 |
| suppressed per day / suppression share of all valid triggers | 1.69 / 54.4% |
| counterfactual of the position-open refusals (each simulated alone) | n=1459, mean -0.311R, win 34.7%, median hold 175 min |
| SEQUENTIAL mode (episodes wait for the slot, no cooldown after close): trades/day, net R mean | 1.53, -0.312R |
| INDEPENDENT mode (every valid trigger simulated alone, overlaps allowed): trades/day, net R mean | 1.95, -0.299R |

**validation**

| item | value |
|---|---|
| trades taken (baseline) | 654 |
| valid triggers refused because a position was open | 855 |
| valid triggers refused by the cooldown after close | 54 |
| suppressed per day / suppression share of all valid triggers | 2.49 / 58.2% |
| counterfactual of the position-open refusals (each simulated alone) | n=855, mean -0.289R, win 39.9%, median hold 170 min |
| SEQUENTIAL mode (episodes wait for the slot, no cooldown after close): trades/day, net R mean | 1.98, -0.304R |
| INDEPENDENT mode (every valid trigger simulated alone, overlaps allowed): trades/day, net R mean | 2.85, -0.307R |

## G. Exit variants (development, combined families; pre-declared selection)

| variant | BE after TP1 | n | trades/cal. day | net R mean | net R/day | PF | win | med hold | max DD | description |
|---|---|---|---|---|---|---|---|---|---|---|
| A | yes | 1329 | 1.46 | -0.339 | -0.494 | 0.50 | 43.2% | 115 | 67.8% | TP1 +1R 40% / TP2 +2R 30% / runner on the 15m structure trail |
| A | no | 1285 | 1.41 | -0.326 | -0.459 | 0.54 | 37.6% | 170 | 63.3% | TP1 +1R 40% / TP2 +2R 30% / runner on the 15m structure trail |
| B | yes | 1375 | 1.51 | -0.339 | -0.511 | 0.44 | 38.6% | 90 | 70.1% | TP1 +0.75R 30% / TP2 +1.5R 30% / runner on the 15m structure trail |
| B | no | 1295 | 1.42 | -0.320 | -0.455 | 0.52 | 35.8% | 160 | 62.7% | TP1 +0.75R 30% / TP2 +1.5R 30% / runner on the 15m structure trail |
| C | yes | 1441 | 1.58 | -0.313 | -0.495 | 0.56 | 33.7% | 75 | 68.0% | single +1.5R target with the 15m structure trail from entry |
| C | no | 1441 | 1.58 | -0.313 | -0.495 | 0.56 | 33.7% | 75 | 68.0% | single +1.5R target with the 15m structure trail from entry |
| D | yes | 1315 | 1.44 | -0.360 | -0.519 | 0.57 | 22.4% | 135 | 72.1% | pure structure: 15m swing trail from entry, no targets |
| D | no | 1315 | 1.44 | -0.360 | -0.519 | 0.57 | 22.4% | 135 | 72.1% | pure structure: 15m swing trail from entry, no targets |

## H. Threshold grids (development, family alone; stability only — nothing was selected)

| family | parameter | value | default | n | trades/day | net R mean | net R sum | PF |
|---|---|---|---|---|---|---|---|---|
| MOMENTUM_CONTINUATION | rel_vol_15m_min | 1.25 | no | 920 | 1.01 | -0.423 | -389.4 | 0.44 |
| MOMENTUM_CONTINUATION | rel_vol_15m_min | 1.50 | yes | 853 | 0.94 | -0.415 | -354.0 | 0.44 |
| MOMENTUM_CONTINUATION | rel_vol_15m_min | 2.00 | no | 732 | 0.80 | -0.374 | -273.5 | 0.48 |
| MOMENTUM_CONTINUATION | max_extension_atr5 | 2.00 | no | 807 | 0.88 | -0.377 | -304.4 | 0.48 |
| MOMENTUM_CONTINUATION | max_extension_atr5 | 3.00 | yes | 853 | 0.94 | -0.415 | -354.0 | 0.44 |
| MOMENTUM_CONTINUATION | max_extension_atr5 | 4.00 | no | 865 | 0.95 | -0.413 | -357.4 | 0.44 |
| PULLBACK_CONTINUATION | impulse_min_atr1h | 1.25 | no | 156 | 0.17 | -0.591 | -92.2 | 0.35 |
| PULLBACK_CONTINUATION | impulse_min_atr1h | 1.50 | yes | 86 | 0.09 | -0.591 | -50.8 | 0.40 |
| PULLBACK_CONTINUATION | impulse_min_atr1h | 2.00 | no | 22 | 0.02 | -0.477 | -10.5 | 0.55 |
| FAILED_BREAKOUT_REVERSAL | rel_vol_5m_min | 1.25 | no | 561 | 0.62 | -0.225 | -126.0 | 0.60 |
| FAILED_BREAKOUT_REVERSAL | rel_vol_5m_min | 1.50 | yes | 554 | 0.61 | -0.214 | -118.6 | 0.62 |
| FAILED_BREAKOUT_REVERSAL | rel_vol_5m_min | 2.00 | no | 539 | 0.59 | -0.237 | -127.9 | 0.59 |
| FLOW_OI_IMPULSE | ret_15m_min_atr15 | 0.75 | no | 256 | 0.28 | -0.169 | -43.3 | 0.71 |
| FLOW_OI_IMPULSE | ret_15m_min_atr15 | 1.00 | yes | 156 | 0.17 | -0.067 | -10.5 | 0.87 |
| FLOW_OI_IMPULSE | ret_15m_min_atr15 | 1.50 | no | 57 | 0.06 | -0.121 | -6.9 | 0.77 |

## I. Risk grid and daily budget overlay

Risk grid (validation, frozen; R results are identical across risk levels — no margin cap binds — account P&L and drawdown scale with the risk fraction):

| risk/trade | primary | net return | max DD | max DD (R) | worst day |
|---|---|---|---|---|---|
| 0.100% | no | -19.8% | 20.7% | 206.6 | -0.6% |
| 0.125% | no | -24.8% | 25.8% | 206.6 | -0.8% |
| 0.150% | yes | -29.7% | 31.0% | 206.6 | -0.9% |
| 0.200% | no | -39.6% | 41.3% | 206.6 | -1.3% |
| 0.250% | no | -49.5% | 51.7% | 206.6 | -1.6% |

Overlay grid (development, frozen variant):

| max full-risk losses/day | daily stop | default | n | trades/day | net R sum | max DD | worst day | triggers blocked | days blocked |
|---|---|---|---|---|---|---|---|---|---|
| 4 | 0.5% | no | 1287 | 1.41 | -411.7 | 62.3% | -0.7% | 10 | 21 |
| 4 | 0.8% | no | 1293 | 1.42 | -413.7 | 62.6% | -0.8% | 2 | 3 |
| 5 | 0.5% | no | 1287 | 1.41 | -411.7 | 62.3% | -0.7% | 10 | 21 |
| 5 | 0.8% | yes | 1293 | 1.42 | -413.7 | 62.6% | -0.8% | 2 | 3 |
| 6 | 0.5% | no | 1287 | 1.41 | -411.7 | 62.3% | -0.7% | 10 | 21 |
| 6 | 0.8% | no | 1293 | 1.42 | -413.7 | 62.6% | -0.8% | 2 | 3 |

Maker-entry variant (pullback family alone, development, reporting only): n=86, net R mean -0.275, PF 0.67 (taker: see D.2).

## J. Once-only OOS

NOT RUN. The pre-registered protocol runs the once-only OOS window only when the frozen specification passes the HARD gate on validation; it did not pass, so the window stays unspent for a future pre-registration.

## K. Acceptance gate (validation, frozen)

| criterion | value | passed |
|---|---|---|
| HARD: mean trades per calendar day >= 2.0 | 1.792 | FAIL |
| HARD: net expectancy after fees/slippage > 0.00R | -0.303 | FAIL |
| HARD: profit factor after costs > 1.00 | 0.550 | FAIL |
| HARD: max drawdown < 10% of equity at the primary risk | 0.310 | FAIL |
| robust: profit factor > 1.15 | 0.550 | FAIL |
| robust: no single year > 60% of profit | - | PASS |
| robust: no single regime > 60% of profit | - | PASS |
| robust: median hold within [15.0, 360.0] min | 165.000 | PASS |
| target: 3-6 trades/day on active days (share of active days in target) | 0.240 | FAIL |

HARD gate: FAILED; robust criteria: failed; classification **C**.

## L. Bottleneck analysis and next architecture

Validation: 1.79 trades per calendar day against the required 2.0; net R mean -0.303, PF 0.55 → **frequency NOT met and NO net edge after costs**.

| bottleneck | measure | value | verdict |
|---|---|---|---|
| event scarcity | first-in-cluster events per day (all families, both sides) | 5.570 | not binding |
| Stage B confirmation | episodes that ever became ACTIONABLE / episodes opened | 0.519 | moderate |
| one-position rule | valid triggers refused while a position was open, per day | 2.342 | binding |
| hold duration | median hold (min) of executed trades | 165.000 | moderate |
| costs | round-trip cost as a share of the median stop | 0.315 | binding |
| cooldowns | valid triggers refused by the cooldown after close, per day | 0.148 | not binding |
| anti-chase / expiry | share of episodes that expired without an executable trigger (MAX_AGE + ENTRY_WINDOW) | 0.185 | not binding |
| thesis invalidation | share of episodes invalidated by their own rule before a trigger | 0.343 | moderate |
| regime restrictions | share of events requiring 1h trend alignment (A) — only family A restricts by regime | - | not binding (no regime gate besides family A's 'not opposed' filter) |

Without the one-position rule: sequential mode 1.98 trades/day at -0.304R; independent mode 2.85 trades/day at -0.307R.

### L.2 Reading of the result and the next architecture

- **Edge:** gross expectancy +0.044R per trade before costs, costs 0.347R per trade (round trip 15.0 bps against a median stop of 48 bps). The signal set as specified carries no gross edge on validation; costs then make every family negative. Lowering costs alone cannot rescue a zero gross edge, and widening stops to dilute costs would lengthen holds and cut frequency further.
- **Frequency:** 1.79 trades per calendar day with one position; 855 valid triggers (58.2% of all) were refused because a position was open. Sequential episodes give 1.98/day and independent episodes 2.85/day, both at the same negative expectancy: the one-position rule is the main frequency throttle but removing it only multiplies a losing trade.
- **Where the loss sits:** STOP exits (median hold under an hour) carry the losses; TRAIL and TIME_LIMIT exits are positive. Stage B pullback entries on the momentum family (the most frequent trigger) have negative gross expectancy: the 15m break with a quick retest is, on this data, more often a failed break than a continuation.
- **What the data supports trying next (would need a NEW pre-registration; nothing here is tuned):**
  1. Shift the venue of the research to Bybit-native flow (stream the Bybit trade archive into 5m flow rows) before any further flow-based family work, so research and forward venue match.
  2. Replace the quick-retest entries with structure that showed positive gross R here: trailing winners (TRAIL/TIME_LIMIT) suggest the edge, if any, is in the continuation leg, not the entry; a family that enters AFTER a confirmed 15m higher-low/lower-high (not the first touch of the level) with a stop below that structure would have fewer, later, longer trades — this conflicts with the frequency target and must be declared as such.
  3. Cost-aware eligibility: refuse any trigger whose round-trip cost exceeds a declared share of the stop (e.g. 20%) so that the frequency statistic counts only executable trades; report the residual frequency honestly.
  4. Prospective-only liquidation families D/E on the live Bybit stream (shadow diagnostics) once several months of allLiquidation data exist; they are the only families with a mechanism that the historical data cannot test.
  5. If the owner's product requirement of >= 2 trades/day with positive net edge stands, the evidence here says a single-position 5m/15m breakout-retest engine on Binance history does not deliver it; a multi-slot (2 independent positions, strictly bounded total risk) design would reach the frequency only if a positive-edge family is found first.
