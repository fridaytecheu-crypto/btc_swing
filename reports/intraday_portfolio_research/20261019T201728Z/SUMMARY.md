# SUMMARY — N2 four-hour persistence test `20261019T201728Z`

**REJECT N2 AS DIRECTIONAL ALPHA** — 4h primary fails: mean_gross_ge_25bps, mean_net_ge_9bps_at_16, day_cluster_lower_bound_gt_0, chronological_stability_ge_3_of_4. 2h / 8h are diagnostics only and are never selected when 4h fails.

Frozen source `reports/intraday_portfolio_research/20261019T180134Z/n2_parents.parquet` (sha256 `f9949071fd1246fe…`, 6140 events); preregistration sha256 `7b24d5940c5f3ef4…`; funding = BINANCE_8H_PROXY (Bybit funding history not on disk); sign-correct per side; NaN when the series is absent.

## Gross markouts from the unchanged post-embargo entry (side-adjusted bps)

| horizon | role | subset | n | gross mean | median | LB95 day-cluster | win | funding mean (proxy) | funding data available | net @16 |
|---|---|---|---|---|---|---|---|---|---|---|
| 120 m | DIAGNOSTIC | ALL | 6139 | 0.66 | -5.01 | -1.32 | 0.459 | 0.01 | 1.000 | -15.36 |
| 240 m | PRIMARY | ALL | 6139 | -1.35 | -6.35 | -4.24 | 0.469 | 0.02 | 1.000 | -17.37 |
| 480 m | DIAGNOSTIC | ALL | 6139 | -1.78 | -6.81 | -5.62 | 0.473 | 0.00 | 1.000 | -17.78 |
| 120 m | DIAGNOSTIC | LONG | 2741 | 3.21 | -4.18 | -0.19 | 0.466 | 0.26 | 1.000 | -13.05 |
| 240 m | PRIMARY | LONG | 2741 | 2.74 | -3.81 | -2.67 | 0.483 | 0.45 | 1.000 | -13.71 |
| 480 m | DIAGNOSTIC | LONG | 2741 | 3.38 | -4.35 | -5.04 | 0.487 | 0.88 | 1.000 | -13.49 |
| 120 m | DIAGNOSTIC | SHORT | 3398 | -1.40 | -5.78 | -4.51 | 0.454 | -0.18 | 1.000 | -17.21 |
| 240 m | PRIMARY | SHORT | 3398 | -4.66 | -7.83 | -9.57 | 0.459 | -0.34 | 1.000 | -20.32 |
| 480 m | DIAGNOSTIC | SHORT | 3398 | -5.94 | -8.66 | -13.65 | 0.461 | -0.70 | 1.000 | -21.24 |
| 240 m | PRIMARY | AVAILABLE_AFTER_OCCUPANCY | 3267 | -2.48 | -7.04 | -6.33 | 0.462 | 0.02 | 1.000 | -18.50 |

## 4h primary economics (all eligible events / available after 4h occupancy)

| set | n | gross | net@14 | net@16 | net@24 | LB95@16 | PF@16 | PF@24 | net R | net USDT | win | blocks+ | max block share | w/o top-5 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all eligible | 6139 | -1.35 | -15.37 | -17.37 | -25.37 | -20.26 | 0.69 | 0.58 | -5680.24 | -28401.20 | 0.390 | 0/4 | n/a | -18.02 |
| available | 3267 | -2.48 | -16.50 | -18.50 | -26.50 | -22.35 | 0.66 | 0.55 | -3736.60 | -18683.01 | 0.376 | 0/4 | n/a | -19.61 |

## Decision checks (4h primary, all eligible events)

- mean_gross_ge_25bps: FAIL
- mean_net_ge_9bps_at_16: FAIL
- day_cluster_lower_bound_gt_0: FAIL
- chronological_stability_ge_3_of_4: FAIL

## Cadence after 4h occupancy (N2 only)

- frozen independent episodes 6140; eligible 4h paths 6139 (4.637/day); available after occupancy 3267 (2.468/day); occupied 2872 (same direction 1453, opposite 1419); zero-available days 108 of 1324.

Funding: Binance 8h rates applied as a labelled proxy (Bybit funding history is not on disk); a NaN funding never becomes zero in the per-trade ledger.
