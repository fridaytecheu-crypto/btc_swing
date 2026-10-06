# BTC Swing V4 — Event & Positioning Driven Active Swing: research report

Generated 2026-10-06 06:47 UTC · window 2022-01-01 00:00 -> 2026-10-01 00:00 UTC · config `2aafd7c42d0b` · design freeze commit `56f7177c53b5` · raw result hash `a24d9edce1e8` · code `3b73f3aed6ac`

**Central question.** Do BTC positioning and participation events — deleveraging, positioning resets and participation-confirmed breakouts — provide a repeatable gross directional edge large enough to survive realistic execution costs while still supporting an active swing style of roughly 1-3 trades per day?

**Validation constraint.** 2022-01..2026-09 is development data inspected by V1-V3; no V4 result on it is untouched out-of-sample. One frozen configuration, deterministic research, no ML; the next genuine validation is forward paper testing after a freeze. Paper/backtest only; no live or paper trading engine, no exchange keys, no real money.

## 1. V1-V3 lessons

- V1 (structural setups, frozen defaults): small positive gross edge erased by costs; untouched 2025-26 holdout net negative. V2 (learned ranking of V1 candidates): no usable ranking signal. V3 (active 4H/1H/15m/5m price-structure generator): hit the activity profile (1.5 trades/day, 3 h median hold) but gross expectancy was already slightly negative and 0.66% stops made costs 0.34R per trade; nulls with the same geometry lost almost as much.
- V4 therefore (a) requires an observable positioning/participation event behind every candidate, (b) measures event edge before any execution (Stage A), (c) floors the stop at 1.5 ATR(1h) so costs are a small fraction of 1R, and (d) treats gross expectancy as the gate.

## 2. V4 hypothesis

- Deleveraging flushes (price impulse + OI contraction + participation + aggressive one-sided flow) that fail to continue, positioning resets inside a 4H trend (OI contraction plus funding/premium cooling or opposite flow, structure intact) and participation-confirmed 1H breakouts (volume, OI and taker-flow expansion without crowding) carry a repeatable signed forward edge at 4-8 h horizons; with a simple 15m confirmation, a deterministic 5m execution rule, volatility-floored structural stops and a generic exit, that edge survives realistic costs at 1-3 trades/day.

## 3. Frozen design proof

| check | value |
|---|---|
| design document | `docs/BTC_SWING_V4_DESIGN.md` and `config/btc_swing_v4.yaml`, committed at `56f7177c53b50f083a4f8765fce9cd4a956127f7` before any V4 code or result |
| config hash | `2aafd7c42d0b714c4e42435b24dd1796db104a484c4627e5d7990d0267930d35` |
| strategy / rule versions | btc_swing_v4_event_positioning 4.0.0-research; features=v4-feat-1, events=v4-event-1, exits=v4-exit-1 |
| code version at run | 3b73f3aed6ac |
| runs of the frozen configuration | one raw run; the overlay, 0.5%, cost-sensitivity and leverage-cap streams and the audits never changed a rule |
| deterministic rerun identical | yes |

## 4. Data coverage

| dataset | rows | first | last | rows in window | expected | coverage |
|---|---|---|---|---|---|---|
| perp_klines_5m | 525888 | 2021-10-01T00:00 | 2026-09-30T23:55 | 499392 | 499392 | 100.00% |
| funding | 5478 | 2021-10-01T00:00 | 2026-09-30T16:00 | 5202 | 5202 | 100.00% |
| metrics_5min | 525688 | 2021-10-01T00:00 | 2026-09-30T23:55 | 499259 | 499392 | 99.97% |
| premium_index_5m | 525015 | 2021-10-01T00:00 | 2026-09-30T23:55 | 498519 | 499392 | 99.83% |
| mark_price_5m | 524731 | 2021-10-01T00:00 | 2026-09-30T23:55 | 498235 | 499392 | 99.77% |
| native_1h | 43824 | 2021-10-01T00:00 | 2026-09-30T23:00 | 41616 | 41616 | 100.00% |
| native_4h | 10956 | 2021-10-01T00:00 | 2026-09-30T20:00 | 10404 | 10404 | 100.00% |
| native_1d | 1826 | 2021-10-01T00:00 | 2026-09-30T00:00 | 1734 | 1734 | 100.00% |

- 1H feature rows in the window: 41616; missing share of key features: oi_chg_4h_z 0.2%, fund_z 0.0%, taker_4h_z 0.0%, prem_z 0.2%, vol_z 0.0%.

## 5. PIT audit

- bars with close_time <= t; metrics/funding/premium rows with time + latency <= t; rolling z-scores over previous rows only; fill at the next 5m open; trail moves applied from the next bar.
- Deterministic rerun: yes. Truncation audit with all data after 2024-05-17T00:00 removed: decisions identical yes (249697 rows); events identical yes (532 events up to two days before the cut).
- Resampling oracle vs native archive bars: 1h: 43824 bars, 3 differ, 4h: 10956 bars, 2 differ, 1d: 1826 bars, 0 differ. Liquidation basis: mark.

## 6. Event definitions (frozen; z = rolling z-score over the previous 30 days of 1H rows)

- A DELEVERAGING_REVERSAL (LONG): 4h return z <= -2.0, OI 4h-change z <= -2.0, 4h volume z >= +1.0, 4h taker-imbalance z <= -1.0; thesis window 12 h; 15m EMA21 reclaim with the 5m close at or above the flush extreme; zone [flush extreme, +1.5 ATR]; structural stop flush extreme - 0.25 ATR. SHORT mirrored.
- B POSITIONING_RESET_CONTINUATION (LONG): 4H trend UP; 1H close below EMA21 with 4h return z <= -0.5 and the close above the last 4H swing low; OI 24h-change z <= -1.0 plus at least one of funding cooled (max z over 48 h minus z now >= 1.0), premium cooled (same), 4h taker-imbalance z <= -1.0; window 24 h; 15m EMA21 reclaim with 1h taker buy ratio > 0.5; zone [24-bar low, +1.5 ATR]; structural stop 4H swing low - 0.25 ATR. SHORT mirrored.
- C PARTICIPATION_BREAKOUT (LONG): 1H close above the previous 48-bar high; 1h volume z >= +2.0; OI 4h-change z >= +1.0; 1h taker-imbalance z >= +1.0; funding z and premium z <= +2.0; window 12 h; acceptance = two consecutive 15m closes above the level after the event bar, latest <= level + 1.5 ATR; zone [level - 0.25 ATR, level + 1.5 ATR]; structural stop level - 0.5 ATR. SHORT mirrored.
- All: the stop finalised at the trigger bar is the farther of the structural stop and a 1.5 ATR(1h) volatility floor from the trigger-bar close, capped at 4 ATR; execution = a completed 5m close inside the zone after confirmation (fill next open); one net exposure; same anchor not re-armed within 24 h.

## 7. Event frequency (Stage A, every 1H bar, both sides, no slot)

| family | side | events |
|---|---|---|
| DELEVERAGING_REVERSAL | LONG | 154 |
| DELEVERAGING_REVERSAL | SHORT | 105 |
| PARTICIPATION_BREAKOUT | LONG | 51 |
| PARTICIPATION_BREAKOUT | SHORT | 20 |
| POSITIONING_RESET_CONTINUATION | LONG | 345 |
| POSITIONING_RESET_CONTINUATION | SHORT | 287 |

- Events: 962 (460 first-in-cluster, i.e. no same-family/side event in the previous 4 hours); 0.56 events/day over the window vs 0.10 trades/day after confirmation, execution and the single slot.

## 8. Event forward-return analysis (signed in the event direction, from the event bar close)

Pooled, all events:

| horizon | n | mean signed return | median | hit rate | t |
|---|---|---|---|---|---|
| 0.5 h | 962 | 0.035% | 0.065% | 57.6% | 1.85 |
| 1 h | 962 | 0.059% | 0.072% | 57.8% | 2.20 |
| 2 h | 962 | 0.024% | 0.106% | 57.2% | 0.72 |
| 4 h | 962 | -0.012% | 0.086% | 53.1% | -0.26 |
| 8 h | 962 | 0.041% | 0.064% | 52.2% | 0.71 |
| 12 h | 961 | 0.077% | 0.024% | 50.5% | 1.15 |
| 24 h | 961 | 0.133% | -0.031% | 49.6% | 1.33 |

Pooled, first-in-cluster events only:

| horizon | n | mean signed return | median | hit rate | t |
|---|---|---|---|---|---|
| 0.5 h | 460 | 0.053% | 0.083% | 60.7% | 1.80 |
| 1 h | 460 | 0.098% | 0.084% | 59.1% | 2.27 |
| 2 h | 460 | 0.055% | 0.095% | 57.8% | 1.09 |
| 4 h | 460 | 0.053% | 0.131% | 54.8% | 0.82 |
| 8 h | 460 | 0.083% | 0.098% | 52.4% | 0.96 |
| 12 h | 459 | 0.170% | 0.138% | 52.3% | 1.66 |
| 24 h | 459 | 0.233% | 0.031% | 51.0% | 1.53 |

Unconditional BTC forward return of every 1H bar over the same horizons (unsigned drift and dispersion):

| horizon | mean | std | mean |return| |
|---|---|---|---|
| 0.5 h | 0.004% | 0.38% | 0.24% |
| 1 h | 0.003% | 0.54% | 0.34% |
| 2 h | 0.006% | 0.76% | 0.47% |
| 4 h | 0.011% | 1.06% | 0.68% |
| 8 h | 0.022% | 1.50% | 0.98% |
| 12 h | 0.034% | 1.84% | 1.23% |
| 24 h | 0.068% | 2.63% | 1.81% |

DELEVERAGING_REVERSAL (n=259; mean MFE 1.71 ATR, mean MAE -3.03 ATR over 24 h):

| horizon | n | mean signed return | median | hit rate | t |
|---|---|---|---|---|---|
| 0.5 h | 259 | -0.038% | 0.018% | 51.0% | -0.78 |
| 1 h | 259 | 0.016% | 0.056% | 56.0% | 0.25 |
| 2 h | 259 | -0.121% | 0.057% | 53.3% | -1.51 |
| 4 h | 259 | -0.187% | 0.132% | 54.8% | -1.84 |
| 8 h | 259 | -0.283% | -0.079% | 48.3% | -2.33 |
| 12 h | 259 | -0.318% | -0.146% | 46.3% | -2.37 |
| 24 h | 259 | -0.399% | -0.263% | 44.4% | -1.90 |

DELEVERAGING_REVERSAL LONG:

| horizon | n | mean signed return | median | hit rate | t |
|---|---|---|---|---|---|
| 0.5 h | 154 | -0.046% | -0.019% | 48.7% | -0.68 |
| 1 h | 154 | -0.002% | 0.085% | 56.5% | -0.03 |
| 2 h | 154 | -0.043% | 0.115% | 57.1% | -0.45 |
| 4 h | 154 | -0.128% | 0.154% | 58.4% | -1.12 |
| 8 h | 154 | -0.253% | 0.013% | 50.6% | -1.73 |
| 12 h | 154 | -0.134% | 0.137% | 53.2% | -0.77 |
| 24 h | 154 | -0.013% | 0.009% | 50.0% | -0.05 |

DELEVERAGING_REVERSAL SHORT:

| horizon | n | mean signed return | median | hit rate | t |
|---|---|---|---|---|---|
| 0.5 h | 105 | -0.026% | 0.036% | 54.3% | -0.37 |
| 1 h | 105 | 0.044% | 0.045% | 55.2% | 0.37 |
| 2 h | 105 | -0.235% | -0.024% | 47.6% | -1.71 |
| 4 h | 105 | -0.275% | -0.009% | 49.5% | -1.47 |
| 8 h | 105 | -0.326% | -0.239% | 44.8% | -1.56 |
| 12 h | 105 | -0.588% | -0.824% | 36.2% | -2.81 |
| 24 h | 105 | -0.964% | -0.643% | 36.2% | -2.62 |

DELEVERAGING_REVERSAL by strength tercile (1 = weakest):

| tercile | n | 1h | 4h | 8h | 24h |
|---|---|---|---|---|---|
| 1 | 87 | 0.066% | -0.168% | -0.187% | -0.671% |
| 2 | 86 | -0.032% | -0.142% | -0.380% | -0.159% |
| 3 | 86 | 0.015% | -0.253% | -0.282% | -0.362% |

PARTICIPATION_BREAKOUT (n=71; mean MFE 4.98 ATR, mean MAE -2.25 ATR over 24 h):

| horizon | n | mean signed return | median | hit rate | t |
|---|---|---|---|---|---|
| 0.5 h | 71 | 0.390% | 0.222% | 67.6% | 4.15 |
| 1 h | 71 | 0.273% | 0.210% | 63.4% | 2.31 |
| 2 h | 71 | 0.284% | 0.330% | 64.8% | 2.06 |
| 4 h | 71 | 0.279% | 0.297% | 60.6% | 1.59 |
| 8 h | 71 | 0.825% | 0.552% | 66.2% | 3.06 |
| 12 h | 70 | 1.267% | 1.092% | 68.6% | 3.72 |
| 24 h | 70 | 1.315% | 0.852% | 64.3% | 3.02 |

PARTICIPATION_BREAKOUT LONG:

| horizon | n | mean signed return | median | hit rate | t |
|---|---|---|---|---|---|
| 0.5 h | 51 | 0.444% | 0.193% | 68.6% | 3.67 |
| 1 h | 51 | 0.417% | 0.236% | 68.6% | 3.21 |
| 2 h | 51 | 0.436% | 0.330% | 66.7% | 3.05 |
| 4 h | 51 | 0.527% | 0.495% | 66.7% | 2.89 |
| 8 h | 51 | 1.015% | 0.660% | 68.6% | 3.11 |
| 12 h | 50 | 1.393% | 1.196% | 74.0% | 3.78 |
| 24 h | 50 | 1.542% | 1.155% | 70.0% | 2.89 |

PARTICIPATION_BREAKOUT SHORT:

| horizon | n | mean signed return | median | hit rate | t |
|---|---|---|---|---|---|
| 0.5 h | 20 | 0.253% | 0.300% | 65.0% | 1.99 |
| 1 h | 20 | -0.092% | -0.038% | 50.0% | -0.38 |
| 2 h | 20 | -0.105% | 0.298% | 60.0% | -0.33 |
| 4 h | 20 | -0.353% | -0.153% | 45.0% | -0.90 |
| 8 h | 20 | 0.341% | 0.204% | 60.0% | 0.73 |
| 12 h | 20 | 0.951% | 0.356% | 55.0% | 1.23 |
| 24 h | 20 | 0.749% | 0.353% | 50.0% | 1.01 |

PARTICIPATION_BREAKOUT by strength tercile (1 = weakest):

| tercile | n | 1h | 4h | 8h | 24h |
|---|---|---|---|---|---|
| 1 | 24 | 0.237% | 0.431% | 1.227% | 1.488% |
| 2 | 23 | 0.242% | 0.288% | 0.419% | 0.891% |
| 3 | 24 | 0.340% | 0.119% | 0.811% | n/a |

POSITIONING_RESET_CONTINUATION (n=632; mean MFE 2.30 ATR, mean MAE -2.15 ATR over 24 h):

| horizon | n | mean signed return | median | hit rate | t |
|---|---|---|---|---|---|
| 0.5 h | 632 | 0.025% | 0.062% | 59.2% | 1.47 |
| 1 h | 632 | 0.052% | 0.067% | 57.9% | 1.90 |
| 2 h | 632 | 0.055% | 0.105% | 57.9% | 1.49 |
| 4 h | 632 | 0.027% | 0.055% | 51.6% | 0.54 |
| 8 h | 632 | 0.086% | 0.055% | 52.2% | 1.32 |
| 12 h | 632 | 0.108% | 0.009% | 50.2% | 1.44 |
| 24 h | 632 | 0.221% | 0.007% | 50.2% | 1.91 |

POSITIONING_RESET_CONTINUATION LONG:

| horizon | n | mean signed return | median | hit rate | t |
|---|---|---|---|---|---|
| 0.5 h | 345 | 0.043% | 0.066% | 61.2% | 2.42 |
| 1 h | 345 | 0.082% | 0.102% | 62.6% | 3.21 |
| 2 h | 345 | 0.127% | 0.149% | 62.9% | 3.43 |
| 4 h | 345 | 0.174% | 0.173% | 58.8% | 3.49 |
| 8 h | 345 | 0.250% | 0.208% | 58.8% | 3.66 |
| 12 h | 345 | 0.263% | 0.176% | 55.1% | 3.27 |
| 24 h | 345 | 0.358% | 0.206% | 55.7% | 3.26 |

POSITIONING_RESET_CONTINUATION SHORT:

| horizon | n | mean signed return | median | hit rate | t |
|---|---|---|---|---|---|
| 0.5 h | 287 | 0.003% | 0.035% | 56.8% | 0.10 |
| 1 h | 287 | 0.016% | 0.045% | 52.3% | 0.31 |
| 2 h | 287 | -0.032% | 0.018% | 51.9% | -0.48 |
| 4 h | 287 | -0.149% | -0.123% | 42.9% | -1.61 |
| 8 h | 287 | -0.111% | -0.199% | 44.3% | -0.96 |
| 12 h | 287 | -0.079% | -0.234% | 44.3% | -0.59 |
| 24 h | 287 | 0.056% | -0.462% | 43.6% | 0.26 |

POSITIONING_RESET_CONTINUATION by strength tercile (1 = weakest):

| tercile | n | 1h | 4h | 8h | 24h |
|---|---|---|---|---|---|
| 1 | 211 | 0.042% | -0.053% | 0.017% | 0.367% |
| 2 | 210 | 0.071% | -0.010% | 0.015% | 0.158% |
| 3 | 211 | 0.043% | 0.145% | 0.224% | 0.136% |

By year (pooled events):

- 2022: n=238, 4h 0.162% (t 1.40), 8h 0.328% (t 2.27)
- 2023: n=221, 4h -0.039% (t -0.45), 8h -0.040% (t -0.39)
- 2024: n=173, 4h 0.182% (t 1.75), 8h 0.358% (t 2.40)
- 2025: n=177, 4h -0.236% (t -2.95), 8h -0.223% (t -2.20)
- 2026: n=153, 4h -0.203% (t -2.15), 8h -0.340% (t -2.74)

- Stage A gate (pre-declared): 4 h mean -0.012% (t -0.26), 8 h mean 0.041% (t 0.71), top vs bottom strength tercile at 8 h 0.251% vs 0.352% -> passed: no.

## 9. Candidate / trade conversion

| stage | count |
|---|---|
| events (Stage A) | 962 |
| episodes opened by the lifecycle (after anchor de-duplication) | 427 |
| triggers blocked by an open position | 16 |
| risk-rejected | 0 |
| trades | 176 |

Episode end reasons:

| end reason | n |
|---|---|
| WATCH_TIMEOUT | 123 |
| LEVEL_BREACHED | 112 |
| STOP | 79 |
| TRAIL | 52 |
| TIME_LIMIT | 45 |
| BLOCKED_POSITION_OPEN | 16 |

## 10. Trade frequency

| metric | value |
|---|---|
| trades / day | 0.101 |
| trades / week | 0.71 |
| trades / month | 3.1 |
| median hours between entries | 168.5 |
| days with 0 / 1 / 2 / 3+ trades | 90% / 10% / 0% / 0% (max 2/day) |

| year | n | trades/day | trades/month | days 0 / 1 / 2 / 3+ |
|---|---|---|---|---|
| 2022 | 40 | 0.110 | 3.3 | 89% / 10% / 0% / 0% (max 2/day) |
| 2023 | 38 | 0.104 | 3.2 | 90% / 10% / 0% / 0% (max 1/day) |
| 2024 | 26 | 0.071 | 2.2 | 93% / 7% / 0% / 0% (max 1/day) |
| 2025 | 36 | 0.099 | 3.0 | 90% / 10% / 0% / 0% (max 1/day) |
| 2026 | 36 | 0.132 | 4.0 | 87% / 13% / 0% / 0% (max 1/day) |

## 11. Holding period

| quantile | hours |
|---|---|
| p10 | 2.6 |
| p25 | 7.1 |
| p50 | 22.8 |
| p75 | 48.0 |
| p90 | 48.0 |

| bucket | share | n |
|---|---|---|
| 0-1h | 5% | 8 |
| 1-2h | 2% | 3 |
| 2-6h | 16% | 29 |
| 6-12h | 9% | 15 |
| 12-24h | 21% | 37 |
| 24-48h | 22% | 39 |
| >48h | 26% | 45 |

## 12. Stop geometry and cost-to-risk

| metric | value |
|---|---|
| median / mean stop (% of price) | 1.54 / 2.07 (V3: 0.66) |
| p10 / p90 stop % | 0.89 / 3.93 |
| median stop in ATR(1h) | 1.70 |
| share of stops set by the volatility floor | 32% |
| round-trip cost assumed (% of notional) | 0.170 |
| median / p90 cost as % of stop | 11.0% / 19.2% |
| share of trades with cost > 25% of stop | 3.4% |
| median / mean cost drag (R) | 0.065 / 0.072 |

| family | n | median stop % |
|---|---|---|
| DELEVERAGING_REVERSAL | 39 | 1.54 |
| PARTICIPATION_BREAKOUT | 40 | 1.07 |
| POSITIONING_RESET_CONTINUATION | 97 | 2.12 |

## 13. Gross expectancy (before fees, slippage and funding)

| population | n | gross R | net R | PF (net) | win |
|---|---|---|---|---|---|
| all trades | 176 | 0.016 | -0.096 | 0.81 | 48% |
| DELEVERAGING_REVERSAL ALL | 39 | -0.132 | -0.230 | 0.63 | 38% |
| DELEVERAGING_REVERSAL LONG | 23 | 0.027 | -0.081 | 0.85 | 48% |
| DELEVERAGING_REVERSAL SHORT | 16 | -0.360 | -0.444 | 0.42 | 25% |
| POSITIONING_RESET_CONTINUATION ALL | 97 | -0.050 | -0.150 | 0.69 | 48% |
| POSITIONING_RESET_CONTINUATION LONG | 57 | 0.151 | 0.035 | 1.08 | 56% |
| POSITIONING_RESET_CONTINUATION SHORT | 40 | -0.339 | -0.414 | 0.31 | 38% |
| PARTICIPATION_BREAKOUT ALL | 40 | 0.318 | 0.171 | 1.34 | 55% |
| PARTICIPATION_BREAKOUT LONG | 29 | 0.460 | 0.305 | 1.63 | 59% |
| PARTICIPATION_BREAKOUT SHORT | 11 | -0.057 | -0.181 | 0.66 | 45% |

- Benchmark for economic interest (not a tuning target): gross expectancy >= +0.15R.

## 14. Costs

| component | primary (frozen) | Bybit-style sensitivity (maker targets), NOT for classification |
|---|---|---|
| trades | 176 | 176 |
| gross P&L before slippage | 71 | 71 |
| slippage | -179 | -179 |
| fees | -304 | -310 |
| funding | -16 | -16 |
| net P&L | -428 | -434 |
| gross R / net R | 0.016 / -0.096 | 0.016 / -0.097 |
| cost drag per trade (R) | 0.112 | 0.113 |
| funding events | 549 | 549 |

## 15. Net expectancy (raw stream, 0.25% risk)

| period | n | trades/day | win | gross R | net R | PF | net P&L | max DD | t |
|---|---|---|---|---|---|---|---|---|---|
| combined | 176 | 0.10 | 48% | 0.016 | -0.095 | 0.81 | -428 | -5.47% | -1.18 |

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| all trades | 176 | 48% | -0.095 | -0.075 | -16.7 | 0.81 | 1.26 | -0.78 | 25.4 | n/a | -428 |

## 16. Yearly results

| period | n | trades/day | win | gross R | net R | PF | net P&L | max DD | t |
|---|---|---|---|---|---|---|---|---|---|
| 2022 | 40 | 0.11 | 40% | -0.103 | -0.191 | 0.70 | -193 | -2.66% | -1.08 |
| 2023 | 38 | 0.10 | 45% | 0.138 | 0.003 | 1.00 | 1 | -1.31% | 0.01 |
| 2024 | 26 | 0.07 | 69% | 0.516 | 0.428 | 2.85 | 281 | -0.48% | 2.25 |
| 2025 | 36 | 0.10 | 50% | -0.090 | -0.207 | 0.58 | -190 | -2.30% | -1.35 |
| 2026 | 36 | 0.13 | 42% | -0.235 | -0.356 | 0.41 | -327 | -3.34% | -2.41 |

## 17. Quarterly stability

| period | n | trades/day | win | gross R | net R | PF | net P&L | max DD | t |
|---|---|---|---|---|---|---|---|---|---|
| 2022-Q1 | 12 | 0.13 | 25% | -0.428 | -0.510 | 0.37 | -155 | -2.17% | -1.69 |
| 2022-Q2 | 10 | 0.11 | 50% | -0.012 | -0.081 | 0.85 | -21 | -0.73% | -0.23 |
| 2022-Q3 | 10 | 0.11 | 40% | -0.158 | -0.242 | 0.63 | -61 | -1.02% | -0.70 |
| 2022-Q4 | 8 | 0.09 | 50% | 0.339 | 0.217 | 1.49 | 44 | -0.29% | 0.47 |
| 2023-Q1 | 13 | 0.14 | 54% | 0.331 | 0.223 | 1.43 | 73 | -1.14% | 0.54 |
| 2023-Q2 | 6 | 0.07 | 50% | 0.317 | 0.204 | 1.93 | 31 | -0.33% | 0.58 |
| 2023-Q3 | 9 | 0.10 | 33% | -0.285 | -0.452 | 0.23 | -104 | -1.04% | -1.74 |
| 2023-Q4 | 10 | 0.11 | 40% | 0.162 | 0.005 | 1.01 | 1 | -0.60% | 0.01 |
| 2024-Q1 | 8 | 0.09 | 50% | 0.313 | 0.220 | 2.12 | 44 | -0.33% | 0.65 |
| 2024-Q2 | 3 | 0.03 | 67% | 0.793 | 0.667 | 2.76 | 51 | -0.29% | 0.73 |
| 2024-Q3 | 7 | 0.08 | 71% | 0.468 | 0.404 | 2.31 | 71 | -0.27% | 1.03 |
| 2024-Q4 | 8 | 0.09 | 88% | 0.656 | 0.566 | 4.96 | 114 | -0.29% | 1.90 |
| 2025-Q1 | 10 | 0.11 | 60% | 0.136 | 0.078 | 1.22 | 20 | -0.65% | 0.24 |
| 2025-Q2 | 9 | 0.10 | 44% | -0.084 | -0.211 | 0.55 | -48 | -0.77% | -0.70 |
| 2025-Q3 | 12 | 0.13 | 50% | -0.290 | -0.464 | 0.20 | -143 | -1.66% | -2.17 |
| 2025-Q4 | 5 | 0.05 | 40% | -0.070 | -0.155 | 0.76 | -19 | -0.53% | -0.28 |
| 2026-Q1 | 11 | 0.12 | 45% | -0.043 | -0.115 | 0.77 | -33 | -0.57% | -0.37 |
| 2026-Q2 | 14 | 0.15 | 29% | -0.450 | -0.594 | 0.21 | -212 | -2.28% | -2.76 |
| 2026-Q3 | 11 | 0.12 | 55% | -0.151 | -0.294 | 0.42 | -82 | -0.82% | -1.17 |

## 18. LONG vs SHORT

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L | gross R | cost drag R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| LONG | 109 | 55% | 0.082 | 0.181 | 9.0 | 1.18 | 1.52 | -0.72 | 26.2 | n/a | 224 | 0.208 | 0.127 |
| SHORT | 67 | 36% | -0.383 | -1.039 | -25.6 | 0.39 | 0.83 | -0.88 | 24.1 | n/a | -653 | -0.297 | 0.088 |

## 19. Family results

| family | side | events | trades | trades/month | win | gross R | net R | PF | MFE R | MAE R | median hold h | median stop % | cost drag R | DD-window P&L | P&L share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| DELEVERAGING REVERSAL | ALL | 259 | 39 | 0.68 | 38% | -0.132 | -0.230 | 0.63 | 1.16 | -0.90 | 18.8 | 1.54 | 0.100 | -109 | 53% |
| DELEVERAGING REVERSAL | LONG | 154 | 23 | 0.40 | 48% | 0.027 | -0.081 | 0.85 | 1.21 | -0.82 | 17.4 | 1.47 | 0.108 | -26 | 11% |
| DELEVERAGING REVERSAL | SHORT | 105 | 16 | 0.28 | 25% | -0.360 | -0.444 | 0.42 | 1.09 | -1.01 | 20.7 | 1.63 | 0.087 | -84 | 42% |
| POSITIONING RESET CONTINUATION | ALL | 632 | 97 | 1.70 | 48% | -0.050 | -0.150 | 0.69 | 0.96 | -0.75 | 37.5 | 2.12 | 0.101 | -378 | 87% |
| POSITIONING RESET CONTINUATION | LONG | 345 | 57 | 1.00 | 56% | 0.151 | 0.035 | 1.08 | 1.18 | -0.68 | 40.2 | 2.06 | 0.118 | -214 | -11% |
| POSITIONING RESET CONTINUATION | SHORT | 287 | 40 | 0.70 | 38% | -0.339 | -0.414 | 0.31 | 0.64 | -0.84 | 31.9 | 2.62 | 0.078 | -164 | 98% |
| PARTICIPATION BREAKOUT | ALL | 71 | 40 | 0.70 | 55% | 0.318 | 0.171 | 1.34 | 2.09 | -0.75 | 13.7 | 1.07 | 0.149 | -67 | -40% |
| PARTICIPATION BREAKOUT | LONG | 51 | 29 | 0.51 | 59% | 0.460 | 0.305 | 1.63 | 2.44 | -0.71 | 14.8 | 1.06 | 0.158 | -43 | -52% |
| PARTICIPATION BREAKOUT | SHORT | 20 | 11 | 0.19 | 45% | -0.057 | -0.181 | 0.66 | 1.15 | -0.85 | 6.8 | 1.29 | 0.126 | -23 | 12% |

- Every family and side is shown (zero rows included); none is hidden.

## 20. Event-strength diagnostics (trades, by event-strength tercile within family)

DELEVERAGING_REVERSAL (n=39):

| tercile | n | gross R | net R |
|---|---|---|---|
| 1 | 13 | -0.190 | -0.252 |
| 2 | 13 | -0.418 | -0.479 |
| 3 | 13 | 0.104 | 0.041 |

PARTICIPATION_BREAKOUT (n=40):

| tercile | n | gross R | net R |
|---|---|---|---|
| 1 | 14 | 0.441 | 0.351 |
| 2 | 13 | 0.348 | 0.240 |
| 3 | 13 | 0.006 | -0.091 |

POSITIONING_RESET_CONTINUATION (n=97):

| tercile | n | gross R | net R |
|---|---|---|---|
| 1 | 33 | -0.185 | -0.244 |
| 2 | 32 | 0.183 | 0.128 |
| 3 | 32 | -0.252 | -0.331 |

## 21. OI / funding / taker / basis analysis (snapshot at entry; means for winners vs losers; univariate Spearman vs net R)

| feature | n | mean winners | mean losers | Spearman vs net R |
|---|---|---|---|---|
| funding_rate_last | 176 | 0.0001 | 0.0000 | -0.005 |
| funding_rate_mean_3 | 176 | 0.0000 | 0.0000 | 0.046 |
| oi_change_1h_pct | 176 | 0.0050 | 0.2417 | -0.095 |
| oi_change_4h_pct | 176 | 0.3738 | 0.5730 | -0.088 |
| oi_change_24h_pct | 176 | -1.3134 | -2.3498 | 0.034 |
| long_short_ratio_accounts | 173 | 1.3737 | 1.4417 | -0.013 |
| top_trader_ls_positions | 139 | 1.4842 | 1.4055 | 0.066 |
| taker_long_short_vol_ratio | 160 | 1.0452 | 1.1475 | -0.014 |
| taker_buy_ratio_1h | 176 | 0.5115 | 0.4977 | 0.073 |
| taker_buy_ratio_4h | 176 | 0.4989 | 0.4975 | -0.017 |
| premium_index | 176 | -0.0004 | -0.0004 | -0.078 |
| premium_mean_1h | 176 | -0.0004 | -0.0004 | -0.077 |
| last_minus_mark_pct | 176 | -0.0014 | -0.0024 | 0.035 |
| volume_accel_5m | 176 | 0.8847 | 1.1157 | -0.096 |
| volume_accel_1h | 176 | 1.5510 | 1.3401 | 0.044 |

## 22. Regime analysis (4H trend state x 1H ATR percentile state at the trigger bar; reported, not gated)

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| DOWN_COMPRESSION | 4 | 75% | -0.131 | 0.063 | -0.5 | 0.51 | 0.63 | -0.78 | 34.6 | n/a | -13 |
| DOWN_EXPANSION | 24 | 33% | -0.216 | -1.038 | -5.2 | 0.66 | 1.36 | -0.98 | 22.0 | n/a | -130 |
| DOWN_NORMAL | 35 | 37% | -0.381 | -1.045 | -13.3 | 0.41 | 0.79 | -0.82 | 25.9 | n/a | -340 |
| NEUTRAL_COMPRESSION | 4 | 25% | -0.721 | -1.110 | -2.9 | 0.16 | 0.68 | -1.01 | 13.9 | n/a | -74 |
| NEUTRAL_EXPANSION | 11 | 55% | 0.139 | 0.244 | 1.5 | 1.33 | 1.46 | -0.56 | 21.7 | n/a | 38 |
| NEUTRAL_NORMAL | 12 | 58% | 0.059 | 0.235 | 0.7 | 1.16 | 1.27 | -0.81 | 24.2 | n/a | 18 |
| UP_COMPRESSION | 14 | 43% | -0.344 | -0.548 | -4.8 | 0.40 | 0.94 | -0.94 | 27.3 | n/a | -122 |
| UP_EXPANSION | 27 | 56% | 0.210 | 0.416 | 5.7 | 1.45 | 1.93 | -0.67 | 24.4 | n/a | 143 |
| UP_NORMAL | 45 | 56% | 0.048 | 0.105 | 2.1 | 1.11 | 1.33 | -0.68 | 28.3 | n/a | 52 |

Stage A by regime (4 h / 8 h signed mean, n): DOWN_COMPRESSION: -0.040% / -0.117% (n=19); DOWN_EXPANSION: -0.215% / -0.164% (n=230); DOWN_NORMAL: -0.135% / -0.098% (n=156); NEUTRAL_EXPANSION: 0.131% / 0.552% (n=62); NEUTRAL_NORMAL: 0.505% / 0.697% (n=11); UP_COMPRESSION: 0.063% / -0.117% (n=54); UP_EXPANSION: 0.039% / 0.048% (n=222); UP_NORMAL: 0.164% / 0.234% (n=208).

## 23. MFE / MAE

| metric | value |
|---|---|
| mean_MFE_R | 1.259 |
| median_MFE_R | 0.758 |
| mean_MAE_R | -0.781 |
| median_MAE_R | -0.866 |
| worst_MAE_R | -2.037 |
| mfe_capture | -0.075 |
| tp1_hit_rate | 38.6% |
| tp2_hit_rate | 19.9% |
| hit_rate_1R | 38.6% |
| hit_rate_1.5R | 27.3% |
| hit_rate_2R | 19.9% |
| hit_rate_3R | 10.2% |

## 24. Exit outcomes

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| STOP | 79 | 0% | -1.116 | -1.105 | -88.2 | 0.00 | 0.35 | -1.21 | 11.7 | n/a | -2240 |
| TIME_LIMIT | 45 | 71% | 0.497 | 0.232 | 22.4 | 9.44 | 1.33 | -0.46 | 48.0 | n/a | 566 |
| TRAIL | 52 | 100% | 0.945 | 0.896 | 49.1 | inf | 2.59 | -0.41 | 26.7 | n/a | 1246 |

- TP1 +1R (40%, breakeven after), TP2 +2R (30%), remainder trails the 1H swing - 0.5 ATR(1h), 48 h cap; not optimised.

## 25. Account simulation

| stream | final equity | return | CAGR | max DD (trades) | max DD (daily) | Sharpe | Sortino | longest losing streak | trades/week |
|---|---|---|---|---|---|---|---|---|---|
| raw, 0.25% risk (default) | 9571.87 | -4.28% | -0.92% | -5.47% | -5.47% | -0.59 | -0.75 | 8 | 0.71 |
| safety overlay, 0.25% risk | 9571.87 | -4.28% | -0.92% | -5.47% | -5.47% | -0.59 | -0.75 | 8 | 0.71 |
| raw, 0.5% risk (reporting only) | 9143.74 | -8.56% | -1.87% | -10.80% | -10.80% | -0.58 | -0.75 | 8 | 0.71 |

- Sequential single-slot account; fixed research equity for sizing; returns beyond -100% denote ruin of the research account.

## 26. Drawdown

| metric | value |
|---|---|
| max drawdown (trade curve / daily mtm) | -5.47% / -5.47% |
| max drawdown (USDT) | -553 |
| drawdown window trades | 59 |
| family contribution inside the window | POSITIONING_RESET_CONTINUATION -378 (n=37); DELEVERAGING_REVERSAL -109 (n=5); PARTICIPATION_BREAKOUT -67 (n=17) |
| longest losing / winning streak | 8 / 6 |

## 27. Daily safety overlay (reported separately)

| metric | raw | overlay |
|---|---|---|
| trades | 176 | 176 |
| entries blocked by the overlay | 0 | 0 |
| net P&L | -428 | -428 |
| max DD (trade curve) | -5.47% | -5.47% |

## 28. Leverage / liquidation

| metric | value |
|---|---|
| leverage used (trades per level) | 1x: 142, 2x: 33, 3x: 1 |
| mean / max leverage | 1.20 / 3 |
| mean notional (USDT) | 1725 |
| mean stop distance % | 2.07 |
| min / median stop-to-liquidation ratio | 11.6 / 55.4 |
| min liquidation distance % | 32.83 |
| liquidations | 0 |

| cap | trades | risk rejected | mean leverage | mean R | return | max DD | min liq distance % | liquidations |
|---|---|---|---|---|---|---|---|---|
| 1x | 176 | 0 | 1.00 | -0.095 | -3.47% | -4.76% | 99.50 | 0 |
| 2x | 176 | 0 | 1.19 | -0.095 | -4.25% | -5.44% | 49.50 | 0 |
| 3x | 176 | 0 | 1.20 | -0.095 | -4.28% | -5.47% | 32.83 | 0 |
| 5x | 176 | 0 | 1.20 | -0.095 | -4.28% | -5.47% | 32.83 | 0 |
| 10x | 176 | 0 | 1.20 | -0.095 | -4.28% | -5.47% | 32.83 | 0 |

## 29. Null comparison

| series | n | mean net R | mean gross R | win | strategy - null (R) | z | P(null >= strategy) | signed BTC drift |
|---|---|---|---|---|---|---|---|---|
| V4 strategy | 176 | -0.095 | -0.023 | 47.7% |  |  |  | 0.0011 |
| null (time-matched) | 1760 | -0.078 | -0.008 | 48.9% | -0.017 | -0.23 | 60% | 0.0001 |
| null (regime-matched) | 1760 | -0.063 | 0.008 | 49.3% | -0.032 | -0.62 | 60% | 0.0003 |

## 30. Outlier dependence

| metric | value |
|---|---|
| mean net R | -0.095 |
| without the best 3 / best 5 | -0.145 / -0.173 |
| without the worst 5 | -0.061 |
| best 5 trades' share of gross profit | 17% |
| best quarter / share of net P&L | 2024-Q4 / n/a |
| best family / share of net P&L | PARTICIPATION_BREAKOUT / n/a |

## 31. Limitations

- Development data (2022-2026 inspected by V1-V3); pre-registration limits researcher degrees of freedom but cannot make results out-of-sample.
- Metrics (OI, long/short, taker ratios) are 5-minute archive rows with observation time as published; funding every 8 h; one instrument; z-scores against a 30-day trailing window are a modelling choice frozen in advance.
- Fill model: next 5m open plus fixed slippage; taker fees on every fill in the primary model; no latency or partial-fill model; liquidation on mark price.
- Event clusters (consecutive hours of the same flush) are auto-correlated; first-in-cluster tables and t-statistics should be read together.
- The overlay, the 0.5% and the Bybit-style streams are reporting views; the classification uses the raw 0.25% stream with the frozen primary costs.

## 32. Recommendation and pre-declared criteria

| # | criterion | met | evidence |
|---|---|---|---|
| 0 | Stage A gate: pooled signed forward return at 4 h and 8 h > 0 with t >= 2.0, top strength tercile >= bottom at 8 h | no | 4h mean -0.0001 (t -0.26); 8h mean +0.0004 (t 0.71); terciles top +0.0025 vs bottom +0.0035 |
| 1 | gross expectancy >= +0.15R | no | +0.016R |
| 2 | net expectancy >= +0.08R | no | -0.095R (n=176) |
| 3 | profit factor >= 1.20 | no | 0.81 |
| 4 | net > 0 in >= 4 of 5 years and >= 60% of quarters with >= 10 trades | no | 2/5 years; 30% of 10 quarters |
| 5 | cost drag <= 50% of gross expectancy | no | gross +0.016R, net -0.095R |
| 6 | max drawdown <= 12% | yes | 5.47% |
| 7 | 0.5-3.0 trades/day and median hold 2-12 h | no | 0.10 trades/day; median hold 22.8 h |
| 8 | net without the 5 best trades >= +0.04R and no family > 60% of net P&L | no | without best 5 -0.173R; best family share nan% |
| 9 | beats the time-matched and regime-matched nulls (net R) | no | strategy -0.095R vs null time -0.078R / regime -0.063R |

**C — NO ROBUST EVENT EDGE**

- Criteria 0, 1, 2, 3, 4, 5, 7, 8, 9 fail and the B conditions do not hold: the events, as frozen, do not produce a reliable predictive edge after costs. No parameter tweak, family removal or re-run is proposed. Families: DELEVERAGING_REVERSAL gross -0.132R / net -0.230R (n=39); POSITIONING_RESET_CONTINUATION gross -0.050R / net -0.150R (n=97); PARTICIPATION_BREAKOUT gross 0.318R / net 0.171R (n=40).

## Appendix — frozen V4 configuration

```yaml
cost_sensitivity:
  apply_funding: true
  default_funding_interval_hours: 8
  entry_slippage_bps: 2.0
  maker_fee_bps: 2.0
  stop_slippage_bps: 5.0
  taker_fee_bps: 5.5
  use_maker_for_targets: true
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
  entry_ready_timeout_bars: 6
  family_priority:
  - DELEVERAGING_REVERSAL
  - POSITIONING_RESET_CONTINUATION
  - PARTICIPATION_BREAKOUT
  max_concurrent_positions: 1
  watch_timeout_bars: 288
events:
  deleveraging:
    flush_lookback_bars: 4
    impulse_ret_z: 2.0
    oi_change_4h_z: -2.0
    struct_buffer_atr: 0.25
    taker_4h_z: 1.0
    volume_4h_z: 1.0
    watch_hours: 12
  participation_breakout:
    accept_bars_15m: 2
    max_chase_atr: 1.5
    max_crowding_z: 2.0
    oi_change_4h_z: 1.0
    struct_buffer_atr: 0.5
    taker_1h_z: 1.0
    volume_1h_z: 2.0
    watch_hours: 12
  positioning_reset:
    cooling_z_drop: 1.0
    oi_change_24h_z: -1.0
    pullback_low_bars: 24
    pullback_ret_z: 0.5
    struct_buffer_atr: 0.25
    taker_4h_z: 1.0
    watch_hours: 24
exits:
  breakeven_after_tp1: true
  evaluate_r_levels:
  - 1.0
  - 1.5
  - 2.0
  - 3.0
  max_hold_hours: 48
  tp1_frac: 0.4
  tp1_r: 1.0
  tp2_frac: 0.3
  tp2_r: 2.0
  trail_atr_buffer: 0.5
  trail_tf: 1h
features:
  cooling_lookback_hours: 48
  funding_z_window: 90
  level_lookback_bars: 48
  rv_window_bars: 288
  z_min_periods: 240
  z_window_bars: 720
geometry:
  max_stop_atr: 4.0
  vol_floor_atr: 1.5
  zone_inner_atr: 0.25
  zone_width_atr: 1.5
indicators:
  atr_period: 14
  donchian_period: 20
  ema_fast: 21
  ema_slow: 50
  ema_trend: 200
  min_bars:
    15m: 220
    1d: 60
    1h: 220
    4h: 220
    5m: 220
  swing_k: 2
instrument:
  market: binance_um_perp
  quote: USDT
  spot_symbol: BTCUSDT
  symbol: BTCUSDT
research:
  criteria:
    b_min_gross_expectancy_r: 0.1
    max_cost_drag_share: 0.5
    max_drawdown: 0.12
    max_family_pnl_share: 0.6
    max_trades_per_day: 3.0
    median_hold_hours:
    - 2.0
    - 12.0
    min_expectancy_without_best5_r: 0.04
    min_gross_expectancy_r: 0.15
    min_net_expectancy_r: 0.08
    min_positive_quarter_share: 0.6
    min_positive_years: 4
    min_profit_factor: 1.2
    min_quarter_trades: 10
    min_trades_per_day: 0.5
    stage_a_min_t: 2.0
  end_exclusive: '2026-10-01'
  leverage_caps:
  - 1.0
  - 2.0
  - 3.0
  - 5.0
  - 10.0
  min_cell_n: 20
  null_k: 10
  seed: 7
  start: '2022-01-01'
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
  report_risk_per_trade: 0.005
  risk_per_trade: 0.0025
safety_overlay:
  full_risk_loss_r: -0.9
  max_daily_loss_frac: 0.0075
  max_full_risk_losses_per_day: 3
stage_a:
  horizons_hours:
  - 0.5
  - 1.0
  - 2.0
  - 4.0
  - 8.0
  - 12.0
  - 24.0
  mfe_mae_hours: 24
strategy_name: btc_swing_v4_event_positioning
```
