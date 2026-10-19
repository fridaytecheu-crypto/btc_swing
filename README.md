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
**V6 ACTIVE INTRADAY** (`btc_swing/v6/`, `config/btc_swing_v6.yaml`, `docs/BTC_V6_ACTIVE_INTRADAY_DESIGN.md`,
`btc-swing v6 research|render`, `reports/BTC_SWING_V6_ACTIVE_INTRADAY_RESEARCH.md`): isolated paper-only
track (no exchange client) with four 5m/15m intraday families (momentum continuation, pullback
continuation, failed-breakout reversal, flow/OI impulse; liquidation families forward-only because no
history exists) under the owner's HARD frequency gate (>= 2 trades per calendar day AND net edge > 0 AND
PF > 1 AND DD < 10%): **C — V6 ACTIVE-INTRADAY OBJECTIVE NOT ACHIEVED** (validation 2024-07..2025-06:
654 trades, 1.79/day, gross +0.04R, costs 0.35R, net -0.30R, PF 0.55; every family negative; without the
one-position rule 2.85 trades/day at the same -0.31R). The once-only OOS window 2025-07..2026-09 was not run.
**V6.1 STRUCTURE + FLOW INTRADAY** (`btc_swing/v61/`, `config/btc_swing_v6_1.yaml`,
`docs/BTC_V6_1_STRUCTURE_FLOW_DESIGN.md`, `btc-swing v61 ingest|research|render`,
`reports/BTC_SWING_V6_1_STRUCTURE_FLOW_RESEARCH.md`): structure -> location -> flow confirmation -> entry on a
Bybit-native 5m dataset streamed from the public trade archive (`data/btc/datasets/bybit_trades_5m/`), five
structure-first families with a structure-only control, cost-share eligibility, taker/maker/maker-first entry
models and time stops: **>= 2 trades/day FAIL (0.40/day), net edge FAIL (gross -0.12R, net -0.27R, PF 0.51 on
validation; Binance robustness identical)**; the locked holdout stays unspent.
**V7 MICROSTRUCTURE SPECIALIST, Phase 1** (`btc_swing/v7/`, `config/btc_swing_v7.yaml`,
`docs/BTC_V7_MICROSTRUCTURE_DESIGN.md`, `btc-swing v7 ingest|discover|render`,
`reports/BTC_SWING_V7_EDGE_DISCOVERY.md`): matched-control edge discovery on Bybit-native 1m microstructure
bars (trade intensity, taker and PIT large-trade imbalance, delta, price response to delta, compression,
structure). **Almost no incremental microstructure edge**: flow features sit within 1 bp of matched
controls; only a failed-range-break reversal (~2 bps at 60 min) and a trend-pullback release (~3 bps,
3.6 events/day) are repeatable, both far below the ~15 bps round trip. Stopped at the report.

**V8 CANDIDATE RANKING** (`docs/BTC_V8_CANDIDATE_RANKING_DESIGN.md`): design only, superseded by the owner
before any code or run; kept on record.

**V9 SIMPLE TREND + VOLUME PULLBACK** (`btc_swing/v9/`, `config/btc_swing_v9.yaml`,
`docs/BTC_V9_SIMPLE_TREND_VOLUME_PULLBACK_DESIGN.md`, `btc-swing v9 config|research|render`,
`reports/BTC_SWING_V9_SIMPLE_TREND_VOLUME_PULLBACK.md`): one deterministic strategy (15m EMA20/50 trend,
5m impulse + 25-60% pullback with impulse-RVOL and pullback-volume rules, 1m breakout entry with relative
volume / taker imbalance / delta confirmation), conservative taker costs, the A/B/C/D volume ladder with the
mandatory funnel, TRAIN-only selection, one pass per validation window. See the report for the result.

**V10 LIVE MICROSTRUCTURE SNIPER** (`btc_swing/v10/`, `config/btc_swing_v10.yaml`,
`docs/BTC_V10_LIVE_MICROSTRUCTURE_DESIGN.md`, `btc-swing v10 forward run|status|diagnostic|shadow-report|freeze`,
`btc-swing v10 demo status|activate|deactivate|reconcile-ack`): forward-only, no backtest. Own Bybit public
feed (trades, liquidations, 50-level book), 1-second features, three families (liquidation absorption
reversal, flow absorption at a level, liquidity-break continuation) as explicit state machines, shadow fills,
hash-chained journals with forward outcomes and near-misses, and a Bybit DEMO executor that is refused until
the owner's activation event on the authoritative Mac.

**V11 FAST MULTI-FACTOR INTRADAY** (`btc_swing/v11/`, `config/btc_swing_v11.yaml`,
`docs/BTC_V11_FAST_MULTIFACTOR_DESIGN.md`, `btc-swing v11 config|research|render`,
`btc-swing v11 forward run|status|diagnostic|shadow-report|freeze|trades`,
`reports/BTC_V11_FAST_MULTIFACTOR_BACKTEST.md`): one pre-registered multi-factor score (trend, momentum,
volatility, volume, location, derivatives, live microstructure; renormalised over the available groups) with
three families (trend pullback, breakout / volatility expansion, liquidity sweep reversal), structural stops
(0.35% floor, 1.00% cap -> rejected), TP1 +0.6R 40% / TP2 +1.2R 40% / trailed runner, 20-min stall review,
60-min hard stop, 0.15% fixed risk. Historical result: classification C (net -0.42R per trade on validation).
RESEARCH + SHADOW ONLY: no exchange client, no Demo authority, fully isolated from V10 (own feed, journals,
state, LaunchAgent `com.btcswing.v11-forward`).

**V11.1 COST-AWARE HIGH-CONVICTION INTRADAY** (`btc_swing/v11_1/`, `config/btc_swing_v11_1.yaml`,
`docs/BTC_V11_1_COST_AWARE_INTRADAY_DESIGN.md`, `btc-swing v111 config|research|render`,
`reports/BTC_V11_1_COST_AWARE_INTRADAY_BACKTEST.md`): the V11 information groups unchanged, plus independent
evidence (>= 4 directional groups), no trend contradiction, tightened A/B/C requirements, failed-breakout
rejection, 0.55% stop floor / 1.00% cap and a cost-in-R filter (reject above 0.25R). ONE pre-registered run:
classification C (validation 0.10 trades/day, net -0.20R, PF 0.36; cost/R fell from 0.36 to 0.20 but gross
stayed ~0). RESEARCH ONLY: no shadow runner was built; V10 and V11 untouched.

**V11-WARM WARM-STARTED V11 SHADOW** (`btc_swing/v11_warm/`, `config/btc_swing_v11_warm.yaml`,
`docs/BTC_V11_WARM_START.md`, `btc-swing v11w config|bootstrap|seed`, `v11w forward
run|status|diagnostic|verify-warm|shadow-report|freeze|trades`, freeze `manifests/v11_warm_forward_freeze.json`,
bootstrap `manifests/v11_warm_bootstrap_1m.parquet`): the frozen V11 strategy, unchanged (hash-checked against
the V11 freeze), with a PIT-safe feature warm-start: 30 days of exact 1m bars from the Bybit public trade
archive seeded before observation_start so RVOL, ATR percentiles, EMA/RSI/ADX and structure are populated
from the first live bar; order book, liquidations and live microstructure are never backfilled. Shadow only;
separate root, journals, freeze and LaunchAgent `com.btcswing.v11-warm-forward`; V10 and V11 untouched.

**Forward full-history reports (read-only, all generations)** (`btc_swing/forward_report/`): `btc-swing v10 report
full-history`, `btc-swing v11 report full-history`, `btc-swing v11w report full-history` (`--verification` reads the
verification root). One generic layer reads the journals, state and freeze of a generation and writes
`full_history_<ts>.{json,md}` + `latest_full_history.*` under `reports/<generation>/` only: candidate -> armed ->
trigger -> executed funnel, families, sides, blockers / failed conditions, score distributions, shadow PnL / R,
outcomes, daily table, and the V10 Demo section (N/A for shadow-only generations). Nothing else is ever written;
regression tests prove journal hashes, state.json, freeze manifests and strategy hashes are unchanged and that no
exchange call is made.

**V11.2 counterfactual gating study** (research only; `btc_swing/v11_2/`, `btc-swing v112 study|combos|render`,
`reports/BTC_V11_2_COUNTERFACTUAL_GATING_STUDY.md`, `reports/v11_2/study.json`): one family gate of the frozen V11
engine changed per variant (A structure relaxations, B boundary 0.40-0.70 ATR5, C measured and tested alone), plus
three minimal A+B combinations, on the consumed TRAIN / VALIDATION windows only. No variant reaches positive gross
or net R or PF > 1 in either window; frequency can be raised to ~3.5-4.4 trades/day (A1 neutral 15m structure) with
net still about -0.37 to -0.39R. No runner, no freeze.

**V11-LITE-WARM** (`btc_swing/v11_lite_warm/`, `config/btc_swing_v11_lite_warm.yaml`, `docs/BTC_V11_LITE_WARM_DESIGN.md`
pre-registered before observation, freeze `manifests/v11_lite_warm_forward_freeze.json`, `btc-swing v11lw config|seed`,
`v11lw forward run|status|diagnostic|verify-warm|shadow-report|freeze|trades`, `v11lw report full-history|compare`): the
warm-started V11 shadow with ONE strategy delta: the A/B/C family structure conditions are computed and journaled as
context labels (`structure_labels`) and never gate arming; one arm state per side, the first frozen trigger/stop template
that fires is journaled as `selected_template` (not a setup) (score >= 70 arms; family triggers / stops / exits / risk / costs unchanged;
signal prefix `V11LWS-`). Read-only telemetry on every event (Fibonacci levels of the last confirmed 5m swing leg,
VWAP distance, 1h / 4h range position, swing distances, ATR-normalised location, flow, microstructure) plus an
in-process strict V11 control twin that attributes every LITE signal (incremental cohort = signals the frozen rules
would reject, with the failed condition). `v11lw report compare` = V11-WARM (control) vs V11-LITE-WARM (challenger),
incremental cohort, Fibonacci buckets, pre-registered checkpoints. SHADOW ONLY; separate root, journals, freeze and
LaunchAgent `com.btcswing.v11-lite-warm-forward`; V10, V11 and V11-WARM untouched (hash-proved).
Optional **Bybit DEMO execution** of the same frozen decisions (`btc_swing/v11_lite_warm/demo/`,
`docs/V11_LITE_WARM_DEMO_EXECUTION.md`, `btc-swing v11lw demo status|freeze|activate|deactivate|reconcile-ack|resume|report`):
paper money only, owner activation only, isolated Demo account by default, 5 USDT risk, caps / daily stop / halt /
reconciliation; the shadow stays the source of truth and is proven unaffected.

**Intraday portfolio research (E1 / E2 / E4)** — `btc_swing/research/intraday_portfolio/`, `btc-swing ipr
inventory|research`, outputs `reports/intraday_portfolio_research/<RUN_ID>/`: an isolated, order-disabled harness
(Phase 0 data inventory, receipt-time data contract, parent episodes, benchmark vs executable ledgers, one-position
portfolio, predeclared gates). Run `20261019T114233Z`: E1 Accepted Range Expansion REJECT, E2 Failed Auction REJECT
(benchmark ledger, 2021-11-15..2025-06-30), E4 Forced Flow INCONCLUSIVE (no 30-day receipt-timed liquidation history).
Nothing is eligible for runner engineering. Second generation on the same harness (`btc-swing ipr research-n`, run
`20261019T180134Z`): N1 Participation Impulse, N2 Persistent Spot-Flow (Binance spot klines, checksummed ingest) and N4
Session-Transition screens — EDGE REJECT for all three (gross within ±2 bps per trade before 16 bps costs), CADENCE
PLAUSIBLE CORE (5.5 / 4.6 / 1.0 independent eligible per day); E4 collector audit in `e4_collection_audit.json`.
N2 four-hour persistence test (`btc-swing ipr research-n2-4h`, run `20261019T201728Z`): the frozen N2 events at 4h are REJECTED as
directional alpha (gross -1.4 bps, net -17.4 @16; 2h / 8h diagnostics no better).
Conditional funding carry screen (`btc-swing ipr research-carry`): implemented and frozen; runs only where the Bybit
funding / mark export exists (Mac), refusing otherwise — not yet executed.

## Quick start
```
uv sync --all-extras
export BTC_DATA_DIR=./data                                  # data is git-ignored
uv run btc-swing data probe                                 # archive inventory (data.binance.vision)
uv run btc-swing data ingest --from 2023-09 --to 2024-12    # resumable, checksum-verified
uv run btc-swing backtest --from 2024-01-01 --to 2025-01-01 --verify-determinism --out data/btc/runs/smoke
uv run btc-swing data ingest --from 2021-10 --to 2024-12 && uv run btc-swing phase2
uv run pytest -q                                            # 352 tests, no database needed
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
the Bybit public collector and the non-activated execution abstraction) · `research/intraday_portfolio`
(E1 / E2 / E4 order-disabled research harness) · `cli.py` (`btc-swing`).

## Non-negotiables
- A decision at T sees only bars with `close_time <= T`; raw archive files are immutable.
- Every threshold lives in `config/btc_swing.default.yaml`; defaults are pre-registered, not searched.
- Hard limits in the config schema: `max_leverage <= 10`, `risk_per_trade <= 2%`.
- Never use 2025+ data for development; it is reserved for a single pre-registered confirmatory run.
