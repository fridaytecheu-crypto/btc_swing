# BTC Swing V2 — Cost-Aware Learned Opportunity Ranking: research report

Generated 2026-10-05 15:21 UTC · candidates 2022-01-01 -> 2026-10-01 · walk-forward predictions from 2023-01-01 · V2 config `4a5b31f76fde` · frozen V1 execution config `5bfc1a7a7f3c` · feature set `v2-fs-1` · code `ef34bcdd3043`

**Central question.** Can a PIT-safe, cost-aware learned ranking identify a substantially smaller subset of BTC swing opportunities whose realised net expectancy survives realistic fees, slippage and funding across different market regimes?

**Validation constraint.** 2025-01..2026-09 was inspected in V1 Phase 3; no V2 result on it is untouched/out-of-sample confirmation. Every number below is chronological walk-forward research on data that has already been inspected; the next genuine test of V2 is forward paper testing after a freeze. Paper/backtest only; no live trading, no exchange keys, no real money.

## 1. V1 failure summary

- V1 (eight frozen setup families, confirmation trigger, 0.5% risk, V1 exits) showed positive gross expectancy in every window but a net result that did not survive costs: 2022-2024 +0.08R net (CONTROL), 2025-2026 untouched holdout -0.10R net (CONTROL) / -0.07R (the Phase 2.4 approved variant), with fees + slippage + funding of 0.16R to 0.18R per trade.
- Entry-mechanics changes (Phase 2.1, 2.2) and the exit change (Phase 2.3) did not help. Blocking new SHORT entries in TREND_DOWN (Phase 2.4) helped consistently (in sample and on the holdout) but not enough. V1 is closed as failed out-of-sample as a strategy and successful as research infrastructure.
- Conclusion carried into V2: the binding constraint is opportunity selection and per-trade edge strength, not leverage or exits.

## 2. V2 hypothesis

- V1 setups are candidate generators. A PIT-safe feature snapshot at each candidate decision point, labelled with the frozen V1 execution engine (realised NET R after fees, slippage and funding), can be ranked by a simple learned model so that higher-scored candidates have higher realised net expectancy, and a selective top slice is net positive after costs while remaining frequent enough to matter.
- Models are pre-declared (logistic regression, ridge, a gated gradient-boosting model), the feature space is fixed (`v2-fs-1`, 85 features), evaluation is chronological walk-forward, and the classification criteria were written before any model was fitted (`docs/BTC_SWING_V2_DESIGN.md`, section 11).

## 3. Candidate population

| metric | value |
|---|---|
| 5m decision bars scanned | 499393 |
| candidates (TRIGGERED episodes, one lifecycle per family, no slot) | 838 |
| risk-rejected by the frozen V1 sizing rule (no label) | 0 |
| labelled candidates with a complete simulated exit | 837 |
| evaluable walk-forward rows (from 2023-01-01) | 721 |
| V1 single-slot trades on the same window (baseline) | 455 |
| episode outcomes | CANDIDATE=838, LEVEL_BREACHED=319, RAN_WITHOUT_US=216, REGIME_INELIGIBLE=12, WATCH_TIMEOUT=312 |

By family:

| family | candidates |
|---|---|
| BREAKDOWN_SHORT | 49 |
| BREAKOUT_LONG | 42 |
| MOMENTUM_CONTINUATION_LONG | 140 |
| RESISTANCE_REJECTION_SHORT | 140 |
| SUPPORT_RECLAIM_LONG | 140 |
| TREND_PULLBACK_LONG | 186 |
| TREND_PULLBACK_SHORT | 141 |

By regime at the trigger bar:

| regime | candidates |
|---|---|
| BREAKOUT_REGIME | 12 |
| LOW_VOLATILITY | 3 |
| RANGE | 202 |
| TREND_DOWN | 212 |
| TREND_UP | 409 |

Labelled candidates by year (independent simulation, no slot):

| year | n | mean net R | P(net R > 0) |
|---|---|---|---|
| 2022 | 116 | 0.081 | 49% |
| 2023 | 196 | 0.078 | 47% |
| 2024 | 215 | 0.042 | 42% |
| 2025 | 183 | -0.126 | 39% |
| 2026 | 127 | -0.186 | 39% |

- Candidate = the V1 episode reaching TRIGGERED (zone reached, confirm-TF confirmation, 5m trigger) with each family's lifecycle run independently and the V1 post-close cooldown after every candidate; the decision time is the trigger-bar close, the hypothetical fill the next 5m open plus slippage. This is the same opportunity definition as V1 minus the single slot.

## 4. Feature definitions (`v2-fs-1`, fixed before fitting)

- 85 features in five groups; all signed-in-trade-direction momentum/distance features carry the side sign. Full list: fam_TREND_PULLBACK_LONG, fam_BREAKOUT_LONG, fam_SUPPORT_RECLAIM_LONG, fam_MOMENTUM_CONTINUATION_LONG, fam_TREND_PULLBACK_SHORT, fam_BREAKDOWN_SHORT, fam_RESISTANCE_REJECTION_SHORT, fam_MOMENTUM_CONTINUATION_SHORT, side, setup_age_bars, bars_since_confirm, dist_zone_mid_atr, dist_anchor_atr, stop_dist_atr, stop_dist_pct, zone_width_atr, structural_target_r, has_structural_target, mom_15m, mom_1h, mom_4h, mom_24h, mom_7d, price_change_24h_pct_raw, dist_ema_fast_1h_atr, dist_ema_slow_1h_atr, ema_spread_1h_atr, dist_ema_fast_4h_atr, dist_ema_slow_4h_atr, ema_spread_4h_atr, dist_ema_trend_1d_atr, ema_spread_1d_atr, ema_slow_slope_1d_atr, range_pos_4h_raw, range_pos_4h_dir, range_pos_1d_raw, range_pos_1d_dir, retrace_1h_atr, volume_accel_5m, volume_accel_1h, atr_pct_1h, atr_pct_4h, atr_pct_1d, atr_expansion_1h, atr_expansion_1d, atr_pct_1d_percentile_90, rv_24h, rv_7d, reg_TREND_UP, reg_TREND_DOWN, reg_RANGE, reg_HIGH_VOLATILITY, reg_LOW_VOLATILITY, reg_BREAKOUT_REGIME, reg_UNCLEAR, regime_age_bars, regime_transitions_7d, short_in_trend_down, funding_rate_last, funding_rate_mean_3, funding_minus_mean3, funding_z_90, log_open_interest, oi_change_1h_pct, oi_change_4h_pct, oi_change_24h_pct, price_oi_interaction_24h, long_short_ratio_accounts, top_trader_ls_positions, taker_long_short_vol_ratio, taker_buy_ratio_1h, taker_buy_ratio_4h, taker_buy_ratio_diff, premium_index, premium_mean_1h, premium_minus_mean_1h, last_minus_mark_pct, hour_sin, hour_cos, dow_sin, dow_cos, weekend, dist_30d_high_pct, dist_30d_low_pct, n_missing.
- Setup: family one-hot, side, setup age, bars since confirmation, distance from zone mid / anchor / stop (setup-TF ATR), stop distance %, zone width, structural target in R.
- Price/momentum: 15m/1h/4h/24h/7d returns, EMA20/EMA50 distances and spreads on 1h/4h/1d, EMA200 distance on 1d, EMA50 slope on 1d, range position on 4h/1d, 1h retrace, volume acceleration 5m/1h, ATR% 1h/4h/1d, ATR expansion 1h/1d, 1d ATR% percentile (previous 90 bars), realised vol 24h/7d.
- Regime: regime one-hot, regime age, transitions in 7 days, `short_in_trend_down` (the V1 structural finding as a feature, not a gate).
- Derivatives (19): funding_rate_last, funding_rate_mean_3, funding_minus_mean3, funding_z_90, log_open_interest, oi_change_1h_pct, oi_change_4h_pct, oi_change_24h_pct, price_oi_interaction_24h, long_short_ratio_accounts, top_trader_ls_positions, taker_long_short_vol_ratio, taker_buy_ratio_1h, taker_buy_ratio_4h, taker_buy_ratio_diff, premium_index, premium_mean_1h, premium_minus_mean_1h, last_minus_mark_pct.
- Context: hour/day-of-week sine-cosine, weekend, distance from the 30-day high/low, `n_missing`.
- Missing values: training-fold median imputation inside the model pipeline (the gradient-boosting model handles NaN natively).

## 5. PIT audit

- bars with close_time <= t; aux rows with time + latency <= t; features from the MarketView at the trigger bar; labels simulated from the next 5m open.
- Truncation audit: candidate generation + feature computation re-run with all data after 2024-05-17 removed: 394 vs 394 candidates up to the cut, identical trigger times and features: yes (max |feature difference| 0.000000000).
- Labels use only bars after the decision (simulation forward from the next open); a label enters a training set only when its simulated exit time is <= the fold's test start.
- Selectivity thresholds are quantiles of each fold's training-set scores; the test block never informs its own selection.

## 6. Labels

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| all labelled candidates (independent simulation) | 837 | 43% | -0.015 | -1.069 | -12.8 | 0.97 | 1.96 | -0.86 | 33.0 | n/a | -693 |

- Targets: A `y_pos` = 1[net R > 0]; B net R clipped to [-2, 4]. Net R is after fees, slippage and funding with the frozen V1 sizing (0.5% of a fixed 10,000 USDT research equity).

## 7. Chronological splits

| fold | test block | train rows | test rows | fitted | thr top 25% |
|---|---|---|---|---|---|
| 0 | 2023-Q1 | 114 | 39 | yes | 0.843 |
| 1 | 2023-Q2 | 153 | 43 | yes | 0.773 |
| 2 | 2023-Q3 | 197 | 49 | yes | 0.710 |
| 3 | 2023-Q4 | 247 | 65 | yes | 0.709 |
| 4 | 2024-Q1 | 312 | 67 | yes | 0.674 |
| 5 | 2024-Q2 | 377 | 43 | yes | 0.660 |
| 6 | 2024-Q3 | 422 | 57 | yes | 0.626 |
| 7 | 2024-Q4 | 479 | 48 | yes | 0.604 |
| 8 | 2025-Q1 | 526 | 32 | yes | 0.601 |
| 9 | 2025-Q2 | 558 | 63 | yes | 0.587 |
| 10 | 2025-Q3 | 620 | 50 | yes | 0.575 |
| 11 | 2025-Q4 | 672 | 38 | yes | 0.561 |
| 12 | 2026-Q1 | 710 | 32 | yes | 0.553 |
| 13 | 2026-Q2 | 741 | 49 | yes | 0.551 |
| 14 | 2026-Q3 | 789 | 46 | yes | 0.550 |

- Expanding window; training rows are candidates with decision time before the block and simulated exit at or before the block start. No random split anywhere.

## 8. Logistic baseline (M1)

| metric | value |
|---|---|
| walk-forward rows | 721 |
| Spearman(score, net R) | -0.022 |
| AUC for net R > 0 | 0.480 |
| base rate P(net R > 0) | 42.3% |
| mean net R, all evaluable | -0.031 |

Per fold:

| fold | n | Spearman | AUC | mean net R |
|---|---|---|---|---|
| 0 | 39 | 0.032 | 0.652 | 0.045 |
| 1 | 43 | -0.078 | 0.476 | -0.159 |
| 2 | 49 | 0.130 | 0.545 | 0.232 |
| 3 | 65 | -0.181 | 0.404 | 0.139 |
| 4 | 67 | 0.062 | 0.533 | 0.413 |
| 5 | 43 | 0.026 | 0.457 | -0.530 |
| 6 | 57 | 0.074 | 0.506 | -0.209 |
| 7 | 48 | 0.054 | 0.556 | 0.336 |
| 8 | 32 | -0.190 | 0.367 | -0.091 |
| 9 | 63 | -0.048 | 0.484 | -0.063 |
| 10 | 50 | 0.122 | 0.431 | -0.179 |
| 11 | 38 | 0.287 | 0.705 | -0.188 |
| 12 | 32 | -0.135 | 0.537 | -0.128 |
| 13 | 49 | -0.143 | 0.342 | 0.078 |
| 14 | 46 | -0.119 | 0.312 | -0.508 |

Largest standardised coefficients (mean across folds; sign consistency = share of folds with the mean's sign):

| feature | mean coef | std | sign consistency |
|---|---|---|---|
| atr_pct_4h | -0.947 | 0.269 | 100% |
| dist_anchor_atr | -0.635 | 0.137 | 100% |
| dist_ema_fast_4h_atr | 0.581 | 0.257 | 100% |
| long_short_ratio_accounts | -0.570 | 0.174 | 100% |
| atr_expansion_1d | 0.545 | 0.170 | 100% |
| ema_slow_slope_1d_atr | -0.480 | 0.189 | 100% |
| setup_age_bars | -0.407 | 0.175 | 100% |
| mom_24h | -0.352 | 0.132 | 100% |
| dist_ema_slow_4h_atr | 0.332 | 0.178 | 100% |
| atr_pct_1h | 0.324 | 0.380 | 87% |
| price_change_24h_pct_raw | -0.314 | 0.257 | 100% |
| log_open_interest | 0.308 | 0.242 | 93% |
| stop_dist_pct | 0.305 | 0.221 | 93% |
| fam_RESISTANCE_REJECTION_SHORT | 0.293 | 0.152 | 100% |
| mom_15m | 0.273 | 0.250 | 100% |
| fam_MOMENTUM_CONTINUATION_LONG | -0.271 | 0.192 | 100% |
| fam_TREND_PULLBACK_SHORT | -0.267 | 0.153 | 87% |
| price_oi_interaction_24h | -0.263 | 0.092 | 100% |
| atr_pct_1d_percentile_90 | -0.261 | 0.261 | 93% |
| n_missing | 0.249 | 0.143 | 100% |

## 9. Ranking monotonicity (M1 walk-forward deciles)

| decile (1 = lowest score) | score range | n | mean net R | median R | PF | win | sum P&L |
|---|---|---|---|---|---|---|---|
| 1 | 0.000..0.007 | 73 | 0.177 | 0.375 | 1.32 | 51% | 653 |
| 2 | 0.007..0.090 | 72 | -0.287 | -1.108 | 0.62 | 36% | -1060 |
| 3 | 0.090..0.197 | 72 | 0.014 | -1.063 | 1.02 | 43% | 47 |
| 4 | 0.198..0.295 | 72 | -0.336 | -1.082 | 0.54 | 36% | -1238 |
| 5 | 0.296..0.385 | 72 | 0.228 | 0.359 | 1.41 | 51% | 839 |
| 6 | 0.386..0.477 | 72 | -0.016 | -1.064 | 0.97 | 42% | -69 |
| 7 | 0.478..0.551 | 72 | 0.301 | 0.422 | 1.54 | 53% | 1097 |
| 8 | 0.553..0.639 | 72 | -0.111 | -1.085 | 0.84 | 39% | -401 |
| 9 | 0.639..0.738 | 72 | -0.152 | -1.089 | 0.79 | 36% | -548 |
| 10 | 0.742..0.986 | 72 | -0.130 | -1.083 | 0.82 | 36% | -490 |

- Spearman between decile index and decile mean net R: -0.09.

Quartiles within 2023:

| quartile | score range | n | mean net R | median R | PF | win | sum P&L |
|---|---|---|---|---|---|---|---|
| 1 | 0.000..0.002 | 49 | 0.172 | 0.375 | 1.32 | 51% | 429 |
| 2 | 0.002..0.043 | 49 | -0.113 | -1.081 | 0.83 | 43% | -287 |
| 3 | 0.044..0.325 | 49 | 0.048 | 0.331 | 1.09 | 51% | 121 |
| 4 | 0.331..0.948 | 49 | 0.205 | -1.079 | 1.31 | 45% | 521 |

Quartiles within 2024:

| quartile | score range | n | mean net R | median R | PF | win | sum P&L |
|---|---|---|---|---|---|---|---|
| 1 | 0.001..0.216 | 54 | 0.178 | -0.601 | 1.30 | 48% | 481 |
| 2 | 0.216..0.387 | 54 | -0.301 | -1.083 | 0.60 | 33% | -828 |
| 3 | 0.389..0.604 | 54 | 0.353 | -1.023 | 1.62 | 48% | 976 |
| 4 | 0.604..0.986 | 53 | -0.063 | -1.085 | 0.90 | 40% | -175 |

Quartiles within 2025:

| quartile | score range | n | mean net R | median R | PF | win | sum P&L |
|---|---|---|---|---|---|---|---|
| 1 | 0.021..0.337 | 46 | -0.395 | -1.087 | 0.50 | 30% | -926 |
| 2 | 0.350..0.536 | 46 | 0.267 | 0.386 | 1.47 | 52% | 626 |
| 3 | 0.540..0.670 | 46 | -0.227 | -1.094 | 0.69 | 37% | -527 |
| 4 | 0.674..0.972 | 45 | -0.147 | -1.072 | 0.79 | 38% | -337 |

Quartiles within 2026:

| quartile | score range | n | mean net R | median R | PF | win | sum P&L |
|---|---|---|---|---|---|---|---|
| 1 | 0.109..0.318 | 32 | -0.239 | -1.079 | 0.66 | 38% | -396 |
| 2 | 0.321..0.464 | 32 | -0.006 | -1.054 | 0.98 | 47% | -18 |
| 3 | 0.466..0.586 | 32 | -0.118 | -1.106 | 0.81 | 41% | -213 |
| 4 | 0.591..0.777 | 31 | -0.389 | -1.089 | 0.52 | 29% | -616 |

Top-N per month (descriptive, not PIT: picks the month's n best after the month is known):

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| top 5 per month | 224 | 38% | -0.075 | -1.080 | -16.7 | 0.90 | 1.99 | -0.93 | 35.1 | n/a | -857 |
| top 10 per month | 432 | 40% | -0.071 | -1.077 | -30.9 | 0.90 | 1.97 | -0.89 | 34.2 | n/a | -1575 |

## 10. Expected-net-R analysis (M2 ridge on clipped net R)

| metric | M1 logistic | M2 ridge |
|---|---|---|
| Spearman(score, net R) | -0.022 | -0.007 |
| decile monotonicity | -0.09 | 0.07 |
| AUC for net R > 0 | 0.480 | 0.490 |

| decile (1 = lowest score) | score range | n | mean net R | median R | PF | win | sum P&L |
|---|---|---|---|---|---|---|---|
| 1 | -10.979..-2.137 | 73 | 0.139 | -1.038 | 1.24 | 49% | 515 |
| 2 | -2.081..-1.005 | 72 | -0.320 | -1.088 | 0.57 | 35% | -1183 |
| 3 | -0.999..-0.624 | 72 | 0.175 | -1.047 | 1.30 | 49% | 645 |
| 4 | -0.621..-0.314 | 72 | -0.239 | -1.100 | 0.68 | 35% | -891 |
| 5 | -0.311..-0.063 | 72 | -0.228 | -1.072 | 0.66 | 40% | -842 |
| 6 | -0.062..0.147 | 72 | -0.080 | -1.080 | 0.89 | 39% | -296 |
| 7 | 0.147..0.320 | 72 | 0.321 | 0.430 | 1.59 | 53% | 1174 |
| 8 | 0.321..0.470 | 72 | 0.284 | 0.363 | 1.50 | 51% | 1049 |
| 9 | 0.474..0.782 | 72 | -0.109 | -1.098 | 0.85 | 38% | -398 |
| 10 | 0.783..2.459 | 72 | -0.256 | -1.075 | 0.66 | 35% | -941 |

M2 PIT slices:

| slice (candidates) | n | mean net R | median R | PF | win | MFE R | MAE R | sum P&L (independent) | t |
|---|---|---|---|---|---|---|---|---|---|
| all candidates | 721 | -0.031 | -1.072 | 0.95 | 42% | 1.94 | -0.87 | -1169 | -0.56 |
| top 50% | 326 | 0.013 | -1.072 | 1.02 | 42% | 1.98 | -0.84 | 211 | 0.15 |
| top 25% | 185 | -0.044 | -1.079 | 0.94 | 40% | 1.93 | -0.85 | -413 | -0.39 |
| top 10% | 80 | -0.307 | -1.082 | 0.59 | 34% | 1.47 | -0.90 | -1255 | -2.16 |
| top 5% | 51 | -0.254 | -1.070 | 0.64 | 37% | 1.38 | -0.88 | -661 | -1.46 |

- M3 (gradient boosting) was NOT fitted: the pre-declared gate requires M1 Spearman > 0 and a top-quartile gain over all candidates > 0.050R; observed Spearman -0.022, gain -0.174R.

## 11. LONG / SHORT (all evaluable vs M1 top-25% slice)

| side | all candidates | top 25% slice | share selected |
|---|---|---|---|
| LONG | n=484, R=0.031, PF=1.05, win=45% | n=115, R=-0.186, PF=0.74, win=36% | 24% |
| SHORT | n=237, R=-0.158, PF=0.78, win=38% | n=58, R=-0.242, PF=0.69, win=33% | 24% |

## 12. Setup-family performance (all evaluable vs M1 top-25% slice)

| family | all candidates | top 25% slice | share selected |
|---|---|---|---|
| BREAKDOWN_SHORT | n=39, R=0.185, PF=1.30, win=49% | n=20, R=0.158, PF=1.26, win=50% | 51% |
| BREAKOUT_LONG | n=40, R=-0.113, PF=0.84, win=40% | n=5, R=-0.899, PF=0.09, win=20% | 12% |
| MOMENTUM_CONTINUATION_LONG | n=133, R=-0.029, PF=0.95, win=43% | n=35, R=0.089, PF=1.15, win=46% | 26% |
| RESISTANCE_REJECTION_SHORT | n=113, R=-0.106, PF=0.84, win=40% | n=30, R=-0.271, PF=0.65, win=30% | 27% |
| SUPPORT_RECLAIM_LONG | n=131, R=0.058, PF=1.09, win=44% | n=29, R=-0.615, PF=0.33, win=21% | 22% |
| TREND_PULLBACK_LONG | n=180, R=0.089, PF=1.15, win=48% | n=46, R=-0.047, PF=0.93, win=39% | 26% |
| TREND_PULLBACK_SHORT | n=85, R=-0.384, PF=0.52, win=29% | n=8, R=-1.130, PF=0.00, win=0% | 9% |

## 13. Regime performance (all evaluable vs M1 top-25% slice)

| regime_at_trigger | all candidates | top 25% slice | share selected |
|---|---|---|---|
| BREAKOUT_REGIME | n=11, R=-0.042, PF=0.93, win=45% | n=1, R=1.026, PF=inf, win=100% | 9% |
| LOW_VOLATILITY | n=3, R=0.980, PF=3.17, win=67% | n=1, R=-1.367, PF=0.00, win=0% | 33% |
| RANGE | n=178, R=-0.013, PF=0.98, win=43% | n=60, R=-0.063, PF=0.91, win=40% | 34% |
| TREND_DOWN | n=137, R=-0.250, PF=0.66, win=35% | n=19, R=-1.008, PF=0.05, win=5% | 14% |
| TREND_UP | n=392, R=0.030, PF=1.05, win=44% | n=92, R=-0.132, PF=0.81, win=37% | 23% |

## 14. Derivatives-feature diagnostics

| feature | n | Spearman vs net R (univariate) | missing |
|---|---|---|---|
| funding_rate_last | 721 | 0.021 | 0.0% |
| funding_rate_mean_3 | 721 | 0.025 | 0.0% |
| funding_minus_mean3 | 721 | -0.016 | 0.0% |
| funding_z_90 | 721 | 0.016 | 0.0% |
| log_open_interest | 721 | -0.045 | 0.0% |
| oi_change_1h_pct | 720 | -0.007 | 0.1% |
| oi_change_4h_pct | 719 | -0.016 | 0.3% |
| oi_change_24h_pct | 720 | -0.010 | 0.1% |
| price_oi_interaction_24h | 720 | 0.048 | 0.1% |
| long_short_ratio_accounts | 721 | -0.109 | 0.0% |
| top_trader_ls_positions | 721 | -0.054 | 0.0% |
| taker_long_short_vol_ratio | 721 | 0.008 | 0.0% |
| taker_buy_ratio_1h | 721 | 0.004 | 0.0% |
| taker_buy_ratio_4h | 721 | 0.054 | 0.0% |
| taker_buy_ratio_diff | 721 | -0.017 | 0.0% |
| premium_index | 721 | 0.031 | 0.0% |
| premium_mean_1h | 721 | 0.040 | 0.0% |
| premium_minus_mean_1h | 721 | -0.004 | 0.0% |
| last_minus_mark_pct | 721 | 0.026 | 0.0% |

Ablation (M1 without the derivatives block):

| metric | M1 full | M1 without derivatives |
|---|---|---|
| Spearman | -0.022 | -0.004 |
| AUC | 0.480 | 0.485 |
| top 25% mean net R | -0.205 | -0.038 |
| top 25% PF | 0.73 | 0.94 |

Coefficients of the derivatives block in M1 (mean across folds):

| feature | mean coef | sign consistency |
|---|---|---|
| long_short_ratio_accounts | -0.570 | 100% |
| log_open_interest | 0.308 | 93% |
| price_oi_interaction_24h | -0.263 | 100% |
| funding_z_90 | -0.240 | 80% |
| funding_rate_last | 0.212 | 100% |
| premium_index | -0.199 | 100% |
| premium_minus_mean_1h | -0.187 | 100% |
| funding_rate_mean_3 | 0.150 | 93% |
| funding_minus_mean3 | 0.135 | 93% |
| top_trader_ls_positions | -0.128 | 60% |
| taker_buy_ratio_1h | -0.110 | 100% |
| premium_mean_1h | -0.090 | 73% |
| taker_buy_ratio_diff | -0.084 | 100% |
| taker_long_short_vol_ratio | -0.077 | 73% |
| oi_change_24h_pct | 0.073 | 80% |
| taker_buy_ratio_4h | -0.068 | 60% |
| oi_change_4h_pct | -0.027 | 87% |
| oi_change_1h_pct | 0.018 | 73% |
| last_minus_mark_pct | 0.004 | 53% |

## 15. Selectivity vs frequency (M1, PIT thresholds)

Candidate populations (every selected candidate simulated independently):

| slice (candidates) | n | mean net R | median R | PF | win | MFE R | MAE R | sum P&L (independent) | t |
|---|---|---|---|---|---|---|---|---|---|
| all candidates | 721 | -0.031 | -1.072 | 0.95 | 42% | 1.94 | -0.87 | -1169 | -0.56 |
| top 50% | 314 | -0.036 | -1.074 | 0.95 | 41% | 1.95 | -0.86 | -585 | -0.42 |
| top 25% | 173 | -0.205 | -1.089 | 0.73 | 35% | 1.69 | -0.91 | -1815 | -1.84 |
| top 10% | 80 | -0.221 | -1.079 | 0.70 | 36% | 1.55 | -0.87 | -908 | -1.48 |
| top 5% | 48 | -0.419 | -1.092 | 0.49 | 29% | 1.39 | -0.92 | -1029 | -2.35 |

Tradeable streams after single-slot sequencing (fixed research equity, as V1):

| stream (single-slot sequential) | trades | trades/month | net R | gross R | PF | win | total costs | max DD | return | t |
|---|---|---|---|---|---|---|---|---|---|---|
| all candidates | 394 | 8.76 | -0.048 | 0.053 | 0.93 | 42% | 3132 | -19.98% | -9.71% | -0.66 |
| top 50% | 205 | 4.56 | -0.056 | 0.042 | 0.92 | 40% | 1606 | -11.83% | -5.74% | -0.53 |
| top 25% | 119 | 2.65 | -0.151 | -0.056 | 0.80 | 34% | 915 | -13.55% | -9.21% | -1.06 |
| top 10% | 59 | 1.31 | -0.260 | -0.169 | 0.66 | 32% | 432 | -9.89% | -7.85% | -1.44 |
| top 5% | 38 | 0.84 | -0.367 | -0.266 | 0.55 | 29% | 306 | -8.01% | -7.12% | -1.73 |

- Halves of the top-25% slice: 2023-2024 n=62, mean 0.018R (all candidates 0.059R); 2025-2026 n=111, mean -0.329R (all candidates -0.150R).

Per fold, top-25% slice:

| fold | n all | mean R all | n selected | mean R selected | P&L selected |
|---|---|---|---|---|---|
| 0 | 39 | 0.045 | 2 | -0.171 | -20 |
| 1 | 43 | -0.159 | 5 | -0.260 | -66 |
| 2 | 49 | 0.232 | 7 | 0.250 | 95 |
| 3 | 65 | 0.139 | 3 | -0.361 | -60 |
| 4 | 67 | 0.413 | 9 | 1.442 | 655 |
| 5 | 43 | -0.530 | 8 | -0.740 | -301 |
| 6 | 57 | -0.209 | 16 | -0.395 | -322 |
| 7 | 48 | 0.336 | 12 | 0.112 | 63 |
| 8 | 32 | -0.091 | 18 | -0.365 | -339 |
| 9 | 63 | -0.063 | 32 | -0.250 | -409 |
| 10 | 50 | -0.179 | 12 | -0.632 | -383 |
| 11 | 38 | -0.188 | 8 | 0.037 | 17 |
| 12 | 32 | -0.128 | 4 | -0.605 | -126 |
| 13 | 49 | 0.078 | 25 | -0.069 | -87 |
| 14 | 46 | -0.508 | 12 | -0.875 | -533 |

## 16. Costs (sequential streams, USDT; frozen assumptions)

| stream | trades | gross R | net R | fees | slippage | funding |
|---|---|---|---|---|---|---|
| all candidates | 394 | 0.053 | -0.048 | 1879 | 1109 | -144 |
| top 50% | 205 | 0.042 | -0.056 | 975 | 575 | -56 |
| top 25% | 119 | -0.056 | -0.151 | 550 | 337 | -29 |
| top 10% | 59 | -0.169 | -0.260 | 257 | 159 | -15 |
| top 5% | 38 | -0.266 | -0.367 | 177 | 110 | -19 |

## 17. Drawdown / equity (sequential streams)

| stream | max DD | return | longest losing streak | net P&L |
|---|---|---|---|---|
| all candidates | -19.98% | -9.71% | 14 | -971 |
| top 50% | -11.83% | -5.74% | 9 | -574 |
| top 25% | -13.55% | -9.21% | 9 | -921 |
| top 10% | -9.89% | -7.85% | 6 | -785 |
| top 5% | -8.01% | -7.12% | 7 | -712 |

## 18. Null comparison and baselines

| population | n | mean net R | null time-matched | null regime-matched | z |
|---|---|---|---|---|---|
| M1 top 25% | 173 | -0.205 | -0.024 | -0.091 | z time -2.27, regime -0.93 |
| all candidates | 721 | -0.031 | -0.044 | -0.141 | z time 0.10, regime 1.51 |

| baseline | n | mean net R | PF | win | notes |
|---|---|---|---|---|---|
| all candidates (generator, unranked) | 721 | -0.031 | 0.95 | 42% | every TRIGGERED episode |
| V1 single-slot traded population | 393 | -0.014 | 0.98 | 43% | V1 CONTROL engine on the same window from 2023-01-01; result hash `a50ea0072ac9` |
| simple rule: NO_NEW_SHORT_IN_TREND_DOWN (unranked) | 584 | 0.021 | 1.03 | 44% | V1 Phase 2.4 rule as a hard block |
| M1 top 25% (model learns the regime effect) | 173 | -0.205 | 0.73 | 35% | Spearman -0.022 |
| M1 top 25% + hard block | 154 | -0.106 | 0.85 | 38% | Spearman on non-blocked -0.032 |

- The learned ranking adds information beyond the candidate generator only if the top slice beats all candidates, the V1 traded population and both geometry-matched nulls; the pre-declared criterion 4 encodes this.

## 19. Model stability

- Folds with Spearman > 0: 8 of 15; folds with AUC > 0.5: 7.
- Top-25% slice positive in 4 of 12 folds with >= 5 selected candidates.
- Coefficient sign consistency across folds (top 20 by magnitude): mean 98%.

## 20. Limitations

- 2025-2026 is not untouched (V1 Phase 3 inspected it); walk-forward discipline limits leakage of *model* information but cannot restore holdout status for *research decisions* made with knowledge of that period.
- One instrument, one candidate generator (V1 setups), one fixed feature space, a few hundred to a few thousand candidates; per-fold estimates are noisy and the per-fold tables should be read as a stability check, not as evidence of edge in any single quarter.
- Labels are simulated independently (no slot); the sequential-account streams reintroduce the slot and are the tradeable view. Costs are the frozen V1 assumptions; no alternative cost scenario was run.
- The regime label and the setup definitions are V1 model outputs with frozen thresholds; the ranking can only re-weight what the generator produces.
- Top-N-per-month tables are descriptive only (not PIT).

## 21. Recommendation and pre-declared criteria

| # | criterion | met | evidence |
|---|---|---|---|
| 1 | Spearman(score, net R) > 0 overall and > 0 in >= 70% of folds with >= 20 candidates | no | overall -0.022; positive in 53% of 15 folds |
| 2 | decile monotonicity: Spearman(decile, decile mean net R) >= 0.6 | no | -0.09 |
| 3 | PIT top-25% slice: net expectancy > 0 and PF > 1 overall, and net expectancy > 0 in both halves | no | mean -0.205R, PF 0.73, halves +0.018R / -0.329R (n=173) |
| 4 | top slice beats all candidates by >= +0.10R and beats both null means | no | top -0.205R vs all -0.031R (gain -0.174R); null time -0.024R, regime -0.091R |
| 5 | top slice after single-slot sequencing yields >= 2.0 trades/month | yes | 2.65 trades/month (119 trades) |
| 6 | stability: top slice positive in >= 60% of folds with >= 5 selected | no | positive in 33% of 12 folds |

**C — NO USEFUL RANKING EDGE**

- Criteria 1, 2, 3, 4, 6 fail; the learned ranking does not add usable information beyond the candidate generator under the frozen execution and cost model. No parameter tweak is proposed. The owner decides whether to stop the V2 track or to pre-register a differently defined candidate generator or label as a new research generation.

## Appendix — V2 protocol configuration

```yaml
candidates:
  cooldown_after_candidate_bars: 12
  end_exclusive: '2026-10-01'
  start: '2022-01-01'
criteria:
  decile_monotonicity: 0.6
  gain_vs_all_r: 0.1
  min_candidates_per_fold_for_spearman: 20
  min_selected_per_fold: 5
  min_trades_per_month: 2.0
  spearman_fold_share: 0.7
  stability_fold_share: 0.6
  top_slice: 0.25
labels:
  clip_high: 4.0
  clip_low: -2.0
models:
  gate_min_top_quartile_gain_r: 0.05
  hgb:
    l2_regularization: 1.0
    learning_rate: 0.05
    max_depth: 3
    max_iter: 200
    min_samples_leaf: 50
  logistic_c: 1.0
  ridge_alpha: 10.0
name: btc_swing_v2_ranking
null_k: 5
seed: 7
slices:
- 0.5
- 0.25
- 0.1
- 0.05
top_n_per_month:
- 5
- 10
v1_config_path: config/btc_swing.default.yaml
walkforward:
  block_months: 3
  first_test_start: '2023-01-01'
  min_train_rows: 100
```
