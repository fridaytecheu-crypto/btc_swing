# SUMMARY — intraday portfolio research `20261019T114233Z`

| engine | verdict | reason |
|---|---|---|
| E1 | **REJECT** | 13080 parents, 166 completed trades over 43.5 months; failed gates: mean_gross_ge_25bps, mean_net_ge_9bps_at_16, net_r_gt_0, pf_ge_1_20, day_cluster_lower_bound_gt_0, stress_24_mean_net_gt_0, stress_24_pf_gt_1, blocks_positive_ge_3_of_4, no_block_gt_50pct_of_positive_pnl (mean net <= 0 and the day-cluster lower bound <= 0 at 16 bps) |
| E2 | **REJECT** | 4734 parents, 134 completed trades over 43.5 months; failed gates: mean_gross_ge_25bps, mean_net_ge_9bps_at_16, net_r_gt_0, pf_ge_1_20, day_cluster_lower_bound_gt_0, stress_24_mean_net_gt_0, stress_24_pf_gt_1, blocks_positive_ge_3_of_4, no_block_gt_50pct_of_positive_pnl (mean net <= 0 and the day-cluster lower bound <= 0 at 16 bps) |
| E4 | **INCONCLUSIVE** | E4 lagged 99th-percentile thresholds need 30 complete UTC days of receipt-timed trades + allLiquidation; local raw stores hold 0 complete days (6 days touched, 551 trade-minutes). Status THRESHOLD_UNAVAILABLE_INSUFFICIENT_HISTORY; 0 qualifying shocks. INSUFFICIENT EVIDENCE — requirement not lowered. |

## Data coverage
- Bybit 5m/1m trade-built bars: 2021-11-15..2025-06-30 (1324 days); holdout from 2025-07-01 never read.
- Historical BBO / receipt time / liquidations: MISSING -> E1/E2 EXECUTABLE replay UNAVAILABLE (BENCHMARK ledger only; feasibility / cost-envelope analysis).
- Raw Bybit receipt-timed stores: 6 UTC days touched, 0 complete (E4 threshold needs 30), 551 trade-minutes, 552 BBO-minutes.

## E1 / E2 benchmark economics (taker/taker, 16 bps base; 24 bps stress)

| engine | parents | eligible | trades | trades/day | gross bps | net bps @16 | LB95 day-cluster | net R | net USDT | PF @16 | win | net bps @24 | PF @24 | MFE | MAE | hold min | maxDD USDT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| E1 | 13080 | 166 | 166 | 0.125 | -6.90 | -22.91 | -28.38 | -120.16 | -600.82 | 0.34 | 0.223 | -30.91 | 0.24 | 32.26 | -27.23 | 23.2 | 604.08 |
| E2 | 4734 | 134 | 134 | 0.101 | 8.10 | -7.93 | -14.75 | -47.06 | -235.31 | 0.67 | 0.313 | -15.93 | 0.47 | 37.25 | -20.50 | 22.1 | 236.88 |

## Frequency

- E1: candidates/day 9.879, eligible/day 0.125, trades/day 0.125, zero-trade days 1175 of 1324, daily p10/p50/p90 [0, 0, 1], rejection rate 0.987; rejections {"CANCEL_CLOSE_INSIDE_BEFORE_RETEST": 4002, "CANCEL_NO_RETEST_15M": 6118, "REGIME_NOT_DIRECTIONAL": 10838, "REGIME_UNAVAILABLE": 3, "STOP_TOO_TIGHT_LT_20BPS": 2139, "STOP_TOO_WIDE_GT_150BPS": 2}
- E2: candidates/day 3.576, eligible/day 0.101, trades/day 0.101, zero-trade days 1200 of 1324, daily p10/p50/p90 [0, 0, 0], rejection rate 0.972; rejections {"CONFIRMATION_FAILED": 1523, "REGIME_NOT_RANGE": 509, "STOP_TOO_TIGHT_LT_20BPS": 930, "STOP_TOO_WIDE_GT_150BPS": 44, "TARGET_ALREADY_REACHED_NO_RETROACTIVE_FILL": 388, "VWAP_DISTANCE_LT_1_5_STOP": 279, "VWAP_DISTANCE_LT_40BPS": 2343}

## Portfolio (one position, engine order E1 > E2, later signals telemetry only)

| portfolio | executed | occupancy loss | abstain opposite | net bps | net R | PF |
|---|---|---|---|---|---|---|
| E1 | 166 | 0 | 0 | -22.91 | -120.16 | 0.30 |
| E2 | 134 | 0 | 0 | -7.93 | -47.06 | 0.54 |
| E1+E2 | 300 | 0 | 0 | -16.22 | -167.23 | 0.39 |

## Gates (per engine)

- E1: failed ['mean_gross_ge_25bps', 'mean_net_ge_9bps_at_16', 'net_r_gt_0', 'pf_ge_1_20', 'day_cluster_lower_bound_gt_0', 'stress_24_mean_net_gt_0', 'stress_24_pf_gt_1', 'blocks_positive_ge_3_of_4', 'no_block_gt_50pct_of_positive_pnl']; positive blocks 0/4; max block share n/a; mean net without top-5 winners -27.22 bps
- E2: failed ['mean_gross_ge_25bps', 'mean_net_ge_9bps_at_16', 'net_r_gt_0', 'pf_ge_1_20', 'day_cluster_lower_bound_gt_0', 'stress_24_mean_net_gt_0', 'stress_24_pf_gt_1', 'blocks_positive_ge_3_of_4', 'no_block_gt_50pct_of_positive_pnl']; positive blocks 1/4; max block share n/a; mean net without top-5 winners -12.98 bps

## E4 event study

- status THRESHOLD_UNAVAILABLE_INSUFFICIENT_HISTORY; messages: trades 660600, liquidations 417, BBO updates 1201541; grid seconds 730337; threshold hours available {'liquidation': 0, 'aggressive_flow': 0}; shocks 0; flow-only parents 0; arms {}

## Missing datasets (exact)

- historical_bbo_bid_ask: PARTIAL — candles cannot reconstruct the BBO; a missing BBO is never a perfect-mid fill
- receipt_time_local: PARTIAL — only the live raw stores (rx_ms); the historical archive has exchange time only
- liquidations: PARTIAL — candles cannot reconstruct liquidations; Binance liquidationSnapshot not on disk
- e4_lagged_threshold_history_30_complete_days: MISSING — raw stores receipt-time coverage
- funding: PARTIAL — Binance Vision fundingRate — PROXY for Bybit funding (venue mismatch, labelled)

## Interpretations applied (not parameters)

- episode_release_after_rejection: E1 cooldown (position closed, >= 60 min, two 5m closes back inside the original box) and the E2 new-excursion rule (VWAP touch, two closes inside, >= 30 min) govern parents that became a position. A setup rejected or cancelled before entry ends its episode at that time: further signals on the SAME frozen hour box are repeats of that parent, a later box can open a new candidate. Without this a single rejected breakout would block its side until price returned inside that box, possibly never.
- benchmark_entry: open of the first Bybit 1m bar after the decision close (no BBO in the archive)
- exit_priority_same_bar: stop before target before 5m-close invalidation before time (conservative)
- er_regime_at: the ER of the 5m bar completing the pattern (E1: 2nd breakout close; E2: sweep bar)

No engine is ELIGIBLE FOR RUNNER ENGINEERING unless its verdict above says so.
