# BTC Swing V1 — Phase 2.3: POST-TP1 EXIT DESIGN (CONTROL vs STRUCTURAL_TRAIL_AFTER_TP1)

Generated 2026-10-05 10:29 UTC · period 2022-01-01 00:00 -> 2025-01-01 00:00 UTC · CONTROL result hash `92ebe5d7fc65` · variant result hash `0493f066c878` · code `5b6b00a7a1ab`

**Central question.** Does forcing breakeven immediately after TP1 prematurely truncate valid BTC swing winners, and can the existing structural trail capture more of their MFE without materially increasing downside?

Paper/backtest only. No live trading, no authenticated exchange access, no real money. 2025+ data untouched.

## 1. Hypothesis

Phase 2 (frozen defaults): realised mean winner 1.42R, mean MFE 2.07R, TP1 at 1.5R closing 40%, then the stop moves to breakeven (`breakeven_after_tp1: true`) and the remainder trails the 1h swing structure minus 0.5 ATR. Pre-registered hypothesis H3: the forced breakeven move cuts valid winners before the structural trail has had time to work; removing ONLY that move (keeping the initial stop until the existing `structure_atr` trail moves it) improves realised expectancy materially (> +0.05R over +0.081R), improves 2024 rather than deteriorating it, raises the profit factor and MFE capture, without a disproportionate drawdown increase, and the extra holding/funding cost does not erase the gain.

## 2. Frozen baseline verification

| check | value |
|---|---|
| Phase 2 validation result hash | `92ebe5d7fc65fc978ba4d3d222723e30c31d1db26e74d6e6585c786528c4ea56` |
| CONTROL result hash (this run) | `92ebe5d7fc65fc978ba4d3d222723e30c31d1db26e74d6e6585c786528c4ea56` |
| identical | yes |
| row-level check vs persisted Phase 2 trades | identical: 275 trades, 89 columns compared |
| CONTROL trades / episodes | 275 / 533 |
| config hash CONTROL / variant | `871a444e107e` / `b66e0256e764` (differ only in `exits.breakeven_after_tp1`) |

## 3. Exact variant definition

- CONTROL: `exits.breakeven_after_tp1: true` — at TP1 (1.5R, 40% closed) the stop is moved to the entry price from the next bar; the remainder then trails the 1h swing structure minus 0.5 ATR (ratchet only); TP2 at 3R closes 30%; 240 h cap.
- STRUCTURAL_TRAIL_AFTER_TP1: `exits.breakeven_after_tp1: false` — identical, except that after TP1 the INITIAL stop stays in place until the existing structural trail moves it. No other stop rule, no other change. Setup discovery, entry, confirmation, regime eligibility, sizing, leverage, TP levels and fractions, trail parameters, max hold and costs are frozen.
- Entries are therefore identical in both arms; only the exit path of trades that reach TP1 can differ (a longer variant trade can occupy the single slot and shift later detections, which is why the trade counts may differ slightly).

## 4. PIT audit

- bar visible iff close_time <= t; the breakeven move and the structural trail are both applied from the bar after the decision; stop-first ordering inside a bar.
- Deterministic rerun: CONTROL yes, variant yes.
- Truncation audit (variant): decisions up to 2023-07-03T00:00 identical with later data removed: yes (157825 rows).
- Resume labels: computed after the run from 5m highs/lows following the CONTROL exit, within the trade's max-hold horizon, initial stop as failure condition; descriptive only, never used by the engine.

## 5. Main comparison (combined 2022-01 -> 2024-12)

| metric | CONTROL | STRUCTURAL_TRAIL_AFTER_TP1 |
|---|---|---|
| trades | 275 | 273 |
| episodes | 533 | 524 |
| setup->entry | 51.6% | 52.1% |
| exp. R (net) | 0.081 | 0.063 |
| exp. R (gross) | 0.226 | 0.208 |
| t | 0.92 | 0.70 |
| PF | 1.14 | 1.10 |
| win rate | 47.3% | 43.6% |
| avg win R | 1.421 | 1.524 |
| avg loss R | -1.121 | -1.066 |
| median R | -1.043 | -1.051 |
| MFE R | 2.074 | 2.130 |
| MAE R | -0.862 | -0.923 |
| max DD | -8.96% | -9.29% |
| Sharpe | 0.66 | 0.52 |
| median hold h | 22.6 | 22.9 |
| fees | -1211 | -1208 |
| slippage | -697 | -693 |
| funding | -117 | -118 |
| net P&L | 1133 | 870 |
| net return | 11.33% | 8.70% |

Exit-path and MFE capture:

| metric | CONTROL | STRUCTURAL_TRAIL_AFTER_TP1 |
|---|---|---|
| TP1 frequency | 46.5% | 46.2% |
| TP2 frequency | 16.4% | 17.2% |
| stop-outs after TP1 (remainder closed below entry) | 26 | 22 |
| after-TP1 exits on: breakeven stop / initial stop / structural trail | 30 / 0 / 98 | 0 / 8 / 118 |
| mean realised R of TP1 trades | 1.432 | 1.414 |
| mean MFE R of TP1 trades | 3.869 | 4.016 |
| MFE captured as realised R (TP1 trades, mean/mean) | 37.0% | 35.2% |
| median per-trade capture (TP1 trades) | 39.2% | 39.2% |
| mean realised winner (R) / mean MFE all trades | 1.421 / 2.074 | 1.524 / 2.130 |
| mean holding of TP1 trades (h) | 50.2 | 51.5 |
| funding paid on TP1 trades (USDT, negative = paid) | -81 | -83 |

## 6. Segment stability

### dev_2022_2023

| metric | CONTROL | STRUCTURAL_TRAIL_AFTER_TP1 |
|---|---|---|
| trades | 168 | 168 |
| episodes | 323 | 320 |
| setup->entry | 52.0% | 52.5% |
| exp. R (net) | 0.128 | 0.105 |
| exp. R (gross) | 0.277 | 0.254 |
| t | 1.12 | 0.92 |
| PF | 1.22 | 1.18 |
| win rate | 48.8% | 45.8% |
| avg win R | 1.451 | 1.511 |
| avg loss R | -1.134 | -1.084 |
| median R | -1.041 | -1.041 |
| MFE R | 2.114 | 2.148 |
| MAE R | -0.895 | -0.962 |
| max DD | -3.56% | -4.18% |
| Sharpe | 0.97 | 0.80 |
| median hold h | 22.4 | 22.5 |
| fees | -799 | -803 |
| slippage | -456 | -456 |
| funding | -25 | -23 |
| net P&L | 1092 | 895 |
| net return | 10.92% | 8.95% |

### val_2024

| metric | CONTROL | STRUCTURAL_TRAIL_AFTER_TP1 |
|---|---|---|
| trades | 107 | 105 |
| episodes | 210 | 204 |
| setup->entry | 51.0% | 51.5% |
| exp. R (net) | 0.007 | -0.005 |
| exp. R (gross) | 0.145 | 0.134 |
| t | 0.05 | -0.04 |
| PF | 1.01 | 0.99 |
| win rate | 44.9% | 40.0% |
| avg win R | 1.368 | 1.547 |
| avg loss R | -1.101 | -1.040 |
| median R | -1.054 | -1.065 |
| MFE R | 2.010 | 2.101 |
| MAE R | -0.810 | -0.861 |
| max DD | -9.89% | -10.09% |
| Sharpe | 0.10 | -0.00 |
| median hold h | 24.9 | 24.9 |
| fees | -412 | -406 |
| slippage | -240 | -237 |
| funding | -92 | -95 |
| net P&L | 42 | -25 |
| net return | 0.42% | -0.25% |

## 7. Matched-trade analysis (identical entries; trades that reached TP1 in both arms)

- Paired trades: 268 (identical entry time in 268); TP1 reached in both arms: 123; TP1 in one arm only: 0. Across all paired trades, realised R differs in 28 trades (sum P&L CONTROL 785 vs variant 681 USDT).

| metric | CONTROL | STRUCTURAL_TRAIL_AFTER_TP1 |
|---|---|---|
| TP1 pairs | 123 | 123 |
| mean realised R | 1.417 | 1.401 |
| sum net P&L | 8876 | 8772 |
| mean MFE after TP1 (R from entry) | 3.696 | 3.868 |
| mean MAE after TP1 (R from entry) | 0.567 | 0.428 |
| TP2 reached | 42 | 45 |
| mean holding (h) | 49.0 | 50.6 |
| funding on these trades (USDT) | -69 | -69 |
| MFE captured (mean realised R / mean full-path MFE) | 36.2% | 35.8% |

- Paired difference (variant - CONTROL): mean -0.016R, paired t = -0.44; variant better in 2%, worse in 20%, identical in 77% of pairs.
- CONTROL exit mix after TP1: TRAIL/TRAIL=95, TRAIL/BREAKEVEN=28.
- Variant exit mix after TP1: TRAIL/TRAIL=117, STOP/INITIAL=6.

Pairs where the exit path differs (28; full table in `tp1_pairs.parquet`), largest gains first then largest losses:

| family | side | entry | h to TP1 | CONTROL exit | variant exit | MFE after TP1 C/V | MAE after TP1 C/V | BE stop | resumed | R C/V |
|---|---|---|---|---|---|---|---|---|---|---|
| BREAKDOWN SHORT | SHORT | 2024-08-02 22:20 | 2.7 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 2.48/20.93 | -0.09/-0.21 | yes | 2R+3R | 0.49/4.20 |
| TREND PULLBACK SHORT | SHORT | 2022-10-10 00:30 | 8.1 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 1.59/3.04 | -0.01/-0.16 | yes | 2R+3R | 0.47/1.80 |
| SUPPORT RECLAIM LONG | LONG | 2024-02-17 20:40 | 0.8 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 1.95/3.21 | -0.24/-0.81 | yes | 2R+3R | 0.39/1.32 |
| BREAKOUT LONG | LONG | 2024-01-29 18:10 | 8.5 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 1.96/1.96 | -0.55/-0.55 | yes | stop | 0.44/0.44 |
| TREND PULLBACK SHORT | SHORT | 2024-08-30 21:20 | 51.6 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 1.42/1.42 | -0.10/-0.10 | yes | 2R+3R | 0.54/0.53 |
| BREAKDOWN SHORT | SHORT | 2024-07-31 20:15 | 4.6 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 6.19/6.19 | -0.12/-0.60 | yes | stop | 1.36/1.28 |
| TREND PULLBACK SHORT | SHORT | 2023-08-22 01:00 | 15.4 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 4.69/4.69 | -0.18/-0.33 | yes | stop | 1.33/1.24 |
| TREND PULLBACK SHORT | SHORT | 2024-07-07 11:20 | 13.3 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 2.43/2.43 | -0.30/-0.30 | yes | stop | 0.55/0.45 |
| RESISTANCE REJECTION SHORT | SHORT | 2023-09-08 10:10 | 76.6 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 1.97/1.97 | -0.39/-0.39 | yes | stop | 0.52/0.39 |
| RESISTANCE REJECTION SHORT | SHORT | 2022-08-24 21:35 | 37.6 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 1.54/1.54 | -0.43/-0.43 | yes | 2R+3R | 0.53/0.39 |
| RESISTANCE REJECTION SHORT | SHORT | 2022-08-11 20:10 | 15.5 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 1.49/1.49 | -0.47/-0.47 | yes | stop | 0.51/0.35 |
| TREND PULLBACK SHORT | SHORT | 2023-09-06 04:45 | 12.4 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 1.38/1.38 | -0.36/-0.36 | yes | stop | 0.43/0.24 |
| SUPPORT RECLAIM LONG | LONG | 2024-01-15 07:50 | 37.8 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 1.41/1.41 | -0.04/-0.47 | yes | stop | 0.47/0.26 |
| BREAKDOWN SHORT | SHORT | 2024-06-11 09:15 | 0.8 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 2.84/2.84 | -0.03/-0.51 | yes | stop | 0.45/0.19 |
| SUPPORT RECLAIM LONG | LONG | 2022-08-16 16:55 | 12.2 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 3.18/3.18 | -0.04/-1.20 | yes | stop | 1.37/1.10 |
| TREND PULLBACK LONG | LONG | 2023-04-30 13:10 | 1.9 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 2.79/2.79 | -0.26/-2.44 | yes | stop | 0.44/0.03 |
| TREND PULLBACK SHORT | SHORT | 2022-04-24 20:20 | 4.6 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 2.47/2.47 | -0.35/-1.56 | yes | stop | 0.50/0.06 |
| BREAKOUT LONG | LONG | 2024-08-24 00:50 | 46.7 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 1.25/1.25 | -0.05/-0.83 | yes | stop | 0.45/-0.01 |
| BREAKDOWN SHORT | SHORT | 2023-08-01 03:20 | 12.2 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 0.59/0.59 | -0.04/-0.86 | yes | stop | 0.39/-0.10 |
| TREND PULLBACK SHORT | SHORT | 2024-07-08 13:45 | 0.8 | TRAIL/BREAKEVEN | STOP/INITIAL | 2.54/2.54 | -0.04/-1.03 | yes | stop | 0.51/-0.08 |
| MOMENTUM CONTINUATION LONG | LONG | 2023-12-20 17:55 | 1.2 | TRAIL/BREAKEVEN | STOP/INITIAL | 1.02/1.02 | -0.06/-1.22 | yes | stop | 0.30/-0.30 |
| SUPPORT RECLAIM LONG | LONG | 2024-02-05 09:20 | 4.3 | TRAIL/BREAKEVEN | STOP/INITIAL | 1.61/1.61 | -0.12/-1.18 | yes | stop | 0.42/-0.18 |
| MOMENTUM CONTINUATION LONG | LONG | 2023-10-23 04:35 | 0.8 | TRAIL/BREAKEVEN | STOP/INITIAL | 2.47/2.47 | -0.03/-1.37 | yes | stop | 0.33/-0.27 |
| TREND PULLBACK SHORT | SHORT | 2023-08-27 23:35 | 6.0 | TRAIL/BREAKEVEN | TRAIL/TRAIL | 1.52/1.52 | -0.16/-1.32 | yes | stop | 0.36/-0.24 |
| BREAKDOWN SHORT | SHORT | 2024-06-21 11:20 | 2.2 | TRAIL/BREAKEVEN | STOP/INITIAL | 1.92/1.92 | -0.27/-1.03 | yes | stop | 0.31/-0.29 |

## 8. CONTROL trades stopped at breakeven after TP1: did price resume?

| metric | value |
|---|---|
| CONTROL trades stopped at breakeven after TP1 | 28 |
| ... that later reached 2R (before the initial stop, within the max-hold horizon) | 6 |
| ... that later reached 3R | 6 |
| ... that hit the initial stop first (breakeven protected a loss) | 22 |
| ... neither within the horizon | 0 |
| mean realised R: CONTROL / variant on these trades | 0.572 / 0.502 |
| variant trades reaching >= 2R / >= 3R realised | 1 / 1 |
| variant mean R when price resumed to 2R / when the initial stop came first | 1.411 / 0.254 |
| net P&L difference on these trades (variant - CONTROL, USDT) | -104 |

By family:

| family | n | resumed to 2R | CONTROL mean R | variant mean R | P&L diff |
|---|---|---|---|---|---|
| BREAKDOWN_SHORT | 5 | 1 | 0.599 | 1.057 | 116 |
| BREAKOUT_LONG | 3 | 0 | 0.682 | 0.427 | -40 |
| MOMENTUM_CONTINUATION_LONG | 2 | 0 | 0.315 | -0.285 | -63 |
| RESISTANCE_REJECTION_SHORT | 4 | 2 | 0.521 | 0.340 | -37 |
| SUPPORT_RECLAIM_LONG | 4 | 1 | 0.662 | 0.627 | -7 |
| TREND_PULLBACK_LONG | 2 | 0 | 0.468 | 0.096 | -38 |
| TREND_PULLBACK_SHORT | 8 | 2 | 0.586 | 0.500 | -35 |

## 9. Downside analysis: trades CONTROL protected at breakeven that lose under the variant

| metric | value |
|---|---|
| trades protected at breakeven by CONTROL | 28 |
| worse under the variant | 25 (mean diff -0.318R, total impact -409 USDT) |
| meaningful losses under the variant (realised R <= -0.25) | 3 (variant mean -0.285R vs CONTROL 0.315R, total impact -94 USDT) |
| better under the variant | 3 (total impact 306 USDT) |
| variant TP1 trades that ended on the INITIAL stop | 6 |

## 10. Family / side / regime

### By setup family (combined)

| family | CONTROL | ZONE_ENTRY |
|---|---|---|
| BREAKDOWN_SHORT | n=20, R=0.768, PF=2.96, win=65%, MFE=4.58, MAE=-0.57, acct=7.49% | n=19, R=0.776, PF=2.79, win=53%, MFE=4.76, MAE=-0.70, acct=7.22% |
| BREAKOUT_LONG | n=20, R=0.261, PF=1.44, win=50%, MFE=3.15, MAE=-0.82, acct=2.75% | n=20, R=0.223, PF=1.38, win=45%, MFE=3.15, MAE=-0.85, acct=2.32% |
| MOMENTUM_CONTINUATION_LONG | n=32, R=-0.053, PF=0.91, win=44%, MFE=1.33, MAE=-0.88, acct=-0.96% | n=31, R=-0.172, PF=0.72, win=35%, MFE=1.19, MAE=-0.97, acct=-2.59% |
| RESISTANCE_REJECTION_SHORT | n=55, R=-0.071, PF=0.88, win=47%, MFE=1.49, MAE=-0.95, acct=-1.72% | n=56, R=-0.102, PF=0.83, win=46%, MFE=1.47, MAE=-0.97, acct=-2.68% |
| SUPPORT_RECLAIM_LONG | n=53, R=0.012, PF=1.02, win=43%, MFE=1.84, MAE=-0.87, acct=0.48% | n=54, R=0.103, PF=1.17, win=43%, MFE=2.13, MAE=-0.91, acct=2.71% |
| TREND_PULLBACK_LONG | n=53, R=0.358, PF=1.65, win=51%, MFE=2.62, MAE=-0.81, acct=9.26% | n=52, R=0.299, PF=1.53, win=50%, MFE=2.57, MAE=-0.87, acct=7.85% |
| TREND_PULLBACK_SHORT | n=42, R=-0.295, PF=0.56, win=40%, MFE=1.31, MAE=-0.94, acct=-5.91% | n=41, R=-0.295, PF=0.56, win=34%, MFE=1.47, MAE=-1.05, acct=-5.78% |

### By setup family — dev_2022_2023

| family | CONTROL | ZONE_ENTRY |
|---|---|---|
| BREAKDOWN_SHORT | n=9, R=1.651, PF=7.83, win=78%, MFE=5.98, MAE=-0.55, acct=7.26% | n=9, R=1.597, PF=7.32, win=67%, MFE=5.98, MAE=-0.59, acct=7.04% |
| BREAKOUT_LONG | n=11, R=0.783, PF=2.40, win=55%, MFE=4.66, MAE=-0.80, acct=4.31% | n=11, R=0.755, PF=2.35, win=55%, MFE=4.66, MAE=-0.86, acct=4.14% |
| MOMENTUM_CONTINUATION_LONG | n=14, R=-0.401, PF=0.44, win=36%, MFE=1.11, MAE=-0.93, acct=-2.73% | n=14, R=-0.487, PF=0.35, win=21%, MFE=1.11, MAE=-1.07, acct=-3.31% |
| RESISTANCE_REJECTION_SHORT | n=39, R=0.072, PF=1.13, win=54%, MFE=1.68, MAE=-0.97, acct=1.42% | n=39, R=0.053, PF=1.09, win=54%, MFE=1.68, MAE=-0.97, acct=1.06% |
| SUPPORT_RECLAIM_LONG | n=29, R=0.269, PF=1.52, win=52%, MFE=2.07, MAE=-0.86, acct=3.69% | n=29, R=0.260, PF=1.50, win=52%, MFE=2.07, MAE=-0.89, acct=3.59% |
| TREND_PULLBACK_LONG | n=31, R=0.088, PF=1.14, win=45%, MFE=2.04, MAE=-0.88, acct=1.54% | n=31, R=0.064, PF=1.10, win=45%, MFE=2.04, MAE=-0.99, acct=1.19% |
| TREND_PULLBACK_SHORT | n=35, R=-0.277, PF=0.59, win=40%, MFE=1.31, MAE=-0.94, acct=-4.69% | n=35, R=-0.279, PF=0.60, win=34%, MFE=1.47, MAE=-1.07, acct=-4.68% |

### By setup family — val_2024

| family | CONTROL | ZONE_ENTRY |
|---|---|---|
| BREAKDOWN_SHORT | n=11, R=0.045, PF=1.09, win=55%, MFE=3.44, MAE=-0.58, acct=0.23% | n=10, R=0.037, PF=1.06, win=40%, MFE=3.67, MAE=-0.81, acct=0.19% |
| BREAKOUT_LONG | n=9, R=-0.377, PF=0.41, win=44%, MFE=1.29, MAE=-0.85, acct=-1.55% | n=9, R=-0.428, PF=0.33, win=33%, MFE=1.29, MAE=-0.85, acct=-1.81% |
| MOMENTUM_CONTINUATION_LONG | n=18, R=0.218, PF=1.44, win=50%, MFE=1.50, MAE=-0.84, acct=1.77% | n=17, R=0.088, PF=1.17, win=47%, MFE=1.26, MAE=-0.88, acct=0.72% |
| RESISTANCE_REJECTION_SHORT | n=16, R=-0.419, PF=0.45, win=31%, MFE=1.03, MAE=-0.91, acct=-3.15% | n=17, R=-0.460, PF=0.42, win=29%, MFE=0.99, MAE=-0.97, acct=-3.74% |
| SUPPORT_RECLAIM_LONG | n=24, R=-0.300, PF=0.60, win=33%, MFE=1.56, MAE=-0.87, acct=-3.21% | n=25, R=-0.080, PF=0.89, win=32%, MFE=2.20, MAE=-0.92, acct=-0.88% |
| TREND_PULLBACK_LONG | n=22, R=0.740, PF=2.63, win=59%, MFE=3.44, MAE=-0.71, acct=7.73% | n=21, R=0.646, PF=2.37, win=57%, MFE=3.34, MAE=-0.70, acct=6.66% |
| TREND_PULLBACK_SHORT | n=7, R=-0.382, PF=0.37, win=43%, MFE=1.28, MAE=-0.92, acct=-1.22% | n=6, R=-0.389, PF=0.30, win=33%, MFE=1.45, MAE=-0.92, acct=-1.10% |

### LONG vs SHORT

| side | CONTROL | STRUCTURAL_TRAIL_AFTER_TP1 |
|---|---|---|
| LONG | n=158, R=0.146, PF=1.25, win=47% | n=157, R=0.129, PF=1.22, win=44% |
| SHORT | n=117, R=-0.008, PF=0.98, win=48% | n=116, R=-0.027, PF=0.95, win=43% |

### By regime at entry — CONTROL

| cell | n | win rate | exp. R | exp. acct | PF | median R | mean hold h | reliable |
|---|---|---|---|---|---|---|---|---|
| BREAKOUT_REGIME | 6 | 50.0% | 0.042 | 0.02% | 1.07 | -0.069 | 70.1 | no |
| LOW_VOLATILITY | 3 | 66.7% | 0.980 | 0.50% | 3.17 | 1.149 | 16.0 | no |
| RANGE | 80 | 52.5% | 0.211 | 0.11% | 1.38 | 0.416 | 30.6 | yes |
| TREND_DOWN | 69 | 42.0% | -0.229 | -0.11% | 0.65 | -1.056 | 30.3 | yes |
| TREND_UP | 117 | 46.2% | 0.153 | 0.07% | 1.26 | -1.056 | 38.0 | yes |

### By regime at entry — STRUCTURAL_TRAIL_AFTER_TP1

| cell | n | win rate | exp. R | exp. acct | PF | median R | mean hold h | reliable |
|---|---|---|---|---|---|---|---|---|
| BREAKOUT_REGIME | 6 | 50.0% | 0.042 | 0.02% | 1.07 | -0.069 | 70.1 | no |
| LOW_VOLATILITY | 3 | 66.7% | 0.880 | 0.45% | 2.95 | 0.849 | 16.9 | no |
| RANGE | 78 | 48.7% | 0.208 | 0.11% | 1.37 | -0.052 | 32.1 | yes |
| TREND_DOWN | 69 | 37.7% | -0.244 | -0.12% | 0.63 | -1.071 | 30.4 | yes |
| TREND_UP | 117 | 42.7% | 0.127 | 0.06% | 1.21 | -1.059 | 37.9 | yes |

## 11. Costs (USDT, combined)

| component | CONTROL | STRUCTURAL_TRAIL_AFTER_TP1 |
|---|---|---|
| trades | 275 | 273 |
| gross P&L before slippage | 3158 | 2890 |
| slippage | -697 | -693 |
| fees | -1211 | -1208 |
| funding (negative = paid) | -117 | -118 |
| funding events while in position | 1172 | 1177 |
| net P&L | 1133 | 870 |
| gross expectancy (R) / net expectancy (R) | 0.226 / 0.081 | 0.208 / 0.063 |
| cost drag per trade (R) | 0.145 | 0.145 |
| mean holding (h) | 34.4 | 34.8 |

## 12. Drawdown / equity (sequential, one position, fixed research equity for sizing)

| metric | CONTROL | STRUCTURAL_TRAIL_AFTER_TP1 |
|---|---|---|
| final equity | 11133.31 | 10870.39 |
| total return / CAGR | 11.33% / 3.64% | 8.70% / 2.82% |
| max drawdown (trade curve / daily mtm) | -8.96% / -8.74% | -9.29% / -9.07% |
| Sharpe / Sortino (daily marks) | 0.66 / 0.94 | 0.52 / 0.72 |
| longest losing streak | 5 | 8 |

Null benchmark (geometry-matched, as in Phase 2):

| series | n | mean R | win rate | strategy - null (R) | z | P(null >= strat) |
|---|---|---|---|---|---|---|
| CONTROL strategy | 275 | 0.081 | 47.3% |  |  |  |
| CONTROL null (time-matched) | 5500 | -0.058 | 41.0% | 0.138 | z=1.99 | 0% |
| CONTROL null (regime-matched) | 5500 | -0.038 | 41.9% | 0.119 | z=1.49 | 10% |
| STRUCTURAL_TRAIL_AFTER_TP1 strategy | 273 | 0.063 | 43.6% |  |  |  |
| STRUCTURAL_TRAIL_AFTER_TP1 null (time-matched) | 5460 | -0.052 | 37.5% | 0.114 | z=1.50 | 5% |
| STRUCTURAL_TRAIL_AFTER_TP1 null (regime-matched) | 5460 | -0.021 | 39.2% | 0.084 | z=0.95 | 20% |

## 13. Uncertainty

- Standard error of mean R: CONTROL 0.088 (n=275), variant 0.090 (n=273); 95% intervals CONTROL [-0.091, 0.252], variant [-0.114, 0.239].
- The paired comparison on 123 TP1 trades is the direct estimate of the exit-rule effect: mean difference -0.016R, paired t = -0.44; only 28 trades differ at all, so the whole experiment rests on a few dozen exit paths.
- dev_2022_2023: CONTROL 0.128R (n=168) vs variant 0.105R (n=168).
- val_2024: CONTROL 0.007R (n=107) vs variant -0.005R (n=105).
- The resume labels use the initial stop as the failure condition and the max-hold horizon; a different horizon would change counts. Single instrument, single 3-year window, no out-of-sample confirmation.

## 14. Verdict

Pre-declared criteria (from the hypothesis, thresholds stated so the reader can disagree):

| criterion | met | evidence |
|---|---|---|
| net expectancy improves materially (> +0.05R over CONTROL) | no | 0.081 -> 0.063 |
| 2024 improves rather than deteriorates | no | 2024: 0.007 -> -0.005 |
| profit factor improves | no | 1.14 -> 1.10 |
| MFE capture improves (TP1 trades) | no | 37.0% -> 35.2% |
| drawdown does not increase disproportionately (<= 1.25x CONTROL + 1pt) | yes | 8.96% -> 9.29% |
| extra holding/funding cost does not erase the gain (net P&L gain > extra funding paid) | no | net P&L change -263 USDT vs extra funding paid 2 USDT |


- **Verdict: NOT SUPPORTED.** removing the breakeven move does not improve realised expectancy on this window; the breakeven stop protects more P&L than the structural trail recovers.
- Trade-off in numbers: of 28 CONTROL trades stopped at breakeven, 6 later resumed to 2R (6 to 3R) and 22 would have hit the initial stop first. Under the variant those trades realise 0.502R on average vs 0.572R, a net P&L difference of -104 USDT; 3 of them become meaningful losses (<= -0.25R, total impact -94 USDT) while 3 improve (+306 USDT).
- Across all 123 paired TP1 trades the exit-rule effect is -0.016R per trade (paired t -0.44); MFE capture 36.2% -> 35.8%; funding on these trades -69 -> -69 USDT.
- Decomposition of the net P&L change (-263 USDT): -104 USDT is the direct exit-rule effect on the 268 trades with identical entries; the remaining -159 USDT comes from the changed trade sequence (longer variant holds shift later detections: CONTROL 275 vs variant 273 trades), a side-effect of the single-slot design, not of the exit rule itself.
- 2025+ stays untouched. No live trading. No tuning. The frozen default remains `breakeven_after_tp1: true` unless the owner decides otherwise.

## Appendix — frozen configuration (CONTROL; the variant differs only in `exits.breakeven_after_tp1`)

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
