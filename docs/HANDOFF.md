# Handoff — state of work and next steps (2026-10-07, V5 research done; forward observation running)

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
- No live trading exists; the forward paper ledger is virtual. 2022-01..2026-09 is development data for every generation.
- Next step: the owner's decision. No report proposes a tweak. Five generations (structure, learned
  ranking, active structure, 1H positioning events, 5m microstructure events) found no edge that
  survives costs on this instrument at these horizons; the recurring finding is a small gross edge
  (+0.02..+0.12R) that costs of 0.08..0.13R per trade erase. If anything is pursued it needs a NEW
  owner pre-registration and, ideally, forward data: the Bybit collector (`btc-swing v5 collect`)
  can accumulate a liquidation/order-book history that does not exist in any archive.

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
bash scripts/check.sh
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
