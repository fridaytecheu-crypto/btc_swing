# BTC Swing V3 — Active Multi-Timeframe Swing (frozen design)

Status: pre-registered design, committed BEFORE any V3 strategy result was inspected. Every
number below is also in `config/btc_swing_v3.yaml` (config hash recorded in the run manifest).
Paper/backtest research only: no live trading, no exchange keys, no order placement, no real
money. V1 (`btc_swing/` outside `v3/`, `config/btc_swing.default.yaml`) and V2 (`btc_swing/v2/`,
`config/btc_swing_v2.default.yaml`) are closed, immutable and untouched by V3.

**Central question.** Can a newly designed multi-timeframe BTC active-swing strategy naturally
generate roughly 1–3 trades per day, with typical holds of hours rather than minutes, while
maintaining positive NET expectancy after realistic fees, slippage and funding across different
market regimes?

## 1. What V3 is and is not

V3 is a new strategy generation, not a refinement of V1. It reuses infrastructure only: the
immutable Binance Vision archive and dataset store, the PIT `MultiTfSeries` / `MarketView`
(5m/15m/1h/4h/1d completed bars, causal indicators), `AuxSeries` (funding, open interest,
long/short and taker ratios, premium, mark price), the cost model, the funding schedule, risk
sizing and the isolated-margin liquidation model, the `Position` ledger (four returns, R multiple,
MFE/MAE, counterfactual R hits), the episode state machine (`EpisodeManager`, reused as a generic
lifecycle with V3 detectors), hashing/manifests and report tooling. V3 setup logic, regime
definition, exits, risk defaults and the account overlay are new and live in `btc_swing/v3/`.

Trading style targeted: BTCUSDT perpetual, LONG and SHORT, scan every 5 minutes, roughly 1–3
trades per day when opportunities exist (15–70 per month depending on regime), typical hold
2–12 h, 12–48 h allowed while structure holds, not scalping, no quota.

## 2. Timeframe architecture

- 4H = structural context (trend state, swing structure, volatility state).
- 1H = setup generation (impulses, levels, sweeps, expansion bars; swing points with fractal
  half-width 2, confirmed two bars later).
- 15m = confirmation / trade structure (reclaims, acceptance, rejection, continuation; trail).
- 5m = execution timing only (a deterministic zone rule after confirmation). 5m never generates
  a trade on its own; every episode originates from a 1H/4H structure.
- 1D = slow macro context inside the regime label only.

Indicators (one set, no search): EMA 21 and EMA 50 on every timeframe, Wilder ATR 14, fractal
swings with half-width 2. Warm-up: 220 completed bars on 5m/15m/1h/4h, 60 on 1d.

## 3. Structural context

- 4H trend: UP if EMA21(4h) > EMA50(4h) and close(4h) > EMA50(4h); DOWN mirrored; else NEUTRAL.
- 1H alignment: EMA21(1h) > EMA50(1h) for LONG, < for SHORT.
- 1H volatility percentile: rank of the current ATR14(1h) among the previous 100 completed 1H
  ATR values.

## 4. The four setup families (frozen definitions)

Every family defines, before any order exists: entry zone, 15m confirmation, structural
invalidation (pre-entry cancel), stop (price and reason), structural target, and requires a
minimum reward-to-risk of 1.5 between the entry reference (the 15m confirmation close) and the
structural target, else no setup. SHORT is the exact mirror of LONG in every family. ATR means
ATR14(1h) unless stated.

### A. TREND_PULLBACK_CONTINUATION
- Context: 4H trend UP and 1H alignment UP (mirror for SHORT).
- Impulse: last confirmed 1H swing low -> last confirmed 1H swing high, size >= 1.5 ATR, the
  swing high more recent than the swing low.
- Pullback: the 1H close lies inside the retracement zone [high - 0.786 x impulse, high - 0.382 x
  impulse]; the pullback low (lowest 1H low since the swing high) stays above the swing low
  (structure intact).
- 15m confirmation (stabilisation/reclaim): 15m close > EMA21(15m) while the previous 15m close
  <= EMA21(15m), bar closes up, and the close is within the retracement zone padded by 0.25 ATR.
- Entry zone: retracement zone padded by 0.25 ATR.
- Stop: pullback low - 0.3 ATR (reason: the pullback failed and structure gave way).
- Structural target: the 1H swing high (continuation). TP2 = target, capped at 4R.
- Pre-entry invalidation: 1H close below the swing low; 5m close below the stop level.

### B. BREAKOUT_RETEST
- Level: L = highest 1H high of the 48 completed 1H bars ending two bars before the current one
  (a two-day structural high; mirror: lowest low).
- Break: a completed 1H close > L + 0.1 ATR (the initial spike is never entered). The consolidation
  height H = L - lowest 1H low of those 48 bars.
- Retest and acceptance within 12 1H bars after the break: a 15m bar whose low <= L + 0.25 ATR
  (touches the level from above) that closes >= L with close > open (the level holds as support).
- Entry zone: [L - 0.25 ATR, L + 0.5 ATR].
- Stop: min(retest-bar low, L) - 0.5 ATR (reason: failed breakout, price accepted back inside).
- Structural target: L + H (measured move), TP2 capped at 4R.
- Pre-entry invalidation: 1H close < L - 0.25 ATR; 5m close below the stop level.

### C. LIQUIDITY_SWEEP_REVERSAL
- Level: the last confirmed 1H swing low or the last confirmed 4H swing low (both are watched;
  the one swept defines the setup). Mirror: swing highs.
- Sweep: a 15m low < level - 0.15 ATR (price trades beyond the level).
- Failure to hold / rejection within 4 15m bars of the sweep bar: a 15m close back above the
  level with the close in the upper half of its own range.
- Entry zone: [level - 0.1 ATR, level + 0.5 ATR].
- Stop: sweep extreme - 0.25 ATR (reason: the sweep continued; no reversal).
- Structural target: the last confirmed 1H swing high (opposite side of the structure), TP2
  capped at 4R.
- Pre-entry invalidation: 15m close below the sweep extreme; 5m close below the stop level.

### D. VOLATILITY_EXPANSION_CONTINUATION
- Compression: the 1H ATR percentile (previous 100 bars) was <= 0.25 at any of the last 12
  completed 1H bars.
- Expansion bar (1H, completed): range >= 1.8 x ATR14 of the prior bar; close location in the
  bar >= 0.7 (LONG); close > the highest high of the previous 12 1H bars; volume >= 1.5 x the mean
  volume of the previous 20 1H bars; 1H structure supports direction (close > EMA21(1h)); no
  extreme chase: close - previous 12-bar high <= 1.5 ATR.
- 15m confirmation (continuation): within 4 15m bars after the expansion bar closes, a 15m close
  above the previous 15m high, with that close <= expansion high + 0.5 ATR.
- Entry zone: [expansion-bar midpoint, expansion high + 0.5 ATR].
- Stop: expansion-bar midpoint - 0.3 ATR (reason: the thrust's second half was given back).
- Structural target: expansion high + expansion range (measured move), TP2 capped at 4R.
- Pre-entry invalidation: 15m close below the expansion midpoint; 5m close below the stop level.

## 5. Execution (simple, deterministic)

Lifecycle per family (the generic state machine): WATCH (setup present) -> ENTRY_READY when a
completed 5m close lies inside the entry zone AND the family's 15m confirmation holds ->
TRIGGERED on the next completed 5m close that is still inside the entry zone (at most 6 5m bars,
then back to WATCH) -> fill at the NEXT 5m open plus entry slippage. No further confirmation
chain. Watch timeout 24 h (288 5m bars) from detection. Each family's lifecycle runs
independently; when several families trigger on the same bar the priority is A, B, C, D and LONG
before SHORT.

## 6. Stops

Always structural, fixed before entry (section 4), with an ATR buffer. Allowed stop distance at
entry: 0.4 ATR to 3.0 ATR (outside this the setup is rejected as noise / not a swing). Never
searched.

## 7. Exits (pre-registered, not optimised)

- TP1 = +1.0R: close 40% of the position; stop moves to breakeven.
- TP2 = structural target (>= 1.5R by construction, capped at 4R): close 30%.
- Remainder: trail at the last confirmed 15m swing low - 0.5 x ATR14(15m) (LONG; mirror SHORT),
  active after TP1, only ever tightens; evaluated at each 5m close with the move applied from the
  next bar.
- Time cap 48 h: exit at the close.
- Same-bar stop and target: stop first. Liquidation on mark price (fallback traded extremes).
- Counterfactual 1R / 1.5R / 2R / 3R hits before the initial stop are recorded on every trade.

## 8. Costs (frozen)

Taker 5 bps on every fill (entries and all exits; no maker assumption), slippage 2 bps on entries,
5 bps on stop-type exits, funding from the archive at every funding timestamp while in position.
Every family reports gross expectancy, cost drag and net expectancy; positive gross with negative
net is a failure.

## 9. Risk, leverage, account

- Planned risk per trade 0.25% of a fixed 10,000 USDT research equity (the default stream). A
  0.5% stream is run for reporting only; no decision uses it.
- Leverage: smallest of {1, 2, 3, 5, 10}x that provides the required notional within a 25%
  isolated-margin cap; liquidation distance >= 3 x stop distance and >= 3 x ATR(4h);
  maintenance margin 0.5%. Leverage caps 1/2/3/5/10x are reported; 10x must never be needed.
- Per trade: notional, margin, planned account risk, leverage, stop distance, liquidation
  distance and stop-to-liquidation buffer are recorded.
- One net BTC exposure at a time; a new setup may enter after the previous position closes.
  Per-family cooldown 12 5m bars after a close.
- Safety overlay (reported separately, never used to select logic): after 3 full-risk losses
  (net R <= -0.9) in a UTC day OR a realised daily net loss <= -0.75% of equity, no new entries
  that day. Daily realised loss and consecutive-loss counters are tracked.

## 10. Regime (V3 structural context, PIT; reported, not used as a gate)

Priority: UNCLEAR (warm-up) > VOLATILITY_EXPANSION (1H ATR percentile >= 0.8 or ATR14(4h) /
ATR14(4h) ten bars earlier >= 1.3) > VOLATILITY_COMPRESSION (percentile <= 0.2) > TREND_UP /
TREND_DOWN (4H trend state agrees with the sign of the 1D EMA50 slope over 5 bars) > TRANSITION
(4H trend state and 1D slope disagree) > RANGE (4H NEUTRAL, normal volatility). Families use only
their own structural conditions (section 4); no eligibility table.

## 11. Derivatives context

At every entry the `AuxSeries` snapshot is recorded (funding last / 3-mean / annualised, funding
z-score over 90 observations, open interest, OI change 1h/4h/24h, price-OI interaction, long/short
ratios, taker ratios 1h/4h and their change, premium and its 1h mean and change, last-minus-mark,
volume acceleration 5m/1h). Reported as context (means by outcome, univariate rank correlation
with net R); not a gate.

## 12. Data and evaluation protocol

Window 2022-01-01 -> 2026-10-01 (data from 2021-10 is warm-up). All of it has been inspected in
V1/V2: it is development data and no V3 result on it is untouched out-of-sample. Chronological
reporting: 2022, 2023, 2024, 2025, 2026 YTD, combined, and quarters. Deterministic research:
no ML in this pass. PIT audit: deterministic rerun; truncation audit (decisions identical with
later data removed); resampling oracle vs native archive bars; look-ahead guard in `MarketView`.
Null benchmarks: time-matched and regime-matched random entries with the same side, stop
distance, ATR and exits under the same costs (K = 10 per trade); BTC directional drift over the
matched holding windows.

## 13. Pre-declared classification (encoded in `btc_swing/v3/evaluation.py`)

Evaluated on the default 0.25% stream, raw (no overlay), combined 2022-01..2026-09:
1. net expectancy >= +0.10R per trade;
2. profit factor >= 1.20;
3. chronological stability: net expectancy > 0 in >= 4 of the 5 calendar years and in >= 60% of
   quarters with >= 10 trades;
4. cost drag <= 50% of gross expectancy (net >= 0.5 x gross);
5. max drawdown (trade curve) <= 12%;
6. natural frequency 0.5–3 trades per day on average (15–90 per month) and median hold 2–12 h;
7. no single family > 60% of net P&L, net expectancy excluding the 5 best trades >= +0.05R, and
   no single quarter > 50% of net P&L;
8. the strategy beats the time-matched and regime-matched nulls and the matched BTC drift.
A — STRUCTURAL EDGE DEMONSTRATED, FREEZE FOR FORWARD PAPER TEST: all eight met.
B — PROMISING STRUCTURAL SIGNAL, CONTROLLED V3.1 RESEARCH JUSTIFIED: net expectancy > 0, PF > 1.05,
positive in >= 3 of 5 years, average frequency >= 0.3 trades/day, net expectancy excluding the 5
best trades > 0, and criterion 8 met, but any other criterion fails.
C — NO ROBUST STRUCTURAL EDGE: otherwise.
Thresholds are not moved after seeing results. No family is dropped, no threshold adjusted, no
TP/SL or leverage optimised, no re-run of variants.

## 14. Deliverables

`btc_swing/v3/` (config schema, regime, setups, engine, risk overlay, null benchmark, evaluation,
report), `config/btc_swing_v3.yaml`, `tests/v3/`, CLI `btc-swing v3 research`,
`reports/BTC_SWING_V3_ACTIVE_SWING_RESEARCH.md` (26 sections + one classification), run manifest
in `manifests/`.
