# BTC Swing V4 — Event & Positioning Driven Active Swing (frozen design)

Status: pre-registered design, committed BEFORE the first V4 result run (the commit hash is
recorded in the V4 report and run manifest). Every number here is also in
`config/btc_swing_v4.yaml`. Paper/backtest research only: no live or paper trading engine, no
exchange keys, no real money. V1, V2 and V3 artefacts are closed, immutable and untouched.

**Central question.** Do BTC positioning and participation events — deleveraging, positioning
resets and participation-confirmed breakouts — provide a repeatable gross directional edge large
enough to survive realistic execution costs while still supporting an active swing style of
roughly 1–3 trades per day?

## 1. Lessons carried from V1–V3

- V1: structural setups had a small positive gross edge that costs erased; the untouched 2025–26
  holdout was net negative. V2: a learned ranking of the V1 candidates found no usable signal.
- V3: an active 4H/1H/15m/5m price-structure generator hit the activity profile (1.5 trades/day,
  3 h median hold) but gross expectancy was already slightly negative and the average stop (0.66%
  of price) made costs 0.34R per trade; null entries with the same geometry lost almost as much.
- Consequences for V4: (a) the thesis must come from observable participation/positioning events,
  not price patterns; (b) event edge is measured BEFORE any execution (two stages); (c) stops get a
  volatility floor so costs are a small fraction of 1R; (d) gross expectancy is the gate.

## 2. Central principle and architecture

Every candidate needs at least one participation/positioning event (open-interest expansion or
contraction, funding crowding or reset, taker-flow imbalance, premium dislocation or
normalisation, abnormal volume). Price action decides HOW to enter; positioning decides WHY.
Timeframes: 4H = structural direction; 1H = event context and swing thesis (all event features are
computed on completed 1H bars); 15m = confirmation/stabilisation; 5m = deterministic execution
timing only (a 5m close inside the zone after confirmation; fill at the next 5m open). 1D is used
only inside the 4H trend-state helper (EMA21/EMA50 on 4H; no daily rule).

## 3. PIT feature frame (1H granularity, rolling, past-only)

At the close time t of every completed 1H bar, from rows with observation time <= t:
- OPEN INTEREST (metrics, 5-minute rows): level; change over 1h, 4h, 24h (vs the value 1/4/24 h
  earlier); rolling z-scores of the 4h and 24h changes.
- FUNDING (8-hourly): last rate; change vs the previous rate; z-score against the previous 90
  funding observations; crowding direction = sign of the z-score; "cooled" = (max z over the
  previous 48 h) - (z now) >= `cooling_z_drop` (and mirror for negative crowding).
- TAKER FLOW (kline taker-buy volume / volume): buy ratio over the last 1h and 4h; rolling
  z-scores (imbalance); acceleration = ratio 1h - ratio 4h.
- PREMIUM / BASIS (premium index klines): last 5m premium; 1h mean; z-score of the 1h mean;
  "cooled" defined as for funding.
- VOLUME (1H bars): z-score of the log 1h volume; sum over 4h with its z-score; acceleration.
- PRICE (1H/4H bars): signed 1h/4h/24h returns and their z-scores; realised volatility (24 h of 5m
  returns); ATR14(1h); 4H trend state; distance to the 1H EMA21 and the last confirmed 4H swing
  low/high; 1H range position (48 bars).
Every z-score uses the previous `z_window_bars` = 720 completed 1H observations (30 days),
excluding the current one, with at least `z_min_periods` = 240; the funding z uses the previous
90 funding observations. No absolute BTC threshold appears in an event definition except the
structural level definitions (highest/lowest 1H extreme over 48 bars; 4H swing points with
fractal half-width 2).

## 4. The three families (frozen event + confirmation + geometry)

All thresholds are in z-units of the feature's own trailing distribution. ATR = ATR14(1h). LONG is
written; SHORT is the exact mirror (signs of returns, taker imbalance, funding/premium crowding
and structure flipped). An event fires at the close of a completed 1H bar (the event bar).

### A. DELEVERAGING_REVERSAL (LONG after a downside leverage flush)
- Event (all required): 4h return z <= -2.0 (violent impulse down); OI 4h-change z <= -2.0
  (significant contraction); 4h volume z >= +1.0 (participation); 4h taker-imbalance z <= -1.0
  (aggressive selling). Funding/premium stress is recorded as strength, not required.
- Event strength = -(OI 4h-change z). Flush extreme = lowest 1H low of the last 4 bars.
- Thesis window 12 h. Continuation fails and 15m stabilises: a 15m close above EMA21(15m) with the
  previous 15m close <= EMA21(15m), the bar closing up, while the 5m close is at or above the
  flush extreme (no new low). Entry zone = [flush extreme, flush extreme + 1.5 ATR].
- Invalidation before entry: a 15m close below the flush extreme (the flush continues).
- Stop = the farther of (flush extreme - 0.25 ATR) and (entry reference - 1.5 ATR), at most
  4.0 ATR from the reference. Structural objective recorded: the 1H swing high before the impulse.

### B. POSITIONING_RESET_CONTINUATION (LONG, trend continuation after a reset)
- Context: 4H trend UP (EMA21 > EMA50 and close > EMA50 on 4H).
- Pullback: 1H close below EMA21(1h) and 4h return z <= -0.5, with the 1H close above the last
  confirmed 4H swing low (structure survives).
- Reset event (required): OI 24h-change z <= -1.0 (OI contracts or stops expanding) AND at least one
  of: funding cooled (max funding z over the previous 48 h minus z now >= 1.0), premium cooled (same
  rule on the premium z), or 4h taker-imbalance z <= -1.0 (opposite flow appeared and structure
  held). Event strength = number of reset components present (1–3) plus -(OI 24h z)/2.
- Thesis window 24 h. Participation returns: a 15m EMA21 reclaim (as in A) AND the 1h taker buy
  ratio > 0.5. Entry zone = [lowest 1H low of the last 24 bars, that low + 1.5 ATR].
- Invalidation before entry: 1H close below the 4H swing low.
- Stop = the farther of (4H swing low - 0.25 ATR) and (reference - 1.5 ATR), at most 4.0 ATR.

### C. PARTICIPATION_BREAKOUT (LONG)
- Event (all required): 1H close above the highest 1H high of the previous 48 bars (level L);
  1h volume z >= +2.0; OI 4h-change z >= +1.0 (new participation); 1h taker-imbalance z >= +1.0
  (aligned flow); not overcrowded: funding z <= +2.0 and premium z <= +2.0.
- Event strength = (volume z + OI z + taker z) / 3.
- Thesis window 12 h. Acceptance: two consecutive completed 15m closes above L after the event bar,
  the latest close <= L + 1.5 ATR (no chase of a single explosive candle). Entry zone =
  [L - 0.25 ATR, L + 1.5 ATR].
- Invalidation before entry: a 1H close below L - 0.25 ATR.
- Stop = the farther of (L - 0.5 ATR) and (reference - 1.5 ATR), at most 4.0 ATR. Structural
  objective: L + height of the 48-bar range.

Common: the entry reference is the 5m close that completes the confirmation; the 5m rule is "a
completed 5m close inside the zone" (at most 6 bars after confirmation, else back to WATCH); the
same event is not re-armed within 24 h (anchor de-duplication on the flush extreme / 24-bar low /
level); one net BTC exposure; priority A > B > C, LONG before SHORT when simultaneous.

## 5. Stage A — event edge (before any execution)

Every event (every family and side, independent of the slot) records, from the event bar's close
price: signed forward returns at 30m, 1h, 2h, 4h, 8h, 12h and 24h (sign = the family's trade
direction), MFE and MAE over 24 h in ATR(1h) units, the event strength, the year and the regime.
Reported: pooled mean signed return per horizon with t-statistics, by family and side, by year,
by strength tercile (monotonicity), and against the unconditional distribution of signed 1H
returns over the same horizons (all 1H bars, both signs). Stage A is the gate: an event family
whose forward returns are indistinguishable from zero cannot be rescued by execution.

## 6. Stage B — execution (generic, frozen)

- Exit architecture (all families): TP1 at +1.0R (40%; stop to breakeven), TP2 at +2.0R (30%),
  remainder trails the last confirmed 1H swing low - 0.5 ATR(1h) (mirror SHORT), active after TP1,
  only tightening, applied from the next bar; time cap 48 h; stop-first on a bar that touches both;
  liquidation on mark price. Counterfactual 1R/1.5R/2R/3R-before-initial-stop recorded.
- Costs (frozen primary model): taker 5 bps on every fill, slippage 2 bps entries / 5 bps stop-type
  exits, archive funding. A clearly labelled sensitivity run uses a Bybit-style schedule (taker
  5.5 bps entries and stops, maker 2 bps on target fills); it never classifies anything.
- Risk: 0.25% planned risk per trade on a fixed 10,000 USDT research equity; a 0.5% stream is
  reported only. One position at a time; max open planned risk = 0.25%; isolated margin; leverage
  ladder 1/2/3/5/10x chosen for margin efficiency under the V1 liquidation constraints
  (stop-to-liquidation >= 3x, >= 3 ATR(4h), 25% margin cap). Leverage caps 1–10x reported.
- Safety overlay (reported separately, never used to classify): 3 full-risk losses (net R <= -0.9)
  in a UTC day or a realised daily loss <= -0.75% of equity -> no new entries that day.
- Reported per trade: notional, margin, leverage, stop distance (% and ATR), liquidation price and
  buffer, round-trip cost as % of the stop distance, cost drag in R.

## 7. Data, protocol, audits

Window 2022-01-01 -> 2026-10-01 (warm-up from 2021-10; z-scores need 30 days). All of it is
development data inspected by V1–V3; no V4 result is untouched out-of-sample. Chronological
reporting by year (2022, 2023, 2024, 2025, 2026 YTD) and quarter. Deterministic research, no
ML. Audits: deterministic rerun, truncation audit (decisions and events identical with later data
removed), resampling oracle, look-ahead guard. Null benchmarks: time-matched and regime-matched
random entries with each trade's side, stop %, ATR, sizing, exits and costs (K = 10), plus the
signed BTC drift over the matched holding window. Regime labels (reported, not gated): 4H trend
state (UP/DOWN/NEUTRAL) crossed with the 1H ATR percentile state (expansion >= 0.8, compression
<= 0.2, else normal).

## 8. Pre-declared classification (encoded in `btc_swing/v4/evaluation.py`)

Stage A gate (pooled over all events, signed): mean forward return at 4 h AND at 8 h > 0 with
t >= 2.0, and the top strength tercile's 8 h mean >= the bottom tercile's.
Trade criteria (raw 0.25% stream, combined 2022-01..2026-09):
1. gross expectancy >= +0.15R; 2. net expectancy >= +0.08R; 3. PF >= 1.20; 4. net > 0 in >= 4 of 5
calendar years and >= 60% of quarters with >= 10 trades; 5. cost drag <= 50% of gross expectancy;
6. max drawdown (trade curve) <= 12%; 7. 0.5–3 trades/day and median hold 2–12 h; 8. net
expectancy without the 5 best trades >= +0.04R and no family > 60% of net P&L; 9. beats the
time-matched and regime-matched nulls.
A — EVENT EDGE DEMONSTRATED, FREEZE FOR FORWARD PAPER TEST: Stage A gate AND all nine trade
criteria.
B — EVENT EDGE EXISTS, CONTROLLED V4.1 SELECTION RESEARCH JUSTIFIED: Stage A gate AND gross trade
expectancy >= +0.10R AND criterion 9, but any other trade criterion fails.
C — NO ROBUST EVENT EDGE: otherwise.
Nothing is tuned, dropped or re-run after seeing results.

## 9. Deliverables

`btc_swing/v4/` (config, features, events, engine, stage_a, null, evaluation, report, research),
`config/btc_swing_v4.yaml`, `tests/v4/`, CLI `btc-swing v4 research`,
`reports/BTC_SWING_V4_EVENT_POSITIONING_RESEARCH.md` (32 sections + one classification), run
manifest in `manifests/`.
