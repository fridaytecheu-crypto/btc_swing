# btc_swing — working notes for Claude sessions

Project: BTC Leveraged Swing Engine V1, a deterministic, point-in-time research engine for a
risk-controlled BTC perpetual LONG/SHORT swing methodology. Paper/backtest only.
Read `docs/HANDOFF.md`, then `docs/BTC_SWING_V1_DESIGN.md`,
`reports/BTC_SWING_V1_PHASE2_VALIDATION.md`, `reports/BTC_SWING_V1_PHASE2_1_ENTRY_MECHANICS.md` and
`reports/BTC_SWING_V1_PHASE2_2_EARLY_ENTRY_CONFIRMATION_EXIT.md`,
`reports/BTC_SWING_V1_PHASE2_3_POST_TP1_EXIT.md`, `reports/BTC_SWING_V1_PHASE2_4_SHORT_REGIME.md` and
`reports/BTC_SWING_V1_PHASE3_UNTOUCHED_VALIDATION.md` (Phase 3: C, failed out of sample).
V1 is closed and immutable. V2 (learned ranking, `btc_swing/v2/`): `docs/BTC_SWING_V2_DESIGN.md`
and `reports/BTC_SWING_V2_RANKING_RESEARCH.md` (classification C — no useful ranking edge).
V3 (active multi-timeframe swing, `btc_swing/v3/`): `docs/BTC_SWING_V3_DESIGN.md` and
`reports/BTC_SWING_V3_ACTIVE_SWING_RESEARCH.md` (classification C — no robust structural edge).
V4 (event & positioning driven, `btc_swing/v4/`): `docs/BTC_SWING_V4_DESIGN.md` and
`reports/BTC_SWING_V4_EVENT_POSITIONING_RESEARCH.md` (classification C — no robust event edge).
V5 (microstructure & liquidation driven, `btc_swing/v5/`): `docs/BTC_SWING_V5_DESIGN.md` and
`reports/BTC_SWING_V5_MICROSTRUCTURE_RESEARCH.md` (classification C — no robust microstructure edge).
`btc_swing/v5/collector.py` is a PUBLIC-data Bybit collector (no auth); `btc_swing/v5/execution.py`
is a Bybit Demo execution abstraction that is designed but NOT activated (never activate it inside a
research phase; no credentials exist or are required).
V5 FORWARD OBSERVATION MODE (`btc_swing/v5/forward/`, `docs/V5_FORWARD_OBSERVATION_RUNBOOK.md`): the
frozen V5 strategy recorded prospectively on live Bybit public data; never change a V5 rule during
the observation; snapshots in `reports/forward/` are immutable. Deployment/migration: `deploy/`,
`docs/V5_FORWARD_DEPLOYMENT.md` (`v5 forward status-text|health|integrity|export|verify`).
Demo execution: `v5 demo preflight|smoke|activate|deactivate|status|verify-journal|recover|reconcile-ack`, config `config/btc_swing_v5_demo.yaml`.
Temporary authoritative host = owner's Mac (launchd, `deploy/macos/`, `docs/V5_MAC_AUTHORITATIVE_HOST.md`):
`v5 forward claim-authority|release-authority|coverage|host-status|signal-diagnostic` (read-only); STRATEGY_DEMO only via the
host-bound `STRATEGY_DEMO_ACTIVATED` event; the cloud container must never run the forward runner again.
V5.1 = BTC_V5_1_DATA_QUALITY_FIX (`btc_swing/v51/`, `config/btc_swing_v5_1.yaml` hash `144f7d58bb71…`,
`docs/BTC_SWING_V5_1_DATA_QUALITY_FIX.md`): the frozen V5 rules (every strategy section byte-identical) on
VALID observations only (gap rows are never observations; clean windows; clean-bar ATR) plus Bybit public
history warm-up seeds (OI/premium/funding, strictly before the V5 start). Own freeze
`manifests/v5_1_forward_freeze.json`, own journals under `forward_v51/`, `V51D-` order ids. CLI `btc-swing v51
forward freeze|cycle|coverage|host-status|signal-diagnostic|compare-diagnostic`, `v51 seed oi|premium|funding|status`,
`v51 demo activate|deactivate|status|reconcile-ack`. Never modify V5 for V5.1.

V6 ACTIVE INTRADAY (`btc_swing/v6/`, `config/btc_swing_v6.yaml`, `docs/BTC_V6_ACTIVE_INTRADAY_DESIGN.md`,
`reports/BTC_SWING_V6_ACTIVE_INTRADAY_RESEARCH.md`, tables `reports/v6/`): isolated PAPER-ONLY research
track (no exchange client, no credentials, `V6D-` reserved); four historical families A/B/C/F on Binance
history with the hard frequency gate (>= 2 trades per calendar day AND net edge > 0 AND PF > 1 AND DD < 10%);
D/E forward-only. Phase 2-4 result: classification C — V6 ACTIVE-INTRADAY OBJECTIVE NOT ACHIEVED (see the
report). The once-only OOS window 2025-07..2026-09 was NOT run (`v6 research --oos --confirm-once-only-oos`
runs it exactly once; refused by a lock file afterwards). CLI `btc-swing v6 config|research|render`.
Never modify V5/V5.1 for V6.

V6.1 STRUCTURE + FLOW INTRADAY (`btc_swing/v61/`, `config/btc_swing_v6_1.yaml`,
`docs/BTC_V6_1_STRUCTURE_FLOW_DESIGN.md` frozen at commit 0de0846 before any result,
`reports/BTC_SWING_V6_1_STRUCTURE_FLOW_RESEARCH.md`, tables `reports/v61/`): structure -> location -> flow
confirmation -> entry on a BYBIT-NATIVE 5m dataset built from the public trade archive (`btc-swing v61
ingest`; `data/btc/datasets/bybit_trades_5m/`, raw gz not retained, sha256 manifest; holdout days refused).
Five families, structure-only control, cost-share eligibility, taker/maker/maker-first entry models,
time stops. CLI `btc-swing v61 ingest|research|render`. Result: >= 2 trades/day FAIL (0.40/day), net edge
FAIL (net -0.27R, PF 0.51; gross negative; Binance identical) — do NOT spend the locked holdout. The locked holdout
2025-07..2026-09 is NOT downloaded or run by this track; spending it needs an explicit owner decision. Paper only; no
exchange client.

V7 MICROSTRUCTURE SPECIALIST (`btc_swing/v7/`, `config/btc_swing_v7.yaml`, `docs/BTC_V7_MICROSTRUCTURE_DESIGN.md`
frozen at d419f3c, `reports/BTC_SWING_V7_EDGE_DISCOVERY.md`, tables `reports/v7/`): Phase 1 edge discovery only
(no engine). Bybit-native 1m microstructure dataset `data/btc/datasets/bybit_trades_1m/` (PIT large-trade
thresholds; `btc-swing v7 ingest`; holdout days refused). Result: almost no incremental microstructure edge
(flow features within 1 bp of matched controls; failed-break reversal ~2 bps and trend-pullback release ~3 bps
are the only repeatable effects). CLI `btc-swing v7 ingest|discover|render`. Paper only; nothing activated;
any Phase 2 needs a new owner pre-registration.

V8 CANDIDATE RANKING (`docs/BTC_V8_CANDIDATE_RANKING_DESIGN.md`): SUPERSEDED by owner decision (2026-10-12)
before any code or run; design kept on record only. No ranking / ML work is to be done.

V9 SIMPLE TREND + VOLUME PULLBACK (`btc_swing/v9/`, `config/btc_swing_v9.yaml`,
`docs/BTC_V9_SIMPLE_TREND_VOLUME_PULLBACK_DESIGN.md`, `reports/BTC_SWING_V9_SIMPLE_TREND_VOLUME_PULLBACK.md`,
tables `reports/v9/`): ONE deterministic BTCUSDT strategy (15m EMA20/50 trend, 5m impulse + pullback with
volume rules, 1m breakout entry with rvol / taker imbalance / delta confirmation; taker costs; A/B/C/D volume
ladder; funnel; TRAIN-only selection, one pass per validation window) on the existing Bybit 1m dataset. CLI
`btc-swing v9 config|research|render`. Result: classification C — PRICE ONLY ~0.9 trades/day at net -0.19R
(gross ~3 bps vs 15 bps costs); the 0.50% minimum stop rejects ~88% of triggers; the floor-stop sensitivity
reaches 4.7/day at -0.28R; no volume rule adds edge on both validation windows. Paper only; no exchange
client; `V9D-` reserved; holdout not read. Stop-geometry sensitivity (`v9 stop-study`,
`reports/BTC_SWING_V9_STOP_GEOMETRY_STUDY.md`): floors 0.5-1.5% with +1R..+2R targets at constant risk never turn
net positive (gross flat ~+0.02R; net -0.27R -> -0.08R is cost arithmetic only). V9 family is closed; do not tune it.

V10 LIVE MICROSTRUCTURE SNIPER (`btc_swing/v10/`, `config/btc_swing_v10.yaml`,
`docs/BTC_V10_LIVE_MICROSTRUCTURE_DESIGN.md`, data `<BTC_DATA_DIR>/btc/forward_v10/` (observation) and
`forward_v10_verify/` (pre-freeze shadow verification), reports `reports/forward_v10/`, freeze
`manifests/v10_forward_freeze.json`, prefixes `V10D-`/`V10S-`/`V10E-`): FORWARD-ONLY. No historical backtest
exists and none may be fabricated; every statistic starts at the freeze's observation_start. Own public WebSocket
feed + own immutable raw store (trades, allLiquidation, orderbook.50, tickers; audit in design section 1:
allLiquidation `S=Buy` = a LONG was liquidated, verified empirically), 1-second features, three families
A (liquidation absorption reversal) / B (flow absorption at a level) / C (liquidity break continuation) as
IDLE->ARMED->TRIGGERED->EXECUTED machines, immediate decision, shadow fills, Demo executor refused until the
owner's `STRATEGY_DEMO_V10_ACTIVATED` event on the authoritative Mac (gates include V5/V5.1 inactive + flat,
Demo flat, no open orders, lease held, freeze, tests, shadow report PASSED). Shadow and Demo execution
counters are separate (shadow cap 5, Demo cap 10 per rolling 24 h); the freeze pins the strategy sections only. CLI `btc-swing v10 forward
run|status [--live]|diagnostic|shadow-report|freeze|report|verify-raw`, `v10 demo status|activate|deactivate|
reconcile-ack`; macOS agent `com.btcswing.v10-forward` (`deploy/macos/install_v10_macos.sh`). Never modify
V5/V5.1 for V10; never tune V10 rules during the first observation period; never activate from the cloud.

V11 FAST MULTI-FACTOR INTRADAY (`btc_swing/v11/`, `config/btc_swing_v11.yaml` strategy hash `a324dbcb0394…`,
`docs/BTC_V11_FAST_MULTIFACTOR_DESIGN.md`, `reports/BTC_V11_FAST_MULTIFACTOR_BACKTEST.md/.json`, tables
`reports/v11/`, forward `reports/forward_v11/`, freeze `manifests/v11_forward_freeze.json`, data
`<BTC_DATA_DIR>/btc/forward_v11/` and `forward_v11_verify/`, prefix `V11S-`; `V11D-` reserved, unused):
RESEARCH + SHADOW ONLY. 0-100 multi-factor score (weights trend 20 / momentum 15 / volatility 10 / volume 15 /
location 15 / derivatives 10 / microstructure 15, renormalised over available groups; candidate 65 / armed 70 /
entry 75), families A trend pullback / B breakout-expansion / C sweep reversal, structural stop + ATR buffer
(floor 0.35%, cap 1.00% -> `STOP_TOO_WIDE_FOR_V11`), TP1 +0.6R 40% / TP2 +1.2R 40% / runner trail, break-even
after TP1, 20-min stall review, 60-min hard stop, risk 0.15% = 7.5 USDT, leverage ladder [1,2,3,5]. ONE
historical experiment (TRAIN 2022-01..2024-06, VALIDATION 2024-07..2025-06, loads capped at 2025-07-01):
classification C — ~2.7 trades/day, net -0.38R / -0.42R, PF 0.22 / 0.19, gross ~0, costs 0.37R; do NOT tune.
Shadow runner reuses the V10 feed/feature classes as a library only (own instance, own raw root, 22-day
rolling frame, microstructure for the last row only); it never imports V10 execution/activation/paths, never
reads V10 state or the V10 Demo counter, never claims authority, and has no exchange client. CLI `btc-swing v11
config|research|render`, `v11 forward run [--shadow-verification]|status|diagnostic|shadow-report|freeze|trades`;
macOS agent `com.btcswing.v11-forward` (`deploy/macos/install_v11_macos.sh`, installs ONLY the V11 agent).
Never modify V10 (or V5/V5.1) for V11; never activate any V11 Demo execution (none exists).

V11.1 COST-AWARE HIGH-CONVICTION INTRADAY (`btc_swing/v11_1/`, `config/btc_swing_v11_1.yaml` strategy hash
`b1c55b9b968d…`, `docs/BTC_V11_1_COST_AWARE_INTRADAY_DESIGN.md` pre-registered before the run,
`reports/BTC_V11_1_COST_AWARE_INTRADAY_BACKTEST.md/.json`, tables `reports/v11_1/`, prefix `V111S-`): RESEARCH
ONLY, a subclass of the V11 engine (V11 imported read-only; V11 files, freeze and journals untouched). H1 entry
quality (>= 4 independent directional groups with sub-score >= 0.60 and 0.20 margin, no trend contradiction,
tightened family rules, failed-breakout rejection) + H2 stop geometry (0.55% floor, 1.00% cap ->
`STOP_TOO_WIDE_V11_1`, cost filter 14 bps / stop > 0.25R -> `COST_TOO_HIGH`); exits, risk, costs as V11. ONE run
on the V11 windows: classification C — VALIDATION 36 trades (0.10/day), gross +0.003R, cost 0.204R, net -0.201R,
PF 0.36; TRAIN 64 trades (0.07/day), net -0.240R, PF 0.40; families B and C never passed the gates. Cost/R
halved, gross edge did not appear. No shadow runner, no V11.2, no tuning. CLI `btc-swing v111 config|research|render`.

V11-WARM WARM-STARTED V11 SHADOW (`btc_swing/v11_warm/`, `config/btc_swing_v11_warm.yaml`, `docs/BTC_V11_WARM_START.md`,
freeze `manifests/v11_warm_forward_freeze.json`, bootstrap `manifests/v11_warm_bootstrap_1m.parquet` (30 days of exact
Bybit 1m bars 2026-09-13..2026-10-12, content hash `3132d3410b54…`), data `<BTC_DATA_DIR>/btc/forward_v11_warm/` and
`forward_v11_warm_verify/`, reports `reports/forward_v11_warm/`): NOT a strategy; the frozen V11 strategy object
(every load asserts the V11 strategy hash equals the V11 freeze) run by a subclass of the V11 live scorer with a
31-day rolling frame seeded from the bootstrap bars, open-time bar detection, seam healing (every missing minute in
the window is filled from Bybit PUBLIC klines at startup and on reconnect holes; `src: kline_seamfill`) and a 30-min
live warm-up before observation_start. Gap-fill between the archive end and the seed instant uses Bybit PUBLIC klines (Mac only; price /
volume exact, taker split NaN, trade count placeholder 1). Never backfilled: order book, liquidations, live
microstructure; derivatives warm live. CLI `btc-swing v11w config|bootstrap|seed`, `v11w forward
run|status|diagnostic|verify-warm|shadow-report|freeze|trades`; macOS agent `com.btcswing.v11-warm-forward`
(`deploy/macos/install_v11_warm_macos.sh`, installs ONLY this agent). Known V11 finding (reported, NOT changed): the
V11 live loop detects new bars by deque length and will stop scoring once 22 days of live bars fill its deque.
Never modify V10 / V11 for V11-WARM; no Demo execution exists.

FORWARD FULL-HISTORY REPORTS (`btc_swing/forward_report/`, generic and READ-ONLY): `btc-swing v10|v11|v11w report
full-history [--verification]` reads a generation's journals / state / freeze through a `GenerationSpec`
(`forward_report/generations.py`) and writes only `reports/<generation>/full_history_*.{json,md}` +
`latest_full_history.*`. Fields a generation does not journal print N/A (never inferred); V10 carries the Demo
section, V11 / V11-WARM are shadow only. Never add writes to journals, state, freezes or runners in this layer;
`tests/forward_report` proves journal hashes, state.json, freezes and strategy hashes are unchanged and no exchange
call is made.

V11.2 COUNTERFACTUAL GATING STUDY (`btc_swing/v11_2/`, research only, owner-approved multi-variant search on consumed
TRAIN / VALIDATION; `reports/BTC_V11_2_COUNTERFACTUAL_GATING_STUDY.md`, `reports/v11_2/study.json`): `GatingEngine`
subclasses the frozen V11 engine and overrides one family gate per variant (A1 struct15 side-or-neutral, A2/A3 aligned
within 2/3 completed 15m bars, B1-B3 near-boundary 0.40/0.50/0.70 ATR5, C0 C-only, C1 C-only without score gates as a
diagnostic) plus combos A1+B1, A1+B3, A3+B1. Result: no variant has gross > 0, net > 0 or PF > 1 in either window
(net -0.36..-0.42R; gross -0.05..+0.03R; cost ~0.36-0.38R); A1 raises frequency to 3.9 / 3.5 per day at net -0.36 /
-0.39R; C structure coincides with score >= 70 in 0.07% of its minutes and has no standalone edge. Selection-biased by
construction; nothing frozen, no runner, nothing adopted. Note: the committed V11 backtest report predates the
post-TP1 stop-leg label fix (1 bp instead of 3 bp slippage on those exits): the current frozen engine's net is ~0.008R
lower than that report; classification C unchanged.

V11-LITE-WARM (`btc_swing/v11_lite_warm/`, `config/btc_swing_v11_lite_warm.yaml`, `docs/BTC_V11_LITE_WARM_DESIGN.md`
pre-registered, freeze `manifests/v11_lite_warm_forward_freeze.json`, data `<BTC_DATA_DIR>/btc/forward_v11_lite_warm/` and
`forward_v11_lite_warm_verify/`, reports `reports/forward_v11_lite_warm/`, prefix `V11LWS-`): SHADOW ONLY challenger to the
V11-WARM control. ONE strategy delta: `V11LiteEngine.structure()` returns data validity only, so the A/B/C structure never
gates arming; the frozen `V11Engine.structure` results are journaled as `structure_labels`. Bookkeeping `v11lw-book-2`
(pre-launch cleanup, replay-proven economically identical to f885f85 on synthetic, adversarial and the full TRAIN/VALIDATION
frames): ONE arm state per side (arm start only on NOT_ARMED -> ARMED, `arm_episode_id`), at most one ARMED / NEAR_MISS /
decision per side-minute, the first frozen trigger/stop template that fires is `selected_template` (NOT a setup; no LITE
journal has a `family` field); f885f85 engine kept verbatim as `tests/v11_lite_warm/legacy_engine_f885f85.py`; the
f885f85 freeze is kept as `manifests/v11_lite_warm_forward_freeze_superseded_f885f85.json` (never ran on the Mac).
Everything else is V11's own frozen object
(V11 strategy hash `a324dbcb0394…` asserted at load; outcome horizons [5,15,30,60,120] are not a strategy section). Read-only
context telemetry (Fibonacci of the last confirmed 5m swing leg, VWAP, 1h/4h range, swings, location, flow, microstructure,
V11 structure sub-conditions) on every journaled event; an in-process strict V11 twin (`journals/control_twin.jsonl`)
attributes each LITE signal (`control_twin.reason`, `incremental`). `btc-swing v11lw report compare` (read-only, writes only
under `reports/forward_v11_lite_warm/`) = control vs challenger, INCREMENTAL COHORT (main experiment), Fibonacci buckets
(PRELIMINARY < 30 samples), mechanical checkpoints (1: 7 days or 300 candidates; 2: 30 trades or 21 days -> gross R > 0 and
incremental 90% upper CI > 0; 3: 60 trades or 45 days -> net R > 0, PF > 1.1, DD <= 15R, not one-sided / one-day). CLI
`btc-swing v11lw config|seed`, `v11lw forward run|status|diagnostic|verify-warm|shadow-report|freeze|trades`, `v11lw report
full-history|compare`; macOS agent `com.btcswing.v11-lite-warm-forward` (`deploy/macos/install_v11_lite_warm_macos.sh`).
Never modify V10 / V11 / V11-WARM for it; never tune it forward.
V11-LITE BYBIT DEMO EXECUTION (`btc_swing/v11_lite_warm/demo/`, `config/btc_swing_v11_lite_warm_demo.yaml`,
`docs/V11_LITE_WARM_DEMO_EXECUTION.md`, manifest `manifests/v11_lite_warm_demo_execution_freeze.json`, prefix `V11LWD-`,
state/journal `<BTC_DATA_DIR>/btc/forward_v11_lite_warm/demo/`): execution layer only (strategy hash unchanged); the shadow
journals first and stays the source of truth; Demo gets copies via `V11LiteWarmLive.demo_sink` on its own threads and
can never change / delay / block a shadow decision. 5 USDT risk, leverage 1, 10 / 24 h, -25 USDT daily stop, halt after 3
full losses, reconciliation on mismatch; default ISOLATED account (`BYBIT_DEMO_V11LW_API_KEY/SECRET`, separate Bybit user
id proven at activation); shared V10 account only with `--shared-account` + the V10 conflict gate. CLI `btc-swing v11lw
demo status|freeze|activate|deactivate|reconcile-ack|resume|report`. Never activate it from the cloud or for the owner;
shadow modules (top level of `v11_lite_warm/`) stay exchange-free (tested).

INTRADAY PORTFOLIO RESEARCH E1 / E2 / E4 (`btc_swing/research/intraday_portfolio/`, CLI `btc-swing ipr inventory|research`,
outputs `reports/intraday_portfolio_research/<RUN_ID>/`, run `20261019T114233Z`): ISOLATED, ORDER-DISABLED research harness
(no exchange client, no order type, imports none of V5/V5.1/V10/V11/V11-WARM/V11-LITE; tested). Phase 0 data inventory
(AVAILABLE / PARTIAL / MISSING, never fabricated), common data contract (exchange / receipt / processing / decision /
benchmark-entry / executable-entry times; every input carries provenance; missing flow is never zero; a missing BBO is never a
mid fill), parent-episode model (one move = one parent, repeated triggers recorded, rejected candidates kept), frozen
primitives (completed UTC 5m bars, Wilder ATR20, trade-weighted VWAP, frozen 1h box, 4h efficiency ratio ER <= 0.25 range /
> 0.25 directional). E1 Accepted Range Expansion and E2 Failed Auction run on the Bybit trade-built 1m/5m bars
2021-11-15..2025-06-30 in the BENCHMARK ledger only (entry = open of the first 1m bar after the decision close; cost
scenarios 14/16/20/24 bps taker/taker; Binance funding as labelled proxy); EXECUTABLE replay is UNAVAILABLE (no historical
BBO / receipt time). E4 Forced Flow is an event study on the local receipt-timed raw stores (~17 h, 0 complete days; the
lagged same-hour 99th-percentile threshold needs 30 complete days -> THRESHOLD UNAVAILABLE, nothing detected).
Result of run `20261019T114233Z`: E1 REJECT (13080 parents, 166 trades, 0.125/day, gross -6.9 bps, net -22.9 bps @16,
PF 0.34), E2 REJECT (4734 parents, 134 trades, 0.101/day, gross +8.1 bps, net -7.9 bps @16, PF 0.68), E4 INCONCLUSIVE
(insufficient raw history). One-position portfolio E1+E2: 300 trades, net -16.2 bps, PF 0.39. Nothing is eligible for
runner engineering; no parameter was tuned; the one recorded INTERPRETATION (a setup rejected before entry ends its
episode; the same frozen box never spawns a second parent) is in the manifest. Locked holdout never read. The V11
seven-group score is RETIRED for new research; V10 stays untouched (no V10.1).

N1 / N2 / N4 PREREGISTERED SCREENS (same package; `btc-swing ipr ingest-spot|research-n|e4-audit`; run
`reports/intraday_portfolio_research/20261019T180134Z/`, SUMMARY.md + `experiment_manifest_n1_n2_n4.json` whose
preregistration block (rules, embargo, episode rule, costs, gates, kill criteria) is hashed BEFORE any outcome):
N1 Participation Impulse Continuation (5m |ret| >= lagged 30-day p95, close beyond the preceding completed 30m
range, 1-min embargo, 30m primary / 60m diagnostic), N2 Persistent Spot-Flow Continuation (15m signed quote flow
2*taker_buy_quote - total_quote from three agreeing 5m bars, lagged top decile; SPOT = Binance spot 5m klines
ingested with published sha256 into `data/btc/datasets/binance_spot_klines/` + `manifests/binance_spot_klines_5m_manifest.jsonl`,
PERP comparison = Binance perp klines; matched price/volume controls; single spot venue -> exploratory), N4
Session-Transition Continuation (Tokyo 09:00 / London 08:00 / New York 09:30 local via zoneinfo, weekdays, first
completed 15m return with opening volume > median of the previous 30 sessions, 45 min, POOLED primary). Two
verdicts per engine on 2021-11-15..2025-06-30 (benchmark ledger, taker/taker): N1 EDGE REJECT / CADENCE PLAUSIBLE CORE
(7310 parents, 5.52/day, gross -0.3 bps, net -16.3 @16); N2S EDGE REJECT / CADENCE PLAUSIBLE CORE (6140 parents,
4.64/day, gross +1.6 bps, net -14.4; +5.1 bps over matched controls = 1-5 bps kill); N4 EDGE REJECT / CADENCE
PLAUSIBLE CORE (1387 parents, 1.05/day, gross -0.4 bps, net -16.4; only New York positive). Union of signals:
8.2 available/day but 3197 of 10899 profitable at 16 bps. E4 collection audit (`e4_collection_audit.json`, read-only):
Bybit trades / liquidations / depth / sequence fields / raw / schema preserved; MISSING monotonic receipt clock,
per-row connection id, Binance perp feed, any spot venue, funding settlements, contract metadata; PARTIAL gap /
clock telemetry, mark / index / OI / funding estimate (tickers deltas only). Nothing implemented for the collector;
no V10.1; E1/E2 untouched; nothing runner-ready.

## Non-negotiables (owner's specification)
- No live trading, no exchange API keys, no order placement, no real money.
  Owner-approved exception (2026-10-08): Bybit DEMO execution validation (`btc_swing/v5/demo/`,
  `docs/V5_DEMO_EXECUTION.md`): demo endpoint only (enforced), demo keys only from
  `BYBIT_DEMO_API_KEY`/`BYBIT_DEMO_API_SECRET`, default mode DISABLED, STRATEGY_DEMO only by the
  owner after a PASSED smoke report. Never production endpoints or keys; never print secrets.
- No parameter search/optimisation; defaults are pre-registered. A rule change needs owner approval
  and a single pre-registered run.
- PIT discipline: a decision at T may only use bars with `close_time <= T` and auxiliary rows with
  `time + latency <= T`. Raw archive files are immutable (sha256 verified).
- 2025-01..2026-09 was the single confirmatory holdout (Phase 3) and is now spent; it must not be
  used for development, and any new hypothesis needs a new owner pre-registration and a new window.
- Hard limits in the config schema: `max_leverage <= 10`, `risk_per_trade <= 2%`.

## Environment
- Python 3.12+, `uv sync --all-extras`. No database. `BTC_DATA_DIR` (default ./data, git-ignored).
- Network must allow `data.binance.vision`, `s3-ap-northeast-1.amazonaws.com` (archive listing) and
  `stream.bybit.com` (V5 public WebSocket collector).
  Binance REST (`api/fapi.binance.com`) is geo-blocked from the cloud container and is not used.
- Checks before any commit: `bash scripts/check.sh` (ruff format/check, strict mypy, pytest).
- Work on branch `main` unless told otherwise.

## Layout
See README.md. Config `config/btc_swing.default.yaml` (V1, frozen) and `config/btc_swing_v2.default.yaml`
(V2 protocol), `config/btc_swing_v3.yaml` (V3, frozen), `config/btc_swing_v4.yaml` (V4, frozen),
`config/btc_swing_v5.yaml` (V5, frozen); CLI
`btc-swing` (`data probe`, `data ingest`, `backtest`, `phase2`, `phase21`, `phase22`, `phase23`,
`phase24`, `phase3-freeze`, `phase3`, `v2 research`, `v3 research`, `v4 research`, `v5 ingest`,
`v5 collect`, `v5 research`, `v5 forward freeze|seed|run|cycle|status|report`); tests `tests/` (+ `tests/v3/`, `tests/v4/`, `tests/v5/`); reproducibility manifests in
`manifests/`. Never modify a closed generation's modules, configs or reports for later work.
