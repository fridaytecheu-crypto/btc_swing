# SUMMARY — N1 / N2 / N4 preregistered screens `20261019T180134Z`

Preregistration sha256 `276024f92373e3ca…` written before any outcome; frozen rules in `experiment_manifest_n1_n2_n4.json`.

| engine | EDGE | CADENCE | edge reason | cadence reason |
|---|---|---|---|---|
| N1 | **REJECT** | **PLAUSIBLE CORE CADENCE** | 7310 parents, 7310 completed trades over 43.5 months; failed gates: mean_gross_ge_25bps, mean_net_ge_9bps_at_16, net_r_gt_0, pf_ge_1_20, day_cluster_lower_bound_gt_0, stress_24_mean_net_gt_0, stress_24_pf_gt_1, blocks_positive_ge_3_of_4, no_block_gt_50pct_of_positive_pnl (mean net <= 0 and the day-cluster lower bound <= 0 at 16 bps); kill criteria: gross_below_base_cost, chronological_unstable, movement_mostly_completed_impulse | 5.521 independent eligible/day >= 0.75; incremental available 3.029/day in the union |
| N2S | **REJECT** | **PLAUSIBLE CORE CADENCE** | 6140 parents, 6140 completed trades over 43.5 months; failed gates: mean_gross_ge_25bps, mean_net_ge_9bps_at_16, net_r_gt_0, pf_ge_1_20, day_cluster_lower_bound_gt_0, stress_24_mean_net_gt_0, stress_24_pf_gt_1, blocks_positive_ge_3_of_4, no_block_gt_50pct_of_positive_pnl (mean net <= 0 and the day-cluster lower bound <= 0 at 16 bps); kill criteria: gross_below_base_cost, chronological_unstable, remaining_effect_1_to_5_bps | 4.637 independent eligible/day >= 0.75; incremental available 2.394/day in the union |
| N4 | **REJECT** | **PLAUSIBLE CORE CADENCE** | 1387 parents, 1387 completed trades over 43.5 months; failed gates: mean_gross_ge_25bps, mean_net_ge_9bps_at_16, net_r_gt_0, pf_ge_1_20, day_cluster_lower_bound_gt_0, stress_24_mean_net_gt_0, stress_24_pf_gt_1, blocks_positive_ge_3_of_4, no_block_gt_50pct_of_positive_pnl (mean net <= 0 and the day-cluster lower bound <= 0 at 16 bps); kill criteria: gross_below_base_cost, chronological_unstable, pooled_gross_below_hurdle, gains_concentrated_one_session | 1.048 independent eligible/day >= 0.75; incremental available 0.440/day in the union |

## Event census (per calendar day, zero-event days included)

| engine | raw flags | parents | eligible | portfolio-available | parents/day | eligible/day | available/day | zero-eligible days | p10/p50/p90 | long/short (eligible) |
|---|---|---|---|---|---|---|---|---|---|---|
| N1 | 10853 | 7310 | 7310 | 6119 | 5.521 | 5.521 | 4.622 | 115/1324 | 1/5/11 | 3595/3715 |
| N2S | 7785 | 6140 | 6140 | 3907 | 4.637 | 4.637 | 2.951 | 105/1324 | 1/4/9 | 2741/3399 |
| N2P | 6064 | 5364 | 5364 | 0 | 4.051 | 4.051 | 0.000 | 109/1324 | 1/4/8 | 2592/2772 |
| N4 | 2744 | 1387 | 1387 | 873 | 1.048 | 1.048 | 0.659 | 564/1324 | 0/1/3 | 709/678 |

## Gross markouts (benchmark entry after the 1-minute embargo; side-adjusted bps)

| engine | subset | n | gross mean | median | LB95 day-cluster | win | no-embargo mean | 60m diag | |impulse| | MFE | MAE |
|---|---|---|---|---|---|---|---|---|---|---|---|
| N1 | ALL | 7310 | -0.28 | -4.39 | -1.46 | 0.452 | -1.01 | -0.40 | 49.74 | 49.85 | -41.74 |
| N1 | LONG | 3595 | 1.72 | -3.03 | 0.01 | 0.465 | 0.77 | 1.70 | 49.08 | 48.79 | -40.33 |
| N1 | SHORT | 3715 | -2.21 | -5.48 | -3.94 | 0.439 | -2.74 | -2.44 | 50.38 | 50.89 | -43.10 |
| N1 | block_1 | 1827 | -0.35 | -5.17 | -3.31 | 0.447 | -0.31 | 0.53 | 65.46 | 60.11 | -51.54 |
| N1 | block_2 | 1828 | 1.99 | -3.58 | -0.14 | 0.451 | 0.09 | 0.68 | 43.35 | 45.31 | -33.72 |
| N1 | block_3 | 1827 | -1.57 | -5.45 | -3.75 | 0.435 | -2.41 | -2.21 | 43.77 | 46.93 | -38.79 |
| N1 | block_4 | 1828 | -1.18 | -3.53 | -3.24 | 0.473 | -1.41 | -0.60 | 46.38 | 47.08 | -42.91 |
| N2S | ALL | 6140 | 1.56 | -2.32 | 0.44 | 0.469 | 1.77 | n/a | n/a | 42.55 | -34.81 |
| N2S | LONG | 2741 | 2.26 | -1.56 | 0.61 | 0.482 | 2.27 | n/a | n/a | 42.25 | -34.90 |
| N2S | SHORT | 3399 | 1.00 | -2.86 | -0.54 | 0.459 | 1.37 | n/a | n/a | 42.79 | -34.74 |
| N2S | block_1 | 1535 | 4.47 | -1.65 | 2.00 | 0.487 | 4.77 | n/a | n/a | 53.93 | -40.47 |
| N2S | block_2 | 1535 | 2.38 | -3.03 | -0.03 | 0.449 | 1.94 | n/a | n/a | 34.62 | -26.59 |
| N2S | block_3 | 1535 | -0.92 | -3.50 | -2.96 | 0.456 | -0.39 | n/a | n/a | 43.66 | -38.18 |
| N2S | block_4 | 1535 | 0.33 | -0.98 | -1.61 | 0.484 | 0.78 | n/a | n/a | 37.99 | -34.01 |
| N2P | ALL | 5364 | 0.78 | -2.26 | -0.40 | 0.464 | 0.47 | n/a | n/a | 43.46 | -36.06 |
| N2P | LONG | 2592 | 1.83 | -1.90 | 0.06 | 0.472 | 1.38 | n/a | n/a | 43.09 | -35.90 |
| N2P | SHORT | 2772 | -0.21 | -2.65 | -1.91 | 0.457 | -0.39 | n/a | n/a | 43.80 | -36.20 |
| N2P | block_1 | 1341 | 0.81 | -3.39 | -2.09 | 0.447 | 0.30 | n/a | n/a | 52.06 | -43.66 |
| N2P | block_2 | 1341 | 1.84 | -2.78 | -0.44 | 0.448 | 1.36 | n/a | n/a | 37.39 | -28.10 |
| N2P | block_3 | 1341 | -0.07 | -1.89 | -2.10 | 0.475 | 0.25 | n/a | n/a | 45.27 | -37.53 |
| N2P | block_4 | 1341 | 0.54 | -0.97 | -1.61 | 0.487 | -0.05 | n/a | n/a | 39.10 | -34.94 |
| N4 | ALL | 1387 | -0.43 | -1.11 | -3.91 | 0.488 | -1.06 | n/a | n/a | 51.31 | -50.10 |
| N4 | LONG | 709 | 0.69 | -0.61 | -3.83 | 0.495 | -0.21 | n/a | n/a | 49.63 | -48.47 |
| N4 | SHORT | 678 | -1.59 | -1.51 | -6.62 | 0.481 | -1.94 | n/a | n/a | 53.06 | -51.80 |
| N4 | block_1 | 346 | 1.75 | 2.97 | -7.39 | 0.517 | 0.82 | n/a | n/a | 64.54 | -62.71 |
| N4 | block_2 | 347 | -4.42 | -3.96 | -9.26 | 0.441 | -5.48 | n/a | n/a | 38.07 | -42.35 |
| N4 | block_3 | 347 | 1.59 | 0.18 | -3.90 | 0.501 | 2.31 | n/a | n/a | 47.73 | -43.22 |
| N4 | block_4 | 347 | -0.62 | -1.06 | -8.22 | 0.493 | -1.87 | n/a | n/a | 54.93 | -52.16 |
| N4 | session_LONDON | 469 | -0.78 | -2.48 | -5.12 | 0.467 | -1.20 | n/a | n/a | 37.18 | -35.83 |
| N4 | session_NEWYORK | 452 | 2.69 | 0.66 | -4.07 | 0.502 | 2.47 | n/a | n/a | 72.84 | -65.39 |
| N4 | session_TOKYO | 466 | -3.09 | -0.22 | -8.38 | 0.496 | -4.33 | n/a | n/a | 44.65 | -49.62 |
| N2S | SPOT_ONLY | 4263 | 1.39 | -1.98 | 0.13 | 0.473 | 1.76 | n/a | n/a | 39.27 | -33.12 |
| N2S | SPOT_AND_PERP | 1877 | 1.96 | -3.10 | -0.50 | 0.460 | 1.79 | n/a | n/a | 50.01 | -38.67 |
| N2S | MATCHED_CONTROLS | 6140 | -3.53 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| N2S | INCREMENTAL_VS_CONTROLS | 6140 | 5.09 | n/a | 3.93 | n/a | n/a | n/a | n/a | n/a | n/a |

## Cost screen (taker/taker; 14 sensitivity / 16 base / 24 stress)

| engine | n | gross | net@14 | net@16 | LB95@16 | PF@16 | net@24 | PF@24 | net R@16 | net USDT@16 | blocks+ | max block share | w/o top-5 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| N1 | 7310 | -0.28 | -14.28 | -16.28 | -17.46 | 0.48 | -24.28 | 0.34 | -5095.87 | -25479.36 | 0/4 | n/a | -16.67 |
| N2S | 6140 | 1.56 | -12.44 | -14.44 | -15.56 | 0.47 | -22.44 | 0.32 | -5011.15 | -25055.75 | 0/4 | n/a | -14.94 |
| N2P | 5364 | 0.78 | -13.22 | -15.22 | -16.40 | 0.45 | -23.22 | 0.31 | -4168.61 | -20843.03 | 0/4 | n/a | -15.69 |
| N4 | 1387 | -0.43 | -14.43 | -16.43 | -19.91 | 0.50 | -24.43 | 0.37 | -1126.70 | -5633.51 | 0/4 | n/a | -17.69 |

## Overlap / incremental cadence (chronological one-signal replay of N1 + N2S + N4)

| engine | eligible | standalone/day | available in union | available/day | overlap rate | same-dir blocked | opposite blocked | incremental vs others | incremental/day | profitable available @16 | profitable/day |
|---|---|---|---|---|---|---|---|---|---|---|---|
| N1 | 7310 | 5.521 | 6119 | 4.622 | 0.187 | 631 | 560 | 4010 | 3.029 | 1840 | 1.390 |
| N2S | 6140 | 4.637 | 3907 | 2.951 | 0.386 | 1801 | 432 | 3170 | 2.394 | 1094 | 0.826 |
| N4 | 1387 | 1.048 | 873 | 0.659 | 0.398 | 400 | 114 | 583 | 0.440 | 263 | 0.199 |
| UNION | 14837 | n/a | 10899 | 8.232 | n/a |  |  |  | n/a | 3197 | n/a |

## N2: spot flow vs matched price/volume controls and vs perp flow

- Matched controls: {"candidate_control_boundaries": 75178, "control_gross_bps_mean": -3.525, "engine": "N2S", "incremental_bps_lb95_day_cluster": 3.93, "incremental_bps_mean": 5.089, "parent_gross_bps_mean": 1.563, "parents_with_controls": 6140}
- Spot flags 7785, perp flags 6064, in both 2299, spot-only share 0.705
- SPOT_ONLY cohort: n 4263, gross 1.39 bps (LB95 0.13); SPOT_AND_PERP: n 1877, gross 1.96; PERP all: n 5364, gross 0.78
- Replication: single spot venue (Binance) -> exploratory screen; persistent spot leadership is NOT claimed.

## N4 sessions (secondary): {"LONDON": {"flags": 915, "parents": 469, "sessions": 946}, "NEWYORK": {"flags": 915, "parents": 452, "sessions": 946}, "TOKYO": {"flags": 914, "parents": 466, "sessions": 946}}

## Kill flags

- N1: {"chronological_unstable": true, "core_cadence_lt_0_75": false, "gross_below_base_cost": true, "movement_mostly_completed_impulse": true}
- N2S: {"cadence_collapses_after_dedup": false, "chronological_unstable": true, "effect_disappears_after_embargo": false, "gross_below_base_cost": true, "incremental_over_controls_absent": false, "remaining_effect_1_to_5_bps": true}
- N4: {"chronological_unstable": true, "gains_concentrated_one_block": false, "gains_concentrated_one_session": true, "gross_below_base_cost": true, "incremental_cadence_negligible": false, "pooled_gross_below_hurdle": true}

## Data coverage

- Bybit 1m/5m trade-built bars 2021-11-15..2025-06-30 (1324 days); holdout from 2025-07-01 never read.
- Binance perp 5m klines: AVAILABLE (394272 rows, taker-buy quote AVAILABLE); Binance spot 5m klines: AVAILABLE (385328 rows, gaps > 5m 1); sync share spot 1.0000, perp 1.0000.
- Spot venues: ['BINANCE_SPOT']; second venue MISSING (no second spot venue archive on disk) -> N2 is an exploratory single-venue screen
- OI (Binance, recorded only): AVAILABLE; E4 raw coverage: 0 complete days of 6 touched.

## E4 collection audit (read-only)

- exchange_event_time: **PRESERVED** — row.ts_exchange_ms = message `ts`; per-event `T` kept inside `raw`
- local_utc_receipt_time: **PRESERVED** — row.ts_received_ms = int(time.time()*1000) at receipt (wall clock, UTC epoch ms)
- monotonic_receipt_time: **MISSING** — no time.monotonic()/perf_counter field on the row; wall clock only
- connection_reconnect_ids: **MISSING** — reconnects are only counted in state.json stats; no per-row connection id
- gap_clock_health_telemetry: **PARTIAL** — sequence gaps counted + latency stats in state.json (every 2000 messages); the per-event seq_gap flag is in-memory only, never written to the raw row; no clock-sync telemetry
- bybit_allLiquidation_raw: **PRESERVED** — V10 topics ['publicTrade.BTCUSDT', 'orderbook.50.BTCUSDT', 'allLiquidation.BTCUSDT', 'tickers.BTCUSDT']
- bybit_public_trades: **PRESERVED** — V10 topics ['publicTrade.BTCUSDT', 'orderbook.50.BTCUSDT', 'allLiquidation.BTCUSDT', 'tickers.BTCUSDT']
- bybit_bbo: **DERIVABLE** — V10 subscribes orderbook.50 only (BBO = top of the maintained book; V5 collector had orderbook.1: False)
- bybit_depth_snapshots_deltas: **PRESERVED** — orderbook.50 snapshot + delta
- bybit_sequence_update_fields: **PRESERVED** — `u`, `seq`, `cts` stay inside the verbatim `raw` string
- binance_perp_trades_bbo: **MISSING** — no Binance feed in any collector
- spot_venue_feeds_two_independent: **MISSING** — no spot venue feed configured (0 of 2)
- mark_index_price: **PARTIAL** — tickers delta carries markPrice / indexPrice only when they change; no dedicated stream
- open_interest: **PARTIAL** — tickers delta carries openInterest when it changes (seen: True)
- funding_estimate: **PARTIAL** — tickers delta carries fundingRate when it changes (seen: True)
- funding_actual_settlement: **MISSING** — no funding-settlement / execution feed; no REST funding history journal
- next_funding_time: **PARTIAL** — tickers delta carries nextFundingTime when it changes (seen: True)
- contract_metadata: **MISSING** — instrument-info (tick / lot size, status) is never journaled
- raw_units: **PRESERVED** — the full message string is stored verbatim with its sha256
- schema_version: **PRESERVED** — bybit-public-raw-1 on every row
- timestamp_units: **PRESERVED** — field names carry the unit (ts_received_ms / ts_exchange_ms); Bybit `ts`/`T` are ms
- aggressor_semantics: **PRESERVED** — publicTrade `S` = taker side; allLiquidation `S=Buy` = a LONG was liquidated (V10 design section 1, verified empirically)

Proposed collector-only change (NOT implemented): {"daily_rest_journal": ["Bybit instruments-info (contract metadata)", "Bybit funding-rate history (actual settlements)", "Bybit open-interest history"], "invariants": ["schema bybit-public-raw-2 with raw kept verbatim", "never run on the cloud container", "never imported by V10 / V11 runners"], "periodic_rows": ["clock_health every 60 s: wall vs monotonic drift, last message age, reconnect count"], "repo_rule": "requires an explicit owner decision; this audit implements nothing", "row_additions": ["rx_monotonic_ns", "conn_id (uuid4 per WebSocket connection)", "conn_seq (per-connection counter)", "seq_gap (bool)"], "subscriptions": ["orderbook.1.BTCUSDT (explicit BBO) next to orderbook.50", "optional: Binance USDT-perp aggTrade + bookTicker, one or two spot venue trade feeds, each in its own raw root"], "where": "a NEW module (e.g. btc_swing/research/collector_v2/) with its own raw root; the frozen V10 feed, its store and every strategy decision stay byte-identical"}

Nothing here is runner-ready; a verdict of ELIGIBLE FOR FULL POLICY REPLAY would only start the next research phase.
