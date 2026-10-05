# BTC Swing V1 — Phase 3: UNTOUCHED CONFIRMATORY VALIDATION (CONTROL vs APPROVED_VARIANT)

Generated 2026-10-05 14:09 UTC · holdout 2025-01-01 00:00 -> 2026-10-01 00:00 UTC · CONTROL result hash `55604c3d4ac4` · APPROVED_VARIANT result hash `73a28e4b5adb` · code `2f10deef02f6` · freeze commit `a53465069c8c`

**Primary question.** Does blocking new SHORT entries in TREND_DOWN (NO_NEW_SHORT_IN_TREND_DOWN (experiment.block_short_in_trend_down = true)) retain its benefit on genuinely untouched 2025-2026 data?

Confirmatory test, not a research phase: one run per arm, nothing changed after seeing any result. Paper/backtest only. No live trading, no authenticated exchange access, no real money.

## 1. Frozen strategy proof

| check | value |
|---|---|
| freeze manifest | `manifests/phase3_freeze_manifest.json` created 2026-10-05T14:05:11 UTC, committed at `a53465069c8c` before any holdout evaluation |
| strategy / version | btc_leveraged_swing_v1 1.0.0-foundation; rule versions backtest=btc-bt-1, costs=btc-cost-1, episodes=btc-episode-1, features=btc-fs-1, regime=btc-regime-1, risk=btc-risk-1, setups=btc-setup-1 |
| code commit at run | `2f10deef02f6` (2f10deef02f6); strategy code unchanged since the freeze (`btc_swing/`, `config/`, lockfile): yes |
| CONTROL config hash | `5bfc1a7a7f3cca02ccab5c49fb7ef282b7a5998b487f532d1633012a03a8a4ce` = Phase 2.4 CONTROL config hash: yes |
| APPROVED_VARIANT config hash | `ecb9d8a9060acf77f98a3f56539052b0ddc268c328850900bce448a7848f668d` = Phase 2.4 variant config hash: yes |
| live configs match the freeze | CONTROL yes, APPROVED_VARIANT yes |
| experiment switches | entry_mode = CONFIRMED_TRIGGER; breakeven_after_tp1 = yes; block_short_in_trend_down = CONTROL false / APPROVED_VARIANT true; no Phase 2.1 / 2.2 / 2.3 variant active |
| re-run 2022-01 -> 2025-01, CONTROL | result hash `92ebe5d7fc65fc97` = Phase 2 `92ebe5d7fc65fc97`: yes (275 trades) |
| re-run 2022-01 -> 2025-01, APPROVED_VARIANT | result hash `870c556507e7000d` = Phase 2.4 `870c556507e7000d`: yes (206 trades) |
| dataset hashes match the freeze | yes |
| holdout dates (frozen) | 2025-01-01 -> 2026-10-01 (exclusive); segments holdout_2025, holdout_2026_ytd |
| runs per arm on the holdout | 1 |

## 2. Untouched-window proof

- Local raw archive before Phase 3 (committed `manifests/raw_archive_manifest.jsonl` @ `a4e2966`): 1461 files, periods 2021-10 -> 2024-12-31, files dated 2025 or later: 0.
- Every earlier run loaded the store with `open_time < period_end` and ended at or before 2025-01-01: foundation_smoke_2024 -> 2025-01-01, phase21_entry_mechanics -> 2025-01-01, phase22_confirmation_exit -> 2025-01-01, phase23_post_tp1_exit -> 2025-01-01, phase24_short_regime -> 2025-01-01, phase2_validation -> 2025-01-01.
- The 2025+ archive files were downloaded for the first time in this phase, after the Phase 2.4 classification and the owner's approval; the freeze manifest (configs, hashes, dates, criteria, thresholds) was written and committed before the first holdout backtest.
- No threshold, family, exit, confirmation, regime rule, indicator, leverage rule or cost assumption was changed in this phase; the only code added is the Phase 3 runner/report (analysis and rendering).

## 3. Data coverage (holdout window)

| dataset | rows | first | last | rows in window | expected | coverage |
|---|---|---|---|---|---|---|
| funding | 5478 | 2021-10-01T00:00 | 2026-09-30T16:00 | 1914 | 1914 | 100.00% |
| mark_price_5m | 524731 | 2021-10-01T00:00 | 2026-09-30T23:55 | 183456 | 183744 | 99.84% |
| metrics_5min | 525688 | 2021-10-01T00:00 | 2026-09-30T23:55 | 183741 | 183744 | 100.00% |
| native_1d | 1826 | 2021-10-01T00:00 | 2026-09-30T00:00 | 638 | 638 | 100.00% |
| native_1h | 43824 | 2021-10-01T00:00 | 2026-09-30T23:00 | 15312 | 15312 | 100.00% |
| native_4h | 10956 | 2021-10-01T00:00 | 2026-09-30T20:00 | 3828 | 3828 | 100.00% |
| perp_klines_5m | 525888 | 2021-10-01T00:00 | 2026-09-30T23:55 | 183744 | 183744 | 100.00% |
| premium_index_5m | 525015 | 2021-10-01T00:00 | 2026-09-30T23:55 | 183456 | 183744 | 99.84% |

- Ingest 2025-01 -> 2026-09: 785 archive files fetched (sha256 verified: 785), 0 skipped, 519261 rows normalised in the resumed pass (the 214 files of the first pass were normalised before it died on a transient TLS error and were sha256-verified again); missing bars {'mark_price/5m/2026-06': 288, 'premium_index/5m/2026-06': 288}; anomalies: 0.

## 4. PIT audit

- bar visible iff close_time <= t; aux features iff time + latency <= t; funding applied in (prev close, t]; the regime used by the rule is the PIT regime at the trigger decision bar (completed 1d/4h bars only); fill at the next 5m open. Scope of the rule: entry eligibility only; open positions are never closed by a regime change.
- Deterministic rerun: CONTROL yes, APPROVED_VARIANT yes.
- Truncation audit (APPROVED_VARIANT): decisions up to 2025-11-16T00:00 identical with all later data removed: yes (91873 rows).
- Resampling oracle on the holdout (our PIT resample of 5m vs native archive bars): 1h: 15312 bars, 0 differ (100.00% exact), 4h: 3828 bars, 0 differ (100.00% exact), 1d: 638 bars, 0 differ (100.00% exact).
- Liquidation basis: mark. Look-ahead guard: MarketView raises on negative offsets; indicators causal (tests).

## 5. Primary results (full holdout)

| metric | CONTROL | APPROVED_VARIANT |
|---|---|---|
| trades | 180 | 121 |
| episodes | 368 | 445 |
| setup->entry | 48.9% | 27.2% |
| exp. R (net) | -0.104 | -0.066 |
| exp. R (gross) | 0.051 | 0.108 |
| t | -0.95 | -0.49 |
| PF | 0.85 | 0.90 |
| win rate | 38.3% | 40.5% |
| avg win R | 1.566 | 1.538 |
| avg loss R | -1.142 | -1.159 |
| median R | -1.076 | -1.095 |
| MFE R | 1.754 | 1.781 |
| MAE R | -0.865 | -0.847 |
| max DD | -11.29% | -7.00% |
| Sharpe | -0.77 | -0.40 |
| median hold h | 17.3 | 18.0 |
| fees | -897 | -668 |
| slippage | -538 | -397 |
| funding | -8 | -30 |
| net P&L | -973 | -426 |
| net return | -9.73% | -4.26% |

| metric | CONTROL | APPROVED_VARIANT |
|---|---|---|
| gross expectancy R (before fees, slippage, funding) | 0.051 | 0.108 |
| std of R | 1.465 | 1.491 |
| total account return / CAGR | -9.73% / -5.69% | -4.26% / -2.46% |
| max drawdown (trade curve / daily mtm) | -11.29% / -11.29% | -7.00% / -7.00% |
| Sortino (daily marks) | -1.00 | -0.52 |
| longest losing / winning streak | 14 / 4 | 9 / 4 |
| mean MFE R / mean MAE R / worst MAE R | 1.754 / -0.865 / -1.531 | 1.781 / -0.847 / -1.483 |
| mean / median holding hours | 31.1 / 17.3 | 32.3 / 18.0 |
| LONG trades / SHORT trades | 88 / 92 | 88 / 33 |
| episodes REGIME_BLOCKED | 0 | 80 |
| trades / week | 1.97 | 1.33 |

## 6. CONTROL vs approved variant

- Net expectancy -0.104R -> -0.066R (0.038R); profit factor 0.85 -> 0.90; net P&L -973 -> -426 USDT; max drawdown -11.29% -> -7.00%.
- Trades 180 -> 121; the rule blocked 57 CONTROL SHORT trades (section 8); 1 trades exist only in the variant (freed single slot), 3 CONTROL trades not blocked by the rule were lost to shifted sequencing.

| component of the net P&L change | USDT |
|---|---|
| total (variant - CONTROL) | 547 |
| direct effect of the blocked trades (their CONTROL P&L, sign reversed) | 793 |
| secondary sequencing effect | -245 |
| variant-only trades | n=1, P&L -68; SHORT: n=1, -68 USDT, -1.300R |
| CONTROL-only trades not blocked (lost to sequencing) | n=3, P&L 178; SHORT: n=3, 178 USDT, 1.171R |

LONG control check (the rule must not touch LONG logic; differences can only come from the single slot):

| metric | value |
|---|---|
| LONG trades CONTROL / variant | 88 / 88 |
| paired LONGs (same detection): identical R / different R | 88 / 0 |
| LONG only in CONTROL | 0 (P&L 0 USDT, mean R n/a) |
| LONG only in the variant | 0 (P&L 0 USDT, mean R n/a) |
| LONG expectancy / PF CONTROL vs variant | -0.067 / 0.90 vs -0.067 / 0.90 |

## 7. LONG vs SHORT

| side | CONTROL | APPROVED_VARIANT |
|---|---|---|
| LONG | n=88, R=-0.067, PF=0.90, win=43% | n=88, R=-0.067, PF=0.90, win=43% |
| SHORT | n=92, R=-0.140, PF=0.81, win=34% | n=33, R=-0.066, PF=0.91, win=33% |

SHORT populations (CONTROL shorts = blocked + remaining + shorts lost to sequencing):

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| CONTROL all SHORT | 92 | 34% | -0.140 | -1.081 | -12.9 | 0.81 | 1.86 | -0.91 | 29.3 | 36% | -675 |
| blocked by the rule (CONTROL trades) | 57 | 32% | -0.272 | -1.076 | -15.5 | 0.64 | 1.49 | -0.91 | 28.1 | 42% | -793 |
| kept in both arms | 32 | 34% | -0.027 | -1.146 | -0.9 | 0.95 | 2.17 | -0.91 | 31.1 | 22% | -60 |
| APPROVED_VARIANT remaining SHORT | 33 | 33% | -0.066 | -1.148 | -2.2 | 0.91 | 2.15 | -0.92 | 30.3 | 24% | -128 |

| drawdown-window contribution | CONTROL | APPROVED_VARIANT |
|---|---|---|
| max DD window (trades, USDT) | 65 trades, -1146 USDT (2025-07 -> 2026-01) | 40 trades, -720 USDT (2025-07 -> 2026-03) |
| SHORT P&L in the window | -537 | -110 |
| LONG P&L in the window | -609 | -609 |

Remaining SHORT by regime at trigger (variant):

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| RANGE | 33 | 33% | -0.066 | -1.148 | -2.2 | 0.91 | 2.15 | -0.92 | 30.3 | 24% | -128 |

SHORT by family, CONTROL:

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BREAKDOWN_SHORT | 11 | 27% | -0.639 | -1.237 | -7.0 | 0.30 | 1.22 | -0.97 | 9.3 | 0% | -367 |
| RESISTANCE_REJECTION_SHORT | 49 | 35% | -0.042 | -1.091 | -2.0 | 0.94 | 1.98 | -0.91 | 31.8 | 31% | -112 |
| TREND_PULLBACK_SHORT | 32 | 34% | -0.119 | -1.062 | -3.8 | 0.83 | 1.90 | -0.88 | 32.3 | 56% | -196 |

SHORT by family, APPROVED_VARIANT:

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BREAKDOWN_SHORT | 11 | 27% | -0.639 | -1.237 | -7.0 | 0.30 | 1.22 | -0.97 | 9.3 | 0% | -367 |
| RESISTANCE_REJECTION_SHORT | 22 | 36% | 0.221 | -1.135 | 4.9 | 1.28 | 2.61 | -0.89 | 40.9 | 36% | 239 |

## 8. Blocked-short analysis

- CONTROL SHORT trades: 92; blocked by the rule: 57; kept: 32; lost to sequencing/other: 3 (UNMATCHED=3).
- Regime at the trigger bar of the blocked trades: TREND_DOWN=57.

Every blocked trade (57; also in `blocked_short_trades.parquet`):

| entry (UTC) | family | regime det./trigger | CONTROL R | P&L | MFE R | MAE R | target before stop | hold h | exit |
|---|---|---|---|---|---|---|---|---|---|
| 2025-02-18 05:10 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | 0.53 | 27 | 2.02 | -0.61 | yes | 28.8 | TRAIL |
| 2025-03-31 19:40 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | -1.09 | -55 | 0.41 | -1.06 | no | 12.9 | STOP |
| 2025-04-01 12:20 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | 0.46 | 24 | 1.81 | -0.48 | no | 2.6 | TRAIL |
| 2025-04-04 02:25 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.10 | -56 | 0.29 | -1.09 | no | 5.7 | STOP |
| 2025-04-04 16:25 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | 1.87 | 94 | 3.60 | -0.75 | yes | 69.8 | TRAIL |
| 2025-04-09 11:05 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.02 | -51 | 0.10 | -1.01 | no | 6.3 | STOP |
| 2025-04-10 22:50 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.04 | -52 | 0.25 | -1.10 | no | 14.8 | STOP |
| 2025-04-11 23:20 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | -1.08 | -54 | 0.25 | -1.03 | no | 14.8 | STOP |
| 2025-09-01 12:05 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.17 | -60 | 1.40 | -1.02 | no | 13.7 | STOP |
| 2025-09-02 12:10 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | -1.11 | -56 | 0.72 | -1.16 | no | 1.8 | STOP |
| 2025-09-04 01:50 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | 0.70 | 36 | 2.53 | -0.17 | no | 21.2 | TRAIL |
| 2025-09-28 10:30 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.34 | -70 | 0.48 | -1.03 | yes | 4.6 | STOP |
| 2025-10-21 21:25 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | -1.03 | -52 | 0.89 | -1.25 | yes | 120.7 | STOP |
| 2025-10-31 23:15 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | 2.45 | 124 | 4.81 | -0.79 | yes | 109.2 | TRAIL |
| 2025-11-07 07:25 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.06 | -54 | 1.12 | -1.02 | yes | 14.8 | STOP |
| 2025-11-09 00:20 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.05 | -53 | 0.11 | -1.03 | yes | 18.6 | STOP |
| 2025-11-11 04:20 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | 1.96 | 99 | 3.02 | -0.09 | yes | 49.6 | TRAIL |
| 2025-11-18 20:50 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | 1.48 | 75 | 3.65 | -0.69 | yes | 28.5 | TRAIL |
| 2025-11-24 08:00 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.08 | -55 | 1.16 | -1.04 | yes | 10.2 | STOP |
| 2025-11-25 01:20 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | -1.08 | -54 | 1.13 | -1.19 | no | 40.5 | STOP |
| 2025-12-02 23:50 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | -1.10 | -56 | 0.29 | -1.13 | no | 2.4 | STOP |
| 2025-12-07 05:20 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | 0.49 | 25 | 1.55 | -0.31 | yes | 10.7 | TRAIL |
| 2025-12-07 22:10 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | -1.05 | -53 | 0.03 | -1.07 | no | 11.0 | STOP |
| 2025-12-18 04:50 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.13 | -58 | 0.39 | -1.09 | yes | 8.3 | STOP |
| 2025-12-20 18:05 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.28 | -67 | 0.68 | -1.53 | no | 14.7 | STOP |
| 2025-12-21 19:35 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.12 | -57 | 0.15 | -1.20 | no | 4.7 | STOP |
| 2025-12-22 07:20 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | -1.16 | -60 | 0.05 | -1.07 | no | 1.1 | STOP |
| 2025-12-24 14:20 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.14 | -58 | 1.17 | -1.19 | yes | 25.2 | STOP |
| 2025-12-25 21:35 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | -1.15 | -58 | 1.20 | -1.38 | no | 4.8 | STOP |
| 2026-01-11 21:30 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | -1.17 | -60 | 0.51 | -1.39 | yes | 3.6 | STOP |
| 2026-01-22 20:55 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.10 | -56 | 0.74 | -1.31 | no | 20.6 | STOP |
| 2026-01-24 06:35 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | 1.01 | 51 | 1.93 | -0.04 | yes | 49.8 | TRAIL |
| 2026-01-26 22:45 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.11 | -56 | 0.73 | -1.03 | no | 21.7 | STOP |
| 2026-01-28 05:15 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | -1.15 | -58 | 0.09 | -1.05 | no | 6.6 | STOP |
| 2026-01-28 19:35 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | 2.61 | 132 | 5.77 | -0.39 | yes | 43.2 | TRAIL |
| 2026-02-04 07:50 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | 2.34 | 118 | 5.35 | -0.03 | yes | 54.0 | TRAIL |
| 2026-02-21 21:35 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | 3.11 | 159 | 10.30 | -0.22 | yes | 75.7 | TRAIL |
| 2026-02-26 03:25 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | 1.03 | 52 | 2.16 | -0.35 | no | 64.2 | TRAIL |
| 2026-03-02 06:25 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.06 | -54 | 0.53 | -1.04 | no | 8.8 | STOP |
| 2026-03-08 20:30 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.08 | -54 | 1.01 | -1.03 | yes | 13.2 | STOP |
| 2026-03-09 11:20 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | -1.07 | -54 | 0.06 | -1.16 | no | 2.2 | STOP |
| 2026-03-21 03:05 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | 1.85 | 94 | 3.29 | -0.58 | yes | 56.1 | TRAIL |
| 2026-03-23 22:35 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | 0.52 | 26 | 1.63 | -0.80 | no | 24.8 | TRAIL |
| 2026-03-25 19:00 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | 2.30 | 116 | 3.64 | -0.57 | yes | 66.7 | TRAIL |
| 2026-03-31 15:45 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.06 | -53 | 0.13 | -1.05 | no | 13.8 | STOP |
| 2026-04-03 10:05 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.11 | -56 | 0.31 | -1.27 | no | 53.5 | STOP |
| 2026-06-09 08:05 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | 1.10 | 56 | 2.21 | -0.01 | yes | 29.6 | TRAIL |
| 2026-06-12 17:15 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | -1.13 | -57 | 0.69 | -1.19 | no | 28.5 | STOP |
| 2026-06-14 05:30 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | -1.19 | -61 | 1.13 | -1.37 | no | 15.8 | STOP |
| 2026-06-22 19:30 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | 2.05 | 105 | 4.20 | -0.56 | yes | 30.1 | TRAIL |
| 2026-06-26 18:50 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.03 | -52 | 1.30 | -1.14 | no | 123.6 | STOP |
| 2026-07-02 06:05 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | -1.08 | -54 | 0.28 | -1.01 | no | 6.8 | STOP |
| 2026-07-30 05:00 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.10 | -56 | 0.11 | -1.09 | no | 7.2 | STOP |
| 2026-07-30 15:30 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | -1.12 | -57 | 0.06 | -1.08 | no | 9.8 | STOP |
| 2026-08-02 12:35 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | -1.19 | -61 | 0.33 | -1.31 | no | 9.2 | STOP |
| 2026-08-04 14:20 | RESISTANCE REJECTION SHORT | TREND_DOWN/TREND_DOWN | -1.15 | -59 | 0.06 | -1.15 | no | 4.0 | STOP |
| 2026-08-13 23:50 | TREND PULLBACK SHORT | TREND_DOWN/TREND_DOWN | -1.08 | -55 | 1.20 | -1.04 | yes | 88.0 | STOP |

Aggregate:

| metric | value |
|---|---|
| winners removed / losers avoided | 18 / 39 |
| positive R sacrificed / negative R avoided | 27.9 / 43.4 |
| net R effect (avoided - sacrificed) | 15.5 |
| winners P&L sacrificed / losers P&L avoided (USDT) | 1412 / 2205 |
| net P&L effect (USDT) | 793 |
| mean / median blocked R | -0.272 / -1.076 |
| t-stat of the blocked population | -1.54 |
| share of avoided negative R from the worst 3 | 9% |
| share of sacrificed positive R from the best 3 | 29% |
| mean blocked R excluding the worst 3 | -0.217 |

By family:

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| RESISTANCE_REJECTION_SHORT | 26 | 31% | -0.306 | -1.077 | -8.0 | 0.60 | 1.44 | -0.93 | 24.3 | 23% | -404 |
| TREND_PULLBACK_SHORT | 31 | 32% | -0.244 | -1.062 | -7.6 | 0.67 | 1.53 | -0.89 | 31.3 | 58% | -389 |

By segment:

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| holdout_2025 | 29 | 28% | -0.464 | -1.076 | -13.5 | 0.42 | 1.22 | -0.95 | 23.2 | 45% | -686 |
| holdout_2026_ytd | 28 | 36% | -0.074 | -1.075 | -2.1 | 0.89 | 1.78 | -0.87 | 33.2 | 39% | -107 |

Phase 2.4 found the blocked population to be a coherent negative-expectancy group (60 trades, mean -0.19R, median -1.05R, 34 losers / 26 winners). Whether that structural explanation survives is read directly from the table above; it is not re-tuned.

## 9. Family stability (every family shown; none removed or hidden)

CONTROL, Phase 2 combined 2022-2024 vs 2025+:

| family | Phase 2 window 2022-2024 | 2025+ holdout |
|---|---|---|
| BREAKDOWN_SHORT | n=20, R=0.768, PF=2.96, win=65% | n=11, R=-0.639, PF=0.30, win=27% |
| BREAKOUT_LONG | n=20, R=0.261, PF=1.44, win=50% | n=10, R=-0.307, PF=0.64, win=30% |
| MOMENTUM_CONTINUATION_LONG | n=32, R=-0.053, PF=0.91, win=44% | n=16, R=-0.031, PF=0.96, win=44% |
| RESISTANCE_REJECTION_SHORT | n=55, R=-0.071, PF=0.88, win=47% | n=49, R=-0.042, PF=0.94, win=35% |
| SUPPORT_RECLAIM_LONG | n=53, R=0.012, PF=1.02, win=43% | n=34, R=-0.009, PF=0.98, win=44% |
| TREND_PULLBACK_LONG | n=53, R=0.358, PF=1.65, win=51% | n=28, R=-0.072, PF=0.88, win=46% |
| TREND_PULLBACK_SHORT | n=42, R=-0.295, PF=0.56, win=40% | n=32, R=-0.119, PF=0.83, win=34% |

APPROVED_VARIANT, Phase 2.4 combined 2022-2024 vs 2025+:

| family | Phase 2 window 2022-2024 | 2025+ holdout |
|---|---|---|
| BREAKDOWN_SHORT | n=20, R=0.768, PF=2.96, win=65% | n=11, R=-0.639, PF=0.30, win=27% |
| BREAKOUT_LONG | n=20, R=0.261, PF=1.44, win=50% | n=10, R=-0.307, PF=0.64, win=30% |
| MOMENTUM_CONTINUATION_LONG | n=32, R=-0.053, PF=0.91, win=44% | n=16, R=-0.031, PF=0.96, win=44% |
| RESISTANCE_REJECTION_SHORT | n=28, R=-0.018, PF=0.96, win=50% | n=22, R=0.221, PF=1.28, win=36% |
| SUPPORT_RECLAIM_LONG | n=53, R=0.012, PF=1.02, win=43% | n=34, R=-0.009, PF=0.98, win=44% |
| TREND_PULLBACK_LONG | n=53, R=0.358, PF=1.65, win=51% | n=28, R=-0.072, PF=0.88, win=46% |

Families in holdout_2025:

| family | CONTROL | APPROVED_VARIANT |
|---|---|---|
| BREAKDOWN_SHORT | n=6, R=-0.636, PF=0.23, win=33% | n=6, R=-0.636, PF=0.23, win=33% |
| BREAKOUT_LONG | n=5, R=-0.583, PF=0.42, win=20% | n=5, R=-0.583, PF=0.42, win=20% |
| MOMENTUM_CONTINUATION_LONG | n=12, R=0.065, PF=1.12, win=42% | n=12, R=0.065, PF=1.12, win=42% |
| RESISTANCE_REJECTION_SHORT | n=22, R=-0.191, PF=0.75, win=32% | n=10, R=0.252, PF=1.37, win=40% |
| SUPPORT_RECLAIM_LONG | n=26, R=0.036, PF=1.06, win=46% | n=26, R=0.036, PF=1.06, win=46% |
| TREND_PULLBACK_LONG | n=15, R=0.111, PF=1.22, win=53% | n=15, R=0.111, PF=1.22, win=53% |
| TREND_PULLBACK_SHORT | n=18, R=-0.165, PF=0.78, win=33% | n=0 |

Families in holdout_2026_ytd:

| family | CONTROL | APPROVED_VARIANT |
|---|---|---|
| BREAKDOWN_SHORT | n=5, R=-0.642, PF=0.37, win=20% | n=5, R=-0.642, PF=0.37, win=20% |
| BREAKOUT_LONG | n=5, R=-0.030, PF=0.95, win=40% | n=5, R=-0.030, PF=0.95, win=40% |
| MOMENTUM_CONTINUATION_LONG | n=4, R=-0.321, PF=0.43, win=50% | n=4, R=-0.321, PF=0.43, win=50% |
| RESISTANCE_REJECTION_SHORT | n=27, R=0.080, PF=1.10, win=37% | n=12, R=0.195, PF=1.22, win=33% |
| SUPPORT_RECLAIM_LONG | n=8, R=-0.155, PF=0.78, win=38% | n=8, R=-0.155, PF=0.78, win=38% |
| TREND_PULLBACK_LONG | n=13, R=-0.283, PF=0.54, win=38% | n=13, R=-0.283, PF=0.54, win=38% |
| TREND_PULLBACK_SHORT | n=14, R=-0.058, PF=0.91, win=36% | n=0 |

## 10. Regime stability (regime at entry; rules unchanged)

CONTROL, 2025+:

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TREND_UP | 62 | 42% | -0.103 | -1.071 | -6.4 | 0.84 | 1.57 | -0.83 | 35.6 | 32% | -315 |
| TREND_DOWN | 59 | 34% | -0.181 | -1.074 | -10.7 | 0.75 | 1.70 | -0.90 | 28.7 | 42% | -547 |
| RANGE | 59 | 39% | -0.028 | -1.130 | -1.6 | 0.95 | 2.00 | -0.86 | 28.8 | 32% | -110 |
| HIGH_VOLATILITY | 0 |  |  |  |  |  |  |  |  |  |  |
| LOW_VOLATILITY | 0 |  |  |  |  |  |  |  |  |  |  |
| BREAKOUT_REGIME | 0 |  |  |  |  |  |  |  |  |  |  |
| UNCLEAR | 0 |  |  |  |  |  |  |  |  |  |  |

APPROVED_VARIANT, 2025+:

| group | n | win | mean R | median R | sum R | PF | MFE R | MAE R | mean hold h | target first | sum P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TREND_UP | 62 | 42% | -0.103 | -1.071 | -6.4 | 0.84 | 1.57 | -0.83 | 35.6 | 32% | -315 |
| TREND_DOWN | 0 |  |  |  |  |  |  |  |  |  |  |
| RANGE | 59 | 39% | -0.028 | -1.130 | -1.6 | 0.95 | 2.00 | -0.86 | 28.8 | 32% | -110 |
| HIGH_VOLATILITY | 0 |  |  |  |  |  |  |  |  |  |  |
| LOW_VOLATILITY | 0 |  |  |  |  |  |  |  |  |  |  |
| BREAKOUT_REGIME | 0 |  |  |  |  |  |  |  |  |  |  |
| UNCLEAR | 0 |  |  |  |  |  |  |  |  |  |  |

CONTROL, Phase 2 window 2022-2024 (reference):

| cell | n | win rate | exp. R | exp. acct | PF | median R | mean hold h | reliable |
|---|---|---|---|---|---|---|---|---|
| BREAKOUT_REGIME | 6 | 50.0% | 0.042 | 0.02% | 1.07 | -0.069 | 70.1 | no |
| LOW_VOLATILITY | 3 | 66.7% | 0.980 | 0.50% | 3.17 | 1.149 | 16.0 | no |
| RANGE | 80 | 52.5% | 0.211 | 0.11% | 1.38 | 0.416 | 30.6 | yes |
| TREND_DOWN | 69 | 42.0% | -0.229 | -0.11% | 0.65 | -1.056 | 30.3 | yes |
| TREND_UP | 117 | 46.2% | 0.153 | 0.07% | 1.26 | -1.056 | 38.0 | yes |

APPROVED_VARIANT, Phase 2 window 2022-2024 (reference):

| cell | n | win rate | exp. R | exp. acct | PF | median R | mean hold h | reliable |
|---|---|---|---|---|---|---|---|---|
| BREAKOUT_REGIME | 6 | 50.0% | 0.042 | 0.01% | 1.07 | -0.069 | 70.1 | no |
| LOW_VOLATILITY | 3 | 66.7% | 0.980 | 0.49% | 3.17 | 1.149 | 16.0 | no |
| RANGE | 80 | 52.5% | 0.211 | 0.11% | 1.38 | 0.416 | 30.6 | yes |
| TREND_UP | 117 | 46.2% | 0.153 | 0.07% | 1.26 | -1.056 | 38.0 | yes |

## 11. 2025 vs 2026 YTD

### holdout_2025 (2025-01-01 -> 2026-01-01)

| metric | CONTROL | APPROVED_VARIANT |
|---|---|---|
| trades | 104 | 74 |
| episodes | 216 | 250 |
| setup->entry | 48.1% | 29.6% |
| exp. R (net) | -0.101 | -0.011 |
| exp. R (gross) | 0.055 | 0.159 |
| t | -0.73 | -0.07 |
| PF | 0.85 | 0.98 |
| win rate | 39.4% | 43.2% |
| avg win R | 1.492 | 1.484 |
| avg loss R | -1.138 | -1.150 |
| median R | -1.074 | -1.074 |
| MFE R | 1.758 | 1.815 |
| MAE R | -0.847 | -0.809 |
| max DD | -6.76% | -4.27% |
| Sharpe | -0.75 | -0.03 |
| median hold h | 18.0 | 19.8 |
| fees | -507 | -385 |
| slippage | -296 | -220 |
| funding | -20 | -32 |
| net P&L | -531 | -37 |
| net return | -5.31% | -0.37% |

Sides in holdout_2025:

| side | CONTROL | APPROVED_VARIANT |
|---|---|---|
| LONG | n=58, R=0.008, PF=1.02, win=45% | n=58, R=0.008, PF=1.02, win=45% |
| SHORT | n=46, R=-0.239, PF=0.69, win=33% | n=16, R=-0.081, PF=0.88, win=38% |

### holdout_2026_ytd (2026-01-01 -> 2026-10-01)

| metric | CONTROL | APPROVED_VARIANT |
|---|---|---|
| trades | 76 | 47 |
| episodes | 152 | 195 |
| setup->entry | 50.0% | 24.1% |
| exp. R (net) | -0.108 | -0.154 |
| exp. R (gross) | 0.046 | 0.029 |
| t | -0.61 | -0.66 |
| PF | 0.84 | 0.79 |
| win rate | 36.8% | 36.2% |
| avg win R | 1.674 | 1.641 |
| avg loss R | -1.148 | -1.171 |
| median R | -1.081 | -1.104 |
| MFE R | 1.750 | 1.728 |
| MAE R | -0.890 | -0.908 |
| max DD | -8.31% | -5.09% |
| Sharpe | -0.80 | -0.90 |
| median hold h | 16.8 | 16.1 |
| fees | -390 | -283 |
| slippage | -242 | -177 |
| funding | 12 | 2 |
| net P&L | -442 | -388 |
| net return | -4.42% | -3.88% |

Sides in holdout_2026_ytd:

| side | CONTROL | APPROVED_VARIANT |
|---|---|---|
| LONG | n=30, R=-0.212, PF=0.68, win=40% | n=30, R=-0.212, PF=0.68, win=40% |
| SHORT | n=46, R=-0.041, PF=0.94, win=35% | n=17, R=-0.051, PF=0.93, win=29% |

Quarterly:

| quarter | CONTROL | APPROVED_VARIANT |
|---|---|---|
| 2025-Q1 | n=15, R=-0.162, sum -2.4R, win 33%, P&L -128 | n=13, R=-0.143, sum -1.9R, win 31%, P&L -99 |
| 2025-Q2 | n=31, R=-0.057, sum -1.8R, win 45%, P&L -89 | n=25, R=0.005, sum 0.1R, win 48%, P&L 7 |
| 2025-Q3 | n=32, R=-0.064, sum -2.1R, win 41%, P&L -98 | n=28, R=0.031, sum 0.9R, win 43%, P&L 52 |
| 2025-Q4 | n=26, R=-0.164, sum -4.3R, win 35%, P&L -216 | n=8, R=0.005, sum 0.0R, win 50%, P&L 2 |
| 2026-Q1 | n=25, R=-0.069, sum -1.7R, win 36%, P&L -101 | n=9, R=-0.855, sum -7.7R, win 11%, P&L -402 |
| 2026-Q2 | n=23, R=0.387, sum 8.9R, win 52%, P&L 449 | n=16, R=0.572, sum 9.2R, win 56%, P&L 461 |
| 2026-Q3 | n=28, R=-0.550, sum -15.4R, win 25%, P&L -790 | n=22, R=-0.395, sum -8.7R, win 32%, P&L -448 |

Monthly:

| month | CONTROL | APPROVED_VARIANT |
|---|---|---|
| 2025-01 | n=5, R=-0.482, sum -2.4R, win 20%, P&L -123 | n=5, R=-0.482, sum -2.4R, win 20%, P&L -123 |
| 2025-02 | n=7, R=0.023, sum 0.2R, win 43%, P&L 5 | n=6, R=-0.061, sum -0.4R, win 33%, P&L -22 |
| 2025-03 | n=3, R=-0.059, sum -0.2R, win 33%, P&L -10 | n=2, R=0.458, sum 0.9R, win 50%, P&L 46 |
| 2025-04 | n=12, R=0.039, sum 0.5R, win 50%, P&L 25 | n=6, R=0.397, sum 2.4R, win 67%, P&L 121 |
| 2025-05 | n=10, R=0.114, sum 1.1R, win 50%, P&L 57 | n=10, R=0.114, sum 1.1R, win 50%, P&L 57 |
| 2025-06 | n=9, R=-0.377, sum -3.4R, win 33%, P&L -172 | n=9, R=-0.377, sum -3.4R, win 33%, P&L -172 |
| 2025-07 | n=12, R=0.102, sum 1.2R, win 42%, P&L 71 | n=12, R=0.102, sum 1.2R, win 42%, P&L 71 |
| 2025-08 | n=9, R=0.004, sum 0.0R, win 44%, P&L -0 | n=9, R=0.004, sum 0.0R, win 44%, P&L -0 |
| 2025-09 | n=11, R=-0.302, sum -3.3R, win 36%, P&L -169 | n=7, R=-0.058, sum -0.4R, win 43%, P&L -19 |
| 2025-10 | n=10, R=0.146, sum 1.5R, win 50%, P&L 75 | n=8, R=0.005, sum 0.0R, win 50%, P&L 2 |
| 2025-11 | n=7, R=0.419, sum 2.9R, win 43%, P&L 151 | n=0 |
| 2025-12 | n=9, R=-0.961, sum -8.7R, win 11%, P&L -442 | n=0 |
| 2026-01 | n=13, R=-0.478, sum -6.2R, win 23%, P&L -327 | n=7, R=-0.758, sum -5.3R, win 14%, P&L -280 |
| 2026-02 | n=3, R=2.158, sum 6.5R, win 100%, P&L 328 | n=0 |
| 2026-03 | n=9, R=-0.221, sum -2.0R, win 33%, P&L -102 | n=2, R=-1.192, sum -2.4R, win 0%, P&L -122 |
| 2026-04 | n=10, R=0.372, sum 3.7R, win 60%, P&L 186 | n=8, R=0.472, sum 3.8R, win 62%, P&L 189 |
| 2026-05 | n=8, R=0.672, sum 5.4R, win 50%, P&L 273 | n=8, R=0.672, sum 5.4R, win 50%, P&L 273 |
| 2026-06 | n=5, R=-0.040, sum -0.2R, win 40%, P&L -10 | n=0 |
| 2026-07 | n=5, R=-0.672, sum -3.4R, win 20%, P&L -171 | n=2, R=-0.033, sum -0.1R, win 50%, P&L -4 |
| 2026-08 | n=12, R=-0.824, sum -9.9R, win 8%, P&L -508 | n=9, R=-0.718, sum -6.5R, win 11%, P&L -333 |
| 2026-09 | n=11, R=-0.196, sum -2.2R, win 45%, P&L -110 | n=11, R=-0.196, sum -2.2R, win 45%, P&L -110 |

## 12. Costs (USDT, full holdout; frozen assumptions, no alternative cost run)

| component | CONTROL | APPROVED_VARIANT |
|---|---|---|
| trades | 180 | 121 |
| gross P&L before slippage | 470 | 670 |
| slippage | -538 | -397 |
| fees | -897 | -668 |
| funding | -8 | -30 |
| net P&L | -973 | -426 |
| gross expectancy R / net expectancy R | 0.051 / -0.106 | 0.108 / -0.069 |
| cost drag per trade (R) | 0.157 | 0.177 |
| fees as % of gross | 190.9% | 99.7% |
| funding events | 693 | 478 |

## 13. Leverage / liquidation (frozen sizing; safety verification only)

| metric | CONTROL | APPROVED_VARIANT |
|---|---|---|
| leverage used (trades per level) | 1x: 31, 2x: 74, 3x: 50, 5x: 19, 10x: 6 | 1x: 15, 2x: 47, 3x: 36, 5x: 17, 10x: 6 |
| mean / max leverage | 2.69 / 10 | 2.99 / 10 |
| mean / max margin % of equity | 19.6 / 27.0 | 19.3 / 25.8 |
| mean / max account risk at stop % | 0.53 / 0.57 | 0.52 / 0.54 |
| risk-capped trades / risk-rejected episodes | 0 / 0 | 0 / 0 |
| min / median stop-to-liquidation ratio | 16.35 / 37.20 | 20.70 / 37.36 |
| min liquidation distance (% / ATR) | 9.50% / 7.69 | 9.50% / 7.69 |
| worst MAE % | -6.14 | -4.85 |
| liquidations | 0 | 0 |
| max planned account loss at stop % / if liquidated % | 0.50 / 24.99 | 0.50 / 24.99 |
| liquidation basis | mark | mark |

## 14. Drawdown / equity (sequential, one position, fixed research equity for sizing)

| metric | CONTROL | APPROVED_VARIANT |
|---|---|---|
| initial / final equity | 10000 / 9026.93 | 10000 / 9574.37 |
| total return / CAGR | -9.73% / -5.69% | -4.26% / -2.46% |
| max drawdown trade curve / daily mtm | -11.29% / -11.29% | -7.00% / -7.00% |
| max drawdown (USDT) | -1146 | -720 |
| Sharpe / Sortino (daily marks) | -0.77 / -1.00 | -0.40 / -0.52 |
| longest losing streak | 14 | 9 |
| drawdown-window family contribution | RESISTANCE_REJECTION_SHORT -360 (n=21); SUPPORT_RECLAIM_LONG -343 (n=9); BREAKOUT_LONG -214 (n=6); TREND_PULLBACK_SHORT -177 (n=16); MOMENTUM_CONTINUATION_LONG -106 (n=4); BREAKDOWN_SHORT -0 (n=3); TREND_PULLBACK_LONG 53 (n=6) | SUPPORT_RECLAIM_LONG -343 (n=9); BREAKOUT_LONG -214 (n=6); MOMENTUM_CONTINUATION_LONG -106 (n=4); BREAKDOWN_SHORT -64 (n=4); RESISTANCE_REJECTION_SHORT -46 (n=11); TREND_PULLBACK_LONG 53 (n=6) |
| Phase 2.4 variant max DD (reference for the cap) |  | -6.48% -> cap 9.72% |

## 15. Null benchmark and statistical uncertainty

| arm | n | mean R | std R | SE | t | 95% interval (normal approx.) |
|---|---|---|---|---|---|---|
| CONTROL | 180 | -0.104 | 1.465 | 0.109 | -0.95 | [-0.318, 0.110] |
| APPROVED_VARIANT | 121 | -0.066 | 1.491 | 0.136 | -0.49 | [-0.332, 0.199] |
| CONTROL holdout_2025 | 104 | -0.101 | 1.415 | 0.139 | -0.73 | [-0.373, 0.171] |
| APPROVED_VARIANT holdout_2025 | 74 | -0.011 | 1.435 | 0.167 | -0.07 | [-0.338, 0.316] |
| CONTROL holdout_2026_ytd | 76 | -0.108 | 1.540 | 0.177 | -0.61 | [-0.455, 0.238] |
| APPROVED_VARIANT holdout_2026_ytd | 47 | -0.154 | 1.588 | 0.232 | -0.66 | [-0.608, 0.300] |

Geometry-matched null benchmark (random entries with the same side, stop %, ATR, sizing, exits and costs; time- and regime-matched; K per trade as in Phase 2):

| series | n | mean R | win rate | strategy - null (R) | z | P(null >= strat) |
|---|---|---|---|---|---|---|
| CONTROL strategy | 180 | -0.104 | 38.3% |  |  |  |
| CONTROL null (time-matched) | 3600 | -0.166 | 38.2% | 0.062 | z=0.69 | 25% |
| CONTROL null (regime-matched) | 3600 | -0.168 | 37.8% | 0.063 | z=0.58 | 35% |
| APPROVED_VARIANT strategy | 121 | -0.066 | 40.5% |  |  |  |
| APPROVED_VARIANT null (time-matched) | 2420 | -0.152 | 38.7% | 0.085 | z=0.65 | 25% |
| APPROVED_VARIANT null (regime-matched) | 2420 | -0.151 | 40.2% | 0.084 | z=0.62 | 30% |

- The interval is the existing normal approximation of the framework (mean ± 1.96·SE); no bootstrap or other statistic was added for this phase, and no statistical selection criterion was introduced after seeing results.
- Blocked population: n=57, mean -0.272R, t = -1.54.

## 16. Pre-declared confirmation criteria (committed in the freeze manifest before any result)

| # | criterion | met | evidence |
|---|---|---|---|
| 1 | 1. net expectancy of APPROVED_VARIANT on the full holdout is positive (mean net R > 0) | no | APPROVED_VARIANT net expectancy -0.066R (n=121) |
| 2 | 2. profit factor of APPROVED_VARIANT on the full holdout is > 1.0 | no | profit factor 0.90 |
| 3 | 3. APPROVED_VARIANT is better than or materially no worse than CONTROL: net expectancy >= CONTROL net expectancy - 0.05R | yes | CONTROL -0.104R vs APPROVED_VARIANT -0.066R (difference +0.038R; floor -0.050R) |
| 4 | 4. SHORT performance improves relative to CONTROL: APPROVED_VARIANT SHORT net expectancy > CONTROL SHORT net expectancy (an arm with no SHORT trades counts as 0R); the rule must have blocked at least one CONTROL trade, otherwise the criterion is NOT TESTABLE and cannot be met | yes | SHORT CONTROL -0.140R (n=92) vs APPROVED_VARIANT -0.066R (n=33); trades blocked by the rule: 57 |
| 5 | 5. max drawdown (trade curve) of APPROVED_VARIANT <= 1.5 x the Phase 2.4 variant drawdown (6.48%), i.e. <= 9.72% | yes | max DD 7.00% vs cap 9.72% |
| 6 | 6. not outlier-driven: (a) APPROVED_VARIANT net expectancy stays > 0 after removing its two best trades, and (b) if the variant beats CONTROL in net P&L, the lead survives after removing the two largest avoided losses among the blocked trades | no | (a) expectancy without best 2 trades -0.149R; (b) net P&L lead +547 USDT, without the 2 largest avoided losses +410 USDT |
| 7 | 7. no catastrophic deterioration between 2025 and 2026 YTD: every chronological segment with >= 10 trades has net expectancy >= -0.25R and max drawdown <= 9.72%; a segment with < 10 trades is reported but cannot fail the criterion | yes | holdout_2025: n=74, -0.011R, DD 4.27% -> ok; holdout_2026_ytd: n=47, -0.154R, DD 5.09% -> ok |

Classification rule (pre-declared): A — CONFIRMED: READY FOR PAPER FORWARD TESTING if all seven criteria are met; C — FAILED OUT-OF-SAMPLE if criterion 1 or 2 fails; B — MIXED: EDGE POSSIBLE BUT NOT CONFIRMED otherwise.

## 17. Final classification

**C — FAILED OUT-OF-SAMPLE**

- Criteria met: 4 of 7.

## 18. Limitations

- One instrument, one holdout of 1.75 years (180 CONTROL / 121 variant trades); the standard errors in section 15 bound what this sample can say.
- The variant rule was derived from the Phase 2 regime table (in-sample on 2022-2024) and confirmed in Phase 2.4 on the same window; this holdout is its first and only out-of-sample test. A single holdout cannot distinguish a durable effect from a favourable regime mix.
- The regime label is a model output with frozen thresholds; the holdout's regime composition (section 10) determines how often the rule fires.
- Costs are the frozen assumptions (taker 5 bps, slippage 2/5 bps, archive funding); liquidation uses mark-price klines where available. No alternative cost or latency scenario was run.
- Sequential single-slot account: results depend on trade ordering; the direct/secondary decomposition in section 6 separates the rule's own effect from sequencing.
- Archive data: Binance Vision monthly/daily files, sha256 verified; two known venue incidents with stale 5m bars exist in the development window; any holdout anomalies are listed in section 3.

## 19. Recommendation

- **C — FAILED OUT-OF-SAMPLE.** The approved variant fails pre-declared criterion/criteria 1, 2, 6 on untouched 2025+ data (section 16).
- No parameter tweak, alternative filter, regime redesign or re-run is proposed. The Phase 2.4 effect did not transfer out of sample under the frozen rules; the honest conclusion is that the strategy as frozen has no demonstrated edge on this holdout. The owner decides whether the research track ends here.

## Appendix — frozen configuration (CONTROL; the variant differs only in `experiment.block_short_in_trend_down: true`)

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
  block_short_in_trend_down: false
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

Pre-declared criteria text as frozen:

- 1. net expectancy of APPROVED_VARIANT on the full holdout is positive (mean net R > 0)
- 2. profit factor of APPROVED_VARIANT on the full holdout is > 1.0
- 3. APPROVED_VARIANT is better than or materially no worse than CONTROL: net expectancy >= CONTROL net expectancy - 0.05R
- 4. SHORT performance improves relative to CONTROL: APPROVED_VARIANT SHORT net expectancy > CONTROL SHORT net expectancy (an arm with no SHORT trades counts as 0R); the rule must have blocked at least one CONTROL trade, otherwise the criterion is NOT TESTABLE and cannot be met
- 5. max drawdown (trade curve) of APPROVED_VARIANT <= 1.5 x the Phase 2.4 variant drawdown (6.48%), i.e. <= 9.72%
- 6. not outlier-driven: (a) APPROVED_VARIANT net expectancy stays > 0 after removing its two best trades, and (b) if the variant beats CONTROL in net P&L, the lead survives after removing the two largest avoided losses among the blocked trades
- 7. no catastrophic deterioration between 2025 and 2026 YTD: every chronological segment with >= 10 trades has net expectancy >= -0.25R and max drawdown <= 9.72%; a segment with < 10 trades is reported but cannot fail the criterion
