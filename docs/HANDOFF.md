# Handoff — state of work and next steps (2026-10-05)

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
- Phase 3 has NOT started. Entry mechanics (2.1, 2.2) and the post-TP1 exit (2.3) are exhausted
  as single-switch hypotheses; the next step is the owner's decision. No tuning. All experiment
  switches stay at their frozen defaults (`entry_mode: CONFIRMED_TRIGGER`,
  `breakeven_after_tp1: true`).

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
bash scripts/check.sh
```

## Known data facts
- Two venue incidents where 5m archive bars are stale vs native hourly bars: 2023-11-10
  15:00–17:00 UTC and 2024-10-28 20:00–21:00 UTC. Daily bars match exactly.
- Mark-price and premium-index klines have a few missing days (2022-10, 2023-02); the engine falls
  back to traded extremes for liquidation where a mark bar is missing.
- Early `metrics` files (2021) contain empty fields; parsed as nulls.
