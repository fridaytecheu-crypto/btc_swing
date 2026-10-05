# BTC Swing V1 — Phase 2.2: EARLY ZONE ENTRY + CONFIRMATION-BASED EARLY EXIT

Generated 2026-10-05 09:31 UTC · period 2022-01-01 00:00 -> 2025-01-01 00:00 UTC · CONTROL result hash `92ebe5d7fc65` · ZONE_ENTRY_CONFIRM_EXIT result hash `b7c691887c32` · code `c7c2ccfc0660`

**Central question.** Can we capture the timing advantage of entering at the plan zone while using the existing confirmation logic as an early risk filter rather than as a prerequisite for entry?

Paper/backtest only. No live trading, no authenticated exchange access, no real money. 2025+ data untouched.

## 1. Hypothesis

Phase 2.1 showed that entering at the pre-defined zone improves timing on shared setups (+0.086R paired on 198 episodes) and recovers profitable never-triggered plans (73 trades, +0.78R), but admits plans that fail right after reaching the zone (70 trades CONTROL had classified INVALIDATED, -1.11R each), and those losses dominate. Pre-registered hypothesis H2: entering at the zone and then treating the ORIGINAL 15m confirmation as an early risk filter (exit if it does not arrive within the existing confirmation-lifecycle timeout, or if the plan's invalidation level is breached first) keeps most of the timing gain and the recovered opportunities while removing most of the early failures, giving net expectancy meaningfully above CONTROL or at least clearly above Phase 2.1 ZONE_ENTRY, materially lower drawdown than ZONE_ENTRY, and no clear breakdown in 2024.

## 2. Frozen baseline verification

| check | value |
|---|---|
| Phase 2 validation result hash | `92ebe5d7fc65fc978ba4d3d222723e30c31d1db26e74d6e6585c786528c4ea56` |
| CONTROL result hash (this run) | `92ebe5d7fc65fc978ba4d3d222723e30c31d1db26e74d6e6585c786528c4ea56` |
| identical | yes |
| CONTROL trades / episodes | 275 / 533 |
| config hash CONTROL / variant | `871a444e107e` / `ef9aa2c56b49` (differ only in `experiment.entry_mode`) |
| row-level check vs persisted Phase 2 trades | identical: 275 trades, 89 columns compared |

## 3. Exact variant definition

- Entry: identical to Phase 2.1 ZONE_ENTRY — WATCH -> TRIGGERED on the first completed 5m bar that reaches the frozen plan zone while the plan is valid; fill at the next 5m open plus slippage. Sizing, leverage, stop, TP1/TP2, breakeven, structural trail, time cap, fees, slippage, funding, mark-price liquidation: unchanged and active from entry.
- Confirmation predicate: the Phase 2 `ZoneSetupDetector.confirmed()` test, unchanged (completed 15m bar closes back through its EMA20 in the trade direction, bar in the trade direction, close not further than one zone pad beyond the zone). It is evaluated at the zone-reached decision (if already true, the trade starts confirmed) and at every later 5m close while unconfirmed.
- Confirmation window: the existing explicit confirmation-lifecycle timeout `episode.entry_ready_timeout_bars` = 24 five-minute bars (2 h), reused unchanged and counted from the entry bar. This is the only short timeout in the frozen state machine (the time CONTROL allows between confirmation and entry); the alternative, the 96-bar watch timeout, would let an unconfirmed position run for up to 8 h and was not tested. No other window was tried.
- Early exits (before confirmation only): (a) a completed 5m close beyond the plan's invalidation level (the Phase 2 `LEVEL_BREACHED` test, unchanged) -> exit at the next 5m open with stop-type slippage (`EARLY_EXIT_INVALIDATION`); (b) no confirmation by the deadline -> exit at the next 5m open with stop-type slippage (`EARLY_EXIT_NO_CONFIRMATION`). Once confirmed, nothing differs from CONTROL's lifecycle.
- Quirk kept deliberately (same predicate, no new rule): the confirmation test requires price to be within one pad of the zone, so a position that runs far in its favour immediately may fail to 'confirm' and be exited at the deadline with a profit; these cases are counted and shown.

## 4. PIT audit

- bar visible iff close_time <= t; zone test, confirmation test and invalidation test use completed bars at t; fills at the next 5m open. Zone: fixed at detection (SetupPlan frozen); never moved. Confirmation: the Phase 2 ZoneSetupDetector.confirmed() predicate, unchanged.
- Deterministic rerun: CONTROL yes, variant yes.
- Truncation audit (variant): decisions up to 2023-07-03T00:00 identical with later data removed: yes (157825 rows).
- At entry the engine cannot know whether confirmation will arrive; the position is real (margin, fees, funding, stop, liquidation) from the zone fill until confirmation, early exit or a normal exit.

## 5. Overall comparison (combined 2022-01 -> 2024-12)

| metric | CONTROL | ZONE_ENTRY |
|---|---|---|
| trades | 275 | 514 |
| episodes | 533 | 603 |
| setup->entry | 51.6% | 85.2% |
| exp. R (net) | 0.081 | -0.056 |
| exp. R (gross) | 0.226 | 0.114 |
| t | 0.92 | -1.10 |
| PF | 1.14 | 0.89 |
| win rate | 47.3% | 42.2% |
| avg win R | 1.421 | 1.007 |
| avg loss R | -1.121 | -0.832 |
| median R | -1.043 | -0.185 |
| MFE R | 2.074 | 1.349 |
| MAE R | -0.862 | -0.655 |
| max DD | -8.96% | -23.47% |
| Sharpe | 0.66 | -0.64 |
| median hold h | 22.6 | 2.1 |
| fees | -1211 | -2638 |
| slippage | -697 | -1656 |
| funding | -117 | -140 |
| net P&L | 1133 | -1439 |
| net return | 11.33% | -14.39% |

| variant lifecycle counts | n | share of trades |
|---|---|---|
| confirmed at entry (confirmation already true at the zone bar) | 99 | 19% |
| confirmed after entry | 167 | 32% |
| early exit: no confirmation by deadline | 183 | 36% |
| early exit: invalidation before confirmation | 33 | 6% |
| unconfirmed, closed by a normal exit (stop/TP) inside the window | 32 | 6% |

Reference — Phase 2.1 ZONE_ENTRY on the same window (from its persisted summary):

| metric | Phase 2.1 ZONE_ENTRY | Phase 2.2 variant | CONTROL |
|---|---|---|---|
| trades | 391 | 514 | 275 |
| net expectancy (R) | 0.029 | -0.056 | 0.081 |
| profit factor | 1.05 | 0.89 | 1.14 |
| max drawdown | -12.07% | -23.47% | -8.96% |
| net P&L | 579 | -1439 | 1133 |
| cost drag per trade (R) | 0.166 | 0.169 | 0.145 |
| CONTROL-INVALIDATED group entered | n=70, -1.111R, -3985 USDT | n=79, -0.874R, -3513 USDT | — |

## 6. dev_2022_2023

| metric | CONTROL | ZONE_ENTRY |
|---|---|---|
| trades | 168 | 315 |
| episodes | 323 | 370 |
| setup->entry | 52.0% | 85.1% |
| exp. R (net) | 0.128 | -0.065 |
| exp. R (gross) | 0.277 | 0.114 |
| t | 1.12 | -1.02 |
| PF | 1.22 | 0.87 |
| win rate | 48.8% | 42.2% |
| avg win R | 1.451 | 0.988 |
| avg loss R | -1.134 | -0.835 |
| median R | -1.041 | -0.188 |
| MFE R | 2.114 | 1.260 |
| MAE R | -0.895 | -0.655 |
| max DD | -3.56% | -12.48% |
| Sharpe | 0.97 | -0.75 |
| median hold h | 22.4 | 2.1 |
| fees | -799 | -1733 |
| slippage | -456 | -1100 |
| funding | -25 | -33 |
| net P&L | 1092 | -1031 |
| net return | 10.92% | -10.31% |

Phase 2.1 ZONE_ENTRY in dev_2022_2023: n=237, expectancy 0.055R, max DD -6.60%.

### Families in dev_2022_2023

| family | CONTROL | ZONE_ENTRY |
|---|---|---|
| BREAKDOWN_SHORT | n=9, R=1.651, PF=7.83, win=78%, MFE=5.98, MAE=-0.55, acct=7.26% | n=11, R=0.864, PF=5.04, win=82%, MFE=3.78, MAE=-0.38, acct=4.92% |
| BREAKOUT_LONG | n=11, R=0.783, PF=2.40, win=55%, MFE=4.66, MAE=-0.80, acct=4.31% | n=22, R=-0.117, PF=0.84, win=36%, MFE=2.26, MAE=-0.82, acct=-1.43% |
| MOMENTUM_CONTINUATION_LONG | n=14, R=-0.401, PF=0.44, win=36%, MFE=1.11, MAE=-0.93, acct=-2.73% | n=18, R=-0.110, PF=0.82, win=44%, MFE=1.30, MAE=-0.78, acct=-1.17% |
| MOMENTUM_CONTINUATION_SHORT | n=0 | n=1, R=-0.045, PF=0.00, win=0%, MFE=0.42, MAE=-0.15, acct=-0.02% |
| RESISTANCE_REJECTION_SHORT | n=39, R=0.072, PF=1.13, win=54%, MFE=1.68, MAE=-0.97, acct=1.42% | n=79, R=0.019, PF=1.03, win=41%, MFE=1.28, MAE=-0.68, acct=0.77% |
| SUPPORT_RECLAIM_LONG | n=29, R=0.269, PF=1.52, win=52%, MFE=2.07, MAE=-0.86, acct=3.69% | n=64, R=-0.063, PF=0.86, win=45%, MFE=1.05, MAE=-0.62, acct=-1.88% |
| TREND_PULLBACK_LONG | n=31, R=0.088, PF=1.14, win=45%, MFE=2.04, MAE=-0.88, acct=1.54% | n=50, R=-0.250, PF=0.54, win=32%, MFE=0.96, MAE=-0.66, acct=-6.05% |
| TREND_PULLBACK_SHORT | n=35, R=-0.277, PF=0.59, win=40%, MFE=1.31, MAE=-0.94, acct=-4.69% | n=70, R=-0.149, PF=0.68, win=44%, MFE=0.93, MAE=-0.63, acct=-5.46% |

## 7. val_2024

| metric | CONTROL | ZONE_ENTRY |
|---|---|---|
| trades | 107 | 199 |
| episodes | 210 | 233 |
| setup->entry | 51.0% | 85.4% |
| exp. R (net) | 0.007 | -0.040 |
| exp. R (gross) | 0.145 | 0.114 |
| t | 0.05 | -0.48 |
| PF | 1.01 | 0.92 |
| win rate | 44.9% | 42.2% |
| avg win R | 1.368 | 1.036 |
| avg loss R | -1.101 | -0.827 |
| median R | -1.054 | -0.171 |
| MFE R | 2.010 | 1.490 |
| MAE R | -0.810 | -0.655 |
| max DD | -9.89% | -14.74% |
| Sharpe | 0.10 | -0.48 |
| median hold h | 24.9 | 2.1 |
| fees | -412 | -905 |
| slippage | -240 | -556 |
| funding | -92 | -107 |
| net P&L | 42 | -408 |
| net return | 0.42% | -4.08% |

Phase 2.1 ZONE_ENTRY in val_2024: n=154, expectancy -0.011R, max DD -12.33%.

### Families in val_2024

| family | CONTROL | ZONE_ENTRY |
|---|---|---|
| BREAKDOWN_SHORT | n=11, R=0.045, PF=1.09, win=55%, MFE=3.44, MAE=-0.58, acct=0.23% | n=15, R=0.242, PF=1.50, win=60%, MFE=3.89, MAE=-0.61, acct=2.32% |
| BREAKOUT_LONG | n=9, R=-0.377, PF=0.41, win=44%, MFE=1.29, MAE=-0.85, acct=-1.55% | n=12, R=0.047, PF=1.13, win=67%, MFE=1.44, MAE=-0.67, acct=0.40% |
| MOMENTUM_CONTINUATION_LONG | n=18, R=0.218, PF=1.44, win=50%, MFE=1.50, MAE=-0.84, acct=1.77% | n=25, R=0.044, PF=1.09, win=48%, MFE=1.19, MAE=-0.73, acct=0.70% |
| RESISTANCE_REJECTION_SHORT | n=16, R=-0.419, PF=0.45, win=31%, MFE=1.03, MAE=-0.91, acct=-3.15% | n=32, R=-0.454, PF=0.27, win=25%, MFE=0.51, MAE=-0.75, acct=-8.85% |
| SUPPORT_RECLAIM_LONG | n=24, R=-0.300, PF=0.60, win=33%, MFE=1.56, MAE=-0.87, acct=-3.21% | n=56, R=-0.298, PF=0.51, win=30%, MFE=1.12, MAE=-0.74, acct=-9.89% |
| TREND_PULLBACK_LONG | n=22, R=0.740, PF=2.63, win=59%, MFE=3.44, MAE=-0.71, acct=7.73% | n=41, R=0.467, PF=2.67, win=51%, MFE=2.38, MAE=-0.50, acct=11.95% |
| TREND_PULLBACK_SHORT | n=7, R=-0.382, PF=0.37, win=43%, MFE=1.28, MAE=-0.92, acct=-1.22% | n=18, R=-0.074, PF=0.77, win=50%, MFE=0.80, MAE=-0.52, acct=-0.80% |

## 8. Matched episodes (same detection in both arms: key = family + detection time)

- Episodes: CONTROL 533, variant 603, matched 471, CONTROL-only 62, variant-only 132.

Outcome transition for matched episodes (CONTROL -> variant):

| CONTROL | ZONE_ENTRY_CONFIRM_EXIT | n |
|---|---|---|
| INVALIDATED | INVALIDATED | 14 |
| INVALIDATED | TRADED | 79 |
| NEVER_TRIGGERED | NEVER_TRIGGERED | 56 |
| NEVER_TRIGGERED | TRADED | 82 |
| TRADED | TRADED | 240 |

Variant trades decomposed by what CONTROL did on the same episode:

| CONTROL outcome | n | win | exp. R | median R | PF | MFE R | MAE R | target first | fees+slip / trade | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|
| INVALIDATED | 79 | 4% | -0.874 | -0.946 | 0.01 | 0.16 | -0.92 | 0% | 11.22 | -3513 |
| NEVER_TRIGGERED | 82 | 71% | 0.206 | 0.113 | 4.17 | 0.63 | -0.20 | 59% | 6.39 | 863 |
| TRADED | 240 | 45% | 0.126 | -0.143 | 1.27 | 2.04 | -0.72 | 43% | 7.77 | 1569 |
| UNMATCHED | 113 | 42% | -0.060 | -0.309 | 0.88 | 1.24 | -0.67 | 33% | 9.00 | -357 |

### Episodes traded by BOTH arms (core analysis: same plan, different entry mechanics)

| metric | CONTROL | ZONE_ENTRY_CONFIRM_EXIT |
|---|---|---|
| pairs | 240 | 240 |
| mean realised R | 0.043 | 0.126 |
| win rate | 46.2% | 45.4% |
| mean stop distance | 1.55% | 1.44% |
| mean MFE R / MAE R | 2.026 / -0.870 | 2.039 / -0.717 |
| sum net P&L | 530 | 1569 |

- Paired difference (variant - CONTROL) in R: mean 0.083, paired t = 1.49, variant better in 56% of pairs.
- Time saved: variant entered earlier in 100% of pairs, mean 1.5 h (median 0.8 h); better entry price in 71% of pairs, mean improvement 0.11% of entry price.
- Structural target reached before invalidation (plan label, identical for both arms): 43% of pairs.

By family (pairs):

| family | pairs | CONTROL mean R | variant mean R | mean h earlier | mean price impr. |
|---|---|---|---|---|---|
| BREAKDOWN_SHORT | 16 | 0.668 | 0.920 | 0.4 | 0.30% |
| BREAKOUT_LONG | 20 | 0.261 | 0.388 | 0.7 | 0.16% |
| MOMENTUM_CONTINUATION_LONG | 26 | -0.116 | -0.110 | 0.7 | 0.09% |
| RESISTANCE_REJECTION_SHORT | 49 | -0.130 | 0.056 | 1.7 | 0.09% |
| SUPPORT_RECLAIM_LONG | 45 | 0.015 | 0.042 | 1.6 | 0.05% |
| TREND_PULLBACK_LONG | 44 | 0.357 | 0.268 | 2.0 | 0.08% |
| TREND_PULLBACK_SHORT | 40 | -0.315 | -0.141 | 1.7 | 0.17% |

First 25 matched pairs (full table in `matched_pairs.parquet`):

| family | side | CONTROL entry | variant entry | h saved | price diff | confirmed | h to conf. | MFE/MAE before conf. (R) | R C/V | target first |
|---|---|---|---|---|---|---|---|---|---|---|
| TREND PULLBACK SHORT | SHORT | 2022-01-02 08:35 | 2022-01-02 08:05 | 0.5 | -0.12% | no | n/a | n/a/n/a | -1.08/-1.07 | no |
| TREND PULLBACK SHORT | SHORT | 2022-01-05 05:20 | 2022-01-05 02:20 | 3.0 | 0.04% | no | n/a | n/a/n/a | 1.69/-0.17 | yes |
| RESISTANCE REJECTION SHORT | SHORT | 2022-01-12 01:15 | 2022-01-12 00:05 | 1.2 | 0.51% | no | n/a | n/a/n/a | -1.08/-1.12 | no |
| TREND PULLBACK SHORT | SHORT | 2022-01-15 12:10 | 2022-01-15 12:05 | 0.1 | 0.07% | no | n/a | n/a/n/a | -1.10/-1.10 | yes |
| RESISTANCE REJECTION SHORT | SHORT | 2022-01-15 23:00 | 2022-01-15 22:05 | 0.9 | 0.10% | no | n/a | n/a/n/a | 1.15/1.23 | yes |
| TREND PULLBACK SHORT | SHORT | 2022-01-19 15:50 | 2022-01-19 12:10 | 3.7 | 0.61% | no | n/a | n/a/n/a | -1.05/-0.10 | no |
| SUPPORT RECLAIM LONG | LONG | 2022-02-11 06:55 | 2022-02-11 01:05 | 5.8 | -0.97% | no | n/a | n/a/n/a | -1.06/-0.43 | no |
| SUPPORT RECLAIM LONG | LONG | 2022-02-12 17:50 | 2022-02-12 17:05 | 0.8 | -0.10% | no | n/a | n/a/n/a | -1.06/-1.05 | no |
| RESISTANCE REJECTION SHORT | SHORT | 2022-02-14 18:50 | 2022-02-14 15:05 | 3.7 | 0.05% | no | n/a | n/a/n/a | -1.10/-0.39 | no |
| TREND PULLBACK SHORT | SHORT | 2022-02-21 18:05 | 2022-02-21 17:05 | 1.0 | 1.31% | no | n/a | n/a/n/a | 1.33/1.20 | yes |
| TREND PULLBACK LONG | LONG | 2022-03-30 16:20 | 2022-03-30 15:50 | 0.5 | 0.24% | no | n/a | n/a/n/a | -1.09/-1.11 | yes |
| MOMENTUM CONTINUATION LONG | LONG | 2022-04-01 16:45 | 2022-04-01 16:05 | 0.7 | -0.26% | no | n/a | n/a/n/a | -1.18/-1.13 | no |
| SUPPORT RECLAIM LONG | LONG | 2022-04-06 05:10 | 2022-04-06 02:05 | 3.1 | 0.27% | no | n/a | n/a/n/a | -1.06/-0.16 | no |
| RESISTANCE REJECTION SHORT | SHORT | 2022-04-10 22:15 | 2022-04-10 22:05 | 0.2 | 0.15% | no | n/a | n/a/n/a | 2.13/2.20 | yes |
| TREND PULLBACK SHORT | SHORT | 2022-04-17 07:35 | 2022-04-17 06:05 | 1.5 | 0.17% | no | n/a | n/a/n/a | 1.57/1.63 | yes |
| RESISTANCE REJECTION SHORT | SHORT | 2022-04-19 22:50 | 2022-04-19 21:05 | 1.7 | -0.19% | no | n/a | n/a/n/a | -1.09/-1.08 | no |
| RESISTANCE REJECTION SHORT | SHORT | 2022-04-20 14:25 | 2022-04-20 14:05 | 0.3 | 0.04% | no | n/a | n/a/n/a | -1.08/-1.08 | no |
| RESISTANCE REJECTION SHORT | SHORT | 2022-04-21 16:30 | 2022-04-21 16:15 | 0.2 | 0.48% | no | n/a | n/a/n/a | 2.55/3.33 | yes |
| TREND PULLBACK SHORT | SHORT | 2022-04-24 20:20 | 2022-04-24 20:05 | 0.2 | 0.38% | no | n/a | n/a/n/a | 0.50/1.38 | yes |
| RESISTANCE REJECTION SHORT | SHORT | 2022-04-26 14:20 | 2022-04-26 14:05 | 0.2 | 0.47% | no | n/a | n/a/n/a | 0.66/1.62 | yes |
| TREND PULLBACK SHORT | SHORT | 2022-04-27 18:40 | 2022-04-27 18:05 | 0.6 | 0.36% | no | n/a | n/a/n/a | -1.08/-1.10 | no |
| TREND PULLBACK SHORT | SHORT | 2022-04-29 08:35 | 2022-04-29 08:05 | 0.5 | 0.50% | no | n/a | n/a/n/a | 1.55/2.24 | yes |
| TREND PULLBACK SHORT | SHORT | 2022-05-02 23:50 | 2022-05-02 22:35 | 1.2 | 0.24% | no | n/a | n/a/n/a | -1.04/-1.05 | yes |
| RESISTANCE REJECTION SHORT | SHORT | 2022-05-04 17:35 | 2022-05-04 17:05 | 0.5 | 0.69% | no | n/a | n/a/n/a | -1.08/-1.13 | no |
| RESISTANCE REJECTION SHORT | SHORT | 2022-07-30 19:25 | 2022-07-30 14:05 | 5.3 | 0.29% | no | n/a | n/a/n/a | 1.11/-0.35 | yes |

## 9. Confirmation timing (variant)

| metric | value |
|---|---|
| trades | 514 |
| confirmed (any time) | 266 (52%) |
| confirmed at entry / after entry | 99 / 167 |
| hours to confirmation (after entry): mean / median / p90 | 0.66 / 0.42 / 1.50 |
| MFE / MAE before confirmation (R, confirmed-after-entry trades) | 0.197 / -0.181 |

| group | n | win | mean R | median R | PF | MFE R | MAE R | mean hold h | fees+slip/trade | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|
| confirmed (all) | 266 | 50% | 0.152 | 0.142 | 1.27 | 2.29 | -0.82 | 32.2 | 7.79 | 2058 |
| confirmed at entry | 99 | 55% | 0.255 | 0.418 | 1.51 | 2.55 | -0.77 | 30.0 | 7.87 | 1291 |
| confirmed after entry | 167 | 48% | 0.090 | -1.043 | 1.15 | 2.14 | -0.85 | 33.4 | 7.75 | 767 |
| never confirmed | 248 | 33% | -0.278 | -0.190 | 0.29 | 0.34 | -0.48 | 1.7 | 8.96 | -3497 |

By family:

| family | n | confirmed share | early exit no-conf | early exit inval | confirmed mean R | unconfirmed mean R |
|---|---|---|---|---|---|---|
| BREAKDOWN_SHORT | 26 | 88% | 3 | 0 | 0.484 | 0.669 |
| BREAKOUT_LONG | 34 | 68% | 4 | 3 | 0.172 | -0.541 |
| MOMENTUM_CONTINUATION_LONG | 43 | 86% | 4 | 1 | 0.009 | -0.204 |
| MOMENTUM_CONTINUATION_SHORT | 1 | 0% | 1 | 0 | n/a | -0.045 |
| RESISTANCE_REJECTION_SHORT | 111 | 41% | 49 | 6 | 0.104 | -0.274 |
| SUPPORT_RECLAIM_LONG | 120 | 41% | 46 | 15 | 0.115 | -0.371 |
| TREND_PULLBACK_LONG | 91 | 49% | 38 | 6 | 0.396 | -0.243 |
| TREND_PULLBACK_SHORT | 88 | 49% | 38 | 2 | -0.078 | -0.188 |

## 10. Early-exit outcomes: no confirmation within the window

| group | n | win | mean R | median R | PF | MFE R | MAE R | mean hold h | fees+slip/trade | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|
| all | 183 | 45% | 0.000 | -0.045 | 1.00 | 0.42 | -0.27 | 2.1 | 7.72 | 2 |

- Profitable share 45%; R quantiles q10=-0.47, q25=-0.27, q50=-0.04, q75=0.19, q90=0.55; fees+slippage total 1413 USDT, funding -3 USDT.

What CONTROL eventually classified the same episode as:

| CONTROL outcome | n | win | mean R | median R | PF | MFE R | MAE R | mean hold h | sum P&L |
|---|---|---|---|---|---|---|---|---|---|
| INVALIDATED | 20 | 15% | -0.344 | -0.426 | 0.11 | 0.28 | -0.47 | 2.1 | -352 |
| NEVER_TRIGGERED | 80 | 72% | 0.238 | 0.121 | 6.93 | 0.62 | -0.18 | 2.1 | 971 |
| TRADED | 44 | 16% | -0.186 | -0.190 | 0.16 | 0.21 | -0.33 | 2.1 | -417 |
| UNMATCHED | 39 | 38% | -0.100 | -0.124 | 0.49 | 0.29 | -0.30 | 2.1 | -200 |

By family:

| family | n | win | mean R | PF | sum P&L |
|---|---|---|---|---|---|
| BREAKDOWN_SHORT | 3 | 100% | 0.669 | inf | 102 |
| BREAKOUT_LONG | 4 | 75% | 0.405 | 4.56 | 83 |
| MOMENTUM_CONTINUATION_LONG | 4 | 75% | 0.116 | 5.52 | 23 |
| MOMENTUM_CONTINUATION_SHORT | 1 | 0% | -0.045 | 0.00 | -2 |
| RESISTANCE_REJECTION_SHORT | 49 | 39% | -0.007 | 0.96 | -21 |
| SUPPORT_RECLAIM_LONG | 46 | 50% | -0.004 | 0.97 | -8 |
| TREND_PULLBACK_LONG | 38 | 34% | -0.070 | 0.52 | -134 |
| TREND_PULLBACK_SHORT | 38 | 50% | -0.021 | 0.86 | -42 |

## 11. Invalidated-before-confirmation outcomes

| group | n | win | mean R | median R | PF | MFE R | MAE R | mean hold h | fees+slip/trade | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|
| all | 33 | 0% | -0.921 | -0.875 | 0.00 | 0.10 | -0.81 | 0.8 | 12.96 | -1532 |

- Profitable share 0%; R quantiles q10=-1.06, q25=-0.98, q50=-0.88, q75=-0.85, q90=-0.76; fees+slippage total 428 USDT, funding -0 USDT.

What CONTROL eventually classified the same episode as:

| CONTROL outcome | n | win | mean R | median R | PF | MFE R | MAE R | mean hold h | sum P&L |
|---|---|---|---|---|---|---|---|---|---|
| INVALIDATED | 28 | 0% | -0.908 | -0.867 | 0.00 | 0.09 | -0.82 | 0.8 | -1272 |
| UNMATCHED | 5 | 0% | -0.996 | -1.066 | 0.00 | 0.20 | -0.75 | 0.5 | -260 |

By family:

| family | n | win | mean R | PF | sum P&L |
|---|---|---|---|---|---|
| BREAKOUT_LONG | 3 | 0% | -0.967 | 0.00 | -148 |
| MOMENTUM_CONTINUATION_LONG | 1 | 0% | -0.470 | 0.00 | -24 |
| RESISTANCE_REJECTION_SHORT | 6 | 0% | -0.887 | 0.00 | -275 |
| SUPPORT_RECLAIM_LONG | 15 | 0% | -0.915 | 0.00 | -705 |
| TREND_PULLBACK_LONG | 6 | 0% | -1.051 | 0.00 | -294 |
| TREND_PULLBACK_SHORT | 2 | 0% | -0.835 | 0.00 | -86 |

Phase 2.1 reference: the CONTROL-INVALIDATED group entered by ZONE_ENTRY was n=70, -1.111R, -3985 USDT. Section 8 above shows the same group under the variant.

## 12. Recovered NEVER_TRIGGERED episodes (CONTROL never traded them)

- CONTROL never-triggered: 152; matched in variant: 138; became trades: 82; later confirmed 2, early exit no-confirmation 80, early exit invalidation 0.

| group | n | win | mean R | median R | PF | MFE R | MAE R | mean hold h | fees+slip/trade | sum P&L | target first |
|---|---|---|---|---|---|---|---|---|---|---|---|
| all recovered | 82 | 71% | 0.206 | 0.113 | 4.17 | 0.63 | -0.20 | 3.9 | 6.39 | 863 | 59% |
| recovered & confirmed | 2 | 0% | -1.079 | -1.079 | 0.00 | 0.79 | -1.08 | 77.7 | 2.93 | -109 | 50% |
| recovered & never confirmed | 80 | 72% | 0.238 | 0.121 | 6.93 | 0.62 | -0.18 | 2.1 | 6.48 | 971 | 59% |

By family:

| family | n | win | mean R | PF | target first | sum P&L |
|---|---|---|---|---|---|---|
| BREAKDOWN_SHORT | 3 | 100% | 0.669 | inf | 100% | 102 |
| BREAKOUT_LONG | 2 | 100% | 0.978 | inf | 50% | 101 |
| MOMENTUM_CONTINUATION_LONG | 3 | 100% | 0.189 | inf | 33% | 29 |
| MOMENTUM_CONTINUATION_SHORT | 1 | 0% | -0.045 | 0.00 | 100% | -2 |
| RESISTANCE_REJECTION_SHORT | 20 | 65% | 0.296 | 6.58 | 50% | 302 |
| SUPPORT_RECLAIM_LONG | 21 | 81% | 0.180 | 3.27 | 48% | 193 |
| TREND_PULLBACK_LONG | 19 | 53% | -0.028 | 0.80 | 53% | -25 |
| TREND_PULLBACK_SHORT | 13 | 77% | 0.247 | 23.45 | 92% | 163 |

By CONTROL end reason:

| CONTROL end reason | n | win | mean R | PF | sum P&L |
|---|---|---|---|---|---|
| RAN_WITHOUT_US | 19 | 79% | 0.385 | 6.66 | 372 |
| REGIME_INELIGIBLE | 5 | 80% | 0.292 | 19.33 | 75 |
| WATCH_TIMEOUT | 58 | 67% | 0.140 | 3.05 | 415 |

Phase 2.1 reference: ZONE_ENTRY recovered n=73 at 0.782R (2921 USDT).

## 13. Family results (combined; no family disabled)

| family | CONTROL | ZONE_ENTRY |
|---|---|---|
| BREAKDOWN_SHORT | n=20, R=0.768, PF=2.96, win=65%, MFE=4.58, MAE=-0.57, acct=7.49% | n=26, R=0.505, PF=2.36, win=69%, MFE=3.85, MAE=-0.51, acct=7.24% |
| BREAKOUT_LONG | n=20, R=0.261, PF=1.44, win=50%, MFE=3.15, MAE=-0.82, acct=2.75% | n=34, R=-0.059, PF=0.90, win=47%, MFE=1.97, MAE=-0.76, acct=-1.03% |
| MOMENTUM_CONTINUATION_LONG | n=32, R=-0.053, PF=0.91, win=44%, MFE=1.33, MAE=-0.88, acct=-0.96% | n=43, R=-0.020, PF=0.96, win=47%, MFE=1.23, MAE=-0.75, acct=-0.47% |
| MOMENTUM_CONTINUATION_SHORT | n=0 | n=1, R=-0.045, PF=0.00, win=0%, MFE=0.42, MAE=-0.15, acct=-0.02% |
| RESISTANCE_REJECTION_SHORT | n=55, R=-0.071, PF=0.88, win=47%, MFE=1.49, MAE=-0.95, acct=-1.72% | n=111, R=-0.117, PF=0.76, win=36%, MFE=1.06, MAE=-0.70, acct=-8.08% |
| SUPPORT_RECLAIM_LONG | n=53, R=0.012, PF=1.02, win=43%, MFE=1.84, MAE=-0.87, acct=0.48% | n=120, R=-0.172, PF=0.66, win=38%, MFE=1.08, MAE=-0.67, acct=-11.77% |
| TREND_PULLBACK_LONG | n=53, R=0.358, PF=1.65, win=51%, MFE=2.62, MAE=-0.81, acct=9.26% | n=91, R=0.073, PF=1.20, win=41%, MFE=1.60, MAE=-0.59, acct=5.91% |
| TREND_PULLBACK_SHORT | n=42, R=-0.295, PF=0.56, win=40%, MFE=1.31, MAE=-0.94, acct=-5.91% | n=88, R=-0.134, PF=0.69, win=45%, MFE=0.91, MAE=-0.60, acct=-6.26% |

## 14. LONG vs SHORT

| side | CONTROL | ZONE_ENTRY_CONFIRM_EXIT |
|---|---|---|
| LONG | n=158, R=0.146, PF=1.25, win=47% | n=288, R=-0.059, PF=0.89, win=41% |
| SHORT | n=117, R=-0.008, PF=0.98, win=48% | n=226, R=-0.052, PF=0.88, win=43% |

dev_2022_2023:

| side | CONTROL | ZONE_ENTRY_CONFIRM_EXIT |
|---|---|---|
| LONG | n=85, R=0.159, PF=1.27, win=47% | n=154, R=-0.137, PF=0.75, win=40% |
| SHORT | n=83, R=0.096, PF=1.17, win=51% | n=161, R=0.003, PF=1.00, win=45% |

val_2024:

| side | CONTROL | ZONE_ENTRY_CONFIRM_EXIT |
|---|---|---|
| LONG | n=73, R=0.132, PF=1.23, win=47% | n=134, R=0.031, PF=1.07, win=43% |
| SHORT | n=34, R=-0.261, PF=0.60, win=41% | n=65, R=-0.188, PF=0.63, win=40% |

## 15. MFE / MAE

| metric | CONTROL | ZONE_ENTRY_CONFIRM_EXIT |
|---|---|---|
| mean MFE (R) | 2.074 | 1.349 |
| mean MAE (R) | -0.862 | -0.655 |
| worst MAE (R) | -6.768 | -6.774 |
| realised mean winner (R) | 1.421 | 1.007 |
| counterfactual 1R before initial stop | 56.4% | 34.4% |
| counterfactual 1.5R before initial stop | 46.5% | 26.7% |
| counterfactual 2R before initial stop | 33.5% | 19.5% |
| counterfactual 3R before initial stop | 16.4% | 10.3% |
| TP1 executed | 46.5% | 26.7% |

## 16. Fees / slippage / funding impact (USDT, combined)

| component | CONTROL | ZONE_ENTRY_CONFIRM_EXIT |
|---|---|---|
| trades | 275 | 514 |
| gross P&L before slippage | 3158 | 2995 |
| slippage | -697 | -1656 |
| fees | -1211 | -2638 |
| funding | -117 | -140 |
| net P&L | 1133 | -1439 |
| gross expectancy (R, before slippage) | 0.226 | 0.114 |
| net expectancy (R) | 0.081 | -0.055 |
| cost drag per trade (R) | 0.145 | 0.169 |

## 17. Drawdown / equity (sequential, one position, fixed research equity for sizing)

| metric | CONTROL | ZONE_ENTRY_CONFIRM_EXIT |
|---|---|---|
| final equity | 11133.31 | 8561.03 |
| total return | 11.33% | -14.39% |
| CAGR | 3.64% | -5.05% |
| max drawdown (trade curve / daily mtm) | -8.96% / -8.74% | -23.47% / -23.41% |
| Sharpe / Sortino (daily marks) | 0.66 / 0.94 | -0.64 / -0.84 |
| longest losing streak | 5 | 7 |
| trades / week | 1.76 | 3.28 |

Null benchmark (same geometry-matched nulls as Phase 2):

| series | n | mean R | win rate | strategy - null (R) | z | P(null >= strat) |
|---|---|---|---|---|---|---|
| CONTROL strategy | 275 | 0.081 | 47.3% |  |  |  |
| CONTROL null (time-matched) | 5500 | -0.058 | 41.0% | 0.138 | z=1.99 | 0% |
| CONTROL null (regime-matched) | 5500 | -0.038 | 41.9% | 0.119 | z=1.49 | 10% |
| ZONE_ENTRY_CONFIRM_EXIT strategy | 514 | -0.056 | 42.2% |  |  |  |
| ZONE_ENTRY_CONFIRM_EXIT null (time-matched) | 10280 | -0.129 | 39.7% | 0.073 | z=1.20 | 10% |
| ZONE_ENTRY_CONFIRM_EXIT null (regime-matched) | 10280 | -0.090 | 40.9% | 0.034 | z=0.43 | 30% |

## 18. Uncertainty

- Standard error of mean R: CONTROL 0.088 (n=275), variant 0.051 (n=514); approximate 95% intervals CONTROL [-0.091, 0.252], variant [-0.156, 0.044].
- Paired comparison on 240 shared episodes: mean difference 0.083R, paired t = 1.49.
- dev_2022_2023: CONTROL 0.128R (n=168, t=1.12) vs variant -0.065R (n=315, t=-1.02).
- val_2024: CONTROL 0.007R (n=107, t=0.05) vs variant -0.040R (n=199, t=-0.48).
- One confirmation window was tested (the frozen 24-bar timeout); results for other windows are unknown by design.
- Early exits are modelled at the next 5m open with stop-type slippage; many are small losses whose size is cost-dominated, so the cost assumptions matter more for this variant than for CONTROL.
- Per-family cells are small; family-level differences are directional. Single instrument, single 3-year window, no out-of-sample confirmation yet.

## 19. Recommendation (not implemented)

Pre-declared criteria (from the hypothesis, thresholds stated so the reader can disagree):

| criterion | met | evidence |
|---|---|---|
| net expectancy meaningfully above CONTROL (> +0.05R) OR clearly above Phase 2.1 ZONE_ENTRY (> +0.05R) | no | CONTROL 0.081, Phase 2.1 0.029, variant -0.056 |
| materially lower drawdown than Phase 2.1 ZONE_ENTRY (<= 0.8x) | no | Phase 2.1 12.07%, variant 23.47%, CONTROL 8.96% |
| early exit removes a large share (>= 50%) of the failed zone-entry loss | no | CONTROL-INVALIDATED group: Phase 2.1 -3985 USDT -> variant -3513 USDT (n=79) |
| recovered NEVER_TRIGGERED value mostly kept (>= 50% of Phase 2.1 recovered P&L) | no | Phase 2.1 2921 USDT -> variant 863 USDT (n=82) |
| 2024 not clearly broken (variant 2024 expectancy >= CONTROL 2024 - 0.05R and > -0.10R) | yes | 2024: CONTROL 0.007, variant -0.040 |


- **Verdict: H2 NOT SUPPORTED: using the confirmation as an early exit does not recover enough of the filter's value, or the extra trades' costs consume the timing gain. Recommended next step: stop changing entry mechanics; remaining pre-registered candidates from Phase 2 are the exit design and the regime eligibility of the SHORT families, each needing a new single hypothesis and owner approval.**
- Where the variant's result comes from (its trades split by CONTROL's outcome on the same episode): INVALIDATED: n=79, -0.874R, -3513 USDT; NEVER_TRIGGERED: n=82, 0.206R, 863 USDT; TRADED: n=240, 0.126R, 1569 USDT; UNMATCHED: n=113, -0.060R, -357 USDT.
- Why the filter value is not recovered: on the plans CONTROL would have rejected (INVALIDATED), the early exit on an invalidation-level close saves only 0.236R per trade versus Phase 2.1 (-1.111R -> -0.874R), because the invalidation level sits only 0.5 ATR above the stop: by the time a 5m bar closes beyond it, most of the stop loss is already realised. Only 33 of these 79 trades exited on the invalidation rule; the rest hit the stop or the deadline.
- Why the recovered opportunities shrink: of 82 recovered never-triggered plans, 2 confirmed and 80 were cut at the 2 h deadline while mostly still in profit (mean 0.238R, 971 USDT); Phase 2.1 let the same plans run to 0.782R. The confirmation predicate requires price within one pad of the zone, so it rarely fires on a plan that moves away quickly, and the deadline exit truncates exactly the winners it was meant to keep.
- Lifecycle: 52% of variant trades confirmed (99 at entry, 167 later, median 0.42 h); 183 exited at the deadline and 33 on invalidation before confirmation.
- 2025+ stays untouched. No live trading. No tuning. The `experiment.entry_mode` default remains CONFIRMED_TRIGGER.

## Appendix — frozen configuration (CONTROL; the variant differs only in `experiment.entry_mode`)

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
