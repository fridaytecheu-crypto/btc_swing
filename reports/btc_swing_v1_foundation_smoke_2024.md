# BTC Swing V1 — foundation smoke run (real Binance archive data, 2024)

Generated 2026-10-04 06:59 UTC · strategy `btc_leveraged_swing_v1` 1.0.0-foundation · config `199fdf6fda0b` · code `6467fb4a0408-dirty` · result hash `aa729bbdb473`

## 0. What this report is

- Paper/backtest research output. No live trading, no real money, no authenticated exchange access.
- Pre-registered default parameters, untuned. One setup family pair (TREND_PULLBACK) is implemented in the V1 foundation.
- Fees, slippage and funding are modelled; liquidation is modelled on traded-price extremes (mark price not yet used).
- A small trade count says nothing about edge in either direction; see `reliable` flags (n >= min_cell_n).

## 1. Run

- Period: 2024-01-01 00:00 -> 2025-01-01 00:00 UTC (105409 five-minute evaluations, 366.0 days)
- Information mode: MARKET_AS_OF · provider: binance_vision · latency: 0 min
- Data hashes: perp_klines_5m=`638c306cfa`, funding=`4a29807f74`
- Rule versions: features=btc-fs-1, regime=btc-regime-1, setups=btc-setup-1, episodes=btc-episode-1, risk=btc-risk-1, costs=btc-cost-1, backtest=btc-bt-1

### Regime occupancy (5m bars)

| regime | bars | share |
|---|---|---|
| TREND_UP | 43920 | 41.7% |
| TREND_DOWN | 12768 | 12.1% |
| RANGE | 27217 | 25.8% |
| HIGH_VOLATILITY | 9216 | 8.7% |
| LOW_VOLATILITY | 0 | 0.0% |
| BREAKOUT_REGIME | 5472 | 5.2% |
| UNCLEAR | 6816 | 6.5% |

## 2. Frequency (naturally produced, no quota)

- Setup episodes: 102 (0.279/day)
- Entries: 43 (0.117/day, 0.822/week)
- Setup -> entry conversion: 42.16%
- Episode end reasons: LEVEL_BREACHED=12, RAN_WITHOUT_US=15, REGIME_INELIGIBLE=2, STOP=21, TIME_LIMIT=1, TRAIL=21, WATCH_TIMEOUT=30

## 3. Trade statistics (primary metric: risk-adjusted expectancy)

| metric | value |
|---|---|
| trades | 43 |
| win rate | 51.16% |
| average win (R / account) | 1.618 / 0.80% |
| average loss (R / account) | -1.112 / -0.54% |
| expectancy (R / account) | **0.285** / 0.15% |
| t-stat of mean R | 1.13 |
| profit factor | 1.53 |
| mean / median holding (h) | 38.4 / 23.9 |
| mean MFE / MAE (R) | 2.492 / -0.777 |
| total fees / funding (USDT) | 175.82 / -49.83 |

### Returns kept separate

| basis | value |
|---|---|
| return on underlying: sum of BTC_RETURN (signed, per trade) | 18.02% (mean 0.42%) |
| return on margin: mean RETURN_ON_MARGIN | 0.60% |
| return on account: sum ACCOUNT_RETURN | 6.29% |
| account: 10000 -> 10633.67 | 6.34% |
| max drawdown (daily marks) | -3.78% |
| Sharpe (daily, annualised) | 1.43 — daily marks incl. unrealised P&L; most days flat -> interpret with care |

### LONG vs SHORT

| cell | n | win rate | exp. R | exp. acct % | PF | mean hold h | reliable (n>=20) |
|---|---|---|---|---|---|---|---|
| LONG | 35 | 54.29% | 0.459 | 0.23% | 1.91 | 40.0 | yes |
| SHORT | 8 | 37.50% | -0.475 | -0.23% | 0.30 | 31.7 | no |

### By regime at entry

| cell | n | win rate | exp. R | exp. acct % | PF | mean hold h | reliable (n>=20) |
|---|---|---|---|---|---|---|---|
| TREND_DOWN | 8 | 37.50% | -0.475 | -0.23% | 0.30 | 31.7 | no |
| TREND_UP | 35 | 54.29% | 0.459 | 0.23% | 1.91 | 40.0 | yes |

### By setup family

| cell | n | win rate | exp. R | exp. acct % | PF | mean hold h | reliable (n>=20) |
|---|---|---|---|---|---|---|---|
| TREND_PULLBACK_LONG | 35 | 54.29% | 0.459 | 0.23% | 1.91 | 40.0 | yes |
| TREND_PULLBACK_SHORT | 8 | 37.50% | -0.475 | -0.23% | 0.30 | 31.7 | no |

### By exit reason

| cell | n | win rate | exp. R | exp. acct % | PF | mean hold h | reliable (n>=20) |
|---|---|---|---|---|---|---|---|
| STOP | 21 | 0.00% | -1.112 | -0.54% | 0.00 | 18.5 | yes |
| TIME_LIMIT | 1 | 100.00% | 2.715 | 1.32% | inf | 240.0 | no |
| TRAIL | 21 | 100.00% | 1.566 | 0.78% | inf | 48.7 | yes |

### R-multiple distribution

| bin | count |
|---|---|
| -inf..-1.5 | 0 |
| -1.5..-1 | 21 |
| -1..-0.5 | 0 |
| -0.5..0 | 0 |
| 0..0.5 | 3 |
| 0.5..1 | 4 |
| 1..1.5 | 7 |
| 1.5..2 | 1 |
| 2..3 | 5 |
| 3..inf | 2 |

quantiles: q5=-1.15, q25=-1.11, q50=0.43, q75=1.31, q95=2.70

### Target evaluation (counterfactual: reached before the initial stop)

| target | hit rate |
|---|---|
| hit_rate_1R | 60.47% |
| hit_rate_1.5R | 51.16% |
| hit_rate_2R | 37.21% |
| hit_rate_3R | 20.93% |
| TP1 executed | 51.16% |
| TP2 executed | 20.93% |
| structural target, median distance (R) | 0.565 |

### Holding time

- mean 38.4 h, median 23.9 h, p90 94.7 h, max 240.0 h
- < 1h: 0.00% · 1h-1d: 51.16% · 1d-3d: 32.56% · > 3d: 16.28%

## 4. Leverage and liquidation risk (modelled explicitly)

| metric | value |
|---|---|
| leverage used (count by level) | 1x=14, 2x=19, 3x=6, 5x=4 |
| min stop-to-liquidation ratio | 24.8 |
| min liquidation distance (% / ATR) | 19.50% / 16.7 |
| worst MAE (% / R) | -4.55% / -1.445 |
| worst single-trade account loss | -0.61% |
| max planned loss at stop (account) | 0.50% |
| max loss if liquidated (margin / account) | 24.74% |
| liquidations | 0 |
| trades sized below target risk (margin cap) | 0 |

## 5. Configuration (pre-registered defaults, untuned)

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
  ingest_metrics: false
  ingest_native_timeframes:
  - 1h
  - 4h
  - 1d
  ingest_premium_index: false
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
