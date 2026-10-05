# BTC Swing V3 — Active Multi-Timeframe Swing: research report

Generated 2026-10-05 16:17 UTC · window 2022-01-01 00:00 -> 2026-10-01 00:00 UTC · config `d2bd8e97f1b9` · raw result hash `b1d7385e8834` · code `c185103c1570`

**Central question.** Can a newly designed multi-timeframe BTC active-swing strategy naturally generate roughly 1-3 trades per day, with typical holds of hours rather than minutes, while maintaining positive NET expectancy after realistic fees, slippage and funding across different market regimes?

**Validation constraint.** 2022-01..2026-09 is development data (inspected in V1/V2); no V3 result on it is untouched out-of-sample. Deterministic research (no ML), one pre-registered configuration, run once; the next genuine validation of V3 is forward paper testing after a freeze. Paper/backtest only; no live trading, no exchange keys, no real money.

## 1. V3 hypothesis

- A 4H-context / 1H-setup / 15m-confirmation / 5m-execution structure with four structurally distinct families (trend pullback continuation, breakout retest, liquidity sweep reversal, volatility expansion continuation), structural stops, a simple TP1/TP2/trail exit and 0.25% planned risk produces 1-3 trades per day held for hours, with positive net expectancy after costs across regimes. V1/V2 showed that micro-entry and exit tweaks and learned ranking of V1 setups did not create edge; V3 tests whether a different, more active structural generator does.

## 2. Frozen design / config proof

| check | value |
|---|---|
| design document | `docs/BTC_SWING_V3_DESIGN.md`, committed before any V3 result (git history) |
| config | `config/btc_swing_v3.yaml`, hash `d2bd8e97f1b9382ecf41e2699181913ae631b02f3a21574f650231a2c5c3d3d3` |
| strategy / rule versions | btc_swing_v3_active_swing 3.0.0-research; setups=v3-setup-1, regime=v3-regime-1, exits=v3-exit-1 |
| code version | c185103c1570 |
| risk per trade (default stream) | 0.25% |
| runs of the strategy configuration | one raw run (plus the reporting-only overlay, 0.5% and leverage-cap streams and the determinism/truncation audits, which never changed a rule) |
| deterministic rerun identical | yes |

## 3. Data coverage

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

## 4. PIT audit

- bar visible iff close_time <= t; aux rows iff time + latency <= t; funding in (prev close, t]; fill at the next 5m open; trail moves applied from the next bar.
- Deterministic rerun: yes. Truncation audit: decisions (regime, position state) up to 2024-05-17T00:00 identical with all later data removed: yes (249697 rows).
- Resampling oracle vs native archive bars: 1h: 43824 bars, 3 differ, 4h: 10956 bars, 2 differ, 1d: 1826 bars, 0 differ.
- Liquidation basis: mark. `MarketView` raises on negative offsets; indicators causal; 1H swing points confirmed two bars later; the 1H ATR percentile uses previous bars only.

## 5. Setup definitions (frozen)

- A TREND_PULLBACK_CONTINUATION: 4H trend and 1H alignment; impulse (last confirmed 1H swing low -> high) >= 1.5 ATR; 1H close inside the 38.2-78.6% retracement; pullback extreme above the swing low; 15m EMA21 reclaim inside the zone; stop = pullback extreme - 0.3 ATR; target = the swing high. Mirror for SHORT.
- B BREAKOUT_RETEST: level = 48-bar 1H high ending two bars back; 1H close > level + 0.1 ATR; within 12 h a 15m bar touches level + 0.25 ATR from above and closes above the level, up; zone [level - 0.25 ATR, level + 0.5 ATR]; stop = level - 0.5 ATR; target = level + consolidation height; cancel on a 1H close < level - 0.25 ATR.
- C LIQUIDITY_SWEEP_REVERSAL: a 15m low below the last confirmed 4H or 1H swing low by >= 0.15 ATR; within 4 15m bars a close back above the level in the upper half of its range; zone [level - 0.1 ATR, level + 0.5 ATR]; stop = sweep extreme - 0.25 ATR; target = last confirmed 1H swing high; cancel on a 15m close below the sweep extreme.
- D VOLATILITY_EXPANSION_CONTINUATION: 1H ATR percentile <= 0.25 within the last 12 bars; expansion bar range >= 1.8 x prior ATR, close in the top 30%, above the prior 12-bar high, volume >= 1.5 x the 20-bar mean, above EMA21, not more than 1.5 ATR above the prior high; within 4 15m bars a 15m close above the previous 15m high and <= expansion high + 0.5 ATR; zone [expansion mid, expansion high + 0.5 ATR]; stop = mid - 0.3 ATR; target = high + range.
- All families: minimum reward-to-risk 1.5 at detection, stop distance 0.4-3.0 ATR(1h), execution on a 5m close inside the zone after confirmation (fill at the next 5m open), 24 h watch timeout, one net exposure, priority A > B > C > D and LONG before SHORT.

## 6. Candidate counts (episodes)

| metric | value |
|---|---|
| 5m decision bars | 499393 |
| episodes (setups detected, all families) | 7020 |
| trades (raw stream) | 2659 |
| triggers blocked by an open position | 779 |
| risk-rejected triggers | 0 |

Episodes by family:

| family | episodes |
|---|---|
| BREAKOUT_RETEST | 1143 |
| LIQUIDITY_SWEEP_REVERSAL | 4272 |
| TREND_PULLBACK_CONTINUATION | 1115 |
| VOLATILITY_EXPANSION_CONTINUATION | 490 |

Episode end reasons:

| end reason | n |
|---|---|
| LEVEL_BREACHED | 2146 |
| STOP | 1407 |
| WATCH_TIMEOUT | 1376 |
| TRAIL | 1245 |
| BLOCKED_POSITION_OPEN | 779 |
| RAN_WITHOUT_US | 59 |
| TIME_LIMIT | 7 |
| END_OF_DATA | 1 |

## 7. Trade frequency

| metric | value |
|---|---|
| trades / day | 1.533 |
| trades / week | 10.73 |
| trades / month | 46.7 |
| median hours between entries | 12.7 |
| days with 0 / 1 / 2 / 3+ trades | 14% / 38% / 32% / 16% (max 6/day) |

By year:

| year | n | trades/day | trades/month | days 0 / 1 / 2 / 3+ |
|---|---|---|---|---|
| 2022 | 572 | 1.567 | 47.7 | 13% / 37% / 32% / 18% (max 5/day) |
| 2023 | 536 | 1.468 | 44.7 | 17% / 36% / 33% / 14% (max 5/day) |
| 2024 | 557 | 1.522 | 46.3 | 15% / 39% / 32% / 15% (max 6/day) |
| 2025 | 585 | 1.603 | 48.8 | 13% / 36% / 34% / 17% (max 5/day) |
| 2026 | 409 | 1.498 | 45.6 | 11% / 44% / 31% / 14% (max 5/day) |

- Target band for this objective: 0.5-3 trades/day (15-90 per month). Fewer means the generator is too sparse for active swing trading; more means it is drifting towards noise.

## 8. Holding periods

| quantile | hours |
|---|---|
| p10 | 0.4 |
| p25 | 1.0 |
| p50 | 3.2 |
| p75 | 7.2 |
| p90 | 12.8 |

| bucket | share | n |
|---|---|---|
| 0-1h | 24% | 630 |
| 1-2h | 15% | 400 |
| 2-6h | 31% | 815 |
| 6-12h | 19% | 511 |
| 12-24h | 9% | 246 |
| 24-48h | 2% | 50 |
| >48h | 0% | 7 |

- Mean 5.3 h; trades under 30 minutes: 11.8%. Median hold by exit: STOP 1.4 h, TRAIL 5.9 h, TIME_LIMIT 48.0 h.

## 9. Combined performance (raw stream, 0.25% risk)

| period | n | trades/day | win | gross R | net R | PF | net P&L | max DD | t |
|---|---|---|---|---|---|---|---|---|---|
| combined | 2659 | 1.53 | 44% | -0.019 | -0.349 | 0.50 | -24543 | -245.43% | -15.19 |

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| all trades | 2659 | 44% | -0.349 | -1.108 | -927.8 | 0.50 | 1.50 | -0.89 | 5.3 | n/a | -24543 |

- Net P&L -24543 USDT on 10,000 (return -245.43%); gross expectancy -0.019R, net -0.354R, cost drag 0.335R per trade; std of R 1.184; t = -15.19.

## 10. Annual performance

| period | n | trades/day | win | gross R | net R | PF | net P&L | max DD | t |
|---|---|---|---|---|---|---|---|---|---|
| 2022 | 572 | 1.57 | 47% | -0.019 | -0.265 | 0.58 | -3972 | -40.29% | -5.61 |
| 2023 | 536 | 1.47 | 44% | 0.030 | -0.354 | 0.51 | -5073 | -51.19% | -6.31 |
| 2024 | 557 | 1.52 | 44% | -0.024 | -0.314 | 0.53 | -4567 | -45.67% | -6.55 |
| 2025 | 585 | 1.60 | 39% | -0.105 | -0.488 | 0.39 | -7583 | -75.96% | -9.99 |
| 2026 | 409 | 1.50 | 46% | 0.046 | -0.308 | 0.53 | -3349 | -34.60% | -5.36 |

## 11. Quarterly stability

| period | n | trades/day | win | gross R | net R | PF | net P&L | max DD | t |
|---|---|---|---|---|---|---|---|---|---|
| 2022-Q1 | 145 | 1.61 | 53% | 0.053 | -0.150 | 0.73 | -565 | -5.85% | -1.61 |
| 2022-Q2 | 146 | 1.60 | 46% | -0.090 | -0.288 | 0.55 | -1093 | -11.25% | -3.19 |
| 2022-Q3 | 148 | 1.61 | 41% | -0.132 | -0.357 | 0.50 | -1380 | -14.29% | -3.80 |
| 2022-Q4 | 133 | 1.45 | 47% | 0.106 | -0.264 | 0.58 | -934 | -9.91% | -2.60 |
| 2023-Q1 | 137 | 1.52 | 47% | 0.148 | -0.199 | 0.69 | -752 | -10.20% | -1.66 |
| 2023-Q2 | 135 | 1.48 | 41% | -0.012 | -0.330 | 0.53 | -1164 | -13.02% | -3.04 |
| 2023-Q3 | 135 | 1.47 | 39% | -0.094 | -0.602 | 0.30 | -2170 | -21.94% | -5.43 |
| 2023-Q4 | 129 | 1.40 | 47% | 0.078 | -0.284 | 0.58 | -987 | -10.33% | -2.67 |
| 2024-Q1 | 127 | 1.40 | 41% | -0.075 | -0.355 | 0.49 | -1171 | -12.05% | -3.57 |
| 2024-Q2 | 134 | 1.47 | 48% | 0.147 | -0.148 | 0.76 | -528 | -7.89% | -1.35 |
| 2024-Q3 | 135 | 1.47 | 46% | -0.023 | -0.290 | 0.55 | -1016 | -11.54% | -3.07 |
| 2024-Q4 | 161 | 1.75 | 43% | -0.125 | -0.439 | 0.39 | -1852 | -18.52% | -5.39 |
| 2025-Q1 | 145 | 1.61 | 41% | -0.016 | -0.337 | 0.53 | -1290 | -12.90% | -3.18 |
| 2025-Q2 | 152 | 1.67 | 41% | -0.086 | -0.447 | 0.41 | -1788 | -17.88% | -4.90 |
| 2025-Q3 | 136 | 1.48 | 29% | -0.234 | -0.759 | 0.24 | -2796 | -28.88% | -7.34 |
| 2025-Q4 | 152 | 1.65 | 42% | -0.092 | -0.430 | 0.41 | -1708 | -17.21% | -4.87 |
| 2026-Q1 | 123 | 1.37 | 50% | 0.209 | -0.085 | 0.85 | -274 | -6.29% | -0.74 |
| 2026-Q2 | 152 | 1.67 | 45% | -0.091 | -0.433 | 0.38 | -1736 | -17.42% | -5.16 |
| 2026-Q3 | 134 | 1.46 | 44% | 0.053 | -0.370 | 0.46 | -1338 | -13.62% | -3.71 |

- Quarters with >= 10 trades and positive net expectancy: 0 of 19.

## 12. LONG vs SHORT

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L | gross R | cost drag R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| LONG | 1348 | 43% | -0.359 | -1.120 | -484.0 | 0.49 | 1.48 | -0.90 | 5.4 | n/a | -12859 | -0.020 | 0.346 |
| SHORT | 1311 | 45% | -0.339 | -1.092 | -443.8 | 0.51 | 1.52 | -0.89 | 5.2 | n/a | -11684 | -0.018 | 0.324 |

## 13. Setup-family performance

| family | side | n | trades/month | win | net R | gross R | cost drag R | PF | MFE R | MAE R | median hold h | DD-window P&L | P&L share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| TREND PULLBACK CONTINUATION | ALL | 550 | 9.65 | 45% | -0.222 | 0.051 | 0.277 | 0.66 | 1.70 | -0.87 | 4.7 | -3214 | 13% |
| TREND PULLBACK CONTINUATION | LONG | 272 | 4.77 | 45% | -0.256 | 0.032 | 0.292 | 0.61 | 1.64 | -0.88 | 5.0 | -1838 | 7% |
| TREND PULLBACK CONTINUATION | SHORT | 278 | 4.88 | 46% | -0.189 | 0.070 | 0.262 | 0.70 | 1.77 | -0.87 | 4.6 | -1376 | 6% |
| BREAKOUT RETEST | ALL | 514 | 9.02 | 39% | -0.494 | -0.132 | 0.366 | 0.33 | 1.39 | -0.87 | 2.0 | -6706 | 27% |
| BREAKOUT RETEST | LONG | 257 | 4.51 | 34% | -0.543 | -0.159 | 0.390 | 0.32 | 1.37 | -0.91 | 2.2 | -3703 | 15% |
| BREAKOUT RETEST | SHORT | 257 | 4.51 | 45% | -0.446 | -0.105 | 0.343 | 0.34 | 1.42 | -0.84 | 1.8 | -3003 | 12% |
| LIQUIDITY SWEEP REVERSAL | ALL | 1499 | 26.31 | 44% | -0.358 | -0.010 | 0.354 | 0.50 | 1.47 | -0.91 | 2.8 | -14234 | 58% |
| LIQUIDITY SWEEP REVERSAL | LONG | 779 | 13.67 | 45% | -0.334 | 0.015 | 0.355 | 0.52 | 1.48 | -0.90 | 2.9 | -6944 | 28% |
| LIQUIDITY SWEEP REVERSAL | SHORT | 720 | 12.64 | 44% | -0.383 | -0.036 | 0.352 | 0.47 | 1.45 | -0.92 | 2.8 | -7291 | 30% |
| VOLATILITY EXPANSION CONTINUATION | ALL | 96 | 1.69 | 49% | -0.156 | 0.040 | 0.198 | 0.74 | 1.47 | -0.84 | 6.0 | -388 | 2% |
| VOLATILITY EXPANSION CONTINUATION | LONG | 40 | 0.70 | 40% | -0.361 | -0.141 | 0.223 | 0.50 | 1.15 | -0.89 | 4.0 | -375 | 2% |
| VOLATILITY EXPANSION CONTINUATION | SHORT | 56 | 0.98 | 55% | -0.009 | 0.170 | 0.179 | 0.98 | 1.69 | -0.80 | 6.7 | -14 | 0% |

- Every family is shown; none is removed. Families with no trades do not appear in the table (count 0).

## 14. Regime performance (V3 regime at the trigger bar; reported, not gated)

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TREND_UP | 425 | 44% | -0.335 | -1.144 | -142.2 | 0.53 | 1.54 | -0.89 | 5.4 | n/a | -3746 |
| TREND_DOWN | 392 | 43% | -0.313 | -1.105 | -122.9 | 0.55 | 1.50 | -0.86 | 4.9 | n/a | -3216 |
| RANGE | 176 | 47% | -0.311 | -0.035 | -54.7 | 0.51 | 1.56 | -0.84 | 5.2 | n/a | -1456 |
| VOLATILITY_EXPANSION | 665 | 46% | -0.279 | -1.073 | -185.6 | 0.56 | 1.47 | -0.87 | 6.0 | n/a | -4838 |
| VOLATILITY_COMPRESSION | 684 | 42% | -0.433 | -1.143 | -295.9 | 0.43 | 1.53 | -0.95 | 5.2 | n/a | -7937 |
| TRANSITION | 317 | 42% | -0.399 | -1.147 | -126.6 | 0.45 | 1.43 | -0.92 | 4.7 | n/a | -3350 |
| UNCLEAR | 0 |  |  |  |  |  |  |  |  |  |  |

Regime bar counts over the window: TREND_UP=70272, TREND_DOWN=65340, RANGE=39864, VOLATILITY_EXPANSION=128557, VOLATILITY_COMPRESSION=142524, TRANSITION=52836, UNCLEAR=0.

## 15. Derivatives context (snapshot at entry; context only)

| feature | n | mean winners | mean losers | Spearman vs net R |
|---|---|---|---|---|
| funding_rate_last | 2659 | 0.0001 | 0.0001 | 0.020 |
| funding_rate_mean_3 | 2659 | 0.0001 | 0.0001 | 0.009 |
| oi_change_1h_pct | 2657 | -0.1027 | -0.0751 | -0.075 |
| oi_change_4h_pct | 2656 | -0.1164 | -0.0319 | -0.057 |
| oi_change_24h_pct | 2657 | -0.0377 | 0.2066 | -0.030 |
| long_short_ratio_accounts | 2625 | 1.5255 | 1.4979 | 0.037 |
| top_trader_ls_positions | 2156 | 1.4587 | 1.4746 | -0.019 |
| taker_long_short_vol_ratio | 2448 | 1.0830 | 1.1266 | -0.012 |
| taker_buy_ratio_1h | 2659 | 0.4985 | 0.4972 | -0.004 |
| taker_buy_ratio_4h | 2659 | 0.4977 | 0.4978 | -0.010 |
| premium_index | 2659 | -0.0003 | -0.0003 | -0.012 |
| premium_mean_1h | 2659 | -0.0003 | -0.0003 | 0.009 |
| last_minus_mark_pct | 2659 | -0.0017 | -0.0038 | -0.053 |
| volume_accel_5m | 2659 | 0.9993 | 0.9785 | 0.012 |
| volume_accel_1h | 2659 | 1.4786 | 1.4708 | 0.018 |

## 16. MFE / MAE

| metric | value |
|---|---|
| mean_MFE_R | 1.502 |
| median_MFE_R | 0.889 |
| mean_MAE_R | -0.893 |
| median_MAE_R | -1.018 |
| worst_MAE_R | -8.347 |
| mfe_capture | -0.232 |
| tp1_hit_rate | 46.9% |
| tp2_hit_rate | 20.1% |
| hit_rate_1R | 46.9% |
| hit_rate_1.5R | 31.1% |
| hit_rate_2R | 22.8% |
| hit_rate_3R | 13.2% |

## 17. Exits

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| STOP | 1407 | 0% | -1.320 | -1.274 | -1856.7 | 0.00 | 0.34 | -1.28 | 3.1 | n/a | -48654 |
| TIME_LIMIT | 7 | 100% | 0.535 | 0.174 | 3.7 | inf | 1.23 | -0.60 | 48.0 | n/a | 95 |
| TRAIL | 1245 | 93% | 0.743 | 0.545 | 925.2 | 58.42 | 2.81 | -0.45 | 7.7 | n/a | 24016 |

- TP1 = +1R (40%, breakeven after), TP2 = structural target in [1.5R, 4R] (30%), remainder trails the 15m swing - 0.5 ATR(15m), 48 h cap. Exit levels were not optimised; the counterfactual R-hit rates in section 16 show what other targets would have reached before the initial stop.

## 18. Costs (USDT, raw stream; frozen assumptions)

| component | value |
|---|---|
| trades | 2659 |
| gross P&L before slippage | -1324 |
| slippage | -8511 |
| fees | -14658 |
| funding | -50 |
| net P&L | -24543 |
| gross expectancy R / net expectancy R | -0.019 / -0.354 |
| cost drag per trade (R) | 0.335 |
| fees as % of gross | 1106.7% |
| funding events | 1746 |

## 19. Risk / account simulation

| stream | final equity | return | CAGR | max DD (trades) | max DD (daily) | Sharpe | Sortino | longest losing streak | trades/week |
|---|---|---|---|---|---|---|---|---|---|
| raw, 0.25% risk (default) | -14543.11 | -245.43% | n/a | -245.43% | -245.43% | 0.42 | 1.60 | 13 | 10.73 |
| safety overlay, 0.25% risk | -13901.67 | -239.02% | n/a | -239.02% | -239.02% | 0.39 | 0.97 | 11 | 10.52 |
| raw, 0.5% risk (reporting only) | -37092.42 | -470.92% | n/a | -470.92% | -470.92% | 0.03 | 0.04 | 13 | 10.73 |

- Sequential single-slot account, fixed research equity for sizing (compounding off), max planned open risk = one position's planned risk.
- The raw stream loses more than the initial equity: realised equity goes below zero, so returns and drawdowns beyond -100% denote ruin of the 10,000 USDT research account under fixed-equity sizing (a live account would have stopped much earlier). The 0.5% stream is for reporting; no decision uses it.

## 20. Leverage / liquidation (raw stream)

| metric | value |
|---|---|
| leverage used (trades per level) | 1x: 355, 2x: 1111, 3x: 691, 5x: 380, 10x: 122 |
| mean / max leverage | 2.92 / 10 |
| mean notional (USDT) | 5513 |
| mean / max margin % of equity | n/a: the research account was ruined (realised equity fell below zero), so ratios to account equity are undefined; sizing used the fixed 10,000 USDT research equity throughout |
| mean / max planned account risk % | 0.25% planned per trade on the fixed research equity (ratios to realised equity undefined after ruin) |
| mean stop distance % | 0.66 |
| min / median stop-to-liquidation ratio | 17.4 / 74.9 |
| min liquidation distance % | 9.50 |
| liquidations | 0 |
| risk-capped trades | 11 |

Leverage caps (same rules, cap on the allowed ladder):

| cap | trades | risk rejected | mean leverage | mean R | return | max DD | min liq distance % | liquidations |
|---|---|---|---|---|---|---|---|---|
| 1x | 2659 | 0 | 1.00 | -0.349 | -109.58% | -109.58% | 99.50 | 0 |
| 2x | 2659 | 0 | 1.87 | -0.349 | -180.35% | -180.35% | 49.50 | 0 |
| 3x | 2659 | 0 | 2.32 | -0.349 | -212.72% | -212.72% | 32.83 | 0 |
| 5x | 2659 | 0 | 2.69 | -0.349 | -235.46% | -235.46% | 19.50 | 0 |
| 10x | 2659 | 0 | 2.92 | -0.349 | -245.43% | -245.43% | 9.50 | 0 |

- Leverage is not alpha: expectancy in R is the same across caps unless the cap rejects trades; the return differs only through notional. 10x must never be needed.

## 21. Daily-loss safety overlay (reported separately)

| metric | raw | overlay |
|---|---|---|
| trades | 2659 | 2605 |
| entries blocked by the overlay | 0 | 78 |
| net P&L | -24543 | -23902 |
| max DD (trade curve) | -245.43% | -239.02% |
| longest losing streak | 13 | 11 |

- Rule: after 3 full-risk losses (net R <= -0.9) in a UTC day or a realised daily loss <= -0.75% of equity, no new entries that day. It is reported for operational context and is not used to classify the strategy.

## 22. Null benchmarks

| series | n | mean R | win | strategy - null (R) | z | P(null >= strategy) | mean signed BTC drift |
|---|---|---|---|---|---|---|---|
| V3 strategy | 2659 | -0.349 | 43.8% |  |  |  | -0.0008 |
| null (time-matched) | 12200 | -0.327 | 43.4% | -0.022 | -0.76 | 80% | -0.0001 |
| null (regime-matched) | 12200 | -0.297 | 45.0% | -0.052 | -1.48 | 90% | 0.0000 |

- Null entries copy each real trade's side, stop distance, ATR and sizing and run through the same V3 exits and costs (TP2 at 2.5R, the midpoint of the structural range); the drift column is the signed BTC move over the matched holding window without costs.

## 23. Drawdown

| metric | value |
|---|---|
| max drawdown (trade curve / daily mtm) | -245.43% / -245.43% |
| max drawdown (USDT) | -24543 |
| drawdown window trades | 2659 |
| family contribution inside the window | LIQUIDITY_SWEEP_REVERSAL -14234 (n=1499); BREAKOUT_RETEST -6706 (n=514); TREND_PULLBACK_CONTINUATION -3214 (n=550); VOLATILITY_EXPANSION_CONTINUATION -388 (n=96) |
| longest losing / winning streak | 13 / 12 |

## 24. Outlier dependence

| metric | value |
|---|---|
| mean net R | -0.349 |
| without the best 3 / best 5 trades | -0.357 / -0.362 |
| without the worst 5 trades | -0.345 |
| best 5 trades' share of gross profit | 4% |
| best quarter / share of net P&L | 2026-Q1 / n/a |
| best family / share of net P&L | VOLATILITY_EXPANSION_CONTINUATION / n/a |

## 25. Limitations

- Development data: every year in the window was inspected in V1/V2; the pre-registration limits researcher degrees of freedom but cannot make these results out-of-sample.
- One instrument, one configuration, 4.75 years; quarterly and family cells are small and noisy; chronological tables are stability checks, not proof.
- Fill model: next 5m open plus fixed slippage, taker fees on every fill, archive funding; no latency, partial-fill or queue model. Liquidation on mark price where available.
- The regime label, the swing definition and the ATR percentile are model outputs with frozen thresholds; a different structural vocabulary would produce a different candidate stream.
- The safety overlay and the 0.5% stream are reporting views; the classification uses the raw 0.25% stream only.

## 26. Recommendation and pre-declared criteria

| # | criterion | met | evidence |
|---|---|---|---|
| 1 | net expectancy >= +0.10R | no | -0.349R (n=2659) |
| 2 | profit factor >= 1.20 | no | PF 0.50 |
| 3 | net expectancy > 0 in >= 4 of 5 years and in >= 60% of quarters with >= 10 trades | no | 0/5 years positive; 0% of 19 qualifying quarters |
| 4 | cost drag <= 50% of gross expectancy | no | gross -0.019R, net -0.354R |
| 5 | max drawdown (trade curve) <= 12% | no | 245.43% |
| 6 | frequency 0.5-3.0 trades/day and median hold 2-12 h | yes | 1.53 trades/day; median hold 3.2 h |
| 7 | no family > 60% of net P&L; expectancy without the 5 best trades >= +0.05R; no quarter > 50% of net P&L | no | best family share nan%; without best 5 -0.362R; best quarter share nan% |
| 8 | beats the time-matched and regime-matched nulls and the matched BTC drift | no | strategy -0.349R vs null time -0.327R / regime -0.297R; signed BTC move -0.0008 vs drift -0.0001 |

**C — NO ROBUST STRUCTURAL EDGE**

- Criteria 1, 2, 3, 4, 5, 7, 8 fail and the B conditions do not hold. The V3 structural generator, as frozen, has no robust net edge on 2022-2026 development data. No parameter tweak, family removal or re-run is proposed. Family results: TREND_PULLBACK_CONTINUATION -0.222R (n=550); BREAKOUT_RETEST -0.494R (n=514); LIQUIDITY_SWEEP_REVERSAL -0.358R (n=1499); VOLATILITY_EXPANSION_CONTINUATION -0.156R (n=96).

## Appendix — frozen V3 configuration

```yaml
context:
  align_tf: 1h
  trend_tf: 4h
  vol_percentile_window: 100
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
  - TREND_PULLBACK_CONTINUATION
  - BREAKOUT_RETEST
  - LIQUIDITY_SWEEP_REVERSAL
  - VOLATILITY_EXPANSION_CONTINUATION
  max_concurrent_positions: 1
  watch_timeout_bars: 288
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
  trail_atr_buffer: 0.5
  trail_tf: 15m
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
regime:
  d1_slope_bars: 5
  vol_compression_percentile: 0.2
  vol_expansion_4h_atr_ratio: 1.3
  vol_expansion_percentile: 0.8
research:
  criteria:
    b_min_positive_years: 3
    b_min_profit_factor: 1.05
    b_min_trades_per_day: 0.3
    max_cost_drag_share: 0.5
    max_drawdown: 0.12
    max_family_pnl_share: 0.6
    max_quarter_pnl_share: 0.5
    max_trades_per_day: 3.0
    median_hold_hours:
    - 2.0
    - 12.0
    min_expectancy_r: 0.1
    min_expectancy_without_best5_r: 0.05
    min_positive_quarter_share: 0.6
    min_positive_years: 4
    min_profit_factor: 1.2
    min_quarter_trades: 10
    min_trades_per_day: 0.5
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
setups:
  breakout_retest:
    break_buffer_atr: 0.1
    invalidation_atr: 0.25
    level_lookback_bars: 48
    retest_touch_atr: 0.25
    retest_window_bars: 12
    stop_buffer_atr: 0.5
    zone_inner_atr: 0.25
    zone_outer_atr: 0.5
  liquidity_sweep:
    reclaim_window_bars: 4
    stop_buffer_atr: 0.25
    sweep_min_atr: 0.15
    zone_inner_atr: 0.1
    zone_outer_atr: 0.5
  max_stop_atr: 3.0
  min_rr: 1.5
  min_stop_atr: 0.4
  tp2_cap_r: 4.0
  trend_pullback:
    impulse_min_atr: 1.5
    retrace_max: 0.786
    retrace_min: 0.382
    stop_buffer_atr: 0.3
    zone_pad_atr: 0.25
  volatility_expansion:
    breakout_lookback_bars: 12
    close_location: 0.7
    compression_lookback_bars: 12
    compression_percentile: 0.25
    confirm_max_above_atr: 0.5
    confirm_window_bars: 4
    expansion_range_atr: 1.8
    max_chase_atr: 1.5
    stop_buffer_atr: 0.3
    volume_lookback_bars: 20
    volume_mult: 1.5
strategy_name: btc_swing_v3_active_swing
```
