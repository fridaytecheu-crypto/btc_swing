# BTC V9 — SIMPLE TREND + VOLUME PULLBACK: pre-registered design (one deterministic strategy)

Status: DESIGN FROZEN BEFORE ANY V9 RESULT. Owner decision (2026-10-12): the V8 candidate-ranking track
is stopped before any code or run (its design document remains on record as superseded). V9 is ONE
deterministic BTCUSDT strategy, simple enough to understand, backtest, validate and later run in Bybit
Demo. No ML, no ranking, no top-N, no neural network, no large parameter optimisation. Isolated in
`btc_swing/v9/` (config `config/btc_swing_v9.yaml`, CLI `btc-swing v9 config|research|render`, report
`reports/BTC_SWING_V9_SIMPLE_TREND_VOLUME_PULLBACK.md`, tables `reports/v9/`). Paper research only: no
exchange client, no credentials, no order path, nothing activated, `V9D-` reserved and unused. V5, V5.1,
V6, V6.1, V7 untouched. The locked holdout 2025-07-01 -> 2026-09-30 is neither downloaded nor read.

## 0. Product objective

BTCUSDT only. 3-6 executed trades per calendar day on average is the TARGET, not a quota; positive NET
expectancy after realistic Bybit taker costs is MANDATORY. If the strategy naturally trades less and is
profitable, that is reported honestly; the strategy is never weakened to manufacture frequency, and a
price-only version that beats the volume-confirmed version is reported as such (useless volume rules are
removed, not kept).

## 1. Data and windows

- Existing Bybit-native 1m dataset (`data/btc/datasets/bybit_trades_1m/`, V7 ingest, 1,324 days
  2021-11-15 -> 2025-06-30, sha256 manifest `manifests/v7_bybit_1m_dataset_manifest.json`). Nothing is
  re-downloaded. 5m and 15m bars are built from completed 1m bars on a day-aligned grid; only completed
  candles are used at every timeframe (no intra-minute logic).
- Funding for cost accounting: the Binance USDT-M funding series already on disk (Bybit history not
  reachable); identified as a cross-venue PROXY, used only as a cost, never as a signal.

| window | span | days | use |
|---|---|---|---|
| TRAIN / DEVELOPMENT | 2022-01-01 -> 2024-06-30 | 912 | ladder, funnel, small neighbourhoods, selection of the frozen specification |
| VALIDATION 1 | 2024-07-01 -> 2024-12-31 | 184 | frozen specification, once |
| RECENT VALIDATION | 2025-01-01 -> 2025-06-30 | 181 | frozen specification, once |
| LOCKED HOLDOUT | 2025-07-01 -> 2026-09-30 | | not read |

Feature warm-up uses 45 days before each window start (features only; every value is PIT).

## 2. Features (all at completed-bar closes; PIT by construction)

| timeframe | feature | definition |
|---|---|---|
| 15m | `ema20`, `ema50` | EMAs of the 15m close over completed 15m bars |
| 15m | `trend` | +1 if ema20 > ema50 AND close > ema20; -1 if ema20 < ema50 AND close < ema20; else 0; the value used at a 5m bar is the last COMPLETED 15m bar |
| 5m | `atr5` | Wilder ATR(14) on completed 5m bars |
| 5m | `rvol5` | 5m volume / median volume of the SAME 5m time-of-day slot over the previous 20 UTC days (at least 10 observations; previous days only) |
| 1m | `rvol1` | 1m volume / mean volume of the previous 20 completed 1m bars |
| 1m | `imb1` | (taker buy - taker sell) / (buy + sell) of the minute (Bybit aggressor side) |
| 1m | `delta1` | taker buy - taker sell (base qty); `delta_med60` = rolling median of \|delta1\| over the previous 60 minutes |
| 1m | `accel1` | 1m volume / mean volume of the previous 5 completed 1m bars (diagnostic only at first) |
| 1m | `brk_up`, `brk_dn` | close above the max high / below the min low of the previous 10 completed 1m bars (the "most recent local 1m pullback high/low") |
| 1h | `atr1h` | Wilder ATR(14) on completed 1h bars, used only by the frozen sizing ladder's liquidation check |

Volume is base quantity (BTC). Shorts use the mirrored price series (high <-> -low, close -> -close,
trend -> -trend) so one code path serves both directions; there is no direction-specific rule.

## 3. The setup (LONG; SHORT is the exact mirror)

1. TREND: `trend` of the last completed 15m bar at the impulse peak bar is +1.
2. IMPULSE (5m): at 5m bar p, high[p] is the highest high of bars p-8..p and of every bar up to the
   arming bar (p is the peak of the leg); origin o = the bar of the lowest low in p-8..p with o < p;
   impulse size = high[p] - low[o]; required size >= `impulse_atr` x atr5[p] (anchor 1.0; neighbourhood
   0.8 / 1.0 / 1.2). Leg bars = o+1..p (1 to 8 bars).
3. IMPULSE VOLUME: impulse RVOL = mean of rvol5 over the leg bars >= `impulse_rvol` (anchor 1.25;
   neighbourhood 1.1 / 1.25 / 1.5).
4. PULLBACK (5m): at bar i with n = i - p in [2, 6] completed bars: no bar in p+1..i has a high above
   high[p]; pullback low = min low over p+1..i; retrace = (high[p] - pullback low) / size in [0.25, 0.60]
   (boundary sensitivity only: [0.20, 0.60] and [0.25, 0.70]); pullback low > low[o] (origin intact).
   When these hold at the close of bar i the setup is ARMED for the 5 minutes of bar i+1; arming is
   re-evaluated at every 5m close (the pullback may extend to 6 bars), so a setup is armed for at most
   five consecutive 5m bars and then expires.
5. PULLBACK VOLUME CONTRACTION: mean 5m volume over the pullback bars / mean 5m volume over the leg
   bars <= `pullback_volume_ratio` (anchor 0.75; neighbourhood 0.60 / 0.75 / 0.90).
6. ENTRY TRIGGER (1m): while armed, the first completed 1m candle with `brk_up` (close above the highest
   high of the previous 10 completed 1m bars) is a trigger; the flow block below is evaluated on that
   candle immediately; if it passes, ENTER at the open of the next minute (taker). If it fails, the setup
   stays armed and the next breakout candle is evaluated the same way. No ranking, no waiting.
7. 1M VOLUME / FLOW CONFIRMATION (only three quantities): A. `rvol1` >= `trigger_rvol` (anchor 1.25;
   1.0 / 1.25 / 1.5); B. taker imbalance `imb1` >= +`imbalance` for LONG, <= -`imbalance` for SHORT
   (anchor 0.20; 0.10 / 0.20 / 0.30); C. delta direction: `delta1` > 0 for LONG, < 0 for SHORT
   (`sign` mode, anchor); the alternative `median` mode (\|delta1\| > `delta_med60`) is one comparison.
8. VOLUME ACCELERATION (diagnostic): `accel1` of the trigger candle is recorded, never gated at first.
   The report shows net R by accel quartile among executed trades in TRAIN and by TRAIN half-year. The
   pre-declared promotion rule: an accel gate (>= 1.5) becomes a rule ONLY if, on TRAIN, it raises net
   expectancy of the full volume version by >= 0.05 R per trade, is positive in >= 4 of 5 half-years and
   keeps >= 2 executed trades/day; otherwise it is dropped.

Explicitly NOT used (owner's list): RSI, MACD, Bollinger, Stochastic, ADX, OBV, VWAP variants, order-book
signals, funding or OI gates, large-trade clusters, liquidation signals, ML scoring.

## 4. Stop, exits, costs, risk

- STOP: pullback low - 0.10 x atr5 (LONG; mirrored for SHORT). Minimum stop distance 0.50% of the entry
  reference (neighbourhood 0.40 / 0.50 / 0.60%): a closer structural stop is a REJECTED micro-stop (the
  trade is not taken; counted in the funnel), never widened artificially in the PRIMARY (`reject` mode).
  Pre-declared secondary (`floor` mode, sensitivity only, added in section 10 before the TRAIN run): the
  stop is widened to exactly the minimum distance and R is measured on the widened stop.
- EXITS (tested exactly as listed): A full exit at +1.0R; B full exit at +1.5R (PRIMARY, chosen now
  because at a ~15 bps round trip and a 0.5% stop the break-even win rate is 65% for +1R and 52% for
  +1.5R); C 50% at +1.0R, remaining 50% at +1.75R with the stop unchanged. TIME STOP 120 minutes after
  entry: if the open P&L at that minute's close is below +0.5R the position is closed at that close;
  otherwise it runs to target/stop with a hard maximum of 2 x the time stop (240 min). Sensitivity: 180
  minutes (hard maximum 360). Stops and targets are resolved on 1m highs/lows; both touched in one
  minute -> stop first (conservative). No trailing engine.
- COSTS (primary, conservative): TAKER entry and TAKER exits of every kind; fee 5.5 bps per side on
  notional; half-spread 0.5 bps per side; slippage 1.0 bps on entry and on target/time exits, 3.0 bps
  on stop exits; funding at the 8-hour marks while open (Binance proxy rate x notional). Reported per
  variant: gross R, fee R, spread+slippage R, funding R, net R, and the same in bps of notional. Stress:
  fees, spread and slippage x1.5 (funding unchanged). No result is judged on gross P&L.
- RISK: 0.125% of equity per trade (sensitivity 0.10% / 0.15%); the frozen ladder
  `btc_swing/risk/sizing.py::size_position` chooses the leverage that fits the margin cap (25%) with
  the liquidation buffer checks (ratio 3.0, 3 x atr1h); leverage never sets risk; non-compounding
  reference equity 5,000 USDT; one BTC position at a time; no averaging, pyramiding or martingale. A
  setup that arms while a position is open is skipped (counted in the funnel).

## 5. The A/B ladder (mandatory) and the funnel

Four variants of the SAME price structure, same stop, exits, costs and risk:

| step | rules |
|---|---|
| A PRICE_ONLY | trend + impulse (>= 1.0 ATR) + pullback (25-60%, 2-6 bars, origin intact) + 1m breakout |
| B + IMPULSE_RVOL | A + impulse RVOL >= 1.25 |
| C + PULLBACK_VOLUME | B + pullback volume ratio <= 0.75 |
| D + TRIGGER_FLOW | C + 1m rvol >= 1.25 + taker imbalance >= 0.20 (signed) + delta sign |

For each step and window: trades/day, win rate, gross R, cost R (fees + spread/slippage + funding),
net R, PF (net), max DD, median hold, LONG and SHORT counts and net R, bps equivalents.

Funnel (counts of distinct impulse legs per calendar day, both directions): raw legs (size >= 0.5 ATR,
any trend) -> after trend -> after impulse (>= 1.0 ATR) -> after impulse RVOL -> after pullback (a valid
pullback armed at least once) -> after pullback-volume rule -> after 1m trigger (a breakout candle during
an armed window) -> after flow confirmation -> executed (after the one-position rule, the micro-stop
rejection and sizing). Reported on every window.

## 6. Parameter discipline (sequential ablation; no Cartesian product)

Everything below is run on TRAIN only, one change at a time from the anchors, and is reported; nothing is
selected from it except as stated in section 7:
impulse ATR 0.8 / 1.0 / 1.2 (on A and D); impulse RVOL 1.1 / 1.25 / 1.5 (on B); pullback bounds
[0.20,0.60] / [0.25,0.60] / [0.25,0.70] (on A); pullback volume ratio 0.60 / 0.75 / 0.90 (on C); 1m RVOL
1.0 / 1.25 / 1.5 (on D); taker imbalance 0.10 / 0.20 / 0.30 (on D); delta sign vs median mode (on D);
minimum stop 0.40 / 0.50 / 0.60% (on A and D); exits A / B / C and time stop 120 / 180 (on A and D);
risk 0.10 / 0.125 / 0.15% (on the selected specification); accel diagnostic (section 3.8); cost stress
x1.5 (every variant). Total: about 35 TRAIN simulations, all pre-declared here.

Temporal consistency: every TRAIN figure is also shown per half-year (2022H1 .. 2024H1, five folds).

## 7. Selection and freeze (TRAIN only; written now)

1. The anchors of section 3-4 with exit B, time stop 120, min stop 0.50%, risk 0.125% are the
   pre-registered PRIMARY specification of each ladder step.
2. "Best simple specification" = the ladder step (A, B, C or D at anchors) with the highest TRAIN net R
   per trade among those with net R > 0 in at least 4 of 5 half-years and >= 2.0 executed trades/day; if
   none qualifies, the step with the highest TRAIN net R is named and V9 is expected to FAIL.
3. The accel promotion rule of section 3.8 is the only sensitivity that may change the frozen
   specification, and only on the step selected in 2.
4. Neighbourhood results never change the anchors: a neighbour that looks better on TRAIN is reported as
   a sensitivity, not adopted.
5. The frozen specification (and, for the A/B question, all four ladder steps at anchors) is then run
   ONCE on VALIDATION 1 and ONCE on RECENT VALIDATION. Nothing is tuned on either window.

## 8. Success criteria (both validation windows, separately)

- Profitability PASS: net R per trade > 0 AND PF (net) > 1.0 in EACH window (PF >= 1.10 preferred);
  max drawdown < 10% of reference equity in each window; the result must hold under the primary
  conservative costs (the x1.5 stress is reported, not required).
- Frequency PASS: mean executed trades per calendar day in [3, 6] in EACH window; also reported: median
  trades/day, % days with 0 / 1-2 / 3-6 / > 6 trades, median hold.
- A profitable specification below 3 trades/day is recorded as an interesting lower-frequency strategy
  that does NOT meet the product goal. Frequency is never bought by weakening rules.
- If volume filters raise net expectancy but cut frequency, the trade-off is reported; if PRICE_ONLY
  beats the volume-confirmed version on TRAIN and both validation windows, the volume rules are declared
  useless and removed from the recommended specification.

## 9. Output (the report)

One concise comparison table PRICE_ONLY vs + IMPULSE_RVOL vs + PULLBACK_VOLUME vs + TRIGGER_FLOW per
window with trades/day, win rate, gross R, cost R, net R, PF, max DD, median hold, LONG, SHORT; the
funnel; the half-year folds; the neighbourhood tables; exits and time stops; cost stress; risk
sensitivity; the accel diagnostic; then the six answers: which conditions genuinely add edge, which only
reduce trade count, the best simple specification, expected trades/day, PASS/FAIL profitability,
PASS/FAIL 3-6 trades/day. STOP before the locked holdout. Nothing activated; no Bybit order; V5.1
untouched; no history rewrite.

## 10. Harness notes recorded before the TRAIN run (2026-10-12)

- The harness (`btc_swing/v9/`) was verified on synthetic data (tests) and on a four-month development
  slice (2023-01 -> 2023-04, inside TRAIN) to confirm that legs, arming, triggers, exits and costs behave
  as written. Two facts from that slice are recorded now so that they cannot be mistaken for post-hoc
  changes: (1) the structural stop (pullback low - 0.1 ATR5m, entry after a 1m breakout) is typically
  0.1-0.3% of price (median ~0.2%; ATR5m ~0.13%), so the 0.50% minimum REJECTS most triggers in the
  primary mode; the `floor` stop mode is therefore added as a pre-declared sensitivity (section 4, 6) so
  that the A/B ladder can also be read at the 3-6 trades/day frequency the owner asked about. The
  primary classification stays on `reject` mode exactly as written. (2) The impulse definition (leg of
  up to 8 completed 5m bars from its lowest low to its peak, size >= 1.0 ATR5m(14)) is a weak filter
  because an 8-bar range usually exceeds one bar's ATR; the pre-registered neighbourhood 0.8 / 1.0 / 1.2
  is kept as written and the funnel reports the cut honestly.
- Nothing in the TRAIN ladder, folds, neighbourhoods or validation windows had been computed when this
  section was written. Harness commit hash recorded in `reports/v9/manifest.json`.

## 11. Outcome (2026-10-12; `reports/BTC_SWING_V9_SIMPLE_TREND_VOLUME_PULLBACK.md`, tables `reports/v9/`)

Executed exactly as pre-registered (config `fe9fb2f28b74`, Bybit 1m dataset `92d27161c194`, 1,322 frame days,
holdout not read). **Classification C — FAIL on both validation windows.**

| window | PRICE ONLY | + IMPULSE RVOL (frozen) | + PULLBACK VOLUME | + TRIGGER FLOW |
|---|---|---|---|---|
| TRAIN /day, net R, PF | 0.93, -0.185, 0.72 | 0.88, -0.170, 0.74 | 0.65, -0.185, 0.72 | 0.59, -0.180, 0.72 |
| VALIDATION 1 | 0.95, -0.228, 0.65 | 0.91, -0.231, 0.65 | 0.61, -0.126, 0.79 | 0.52, -0.311, 0.51 |
| RECENT | 0.83, -0.183, 0.73 | 0.78, -0.183, 0.73 | 0.47, -0.175, 0.72 | 0.37, -0.172, 0.71 |

Gross edge of the price structure is ~3 bps per trade against a 15 bps conservative round trip; each ladder step
is positive in only one of five TRAIN half-years. The 0.50% minimum stop rejects ~88% of triggers (median
structural stop ~0.2%); the pre-declared floor mode reaches 4.5-4.8 trades/day but at -0.23 to -0.34 R net
(0/5 folds positive). No volume rule adds edge on both validation windows; the accel gate is not promoted.
Selection rule: no step qualified; B (highest TRAIN net R) was frozen and failed. Nothing tuned on validation.

## 12. Stop-geometry sensitivity study (owner request 2026-10-12; pre-registered before the run)

Question: do wider stops with proportionally larger targets turn the EXISTING V9 setup into a positive net
strategy? The entry signal is unchanged (no new indicator, no threshold change, nothing tuned on
validation, holdout untouched). Study module `btc_swing/v9/stop_study.py`, CLI `btc-swing v9 stop-study`,
report `reports/BTC_SWING_V9_STOP_GEOMETRY_STUDY.md`, table `reports/v9/stop_study.json`.

- Bases (both pre-declared): the frozen specification `FROZEN_B_IMPULSE_RVOL` (primary) and PRICE ONLY (A,
  larger sample, control). Everything except the stop floor and the target is byte-identical to section 4:
  taker costs, 120-minute time stop with the 0.5R progress rule and 240-minute hard maximum, one position,
  non-compounding reference equity, the frozen sizing ladder.
- Stop floors 0.50 / 0.75 / 1.00 / 1.25 / 1.50% in FLOOR mode: stop = the structural stop when it is at least
  the floor, otherwise exactly the floor (the pre-declared `floor` mode of section 4). Account risk is
  constant at 0.125% of equity, so a wider stop MUST reduce the position size (qty = risk / stop distance);
  USDT risk per trade never rises. A secondary table shows REJECT mode at the same floors (only trades whose
  structural stop already exceeds the floor) for completeness.
- Exits: full exit at +1.0R, +1.5R, +2.0R; split 50% at +1.0R and 50% at +2.0R. Matrix = 5 floors x 4
  exits x 2 bases x 3 windows (120 simulations) plus one pre-declared time-stop sensitivity at the 1.00%
  floor (time stop 180 min, hard maximum 360) for the +1.5R and +2.0R exits, to show whether the identical
  120-minute time stop is what caps the wider geometries.
- Reported per cell and window: trades/day, win rate, gross R, cost R (and cost as % of 1R), net R with a
  day-cluster 95% CI, PF, max drawdown, median hold, stop-outs, TP hits, time exits (time stop + hard
  maximum), LONG and SHORT separately (n, net R), mean actual stop %, and the one-position cost (trigger
  attempts skipped because a position was open, per day).
- Reading rules written now: the question is whether net expectancy improves monotonically or materially
  from 0.50% toward 1.0-1.5%; a cell is useful only with net R > 0 AND PF > 1 on BOTH validation windows;
  PF improving while net stays negative is not success. If every cell stays negative on validation, stop
  geometry is not the problem and the strategy family is closed. This is a sensitivity matrix, not
  parameter mining: no cell is selected, nothing is adopted.

Outcome (2026-10-12; `reports/BTC_SWING_V9_STOP_GEOMETRY_STUDY.md`, 162 simulations): **no stop/target cell is
net positive with PF > 1 on either validation window, for either base.** Net R improves monotonically with the
floor (frozen base, +1.5R, TRAIN: -0.27 R at 0.50% -> -0.08 R at 1.50%; Spearman 1.0 in 23 of 24 rows) exactly
as the cost share of 1R falls (28% -> 9.5%), while gross R stays flat at +0.01 to +0.03 R at every width. At wide
floors 75-80% of trades end on the identical 120-minute time stop; 180 minutes does not change the sign. Executed
frequency stays 2.5-3.1/day (frozen) and 4.0-4.7/day (price only) with ~3-5 skipped triggers/day from the
one-position rule. Stop geometry is not the problem; the V9 family is closed. Nothing adopted.
