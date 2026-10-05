# btc_swing — BTC Leveraged Swing Engine V1

Deterministic, point-in-time research engine for a risk-controlled BTC perpetual LONG/SHORT swing
methodology, scanned every 5 minutes but holding for hours to days. **Paper/backtest only**: no
live trading, no authenticated exchange endpoints, no real money, no parameter optimisation.

Read first: `docs/BTC_SWING_V1_DESIGN.md` (architecture, setups, risk model, Phase 2 addendum) and
`reports/BTC_SWING_V1_PHASE2_VALIDATION.md` (first validation on 2022–2024, frozen defaults).

## Status (2026-10-05)
Phase 1 (foundation) and Phase 2 (all eight setup families, PIT features, mark-price liquidation,
null benchmark, leverage comparison, first validation) are complete. The validation verdict is
that repeatable positive expectancy is **not demonstrated** with the frozen defaults. Phase 2.1
tested one pre-registered hypothesis (entry at the zone without confirmation, `btc-swing phase21`,
`reports/BTC_SWING_V1_PHASE2_1_ENTRY_MECHANICS.md`): partially supported, not adopted. Phase 2.2
tested zone entry with the confirmation as an early-exit filter (`btc-swing phase22`,
`reports/BTC_SWING_V1_PHASE2_2_EARLY_ENTRY_CONFIRMATION_EXIT.md`): not supported. Phase 3 has
not started. 2025+ data is reserved and must not be used for development.

## Quick start
```
uv sync --all-extras
export BTC_DATA_DIR=./data                                  # data is git-ignored
uv run btc-swing data probe                                 # archive inventory (data.binance.vision)
uv run btc-swing data ingest --from 2023-09 --to 2024-12    # resumable, checksum-verified
uv run btc-swing backtest --from 2024-01-01 --to 2025-01-01 --verify-determinism --out data/btc/runs/smoke
uv run btc-swing data ingest --from 2021-10 --to 2024-12 && uv run btc-swing phase2
uv run pytest -q                                            # 38 tests, no database needed
bash scripts/check.sh                                       # ruff format/check, strict mypy, tests
```
Data source: the Binance public historical archive (`data.binance.vision`; monthly/daily zips with
published SHA256). The REST API is not used. `manifests/` holds the sha256 of every archive file
and partition used by the published reports, so a re-ingest can be verified byte for byte.

## Layout
`btc_swing/core` (config schema + hash, enums, timeframes, hashing, settings, versions) ·
`providers` (archive provider, synthetic twin) · `storage` (immutable raw store, Parquet bar store) ·
`ingest` · `features` (PIT resampling, indicators, market view, auxiliary features) · `regime` ·
`setups` (eight families) · `episodes` (state machine) · `risk` (sizing, liquidation) · `execution`
(fees, slippage, funding) · `backtest` (engine, ledger) · `research` (metrics, labels, null
benchmark, Phase 2 runner and report) · `cli.py` (`btc-swing`).

## Non-negotiables
- A decision at T sees only bars with `close_time <= T`; raw archive files are immutable.
- Every threshold lives in `config/btc_swing.default.yaml`; defaults are pre-registered, not searched.
- Hard limits in the config schema: `max_leverage <= 10`, `risk_per_trade <= 2%`.
- Never use 2025+ data for development; it is reserved for a single pre-registered confirmatory run.
