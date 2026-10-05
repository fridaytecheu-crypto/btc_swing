# BTC Swing V1 — Phase 2 validation (frozen pre-registered defaults)

Generated 2026-10-05 06:12 UTC · strategy `btc_leveraged_swing_v1` 1.0.0-foundation · config `a837c775056e` · code `unknown` · result hash `92ebe5d7fc65` · deterministic rerun: yes

**Central question.** With the pre-registered strategy and realistic costs/risk controls, does this BTC 5-minute-scanned, multi-timeframe LONG/SHORT swing engine show repeatable positive expectancy before any optimisation?

**Scope.** Paper/backtest only. No live trading, no authenticated exchange access, no real money. All thresholds are the frozen V1 defaults plus the pre-registered Phase 2 defaults for the six new families (set before any run on these data). Nothing was tuned, no family was dropped. Development/first validation 2022-01-01 -> 2023-12-31; chronological validation 2024-01-01 -> 2024-12-31; 2025 onward untouched.

## 1. Data coverage

Source: Binance Vision public archive (USDT-margined perpetual BTCUSDT), checksum-verified zip files, immutable raw store.

| dataset | rows | first | last | coverage in study window |
|---|---|---|---|---|
| perp_klines_5m | 342144 | 2021-10-01T00:00 | 2024-12-31T23:55 | 100.00% |
| funding | 3564 | 2021-10-01T00:00 | 2024-12-31T16:00 | 100.00% |
| metrics_5min | 341947 | 2021-10-01T00:00 | 2024-12-31T23:55 | 99.96% |
| premium_index_5m | 341559 | 2021-10-01T00:00 | 2024-12-31T23:55 | 99.81% |
| mark_price_5m | 341275 | 2021-10-01T00:00 | 2024-12-31T23:55 | 99.72% |
| native_1h | 28512 | 2021-10-01T00:00 | 2024-12-31T23:00 | 100.00% |
| native_4h | 7128 | 2021-10-01T00:00 | 2024-12-31T20:00 | 100.00% |
| native_1d | 1188 | 2021-10-01T00:00 | 2024-12-31T00:00 | 100.00% |

## 2. PIT audit

- Visibility rule: bar visible iff close_time <= t; aux features iff time + latency <= t; funding applied in (prev close, t]
- Look-ahead guard: MarketView raises LookAheadError on negative offsets; indicators causal (tests/btc)
- Deterministic rerun (identical result hash): yes
- Truncation audit: decision journal up to 2023-07-03T00:00 identical with data after the cut removed: yes (157825 rows)
- Liquidation evaluated on: mark price

Resampling oracle (5m -> higher timeframes vs Binance native bars):

| timeframe | bars compared | differing | exact match | first differing bars (venue incidents) |
|---|---|---|---|---|
| 1h | 28512 | 3 | 99.989% | 2023-11-10T15:00, 2023-11-10T16:00, 2024-10-28T21:00 |
| 4h | 7128 | 2 | 99.972% | 2023-11-10T16:00, 2024-10-28T20:00 |
| 1d | 1188 | 0 | 100.000% |  |

## 3. Regime distribution (5m bars)

| regime | bars (all) | share (all) | share dev_2022_2023 | share val_2024 |
|---|---|---|---|---|
| TREND_UP | 93312 | 29.6% | 23.5% | 41.7% |
| TREND_DOWN | 60528 | 19.2% | 22.7% | 12.1% |
| RANGE | 70561 | 22.4% | 20.6% | 25.8% |
| HIGH_VOLATILITY | 47808 | 15.1% | 18.4% | 8.7% |
| LOW_VOLATILITY | 864 | 0.3% | 0.4% | 0.0% |
| BREAKOUT_REGIME | 12384 | 3.9% | 3.3% | 5.2% |
| UNCLEAR | 30192 | 9.6% | 11.1% | 6.5% |

## 4. Setup counts (every episode recorded: WATCH, ENTRY_READY, TRIGGERED, INVALIDATED, EXPIRED/NEVER_TRIGGERED)

| family | episodes | TRADED | INVALIDATED | NEVER_TRIGGERED | RISK_REJECTED | reached ENTRY_READY | expired (watch timeout) |
|---|---|---|---|---|---|---|---|
| BREAKDOWN_SHORT | 30 | 20 | 3 | 7 | 0 | 77% | 0% |
| BREAKOUT_LONG | 42 | 20 | 12 | 10 | 0 | 57% | 2% |
| MOMENTUM_CONTINUATION_LONG | 40 | 32 | 5 | 3 | 0 | 85% | 0% |
| MOMENTUM_CONTINUATION_SHORT | 2 | 0 | 0 | 2 | 0 | 0% | 0% |
| RESISTANCE_REJECTION_SHORT | 117 | 55 | 27 | 35 | 0 | 49% | 22% |
| SUPPORT_RECLAIM_LONG | 119 | 53 | 35 | 31 | 0 | 45% | 19% |
| TREND_PULLBACK_LONG | 96 | 53 | 13 | 30 | 0 | 59% | 19% |
| TREND_PULLBACK_SHORT | 87 | 42 | 11 | 34 | 0 | 52% | 30% |

Shadow detections (a setup fired while the single slot was occupied by another episode or an open position): 46. By family: BREAKDOWN_SHORT=1, BREAKOUT_LONG=1, MOMENTUM_CONTINUATION_LONG=11, MOMENTUM_CONTINUATION_SHORT=1, RESISTANCE_REJECTION_SHORT=11, SUPPORT_RECLAIM_LONG=9, TREND_PULLBACK_LONG=8, TREND_PULLBACK_SHORT=4

## 5. Entry frequency (naturally produced, no quota)

| metric | value |
|---|---|
| setup episodes / day | 0.486 |
| trades / day | 0.251 |
| trades / week | 1.756 |
| trades / month | 7.637 |
| median hours between entries | 66.2 |
| median holding (h) | 22.6 |
| simultaneously active trades (max) | 1 |
| setup -> entry conversion | 51.6% |

## 6. Overall performance (combined 2022-01 -> 2024-12, sequential single-position account)

| metric | value |
|---|---|
| trades | 275 |
| win rate | 47.3% |
| expectancy (R) | **0.081** (t = 0.92, sd 1.452) |
| expectancy (account) | 0.04% |
| profit factor | 1.14 |
| average winner / loser (R) | 1.421 / -1.121 |
| average winner / loser (account) | 0.68% / -0.53% |
| median R | -1.043 |
| return on underlying (sum signed BTC_RETURN) | 79.42% |
| return on margin (mean per trade) | 0.27% |
| return on account (sum) | 11.40% |

## 7. LONG vs SHORT

| cell | n | win rate | exp. R | exp. acct | PF | median R | mean hold h | reliable |
|---|---|---|---|---|---|---|---|---|
| LONG | 158 | 46.8% | 0.146 | 0.07% | 1.25 | -1.052 | 36.5 | yes |
| SHORT | 117 | 47.9% | -0.008 | -0.00% | 0.98 | -1.043 | 31.5 | yes |

## 8. Setup-family performance (all eight families shown; none hidden)

| family | side | n | win rate | exp. R | PF | median R | MFE R | MAE R | median hold h | sum acct ret | reliable |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BREAKDOWN_SHORT | SHORT | 20 | 65.0% | 0.768 | 2.96 | 0.467 | 4.584 | -0.566 | 22.7 | 7.49% | yes |
| BREAKOUT_LONG | LONG | 20 | 50.0% | 0.261 | 1.44 | -0.329 | 3.147 | -0.823 | 16.1 | 2.75% | yes |
| MOMENTUM_CONTINUATION_LONG | LONG | 32 | 43.8% | -0.053 | 0.91 | -1.039 | 1.329 | -0.881 | 35.2 | -0.96% | yes |
| RESISTANCE_REJECTION_SHORT | SHORT | 55 | 47.3% | -0.071 | 0.88 | -1.054 | 1.490 | -0.954 | 23.2 | -1.72% | yes |
| SUPPORT_RECLAIM_LONG | LONG | 53 | 43.4% | 0.012 | 1.02 | -1.059 | 1.836 | -0.867 | 18.1 | 0.48% | yes |
| TREND_PULLBACK_LONG | LONG | 53 | 50.9% | 0.358 | 1.65 | 0.430 | 2.623 | -0.813 | 28.0 | 9.26% | yes |
| TREND_PULLBACK_SHORT | SHORT | 42 | 40.5% | -0.295 | 0.56 | -1.054 | 1.307 | -0.940 | 18.2 | -5.91% | yes |

Contribution to the maximum drawdown window (trade-sequence equity curve, peak to trough): SUPPORT_RECLAIM_LONG -408 USDT (15 trades), RESISTANCE_REJECTION_SHORT -321 USDT (14 trades), TREND_PULLBACK_SHORT -135 USDT (7 trades), MOMENTUM_CONTINUATION_LONG -101 USDT (5 trades), BREAKOUT_LONG -99 USDT (5 trades), TREND_PULLBACK_LONG -31 USDT (6 trades), BREAKDOWN_SHORT +51 USDT (9 trades)

## 9. Regime performance (regime at entry)

| cell | n | win rate | exp. R | exp. acct | PF | median R | mean hold h | reliable |
|---|---|---|---|---|---|---|---|---|
| BREAKOUT_REGIME | 6 | 50.0% | 0.042 | 0.02% | 1.07 | -0.069 | 70.1 | no |
| LOW_VOLATILITY | 3 | 66.7% | 0.980 | 0.50% | 3.17 | 1.149 | 16.0 | no |
| RANGE | 80 | 52.5% | 0.211 | 0.11% | 1.38 | 0.416 | 30.6 | yes |
| TREND_DOWN | 69 | 42.0% | -0.229 | -0.11% | 0.65 | -1.056 | 30.3 | yes |
| TREND_UP | 117 | 46.2% | 0.153 | 0.07% | 1.26 | -1.056 | 38.0 | yes |

Episodes by regime at detection and share that became trades:

| regime | episodes | traded |
|---|---|---|
| BREAKOUT_REGIME | 8 | 75% |
| LOW_VOLATILITY | 3 | 100% |
| RANGE | 183 | 44% |
| TREND_DOWN | 140 | 49% |
| TREND_UP | 199 | 59% |

## 10. MFE / MAE

| metric | value |
|---|---|
| mean MFE (R) / mean MAE (R) | 2.074 / -0.862 |
| worst MAE (R) | -6.768 |
| realised mean win (R) vs mean MFE (R) | 1.421 vs 2.074 (exit design question, not tuned) |

Counterfactual target reach before the initial stop (fixed stop, no trailing):

| target | hit rate |
|---|---|
| 1R | 56.4% |
| 1.5R | 46.5% |
| 2R | 33.5% |
| 3R | 16.4% |
| TP1 executed | 46.5% |
| TP2 executed | 16.4% |
| structural target median distance (R) | 1.342 |

## 11. R-multiple distribution

| bin | count |
|---|---|
| -inf..-1.5 | 0 |
| -1.5..-1 | 143 |
| -1..-0.5 | 0 |
| -0.5..0 | 2 |
| 0..0.5 | 19 |
| 0.5..1 | 32 |
| 1..1.5 | 32 |
| 1.5..2 | 16 |
| 2..3 | 22 |
| 3..inf | 9 |

quantiles: q5=-1.23, q25=-1.12, q50=-1.04, q75=1.09, q95=2.65

## 12. Holding-period distribution

| metric | value |
|---|---|
| mean / median / p90 / max (h) | 34.4 / 22.6 / 71.7 / 240.0 |
| < 1h | 3.6% |
| 1h - 1d | 48.4% |
| 1d - 3d | 38.2% |
| > 3d | 9.8% |

## 13. Fees / slippage / funding impact (USDT, combined period)

| component | value |
|---|---|
| gross P&L before slippage | 3157.69 |
| slippage | -696.63 |
| fees | -1211.15 |
| funding (received positive) | -116.60 |
| net P&L | 1133.31 |
| expectancy R before costs / net | 0.226 / 0.081 |
| cost drag per trade (R) | 0.145 |
| fees as % of gross | 38.4% |
| funding events while in position | 1172 |

## 14. Leverage comparison (same account-risk methodology; max leverage capped, ladder truncated; margin cap 25% of equity)

| max lev | trades | risk-rejected | sized at full risk | mean lev used | mean margin/equity | mean acct risk | min stop:liq | min liq dist | liq. | exp. R | total return | max DD | mean RoM |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1x | 275 | 0 | 23.3% | 1.00 | 22.6% | 0.329% | 17.1 | 99.5% | 0 | 0.081 | 7.81% | -5.63% | 0.17% |
| 3x | 275 | 0 | 89.5% | 2.09 | 18.0% | 0.463% | 17.1 | 32.8% | 0 | 0.081 | 10.41% | -8.93% | 0.21% |
| 5x | 275 | 0 | 98.9% | 2.30 | 17.5% | 0.474% | 17.1 | 19.5% | 0 | 0.081 | 11.34% | -8.96% | 0.25% |
| 10x | 275 | 0 | 100.0% | 2.36 | 17.4% | 0.475% | 17.1 | 9.5% | 0 | 0.081 | 11.33% | -8.96% | 0.27% |

Reading: the risk per trade is identical across rows; leverage only changes how much margin a given position needs. A lower cap forces tight-stop setups to be sized below target risk (fewer trades at full risk), a higher cap uses less margin but moves liquidation closer.

## 15. Liquidation safety (mark-price based where mark data exists)

| metric | value |
|---|---|
| leverage used | 1x=64, 2x=122, 3x=60, 5x=26, 10x=3 |
| min stop-to-liquidation ratio | 17.1 |
| min liquidation distance (% / ATR 4h) | 9.50% / 8.6 |
| worst MAE (% / R) | -6.02% / -6.768 |
| worst single-trade account loss | -0.72% |
| max planned loss at stop (account) | 0.50% |
| max loss if liquidated (margin/account) | 24.96% |
| liquidations | 0 |
| trades sized below target risk | 0 |

## 16. Account equity / drawdown (sequential, one position at a time, fixed research equity for sizing)

| metric | value |
|---|---|
| initial -> final equity | 10000 -> 11133.31 |
| total return | 11.33% |
| CAGR | 3.64% |
| max drawdown (trade curve / daily mark-to-market) | -8.96% / -8.74% |
| Sharpe / Sortino (daily marks, annualised; most days flat) | 0.66 / 0.94 |
| longest losing / winning streak (trades) | 5 / 8 |
| overlapping trades | 0 |

## 17. Null benchmark comparison

Each real trade is matched with K random entries of the same side, stop distance (%), setup ATR and risk sizing, run through the identical exit engine with identical costs: (a) time-matched = uniform over the study window, (b) regime-matched = uniform over bars in the trade's regime at entry. A cost-free raw BTC return over the matched holding time is also shown.

| series | n | mean R | win rate | mean BTC_RETURN | raw BTC ret (matched hold) | strategy - null (R) | significance vs replicate means |
|---|---|---|---|---|---|---|---|
| strategy | 275 | 0.081 | 47.3% | 0.29% |  |  |  |
| null (time-matched, K=20) | 5500 | -0.058 | 41.0% | 0.09% | 0.07% | 0.138 | z=1.99, P(null >= strat)=0% |
| null (regime-matched, K=20) | 5500 | -0.038 | 41.9% | 0.16% | 0.23% | 0.119 | z=1.49, P(null >= strat)=10% |

- dev_2022_2023: strategy mean R 0.128 vs time-null -0.137 (z=2.43) / regime-null -0.064 (z=1.83)
- val_2024: strategy mean R 0.007 vs time-null -0.030 (z=0.27) / regime-null 0.008 (z=-0.01)

## 18. Rejected / never-triggered setup analysis (forward labels from the detection close, signed by side)

| outcome class | episodes | mean fwd 4h | mean fwd 24h | mean fwd 72h | P(fwd 24h > 0) | target before invalidation | invalidation before target |
|---|---|---|---|---|---|---|---|
| TRADED | 275 | 0.28% | 0.34% | 0.42% | 54% | 42% | 57% |
| INVALIDATED | 106 | -0.67% | -1.03% | -1.32% | 40% | 0% | 100% |
| NEVER_TRIGGERED | 152 | 0.49% | 1.13% | 1.72% | 64% | 66% | 34% |

Never-triggered episodes by family (did the move happen without us?):

| family | n | mean fwd 24h | mean fwd 72h | end reasons |
|---|---|---|---|---|
| BREAKDOWN_SHORT | 7 | 3.00% | 1.72% | RAN_WITHOUT_US=7 |
| BREAKOUT_LONG | 10 | 1.20% | 1.87% | RAN_WITHOUT_US=9, WATCH_TIMEOUT=1 |
| MOMENTUM_CONTINUATION_LONG | 3 | -0.54% | -0.31% | RAN_WITHOUT_US=3 |
| MOMENTUM_CONTINUATION_SHORT | 2 | -1.14% | -0.49% | RAN_WITHOUT_US=2 |
| RESISTANCE_REJECTION_SHORT | 35 | 2.10% | 2.46% | RAN_WITHOUT_US=8, REGIME_INELIGIBLE=1, WATCH_TIMEOUT=26 |
| SUPPORT_RECLAIM_LONG | 31 | 0.52% | 0.83% | RAN_WITHOUT_US=5, REGIME_INELIGIBLE=3, WATCH_TIMEOUT=23 |
| TREND_PULLBACK_LONG | 30 | 0.51% | 1.08% | RAN_WITHOUT_US=10, REGIME_INELIGIBLE=2, WATCH_TIMEOUT=18 |
| TREND_PULLBACK_SHORT | 34 | 1.12% | 2.60% | RAN_WITHOUT_US=7, REGIME_INELIGIBLE=1, WATCH_TIMEOUT=26 |

## 19. 2022-23 vs 2024 stability

| segment | period | trades | win rate | exp. R | t | PF | return | max DD | Sharpe |
|---|---|---|---|---|---|---|---|---|---|
| dev_2022_2023 | 2022-01-01 -> 2024-01-01 | 168 | 48.8% | 0.128 | 1.12 | 1.22 | 10.92% | -3.56% | 0.97 |
| val_2024 | 2024-01-01 -> 2025-01-01 | 107 | 44.9% | 0.007 | 0.05 | 1.01 | 0.42% | -9.89% | 0.10 |

Per family and segment:

| family | dev_2022_2023 | val_2024 |
|---|---|---|
| BREAKDOWN_SHORT | n=9, R=1.651, PF=7.83 | n=11, R=0.045, PF=1.09 |
| BREAKOUT_LONG | n=11, R=0.783, PF=2.40 | n=9, R=-0.377, PF=0.41 |
| MOMENTUM_CONTINUATION_LONG | n=14, R=-0.401, PF=0.44 | n=18, R=0.218, PF=1.44 |
| RESISTANCE_REJECTION_SHORT | n=39, R=0.072, PF=1.13 | n=16, R=-0.419, PF=0.45 |
| SUPPORT_RECLAIM_LONG | n=29, R=0.269, PF=1.52 | n=24, R=-0.300, PF=0.60 |
| TREND_PULLBACK_LONG | n=31, R=0.088, PF=1.14 | n=22, R=0.740, PF=2.63 |
| TREND_PULLBACK_SHORT | n=35, R=-0.277, PF=0.59 | n=7, R=-0.382, PF=0.37 |

Per side and segment:

| side | dev_2022_2023 | val_2024 |
|---|---|---|
| LONG | n=85, R=0.159, PF=1.27 | n=73, R=0.132, PF=1.23 |
| SHORT | n=83, R=0.096, PF=1.17 | n=34, R=-0.261, PF=0.60 |

## 20. Limitations

- One instrument, one venue's archive; two documented venue incidents where 5m bars are stale (listed in section 2).
- Single-position sequential account with fixed research equity for sizing (`compounding: false`); the account curve is additive in USDT.
- Fills: next-5m-bar open plus fixed slippage; stops fill at the stop (or the gap open) plus slippage; no order-book depth model; mark price used for liquidation only.
- Funding applied at the archive rate; maintenance margin rate is a single configured tier (0.5%), not the live tier ladder.
- Episode slot is single: a setup detected while another episode is open is recorded as a shadow detection, not traded; family order in the config is the priority order.
- Null benchmark re-uses the trade's own stop geometry; it answers 'does timing add information', not 'is BTC long/short profitable'.
- Daily Sharpe/Sortino are computed on mark-to-market equity with most days flat; they are indicative only. Trade counts per cell are small; `reliable` flags mark n >= min_cell_n.
- No parameter was changed after seeing results; the Phase 2 defaults for the six new families were set before the first run on these data.

## 21. Recommendation for the next phase (not implemented)

- **Verdict on the central question: NOT DEMONSTRATED.** Combined expectancy +0.081R (t=0.92, n=275, PF 1.14); dev_2022_2023: POSITIVE (+0.128R, t=1.12, separated from the null); val_2024: FLAT (+0.007R, t=0.05, not separated from the null).
- Costs: gross expectancy before slippage +0.226R becomes +0.081R net; slippage + fees - funding take 0.145R per trade (64% of the gross edge).
- Families positive in every segment (n >= 7 each): BREAKDOWN_SHORT (dev_2022_2023 +1.65R n=9, val_2024 +0.05R n=11); TREND_PULLBACK_LONG (dev_2022_2023 +0.09R n=31, val_2024 +0.74R n=22).
- Families whose sign flipped between segments (no evidence of a stable edge): BREAKOUT_LONG (dev_2022_2023 +0.78R n=11, val_2024 -0.38R n=9); MOMENTUM_CONTINUATION_LONG (dev_2022_2023 -0.40R n=14, val_2024 +0.22R n=18); RESISTANCE_REJECTION_SHORT (dev_2022_2023 +0.07R n=39, val_2024 -0.42R n=16); SUPPORT_RECLAIM_LONG (dev_2022_2023 +0.27R n=29, val_2024 -0.30R n=24).
- Families negative in every segment (reported, not removed): TREND_PULLBACK_SHORT (dev_2022_2023 -0.28R n=35, val_2024 -0.38R n=7).
- Plan vs entry mechanics: mean signed 24h forward return from the detection close is +0.34% for traded episodes and +1.13% for never-triggered episodes. The plans that never triggered moved further in the intended direction than the ones we entered: the 15m/5m entry mechanics, not the setup discovery, are the first thing to examine.
- Recommended next phase (not a parameter search): pre-register ONE structural hypothesis derived from the evidence above — the candidates, in order of evidence, are (1) the entry mechanics (confirmation/trigger) versus entering at the plan's zone, measured with the existing forward labels; (2) the exit design (mean MFE vs realised winner, counterfactual R-target table); (3) the regime eligibility table for the SHORT families. Run the single pre-registered variant on 2022-2024 once, compare against this frozen baseline on the same trades/episodes, and only then decide whether a confirmatory run on 2025+ is justified. Do not drop families or tune thresholds in that step.
- In all cases: 2025+ stays untouched until a single confirmatory run is pre-registered and approved; leverage stays at the level the liquidation table supports (section 14); no live trading.

## Appendix — frozen configuration

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
