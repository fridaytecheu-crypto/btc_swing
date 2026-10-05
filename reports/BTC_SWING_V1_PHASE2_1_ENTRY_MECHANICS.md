# BTC Swing V1 — Phase 2.1: ENTRY MECHANICS (CONTROL vs ZONE_ENTRY)

Generated 2026-10-05 08:25 UTC · period 2022-01-01 00:00 -> 2025-01-01 00:00 UTC · CONTROL result hash `92ebe5d7fc65` · ZONE_ENTRY result hash `83234c5cbb54` · code `b2e9935c0b72-dirty`

**Central question.** Is the current confirmation trigger destroying edge by making us enter too late or miss valid BTC swing setups that already reached the planned entry zone?

Paper/backtest only. No live trading, no authenticated exchange access, no real money. 2025+ data untouched.

## 1. Hypothesis

Phase 2 (frozen defaults) found: traded episodes had a mean signed 24h forward return of about +0.34% from the detection close, never-triggered episodes about +1.13%, and never-triggered episodes reached their structural target before invalidation more often (66% vs 42%). Pre-registered hypothesis H1: entering when a valid setup first reaches its pre-defined entry zone (ZONE_ENTRY) has materially stronger NET expectancy than waiting for the 15m confirmation and 5m trigger (CONTROL), without an unacceptable increase in drawdown, in both 2022-23 and 2024, and it recovers useful never-triggered setups. Only the WATCH -> TRIGGERED transition changes; setup detection, zone, invalidation, stop, sizing, leverage, exits, costs and regime rules are identical.

## 2. Frozen baseline verification

| check | value |
|---|---|
| Phase 2 validation result hash | `92ebe5d7fc65fc978ba4d3d222723e30c31d1db26e74d6e6585c786528c4ea56` |
| CONTROL result hash (this run) | `92ebe5d7fc65fc978ba4d3d222723e30c31d1db26e74d6e6585c786528c4ea56` |
| identical | yes |
| CONTROL trades / episodes | 275 / 533 |
| config hash CONTROL / ZONE_ENTRY | `871a444e107e` / `ad3332d75dce` |
| note | the config hash differs from Phase 2 only because the `experiment` section was added to the schema; the CONTROL result hash is the equivalence proof |

## 3. Exact variant definition

- CONTROL (`experiment.entry_mode: CONFIRMED_TRIGGER`): WATCH -> ENTRY_READY when the completed 5m bar reaches the zone AND the completed 15m bar closes back through its EMA20 in the trade direction; ENTRY_READY -> TRIGGERED when a completed 5m bar closes beyond the previous 5m extreme on the right side of the zone; fill at the next 5m open plus slippage.
- ZONE_ENTRY (`experiment.entry_mode: ZONE_ENTRY`): WATCH -> TRIGGERED on the first completed 5m bar that reaches the zone (`zone_reached`: bar traded into the zone and closed on the correct side of its far edge) while the plan is still valid; fill at the next 5m open plus slippage. No ENTRY_READY state, no 15m confirmation, no 5m trigger.
- Unchanged in both arms: families, regime eligibility, detection, the frozen `SetupPlan` (zone, invalidation, stop, structural target), watch timeout and run-away invalidation, cooldown and anchor de-duplication, single slot, sizing and leverage ladder, TP1/TP2/breakeven/structural trail/time cap, fees, slippage, funding, mark-price liquidation.
- Implementation: one `if self.entry_mode == "ZONE_ENTRY"` branch in `EpisodeManager.step`, covered by `tests/test_state_machine.py`.

## 4. PIT audit

- Visibility: bar visible iff close_time <= t; zone test uses the completed 5m bar at t; fill at the next 5m open. Zone: fixed at detection (SetupPlan is frozen); never moved after observing later prices.
- Deterministic rerun: CONTROL yes, ZONE_ENTRY yes.
- Truncation audit (ZONE_ENTRY): decisions up to 2023-07-03T00:00 identical with later data removed: yes (157825 rows).
- Data, resampling oracle and venue incidents: unchanged from the Phase 2 report (same ingested series, same hashes).

## 5. Overall CONTROL vs ZONE_ENTRY (combined 2022-01 -> 2024-12)

| metric | CONTROL | ZONE_ENTRY |
|---|---|---|
| trades | 275 | 391 |
| episodes | 533 | 462 |
| setup->entry | 51.6% | 84.6% |
| exp. R (net) | 0.081 | 0.029 |
| exp. R (gross) | 0.226 | 0.195 |
| t | 0.92 | 0.37 |
| PF | 1.14 | 1.05 |
| win rate | 47.3% | 44.2% |
| avg win R | 1.421 | 1.509 |
| avg loss R | -1.121 | -1.146 |
| median R | -1.043 | -1.065 |
| MFE R | 2.074 | 2.133 |
| MAE R | -0.862 | -0.870 |
| max DD | -8.96% | -12.07% |
| Sharpe | 0.66 | 0.29 |
| median hold h | 22.6 | 16.2 |
| fees | -1211 | -2006 |
| slippage | -697 | -1162 |
| funding | -117 | -148 |
| net P&L | 1133 | 579 |
| net return | 11.33% | 5.79% |

## 6. dev_2022_2023 comparison

| metric | CONTROL | ZONE_ENTRY |
|---|---|---|
| trades | 168 | 237 |
| episodes | 323 | 281 |
| setup->entry | 52.0% | 84.3% |
| exp. R (net) | 0.128 | 0.055 |
| exp. R (gross) | 0.277 | 0.229 |
| t | 1.12 | 0.53 |
| PF | 1.22 | 1.09 |
| win rate | 48.8% | 45.1% |
| avg win R | 1.451 | 1.531 |
| avg loss R | -1.134 | -1.160 |
| median R | -1.041 | -1.065 |
| MFE R | 2.114 | 2.182 |
| MAE R | -0.895 | -0.899 |
| max DD | -3.56% | -6.60% |
| Sharpe | 0.97 | 0.49 |
| median hold h | 22.4 | 17.8 |
| fees | -799 | -1313 |
| slippage | -456 | -759 |
| funding | -25 | -35 |
| net P&L | 1092 | 661 |
| net return | 10.92% | 6.61% |

### Families in dev_2022_2023

| family | CONTROL | ZONE_ENTRY |
|---|---|---|
| BREAKDOWN_SHORT | n=9, R=1.651, PF=7.83, win=78%, MFE=5.98, MAE=-0.55, acct=7.26% | n=10, R=0.835, PF=3.44, win=70%, MFE=3.94, MAE=-0.50, acct=4.08% |
| BREAKOUT_LONG | n=11, R=0.783, PF=2.40, win=55%, MFE=4.66, MAE=-0.80, acct=4.31% | n=22, R=0.029, PF=1.04, win=41%, MFE=2.90, MAE=-0.85, acct=0.18% |
| MOMENTUM_CONTINUATION_LONG | n=14, R=-0.401, PF=0.44, win=36%, MFE=1.11, MAE=-0.93, acct=-2.73% | n=12, R=-0.437, PF=0.41, win=33%, MFE=1.05, MAE=-0.86, acct=-2.51% |
| RESISTANCE_REJECTION_SHORT | n=39, R=0.072, PF=1.13, win=54%, MFE=1.68, MAE=-0.97, acct=1.42% | n=69, R=0.366, PF=1.66, win=54%, MFE=2.69, MAE=-0.91, acct=12.64% |
| SUPPORT_RECLAIM_LONG | n=29, R=0.269, PF=1.52, win=52%, MFE=2.07, MAE=-0.86, acct=3.69% | n=51, R=0.066, PF=1.10, win=45%, MFE=1.93, MAE=-0.89, acct=1.69% |
| TREND_PULLBACK_LONG | n=31, R=0.088, PF=1.14, win=45%, MFE=2.04, MAE=-0.88, acct=1.54% | n=32, R=-0.335, PF=0.56, win=34%, MFE=1.54, MAE=-1.05, acct=-4.90% |
| TREND_PULLBACK_SHORT | n=35, R=-0.277, PF=0.59, win=40%, MFE=1.31, MAE=-0.94, acct=-4.69% | n=41, R=-0.211, PF=0.69, win=39%, MFE=1.65, MAE=-0.91, acct=-4.08% |

## 7. val_2024 comparison

| metric | CONTROL | ZONE_ENTRY |
|---|---|---|
| trades | 107 | 154 |
| episodes | 210 | 181 |
| setup->entry | 51.0% | 85.1% |
| exp. R (net) | 0.007 | -0.011 |
| exp. R (gross) | 0.145 | 0.144 |
| t | 0.05 | -0.09 |
| PF | 1.01 | 0.98 |
| win rate | 44.9% | 42.9% |
| avg win R | 1.368 | 1.473 |
| avg loss R | -1.101 | -1.124 |
| median R | -1.054 | -1.062 |
| MFE R | 2.010 | 2.057 |
| MAE R | -0.810 | -0.825 |
| max DD | -9.89% | -12.33% |
| Sharpe | 0.10 | -0.05 |
| median hold h | 24.9 | 14.4 |
| fees | -412 | -693 |
| slippage | -240 | -403 |
| funding | -92 | -112 |
| net P&L | 42 | -82 |
| net return | 0.42% | -0.82% |

### Families in val_2024

| family | CONTROL | ZONE_ENTRY |
|---|---|---|
| BREAKDOWN_SHORT | n=11, R=0.045, PF=1.09, win=55%, MFE=3.44, MAE=-0.58, acct=0.23% | n=13, R=0.378, PF=1.79, win=62%, MFE=3.81, MAE=-0.62, acct=2.54% |
| BREAKOUT_LONG | n=9, R=-0.377, PF=0.41, win=44%, MFE=1.29, MAE=-0.85, acct=-1.55% | n=10, R=-0.288, PF=0.51, win=50%, MFE=1.22, MAE=-0.81, acct=-1.41% |
| MOMENTUM_CONTINUATION_LONG | n=18, R=0.218, PF=1.44, win=50%, MFE=1.50, MAE=-0.84, acct=1.77% | n=21, R=-0.254, PF=0.63, win=33%, MFE=1.10, MAE=-0.93, acct=-2.55% |
| RESISTANCE_REJECTION_SHORT | n=16, R=-0.419, PF=0.45, win=31%, MFE=1.03, MAE=-0.91, acct=-3.15% | n=26, R=-0.342, PF=0.59, win=27%, MFE=1.67, MAE=-0.92, acct=-4.36% |
| SUPPORT_RECLAIM_LONG | n=24, R=-0.300, PF=0.60, win=33%, MFE=1.56, MAE=-0.87, acct=-3.21% | n=46, R=-0.184, PF=0.73, win=39%, MFE=1.62, MAE=-0.84, acct=-4.17% |
| TREND_PULLBACK_LONG | n=22, R=0.740, PF=2.63, win=59%, MFE=3.44, MAE=-0.71, acct=7.73% | n=28, R=0.699, PF=2.48, win=57%, MFE=3.53, MAE=-0.73, acct=9.83% |
| TREND_PULLBACK_SHORT | n=7, R=-0.382, PF=0.37, win=43%, MFE=1.28, MAE=-0.92, acct=-1.22% | n=10, R=-0.057, PF=0.89, win=50%, MFE=1.51, MAE=-0.82, acct=-0.25% |

## 8. Matched-episode analysis (same detection in both arms: key = family + detection time)

- Episodes: CONTROL 533, ZONE_ENTRY 462, matched 399, CONTROL-only 134, ZONE_ENTRY-only 63 (arm-only episodes arise because an earlier entry occupies the single slot and shifts later detections).

Outcome transition for matched episodes (CONTROL outcome -> ZONE_ENTRY outcome):

| CONTROL | ZONE_ENTRY | n |
|---|---|---|
| INVALIDATED | INVALIDATED | 12 |
| INVALIDATED | TRADED | 70 |
| NEVER_TRIGGERED | NEVER_TRIGGERED | 46 |
| NEVER_TRIGGERED | TRADED | 73 |
| TRADED | TRADED | 198 |

ZONE_ENTRY trades decomposed by what CONTROL did on the same episode (population check):

| CONTROL outcome | n | win | exp. R | median R | PF | MFE R | MAE R | target first | fees+slip / trade | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|
| INVALIDATED | 70 | 3% | -1.111 | -1.158 | 0.03 | 0.27 | -1.25 | 0% | 10.23 | -3985 |
| NEVER_TRIGGERED | 73 | 68% | 0.782 | 0.784 | 3.23 | 3.28 | -0.62 | 58% | 5.96 | 2921 |
| TRADED | 198 | 52% | 0.187 | 0.339 | 1.35 | 2.42 | -0.80 | 42% | 7.68 | 1913 |
| UNMATCHED | 50 | 38% | -0.099 | -1.081 | 0.86 | 1.93 | -0.99 | 26% | 9.92 | -270 |

### Episodes traded by BOTH arms (timing effect only, identical plan)

| metric | CONTROL | ZONE_ENTRY |
|---|---|---|
| pairs | 198 | 198 |
| mean realised R | 0.101 | 0.187 |
| win rate | 48.0% | 51.5% |
| mean stop distance | 1.55% | 1.42% |
| mean MFE R / MAE R | 2.097 / -0.836 | 2.418 / -0.801 |
| sum net P&L | 1024 | 1913 |

- Paired difference (ZONE_ENTRY - CONTROL) in R: mean 0.086, paired t = 1.70, ZONE_ENTRY better in 52% of pairs.
- Timing: ZONE_ENTRY entered earlier in 100% of pairs, mean 1.3 h (median 0.8 h) earlier; better entry price in 73% of pairs, mean price improvement 0.13% of entry price (positive = cheaper for longs / higher for shorts).
- Structural target reached before invalidation (plan label, identical for both arms): 42% of pairs.

By family (pairs):

| family | pairs | CONTROL mean R | ZONE mean R | mean h earlier | mean price impr. |
|---|---|---|---|---|---|
| BREAKDOWN_SHORT | 15 | 0.520 | 0.760 | 0.4 | 0.31% |
| BREAKOUT_LONG | 18 | 0.324 | 0.322 | 0.7 | 0.14% |
| MOMENTUM_CONTINUATION_LONG | 24 | -0.127 | -0.119 | 0.7 | 0.09% |
| RESISTANCE_REJECTION_SHORT | 44 | -0.068 | 0.084 | 1.6 | 0.08% |
| SUPPORT_RECLAIM_LONG | 37 | 0.081 | 0.240 | 1.6 | 0.10% |
| TREND_PULLBACK_LONG | 32 | 0.677 | 0.490 | 1.5 | 0.12% |
| TREND_PULLBACK_SHORT | 28 | -0.440 | -0.201 | 1.7 | 0.20% |

First 25 matched pairs (full table in `matched_pairs.parquet`):

| family | side | CONTROL entry | ZONE entry | h earlier | price impr. | stop% C/Z | MFE R C/Z | MAE R C/Z | R C/Z | target first |
|---|---|---|---|---|---|---|---|---|---|---|
| TREND PULLBACK SHORT | SHORT | 2022-01-02 08:35 | 2022-01-02 08:05 | 0.5 | -0.12% | 1.85%/1.97% | 0.14/0.11 | -1.00/-1.00 | -1.08/-1.07 | no |
| RESISTANCE REJECTION SHORT | SHORT | 2022-01-12 01:15 | 2022-01-12 00:05 | 1.2 | 0.51% | 1.67%/1.16% | 0.26/0.82 | -1.18/-1.26 | -1.08/-1.12 | no |
| TREND PULLBACK SHORT | SHORT | 2022-01-15 12:10 | 2022-01-15 12:05 | 0.1 | 0.07% | 1.56%/1.49% | 0.58/0.66 | -1.11/-1.11 | -1.10/-1.10 | yes |
| RESISTANCE REJECTION SHORT | SHORT | 2022-01-15 23:00 | 2022-01-15 22:05 | 0.9 | 0.10% | 1.71%/1.60% | 2.65/2.89 | -0.34/-0.29 | 1.15/1.23 | yes |
| TREND PULLBACK SHORT | SHORT | 2022-01-19 15:50 | 2022-01-19 12:10 | 3.7 | 0.61% | 2.41%/1.79% | 0.29/0.73 | -1.09/-1.12 | -1.05/-1.07 | no |
| SUPPORT RECLAIM LONG | LONG | 2022-02-12 17:50 | 2022-02-12 17:05 | 0.8 | -0.10% | 2.66%/2.76% | 0.27/0.22 | -1.00/-1.00 | -1.06/-1.05 | no |
| TREND PULLBACK SHORT | SHORT | 2022-02-21 18:05 | 2022-02-21 17:05 | 1.0 | 1.31% | 4.13%/2.78% | 2.47/2.21 | -0.67/-0.12 | 1.33/1.20 | yes |
| TREND PULLBACK LONG | LONG | 2022-03-30 16:20 | 2022-03-30 15:50 | 0.5 | 0.24% | 1.82%/1.58% | 0.53/0.77 | -1.19/-1.23 | -1.09/-1.11 | yes |
| MOMENTUM CONTINUATION LONG | LONG | 2022-04-01 16:45 | 2022-04-01 16:05 | 0.7 | -0.26% | 0.85%/1.11% | 0.00/0.08 | -1.33/-1.25 | -1.18/-1.13 | no |
| SUPPORT RECLAIM LONG | LONG | 2022-04-06 05:10 | 2022-04-06 02:05 | 3.1 | 0.27% | 2.67%/2.41% | 0.11/0.23 | -1.08/-1.09 | -1.06/-1.07 | no |
| RESISTANCE REJECTION SHORT | SHORT | 2022-04-10 22:15 | 2022-04-10 22:05 | 0.2 | 0.15% | 2.22%/2.07% | 3.64/3.98 | -0.07/-0.05 | 2.13/2.20 | yes |
| TREND PULLBACK SHORT | SHORT | 2022-04-17 07:35 | 2022-04-17 06:05 | 1.5 | 0.17% | 1.31%/1.13% | 3.42/4.10 | -0.58/-0.52 | 1.57/1.63 | yes |
| RESISTANCE REJECTION SHORT | SHORT | 2022-04-19 22:50 | 2022-04-19 21:05 | 1.7 | -0.19% | 1.45%/1.64% | 0.22/0.07 | -1.02/-1.02 | -1.09/-1.08 | no |
| RESISTANCE REJECTION SHORT | SHORT | 2022-04-20 14:25 | 2022-04-20 14:05 | 0.3 | 0.04% | 1.85%/1.80% | 0.98/1.02 | -1.24/-1.24 | -1.08/-1.08 | no |
| RESISTANCE REJECTION SHORT | SHORT | 2022-04-21 16:30 | 2022-04-21 16:15 | 0.2 | 0.48% | 1.32%/0.83% | 4.98/8.46 | -0.30/-0.14 | 2.55/3.33 | yes |
| TREND PULLBACK SHORT | SHORT | 2022-04-24 20:20 | 2022-04-24 20:05 | 0.2 | 0.38% | 1.43%/1.04% | 2.47/3.73 | -0.44/-0.24 | 0.50/1.38 | yes |
| RESISTANCE REJECTION SHORT | SHORT | 2022-04-26 14:20 | 2022-04-26 14:05 | 0.2 | 0.47% | 1.61%/1.14% | 2.96/4.58 | -0.20/-0.18 | 0.66/1.62 | yes |
| TREND PULLBACK SHORT | SHORT | 2022-04-27 18:40 | 2022-04-27 18:05 | 0.6 | 0.36% | 1.90%/1.54% | 0.40/0.72 | -1.01/-1.01 | -1.08/-1.10 | no |
| TREND PULLBACK SHORT | SHORT | 2022-04-29 08:35 | 2022-04-29 08:05 | 0.5 | 0.50% | 1.78%/1.27% | 2.91/4.43 | -0.11/-0.05 | 1.55/2.24 | yes |
| TREND PULLBACK SHORT | SHORT | 2022-05-02 23:50 | 2022-05-02 22:35 | 1.2 | 0.24% | 2.33%/2.08% | 1.13/1.38 | -1.08/-1.09 | -1.04/-1.05 | yes |
| RESISTANCE REJECTION SHORT | SHORT | 2022-05-04 17:35 | 2022-05-04 17:05 | 0.5 | 0.69% | 1.87%/1.17% | 0.42/1.25 | -1.09/-1.15 | -1.08/-1.13 | no |
| RESISTANCE REJECTION SHORT | SHORT | 2022-07-30 19:25 | 2022-07-30 14:05 | 5.3 | 0.29% | 1.86%/1.57% | 1.91/2.44 | -0.14/-0.42 | 1.11/1.31 | yes |
| RESISTANCE REJECTION SHORT | SHORT | 2022-08-03 21:50 | 2022-08-03 21:05 | 0.8 | 0.21% | 1.59%/1.37% | 2.50/3.04 | -0.01/-0.01 | 0.63/1.53 | yes |
| RESISTANCE REJECTION SHORT | SHORT | 2022-08-07 20:45 | 2022-08-07 18:05 | 2.7 | 0.11% | 0.71%/0.60% | 0.00/0.76 | -1.09/-1.11 | -1.21/-1.25 | no |
| SUPPORT RECLAIM LONG | LONG | 2022-08-10 05:55 | 2022-08-10 02:05 | 3.8 | 0.06% | 1.69%/1.64% | 5.33/5.55 | -0.23/-0.40 | 2.45/2.49 | yes |

## 9. Never-triggered recovery (Phase 2 CONTROL episodes classified NEVER_TRIGGERED)

- CONTROL never-triggered episodes: 152 (mean signed 24h fwd return 1.13%, target-before-invalidation 66%); matched in ZONE_ENTRY: 119.
- ZONE_ENTRY outcome for those episodes: NEVER_TRIGGERED=46, TRADED=73.

| recovered trades | n | win | exp. R net | exp. R gross | median R | PF | MFE R | MAE R | target first | fees+slip / trade | funding / trade | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 73 | 68% | 0.782 | 0.869 | 0.784 | 3.23 | 3.28 | -0.62 | 58% | 5.96 | -0.48 | 2921 |

Mean signed 24h forward return of the recovered episodes (from the plan label): 0.86%.

By family:

| family | n | win | exp. R | PF | target first | sum P&L |
|---|---|---|---|---|---|---|
| BREAKDOWN_SHORT | 3 | 100% | 2.101 | inf | 100% | 320 |
| BREAKOUT_LONG | 2 | 100% | 2.322 | inf | 50% | 237 |
| MOMENTUM_CONTINUATION_LONG | 3 | 33% | -0.383 | 0.45 | 33% | -58 |
| RESISTANCE_REJECTION_SHORT | 22 | 86% | 1.608 | 11.23 | 55% | 1823 |
| SUPPORT_RECLAIM_LONG | 17 | 76% | 0.651 | 3.28 | 47% | 558 |
| TREND_PULLBACK_LONG | 15 | 33% | -0.319 | 0.57 | 47% | -242 |
| TREND_PULLBACK_SHORT | 11 | 64% | 0.509 | 2.27 | 91% | 282 |

By CONTROL end reason:

| CONTROL end reason | n | win | exp. R | PF | sum P&L |
|---|---|---|---|---|---|
| RAN_WITHOUT_US | 17 | 71% | 0.987 | 4.11 | 852 |
| REGIME_INELIGIBLE | 5 | 60% | 2.110 | 5.95 | 557 |
| WATCH_TIMEOUT | 51 | 69% | 0.583 | 2.64 | 1511 |

## 10. Family-level results (combined; no family disabled)

| family | CONTROL | ZONE_ENTRY |
|---|---|---|
| BREAKDOWN_SHORT | n=20, R=0.768, PF=2.96, win=65%, MFE=4.58, MAE=-0.57, acct=7.49% | n=23, R=0.577, PF=2.37, win=65%, MFE=3.86, MAE=-0.57, acct=6.62% |
| BREAKOUT_LONG | n=20, R=0.261, PF=1.44, win=50%, MFE=3.15, MAE=-0.82, acct=2.75% | n=32, R=-0.070, PF=0.90, win=44%, MFE=2.37, MAE=-0.84, acct=-1.23% |
| MOMENTUM_CONTINUATION_LONG | n=32, R=-0.053, PF=0.91, win=44%, MFE=1.33, MAE=-0.88, acct=-0.96% | n=33, R=-0.321, PF=0.54, win=33%, MFE=1.08, MAE=-0.90, acct=-5.05% |
| RESISTANCE_REJECTION_SHORT | n=55, R=-0.071, PF=0.88, win=47%, MFE=1.49, MAE=-0.95, acct=-1.72% | n=95, R=0.172, PF=1.27, win=46%, MFE=2.41, MAE=-0.91, acct=8.29% |
| SUPPORT_RECLAIM_LONG | n=53, R=0.012, PF=1.02, win=43%, MFE=1.84, MAE=-0.87, acct=0.48% | n=97, R=-0.052, PF=0.92, win=42%, MFE=1.78, MAE=-0.87, acct=-2.48% |
| TREND_PULLBACK_LONG | n=53, R=0.358, PF=1.65, win=51%, MFE=2.62, MAE=-0.81, acct=9.26% | n=60, R=0.148, PF=1.24, win=45%, MFE=2.47, MAE=-0.90, acct=4.93% |
| TREND_PULLBACK_SHORT | n=42, R=-0.295, PF=0.56, win=40%, MFE=1.31, MAE=-0.94, acct=-5.91% | n=51, R=-0.181, PF=0.73, win=41%, MFE=1.63, MAE=-0.90, acct=-4.33% |

## 11. LONG vs SHORT

| side | CONTROL | ZONE_ENTRY |
|---|---|---|
| LONG | n=158, R=0.146, PF=1.25, win=47% | n=222, R=-0.041, PF=0.94, win=42% |
| SHORT | n=117, R=-0.008, PF=0.98, win=48% | n=169, R=0.121, PF=1.20, win=47% |

dev_2022_2023:

| side | CONTROL | ZONE_ENTRY |
|---|---|---|
| LONG | n=85, R=0.159, PF=1.27, win=47% | n=117, R=-0.102, PF=0.85, win=40% |
| SHORT | n=83, R=0.096, PF=1.17, win=51% | n=120, R=0.208, PF=1.36, win=50% |

val_2024:

| side | CONTROL | ZONE_ENTRY |
|---|---|---|
| LONG | n=73, R=0.132, PF=1.23, win=47% | n=105, R=0.027, PF=1.05, win=44% |
| SHORT | n=34, R=-0.261, PF=0.60, win=41% | n=49, R=-0.093, PF=0.86, win=41% |

## 12. MFE / MAE

| metric | CONTROL | ZONE_ENTRY |
|---|---|---|
| mean MFE (R) | 2.074 | 2.133 |
| mean MAE (R) | -0.862 | -0.870 |
| worst MAE (R) | -6.768 | -6.774 |
| realised mean winner (R) | 1.421 | 1.509 |
| counterfactual 1R reached before initial stop | 56.4% | 51.9% |
| counterfactual 1.5R reached before initial stop | 46.5% | 43.7% |
| counterfactual 2R reached before initial stop | 33.5% | 33.2% |
| counterfactual 3R reached before initial stop | 16.4% | 18.9% |
| TP1 executed | 46.5% | 43.7% |

## 13. Fees / slippage / funding impact (USDT, combined)

| component | CONTROL | ZONE_ENTRY |
|---|---|---|
| trades | 275 | 391 |
| gross P&L before slippage | 3158 | 3895 |
| slippage | -697 | -1162 |
| fees | -1211 | -2006 |
| funding | -117 | -148 |
| net P&L | 1133 | 579 |
| gross expectancy (R, before slippage) | 0.226 | 0.195 |
| net expectancy (R) | 0.081 | 0.029 |
| cost drag per trade (R) | 0.145 | 0.166 |
| total cost (slippage + fees - funding) | 2024 | 3316 |

## 14. Account return / drawdown (sequential, one position, fixed research equity for sizing)

| metric | CONTROL | ZONE_ENTRY |
|---|---|---|
| final equity | 11133.31 | 10578.78 |
| total return | 11.33% | 5.79% |
| CAGR | 3.64% | 1.89% |
| max drawdown (trade curve / daily mtm) | -8.96% / -8.74% | -12.07% / -11.85% |
| Sharpe / Sortino (daily marks) | 0.66 / 0.94 | 0.29 / 0.40 |
| longest losing streak | 5 | 10 |
| trades / week | 1.76 | 2.50 |
| median hours between entries | 66.2 | 42.0 |

## 15. Null benchmark comparison (K random geometry-matched entries per trade; time- and regime-matched)

| series | n | mean R | win rate | strategy - null (R) | z vs replicate means | P(null >= strategy) |
|---|---|---|---|---|---|---|
| CONTROL strategy | 275 | 0.081 | 47.3% |  |  |  |
| CONTROL null (time-matched) | 5500 | -0.058 | 41.0% | 0.138 | z=1.99 | 0% |
| CONTROL null (regime-matched) | 5500 | -0.038 | 41.9% | 0.119 | z=1.49 | 10% |
| ZONE_ENTRY strategy | 391 | 0.029 | 44.2% |  |  |  |
| ZONE_ENTRY null (time-matched) | 7820 | -0.120 | 39.6% | 0.149 | z=2.20 | 0% |
| ZONE_ENTRY null (regime-matched) | 7820 | -0.093 | 40.3% | 0.122 | z=1.36 | 10% |

dev_2022_2023:

| series | n | mean R | win rate | strategy - null (R) | z | P(null >= strat) |
|---|---|---|---|---|---|---|
| CONTROL strategy | 168 | 0.128 | 48.8% |  |  |  |
| CONTROL null (time-matched) | 3360 | -0.137 | 39.0% | 0.265 | z=2.43 | 0% |
| CONTROL null (regime-matched) | 3360 | -0.064 | 41.8% | 0.192 | z=1.83 | 5% |
| ZONE_ENTRY strategy | 237 | 0.055 | 45.1% |  |  |  |
| ZONE_ENTRY null (time-matched) | 4740 | -0.157 | 38.8% | 0.212 | z=1.99 | 5% |
| ZONE_ENTRY null (regime-matched) | 4740 | -0.133 | 40.1% | 0.187 | z=2.00 | 5% |

val_2024:

| series | n | mean R | win rate | strategy - null (R) | z | P(null >= strat) |
|---|---|---|---|---|---|---|
| CONTROL strategy | 107 | 0.007 | 44.9% |  |  |  |
| CONTROL null (time-matched) | 2140 | -0.030 | 42.6% | 0.037 | z=0.27 | 45% |
| CONTROL null (regime-matched) | 2140 | 0.008 | 41.6% | -0.001 | z=-0.01 | 50% |
| ZONE_ENTRY strategy | 154 | -0.011 | 42.9% |  |  |  |
| ZONE_ENTRY null (time-matched) | 3080 | -0.115 | 39.9% | 0.104 | z=1.17 | 15% |
| ZONE_ENTRY null (regime-matched) | 3080 | -0.066 | 40.3% | 0.056 | z=0.46 | 30% |

## 16. Uncertainty

- Standard error of mean R: CONTROL 0.088 (n=275), ZONE_ENTRY 0.078 (n=391). Approximate 95% intervals: CONTROL [-0.091, 0.252], ZONE_ENTRY [-0.125, 0.183]. The intervals overlap heavily; neither arm is distinguishable from zero expectancy at conventional levels.
- The paired comparison on 198 shared episodes is the cleanest timing estimate: mean difference 0.086R, paired t = 1.70.
- dev_2022_2023: CONTROL 0.128R (n=168, t=1.12) vs ZONE_ENTRY 0.055R (n=237, t=0.53).
- val_2024: CONTROL 0.007R (n=107, t=0.05) vs ZONE_ENTRY -0.011R (n=154, t=-0.09).
- Per-family cells are small (most n < 60); family-level differences are directional evidence only.
- The two arms share the same data, costs and exit engine, so differences are not data noise, but a single 3-year window of one instrument cannot establish generality.
- The forward labels and the null benchmark are themselves estimated on the same window (no out-of-sample confirmation yet).

## 17. Recommendation (not implemented)

Pre-declared success criteria (set in the hypothesis, not after the results):

| criterion | met | evidence |
|---|---|---|
| materially stronger NET expectancy (> +0.05R over CONTROL) | no | 0.081 -> 0.029 |
| no unacceptable drawdown increase (<= 1.25x CONTROL + 1pt) | yes | 8.96% -> 12.07% |
| positive and better than CONTROL in BOTH segments | no | dev_2022_2023: 0.128 -> 0.055; val_2024: 0.007 -> -0.011 |
| recovered never-triggered setups have positive net expectancy | yes | n=73, R=0.782 |
| timing effect on shared episodes is positive | yes | paired diff 0.086R (t=1.70) on 198 pairs |


- **Verdict: H1 PARTIALLY SUPPORTED. The variant improves some pre-declared criteria but not all; it must not be adopted on this evidence. Recommended next step: owner review of which criterion failed and why (sections 8, 9, 13), then either (a) stop here, or (b) pre-register a second, narrower hypothesis derived from the matched analysis (for example confirmation-free entry only where the paired timing effect is positive) for one more run on 2022-2024. No parameter search, no family dropped.**
- Where the ZONE_ENTRY result comes from (its trades split by what CONTROL did on the same episode): INVALIDATED: n=70, -1.111R, -3985 USDT; NEVER_TRIGGERED: n=73, 0.782R, 2921 USDT; TRADED: n=198, 0.187R, 1913 USDT; UNMATCHED: n=50, -0.099R, -270 USDT. Reading: the confirmation trigger acts mainly as a FILTER against plans that fail after reaching the zone (the CONTROL-INVALIDATED group), not only as a timing delay; zone entry gains on shared episodes and on recovered never-triggered plans, and gives most of it back on the group CONTROL would have filtered out.
- Drawdown note: the drawdown criterion is met only at the margin (8.96% -> 12.07%, limit 12.20%); the longest losing streak doubled (5 -> 10).
- 2025+ stays untouched. No live trading. No tuning.

## Appendix — frozen configuration (CONTROL; ZONE_ENTRY differs only in `experiment.entry_mode`)

```yaml
backtest:
  fill_rule: next_bar_open
  information_mode: MARKET_AS_OF
  same_bar_stop_and_target: stop_first
  warmup_days: 60
costs:
  apply_funding: true
  default_funding_interval_hours: 8
  entry_slippage_bps: 2.0
  maker_fee_bps: 2.0
  stop_slippage_bps: 5.0
  taker_fee_bps: 5.0
  use_maker_for_targets: false
data:
  base_timeframe: 5m
  ingest_funding: true
  ingest_mark_price: true
  ingest_metrics: true
  ingest_native_timeframes:
  - 1h
  - 4h
  - 1d
  ingest_premium_index: true
  ingest_spot: false
  latency_minutes: 0
  provider: binance_vision
episode:
  anchor_tolerance_atr: 0.25
  cooldown_bars_after_close: 12
  cooldown_bars_after_invalidation: 6
  dedupe_same_anchor: true
  entry_ready_timeout_bars: 24
  max_concurrent_positions: 1
  watch_timeout_bars: 96
exits:
  breakeven_after_tp1: true
  evaluate_r_levels:
  - 1.0
  - 1.5
  - 2.0
  - 3.0
  max_hold_hours: 240
  regime_exit: false
  tp1_frac: 0.4
  tp1_r: 1.5
  tp2_frac: 0.3
  tp2_r: 3.0
  trail_atr_buffer: 0.5
  trail_method: structure_atr
  trail_tf: 1h
experiment:
  entry_mode: CONFIRMED_TRIGGER
indicators:
  atr_period: 14
  donchian_period: 20
  ema_fast: 20
  ema_slow: 50
  ema_trend: 200
  min_bars:
    15m: 220
    1d: 60
    1h: 220
    4h: 220
    5m: 220
  swing_k: 3
instrument:
  market: binance_um_perp
  quote: USDT
  spot_symbol: BTCUSDT
  symbol: BTCUSDT
regime:
  breakout_atr_expansion: 1.25
  breakout_lookback_bars: 3
  high_vol_atr_pct: 0.055
  low_vol_atr_pct: 0.018
  range_ema_band_atr: 1.0
  trend_slope_bars: 5
research:
  min_cell_n: 20
  sharpe_periods_per_year: 365
risk:
  allowed_leverage:
  - 1.0
  - 2.0
  - 3.0
  - 5.0
  - 10.0
  compounding: false
  initial_equity: 10000.0
  liquidation_atr_tf: 4h
  maintenance_margin_rate: 0.005
  margin_cap_frac: 0.25
  max_leverage: 10.0
  min_liquidation_distance_atr: 3.0
  min_stop_to_liquidation_ratio: 3.0
  risk_per_trade: 0.005
setups:
  breakout:
    confirm_tf: 15m
    entry_tf: 5m
    level_tf: 4h
    max_extension_atr: 2.0
    max_stop_atr: 3.0
    min_expansion: 1.2
    min_stop_atr: 0.5
    setup_tf: 1h
    stop_atr_buffer: 0.5
    zone_inner_atr: 0.25
    zone_outer_atr: 0.75
  eligible_regimes:
    BREAKDOWN_SHORT:
    - BREAKOUT_REGIME
    - RANGE
    - LOW_VOLATILITY
    BREAKOUT_LONG:
    - BREAKOUT_REGIME
    - RANGE
    - LOW_VOLATILITY
    MOMENTUM_CONTINUATION_LONG:
    - TREND_UP
    - BREAKOUT_REGIME
    MOMENTUM_CONTINUATION_SHORT:
    - TREND_DOWN
    - BREAKOUT_REGIME
    RESISTANCE_REJECTION_SHORT:
    - RANGE
    - TREND_DOWN
    SUPPORT_RECLAIM_LONG:
    - RANGE
    - TREND_UP
    TREND_PULLBACK_LONG:
    - TREND_UP
    TREND_PULLBACK_SHORT:
    - TREND_DOWN
  enabled:
  - TREND_PULLBACK_LONG
  - TREND_PULLBACK_SHORT
  - BREAKOUT_LONG
  - BREAKDOWN_SHORT
  - SUPPORT_RECLAIM_LONG
  - RESISTANCE_REJECTION_SHORT
  - MOMENTUM_CONTINUATION_LONG
  - MOMENTUM_CONTINUATION_SHORT
  momentum_continuation:
    confirm_tf: 15m
    entry_tf: 5m
    flag_max_age_bars: 3
    flag_max_atr: 0.6
    flag_min_atr: 0.2
    impulse_close_location: 0.7
    impulse_min_atr: 1.5
    impulse_tf: 4h
    max_stop_atr: 4.0
    min_stop_atr: 0.5
    setup_tf: 1h
    stop_atr_buffer: 0.5
  support_reclaim:
    confirm_tf: 15m
    entry_tf: 5m
    level_max_age_bars: 90
    level_tf: 4h
    max_stop_atr: 3.0
    min_stop_atr: 0.5
    setup_tf: 1h
    stop_atr_buffer: 0.5
    sweep_min_atr: 0.1
    sweep_window_bars: 6
    zone_inner_atr: 0.25
    zone_outer_atr: 0.75
  trend_pullback:
    confirm_tf: 15m
    entry_tf: 5m
    max_stop_atr: 4.0
    min_pullback_atr: 1.0
    min_stop_atr: 0.75
    setup_tf: 1h
    stop_atr_buffer: 0.5
    structural_target_lookback: 50
    trend_tf: 4h
    zone_lower_ema: ema_slow
    zone_pad_atr: 0.25
    zone_upper_ema: ema_fast
strategy_name: btc_leveraged_swing_v1
```
