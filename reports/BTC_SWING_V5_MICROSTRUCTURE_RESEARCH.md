# BTC Swing V5 — Microstructure & Liquidation Driven Active Swing: research report

Generated 2026-10-06 12:56 UTC · window 2022-01-01 00:00 -> 2026-10-01 00:00 UTC · config `d18ebf19bd0c` · design freeze commit `4e9412a` · raw result hash `e4ed6d4ebf25` · code `5ff237d6c94f-dirty`

**Central question.** Do observable microstructure events — liquidation-type flushes (historically an OI-flush proxy), absorption of aggressive flow, flow-plus-open-interest continuation and price/flow divergence at range extremes — carry a repeatable signed forward edge, and does that edge survive a simple deterministic execution rule and realistic costs at roughly 1-3 trades per day?

**Validation constraint.** 2022-01..2026-09 is development data inspected by V1-V4; no V5 result on it is untouched out-of-sample. Dataset start dates are disclosed; nothing is backfilled. One frozen configuration, one Stage B run, no ML, no parameter search; the next genuine validation is forward paper testing after a freeze. Paper/backtest only; no live or paper trading engine, no exchange keys, no order placement, no real money.

## 1. V1-V4 lessons

- V1 (structural setups, frozen defaults): small positive gross edge erased by costs; untouched 2025-26 holdout net negative. V2 (learned ranking of V1 candidates): no usable ranking signal. V3 (active 4H/1H/15m/5m price-structure generator): activity profile met (1.5 trades/day, 3 h median hold) but gross expectancy slightly negative and 0.66% stops made costs 0.34R per trade. V4 (event and positioning driven, 1H events): Stage A gate failed; only one family/side showed a small-sample signal; stops at 1.54% kept cost drag low but there was no gross edge to protect.
- V5 therefore moves the event clock to 5 minutes and to the flow itself (aggressor side from every trade, cumulative volume delta, open-interest change, basis/premium/funding), measures event edge at 15m-12h horizons BEFORE any execution (Stage A, with a per-family gate, bootstrap intervals, strength monotonicity and year stability), keeps one generic execution rule for all families, and floors the stop at 1.25 ATR(1h) so costs stay a small fraction of 1R.

## 2. V5 hypothesis

- Four pre-registered microstructure event families (A liquidation/OI-flush continuation, B absorption reversal, C flow+OI continuation, D flow divergence reversal), defined on z-scores of trailing 30-day distributions of trade-flow, open-interest and price features at 5-minute granularity, carry a positive signed forward return over 1-4 h that is visible in first-in-cluster events, monotone in event strength and stable across years; with a single 15m confirmation, a 5m zone entry, a volatility-floored structural stop and a generic TP1/TP2/trail exit, the edge survives taker fees, slippage and funding at 0.5-3 trades per day.

## 3. Design freeze proof

| check | value |
|---|---|
| design document | `docs/BTC_SWING_V5_DESIGN.md` and `config/btc_swing_v5.yaml`, committed at `4e9412a` before any feature, event, Stage A or Stage B result existed |
| config hash | `d18ebf19bd0cde67be1c27683d3e7ad0385d2456a6edaa1fe55146f013096177` |
| strategy / rule versions | btc_swing_v5_microstructure 5.0.0-research; features=v5-feat-1, events=v5-event-1, exits=v5-exit-1 |
| code version at run | 5ff237d6c94f-dirty |
| runs of the frozen configuration | one Stage A scan; one raw Stage B run; the overlay, 0.5%, cost-sensitivity and leverage-cap streams and the audits never changed a rule |
| deterministic rerun identical | yes |

## 4. Historical data availability (audit before any result; nothing fabricated, nothing backfilled)

| field | source | available | used in V5 history | note |
|---|---|---|---|---|
| aggTrades (every trade, aggressor flag) | Binance Vision futures/um/daily/aggTrades | yes, 2019-12-31 -> present | yes: 5m aggregates (buy/sell qty, counts, big trades >= 1 BTC, vwap) | 5m aggregates of archive aggTrades (taker side from is_buyer_maker); raw trade rows not retained, sha256 of every archive file recorded |
| order-book depth | Binance Vision futures/um/daily/bookDepth | yes, 2023-01-01 -> present (+-1..5% snapshots ~30 s) | diagnostics only (no event uses it) | archive bookDepth snapshots (+-1..5% depth, ~30 s cadence) from 2023-01-01; last snapshot <= T; diagnostics only, no event uses it |
| best bid/ask (bookTicker) | Binance Vision | partial (2023-05-16 -> 2024-03-30 only) | no | too short and discontinuous; not ingested |
| liquidations | Binance Vision liquidationSnapshot | NOT available for BTCUSDT UM (404) | no | NOT AVAILABLE historically (no archive dataset for BTCUSDT UM); forward only via the Bybit collector; family A uses the labelled OI-flush proxy |
| open interest | Binance Vision metrics (5m) | yes | yes: level, 5m/15m/1h change, acceleration, z | archive metrics (5m), observation time + latency <= T |
| mark / index price | Binance Vision markPriceKlines / indexPriceKlines | yes | yes: liquidation on mark; basis = perp/index - 1 | 5m index klines for the basis feature |
| premium index | Binance Vision premiumIndexKlines | yes | yes: 1h mean, z |  |
| funding | Binance Vision fundingRate | yes | yes: level, change, z (90 obs) |  |
| taker buy/sell ratios | Binance Vision metrics + klines taker-buy volume | yes | kline taker-buy volume is the fallback for a missing aggTrades day |  |
| volume delta / CVD | derived from aggTrades aggregates | yes | yes | delta = taker buy - taker sell quantity per 5m bar |
| Bybit historical trades | public.bybit.com/trading | reachable | no | Binance is the historical instrument; Bybit is the forward collector venue |

Coverage inside the study window:

| dataset | rows | first | last | share of 5m bars covered |
|---|---|---|---|---|
| aggtrades_flow | 508299 | 2021-12-01 00:00 | 2026-09-30 23:55 | 100.0% |
| book_depth | 39266738 | 2023-01-01 00:06 | 2026-09-30 23:59 | 78.4% |
| index_klines | 525884 | 2021-10-01 00:00 | 2026-09-30 23:55 | 100.0% |
| open_interest |  |  |  | 99.9% |
| liquidations | 0 |  |  | 0.0% |
| funding |  |  |  | 100.0% |
| premium |  |  |  | 99.8% |

- aggTrades aggregates cover 100.0% of the 5m bars in the window; the kline taker-buy fallback covers 0.0%. Order-book features exist only from 2023-01-01 and are never an event input.

| archive dataset | rows | first | last | rows in window | expected | coverage |
|---|---|---|---|---|---|---|
| perp_klines_5m | 525888 | 2021-10-01T00:00 | 2026-09-30T23:55 | 499392 | 499392 | 100.00% |
| funding | 5478 | 2021-10-01T00:00 | 2026-09-30T16:00 | 5202 | 5202 | 100.00% |
| metrics_5min | 525688 | 2021-10-01T00:00 | 2026-09-30T23:55 | 499259 | 499392 | 99.97% |
| premium_index_5m | 525015 | 2021-10-01T00:00 | 2026-09-30T23:55 | 498519 | 499392 | 99.83% |
| mark_price_5m | 524731 | 2021-10-01T00:00 | 2026-09-30T23:55 | 498235 | 499392 | 99.77% |
| native_1h | 43824 | 2021-10-01T00:00 | 2026-09-30T23:00 | 41616 | 41616 | 100.00% |
| native_4h | 10956 | 2021-10-01T00:00 | 2026-09-30T20:00 | 10404 | 10404 | 100.00% |
| native_1d | 1826 | 2021-10-01T00:00 | 2026-09-30T00:00 | 1734 | 1734 | 100.00% |

## 5. Forward-data availability (Bybit public WebSocket, no authentication)

- Collector (`btc_swing/v5/collector.py`): `wss://stream.bybit.com/v5/public/linear`, topics publicTrade, orderbook.50, tickers (mark, index, funding, open interest, best bid/ask) and allLiquidation for BTCUSDT; every message is stored verbatim as an immutable JSONL row (ts_received_ms, ts_exchange_ms, symbol, channel, type, schema_version, sha256 of the payload, raw payload) in hourly files plus a state file for resumption; duplicate suppression on topic + exchange ts + update id; order-book sequence-gap detection (`u` must increase by one between deltas, snapshot resets); ping heartbeat and a 30 s stale timeout; reconnect with 1-30 s backoff; periodic flush/state save. Verification results are in section 33.
- The liquidation stream therefore exists ONLY forward; the historical family A is the labelled OI-flush proxy and the forward collector makes the liquidation-burst template measurable later without any re-fitting.

## 6. PIT audit

- 5m/1H/4H bars with close_time <= t; aggTrades aggregates of the same bar; last order-book snapshot <= t; metrics/funding/premium rows with time + latency <= t; index kline of the same bar; rolling z-scores over previous rows only; fill at the next 5m open; trail moves applied from the next bar.
- Deterministic rerun: yes. Truncation audit with all data after 2024-05-17T00:00 removed: decisions identical yes (249697 rows); events identical yes (4629 events up to one day before the cut).
- Resampling oracle vs native archive bars: 1h: 43824 bars, 3 differ, 4h: 10956 bars, 2 differ, 1d: 1826 bars, 0 differ. Liquidation basis: mark.
- Forward labels (Stage A) are computed strictly from bars after the event bar; nothing in the feature frame reads a later row (rolling statistics exclude the current row). No order book is reconstructed from candles; no liquidation history is approximated.

## 7. Microstructure feature definitions (frozen `v5-feat-1`; z = rolling z-score over the previous 30 days of 5m rows, excluding the current row, min 10 days)

- Trade flow: taker buy/sell quantity per 5m bar (aggTrades `is_buyer_maker == false` = taker buy; kline taker-buy volume as fallback), delta = buy - sell, delta over 15m / 1h, imbalance_1h = delta_1h / (buy+sell)_1h, CVD = cumulative delta, CVD slope 1h/4h (= delta over the window per bar), flow acceleration = delta_15m minus the previous 15m delta, big-trade imbalance over 1h (trades >= 1 BTC); z-scores of imbalance_1h, cvd_slope_1h/4h, flow acceleration and big-trade imbalance; divergence = z(ret_1h) - z(cvd_slope_1h).
- Order book (2023-01-01 onwards, last snapshot <= T, stale after 1 h): bid/ask depth within 1% and 5%, imbalance_1 and imbalance_5, 1h change of imbalance_1, z-scores of bid1, ask1 and total depth. Diagnostics only.
- Open interest (metrics 5m, observation time + latency <= T): level; 5m/15m/1h change; acceleration (15m change minus the previous one); z of the 1h and 15m change.
- Derivatives: funding level/change/z (90 observations); premium-index 1h mean and z; basis = perp close / index close - 1, 1h change, z.
- Price/volume: returns 5m/15m/1h/4h with z of ret_15m and ret_1h; realised volatility 1h/24h and its z; ATR14 from the 1H series; 5m and 1h volume z; range position over 48 h; new 24h high/low flags (previous 288 bars); 4H trend state (EMA21/EMA50/close) and 1H alignment (close vs EMA21). Regime label = 4H trend x 24h-volatility z state.
- Feature rows in the window: 499392; missing share: imbalance_1h_z 0.0%, cvd_slope_1h_z 0.0%, oi_chg_1h_z 0.2%, fund_z 0.0%, prem_z 0.2%, basis_z 0.0%, book_imb_1 21.6%, vol_1h_z 0.0%.

## 8. Liquidation events (family A, historical OI-flush PROXY)

- Definition (LONG, SHORT mirrored): signed 1h-return z >= +2.0, OI 1h-change z <= -1.5, 1h volume z >= +1.5, signed 1h taker-imbalance z >= +1.0; strength = vol z - OI-change z; direction = with the impulse. Forward-only template: the same with the liquidation-stream burst z replacing the OI flush.

- Events: 2947 (639 first-in-cluster: no same family/side event in the previous 12 bars); LONG 1317 / SHORT 1630 (all events). Mean MFE 2.68 ATR / mean MAE -1.97 ATR over 12 h (medians 1.72 / -1.46); strength p10/p50/p90 3.35 / 4.29 / 6.51.

First-in-cluster events, both sides (the gate population):

| horizon | n | mean signed return | median | hit rate | t | 95% bootstrap CI |
|---|---|---|---|---|---|---|
| 0.25 h | 639 | -0.045% | -0.069% | 43.7% | -1.71 |  |
| 0.5 h | 639 | -0.053% | -0.091% | 43.5% | -1.57 |  |
| 1 h | 639 | 0.016% | -0.051% | 47.3% | 0.36 | [-0.067%, 0.096%] |
| 2 h | 639 | -0.004% | -0.086% | 46.0% | -0.08 |  |
| 4 h | 639 | 0.034% | -0.075% | 47.3% | 0.53 | [-0.088%, 0.158%] |
| 8 h | 639 | 0.072% | 0.020% | 50.7% | 0.86 |  |
| 12 h | 639 | 0.092% | 0.050% | 51.2% | 0.97 |  |

All events (clusters included):

| horizon | n | mean signed return | median | hit rate | t | 95% bootstrap CI |
|---|---|---|---|---|---|---|
| 0.25 h | 2947 | 0.009% | -0.010% | 48.5% | 0.69 |  |
| 0.5 h | 2947 | 0.022% | -0.009% | 49.4% | 1.39 |  |
| 1 h | 2947 | 0.039% | 0.008% | 50.4% | 1.98 | [0.000%, 0.079%] |
| 2 h | 2947 | 0.026% | -0.041% | 47.8% | 1.04 |  |
| 4 h | 2947 | 0.035% | -0.060% | 47.5% | 1.14 | [-0.024%, 0.096%] |
| 8 h | 2947 | 0.135% | -0.018% | 49.5% | 3.45 |  |
| 12 h | 2947 | 0.183% | 0.075% | 51.8% | 4.20 |  |

LONG (first-in-cluster):

| horizon | n | mean signed return | median | hit rate | t | 95% bootstrap CI |
|---|---|---|---|---|---|---|
| 0.25 h | 260 | -0.044% | -0.015% | 48.5% | -1.03 |  |
| 0.5 h | 260 | -0.064% | -0.050% | 46.5% | -1.18 |  |
| 1 h | 260 | 0.078% | 0.025% | 51.2% | 1.11 | [-0.063%, 0.209%] |
| 2 h | 260 | 0.082% | -0.056% | 48.8% | 0.99 |  |
| 4 h | 260 | 0.075% | -0.069% | 46.9% | 0.67 | [-0.143%, 0.286%] |
| 8 h | 260 | 0.144% | 0.110% | 53.1% | 1.02 |  |
| 12 h | 260 | 0.312% | 0.217% | 56.2% | 2.03 |  |

SHORT (first-in-cluster):

| horizon | n | mean signed return | median | hit rate | t | 95% bootstrap CI |
|---|---|---|---|---|---|---|
| 0.25 h | 379 | -0.046% | -0.098% | 40.4% | -1.37 |  |
| 0.5 h | 379 | -0.045% | -0.106% | 41.4% | -1.05 |  |
| 1 h | 379 | -0.027% | -0.089% | 44.6% | -0.49 | [-0.140%, 0.084%] |
| 2 h | 379 | -0.063% | -0.128% | 44.1% | -0.91 |  |
| 4 h | 379 | 0.006% | -0.075% | 47.5% | 0.08 | [-0.143%, 0.155%] |
| 8 h | 379 | 0.023% | -0.022% | 49.1% | 0.22 |  |
| 12 h | 379 | -0.059% | -0.039% | 47.8% | -0.49 |  |

Stage A null (random bars, same count and side mix, 200 replications):

| horizon | events mean | time-matched null mean (sd) | z | P(null >= events) | regime-matched null mean (sd) | z | P(null >= events) |
|---|---|---|---|---|---|---|---|
| 1 h | 0.016% | -0.001% (0.022%) | 0.75 | 23% | -0.003% (0.025%) | 0.74 | 24% |
| 4 h | 0.034% | -0.004% (0.041%) | 0.92 | 17% | -0.001% (0.046%) | 0.75 | 22% |

By regime (first-in-cluster, 4H trend x 24h realised-volatility state):

| period | n | 1 h mean | t | 4 h mean | t |
|---|---|---|---|---|---|
| DOWN_COMPRESSION | 10 | -0.165% | -0.80 | -0.054% | -0.16 |
| DOWN_EXPANSION | 117 | 0.058% | 0.43 | 0.017% | 0.09 |
| DOWN_NORMAL | 155 | 0.034% | 0.42 | 0.079% | 0.65 |
| NEUTRAL_COMPRESSION | 2 | -0.102% | n/a | 0.335% | n/a |
| NEUTRAL_EXPANSION | 19 | 0.098% | 0.24 | 0.538% | 1.23 |
| NEUTRAL_NORMAL | 74 | -0.042% | -0.45 | 0.003% | 0.02 |
| UP_COMPRESSION | 3 | 1.436% | 1.72 | 1.929% | 1.48 |
| UP_EXPANSION | 70 | -0.108% | -0.70 | 0.026% | 0.13 |
| UP_NORMAL | 189 | 0.022% | 0.39 | -0.057% | -0.65 |

Gate evaluation (pre-declared): 1h: mean 0.016% (t 0.36, CI [-0.067%, 0.096%]), positive in 2/5 years, top tercile -0.067% vs bottom 0.067% -> fail; 4h: mean 0.034% (t 0.53, CI [-0.088%, 0.158%]), positive in 3/5 years, top tercile 0.038% vs bottom 0.111% -> fail; n >= 100: yes (n=639) -> family FAILED the gate.

## 9. Absorption events (family B)

- Definition (LONG, SHORT mirrored): signed 1h taker-imbalance z <= -2.0 (heavy aggressive selling), 1h volume z >= +1.0, signed 1h-return z >= -0.5 (impact absorbed); strength = (-signed imbalance z) - |return z|.

- Events: 57 (21 first-in-cluster: no same family/side event in the previous 12 bars); LONG 11 / SHORT 46 (all events). Mean MFE 1.61 ATR / mean MAE -1.62 ATR over 12 h (medians 1.41 / -1.06); strength p10/p50/p90 1.65 / 1.93 / 2.13.

First-in-cluster events, both sides (the gate population):

| horizon | n | mean signed return | median | hit rate | t | 95% bootstrap CI |
|---|---|---|---|---|---|---|
| 0.25 h | 21 | -0.015% | 0.037% | 52.4% | -0.27 |  |
| 0.5 h | 21 | -0.012% | 0.056% | 57.1% | -0.18 |  |
| 1 h | 21 | -0.027% | 0.001% | 52.4% | -0.20 | [-0.296%, 0.210%] |
| 2 h | 21 | -0.042% | 0.118% | 61.9% | -0.22 |  |
| 4 h | 21 | -0.265% | 0.235% | 71.4% | -0.79 | [-0.960%, 0.307%] |
| 8 h | 21 | -0.030% | 0.162% | 52.4% | -0.11 |  |
| 12 h | 21 | -0.199% | 0.038% | 61.9% | -0.72 |  |

All events (clusters included):

| horizon | n | mean signed return | median | hit rate | t | 95% bootstrap CI |
|---|---|---|---|---|---|---|
| 0.25 h | 57 | 0.013% | 0.041% | 61.4% | 0.49 |  |
| 0.5 h | 57 | 0.042% | 0.091% | 63.2% | 1.28 |  |
| 1 h | 57 | 0.013% | 0.051% | 57.9% | 0.20 | [-0.115%, 0.131%] |
| 2 h | 57 | 0.031% | 0.136% | 68.4% | 0.36 |  |
| 4 h | 57 | -0.259% | 0.228% | 61.4% | -1.63 | [-0.578%, 0.034%] |
| 8 h | 57 | -0.308% | -0.200% | 38.6% | -2.10 |  |
| 12 h | 57 | -0.546% | -0.370% | 36.8% | -3.80 |  |

LONG (first-in-cluster):

| horizon | n | mean signed return | median | hit rate | t | 95% bootstrap CI |
|---|---|---|---|---|---|---|
| 0.25 h | 8 | -0.028% | -0.069% | 37.5% | -0.23 |  |
| 0.5 h | 8 | -0.085% | -0.076% | 37.5% | -0.61 |  |
| 1 h | 8 | -0.118% | -0.061% | 37.5% | -0.34 | [-0.848%, 0.431%] |
| 2 h | 8 | -0.042% | 0.141% | 87.5% | -0.10 |  |
| 4 h | 8 | -0.173% | 0.413% | 75.0% | -0.24 | [-1.663%, 0.750%] |
| 8 h | 8 | 0.499% | 0.441% | 75.0% | 2.13 |  |
| 12 h | 8 | 0.021% | 0.423% | 62.5% | 0.05 |  |

SHORT (first-in-cluster):

| horizon | n | mean signed return | median | hit rate | t | 95% bootstrap CI |
|---|---|---|---|---|---|---|
| 0.25 h | 13 | -0.007% | 0.050% | 61.5% | -0.13 |  |
| 0.5 h | 13 | 0.033% | 0.137% | 69.2% | 0.54 |  |
| 1 h | 13 | 0.029% | 0.051% | 61.5% | 0.36 | [-0.132%, 0.181%] |
| 2 h | 13 | -0.043% | -0.005% | 46.2% | -0.25 |  |
| 4 h | 13 | -0.321% | 0.235% | 69.2% | -0.93 | [-1.133%, 0.233%] |
| 8 h | 13 | -0.355% | -0.098% | 38.5% | -0.85 |  |
| 12 h | 13 | -0.335% | 0.023% | 61.5% | -0.88 |  |

Stage A null (random bars, same count and side mix, 200 replications):

| horizon | events mean | time-matched null mean (sd) | z | P(null >= events) | regime-matched null mean (sd) | z | P(null >= events) |
|---|---|---|---|---|---|---|---|
| 1 h | -0.027% | 0.002% (0.135%) | -0.22 | 62% | -0.001% (0.124%) | -0.21 | 63% |
| 4 h | -0.265% | 0.008% (0.239%) | -1.14 | 88% | -0.012% (0.257%) | -0.98 | 83% |

By regime (first-in-cluster, 4H trend x 24h realised-volatility state):

| period | n | 1 h mean | t | 4 h mean | t |
|---|---|---|---|---|---|
| DOWN_EXPANSION | 2 | -1.226% | n/a | -2.557% | n/a |
| DOWN_NORMAL | 4 | 0.133% | 0.38 | -1.147% | -1.05 |
| NEUTRAL_NORMAL | 1 | -0.122% | n/a | -0.659% | n/a |
| UP_EXPANSION | 2 | 0.153% | n/a | 0.422% | n/a |
| UP_NORMAL | 12 | 0.098% | 1.30 | 0.330% | 2.95 |

Gate evaluation (pre-declared): 1h: mean -0.027% (t -0.20, CI [-0.296%, 0.210%]), positive in 3/4 years, top tercile n/a vs bottom n/a -> fail; 4h: mean -0.265% (t -0.79, CI [-0.960%, 0.307%]), positive in 1/4 years, top tercile n/a vs bottom n/a -> fail; n >= 100: no (n=21) -> family FAILED the gate.

## 10. Flow / OI continuation events (family C)

- Definition (LONG, SHORT mirrored): signed 1h-return z >= +1.0, signed imbalance z >= +1.0, signed CVD-slope z >= +1.0, OI 1h-change z >= +1.0, not crowded (signed funding z and premium z <= +2.0); strength = mean of the three signed flow/OI z-scores.

- Events: 5729 (1527 first-in-cluster: no same family/side event in the previous 12 bars); LONG 3879 / SHORT 1850 (all events). Mean MFE 2.52 ATR / mean MAE -2.00 ATR over 12 h (medians 1.71 / -1.43); strength p10/p50/p90 1.24 / 1.63 / 2.46.

First-in-cluster events, both sides (the gate population):

| horizon | n | mean signed return | median | hit rate | t | 95% bootstrap CI |
|---|---|---|---|---|---|---|
| 0.25 h | 1527 | 0.000% | -0.034% | 44.9% | 0.02 |  |
| 0.5 h | 1527 | 0.004% | -0.033% | 45.4% | 0.25 |  |
| 1 h | 1527 | 0.031% | -0.037% | 46.2% | 1.57 | [-0.006%, 0.068%] |
| 2 h | 1527 | 0.048% | -0.050% | 47.1% | 1.73 |  |
| 4 h | 1527 | 0.061% | -0.040% | 48.0% | 1.68 | [-0.009%, 0.137%] |
| 8 h | 1527 | 0.101% | 0.026% | 50.7% | 2.08 |  |
| 12 h | 1525 | 0.166% | 0.033% | 51.2% | 2.90 |  |

All events (clusters included):

| horizon | n | mean signed return | median | hit rate | t | 95% bootstrap CI |
|---|---|---|---|---|---|---|
| 0.25 h | 5729 | 0.006% | -0.028% | 46.2% | 1.07 |  |
| 0.5 h | 5729 | 0.023% | -0.023% | 47.5% | 3.18 |  |
| 1 h | 5729 | 0.056% | -0.021% | 48.0% | 5.52 | [0.037%, 0.076%] |
| 2 h | 5729 | 0.061% | -0.025% | 48.5% | 4.50 |  |
| 4 h | 5729 | 0.100% | -0.027% | 48.4% | 5.72 | [0.067%, 0.134%] |
| 8 h | 5729 | 0.192% | 0.034% | 51.1% | 7.72 |  |
| 12 h | 5718 | 0.287% | 0.062% | 51.7% | 9.52 |  |

LONG (first-in-cluster):

| horizon | n | mean signed return | median | hit rate | t | 95% bootstrap CI |
|---|---|---|---|---|---|---|
| 0.25 h | 942 | -0.005% | -0.034% | 45.5% | -0.32 |  |
| 0.5 h | 942 | 0.021% | -0.022% | 46.5% | 1.13 |  |
| 1 h | 942 | 0.054% | -0.018% | 48.4% | 2.16 | [0.005%, 0.102%] |
| 2 h | 942 | 0.071% | -0.034% | 48.1% | 1.97 |  |
| 4 h | 942 | 0.105% | 0.001% | 50.0% | 2.34 | [0.016%, 0.194%] |
| 8 h | 942 | 0.184% | 0.074% | 52.8% | 3.00 |  |
| 12 h | 940 | 0.207% | 0.088% | 52.2% | 2.85 |  |

SHORT (first-in-cluster):

| horizon | n | mean signed return | median | hit rate | t | 95% bootstrap CI |
|---|---|---|---|---|---|---|
| 0.25 h | 585 | 0.008% | -0.034% | 43.9% | 0.43 |  |
| 0.5 h | 585 | -0.024% | -0.049% | 43.8% | -1.05 |  |
| 1 h | 585 | -0.007% | -0.067% | 42.7% | -0.22 | [-0.067%, 0.053%] |
| 2 h | 585 | 0.010% | -0.083% | 45.5% | 0.23 |  |
| 4 h | 585 | -0.010% | -0.090% | 44.8% | -0.17 | [-0.132%, 0.108%] |
| 8 h | 585 | -0.032% | -0.102% | 47.4% | -0.41 |  |
| 12 h | 585 | 0.101% | -0.013% | 49.6% | 1.08 |  |

Stage A null (random bars, same count and side mix, 200 replications):

| horizon | events mean | time-matched null mean (sd) | z | P(null >= events) | regime-matched null mean (sd) | z | P(null >= events) |
|---|---|---|---|---|---|---|---|
| 1 h | 0.031% | -0.001% (0.015%) | 2.16 | 1% | 0.001% (0.015%) | 2.01 | 2% |
| 4 h | 0.061% | -0.000% (0.028%) | 2.16 | 1% | 0.003% (0.029%) | 1.98 | 2% |

By regime (first-in-cluster, 4H trend x 24h realised-volatility state):

| period | n | 1 h mean | t | 4 h mean | t |
|---|---|---|---|---|---|
| DOWN_COMPRESSION | 40 | -0.056% | -0.52 | -0.040% | -0.20 |
| DOWN_EXPANSION | 162 | 0.032% | 0.39 | 0.143% | 0.87 |
| DOWN_NORMAL | 421 | 0.067% | 1.83 | 0.084% | 1.23 |
| NEUTRAL_COMPRESSION | 16 | -0.037% | -0.32 | 0.138% | 0.82 |
| NEUTRAL_EXPANSION | 30 | 0.050% | 0.26 | -0.077% | -0.22 |
| NEUTRAL_NORMAL | 198 | -0.019% | -0.37 | 0.065% | 0.71 |
| UP_COMPRESSION | 61 | 0.112% | 1.36 | 0.232% | 1.68 |
| UP_EXPANSION | 84 | -0.040% | -0.36 | 0.145% | 0.85 |
| UP_NORMAL | 515 | 0.031% | 1.04 | -0.007% | -0.13 |

Gate evaluation (pre-declared): 1h: mean 0.031% (t 1.57, CI [-0.006%, 0.068%]), positive in 4/5 years, top tercile 0.066% vs bottom 0.018% -> fail; 4h: mean 0.061% (t 1.68, CI [-0.009%, 0.137%]), positive in 5/5 years, top tercile 0.071% vs bottom 0.097% -> fail; n >= 100: yes (n=1527) -> family FAILED the gate.

## 11. Divergence events (family D)

- Definition: SHORT when the 5m high exceeds the previous 288-bar high with CVD-slope z <= 0 and OI 1h-change z <= 0 (strength = -(CVD z + OI z)/2); LONG mirror at a new 24h low with CVD-slope z >= 0 and OI 1h-change z <= 0 (OI term unsigned: no new positioning).

- Events: 248 (191 first-in-cluster: no same family/side event in the previous 12 bars); LONG 154 / SHORT 94 (all events). Mean MFE 2.20 ATR / mean MAE -2.10 ATR over 12 h (medians 1.72 / -1.29); strength p10/p50/p90 0.13 / 0.40 / 1.04.

First-in-cluster events, both sides (the gate population):

| horizon | n | mean signed return | median | hit rate | t | 95% bootstrap CI |
|---|---|---|---|---|---|---|
| 0.25 h | 191 | 0.035% | 0.046% | 57.1% | 1.31 |  |
| 0.5 h | 191 | 0.053% | 0.016% | 51.8% | 1.24 |  |
| 1 h | 191 | 0.051% | 0.098% | 58.6% | 0.84 | [-0.072%, 0.170%] |
| 2 h | 191 | 0.058% | 0.124% | 60.7% | 0.76 |  |
| 4 h | 191 | 0.060% | 0.173% | 60.2% | 0.60 | [-0.135%, 0.265%] |
| 8 h | 191 | 0.104% | 0.246% | 59.2% | 0.81 |  |
| 12 h | 191 | 0.160% | 0.329% | 63.4% | 1.09 |  |

All events (clusters included):

| horizon | n | mean signed return | median | hit rate | t | 95% bootstrap CI |
|---|---|---|---|---|---|---|
| 0.25 h | 248 | 0.019% | 0.032% | 55.2% | 0.85 |  |
| 0.5 h | 248 | 0.031% | 0.001% | 50.0% | 0.88 |  |
| 1 h | 248 | 0.016% | 0.084% | 56.0% | 0.33 | [-0.087%, 0.107%] |
| 2 h | 248 | 0.035% | 0.109% | 60.1% | 0.55 |  |
| 4 h | 248 | 0.052% | 0.146% | 58.1% | 0.64 | [-0.099%, 0.210%] |
| 8 h | 248 | 0.058% | 0.201% | 57.3% | 0.54 |  |
| 12 h | 248 | 0.138% | 0.259% | 60.5% | 1.14 |  |

LONG (first-in-cluster):

| horizon | n | mean signed return | median | hit rate | t | 95% bootstrap CI |
|---|---|---|---|---|---|---|
| 0.25 h | 113 | 0.049% | 0.046% | 58.4% | 1.24 |  |
| 0.5 h | 113 | 0.044% | 0.031% | 52.2% | 0.70 |  |
| 1 h | 113 | 0.007% | 0.109% | 57.5% | 0.08 | [-0.168%, 0.177%] |
| 2 h | 113 | 0.011% | 0.137% | 65.5% | 0.11 |  |
| 4 h | 113 | -0.061% | 0.090% | 58.4% | -0.44 | [-0.330%, 0.220%] |
| 8 h | 113 | 0.022% | 0.155% | 55.8% | 0.11 |  |
| 12 h | 113 | 0.209% | 0.109% | 60.2% | 0.96 |  |

SHORT (first-in-cluster):

| horizon | n | mean signed return | median | hit rate | t | 95% bootstrap CI |
|---|---|---|---|---|---|---|
| 0.25 h | 78 | 0.014% | 0.045% | 55.1% | 0.46 |  |
| 0.5 h | 78 | 0.067% | 0.005% | 51.3% | 1.27 |  |
| 1 h | 78 | 0.114% | 0.095% | 60.3% | 1.52 | [-0.022%, 0.261%] |
| 2 h | 78 | 0.126% | 0.080% | 53.8% | 1.04 |  |
| 4 h | 78 | 0.237% | 0.259% | 62.8% | 1.70 | [-0.016%, 0.490%] |
| 8 h | 78 | 0.225% | 0.402% | 64.1% | 1.49 |  |
| 12 h | 78 | 0.088% | 0.408% | 67.9% | 0.50 |  |

Stage A null (random bars, same count and side mix, 200 replications):

| horizon | events mean | time-matched null mean (sd) | z | P(null >= events) | regime-matched null mean (sd) | z | P(null >= events) |
|---|---|---|---|---|---|---|---|
| 1 h | 0.051% | 0.004% (0.039%) | 1.19 | 10% | 0.002% (0.037%) | 1.31 | 10% |
| 4 h | 0.060% | 0.011% (0.080%) | 0.62 | 28% | -0.003% (0.070%) | 0.90 | 17% |

By regime (first-in-cluster, 4H trend x 24h realised-volatility state):

| period | n | 1 h mean | t | 4 h mean | t |
|---|---|---|---|---|---|
| DOWN_COMPRESSION | 11 | 0.027% | 0.41 | 0.172% | 1.73 |
| DOWN_EXPANSION | 11 | -0.178% | -0.26 | -0.858% | -0.90 |
| DOWN_NORMAL | 53 | 0.115% | 1.15 | 0.207% | 1.46 |
| NEUTRAL_COMPRESSION | 3 | -0.243% | -1.10 | -0.290% | -1.60 |
| NEUTRAL_EXPANSION | 3 | 0.265% | 0.72 | -0.605% | -0.34 |
| NEUTRAL_NORMAL | 32 | 0.001% | 0.01 | 0.051% | 0.36 |
| UP_COMPRESSION | 11 | -0.144% | -0.74 | -0.475% | -1.40 |
| UP_EXPANSION | 15 | -0.049% | -0.22 | -0.520% | -1.24 |
| UP_NORMAL | 52 | 0.143% | 1.39 | 0.426% | 2.22 |

Gate evaluation (pre-declared): 1h: mean 0.051% (t 0.84, CI [-0.072%, 0.170%]), positive in 3/5 years, top tercile 0.137% vs bottom 0.007% -> fail; 4h: mean 0.060% (t 0.60, CI [-0.135%, 0.265%]), positive in 4/5 years, top tercile 0.061% vs bottom 0.032% -> fail; n >= 100: yes (n=191) -> family FAILED the gate.

## 12. Stage A forward returns (pooled; signed in the event direction from the event bar close)

Pooled, all events:

| horizon | n | mean signed return | median | hit rate | t | 95% bootstrap CI |
|---|---|---|---|---|---|---|
| 0.25 h | 8981 | 0.007% | -0.021% | 47.3% | 1.31 |  |
| 0.5 h | 8981 | 0.023% | -0.018% | 48.3% | 3.28 |  |
| 1 h | 8981 | 0.049% | -0.012% | 49.1% | 5.30 | [0.031%, 0.067%] |
| 2 h | 8981 | 0.048% | -0.021% | 48.7% | 4.02 |  |
| 4 h | 8981 | 0.075% | -0.030% | 48.5% | 4.93 | [0.047%, 0.103%] |
| 8 h | 8981 | 0.166% | 0.019% | 50.7% | 8.06 |  |
| 12 h | 8970 | 0.243% | 0.069% | 51.9% | 10.05 |  |

Pooled, first-in-cluster events only:

| horizon | n | mean signed return | median | hit rate | t | 95% bootstrap CI |
|---|---|---|---|---|---|---|
| 0.25 h | 2378 | -0.009% | -0.034% | 45.6% | -0.90 |  |
| 0.5 h | 2378 | -0.008% | -0.037% | 45.5% | -0.58 |  |
| 1 h | 2378 | 0.028% | -0.032% | 47.6% | 1.56 | [-0.007%, 0.065%] |
| 2 h | 2378 | 0.034% | -0.042% | 48.0% | 1.43 |  |
| 4 h | 2378 | 0.050% | -0.024% | 49.0% | 1.68 | [-0.004%, 0.108%] |
| 8 h | 2378 | 0.093% | 0.045% | 51.4% | 2.32 |  |
| 12 h | 2376 | 0.142% | 0.073% | 52.3% | 3.07 |  |

Unconditional BTC forward return of every 5m bar over the same horizons (unsigned drift and dispersion):

| horizon | mean | std | mean |return| |
|---|---|---|---|
| 0.25 h | 0.001% | 0.27% | 0.17% |
| 0.5 h | 0.001% | 0.38% | 0.24% |
| 1 h | 0.003% | 0.54% | 0.33% |
| 2 h | 0.006% | 0.76% | 0.47% |
| 4 h | 0.011% | 1.06% | 0.67% |
| 8 h | 0.022% | 1.50% | 0.98% |
| 12 h | 0.034% | 1.84% | 1.22% |

- Pooled numbers mix families with different directions and strengths; the gate is evaluated per family (sections 8-11).

## 13. Event-strength monotonicity (first-in-cluster events, within-family terciles; 1 = weakest)

LIQUIDATION_CONTINUATION:

| tercile | n | strength range | 15m | 1h | 2h | 4h | 8h | 12h | MFE ATR | MAE ATR |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 213 | 3.02 .. 3.90 | 0.030% | 0.067% | 0.095% | 0.111% | 0.127% | 0.096% | 2.46 | -1.87 |
| 2 | 213 | 3.91 .. 4.86 | -0.078% | 0.047% | -0.089% | -0.048% | 0.043% | 0.207% | 2.59 | -1.98 |
| 3 | 213 | 4.86 .. 12.16 | -0.088% | -0.067% | -0.018% | 0.038% | 0.047% | -0.027% | 3.00 | -2.06 |

- ABSORPTION_REVERSAL: n=21 first-in-cluster events (fewer than 30: no terciles).

FLOW_OI_CONTINUATION:

| tercile | n | strength range | 15m | 1h | 2h | 4h | 8h | 12h | MFE ATR | MAE ATR |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 509 | 1.04 .. 1.47 | -0.022% | 0.018% | 0.040% | 0.097% | 0.137% | 0.235% | 2.46 | -1.91 |
| 2 | 509 | 1.47 .. 1.84 | 0.005% | 0.008% | 0.015% | 0.014% | 0.053% | 0.069% | 2.49 | -2.08 |
| 3 | 509 | 1.84 .. 5.73 | 0.018% | 0.066% | 0.088% | 0.071% | 0.113% | 0.195% | 2.61 | -2.01 |

FLOW_DIVERGENCE_REVERSAL:

| tercile | n | strength range | 15m | 1h | 2h | 4h | 8h | 12h | MFE ATR | MAE ATR |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 64 | 0.04 .. 0.30 | 0.034% | 0.007% | 0.006% | 0.032% | 0.044% | -0.053% | 2.11 | -2.44 |
| 2 | 63 | 0.30 .. 0.58 | -0.006% | 0.007% | 0.079% | 0.089% | 0.149% | 0.340% | 2.33 | -1.72 |
| 3 | 64 | 0.61 .. 3.53 | 0.077% | 0.137% | 0.090% | 0.061% | 0.121% | 0.194% | 2.17 | -2.14 |

## 14. Event frequency

| family | side | events | first-in-cluster |
|---|---|---|---|
| ABSORPTION_REVERSAL | LONG | 11 | 8 |
| ABSORPTION_REVERSAL | SHORT | 46 | 13 |
| FLOW_DIVERGENCE_REVERSAL | LONG | 154 | 113 |
| FLOW_DIVERGENCE_REVERSAL | SHORT | 94 | 78 |
| FLOW_OI_CONTINUATION | LONG | 3879 | 942 |
| FLOW_OI_CONTINUATION | SHORT | 1850 | 585 |
| LIQUIDATION_CONTINUATION | LONG | 1317 | 260 |
| LIQUIDATION_CONTINUATION | SHORT | 1630 | 379 |

| stage | count | per day |
|---|---|---|
| events (every 5m bar, both sides) | 8981 | 5.18 |
| first-in-cluster events | 2378 | 1.37 |
| episodes opened by the lifecycle (slot, cooldown and 24 h anchor de-duplication) | 1981 | 1.14 |
| qualified setups (15m confirmation inside the 2 h window) | 1847 | 1.07 |
| trades | 1210 | 0.698 |

## 15. Year stability (Stage A, first-in-cluster events; mean signed return and t at 1 h / 4 h)

Pooled by year:

| period | n | 1 h mean | t | 4 h mean | t |
|---|---|---|---|---|---|
| 2022 | 521 | 0.063% | 1.39 | -0.006% | -0.07 |
| 2023 | 442 | 0.037% | 0.93 | 0.102% | 1.55 |
| 2024 | 519 | -0.027% | -0.64 | -0.003% | -0.05 |
| 2025 | 506 | 0.055% | 1.82 | 0.123% | 2.24 |
| 2026 | 390 | 0.008% | 0.20 | 0.045% | 0.72 |

LIQUIDATION_CONTINUATION by year:

| period | n | 1 h mean | t | 4 h mean | t |
|---|---|---|---|---|---|
| 2022 | 163 | 0.102% | 1.15 | -0.068% | -0.46 |
| 2023 | 178 | 0.097% | 1.19 | 0.105% | 0.97 |
| 2024 | 130 | -0.106% | -0.93 | 0.012% | 0.09 |
| 2025 | 87 | -0.019% | -0.20 | 0.278% | 1.67 |
| 2026 | 81 | -0.106% | -1.06 | -0.146% | -0.85 |

LIQUIDATION_CONTINUATION by quarter:

| period | n | 1 h mean | t | 4 h mean | t |
|---|---|---|---|---|---|
| 2022Q1 | 45 | -0.039% | -0.18 | -0.103% | -0.36 |
| 2022Q2 | 47 | 0.203% | 1.16 | -0.175% | -0.57 |
| 2022Q3 | 37 | 0.199% | 1.21 | -0.016% | -0.06 |
| 2022Q4 | 34 | 0.042% | 0.39 | 0.072% | 0.25 |
| 2023Q1 | 54 | 0.188% | 1.12 | 0.451% | 1.70 |
| 2023Q2 | 55 | 0.077% | 0.59 | 0.001% | 0.01 |
| 2023Q3 | 33 | 0.176% | 0.97 | 0.063% | 0.34 |
| 2023Q4 | 36 | -0.080% | -0.45 | -0.215% | -0.99 |
| 2024Q1 | 34 | -0.019% | -0.08 | -0.068% | -0.23 |
| 2024Q2 | 30 | -0.046% | -0.17 | 0.083% | 0.33 |
| 2024Q3 | 39 | -0.103% | -0.44 | 0.029% | 0.12 |
| 2024Q4 | 27 | -0.285% | -1.98 | 0.009% | 0.03 |
| 2025Q1 | 19 | -0.095% | -0.31 | 0.732% | 1.29 |
| 2025Q2 | 28 | 0.016% | 0.13 | -0.241% | -1.11 |
| 2025Q3 | 20 | 0.019% | 0.13 | 0.050% | 0.26 |
| 2025Q4 | 20 | -0.032% | -0.18 | 0.802% | 2.85 |
| 2026Q1 | 30 | -0.262% | -1.18 | 0.107% | 0.26 |
| 2026Q2 | 31 | -0.021% | -0.18 | -0.137% | -0.97 |
| 2026Q3 | 20 | -0.004% | -0.03 | -0.540% | -2.16 |

ABSORPTION_REVERSAL by year:

| period | n | 1 h mean | t | 4 h mean | t |
|---|---|---|---|---|---|
| 2022 | 1 | 0.256% | n/a | 0.368% | n/a |
| 2024 | 1 | -2.273% | n/a | -5.057% | n/a |
| 2025 | 8 | 0.071% | 0.81 | -0.062% | -0.19 |
| 2026 | 11 | 0.080% | 0.61 | -0.034% | -0.09 |

ABSORPTION_REVERSAL by quarter:

| period | n | 1 h mean | t | 4 h mean | t |
|---|---|---|---|---|---|
| 2022Q3 | 1 | 0.256% | n/a | 0.368% | n/a |
| 2024Q4 | 1 | -2.273% | n/a | -5.057% | n/a |
| 2025Q2 | 2 | 0.083% | n/a | 0.472% | n/a |
| 2025Q3 | 3 | -0.096% | -1.02 | -0.066% | -0.27 |
| 2025Q4 | 3 | 0.230% | 2.71 | -0.415% | -0.47 |
| 2026Q1 | 2 | -0.365% | n/a | -2.160% | n/a |
| 2026Q2 | 7 | 0.190% | 1.18 | 0.495% | 2.80 |
| 2026Q3 | 2 | 0.142% | n/a | 0.241% | n/a |

FLOW_OI_CONTINUATION by year:

| period | n | 1 h mean | t | 4 h mean | t |
|---|---|---|---|---|---|
| 2022 | 335 | 0.051% | 1.00 | 0.054% | 0.53 |
| 2023 | 252 | -0.023% | -0.61 | 0.069% | 0.82 |
| 2024 | 346 | 0.003% | 0.06 | 0.004% | 0.05 |
| 2025 | 338 | 0.060% | 1.72 | 0.091% | 1.48 |
| 2026 | 256 | 0.057% | 1.26 | 0.098% | 1.40 |

FLOW_OI_CONTINUATION by quarter:

| period | n | 1 h mean | t | 4 h mean | t |
|---|---|---|---|---|---|
| 2022Q1 | 97 | 0.104% | 0.96 | 0.235% | 1.16 |
| 2022Q2 | 113 | 0.030% | 0.34 | 0.059% | 0.31 |
| 2022Q3 | 86 | 0.080% | 0.77 | -0.130% | -0.69 |
| 2022Q4 | 39 | -0.081% | -0.98 | -0.008% | -0.05 |
| 2023Q1 | 62 | 0.007% | 0.08 | 0.056% | 0.33 |
| 2023Q2 | 60 | -0.075% | -1.03 | 0.055% | 0.38 |
| 2023Q3 | 57 | 0.100% | 1.39 | 0.247% | 1.73 |
| 2023Q4 | 73 | -0.102% | -1.51 | -0.049% | -0.26 |
| 2024Q1 | 78 | -0.070% | -0.60 | -0.041% | -0.22 |
| 2024Q2 | 89 | -0.030% | -0.40 | 0.055% | 0.41 |
| 2024Q3 | 81 | -0.015% | -0.17 | -0.072% | -0.45 |
| 2024Q4 | 98 | 0.104% | 1.27 | 0.055% | 0.42 |
| 2025Q1 | 82 | 0.014% | 0.18 | -0.068% | -0.49 |
| 2025Q2 | 81 | 0.030% | 0.42 | 0.063% | 0.49 |
| 2025Q3 | 83 | 0.063% | 1.26 | 0.029% | 0.34 |
| 2025Q4 | 92 | 0.125% | 1.69 | 0.313% | 2.42 |
| 2026Q1 | 98 | 0.126% | 1.50 | 0.142% | 1.05 |
| 2026Q2 | 77 | -0.090% | -1.26 | 0.073% | 0.70 |
| 2026Q3 | 81 | 0.113% | 1.54 | 0.071% | 0.62 |

FLOW_DIVERGENCE_REVERSAL by year:

| period | n | 1 h mean | t | 4 h mean | t |
|---|---|---|---|---|---|
| 2022 | 22 | -0.054% | -0.16 | -0.471% | -0.87 |
| 2023 | 12 | 0.425% | 1.49 | 0.755% | 1.81 |
| 2024 | 42 | 0.030% | 0.39 | 0.018% | 0.14 |
| 2025 | 73 | 0.114% | 1.61 | 0.105% | 0.71 |
| 2026 | 42 | -0.092% | -0.69 | 0.105% | 0.61 |

FLOW_DIVERGENCE_REVERSAL by quarter:

| period | n | 1 h mean | t | 4 h mean | t |
|---|---|---|---|---|---|
| 2022Q1 | 4 | -0.484% | -0.58 | -0.155% | -0.16 |
| 2022Q2 | 7 | -0.247% | -0.33 | -0.208% | -0.14 |
| 2022Q3 | 4 | 0.442% | 0.37 | -2.336% | -1.83 |
| 2022Q4 | 7 | 0.103% | 0.81 | 0.150% | 0.96 |
| 2023Q1 | 5 | 0.979% | 1.58 | 1.538% | 1.68 |
| 2023Q2 | 2 | 0.028% | n/a | 0.346% | n/a |
| 2023Q3 | 4 | -0.066% | -0.48 | -0.006% | -0.03 |
| 2023Q4 | 1 | 0.415% | n/a | 0.708% | n/a |
| 2024Q1 | 13 | -0.109% | -0.66 | -0.301% | -1.35 |
| 2024Q2 | 9 | 0.215% | 1.59 | -0.036% | -0.15 |
| 2024Q3 | 9 | 0.070% | 0.44 | -0.065% | -0.19 |
| 2024Q4 | 11 | 0.011% | 0.07 | 0.507% | 2.14 |
| 2025Q1 | 17 | 0.206% | 1.08 | 0.467% | 1.06 |
| 2025Q2 | 22 | 0.038% | 0.30 | -0.041% | -0.22 |
| 2025Q3 | 13 | 0.161% | 1.33 | 0.039% | 0.19 |
| 2025Q4 | 21 | 0.090% | 0.75 | 0.005% | 0.02 |
| 2026Q1 | 12 | -0.319% | -0.75 | 0.127% | 0.30 |
| 2026Q2 | 8 | -0.069% | -0.44 | -0.017% | -0.08 |
| 2026Q3 | 22 | 0.023% | 0.25 | 0.138% | 0.59 |

## 16. Stage B execution (generic, identical for all families)

- Context 4H/1H from the feature frame (reported, not gated); confirmation = the first completed 15m bar after the event whose close is beyond the previous 15m close in the trade direction, inside a 2 h thesis window; entry zone [event close - 0.5 ATR, event close + 1.0 ATR] (LONG, mirrored); execution = a completed 5m close inside the zone within 6 bars of the confirmation, fill at the next 5m open plus slippage; one net position; family priority A > B > C > D only when two detectors fire on the same bar; same anchor not re-armed within 24 h.

| stage | count |
|---|---|
| events (Stage A) | 8981 |
| episodes opened | 1981 |
| confirmed (qualified setups) | 1847 |
| reached ENTRY_READY (confirmed and 5m close in zone) | 1847 |
| triggers blocked by an open position | 513 |
| risk-rejected | 0 |
| trades | 1210 |

Episode end reasons:

| end reason | n |
|---|---|
| BLOCKED_POSITION_OPEN | 513 |
| STOP | 472 |
| TRAIL | 372 |
| TIME_LIMIT | 365 |
| WATCH_TIMEOUT | 258 |
| END_OF_DATA | 1 |

## 17. Entry delay and movement missed

| metric | mean | p10 | p50 | p90 | max |
|---|---|---|---|---|---|
| confirmation delay after the event (min) | 17.9 | 5.0 | 10.0 | 40.0 | 110.0 |
| entry (fill) delay after the event (min) | 23.9 | 10.0 | 17.5 | 45.0 | 120.0 |
| movement missed, signed (ATR) | 0.1 | -0.3 | 0.1 | 0.6 | 1.0 |
| movement missed, signed (%) | 0.1 | -0.2 | 0.1 | 0.4 | 2.4 |

- Share of trades that entered more than 0.5 ATR beyond the event close: 16%; share that entered at a better price than the event close: 41%.

| family | n | median entry delay (min) | median confirmation delay (min) | median missed (ATR) | mean missed (ATR) |
|---|---|---|---|---|---|
| ABSORPTION_REVERSAL | 7 | 20 | 15 | 0.10 | 0.12 |
| FLOW_DIVERGENCE_REVERSAL | 90 | 25 | 20 | 0.21 | 0.24 |
| FLOW_OI_CONTINUATION | 796 | 15 | 10 | 0.07 | 0.11 |
| LIQUIDATION_CONTINUATION | 317 | 15 | 10 | 0.05 | 0.12 |

## 18. Stop geometry and cost-to-risk

| metric | value |
|---|---|
| median / mean stop (% of price) | 1.40 / 1.55 (V3: 0.66, V4: 1.54) |
| p10 / p90 stop % | 0.75 / 2.53 |
| median / p10 / p90 stop in ATR(1h) | 1.97 / 1.28 / 3.03 |
| share of stops set by the 1.25 ATR volatility floor / capped at 3 ATR | 19% / 19% |
| round-trip cost assumed (% of notional) | 0.170 |
| median / p90 cost as % of stop | 12.1% / 22.8% |
| share of trades with cost > 25% of stop | 7.6% |
| median / mean cost drag (R) | 0.072 / 0.083 |

| family | n | median stop % | median stop ATR | share vol floor |
|---|---|---|---|---|
| ABSORPTION_REVERSAL | 7 | 0.79 | 1.30 | 57% |
| FLOW_DIVERGENCE_REVERSAL | 90 | 0.80 | 1.28 | 94% |
| FLOW_OI_CONTINUATION | 796 | 1.31 | 1.83 | 17% |
| LIQUIDATION_CONTINUATION | 317 | 1.89 | 2.68 | 1% |

## 19. Gross expectancy (before fees, slippage and funding)

| population | n | gross R | net R | PF (net) | win | median R |
|---|---|---|---|---|---|---|
| all trades | 1210 | 0.115 | -0.015 | 0.97 | 53% | 0.193 |
| LIQUIDATION_CONTINUATION ALL | 317 | 0.083 | -0.008 | 0.98 | 52% | 0.133 |
| LIQUIDATION_CONTINUATION LONG | 127 | 0.105 | 0.010 | 1.02 | 51% | 0.120 |
| LIQUIDATION_CONTINUATION SHORT | 190 | 0.068 | -0.020 | 0.95 | 52% | 0.146 |
| ABSORPTION_REVERSAL ALL | 7 | 0.558 | 0.372 | 2.04 | 71% | 0.301 |
| ABSORPTION_REVERSAL LONG | 3 | 1.026 | 0.883 | inf | 100% | 0.498 |
| ABSORPTION_REVERSAL SHORT | 4 | 0.211 | -0.011 | 0.98 | 50% | -0.484 |
| FLOW_OI_CONTINUATION ALL | 796 | 0.124 | -0.010 | 0.98 | 53% | 0.203 |
| FLOW_OI_CONTINUATION LONG | 501 | 0.090 | -0.049 | 0.90 | 53% | 0.184 |
| FLOW_OI_CONTINUATION SHORT | 295 | 0.181 | 0.057 | 1.12 | 53% | 0.260 |
| FLOW_DIVERGENCE_REVERSAL ALL | 90 | 0.120 | -0.107 | 0.79 | 56% | 0.130 |
| FLOW_DIVERGENCE_REVERSAL LONG | 57 | 0.212 | -0.021 | 0.96 | 60% | 0.223 |
| FLOW_DIVERGENCE_REVERSAL SHORT | 33 | -0.039 | -0.255 | 0.49 | 48% | -0.138 |

- Benchmark for economic interest (pre-declared, not a tuning target): gross expectancy >= +0.15R.

## 20. Costs

| component | primary (frozen) | Bybit-style sensitivity (taker 5.5 bps, maker 2 bps on targets), NOT for classification |
|---|---|---|
| trades | 1210 | 1210 |
| gross P&L before slippage | 3542 | 3542 |
| slippage | -1433 | -1433 |
| fees | -2537 | -2551 |
| funding | -25 | -25 |
| net P&L | -453 | -467 |
| gross R / net R | 0.115 / -0.015 | 0.115 / -0.015 |
| cost drag per trade (R) | 0.130 | 0.130 |
| funding events | 2019 | 2019 |

## 21. Net expectancy (raw stream, 0.25% risk) and chronology

| period | n | trades/day | win | gross R | net R | PF | net P&L | max DD | t |
|---|---|---|---|---|---|---|---|---|---|
| combined | 1210 | 0.70 | 53% | 0.115 | -0.014 | 0.97 | -453 | -14.53% | -0.47 |

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| all trades | 1210 | 53% | -0.014 | 0.193 | -17.5 | 0.97 | 1.38 | -0.71 | 13.7 | n/a | -453 |

By year (2026 = YTD to the end of the window):

| period | n | trades/day | win | gross R | net R | PF | net P&L | max DD | t |
|---|---|---|---|---|---|---|---|---|---|
| 2022 | 271 | 0.74 | 52% | 0.091 | -0.005 | 0.99 | -34 | -4.62% | -0.08 |
| 2023 | 224 | 0.61 | 50% | 0.111 | -0.028 | 0.94 | -162 | -4.97% | -0.37 |
| 2024 | 271 | 0.74 | 53% | 0.101 | -0.017 | 0.96 | -121 | -3.57% | -0.26 |
| 2025 | 268 | 0.73 | 53% | 0.107 | -0.056 | 0.88 | -383 | -9.42% | -0.87 |
| 2026 | 176 | 0.64 | 59% | 0.193 | 0.055 | 1.13 | 245 | -3.16% | 0.69 |

By quarter:

| period | n | trades/day | win | gross R | net R | PF | net P&L | max DD | t |
|---|---|---|---|---|---|---|---|---|---|
| 2022-Q1 | 74 | 0.82 | 53% | 0.121 | 0.041 | 1.09 | 77 | -2.68% | 0.31 |
| 2022-Q2 | 82 | 0.90 | 55% | 0.136 | 0.049 | 1.11 | 101 | -2.25% | 0.42 |
| 2022-Q3 | 72 | 0.78 | 47% | -0.033 | -0.125 | 0.76 | -228 | -2.84% | -1.04 |
| 2022-Q4 | 43 | 0.47 | 51% | 0.157 | 0.015 | 1.04 | 16 | -0.76% | 0.10 |
| 2023-Q1 | 63 | 0.70 | 44% | -0.071 | -0.199 | 0.64 | -321 | -3.69% | -1.55 |
| 2023-Q2 | 55 | 0.60 | 45% | 0.092 | -0.025 | 0.95 | -34 | -1.77% | -0.16 |
| 2023-Q3 | 47 | 0.51 | 49% | 0.186 | 0.015 | 1.03 | 17 | -1.48% | 0.08 |
| 2023-Q4 | 59 | 0.64 | 61% | 0.264 | 0.116 | 1.27 | 176 | -1.42% | 0.77 |
| 2024-Q1 | 72 | 0.79 | 44% | -0.038 | -0.146 | 0.75 | -267 | -3.57% | -1.08 |
| 2024-Q2 | 63 | 0.69 | 59% | 0.223 | 0.095 | 1.26 | 148 | -2.27% | 0.75 |
| 2024-Q3 | 69 | 0.75 | 54% | 0.101 | -0.009 | 0.98 | -17 | -2.39% | -0.07 |
| 2024-Q4 | 67 | 0.73 | 57% | 0.135 | 0.009 | 1.02 | 15 | -1.41% | 0.07 |
| 2025-Q1 | 59 | 0.66 | 47% | 0.133 | 0.011 | 1.02 | 17 | -1.94% | 0.08 |
| 2025-Q2 | 71 | 0.78 | 46% | -0.052 | -0.208 | 0.61 | -378 | -4.22% | -1.83 |
| 2025-Q3 | 71 | 0.77 | 52% | 0.069 | -0.147 | 0.73 | -263 | -4.50% | -1.11 |
| 2025-Q4 | 67 | 0.73 | 64% | 0.291 | 0.142 | 1.38 | 241 | -1.77% | 1.15 |
| 2026-Q1 | 58 | 0.64 | 57% | 0.149 | 0.044 | 1.10 | 65 | -1.43% | 0.32 |
| 2026-Q2 | 54 | 0.59 | 69% | 0.437 | 0.300 | 1.88 | 415 | -0.99% | 1.96 |
| 2026-Q3 | 64 | 0.70 | 52% | 0.027 | -0.141 | 0.72 | -235 | -2.54% | -1.13 |

## 22. LONG vs SHORT

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L | gross R | cost drag R | median hold h |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| LONG | 688 | 53% | -0.032 | 0.191 | -22.1 | 0.93 | 1.37 | -0.73 | 13.3 | n/a | -570 | 0.107 | 0.139 | 12.2 |
| SHORT | 522 | 52% | 0.009 | 0.195 | 4.6 | 1.02 | 1.39 | -0.69 | 14.1 | n/a | 117 | 0.126 | 0.117 | 14.5 |

## 23. Event-family trade performance

| family | side | events | first-in-cluster | episodes | qualified | trades | trades/month | win | gross R | net R | PF | MFE R | MAE R | median hold h | median stop % | median delay min | median missed ATR | cost drag R | DD-window P&L | P&L share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| LIQUIDATION CONTINUATION | ALL | 2947 | 639 | 582 | 527 | 317 | 5.56 | 52% | 0.083 | -0.008 | 0.98 | 1.17 | -0.67 | 19.7 | 1.89 | 15 | 0.05 | 0.091 | -454 | 14% |
| LIQUIDATION CONTINUATION | LONG | 1317 | 260 | 237 | 218 | 127 | 2.23 | 51% | 0.105 | 0.010 | 1.02 | 1.30 | -0.72 | 19.8 | 1.95 | 20 | 0.06 | 0.095 | -185 | -7% |
| LIQUIDATION CONTINUATION | SHORT | 1630 | 379 | 345 | 309 | 190 | 3.34 | 52% | 0.068 | -0.020 | 0.95 | 1.08 | -0.64 | 19.1 | 1.85 | 15 | 0.05 | 0.087 | -269 | 21% |
| ABSORPTION REVERSAL | ALL | 57 | 21 | 22 | 21 | 7 | 0.12 | 71% | 0.558 | 0.372 | 2.04 | 1.89 | -0.56 | 11.6 | 0.79 | 20 | 0.10 | 0.189 | -64 | -15% |
| ABSORPTION REVERSAL | LONG | 11 | 8 | 8 | 7 | 3 | 0.05 | 100% | 1.026 | 0.883 | inf | 2.30 | -0.29 | 11.6 | 1.00 | 30 | -0.13 | 0.141 | 0 | -15% |
| ABSORPTION REVERSAL | SHORT | 46 | 13 | 14 | 14 | 4 | 0.07 | 50% | 0.211 | -0.011 | 0.98 | 1.59 | -0.77 | 11.2 | 0.69 | 18 | 0.17 | 0.224 | -64 | 0% |
| FLOW OI CONTINUATION | ALL | 5729 | 1527 | 1194 | 1134 | 796 | 13.97 | 53% | 0.124 | -0.010 | 0.98 | 1.44 | -0.73 | 12.0 | 1.31 | 15 | 0.07 | 0.134 | -750 | 46% |
| FLOW OI CONTINUATION | LONG | 3879 | 942 | 720 | 687 | 501 | 8.79 | 53% | 0.090 | -0.049 | 0.90 | 1.35 | -0.74 | 11.7 | 1.31 | 15 | 0.08 | 0.140 | -702 | 140% |
| FLOW OI CONTINUATION | SHORT | 1850 | 585 | 474 | 447 | 295 | 5.18 | 53% | 0.181 | 0.057 | 1.12 | 1.61 | -0.71 | 12.6 | 1.31 | 15 | 0.06 | 0.124 | -48 | -95% |
| FLOW DIVERGENCE REVERSAL | ALL | 248 | 191 | 183 | 165 | 90 | 1.58 | 56% | 0.120 | -0.107 | 0.79 | 1.47 | -0.74 | 7.2 | 0.80 | 25 | 0.21 | 0.228 | -240 | 55% |
| FLOW DIVERGENCE REVERSAL | LONG | 154 | 113 | 108 | 94 | 57 | 1.00 | 60% | 0.212 | -0.021 | 0.96 | 1.63 | -0.71 | 7.6 | 0.80 | 20 | 0.19 | 0.234 | -142 | 7% |
| FLOW DIVERGENCE REVERSAL | SHORT | 94 | 78 | 75 | 71 | 33 | 0.58 | 48% | -0.039 | -0.255 | 0.49 | 1.19 | -0.80 | 6.8 | 0.81 | 30 | 0.25 | 0.217 | -98 | 48% |

- Every family and side is shown (zero rows included); none is hidden. Trade-level strength terciles (within family):

- ABSORPTION_REVERSAL: n=7 (too few trades for terciles).

FLOW_DIVERGENCE_REVERSAL (n=90):

| tercile | n | gross R | net R | win |
|---|---|---|---|---|
| 1 | 30 | 0.106 | -0.067 | 60% |
| 2 | 30 | -0.182 | -0.332 | 43% |
| 3 | 30 | 0.189 | 0.078 | 63% |

FLOW_OI_CONTINUATION (n=796):

| tercile | n | gross R | net R | win |
|---|---|---|---|---|
| 1 | 266 | 0.128 | 0.036 | 55% |
| 2 | 265 | 0.072 | -0.018 | 52% |
| 3 | 265 | 0.028 | -0.049 | 52% |

LIQUIDATION_CONTINUATION (n=317):

| tercile | n | gross R | net R | win |
|---|---|---|---|---|
| 1 | 106 | 0.093 | 0.034 | 56% |
| 2 | 105 | -0.010 | -0.064 | 48% |
| 3 | 106 | 0.063 | 0.006 | 52% |

Regime at trigger (4H trend x 24h-volatility state; reported, not gated):

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| DOWN_COMPRESSION | 43 | 58% | 0.124 | 0.248 | 5.3 | 1.26 | 2.34 | -0.84 | 11.1 | n/a | 137 |
| DOWN_EXPANSION | 114 | 55% | 0.079 | 0.319 | 9.0 | 1.19 | 1.27 | -0.66 | 14.3 | n/a | 227 |
| DOWN_NORMAL | 334 | 50% | -0.041 | 0.095 | -13.7 | 0.92 | 1.39 | -0.71 | 13.5 | n/a | -351 |
| NEUTRAL_COMPRESSION | 12 | 58% | 0.127 | 0.263 | 1.5 | 1.25 | 1.86 | -0.74 | 12.7 | n/a | 39 |
| NEUTRAL_EXPANSION | 20 | 35% | -0.337 | -0.933 | -6.7 | 0.45 | 0.73 | -0.79 | 14.7 | n/a | -171 |
| NEUTRAL_NORMAL | 158 | 53% | -0.038 | 0.221 | -6.0 | 0.92 | 1.24 | -0.72 | 13.8 | n/a | -154 |
| UP_COMPRESSION | 49 | 61% | 0.093 | 0.180 | 4.6 | 1.20 | 1.79 | -0.68 | 11.9 | n/a | 115 |
| UP_EXPANSION | 79 | 54% | 0.121 | 0.262 | 9.6 | 1.32 | 1.58 | -0.62 | 15.0 | n/a | 243 |
| UP_NORMAL | 401 | 53% | -0.052 | 0.191 | -21.0 | 0.89 | 1.27 | -0.74 | 13.7 | n/a | -539 |

Derivatives/flow snapshot at entry (means for winners vs losers; univariate Spearman vs net R):

| feature | n | mean winners | mean losers | Spearman vs net R |
|---|---|---|---|---|
| funding_rate_last | 1210 | 0.0001 | 0.0001 | -0.071 |
| funding_rate_mean_3 | 1210 | 0.0001 | 0.0001 | -0.081 |
| oi_change_1h_pct | 1209 | 0.2587 | 0.2907 | -0.033 |
| oi_change_4h_pct | 1209 | 0.3678 | 0.4173 | -0.044 |
| oi_change_24h_pct | 1208 | 0.4133 | 0.2960 | 0.023 |
| long_short_ratio_accounts | 1197 | 1.4916 | 1.5007 | 0.033 |
| top_trader_ls_positions | 971 | 1.4754 | 1.4741 | -0.044 |
| taker_long_short_vol_ratio | 1098 | 1.0854 | 1.0644 | 0.018 |
| taker_buy_ratio_1h | 1210 | 0.5065 | 0.5054 | -0.022 |
| taker_buy_ratio_4h | 1210 | 0.5018 | 0.5018 | -0.042 |
| premium_index | 1210 | -0.0003 | -0.0003 | -0.025 |
| premium_mean_1h | 1210 | -0.0003 | -0.0003 | -0.026 |
| last_minus_mark_pct | 1210 | -0.0036 | -0.0028 | -0.010 |
| volume_accel_5m | 1210 | 1.5888 | 1.5014 | 0.027 |
| volume_accel_1h | 1210 | 2.2045 | 2.2484 | 0.033 |

## 24. Holding periods

| quantile | hours |
|---|---|
| p10 | 1.8 |
| p25 | 4.8 |
| p50 | 13.2 |
| p75 | 24.0 |
| p90 | 24.0 |

| bucket | share | n |
|---|---|---|
| 0-1h | 4% | 52 |
| 1-2h | 6% | 75 |
| 2-6h | 19% | 231 |
| 6-12h | 17% | 211 |
| 12-24h | 23% | 276 |
| 24-48h | 30% | 365 |
| >48h | 0% | 0 |

## 25. Frequency (events/day, qualified setups/day, trades/day)

| metric | value |
|---|---|
| events / day | 5.18 |
| first-in-cluster events / day | 1.37 |
| qualified setups / day | 1.07 |
| trades / day | 0.698 |
| trades / week | 4.88 |
| trades / month | 21.2 |
| median hours between entries | 27.7 |
| days with 0 / 1 / 2 / 3+ trades | 44% / 44% / 10% / 1% (max 4/day) |

| year | n | trades/day | trades/month | days 0 / 1 / 2 / 3+ |
|---|---|---|---|---|
| 2022 | 271 | 0.742 | 22.6 | 41% / 45% / 12% / 2% (max 4/day) |
| 2023 | 224 | 0.614 | 18.7 | 50% / 40% / 10% / 1% (max 3/day) |
| 2024 | 271 | 0.740 | 22.5 | 41% / 46% / 11% / 2% (max 3/day) |
| 2025 | 268 | 0.734 | 22.3 | 41% / 47% / 11% / 1% (max 3/day) |
| 2026 | 176 | 0.645 | 19.6 | 47% / 44% / 7% / 2% (max 3/day) |

## 26. MFE / MAE

| metric | value |
|---|---|
| mean_MFE_R | 1.375 |
| median_MFE_R | 0.953 |
| mean_MAE_R | -0.714 |
| median_MAE_R | -0.719 |
| worst_MAE_R | -3.758 |
| mfe_capture | -0.011 |
| tp1_hit_rate | 48.9% |
| tp2_hit_rate | 20.2% |
| hit_rate_1R | 48.9% |
| hit_rate_1.5R | 29.8% |
| hit_rate_2R | 20.2% |
| hit_rate_3R | 10.1% |

## 27. Exits

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| END_OF_DATA | 1 | 0% | -0.746 | -0.746 | -0.7 | 0.00 | 0.18 | -0.74 | 8.2 | n/a | -19 |
| STOP | 472 | 0% | -1.127 | -1.112 | -532.0 | 0.00 | 0.34 | -1.16 | 6.8 | n/a | -13536 |
| TIME_LIMIT | 365 | 74% | 0.846 | 0.901 | 308.8 | 9.53 | 2.22 | -0.48 | 24.0 | n/a | 7850 |
| TRAIL | 372 | 100% | 0.555 | 0.337 | 206.4 | 1399.90 | 1.86 | -0.38 | 12.2 | n/a | 5251 |

- TP1 +1R (40%, stop to breakeven), TP2 +2R (30%), remainder trails the 1H swing - 0.5 ATR(1h) after TP1 (applied from the next bar), 24 h cap, stop-first; counterfactual 1R / 1.5R / 2R / 3R-before-stop recorded per trade (`cf_hit_*` columns of the trade frame; 1.5R share above). Not optimised.

## 28. Daily safety overlay (reported separately)

| metric | raw | overlay (3 full-risk losses or -0.75% realised in a UTC day -> no new entries) |
|---|---|---|
| trades | 1210 | 1209 |
| entries blocked by the overlay | 0 | 1 |
| net P&L | -453 | -462 |
| max DD (trade curve) | -14.53% | -14.62% |

## 29. Account simulation

| stream | final equity | return | CAGR | max DD (trades) | max DD (daily) | Sharpe | Sortino | longest losing streak | trades/week |
|---|---|---|---|---|---|---|---|---|---|
| raw, 0.25% risk (default) | 9546.77 | -4.53% | -0.97% | -14.53% | -14.56% | -0.21 | -0.28 | 7 | 4.88 |
| safety overlay, 0.25% risk | 9537.52 | -4.62% | -0.99% | -14.62% | -14.65% | -0.21 | -0.29 | 7 | 4.88 |
| raw, 0.5% risk (reporting only) | 9093.55 | -9.06% | -1.98% | -28.02% | -28.08% | -0.19 | -0.25 | 7 | 4.88 |

- Sequential single-slot account; fixed research equity for sizing; returns beyond -100% denote ruin of the research account.

| drawdown metric | value |
|---|---|
| max drawdown (trade curve / daily mtm) | -14.53% / -14.56% |
| max drawdown (USDT) | -1508 |
| drawdown window trades | 834 |
| family contribution inside the window | FLOW_OI_CONTINUATION -750 (n=542); LIQUIDATION_CONTINUATION -454 (n=235); FLOW_DIVERGENCE_REVERSAL -240 (n=55); ABSORPTION_REVERSAL -64 (n=2) |
| longest losing / winning streak | 7 / 10 |

## 30. Leverage / liquidation

| metric | value |
|---|---|
| leverage used (trades per level) | 1x: 885, 2x: 294, 3x: 26, 5x: 5 |
| mean / max leverage | 1.30 / 5 |
| mean notional (USDT) | 2096 |
| mean stop distance % | 1.55 |
| min / median stop-to-liquidation ratio | 17.5 / 62.4 |
| min liquidation distance % | 19.50 |
| liquidations | 0 |

| cap | trades | risk rejected | mean leverage | mean R | return | max DD | min liq distance % | liquidations |
|---|---|---|---|---|---|---|---|---|
| 1x | 1210 | 0 | 1.00 | -0.014 | -1.90% | -11.27% | 99.50 | 0 |
| 2x | 1210 | 0 | 1.27 | -0.014 | -4.50% | -14.14% | 49.50 | 0 |
| 3x | 1210 | 0 | 1.29 | -0.014 | -4.72% | -14.46% | 32.83 | 0 |
| 5x | 1210 | 0 | 1.30 | -0.014 | -4.53% | -14.53% | 19.50 | 0 |
| 10x | 1210 | 0 | 1.30 | -0.014 | -4.53% | -14.53% | 19.50 | 0 |

## 31. Null benchmarks (time-matched and regime-matched random entries with the same side, stop %, ATR, sizing, exits and costs; K = 10 per trade)

| series | n | mean net R | mean gross R | win | strategy - null (R) | z | P(null >= strategy) | signed BTC drift |
|---|---|---|---|---|---|---|---|---|
| V5 strategy | 1210 | -0.014 | 0.068 | 52.9% |  |  |  | 0.0009 |
| null (time-matched) | 12100 | -0.134 | -0.051 | 46.5% | 0.119 | 3.97 | 0% | 0.0001 |
| null (regime-matched) | 12100 | -0.149 | -0.066 | 46.3% | 0.135 | 5.97 | 0% | -0.0000 |

- "mean gross R" in this table is the ledger R before fees and funding (slippage inside the fills); section 19 reports expectancy before fees, slippage and funding. The Stage A nulls per family (random bars, no execution) are in sections 8-11.

## 32. Outlier robustness

| metric | value |
|---|---|
| mean net R / median net R | -0.014 / 0.193 |
| without the best 1 / 3 / 5 trades | -0.018 / -0.023 / -0.028 |
| without the worst 1 / 3 / 5 trades | -0.013 / -0.011 / -0.009 |
| without the best 5 and worst 5 | -0.023 |
| gross R without the best 1 / 3 / 5 | 0.065 / 0.059 / 0.054 |
| best / worst trade (R) | 4.11 / -1.52 |
| best 5 trades' share of gross profit | 3% |
| best quarter / share of net P&L | 2026-Q2 / n/a |
| best family / share of net P&L | ABSORPTION_REVERSAL / n/a |

## 33. Bybit collector verification (public data, no authentication, no orders)

| check | result |
|---|---|
| uptime test | 900 s bounded run (with a forced mid-run reconnect) |
| messages received | 40490 (45.0/s): orderbook.50.BTCUSDT 29955, publicTrade.BTCUSDT 4605, subscribe 2, tickers.BTCUSDT 5928 |
| order-book deltas / sequence gaps / gap rate | 29953 / 0 / 0.000% |
| duplicates suppressed | 0 |
| reconnect test | 1 reconnect(s), 0 stale timeout(s), 1 transport error(s); subscription re-established and storage continued |
| raw storage verification | 1 hourly file(s), 40488 rows re-read, 0 payload hash mismatches, 24.3 MB; per channel orderbook.50.BTCUSDT 29955, publicTrade.BTCUSDT 4605, tickers.BTCUSDT 5928 |
| timestamp latency (received - exchange ts, ms) | p50 41, p90 51, p99 80, max 892 over 40488 messages |
| resumed from a previous state file | no |
| liquidation messages | 0 (the stream carries only actual liquidations; a short run can legitimately see none) |

- Additional run (verification run 2: restart/resume from state, 120 s): 130 s, 4827 messages, 0 gaps, 0 reconnects, resumed from state yes, storage rows 45314 with 0 mismatches.

- Data persisted under `data/btc/forward/bybit/BTCUSDT/<date>/<hour>.jsonl` (git-ignored); a restart continues from the state file; nothing is modified after it is written.

## 34. Bybit Demo architecture (designed, NOT activated)

- `btc_swing/v5/execution.py`: `ExecutionAdapter` protocol (place_order, cancel_order, position_state, set_stop, set_take_profit, account_balance, fills, funding) with typed request/response records (`OrderRequest`, `OrderAck`, `PositionState`, `Fill`, `FundingEvent`, `Balance`); `DryRunAdapter` records intents in memory and never fills; `BybitDemoAdapter` holds the endpoint map (`api-demo.bybit.com`: /v5/order/create, /v5/order/cancel, /v5/position/list, /v5/position/trading-stop, /v5/account/wallet-balance, /v5/execution/list, /v5/account/transaction-log) and raises `NotActivatedError` on every method; constructing it with `activated=True` also raises. No credentials are read, required or stored anywhere in V5; no order was placed.
- Activation would be a separate owner-approved change outside any research phase: authenticated transport (API key, timestamp, recv_window, HMAC signature), idempotent client order ids, position/fill reconciliation against the public collector, and a kill switch; none of it exists in this repository.

## 35. Limitations

- Development data (2022-2026 inspected by V1-V4); pre-registration limits researcher degrees of freedom but cannot make results out-of-sample. 2025-01..2026-09 was V1's spent holdout and is development data here.
- Liquidation history does not exist in the archive; family A is an OI-flush proxy and is labelled as such. Order-book history starts 2023-01-01 and is diagnostic only. aggTrades aggregates are 5-minute sums; intra-bar sequencing (sweeps, icebergs) is not observable.
- Metrics (OI) are 5-minute archive rows with observation time as published; funding every 8 h; one instrument (Binance USDT-M perp); the forward venue is Bybit, whose flow, book and liquidation microstructure differ.
- Fill model: next 5m open plus fixed slippage; taker fees on every fill in the primary model; no latency, queue or partial-fill model; liquidation on mark price.
- Event clusters are auto-correlated; first-in-cluster tables, bootstrap intervals and the per-family nulls should be read together. Stage A returns are gross of costs.
- The overlay, the 0.5% and the Bybit-style streams are reporting views; the classification uses the raw 0.25% stream with the frozen primary costs.

## 36. Recommendation and pre-declared criteria

| # | criterion | met | evidence |
|---|---|---|---|
| 1 | Stage A gate passed by >= 1 family (first-in-cluster n >= 100; mean > 0 with t >= 2.0 at 1 h or 4 h; positive in >= 3 years; top tercile >= bottom) | no | 0 of 4 families passed: none |
| 2 | pooled gross expectancy >= +0.15R | no | +0.115R |
| 3 | pooled net expectancy >= +0.08R | no | -0.014R (n=1210) |
| 4 | profit factor >= 1.20 | no | 0.97 |
| 5 | net > 0 in >= 3 of 5 years and >= 60% of quarters with >= 10 trades | no | 1/5 years; 58% of 19 quarters |
| 6 | cost drag <= 50% of gross expectancy | no | gross +0.115R, net -0.014R |
| 7 | max drawdown <= 12% | no | 14.53% |
| 8 | 0.5-3.0 trades/day and median hold 1-8 h | no | 0.70 trades/day; median hold 13.2 h |
| 9 | net without the 5 best trades >= +0.04R and no family > 60% of net P&L | no | without best 5 -0.028R |
| 10 | beats the time-matched and regime-matched nulls (net R) | yes | strategy -0.014R vs null time -0.134R / regime -0.149R |

**C — NO ROBUST MICROSTRUCTURE EDGE**

- Criteria 1, 2, 3, 4, 5, 6, 7, 8, 9 fail and the B conditions do not hold (gate passed by: none): the frozen microstructure events do not produce a robust, cost-surviving edge. No threshold, stop, window, exit or family is changed and no re-run is proposed. Families: LIQUIDATION_CONTINUATION gross 0.083R / net -0.008R (n=317); ABSORPTION_REVERSAL gross 0.558R / net 0.372R (n=7); FLOW_OI_CONTINUATION gross 0.124R / net -0.010R (n=796); FLOW_DIVERGENCE_REVERSAL gross 0.120R / net -0.107R (n=90).

## Appendix — frozen V5 configuration

```yaml
collector:
  dedupe_cache_size: 50000
  ping_seconds: 20.0
  reconnect_backoff_seconds:
  - 1.0
  - 2.0
  - 5.0
  - 10.0
  - 30.0
  stale_seconds: 30.0
  symbol: BTCUSDT
  topics:
  - publicTrade.BTCUSDT
  - orderbook.50.BTCUSDT
  - tickers.BTCUSDT
  - allLiquidation.BTCUSDT
  url: wss://stream.bybit.com/v5/public/linear
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
  - LIQUIDATION_CONTINUATION
  - ABSORPTION_REVERSAL
  - FLOW_OI_CONTINUATION
  - FLOW_DIVERGENCE_REVERSAL
  max_concurrent_positions: 1
  watch_timeout_bars: 24
events:
  absorption_reversal:
    imbalance_1h_z: -2.0
    max_adverse_ret_1h_z: -0.5
    vol_1h_z: 1.0
  cluster_bars: 12
  flow_divergence_reversal:
    max_cvd_slope_z: 0.0
    max_oi_chg_z: 0.0
  flow_oi_continuation:
    cvd_slope_1h_z: 1.0
    imbalance_1h_z: 1.0
    max_crowding_z: 2.0
    oi_chg_1h_z: 1.0
    ret_1h_z: 1.0
  liquidation_continuation:
    imbalance_1h_z: 1.0
    impulse_ret_1h_z: 2.0
    oi_chg_1h_z: -1.5
    vol_1h_z: 1.5
execution:
  confirm_window_bars: 24
  max_stop_atr: 3.0
  struct_buffer_atr: 0.25
  struct_lookback_bars: 12
  vol_floor_atr: 1.25
  zone_above_atr: 1.0
  zone_below_atr: 0.5
exits:
  breakeven_after_tp1: true
  evaluate_r_levels:
  - 1.0
  - 1.5
  - 2.0
  - 3.0
  max_hold_hours: 24
  tp1_frac: 0.4
  tp1_r: 1.0
  tp2_frac: 0.3
  tp2_r: 2.0
  trail_atr_buffer: 0.5
  trail_tf: 1h
features:
  big_trade_qty_btc: 1.0
  break_window_bars: 288
  funding_z_window: 90
  range_window_bars: 576
  z_min_periods: 2880
  z_window_bars: 8640
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
    - 1.0
    - 8.0
    min_expectancy_without_best5_r: 0.04
    min_gross_expectancy_r: 0.15
    min_net_expectancy_r: 0.08
    min_positive_quarter_share: 0.6
    min_positive_years: 3
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
stage_a:
  bootstrap_resamples: 1000
  gate:
    horizons_hours:
    - 1.0
    - 4.0
    min_events: 100
    min_positive_years: 3
    min_t: 2.0
  horizons_hours:
  - 0.25
  - 0.5
  - 1.0
  - 2.0
  - 4.0
  - 8.0
  - 12.0
  mfe_mae_hours: 12
strategy_name: btc_swing_v5_microstructure
```
