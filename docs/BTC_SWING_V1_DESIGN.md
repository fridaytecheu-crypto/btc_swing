# BTC Leveraged Swing Engine V1 — design and foundation (2026-10-04)

Status: V1 design + minimum data/provider/backtest foundation implemented and validated on real
archive data. Paper/backtest only. No live trading, no authenticated exchange endpoints, no real
money, no parameter optimisation. Stop point: the owner reviews this document and the proposed
Phase 2 before anything else is built.

Package: `btc_swing/`. CLI: `btc-swing`.
Config: `config/btc_swing.default.yaml`. Tests: `tests/btc/` (32 tests). Smoke report on real
2024 data: `reports/btc_swing_v1_foundation_smoke_2024.md`.

Repository note: developed first as a separate track inside `jack-app`, migrated on 2026-10-05
into this standalone repository (`fridaytecheu-crypto/btc_swing`, branch `main`). The generic
utilities it used from the equity codebase (hashing, provider entitlement, settings, code
version) were extracted into `btc_swing/core`; no equity strategy code was copied.

---

## 1. Architecture

```
Binance Vision archive (static files + published SHA256)        synthetic twin (tests)
            │  CryptoMarketDataProvider.fetch(dataset, symbol, tf, period)
            ▼
ArchiveStore  — raw zip kept byte-for-byte, append-only JSONL manifest (immutable)
            ▼  normalise (header/no-header, ms/µs, OHLC sanity, alignment, gap count)
BarStore      — Parquet per (dataset, symbol, tf, month) + manifest with source sha
            │  (resumable: unchanged source sha -> skip)
            ▼
MultiTfSeries — 5m -> 15m/1h/4h/1d resampled once, completed bars only, causal indicators
            ▼  MarketView(t): per-timeframe index of the last bar with close_time <= t
BacktestEngine (per completed 5m bar):
   1 fill pending entry at open         4 decision at t = close_time:
   2 exits on this bar's path             regime -> episode state machine -> setup detectors
     (liq > stop > TP1 > TP2 > time)      -> sizing (equity -> risk -> stop -> qty -> leverage)
   3 funding events in (prev, t]        5 daily equity mark at UTC midnight
            ▼
trades / episodes / decisions / daily_equity (Parquet) + manifest.json (config hash, code
version, data hashes, rule versions, information mode, result hash) + metrics.json + report.md
```

Design rules carried over from the US-equity track: raw before normalised; every threshold in
configuration; every run hashed and reproducible; decision time, information cutoff and execution
price kept distinct; an evaluation at T may only use rows whose close/availability time <= T.

## 2. Reused components (from `jack/`)

| Reused | How |
|---|---|
| `jack.core.hashing` (`sha256_text`, `stable_hash`, `round_floats`) | config hash, result hash, data hashes |
| `jack.core.clock.ProviderEntitlement` + information modes (MARKET_AS_OF / DEPLOYMENT_AS_OF) | provider latency model; `provider_available_time = close_time + latency` |
| `jack.core.versions.code_version()` | git sha (+dirty flag) in every run manifest |
| `jack.core.settings` (`JACK_DATA_DIR`) | data directory |
| Patterns, not code: strict Pydantic config with `extra="forbid"` and canonical YAML hash; immutable raw store; Parquet store with manifest; resumable month-wise ingestion; deterministic rerun check; journal rows with rule versions; state-machine style (attention engine); execution model (decision vs fill); risk/invalidation vocabulary; report structure with caveats first | re-implemented in `btc_swing` because the data shapes differ (24/7 UTC bars, no sessions/holidays, no instruments table) |

Deliberately NOT reused: US-equity universe/attention/catalyst/support/geometry/scoring/gates,
the NYSE session calendar, Massive providers, the Postgres schema for equities. The BTC engine
has no database dependency in the foundation (file-based immutability + Parquet); a Postgres
journal (reusing the `scanner_runs`/`config_versions` pattern) is a Phase 2 item, not a blocker.

## 3. New components (`btc_swing/`)

| Module | Content |
|---|---|
| `core/enums.py` | Timeframe, Side, Regime, SetupFamily (8), EpisodeState (7), ExitReason, InvalidationReason |
| `core/config.py` | `BtcStrategyConfig` (12 sections), hard ceiling `max_leverage <= 10`, `risk_per_trade <= 2%`, ascending leverage ladder, TP fractions <= 1 |
| `core/timeframes.py` | UTC epoch-ms bar arithmetic (open/close, alignment) |
| `providers/base.py` | `CryptoMarketDataProvider` (list_periods / published_checksum / fetch), `Dataset` enum, `ArchiveMeta`, canonical raw columns |
| `providers/binance_vision.py` | S3 listing with pagination, CHECKSUM verification, zip -> frame |
| `providers/synthetic.py` | seeded regime-switching 5m generator emitting Binance-format CSV |
| `ingest/normalise.py`, `ingest/pipeline.py` | CSV quirks, PIT columns, anomaly/gap counting, resumable `BtcIngestor` |
| `storage/archive_store.py`, `storage/bar_store.py` | immutable raw files + manifest; Parquet series + manifest + series hash |
| `features/resample.py`, `indicators.py`, `view.py` | completed-bar resampling, causal EMA/ATR/Donchian/fractal swings, `MarketView` with look-ahead guards |
| `regime/classifier.py` | 7-state regime from 1d + 4h |
| `setups/base.py`, `trend_pullback.py`, `registry.py` | `SetupPlan` (plan before entry), detector protocol, TREND_PULLBACK long/short |
| `episodes/state_machine.py` | `Episode`, `EpisodeManager` (cooldown, anchor de-dup, timeouts) |
| `risk/sizing.py` | equity -> risk -> stop -> qty -> leverage; isolated-margin liquidation estimate; buffer checks |
| `execution/costs.py` | taker/maker fees, adverse slippage, funding schedule |
| `backtest/engine.py`, `ledger.py` | chronological engine; Position/Trade with the four separate returns, MFE/MAE, counterfactual R hits |
| `research/metrics.py`, `report.py` | all required metrics; markdown report |
| `cli.py` | `btc-swing data probe`, `data ingest`, `backtest --verify-determinism` |

## 4. Data sources (researched 2026-10-04 from the cloud container)

Reachability: `api.binance.com` and `fapi.binance.com` answer HTTP 451 (geo-restricted) from the
container; `api.bybit.com` 403; `api.coinbase.com` 200 (spot only, no perpetuals);
`data.binance.vision` (Binance's public historical archive, S3-backed, SHA256 CHECKSUM per
file) 200. Therefore V1 reads the archive and does not depend on the REST API at all. This is
also the right choice for research: archive files are immutable, checksummed and complete.

Inventory for `BTCUSDT` USDT-margined perpetual (`data/futures/um/`):

| Dataset | Granularity | First | Last (at probe) | Used in V1 |
|---|---|---|---|---|
| klines 5m, 15m, 1h, 4h, 1d (+1m, 3m, 30m, 2h, 6h, 8h, 12h, 3d, 1w, 1mo) | monthly zips, daily zips for recent days | 2020-01 | 2026-09 | 5m is the base; 1h/4h/1d ingested only to cross-check resampling |
| fundingRate | monthly | 2020-01 | 2026-09 | yes (cost model) |
| premiumIndexKlines 5m (perpetual premium = basis proxy) | monthly | 2020-01 | 2026-09 | parser ready, not used in V1 rules |
| markPriceKlines 5m | monthly | 2020-01 | 2026-09 | parser ready; Phase 2: liquidation on mark price |
| metrics (open interest, OI value, top-trader L/S ratios, global L/S ratio, taker buy/sell volume ratio; 5-minute rows) | daily zips only | 2020-09-01 | current | parser + ingest path ready (`ingest_metrics: false`) |
| aggTrades, trades, bookTicker, bookDepth | monthly/daily | 2020-01 | current | not used (trade imbalance is already in klines as `taker_buy_volume`) |
| spot klines 5m (`data/spot/`) | monthly | 2017-08 | 2026-08 | parser ready (`ingest_spot: false`); spot context kept as a separate dataset, never mixed with perp bars |

Format facts handled in code (`ingest/normalise.py`): futures files have a header row, spot files
do not; spot timestamps are microseconds from 2025-01 (detected per file by magnitude); kline
`close_time` is `open + tf - 1 ms` (validated; canonical `close_time = open + tf`); klines carry
`taker_buy_volume` and `trades` (trade-imbalance feature source).

Data quality found on the ingested window 2023-09..2024-12 (16 months, 140,544 five-minute bars,
80 archive files, all checksums verified, 0 missing bars, 0 OHLC anomalies): resampled 1h/4h/1d
bars match Binance's native bars exactly for 99.97% of bars; the 4 differing hours are two venue
incidents where the 5m archive is stale/incomplete while native hourly bars are not:
2023-11-10 15:00–17:00 UTC and 2024-10-28 20:00–21:00 UTC (12 flat zero-volume 5m bars). Daily
bars match exactly. Phase 2 adds a flat-run detector so such hours are flagged in reports.

## 5. Timeframe handling

- Base series: 5m perpetual klines. 15m/1h/4h/1d are derived from 5m by bucketing on
  `open_time // tf` (UTC-aligned, daily at 00:00 UTC, as on the venue). Native higher-TF bars are
  never used in decisions; they are only a correctness oracle (`tests/btc/test_resample_pit.py`).
- A derived bar exists only when its nominal close `<= T`. Partial buckets are dropped. A 1d bar
  for "today" is invisible until 00:00 UTC of the next day; a 4h bar until its 4-hour boundary.
- Indicators are causal and computed once per timeframe over completed bars; the view at T is an
  index per timeframe (binary search on close_time). Reading a negative offset raises
  `LookAheadError`. The truncation test proves the decision journal up to T is identical whether or
  not data after T exists.
- Roles: 1d = macro trend / structural regime; 4h = dominant trend and major levels; 1h = primary
  swing setup; 15m = confirmation; 5m = entry timing only. A 5m signal alone cannot open an
  episode: detection needs the 4h trend and the 1h structure; the 5m bar only times the entry.
- Information modes: `MARKET_AS_OF` (bar visible at its close) is the default for the archive;
  `DEPLOYMENT_AS_OF` applies `latency_minutes` (default 0) to model scheduler/feed lag. With a 5m
  scheduler running at bar close, the decision uses the bar that just closed and fills at the
  next bar's open.

## 6. Regime definition (`regime/classifier.py`, 1d + 4h completed bars)

Priority order, first match wins:

| Regime | Rule (defaults in config) |
|---|---|
| UNCLEAR | < 60 completed 1d bars or indicators not warm |
| HIGH_VOLATILITY | 1d ATR(14)/close >= 5.5% |
| BREAKOUT_REGIME | 1d ATR(14) / ATR(14) ten bars earlier >= 1.25 and a 1d close beyond the prior 20-bar Donchian channel within the last 3 bars |
| TREND_UP | 1d close > EMA50, EMA20 > EMA50, EMA50 rising over 5 bars, and 4h EMA20 > EMA50 |
| TREND_DOWN | mirror image |
| LOW_VOLATILITY | 1d ATR(14)/close <= 1.8% |
| RANGE | abs(EMA20 - EMA50) <= 1.0 x ATR on 1d |
| UNCLEAR | otherwise |

The regime gates eligibility per setup family (`setups.eligible_regimes`); it never sizes,
triggers or exits (regime exit exists as an option, default off). 2024 occupancy on real data:
TREND_UP 41.7%, RANGE 25.8%, TREND_DOWN 12.1%, HIGH_VOL 8.7%, UNCLEAR 6.5%, BREAKOUT 5.2%,
LOW_VOL 0% (the 1.8% threshold is probably too low for BTC daily ATR; reported, not tuned).

## 7. Setup definitions

Eight interpretable families are defined (enum + eligibility config). V1 foundation implements
one pair; the rest follow the same `SetupDetector` protocol in Phase 2.

| Family | Eligible regimes | Core idea (long; short mirrored) | Status |
|---|---|---|---|
| TREND_PULLBACK_LONG/SHORT | TREND_UP / TREND_DOWN | 4h trend intact, 1h pullback of >= 1 ATR from a confirmed swing into the 1h EMA20..EMA50 zone, 15m reclaim of EMA20, 5m break of the previous 5m high | implemented |
| BREAKOUT_LONG / BREAKDOWN_SHORT | BREAKOUT_REGIME, RANGE, LOW_VOL | 4h Donchian/range boundary, 1h close beyond it with ATR expansion, entry on 15m/5m retest hold; stop below the broken level | designed |
| SUPPORT_RECLAIM_LONG / RESISTANCE_REJECTION_SHORT | RANGE, TREND_UP / TREND_DOWN | 4h level (prior swing / range edge), 1h sweep-and-reclaim (or test-and-reject), 15m confirmation; stop beyond the sweep extreme | designed |
| MOMENTUM_CONTINUATION_LONG/SHORT | TREND_UP, BREAKOUT / TREND_DOWN, BREAKOUT | strong 4h impulse, shallow 1h flag (< 0.6 ATR retrace), 5m continuation break; stop below flag low | designed |

A `SetupPlan` is frozen at detection and records: family, side, structural anchor, entry zone
(low/high), trigger text, invalidation level + rule, STOP_PRICE, STOP_DISTANCE (reference),
STOP_REASON, structural target, ATR of the setup timeframe, detection time, inputs.

## 8. Entry state machine (`episodes/state_machine.py`)

```
NO_SETUP --detect--> WATCH --zone reached AND 15m confirmed--> ENTRY_READY --5m trigger--> TRIGGERED
   WATCH/ENTRY_READY --5m close beyond invalidation | regime ineligible | watch timeout (96 bars = 8h)
                       | setup-TF close beyond anchor (RAN_WITHOUT_US)--> INVALIDATED
   ENTRY_READY --no trigger within 24 bars (2h)--> WATCH
   TRIGGERED --fill at next 5m open (sizing accepted)--> ACTIVE --stop/TP/trail/time--> CLOSED
   TRIGGERED --sizing rejected--> INVALIDATED (RISK_REJECTED:<reason>)
```

One episode = at most one trade; repeated 5m signals never create a second episode while one is
open; `max_concurrent_positions = 1`. After an episode ends: per-family cooldown (12 bars after a
trade, 6 after invalidation) and a structural-anchor de-duplication window (no new episode on the
same anchor within 0.25 ATR for the longer of cooldown / watch timeout). Every transition is
journaled with time, bar and reason. The 2024 smoke run produced 102 episodes and 43 trades
(0.28 setups/day, 0.82 entries/week, no trade shorter than 1 hour, median hold 24 h).

## 9. Stop / invalidation methodology

- Invalidation level is structural (1h swing low/high that preceded the impulse). The stop sits
  beyond it by `stop_atr_buffer` (0.5 x ATR 1h), so STOP_REASON = `1h_SWING_LOW_MINUS_0.5xATR`.
- Sanity band in volatility units: stop distance must be within [0.75, 4.0] x ATR(1h) measured
  from the zone mid; otherwise fall back to the entry-zone edge minus the buffer
  (`ENTRY_ZONE_EDGE_MINUS_0.5xATR_1h`); if that also fails, no setup. Fixed percentages are never
  the mechanism; the realised stop distances in 2024 were 0.5%–3.2% (median 1.45%, 2.1 ATR).
- Stored per trade: STOP_PRICE, STOP_DISTANCE (price, %, ATR), STOP_REASON, initial and final stop.
- Exits: TP1 at 1.5R closes 40%; TP2 at 3R closes 30%; after TP1 the stop moves to breakeven
  (applied from the next bar) and the remainder trails the 1h swing structure minus 0.5 ATR
  (ratchets only). The trail is what lets a winner run while the trend structure holds
  (21 of 43 exits in 2024 were TRAIL; mean MFE 2.5R vs realised mean win 1.6R, so target design is
  an open research question, not a tuned fact). Safety cap 240 h (hit once). 1R/1.5R/2R/3R and the
  structural target are evaluated counterfactually on every trade (reached before the initial stop)
  and reported; they are not assumed.

## 10. Leverage / risk model (`risk/sizing.py`)

```
risk_amount   = equity x risk_per_trade                 (default 0.50%; schema ceiling 2%)
qty           = risk_amount / |entry_ref - stop|
notional      = qty x entry_ref
leverage      = smallest allowed level (1,2,3,5,10) with notional / L <= equity x margin_cap (25%)
                AND liquidation checks pass; hard ceiling 10x in the schema
margin        = notional / L  (isolated)
liquidation   = entry x (1 - 1/L + mmr)  long | entry x (1 + 1/L - mmr) short, mmr = 0.5% (config)
checks        : liq distance >= 3.0 x stop distance  AND  >= 3.0 x ATR(4h)
```
If the full-risk notional fits under the cap only at a leverage that fails the liquidation
checks, the position is scaled DOWN at the safest acceptable leverage (`RISK_CAPPED`, risk below
target) — never levered up. If no level passes, the trade is rejected and the episode ends with
`RISK_REJECTED`. Equity for sizing is fixed at the research equity by default
(`compounding: false`); the account curve still compounds. Per trade the ledger stores leverage,
margin, liquidation price/distance (%, ATR), stop-to-liquidation ratio, MAE, max unrealised loss,
max account loss at stop and at liquidation. 2024 smoke: leverage 1x–5x (mean 2.1x), min
stop-to-liquidation ratio 24.8, min liquidation distance 19.5% (16.7 ATR), worst MAE -4.55%,
worst single-trade account loss -0.61%, zero liquidations, zero capped trades.

## 11. Fees / slippage / funding model (`execution/costs.py`)

Taker 5 bps on every fill (entry, stop, time exit; targets too unless `use_maker_for_targets`,
then 2 bps); adverse slippage 2 bps on entries and 5 bps on stop-type exits; gap-through stops fill
at the bar open. Funding: the archive rate at each funding timestamp (8h for BTCUSDT) is applied
while the position is open, `-side x rate x qty x price`, strictly within (previous bar close,
this bar close]. No leveraged return is ever reported gross of these (2024: fees 175.8 USDT and
funding -49.8 USDT against 633.7 USDT net P&L on a 10,000 USDT research account). Liquidation is
currently detected on traded-price extremes; mark-price klines are the Phase 2 input.

## 12. Backtesting methodology

Strict chronological 5m loop (section 1). Decision at the bar close; fill at the next bar open;
exits evaluated on the following bars' high/low path with stop-first ordering when a bar touches
stop and target; funding accrued per bar; partial exits aggregated per trade. Every run writes a
manifest (config hash, code version incl. dirty flag, data series hashes, rule versions,
information mode, period, regime occupancy, result hash). `--verify-determinism` reruns and
compares hashes (2024 smoke: identical). Tests cover: partial-bucket exclusion, view indices at
close-time boundaries, causal indicators under future perturbation, swing confirmation delay,
resample == native aggregation, truncation invariance of the decision journal, accounting
identities, leverage/liquidation limits, state-machine transitions, ingest resumability and raw
immutability. Research unit = the episode and the trade; decisions at every 5m step are journaled
(105,409 rows for 2024) so WATCH episodes that never triggered can be studied in Phase 2.

## 13. Metrics (`research/metrics.py`, all in the report)

Trade count; win rate; average win/loss (R and account %); expectancy (R and account %) with a
t-statistic; profit factor; Sharpe on daily marks (annualised sqrt(365), with the flat-day caveat
printed); max drawdown; MFE/MAE (R, %); R-multiple histogram and quantiles; holding-time
distribution (<1h, 1h–1d, 1d–3d, >3d); LONG vs SHORT; by regime at entry; by setup family; by
exit reason; `reliable` flag per cell (n >= 20); setups/day, entries/day, entries/week,
setup-to-entry conversion, episode end reasons; return on underlying (sum of signed BTC_RETURN),
return on margin (mean RETURN_ON_MARGIN), return on account (sum ACCOUNT_RETURN and equity curve)
— kept separate; leverage distribution, min stop-to-liquidation ratio, min liquidation distance
(% and ATR), worst MAE, max account loss, liquidations, risk-capped count; counterfactual hit
rates for 1R/1.5R/2R/3R, TP1/TP2 executed, structural target distance. Primary metric:
risk-adjusted expectancy (mean R, t-stat, profit factor), never return on margin.

## 14. Proposed first historical validation period

Data allows 2020-01 onward. Owner rule: 2020–2021 never used for conclusions. Proposal:

| Segment | Use | Why |
|---|---|---|
| 2021-10 → 2021-12 | warm-up only (1d EMA50/ATR, 200 bars of 4h) | no decisions journaled |
| 2022-01-01 → 2023-12-31 | first validation run, pre-registered defaults, all 8 families once implemented | bear market with LUNA/FTX shocks, then 2023 recovery and ranges: both sides of the book, HIGH_VOL and RANGE regimes |
| 2024-01-01 → 2024-12-31 | second segment, same config (already ingested; smoke report exists) | ETF-driven trend, mid-year range, August shock, Q4 breakout |
| 2025-01-01 → 2026-09-30 | holdout, touched once per strategy version | — |

Acceptance for the validation report (not for "edge"): >= 20 trades per side per segment where
the regime allows, decision journal complete, resample oracle 100% outside flagged venue
incidents, no liquidation, no risk-capped trade above the 2% schema ceiling, determinism check
identical. Costs reported gross and net, with the funding share stated.

## 15. Implementation plan

**Phase 1 — foundation (done, this branch).** Provider abstraction + Binance Vision archive
provider with checksum verification; immutable raw store; Parquet bar store; resumable ingest;
PIT resampling and causal features; regime classifier; `SetupPlan`/detector protocol with the
TREND_PULLBACK pair; episode state machine; sizing/liquidation model; cost model; chronological
engine; metrics; report; CLI; 32 tests; real-data smoke on 2024 (43 trades, deterministic).

**Phase 2 — complete V1 research engine (proposed next, needs owner approval).**
1. Data: ingest `markPriceKlines` (liquidation on mark price), `metrics` (open interest,
   long/short, taker ratio) and `premiumIndexKlines` (basis) as optional PIT features; flat-run and
   venue-incident detector; extend ingestion to 2021-10 → 2026-09; store the native-vs-resample
   oracle result in the run manifest.
2. Setups: implement the remaining six families against the existing protocol, each with its own
   STOP_REASON vocabulary and structural target; add detector-level tests on scripted bars.
3. Research: signal-level labels for every episode (including never-triggered WATCH episodes:
   forward returns from the plan's zone mid at 4h/24h/72h; did the plan's invalidation hit before
   its structural target?) so the question "does the setup carry information independent of our
   trigger?" is answered; regime x family x side cells; funding-share and cost-share tables;
   null model (random entries with the same stop/target geometry and the same regime occupancy).
4. Journal: Postgres tables for runs/episodes/trades (reusing the `jack` run/config-version
   pattern) so BTC and equity research share one audit trail; keep Parquet as the bulk store.
5. Run the first validation (section 14) with the pre-registered defaults and write the
   V1 VALIDATION REPORT. No threshold search. Stop for review.

**Phase 3 — paper scheduler (not before Phase 2 review).** A 5-minute paper loop needs a live
feed; the Binance REST API is unreachable from this container (HTTP 451), so the provider choice
(jurisdiction, venue, or a licensed feed) is an owner decision. Still no orders, no keys.

Out of scope until explicitly approved: live trading, authenticated APIs, real money, parameter
optimisation, portfolio/multi-asset, LLM reasoning layer.

---

## Phase 2 addendum (2026-10-04) — research strategy completed, validation run

Approved after the Phase 1 review. Everything below was specified and its defaults frozen BEFORE
the first run on 2022–2024 data (`config/btc_swing.default.yaml`, sections `setups.breakout`,
`setups.support_reclaim`, `setups.momentum_continuation`). No parameter was changed afterwards.

### A. All eight families (shared entry mechanics in `setups/base.py::ZoneSetupDetector`)

| Family (long; short mirrored) | Discovery rule | Zone | Invalidation | STOP_REASON | Structural target | Run-away (RAN_WITHOUT_US) |
|---|---|---|---|---|---|---|
| TREND_PULLBACK | 4h EMA20>EMA50 & close>EMA50; 1h swing high after swing low; pullback >= 1 ATR(1h) | 1h EMA20..EMA50 ± 0.25 ATR | 1h swing low | `1h_SWING_LOW_MINUS_0.5xATR` | swing high | swing high |
| BREAKOUT / BREAKDOWN | 1h close beyond prior 20-bar 4h Donchian with ATR(1h) expansion >= 1.2, extension <= 2 ATR | level −0.25..+0.75 ATR | level − 0.25 ATR | `BREAK_LEVEL_4h_MINUS_0.5xATR_1h` | level + range height | level + 2 ATR |
| SUPPORT_RECLAIM / RESISTANCE_REJECTION | last confirmed 4h swing low (<= 90 bars old) swept by >= 0.1 ATR within 6 × 1h bars, 1h close back above | level −0.25..+0.75 ATR | sweep extreme | `SWEEP_EXTREME_MINUS_0.5xATR_1h` | opposite 4h swing | target (else level + 3 ATR) |
| MOMENTUM_CONTINUATION | 4h impulse bar range >= 1.5 ATR(4h), close in top 30%, within 3 bars, 4h EMA20>EMA50; 1h retrace 0.2..0.6 ATR(4h) | impulse high − 0.6..−0.2 ATR(4h) | impulse midpoint | `FLAG_LOW_MINUS_0.5xATR_1h` | impulse high + impulse range | impulse high |

Common: 15m confirmation (close back through 15m EMA20 with a bar in the trade direction, not
beyond one pad past the zone), 5m trigger (close beyond the previous 5m extreme on the right side
of the zone), structural stop bounded to [min, max] × ATR(1h) with a zone-edge fallback. Config
order of `setups.enabled` is the priority when several families fire on the same bar; the single
episode slot is kept (max one position). Setups that fire while the slot is occupied are
recorded as **shadow detections** (missed opportunities), never traded.

### B. PIT auxiliary features (`features/context.py`, recorded on every episode at detection and
every trade at trigger; features only, no gates)

funding_rate_last / mean of last 3 / annualised; open_interest, OI value, OI change 1h/4h/24h;
long/short ratio (accounts), top-trader long/short (positions), taker long/short volume ratio
(all from the 5-minute `metrics` archive, available from 2020-09); premium index close and 1h mean
(perpetual basis proxy); taker buy ratio 1h/4h and volume acceleration 1h/5m (from kline flow
columns); mark price and last-minus-mark. Every lookup is `time + latency <= t`.

### C. Mark-price liquidation
`markPriceKlines` are ingested and aligned to the 5m perpetual bars. Liquidation is evaluated on
the mark bar's adverse extreme (traded extremes only where a mark bar is missing); the trade row
records entry price, bar open before slippage, mark price at the decision, stop, liquidation
price, distance to stop and to liquidation (%, ATR), stop-to-liquidation ratio and buffer, and
`liquidation_basis`. Slippage is accounted separately from fees (`slippage` column).

### D. Research layer (`research/phase2.py`, `labels.py`, `null_benchmark.py`, `phase2_report.py`)
Forward labels for every episode (signed 4h/24h/72h returns from the detection close; structural
target vs invalidation first within the 240 h cap); geometry-matched null benchmark (K random
entries per trade, same side/stop %/ATR/sizing, identical exit engine and costs, time-matched and
regime-matched); leverage comparison at max 1x/3x/5x/10x with the same risk rule; sequential
account statistics (CAGR, max drawdown on trade curve and daily marks, Sharpe, Sortino, streaks,
time between entries, drawdown-window attribution per family); resample oracle and truncation
audit on the real data; 21-section report `reports/BTC_SWING_V1_PHASE2_VALIDATION.md`.
Command: `uv run btc-swing phase2` (segments default to 2022-01..2023-12 and 2024).
