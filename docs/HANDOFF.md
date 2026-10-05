# Handoff — state of work and next steps (2026-10-05, V2 research done)

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
- Next step: the owner's decision. The V2 report proposes no parameter tweak; a different
  candidate generator or label definition would be a new research generation with a new
  pre-registration. Nothing should be re-run with changed thresholds in search of a positive result.

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
bash scripts/check.sh
```

## Known data facts
- Two venue incidents where 5m archive bars are stale vs native hourly bars: 2023-11-10
  15:00–17:00 UTC and 2024-10-28 20:00–21:00 UTC. Daily bars match exactly.
- Mark-price and premium-index klines have a few missing days (2022-10, 2023-02); the engine falls
  back to traded extremes for liquidation where a mark bar is missing.
- Early `metrics` files (2021) contain empty fields; parsed as nulls.
