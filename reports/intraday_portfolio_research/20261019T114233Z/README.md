# Intraday portfolio research run `20261019T114233Z`

RESEARCH ONLY. No order-capable component exists in `btc_swing/research/intraday_portfolio/`.
No frozen runner (V5/V5.1, V10, V11, V11-WARM, V11-LITE) was read, modified or imported.

## Files

| file | content |
|---|---|
| data_inventory.json | Phase 0 inventory: AVAILABLE / PARTIAL / MISSING per required input |
| experiment_manifest.json | frozen parameters, package hashes, data spans, every metric |
| e1_parents.{parquet,csv} / e2_parents.* | every parent episode incl. rejected (reasons kept) |
| trade_replay.* | the BENCHMARK-ledger completed trades of E1 and E2 |
| markouts_e1_e2_benchmark.* | gross markouts after the benchmark entry (no receipt time) |
| portfolio_replay.* | one-position simulator rows for E1, E2, E1+E2 |
| cost_analysis.csv | scenarios 14/16/20/24 bps, taker/taker, per engine |
| frequency_analysis.csv | candidates / eligible / fills / executed per day, overlap, occupancy |
| regime_analysis.csv | side, chronological block, weekday/weekend, vol bucket, exit reason |
| e4_parents.* / e4_flow_parents.* / e4_controls.csv / markouts_e4.csv | E4 event study |
| SUMMARY.md | one verdict per engine with the exact reason |

## Ledgers

BENCHMARK: entry = open of the first Bybit 1m bar after the decision close; cost scenario
subtracted once (commission + spread + shortfall + latency); funding = Binance proxy (labelled).
EXECUTABLE: UNAVAILABLE for E1/E2 (no historical BBO / receipt time) — stated, never inferred.

## Data

Bybit 5m/1m trade-built bars 2021-11-15..2025-06-30
(1324 days); locked holdout from 2025-07-01
never read. E4 raw stores: see `raw_store_coverage` in data_inventory.json.
