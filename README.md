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
`reports/BTC_SWING_V1_PHASE2_2_EARLY_ENTRY_CONFIRMATION_EXIT.md`): not supported. Phase 2.3 tested
removing the forced breakeven after TP1 (`btc-swing phase23`,
`reports/BTC_SWING_V1_PHASE2_3_POST_TP1_EXIT.md`): not supported. Phase 2.4 tested blocking new
SHORT entries in the TREND_DOWN regime (`btc-swing phase24`,
`reports/BTC_SWING_V1_PHASE2_4_SHORT_REGIME.md`): classified A, adoptable for untouched validation,
pending the owner's decision. Phase 3 then ran the single owner-approved confirmatory test of that
variant on the untouched 2025-01 -> 2026-09 holdout (`btc-swing phase3-freeze`, `btc-swing phase3`,
`reports/BTC_SWING_V1_PHASE3_UNTOUCHED_VALIDATION.md`): **C — FAILED OUT-OF-SAMPLE** (variant
-0.07R / PF 0.90 vs CONTROL -0.10R / PF 0.85; the regime rule helps relative to CONTROL but the
frozen strategy is net negative out of sample). The 2025-01..2026-09 window is now spent as a
holdout. V1 is closed by the owner as failed out-of-sample as a strategy and successful as research
infrastructure; its artefacts are immutable.

**V2 (cost-aware learned opportunity ranking)**: V1 setups as candidate generators, frozen-V1
execution labels (net R after costs), a fixed PIT feature set, chronological walk-forward models
(`docs/BTC_SWING_V2_DESIGN.md`, `btc-swing v2 research`, `reports/BTC_SWING_V2_RANKING_RESEARCH.md`):
**C — NO USEFUL RANKING EDGE** (walk-forward Spearman -0.02, top-25% slice -0.21R vs all candidates
-0.03R). **V3 (active multi-timeframe swing, 4H/1H/15m/5m, four new families, 0.25% risk)**:
`docs/BTC_SWING_V3_DESIGN.md`, `btc-swing v3 research`, `reports/BTC_SWING_V3_ACTIVE_SWING_RESEARCH.md`:
**C — NO ROBUST STRUCTURAL EDGE** (1.5 trades/day and 3 h median hold as targeted, but -0.35R net
on 2659 trades; costs of 0.34R per trade against 0.66% stops). 2022-2026 is development data for
every generation. **V4 (event & positioning driven, three event families on rolling z-scores of OI,
funding, taker flow, premium and volume; Stage A event edge before execution)**:
`docs/BTC_SWING_V4_DESIGN.md`, `btc-swing v4 research`, `reports/BTC_SWING_V4_EVENT_POSITIONING_RESEARCH.md`:
**C — NO ROBUST EVENT EDGE** (pooled event forward returns ~0; 176 trades, gross +0.02R, net
-0.10R; only participation-confirmed LONG breakouts showed a small-sample signal). **V5
(microstructure & liquidation driven: aggTrades flow / CVD, open interest, basis, four 5-minute event
families, Stage A per-family gate, Bybit public forward collector, Bybit Demo abstraction designed but
not activated)**: `docs/BTC_SWING_V5_DESIGN.md`, `btc-swing v5 ingest|collect|research`,
`reports/BTC_SWING_V5_MICROSTRUCTURE_RESEARCH.md`: **C — NO ROBUST MICROSTRUCTURE EDGE** (no family
passed the Stage A gate; 1210 trades, gross +0.12R, net -0.01R, PF 0.97; liquidation history does
not exist in any public archive, so it is collected forward only). No further research without a new
owner pre-registration. **V5 forward observation mode** (`btc-swing v5 forward ...`,
`docs/V5_FORWARD_OBSERVATION_RUNBOOK.md`, `reports/forward/`): the frozen V5 signals are recorded
prospectively on live Bybit public data with a virtual paper ledger; observational only. Persistent-host
deployment and restart-safe migration: `deploy/`, `docs/V5_FORWARD_DEPLOYMENT.md`.
Bybit DEMO execution validation (demo endpoint only, default DISABLED): `docs/V5_DEMO_EXECUTION.md`.

## Quick start
```
uv sync --all-extras
export BTC_DATA_DIR=./data                                  # data is git-ignored
uv run btc-swing data probe                                 # archive inventory (data.binance.vision)
uv run btc-swing data ingest --from 2023-09 --to 2024-12    # resumable, checksum-verified
uv run btc-swing backtest --from 2024-01-01 --to 2025-01-01 --verify-determinism --out data/btc/runs/smoke
uv run btc-swing data ingest --from 2021-10 --to 2024-12 && uv run btc-swing phase2
uv run pytest -q                                            # 99 tests, no database needed
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
benchmark, Phase 2 runner and report) · `v2`, `v3`, `v4`, `v5` (closed generations; `v5` also holds
the Bybit public collector and the non-activated execution abstraction) · `cli.py` (`btc-swing`).

## Non-negotiables
- A decision at T sees only bars with `close_time <= T`; raw archive files are immutable.
- Every threshold lives in `config/btc_swing.default.yaml`; defaults are pre-registered, not searched.
- Hard limits in the config schema: `max_leverage <= 10`, `risk_per_trade <= 2%`.
- Never use 2025+ data for development; it is reserved for a single pre-registered confirmatory run.
