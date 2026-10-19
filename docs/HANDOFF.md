# Handoff — state of work and next steps (2026-10-13, V6..V9 closed; V10 ACTIVE on Bybit Demo on the Mac (sacrosanct); V11 frozen cold-start shadow benchmark; V11.1: C; V11-WARM warm-started V11 shadow frozen, awaiting Mac install; locked holdout unspent)

## Where things stand
- Migrated from the `jack-app` repository into this standalone repository; no dependency on the
  US-equity codebase remains (generic utilities live in `btc_swing/core`).
- Phase 1 (foundation) and Phase 2 (eight setup families, PIT auxiliary features, mark-price
  liquidation, forward labels, null benchmark, leverage comparison, first validation) are done.
- Validation with FROZEN pre-registered defaults, 2022-01..2023-12 development and 2024
  chronological validation: `reports/BTC_SWING_V1_PHASE2_VALIDATION.md`. Verdict: repeatable positive
  expectancy NOT demonstrated (combined +0.08R net, t=0.9; 2022-23 +0.13R, 2024 +0.01R; costs take
  ~64% of the gross edge; never-triggered plans out-ran traded ones). Result hash
  `92ebe5d7fc65...` (see `manifests/phase2_validation_run_manifest.json`).
- Phase 2.1 (ENTRY MECHANICS, one pre-registered hypothesis) done: CONTROL (frozen Phase 2, result
  hash verified identical) vs ZONE_ENTRY (enter at the pre-defined zone without the 15m/5m
  confirmation). Report: `reports/BTC_SWING_V1_PHASE2_1_ENTRY_MECHANICS.md`. Verdict: PARTIALLY
  SUPPORTED, not adoptable: net expectancy fell (+0.08R -> +0.03R) and drawdown rose; the timing
  effect on the 198 shared episodes is positive (+0.09R paired) and 73 never-triggered plans were
  recovered at +0.78R, but 70 plans that CONTROL filtered out as INVALIDATED were entered at
  -1.11R each. The confirmation trigger acts mainly as a filter, not a timing delay.
- Phase 2.2 (EARLY ZONE ENTRY + CONFIRMATION-BASED EARLY EXIT, one pre-registered hypothesis) done:
  variant `ZONE_ENTRY_CONFIRM_EXIT` (zone entry, then the frozen 15m confirmation as an early-exit
  filter within the existing 24-bar timeout). Report:
  `reports/BTC_SWING_V1_PHASE2_2_EARLY_ENTRY_CONFIRMATION_EXIT.md`. Verdict: NOT SUPPORTED (net
  -0.06R vs CONTROL +0.08R, max DD 23%): the invalidation-level exit saves little because the level
  sits 0.5 ATR above the stop, and the deadline exit truncates the recovered never-triggered winners
  (80 of 82 never "confirm" because the predicate needs price near the zone). Entry mechanics are
  exhausted as a hypothesis family.
- Phase 2.3 (POST-TP1 EXIT DESIGN, one pre-registered hypothesis) done: variant
  `STRUCTURAL_TRAIL_AFTER_TP1` = `exits.breakeven_after_tp1: false`, everything else frozen.
  Report: `reports/BTC_SWING_V1_PHASE2_3_POST_TP1_EXIT.md`. Verdict: NOT SUPPORTED (net +0.063R vs
  +0.081R, 2024 slightly worse, PF 1.10 vs 1.14, MFE capture 35% vs 37%). Of the 28 CONTROL trades
  stopped at breakeven, only 6 later resumed to 2R while 22 would have hit the initial stop first;
  the paired exit-rule effect is -0.016R per TP1 trade. The breakeven move protects more than the
  structural trail recovers.
- Phase 2.4 (SHORT REGIME ELIGIBILITY, the final planned Phase 2 hypothesis) done: variant
  `NO_NEW_SHORT_IN_TREND_DOWN` = `experiment.block_short_in_trend_down: true` (a SHORT trigger firing
  while the PIT regime is TREND_DOWN does not open a trade; uniform across SHORT families; nothing
  else changed). Report: `reports/BTC_SWING_V1_PHASE2_4_SHORT_REGIME.md`. Classification:
  A — ADOPTABLE FOR UNTOUCHED VALIDATION: net +0.184R vs +0.081R, PF 1.32 vs 1.14, max DD 6.5% vs
  9.0%, 2024 +0.061R vs +0.007R, SHORT +0.31R vs -0.01R, LONG identical (158 trades, same R); the
  60 removed TREND_DOWN shorts were a coherent -0.19R population (34 losers / 26 winners, worst-3
  share 10%). Caveats: the hypothesis came from the Phase 2 regime table on the same window (in-
  sample confirmation, not out-of-sample evidence); removed-population t = -1.24; remaining 2024
  SHORT still -0.19R on 21 trades; criterion 2 met narrowly (+0.054R vs +0.05R threshold).
- Phase 3 (UNTOUCHED CONFIRMATORY VALIDATION, owner-approved, one run per arm) done:
  `reports/BTC_SWING_V1_PHASE3_UNTOUCHED_VALIDATION.md`. Holdout 2025-01-01 -> 2026-10-01 (first
  time any 2025+ file was downloaded; freeze manifest `manifests/phase3_freeze_manifest.json`
  committed before the run; both arms re-reproduce the Phase 2 / Phase 2.4 hashes on 2022-2024).
  Classification: C — FAILED OUT-OF-SAMPLE. APPROVED_VARIANT (`block_short_in_trend_down: true`)
  net -0.066R, PF 0.90, net P&L -426 USDT on 121 trades; CONTROL -0.104R, PF 0.85, -973 USDT on
  180 trades. The SHORT regime rule still helped relative to CONTROL (57 blocked TREND_DOWN shorts
  at -0.27R, max DD 7.0% vs 11.3%, criteria 3/4/5/7 met) but the strategy as frozen is net
  negative out of sample (criteria 1/2 fail) and its 2026 YTD segment is -0.15R; LONG families are
  the main holdout drag (-0.07R on 88 trades, identical in both arms). Pre-declared criteria were
  not moved; no parameter tweak is proposed.
- V1 is CLOSED by the owner: FAILED OUT-OF-SAMPLE AS A TRADING STRATEGY, SUCCESSFUL AS RESEARCH
  INFRASTRUCTURE. V1 code, configs, reports and manifests are immutable (reproducible via the
  hashes in `manifests/`). All V1 experiment switches stay at their frozen defaults.
- BTC Swing V2 (COST-AWARE LEARNED OPPORTUNITY RANKING) research generation done:
  `docs/BTC_SWING_V2_DESIGN.md` (pre-declared protocol), package `btc_swing/v2/`, CLI
  `btc-swing v2 research`, report `reports/BTC_SWING_V2_RANKING_RESEARCH.md`, run manifest
  `manifests/v2_ranking_research_run_manifest.json`. V1 setups were used as candidate generators
  (838 candidates 2022-01..2026-09, one lifecycle per family, no slot), labelled with the frozen V1
  execution engine (net R after fees/slippage/funding), 78 PIT features (`v2-fs-1`), quarterly
  walk-forward (15 folds, 721 predictions from 2023-01), logistic / ridge / gated boosting.
  Classification: C — NO USEFUL RANKING EDGE. Walk-forward Spearman(score, net R) = -0.02, AUC
  0.48, deciles non-monotone, the PIT top-25% slice -0.21R (worse than all candidates -0.03R and
  both nulls), ablation without derivatives no better, gradient boosting not fitted (gate failed).
  Only criterion 5 (frequency) was met. The candidate population itself is net negative from 2025
  (-0.13R / -0.19R per year) and costs (0.10R per trade) exceed its gross edge; the unranked
  NO_NEW_SHORT_IN_TREND_DOWN hard block (+0.02R) remains the only thing that helped.
- 2025-01..2026-09 was inspected in V1 Phase 3 and used chronologically in V2; it is NOT an
  untouched holdout for anything any more. No live or paper trading exists.
- V1 and V2 are CLOSED research generations (immutable). V3 (ACTIVE MULTI-TIMEFRAME SWING,
  `btc_swing/v3/`, `config/btc_swing_v3.yaml`, `docs/BTC_SWING_V3_DESIGN.md` committed before any
  result, CLI `btc-swing v3 research`, report `reports/BTC_SWING_V3_ACTIVE_SWING_RESEARCH.md`,
  manifest `manifests/v3_active_swing_run_manifest.json`) done: one pre-registered run on
  2022-01..2026-09. Classification: C — NO ROBUST STRUCTURAL EDGE. Frequency and holds hit the
  target (1.53 trades/day, median hold 3.2 h) but net expectancy is -0.35R on 2659 trades
  (gross -0.02R, cost drag 0.34R per trade because stops average 0.66% of price against ~17 bps
  round-trip costs), PF 0.50, every year and every qualifying quarter negative, every family
  negative net, nulls with the same geometry lose almost as much (timing adds nothing), and the
  research account is ruined. Only criterion 6 (frequency/hold) was met. Deterministic rerun,
  truncation audit and resampling oracle all pass.
- Lessons recorded for any future generation (new pre-registration required): at this holding
  horizon the stop geometry, not the signal, decides survivability; a design needs either much
  wider structural stops (lower frequency) or far cheaper execution; the LIQUIDITY_SWEEP family
  dominated the candidate stream (61% of episodes) with the tightest stops.
- V4 (EVENT & POSITIONING DRIVEN ACTIVE SWING, `btc_swing/v4/`, `config/btc_swing_v4.yaml`,
  `docs/BTC_SWING_V4_DESIGN.md` frozen at commit 56f7177 before any code, CLI `btc-swing v4
  research`, report `reports/BTC_SWING_V4_EVENT_POSITIONING_RESEARCH.md`, manifest
  `manifests/v4_event_positioning_run_manifest.json`) done: two-stage research on 2022-01..2026-09.
  Classification: C — NO ROBUST EVENT EDGE. Stage A: 962 events (0.56/day); pooled signed forward
  return ~0 at 4 h / 8 h (gate failed); DELEVERAGING_REVERSAL events CONTINUE rather than reverse
  (8 h -0.28%, t -2.3); POSITIONING_RESET ~0; PARTICIPATION_BREAKOUT LONG is the one sub-population
  with signal (51 events, 8 h +1.0%, t 3.1; 29 trades gross +0.46R / net +0.31R, PF 1.63) but it
  is too small and was not pre-selected, so it cannot be claimed. Stage B: 176 trades
  (0.10/day, median hold 22.8 h), gross +0.016R, net -0.095R, PF 0.81, max DD 5.5%, SHORT -0.38R,
  nulls with the same geometry equal the strategy. The stop fix worked: median stop 1.54% of
  price (V3 0.66%), cost 11% of stop, drag 0.07-0.11R. Only criterion 6 (drawdown) was met.
  Deterministic rerun, truncation audit (decisions and events) and resampling oracle pass.
- V5 (MICROSTRUCTURE & LIQUIDATION DRIVEN ACTIVE SWING, `btc_swing/v5/`, `config/btc_swing_v5.yaml`,
  `docs/BTC_SWING_V5_DESIGN.md` frozen at commit 4e9412a before any feature or result, CLI
  `btc-swing v5 ingest|collect|research`, report `reports/BTC_SWING_V5_MICROSTRUCTURE_RESEARCH.md`,
  manifests `manifests/v5_microstructure_run_manifest.json` and
  `manifests/v5_bybit_collector_verification.json`) done: data audit first (aggTrades with aggressor
  flag 2019-12 -> present, ingested as checksum-verified 5m flow aggregates; bookDepth from
  2023-01-01, diagnostics only; NO liquidation history exists for BTCUSDT UM, so family A is a
  labelled OI-flush proxy and liquidations are collected forward only), 5m PIT feature frame
  (trade flow / CVD / OI / basis / funding / premium / price), four pre-registered event families,
  Stage A per-family gate, one Stage B run. Classification: C — NO ROBUST MICROSTRUCTURE EDGE.
  Stage A: 8981 events (5.2/day, 2378 first-in-cluster); no family passed the gate (C
  FLOW_OI_CONTINUATION is the closest: 1527 first-in-cluster events, +0.03% at 1 h / +0.06% at 4 h,
  t 1.6-1.7, positive in 4-5 of 5 years, but the 4 h strength terciles are not monotone and the
  pooled flow/OI effect is ~0.1 x the hourly dispersion). Stage B: 1210 trades (0.70/day, median
  hold 13 h), gross +0.115R, net -0.014R, PF 0.97, max DD 14.5%, net positive in 1 of 5 years;
  stops 1.40% of price (cost 12% of stop, drag 0.13R); beats both nulls (the only criterion met)
  because random entries with the same geometry lose -0.13R. The Bybit public collector was verified
  (900 s run with forced reconnect + 120 s resume: 45 k messages, 0 sequence gaps, 0 duplicates,
  0 hash mismatches, latency p50 41 ms); the Bybit Demo execution abstraction exists but is NOT
  activated (no credentials, every network method raises). Execution note: the frozen configuration
  was executed three times for technical reasons (an order-book loader de-duplication bug left the
  diagnostic book columns empty in the first execution; the second was stopped for a NaN-aware
  display fix); the event and trade streams are identical across executions and no rule changed.
  The published manifest records code `5ff237d6c94f-dirty` because those two fixes (committed as
  f78c949) were in the working tree during the final execution; the null-table footnote in the
  report and two 12 h strength-tercile cells of family C (NaN-propagated means recomputed as
  NaN-aware means from the persisted `events.parquet`) were edited in the generated markdown by hand
  (cosmetic, disclosed here; the code fix is committed for reproduction).
- V5 FORWARD OBSERVATION MODE (`btc_swing/v5/forward/`, `config/btc_swing_v5_forward.yaml`,
  `docs/V5_FORWARD_OBSERVATION_RUNBOOK.md`, freeze `manifests/v5_forward_freeze.json`, CLI
  `btc-swing v5 forward freeze|seed|run|cycle|status|report`, snapshots `reports/forward/`):
  the frozen V5 strategy is recorded prospectively on live Bybit public data (trades, book,
  tickers, liquidations, klines) with a virtual paper ledger; observation start 2026-10-07
  14:59:22 UTC; seed = 46 Bybit archive days (2026-08-22..2026-10-06). OI/funding/basis z-scores
  warm up forward (10 days / 30 days), so families A, C and D cannot fire before ~2026-10-17.
  No orders, no credentials. The runner must live on a persistent host: deployment package in
  `deploy/` (systemd service with restart/graceful stop, health timer + alert hook, logrotate,
  journald), restart-safe cold migration (`v5 forward export|verify|integrity`, scripts
  `deploy/migrate_export.sh` / `deploy/migrate_import.sh`) documented in
  `docs/V5_FORWARD_DEPLOYMENT.md`; a local rehearsal (export, verify, cycle, integrity comparison)
  passed. The cloud-container runner died once when the session restarted (15:25-15:58 UTC on
  2026-10-07, carried as flagged gap bars) which is exactly why the persistent host is needed.
- V5 BYBIT DEMO EXECUTION (`btc_swing/v5/demo/`, `config/btc_swing_v5_demo.yaml` mode DISABLED,
  `docs/V5_DEMO_EXECUTION.md`): demo-only adapter (host allowlist, env-only credentials, fail
  closed), EXECUTION_SMOKE sequence, STRATEGY_DEMO executor (frozen triggers via an engine subclass,
  frozen sizing on a 100 USDT reference equity, deterministic order ids, write-ahead state,
  fail-safes, frozen exit management, restart recovery, paper-vs-demo reconciliation), hash-chained
  journals, demo status in `v5 forward status-text`. 26 offline tests against an in-process fake
  Bybit DEMO pass (incl. every restart scenario). The real smoke run from the cloud container
  (2026-10-08, runs 261008192245/261008192324) is BLOCKED by Bybit's country restriction at the first
  unauthenticated request: no signed request or order left the container. Open owner decisions:
  run the smoke on the persistent host; 100 USDT reference equity is below Bybit's 0.001 BTC minimum
  for nearly every V5 trade (~480 USDT needed at the median stop; ~1,700 USDT to place both TP legs).
  The forward runner in the container died again on 2026-10-07 16:17 UTC and was restarted
  2026-10-08 19:08 UTC (a 27 h gap in this copy of the observation).
- 2026-10-08 (later): owner set the demo reference equity to 2000 USDT and asked for the real DEMO
  validation on the persistent host. This session ran again in the ephemeral cloud container (no
  systemd, no deployed service, runner not running, Bybit still country-blocked), so the new
  read-only preflight (`btc-swing v5 demo preflight`, report `reports/forward/demo_preflight/261008194142`)
  stopped at the environment gate with 0 authenticated requests; the smoke command now refuses to
  run without a PASSED preflight. Freeze hash and observation start verified unchanged. The real
  validation must be run from a Claude Code session on the persistent host
  (`docs/V5_DEMO_EXECUTION.md` section 7). The container's forward runner was NOT restarted to avoid
  a second runner beside the persistent host's.
- 2026-10-08 (later still): the owner's Mac reached `api-demo.bybit.com` (HTTP 200, freeze and
  observation start correct) but the preflight was blocked only by the forward-runner checks, which
  do not apply to a machine used only for demo connectivity and the smoke test. The preflight is now
  split into `DEMO_EXECUTION_PREFLIGHT` (needed for EXECUTION_SMOKE) and `FORWARD_HOST_PREFLIGHT`
  (runner on this host, exactly one runner, systemd, freeze; needed additionally for STRATEGY_DEMO,
  re-checked live by `build_executor`, which fails closed while the observation continues). Next:
  on the Mac, `btc-swing v5 demo preflight`, then `btc-swing v5 demo smoke --mode EXECUTION_SMOKE`.
- 2026-10-08 (real DEMO smoke 261008210024 from the owner's Mac, report not in this repo): the
  execution lifecycle succeeded, but recovery failed on an execution-confirmation race: the entry
  order reported Filled before `/v5/execution/list` showed its execution, so fees were recorded as 0
  (own net -0.0171 vs Bybit closed PnL -0.1070431 = both fees missing). Fixed without weakening
  reconciliation: Filled is provisional; executions are awaited (private execution WebSocket preferred,
  bounded REST polling fallback), aggregated and de-duplicated by execId, and journaled as an
  append-only `EXECUTION_CONFIRMED` event; own net = gross - entry fees - exit fees vs Bybit closed
  PnL within 0.001 USDT; wallet change = USDT wallet delta (`docs/V5_DEMO_EXECUTION.md` section 4).
  Next: rerun the real smoke on the Mac (preflight, then `demo smoke --mode EXECUTION_SMOKE`).
- 2026-10-08 (night): real DEMO smoke PASSED on the owner's Mac. Owner decision: the Mac is the
  temporary SINGLE authoritative forward + STRATEGY_DEMO host until the Windows NUC (~3 days).
  Added: macOS launchd support in FORWARD_HOST_PREFLIGHT (LINUX+systemd or MACOS+launchd, managed pid
  = runner), single-runner flock, host-bound authority lease (`v5 forward claim-/release-authority`),
  `v5 forward coverage` (missing periods, never backfilled; archive warm-up never after the start
  day), `v5 forward host-status`, `v5 demo activate|deactivate` (immutable host-bound
  `STRATEGY_DEMO_ACTIVATED` after tests + both gates + smoke + freeze + integrity + reconciliation +
  sizing; pre-activation triggers refused), reference equity 5,000 USDT, `deploy/macos/` (LaunchAgents,
  caffeinate wrapper, 5-minute health, install/import/start/stop/migrate scripts), runbook
  `docs/V5_MAC_AUTHORITATIVE_HOST.md` incl. the Mac -> NUC/WSL cold migration (NOT run yet).
  The cloud collector's state (17 live bars; last bar 2026-10-08 19:30 UTC) was cold-exported with
  its authority released and published on branch `v5-forward-state-cloud-20261008` for the Mac.
  Activation itself must be done by the owner ON THE MAC; it was not (and cannot be) done from here.
- 2026-10-09: `btc-swing v5 forward signal-diagnostic` (read-only, zero side effects, verified by a
  data-tree fingerprint): per family/side every frozen Stage A condition (value, threshold, PASS/FAIL,
  distance, z warm-up), Stage B state from an in-memory replay, cooldowns, gap/demo/risk blockers,
  closest-to-trigger ranking; verdicts cross-checked against the frozen detector masks. Finding on
  the cloud copy (last bar 2026-10-08 19:30Z): families A/C/D cannot fire until `oi_chg_1h_z` has
  2880 observations (OI exists only forward: ~10 days of collection); families A/B cannot fire while
  the frozen 30-day `vol_1h_z` window contains the carried-forward outage rows (1h volume ~1e-12
  from rolling-sum residue -> log ~ -27 -> window std x9.7 -> z>=1 needs ~750k BTC/h) until those
  rows leave the window (2026-11-07 19:05Z on that copy). This is a frozen-feature behaviour under
  outages; changing it would be a V5 rule change (owner decision + pre-registration), not done.
- 2026-10-09 (later): BTC_V5_1_DATA_QUALITY_FIX implemented and pushed (`btc_swing/v51/`,
  `docs/BTC_SWING_V5_1_DATA_QUALITY_FIX.md`). V5 untouched and still running. V5.1: frozen V5 strategy
  sections byte-identical (hash `144f7d58bb71…`), gap rows never observations, clean-window
  features, clean-bar Wilder ATR, Bybit PUBLIC history seeds (OI 5min, mark/index klines, funding
  history; immutable pages with retrieved_at; warm-up strictly before the V5 start; alignment
  verified vs live rows), own freeze/journals/demo (`V51D-`), read-only V5.1 diagnostic with the
  FEATURE | VALID OBS | WARM | CURRENT | Z | QUALITY table and a V5-vs-V5.1 comparison, owner-gated
  activation (`v51 demo activate`: all V5 gates + V5.1 data-quality gates + V5 demo inactive/flat).
  28 new tests (190 total). On the cloud copy: V5.1 vol_1h_z baseline clean (z>=1 needs ~3.8k
  BTC/h instead of ~748k), current window correctly INVALID NOW (stale copy ends 5 bars after an
  outage), OI 0/2880 because Bybit REST is geo-blocked here: the OI seed, the V5.1 diagnostic on
  live data, the V5.1 freeze and (later) the activation must be run by the owner ON THE MAC. NOT
  activated. Next: owner runs `v51 seed oi`, `v51 forward signal-diagnostic`, `compare-diagnostic`,
  then decides on `v51 forward freeze` and, after every gate passes, `v51 demo activate`.
- 2026-10-10/12: V6 ACTIVE INTRADAY. Phase 1 (`docs/BTC_V6_ACTIVE_INTRADAY_DESIGN.md`, commit 2c4e51a):
  data audit (no liquidation history anywhere; no untouched historical window), why V5 is quiet (hold
  time, 30-day z extremes, swing geometry, costs), families A-F, pre-declared grids/exits/costs/windows,
  acceptance criteria. Owner then made trade frequency a HARD requirement (>= 2 executed trades per
  calendar day on the holdout, target 3-6 on active days, plus net edge > 0, PF > 1, DD acceptable) and
  said proceed; the four open decisions were applied as the design defaults (recorded in section 9).
  Phase 2-4 (`btc_swing/v6/`: strict config, PIT 5m frame with V5.1 data-quality rules and
  same-time-of-day baselines, families A/B/C/F as vectorised Stage A + per-episode anti-chase Stage B,
  chronological paper engine with the frozen sizing ladder, Bybit-like costs, four exit variants,
  one-position rule with refusal counterfactuals and `sequential`/`independent` diagnostic modes,
  calendar-day frequency report, folds, regime/hour-matched nulls, criteria/gate, report; 32 tests)
  ran development 2022-01..2024-06 and validation 2024-07..2025-06 with the pre-registered protocol
  (exit variant chosen on development by the declared rule = B without breakeven; no variant reached
  2 trades/day on development, max 1.58). Result `reports/BTC_SWING_V6_ACTIVE_INTRADAY_RESEARCH.md`,
  tables `reports/v6/`: **C — V6 ACTIVE-INTRADAY OBJECTIVE NOT ACHIEVED.** Validation: 654 trades,
  1.79 per calendar day (10.7% zero days, 23% of days 3-6, max 5), median hold 165 min, gross +0.044R,
  fees -0.269R, slippage -0.077R, net -0.303R (95% CI -0.40..-0.21), PF 0.55, win 38%, max DD 31% of
  the 5,000 USDT reference at 0.15% risk; every family negative on development AND validation
  (momentum -0.39R, failed-breakout -0.23R, pullback -0.16R, flow/OI -0.14R); STOP exits -1.24R
  carry the loss, TRAIL +0.40R and TIME_LIMIT +1.42R are positive. Bottlenecks: costs (round trip
  15 bps = 31% of the 48 bps median stop; binding) and the one-position rule (855 valid triggers =
  58% refused while a position was open; binding for frequency only), but removing the slot does not
  help: sequential mode 1.98/day at -0.30R, independent mode 2.85/day at -0.31R. Nulls with the same
  geometry lose similarly (momentum edge vs null -0.02R). The once-only OOS window was NOT run (gate
  failed) and stays unspent; the design doc section 11 and report section L.2 record the proposed next
  architecture (Bybit-native flow history, structure-confirmed later entries at lower frequency,
  cost-aware trigger eligibility, prospective liquidation families). V5/V5.1 untouched.
- 2026-10-12 (later): V6.1 STRUCTURE + FLOW INTRADAY (`docs/BTC_V6_1_STRUCTURE_FLOW_DESIGN.md` frozen at
  0de0846 before any result; `btc_swing/v61/`; `config/btc_swing_v6_1.yaml`; CLI `btc-swing v61
  ingest|research|render`). Built the BYBIT-NATIVE 5m dataset from the public trade archive (1,324 days
  2021-11-15..2025-06-30, 1.52 bn trades, 56 GB streamed, raw not retained, sha256 manifest; holdout days
  refused; `manifests/v61_bybit_dataset_manifest.json`; an fd leak in the first ingest run filled the disk once
  and was fixed). Five structure-first families, structure-only control, cost-share eligibility (round trip /
  stop <= 0.20 primary), TAKER / MAKER / MAKER_FIRST entry models, time stops, Binance robustness run, nulls,
  folds, declared gate. Result `reports/BTC_SWING_V6_1_STRUCTURE_FLOW_RESEARCH.md`: **>= 2 trades/day FAIL
  (0.40/day), net edge FAIL (gross -0.12R, net -0.27R, PF 0.51 on Bybit validation; Binance -0.28R; null
  -0.24R); every family negative on both windows; flow helped only the trend-pullback family; maker fills raise
  the count, not the edge; without the cost filter 4.2 trades/day at -0.56R.** Recommendation: do NOT spend the
  locked holdout (unspent, never downloaded by V6/V6.1). Design section 17 and report section 14 record the
  reading. V5/V5.1/V6 untouched; nothing activated; no order placed.
- 2026-10-12 (V7 MICROSTRUCTURE SPECIALIST, Phase 1 only): `docs/BTC_V7_MICROSTRUCTURE_DESIGN.md` frozen at
  d419f3c before any result (windows discovery 2022-01..2024-06 / validation 1 2024 H2 / recent 2025 H1,
  holdout untouched); second streaming pass of the Bybit archive into 1m microstructure bars with PIT
  large-trade thresholds (`btc-swing v7 ingest`, `manifests/v7_bybit_1m_dataset_manifest.json`, 1.9 M
  rows); matched-control edge discovery (`btc-swing v7 discover`, `btc_swing/v7/`, methods amendments in
  design section 8). Result `reports/BTC_SWING_V7_EDGE_DISCOVERY.md`: intensity bursts, taker imbalance,
  large-trade imbalance, delta acceleration and efficient flow carry no incremental information (Δ60 within
  +-0.03 ATR5m, < 1 bp, of matched controls); FAILED_BREAK reversal (+0.12 ATR, ~2 bps, 5/5 half-years,
  survives both validation windows) and E1 TREND_PULLBACK_RELEASE (+0.17 ATR, ~3 bps, 3.6 events/day,
  wide intervals, only +-1% brackets positive after costs at ~+6 bps) are the only repeatable effects;
  absorption is a ~1.5 bp effect that fails validation 1; compression breaks reverse; E2/E3 unsupported.
  STOPPED at the discovery report as instructed; no engine built; nothing activated; no order placed.
- 2026-10-12 (V8 CANDIDATE RANKING, DESIGN ONLY, awaiting owner approval): the owner accepted the V7
  result and declined the three V7 engines. `docs/BTC_V8_CANDIDATE_RANKING_DESIGN.md` pre-registers the
  next BTC-only track: a permissive deterministic candidate generator (relaxed E1, cost-eligible failed
  breaks, absorption at a level, compression-break fade; 10-30 candidates/day) plus a PIT-safe context
  ranking (model-free Stage A information check first; then monotone simple score / logistic / shallow
  boosted vs volatility-only and time-only controls; no neural network), labels at 30-180 min incl.
  +0.75%/-0.50% bracket net of Bybit costs, TRAIN-only selection, frozen artefact, validation once per
  window, success = 3-6 executed trades/day with net > 0 and monotone rank buckets. SUPERSEDED the same day:
  the owner stopped the ranking/ML direction before any code or run; the design stays on record only.
- 2026-10-12 (V9 SIMPLE TREND + VOLUME PULLBACK): owner-specified single deterministic strategy, design
  `docs/BTC_V9_SIMPLE_TREND_VOLUME_PULLBACK_DESIGN.md` (frozen before any result; section 10 records the
  harness notes), harness `btc_swing/v9/` (features on 1m/5m/15m from the existing Bybit 1m dataset, leg /
  arm tables, 1m trigger, taker-cost engine with the frozen sizing ladder), A/B/C/D volume ladder with the
  mandatory funnel, TRAIN half-year folds, pre-declared neighbourhoods, accel diagnostic, TRAIN-only
  selection + freeze, one pass per validation window. **Result (classification C): PRICE ONLY 0.93/day,
  net -0.185R, PF 0.72 on TRAIN; frozen step B (impulse RVOL) 0.91/day, -0.231R, PF 0.65 on validation 1 and
  0.78/day, -0.183R, PF 0.73 on recent validation; gross ~3 bps vs 15 bps round trip; the 0.50% minimum
  stop rejects ~88% of triggers (structural stop ~0.2%); the floor-stop sensitivity reaches 4.7/day at
  -0.28R; no volume rule adds edge on both validation windows; accel not promoted.** Profitability FAIL,
  3-6/day FAIL. Locked holdout untouched; nothing activated; no order placed.
- 2026-10-12 (V9 stop-geometry sensitivity, owner request; design section 12, `btc-swing v9 stop-study`,
  `reports/BTC_SWING_V9_STOP_GEOMETRY_STUDY.md`, `reports/v9/stop_study.json`): unchanged entry signal, floor-mode
  stops 0.50/0.75/1.00/1.25/1.50%, exits +1R/+1.5R/+2R/split 1R+2R, constant 0.125% risk, identical time stop.
  **No cell is net positive with PF > 1 on either validation window.** Net R rises monotonically with the floor
  (-0.27 R -> -0.08 R on TRAIN) only because cost/R falls (28% -> 9.5%); gross R is flat at +0.01..+0.03 R; 75-80% of
  wide-stop trades end on the time stop. Stop geometry is not the problem; V9 family closed; nothing adopted.
- 2026-10-12 (V10 LIVE MICROSTRUCTURE SNIPER, forward-only; owner spec): feed audit done on the stored cloud
  recording + Bybit docs (design section 1; liquidation side verified: `S=Buy` -> long liquidated; orderbook.50
  gap rate 1e-5, 50/50 levels, never crossed; trades 10-25/s; latency p50 ~40 ms); design
  `docs/BTC_V10_LIVE_MICROSTRUCTURE_DESIGN.md`; harness `btc_swing/v10/` (own feed + raw store, 1-s features,
  families A/B/C state machines, shadow fills, hash-chained journals for episodes / signals / near-misses /
  shadow trades / outcomes, daily safety, Demo executor gated by `STRATEGY_DEMO_V10_ACTIVATED`, activation
  gates incl. V5/V5.1 conflicts, status/diagnostic/shadow-report/freeze CLI, macOS LaunchAgent). Shadow
  verification was run in the cloud container (verification root, no orders); result and the freeze are in
  `reports/forward_v10/` and `manifests/v10_forward_freeze.json` once written. NOTHING ACTIVATED. Owner steps on
  the Mac (in this order): `git pull`; `uv sync --all-extras`; `bash deploy/macos/install_v10_macos.sh` (loads
  the V10 agent; V5/V5.1 agent untouched); `uv run btc-swing v10 forward status --live`; let it shadow for a
  few hours; `uv run btc-swing v10 forward shadow-report --verification false`; review; then and only then
  `uv run btc-swing v10 demo activate --note "<why>"` (every gate must pass; requires V5.1 STRATEGY_DEMO
  inactive: `uv run btc-swing v51 demo deactivate --note "<why>"` is the owner command, never automatic).
- 2026-10-13 (V10 diagnostic freshness bug, reporting only): on the Mac observation run `v10 forward diagnostic`
  froze at 22:14 UTC (bars_1m 72) and `status` kept printing WARMING although the engine had armed, triggered and
  executed shadow trades. Cause (reproduced by replay): family B's location condition compares a numpy float, which
  yields `numpy.bool_`; the 5-second diagnostic snapshot writer then failed on `json.dumps`, and the engine loop's
  generic handler slept 1 s per failure. The engine, journals and state kept running (journals use `default=str`).
  Fix (no strategy change): `_clean` converts numpy scalars, condition dicts are plain bools, the snapshot writer is
  isolated from the tick loop (no stall), `status` derives warm-up from state.json + bars_1m.jsonl and flags a stale
  snapshot; new read-only `v10 forward warmup` and `v10 forward trades`; `shadow-report` accepts `--no-verification`
  or `--observation` for the observation root. The fix takes effect on the Mac only after the owner restarts the V10
  agent (`launchctl kickstart -k gui/$(id -u)/com.btcswing.v10-forward`), at a moment of their choosing.
- 2026-10-13 (V10 pre-activation change, owner request): shadow and Demo execution counters separated
  (`executed_ms` = shadow, `demo_executed_ms` = demo); new `safety.demo_max_trades_rolling_24h: 10` (shadow cap
  5 unchanged); `start_demo_regime` resets day / streak counters when the executor attaches (journaled); the
  freeze check now pins the strategy sections (`strategy_hash`), so the operational cap change does not
  invalidate `manifests/v10_forward_freeze.json`. Risk 5 USDT, -25 USDT daily stop, 3-loss streak, one position,
  thresholds, stop geometry, observation_start and the shadow journals are unchanged.
- 2026-10-13 (V11 FAST MULTI-FACTOR INTRADAY, new isolated generation; owner spec, RESEARCH + SHADOW ONLY):
  V10 was ACTIVE on Bybit Demo during the whole build and was not touched (every V10 file byte-identical to
  `eabd2a5`; verified by sha256 before and after). Phase 1 audit + design `docs/BTC_V11_FAST_MULTIFACTOR_DESIGN.md`
  (frozen before any result: Bybit 1m dataset 2021-11..2025-06, Binance OI/funding PIT proxy, no volume profile,
  no fabricated book / liquidation history, costs taker 5.5 + half-spread 0.5 + slippage 1 bps (3 on stops)).
  Phase 2 `btc_swing/v11/` (features, score, engine, research, report, shadow runtime) + `tests/v11/` (17 tests:
  PIT / no lookahead, renormalisation, LONG/SHORT symmetry, stop floor / cap rejection, fixed-risk sizing with
  3x/5x invariance, TP quantities, break-even, 60-min stop, stall, costs, one position, V10/V11 isolation,
  no exchange orders, freeze/hash, restart/recovery, journal integrity). Phase 3/4 ONE experiment
  `reports/BTC_V11_FAST_MULTIFACTOR_BACKTEST.md`: classification C — TRAIN 2445 trades (2.68/day), net
  -0.38R, PF 0.22; VALIDATION 974 trades (2.67/day), net -0.42R, PF 0.19; gross -0.02R / -0.05R, costs
  0.37R per trade (2.7 USDT); median hold 20 min; every family and side negative; x1.5 costs worse. No
  optimisation was done and none is proposed. Phase 5/6: shadow runner built and verified live in the cloud
  (verification root; see `reports/forward_v11/`), then frozen (`manifests/v11_forward_freeze.json`,
  observation_start = freeze time). Owner steps on the Mac (ONLY the V11 shadow agent; V10 untouched):
  `git pull`; `uv sync --all-extras`; `bash deploy/macos/install_v11_macos.sh`;
  `uv run btc-swing v11 forward status`. There is no V11 Demo command and nothing to activate.
- 2026-10-13 (V11 freeze): `manifests/v11_forward_freeze.json` written at 11:37:48 UTC (observation_start =
  frozen_at; code 98445d6; strategy hash `a324dbcb0394…`, config hash `74472b51dab0…`; versions v11-feat-1 /
  v11-score-1 / v11-exit-1). Cloud live-feed verification (verification root, 9 min foreground + two earlier
  short runs; `reports/forward_v11/`): 33,910 messages in 540 s, 0 reconnects / gaps / duplicates / engine errors,
  latency p50 13 ms, p99 38 ms; 1m bars completed and persisted; microstructure histories populated; journals
  verify. NOT proven live in the cloud: indicator / score population on real bars, because the frame needs 120
  completed live bars (2 h) and a cloud run cannot outlive a session turn (the container's outbound proxy is
  torn down between turns, which killed the 2h25m attempt at 09:07 UTC after 5.5 min). That path is covered by
  `tests/v11` (the real live code path driven with 1000 bars) and will be visible on the Mac after ~2 h of
  shadow observation (`v11 forward status`; sizing additionally needs ~15 h for ATR(1h)). The freeze records the
  shadow verification as NOT_PASSED for that reason; nothing in the strategy depends on it.
- 2026-10-13 (V11.1 COST-AWARE HIGH-CONVICTION INTRADAY, owner follow-up experiment, RESEARCH ONLY): design
  `docs/BTC_V11_1_COST_AWARE_INTRADAY_DESIGN.md` pre-registered before the run (H1 entry quality: >= 4 independent
  directional groups, no trend contradiction, tightened A/B/C rules, failed-breakout rejection; H2 stop geometry:
  0.55% floor / 1.00% cap, 0.20 ATR buffers, cost filter 14 bps / stop <= 0.25R). `btc_swing/v11_1/` subclasses
  the V11 engine (V11 imported read-only; V10 and V11 files hash-identical before and after; neither runner
  touched). ONE run (`btc-swing v111 research`) on the V11 windows: classification C — VALIDATION 36 trades
  (0.10/day, 91% zero-trade days), gross +0.003R, cost 0.204R, net -0.201R, PF 0.36, max DD 1.1%, median hold
  26 min; TRAIN 64 trades (0.07/day), gross -0.036R, cost 0.204R, net -0.240R, PF 0.40. Versus V11 validation
  (2.67/day, gross -0.051R, cost 0.364R, net -0.415R, PF 0.19): cost/R -0.16R (wider stops work as cost
  arithmetic), gross +0.05R (not an edge), frequency -96%; families B and C produced zero trades under the
  pre-registered gates (B: failed-breakout / RVOL / momentum rules; C: extension rule plus evidence and trend
  contradiction); COST_TOO_HIGH rejected 227 (validation) / 543 (train) otherwise-valid decisions, STOP_TOO_WIDE
  16 / 36. Nothing was tuned after the run (the engine's evidence counter was switched to the vectorised array
  during the run; both derive from the same confirmation flags, so results are identical). No V11.1 shadow
  runner, no freeze, no V11.2. `tests/v11_1` (13 tests). Next step: the owner's decision; nothing is proposed.
- 2026-10-13 (V11 cold-start audit, read-only): after 7h40m on the Mac the V11 shadow had 441 bars, 25 candidates,
  0 armed. Cause: RVOL needs > 20 calendar days of bars in the live frame (the `rvol_min_days: 10` setting never
  applies live), the 5m ATR percentile ~2.4 days, 15m structure hours, the 1h ATR ~14 h; the backtest had a 30-day
  warm-up. Cold-start replay of the exact live code on 8 validation starts: 0.38 trades/day in hours 0-8, 2.1/day
  after 3 days, ~2.4-2.5/day after a week (warm: 2.5/day). Not a feed bug. Latent V11 bias noted: three volatility
  flags read 0 instead of NaN while their percentile is missing (first ~2.4 days). NOT changed.
- 2026-10-13 (V11-WARM, operational fix, owner request): `btc_swing/v11_warm/` runs the frozen V11 strategy object
  (hash-checked against `manifests/v11_forward_freeze.json`) with a PIT-safe warm-start: 30 days of exact Bybit 1m
  bars (public trade archive 2026-09-13..2026-10-12, 43,200 contiguous bars, OHLCV + taker split + trade count,
  content hash `3132d3410b54…`) committed as `manifests/v11_warm_bootstrap_1m.parquet`; `v11w seed` writes them into
  the V11-WARM root (never into a research dataset; refuses re-seeds and any bar closing after observation_start)
  and, on the Mac only, appends Bybit PUBLIC klines for the archive->now gap (price/volume exact, taker split NaN,
  trade count placeholder; REST is geo-blocked in the cloud). NOTE: 2026-09-13..09-30 lie inside the locked holdout;
  they are rolling-baseline context only, never a statistic or decision. Never backfilled: order book,
  liquidations, live microstructure; derivatives warm live. Runtime = V11 live scorer subclass: 31-day rolling
  frame, open-time bar detection, 30-min live warm-up (observation_start = frozen_at + 30 min). Test proves identical
  indicators / scores / decisions to V11 on the same warmed history. Cloud verification (verification root, no
  gap-fill possible): 19 + 70 + 95 min live, 390k messages, 2 auto-reconnects, 0 gaps, 0 engine errors; RVOL,
  EMA50, RSI, ADX, VWAP, structure populated from the first completed 5m bar, 60-min range after 61 bars, 5m ATR /
  tercile after 75 min, 9 candidates journaled in-window, 15 outcomes, chains valid; still missing at the end only
  the 1h ATR (14 h after a gap) and the Bollinger-based volatility group (20 contiguous 5m bars, 5 min after the
  run ended) — both closed on the Mac by the kline gap-fill (unit-tested with mocked klines). Freeze
  `manifests/v11_warm_forward_freeze.json`: frozen 2026-10-13T23:13:03Z, observation_start 2026-10-13T23:43:03Z,
  code 6da87de, V11 strategy `a324dbcb0394…`, shadow report recorded NOT_PASSED (gap-dependent warm checks +
  in-window duration 65 min < 90). FINDING (reported, NOT changed, owner decision): the V11 live loop detects new
  bars by deque length, so the frozen V11 shadow will stop scoring once 22 days of live bars fill its deque
  (~2026-11-04). Mac steps (ONLY the V11-WARM agent): `git pull`, `uv sync --all-extras`,
  `bash deploy/macos/install_v11_warm_macos.sh` (seeds + gap-fills + loads the agent), `uv run btc-swing v11w forward
  verify-warm`. No Demo exists for V11-WARM.
- 2026-10-14 (V11-WARM seam fix, plumbing only; owner report: Mac verify-warm NOT_WARM with ATR5 / HI60 /
  vol_tercile and the trend / momentum / volatility / location groups missing while EMA / RSI / ADX / RVOL
  were populated): root cause reproduced in a test — one or two minutes missing between the kline gap-fill
  (ends at the minute before `seed` ran) and the first live bar (minute of the first trade after connect);
  a single missing minute resets every contiguous-window feature (ATR5 75 min, HI60 61 min, Bollinger 100 min)
  while state-carrying ones survive. Fix in `btc_swing/v11_warm/live.py`: bars file loaded sorted +
  de-duplicated; `heal_holes()` fills every missing minute in the window with Bybit PUBLIC klines
  (`src: kline_seamfill`) at startup and whenever a new live bar is more than one minute after the previous
  one (reconnects too); diagnostic shows `holes_in_window` / `seam_fills`. No strategy, threshold, family,
  risk or exit change; V11 strategy hash unchanged (`a324dbcb0394…`); freeze unchanged (it pins strategy and
  bootstrap, not code). Mac: `git pull`, `uv sync --all-extras`, `launchctl kickstart -k
  gui/$(id -u)/com.btcswing.v11-warm-forward`, then `uv run btc-swing v11w forward verify-warm` after ~2 min
  (the restart heals the historical seam hole from the kline endpoint before the first tick).
- 2026-10-14 (generic read-only forward reporting layer, owner request): `btc_swing/forward_report/` (spec, reader,
  metrics, renderer, typer factory) + `generations.py` spec builders; `btc-swing v10|v11|v11w report full-history
  [--verification]` writes `reports/<generation>/full_history_<ts>.{json,md}` and `latest_full_history.*` only.
  Report content: funnel (candidates / armed / triggers / executed / blocked / near misses / outcomes), family x
  side tables, blockers and failed conditions, score distributions, shadow PnL / R (win rate, PF, max DD, holds,
  exits, by family / side), outcomes by kind and horizon, daily table, journal inventory, V10 Demo section (journal
  kinds, activation events, demo trades); N/A for anything a generation does not journal. Only the three
  generation CLI files gained a `report` sub-app registration; engines, configs, freezes and journals are
  unchanged. `tests/forward_report` (6 tests) proves: journal file hashes, state.json, freeze manifests and strategy
  hashes identical after a report; writes confined to reports/<generation>/ (other targets refused); no network
  (httpx / socket forbidden during the run); no execution / exchange import in the layer.
- 2026-10-14 (V11-WARM arm audit, read-only): 22 candidates / 0 armed is consistent with the frozen rules
  (historically 8.4% of candidates arm within 5 min; family C is effectively impossible at high scores; B needs price
  just inside the 60-min edge; A needs a fresh 5m impulse-pullback plus aligned 15m structure). Reported, not
  changed: the V11 live loop never increments the state `triggers` counter, and it journals / counts ARMED on every
  re-arm refresh minute (V11-WARM inherits both). A read-only per-candidate audit script was handed to the owner.
- 2026-10-14 (V11.2 counterfactual gating study, research only, owner request): `btc_swing/v11_2/`, report
  `reports/BTC_V11_2_COUNTERFACTUAL_GATING_STUDY.md`. 9 independent variants + 3 combinations on the consumed
  windows (loads capped at 2025-07-01). BASE = current frozen engine (matches the committed V11 report on entries
  and gross; net ~0.008R lower because that report predates the post-TP1 stop-leg slippage label fix). No variant
  meets the owner's selection rule (positive gross and net, PF > 1) in either window; best frequency/expectancy
  trade-off A1 (struct15 side-or-neutral): TRAIN 3.86/day net -0.361R, VALIDATION 3.48/day net -0.386R (BASE
  -0.389 / -0.424). Family C: structure and score >= 70 coincide in 0.07% of C minutes; C alone without score gates
  trades ~30/day at gross +0.01R, net -0.40R. Selection-biased; nothing frozen, no runner, nothing adopted.
- 2026-10-14 (V11-LITE-WARM, new isolated SHADOW-ONLY forward experiment, owner request): `btc_swing/v11_lite_warm/`,
  design `docs/BTC_V11_LITE_WARM_DESIGN.md` pre-registered before observation. The warm-started V11 shadow with ONE delta:
  `V11LiteEngine.structure()` = data validity only (A/B/C structure journaled as labels, never a gate); triggers, stops,
  exits, risk, costs, score, thresholds, warm-start, seam healer unchanged (V11 strategy hash asserted). Read-only
  Fibonacci / VWAP / range / swing / location / flow / microstructure telemetry on every event; in-process strict V11
  control twin attributes every LITE signal (incremental cohort with the exact failed structure condition).
  `v11lw report compare` = V11-WARM vs V11-LITE-WARM + incremental cohort + Fibonacci buckets + checkpoints. Cloud
  verification (verification root, 70 in-window minutes, 0 engine errors): 12 candidates, 13 arm starts, 3 signals (1
  executed, 2 blocked POSITION_OPEN), ALL incremental (strict twin: 0 armed; reasons STRUCTURE_A/C_FALSE named); shadow
  report NOT_PASSED only on the kline-gap-dependent fields (1h ATR, Bollinger volatility; REST geo-blocked here, the Mac
  gap-fill closes them), as for V11-WARM. Freeze `manifests/v11_lite_warm_forward_freeze.json`: frozen 2026-10-14
  13:34:40Z, observation_start 2026-10-14T14:04:40Z, code 58083d4, V11 strategy a324dbcb0394, lite strategy
  c66ef46a493f. V10 / V11 / V11-WARM code, configs, freezes, journals untouched (sha256 snapshot before/after +
  tests). Mac: install ONLY `com.btcswing.v11-lite-warm-forward`; never activate anything.
- 2026-10-14 (V11-LITE-WARM pre-launch bookkeeping cleanup, owner request after a read-only design audit; economics
  unchanged): one arm state per side (`v11lw-book-2`), arm starts only on NOT_ARMED -> ARMED, at most one ARMED /
  NEAR_MISS / decision per side-minute, `selected_template` (first frozen trigger/stop template, not a setup) kept apart
  from the frozen `structure_labels`; comparison report shows unique opportunities (side-level arm episodes, decisions,
  blocked, near misses) for both generations. Equivalence with the f885f85 engine proven by replay: synthetic, adversarial
  (score walks + ATR5 gaps), live `_step`, and the full consumed TRAIN / VALIDATION frames (133,107 + 44,915 decisions,
  19,916 + 7,049 trades, zero field mismatches; `reports/forward_v11_lite_warm/equivalence_f885f85_vs_book2.json`).
  The f885f85 freeze (observation_start 2026-10-14T14:04:40Z) never entered Mac forward observation; it is kept
  byte-identical as `manifests/v11_lite_warm_forward_freeze_superseded_f885f85.json` and a new freeze supersedes it.
  New freeze `manifests/v11_lite_warm_forward_freeze.json` (bookkeeping v11lw-book-2): frozen 2026-10-14T22:31:09Z,
  code de9531d (= 0e12844 code), V11 strategy a324dbcb0394, lite strategy c66ef46a493f (unchanged), observation_start
  2026-10-15T12:00:00Z (Mac launch: install before ~11:30 UTC so the runner is live-warm). Cloud verification of the
  cleaned code: 70 in-window minutes, 0 engine errors, NOT_PASSED only on the 1h ATR field (cloud seam fill blocked).
- 2026-10-16 (V11-LITE-WARM pre-launch re-freeze, owner request; freeze only, no code / config / strategy change):
  the Mac runner was never installed, so the 2026-10-15T12:00Z observation_start passed without observation. New
  `manifests/v11_lite_warm_forward_freeze.json` (frozen 2026-10-16T09:06:07Z, code 5a95345, lite strategy c66ef46a493f,
  bookkeeping v11lw-book-2, observation_start 2026-10-16T11:30:00Z); the b80f202 freeze is kept byte-identical as
  `manifests/v11_lite_warm_forward_freeze_superseded_b80f202.json` (entered_mac_forward_observation: false). Seed today:
  the bootstrap ends 2026-10-13 and the ~81 h gap exceeds gapfill_max_hours (48), so ATR5 / HI60 / Bollinger warm live
  (~100 min) and the 1h ATR (sizing liquidation check only) after ~14 h.
- 2026-10-16 (V11-LITE-WARM FINAL pre-launch shadow re-freeze, owner request; freeze only): the 2026-10-16T11:30Z
  observation_start passed before the Mac runner was installed, so NO previous V11-LITE window (14T14:04Z, 15T12:00Z,
  16T11:30Z) ever entered Mac forward observation (recorded in the freeze under `previous_observation_windows`). New
  `manifests/v11_lite_warm_forward_freeze.json`: frozen 2026-10-16T12:18:38Z, code c358109, observation_start
  2026-10-16T14:30:00Z, lite strategy c66ef46a493f, bookkeeping v11lw-book-2, lite config 914085bfafb0 (all unchanged);
  the 01e7c03 freeze kept byte-identical as `manifests/v11_lite_warm_forward_freeze_superseded_01e7c03.json`; the Demo
  execution manifest (sha 6d320ce6…) is unchanged and not activated. Install the runner by ~12:45Z so the ~100 min of
  live warm-up completes before 14:30Z.
- 2026-10-16 (V11-LITE-WARM Bybit DEMO execution layer, owner request; execution only, NOT activated):
  `btc_swing/v11_lite_warm/demo/`, `config/btc_swing_v11_lite_warm_demo.yaml`, `docs/V11_LITE_WARM_DEMO_EXECUTION.md`,
  manifest `manifests/v11_lite_warm_demo_execution_freeze.json`. The shadow stays the source of truth (copy-only observer
  after journaling; Demo worker / supervisor threads; shadow journals proven identical with and without Demo); Demo
  consumes the same frozen decisions: 5 USDT risk (0.10 % of 5000) on the shadow stop, leverage 1, exchange stop + TP1 /
  TP2 reduce-only limits, frozen BE / trail / stall / 60-min exits; caps 10 / 24 h, daily stop -25 USDT, halt after 3
  full losses (`v11lw demo resume`), reconciliation on any mismatch (`v11lw demo reconcile-ack`). Default account mode
  ISOLATED (`BYBIT_DEMO_V11LW_API_KEY/SECRET` in `~/.config/btc_swing/v11lw_demo.env`, mode 600; activation proves a
  different Bybit user id than V10's); shared mode only with `--shared-account` and the V10 conflict gate
  (`V10_POSITION_OR_ORDERS_ACTIVE`, `V10_RECONCILIATION_PENDING`, `OWNERSHIP_UNPROVEN`) — not recommended (V10's own
  executor would block / reconcile on LITE positions). Activation only by the owner: `btc-swing v11lw demo activate`.
- No live trading exists; the forward paper ledger is virtual. 2022-01..2026-09 is development data for every generation.
- Next step: the owner's decision. No report proposes a tweak. Five generations (structure, learned
  ranking, active structure, 1H positioning events, 5m microstructure events) found no edge that
  survives costs on this instrument at these horizons; the recurring finding is a small gross edge
  (+0.02..+0.12R) that costs of 0.08..0.13R per trade erase. If anything is pursued it needs a NEW
  owner pre-registration and, ideally, forward data: the Bybit collector (`btc-swing v5 collect`)
  can accumulate a liquidation/order-book history that does not exist in any archive.

- INTRADAY PORTFOLIO RESEARCH (E1 / E2 / E4, 2026-10-19): new isolated, order-disabled harness
  `btc_swing/research/intraday_portfolio/` (`btc-swing ipr inventory|research`), 32 tests
  (`tests/intraday_portfolio/`: PIT safety, future-data invariance, no same-close fills, receipt-time
  availability, duplicate / reordered input, fail-closed missing data, bounded buffers, episode dedup,
  long/short symmetry, no spread double-counting, one-position portfolio, deterministic reruns, journal /
  report reconciliation). Run `reports/intraday_portfolio_research/20261019T114233Z/` (SUMMARY.md, manifest,
  parents / trade / portfolio replays, cost / frequency / regime tables, data inventory). Verdicts: E1
  REJECT, E2 REJECT (benchmark ledger on 2021-11-15..2025-06-30; both net negative at 16 bps with the
  day-cluster lower bound < 0; E1 even gross negative), E4 INCONCLUSIVE (no 30-day receipt-timed
  liquidation history exists anywhere; the Mac stores have ~7 days). Executable replay for E1/E2 is
  UNAVAILABLE (no historical BBO). Nothing qualifies for runner engineering.
- Next step: owner decision. E4 can only become testable by letting the Bybit public collector run
  >= 31 complete days (the Mac V10 raw store already accumulates trades + allLiquidation + orderbook.50)
  and then running `btc-swing ipr research` on that machine; E1/E2 need no further historical work.

## Commands
```
uv sync --all-extras
export BTC_DATA_DIR=./data
uv run btc-swing data ingest --from 2021-10 --to 2024-12   # ~1.5k archive files, resumable
uv run btc-swing backtest --from 2024-01-01 --to 2025-01-01 --verify-determinism --out data/btc/runs/smoke
uv run btc-swing phase2 --out data/btc/runs/phase2_validation
uv run btc-swing phase21 --out data/btc/runs/phase21_entry_mechanics   # CONTROL vs ZONE_ENTRY (~8 min)
uv run btc-swing phase22 --out data/btc/runs/phase22_confirmation_exit  # CONTROL vs ZONE_ENTRY_CONFIRM_EXIT (~10 min)
uv run btc-swing phase23 --out data/btc/runs/phase23_post_tp1_exit      # CONTROL vs STRUCTURAL_TRAIL_AFTER_TP1 (~10 min)
uv run btc-swing phase24 --out data/btc/runs/phase24_short_regime       # CONTROL vs NO_NEW_SHORT_IN_TREND_DOWN (~10 min)
uv run btc-swing data ingest --from 2025-01 --to 2026-09                 # holdout archive (Phase 3 only)
uv run btc-swing phase3-freeze --ingest-stats manifests/phase3_ingest_stats.json   # freeze BEFORE the run; commit it
uv run btc-swing phase3 --out data/btc/runs/phase3_untouched_validation  # ONE confirmatory run per arm (~15 min)
uv run btc-swing v2 research --out data/btc/runs/v2_ranking_research     # V2 candidates/labels/features/walk-forward/report (~25 min)
uv run btc-swing v3 research --out data/btc/runs/v3_active_swing_research # V3 one pre-registered run + reporting streams (~60 min)
uv run btc-swing v4 research --out data/btc/runs/v4_event_positioning_research # V4 Stage A + Stage B (~45 min)
uv run btc-swing v5 ingest --start 2021-12-01 --end 2026-09-30            # V5 archive datasets (index klines, bookDepth, aggTrades -> 5m flow; ~1 h)
uv run btc-swing v5 collect --duration 900 --reconnect-after 420            # Bybit PUBLIC collector verification run (no auth, no orders)
uv run btc-swing v5 research --out data/btc/runs/v5_microstructure_research --collector-stats manifests/v5_bybit_collector_verification.json  # V5 Stage A + Stage B (~8 min)
uv run btc-swing v5 forward freeze && uv run btc-swing v5 forward seed && nohup uv run btc-swing v5 forward run &   # forward observation (see docs/V5_FORWARD_OBSERVATION_RUNBOOK.md)
uv run btc-swing v5 forward status                                          # live status
uv run btc-swing v6 research                                                # V6 development + validation (~6 min); --oos --confirm-once-only-oos spends the OOS window once
uv run btc-swing v6 render                                                  # re-render the V6 report from reports/v6/summary.json
uv run btc-swing v61 ingest                                                 # Bybit trade archive -> 5m rows 2021-11-15..2025-06-30 (~1 h; holdout refused)
uv run btc-swing v7 ingest                                                  # Bybit trade archive -> 1m microstructure bars (~1.5 h; holdout refused)
uv run btc-swing v7 discover                                                # V7 Phase 1 edge discovery (~6 min); stops at the report
uv run btc-swing v61 research                                               # V6.1 development + validation (~4 min); stops before the holdout
bash scripts/check.sh
uv run btc-swing v11 research                                            # ONE pre-registered historical experiment (done; C)
uv run btc-swing v11 forward run --shadow-verification --duration 600       # live-feed verification (verification root)
uv run btc-swing v11 forward status | diagnostic | shadow-report | trades   # read-only; --verification / --no-verification
uv run btc-swing v111 research                                           # V11.1 ONE pre-registered experiment (done; C)
uv run btc-swing v11w seed && uv run btc-swing v11w forward status      # V11-WARM: seed root (committed bootstrap + kline gap-fill), status
uv run btc-swing v11w forward verify-warm | diagnostic | shadow-report  # read-only; --verification for the verify root
uv run btc-swing v10 report full-history | v11 report full-history | v11w report full-history   # read-only; --verification
uv run btc-swing v112 study && uv run btc-swing v112 combos "A1_NEUTRAL_OK+B1_NEAR_040"   # V11.2 counterfactual study (research only)
uv run btc-swing v11lw seed && uv run btc-swing v11lw forward status    # V11-LITE-WARM: seed root, status (shadow only)
uv run btc-swing v11lw report compare                                    # read-only V11-WARM vs V11-LITE-WARM + incremental cohort
uv run btc-swing v11lw demo status --live && uv run btc-swing v11lw demo report  # V11-LITE Bybit DEMO (owner activation only)
uv run python scripts/v11lw_forward_forensic_audit.py             # READ-ONLY per-trade forensic audit of the LITE shadow (Mac; live-safe prefix snapshot)
uv run btc-swing ipr inventory && uv run btc-swing ipr research          # intraday portfolio research E1/E2/E4 (~5 min; files only, order-disabled)
```

## Known data facts
- Two venue incidents where 5m archive bars are stale vs native hourly bars: 2023-11-10
  15:00–17:00 UTC and 2024-10-28 20:00–21:00 UTC. Daily bars match exactly.
- Mark-price and premium-index klines have a few missing days (2022-10, 2023-02); the engine falls
  back to traded extremes for liquidation where a mark bar is missing.
- Early `metrics` files (2021) contain empty fields; parsed as nulls.
- V5 archive facts: the monthly indexPriceKlines files lack 13 whole days (2022-04-27, 2022-07-24/25/27/28/30/31,
  2022-10-02, 2023-02-24, 2023-04-07/08, 2023-11-10, 2026-06-29); the daily files exist and are used
  to fill them. bookDepth has no file for 2023-02-08, 2023-02-09 and 2024-04-18. aggTrades daily
  files before mid-2025 have no CSV header. No liquidationSnapshot dataset exists for BTCUSDT UM.
