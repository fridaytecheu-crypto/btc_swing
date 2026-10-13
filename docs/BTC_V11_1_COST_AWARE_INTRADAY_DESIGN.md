# BTC V11.1 — COST-AWARE HIGH-CONVICTION INTRADAY (pre-registered design)

Status: PRE-REGISTERED before any result (2026-10-13). ONE historical experiment. Nothing in this
document may be changed after the run; results are reported as they fall. RESEARCH ONLY: no shadow
runner, no exchange client, no Demo authority exists for V11.1 unless the success criteria in section
10 are met (and even then Demo is never activated by this track).

V10 (ACTIVE on Bybit Demo) and V11 (frozen forward SHADOW benchmark, freeze `manifests/v11_forward_freeze.json`)
are not modified, restarted or read by V11.1. V11.1 imports the V11 feature builder, scorer and engine
as a read-only library (`btc_swing.v11.features`, `.scoring`, `.engine`) and subclasses the engine in
its own namespace `btc_swing/v11_1/`. No V11 file changes; `tests/v11_1` asserts it.

## 0. Why V11.1 exists and the two hypotheses

V11 VALIDATION (2024-07..2025-06): 974 trades, 2.67/day, median hold 20 min, gross -0.051R, cost 0.364R,
net -0.415R, PF 0.19; every family and both sides negative.

- **H1 (entry quality):** V11 admits too many mediocre setups. Requiring INDEPENDENT EVIDENCE from
  several information groups, no trend contradiction and tighter family requirements, using the SAME
  information groups and formulas, improves gross R per trade.
- **H2 (stop geometry / cost):** the 0.35% stop floor makes the fixed round-trip cost (about 14 bps of
  notional) enormous in R. A wider structural stop (0.55% minimum) plus an explicit cost-in-R filter
  materially reduces cost/R without turning trades into multi-hour positions.

Both are tested in one combined specification (this is not a factorial study; the report answers the six
owner questions from the single run plus the V11 benchmark).

## 1. Objective

BTCUSDT perpetual, LONG and SHORT, ~2-5 quality trades/day (frequency is not forced), typical hold
15-45 min, hard maximum 60 min, one position at a time, deterministic and explainable. Net positive
expectancy after conservative taker costs matters more than frequency.

## 2. Information universe (unchanged from V11)

Feature set `v11-feat-1` (`btc_swing/v11/features.py`, formulas in `docs/BTC_V11_FAST_MULTIFACTOR_DESIGN.md`
section 3) and the group sub-scores `v11-score-1` (`btc_swing/v11/scoring.py`) are reused byte-for-byte:
trend (VWAP, EMA9/21/50, 15m structure), momentum (RSI7/14, ADX14, ROC3, 5m acceleration), volatility
(ATR band, BB width, BB deviation), volume/flow (5m imbalance, 15m delta, RVOL, intensity, cumulative
delta), location (VWAP distance, swing, room to the 60-min extreme), derivatives (Binance OI/funding PIT
proxy, latency 300 s), microstructure (forward-only; absent historically, never fabricated). No new
indicator, no EMA zoo, no ML. Weights 20/15/10/15/15/10/15, renormalised over available groups, as V11.

## 3. Entry quality (CHANGE #1)

A V11.1 entry requires ALL of:

1. valid family structure (section 4-6) and the family's armed state (score >= 70 at arming, as V11);
2. score >= 75 at the trigger bar (V11 entry threshold, unchanged);
3. **independent evidence >= 4 groups**: a group provides directional evidence for side s when its
   sub-score for s is >= 0.60 AND exceeds the sub-score for the opposite side by >= 0.20. Counted groups:
   trend, momentum, volume, location, derivatives, microstructure (six directional groups; historically
   five are available, so >= 4 of 5). Volatility is non-directional and never counts. A group is one
   unit of evidence whatever the number of indicators inside it (EMA9/21/50 alignment is one trend vote,
   not three).
4. **no trend/regime contradiction**: reject when `struct15 == -s`, or when both `s*(ema21-ema50) < 0`
   and `s*(close-vwap) < 0`;
5. acceptable cost/R (section 8);
6. an immediate 1m trigger (family-specific, section 4-6) on the decision bar; fill at the next 1m open
   (V11 next-bar semantics).

Every executed decision logs `evidence_groups` (the list of confirming groups), `evidence_n`, the group
contributions and the indicator snapshot.

## 4. Family A — trend pullback continuation (both sides; mirrored for shorts)

V11-A structure is kept (`a_struct_*`: impulse >= 1.0 ATR5 within 8 five-minute bars, 2-6 bar pullback
retracing 20-60%, pullback volume <= 0.90 x impulse volume per bar) and tightened with, all at the
decision bar i (completed 1m bar):

- clear 15m regime: `struct15 == s` AND `s*ema21_slope > 0`;
- VWAP position: `s*(close - vwap) > 0` AND `s*dist_vwap <= 2.0` ATR5 (not extremely extended);
- pullback does not invalidate structure: `s*(close - swing_lo5)>0` (long: close above the last 5m swing
  low; short: below the swing high);
- momentum not exhausted: `rsi7 < 85` for longs, `> 15` for shorts;
- 1m re-acceleration toward the trend: `s*accel1 > 0`;
- directional flow: `s*delta1 > 0` AND `s*imb5 > 0`; opposing flow dominant (`s*imb15 < -0.20`) rejects;
- reward room: distance to the 60-min extreme in the trade direction >= `tp1_r` x stop distance, or
  price already beyond that extreme;
- trigger: `s*(close - hi3/lo3) > 0` (1m close through the high/low of the last 3 completed 1m bars), as V11.

Stop: `min(pullback low, swing_lo5) - 0.20 ATR5` (mirrored).

## 5. Family B — breakout / expansion (both sides)

V11-B structure kept (recent squeeze release or ATR-low compression; price within 0.30 ATR5 of the
60-min boundary when armed) and tightened with:

- rising RVOL: `rvol5 >= 1.5` AND `rvol5 > rvol5 of the previous completed 5m bar`, OR `vol_spike == 1`;
- momentum expansion: `s*roc3 > 0` AND `s*accel1 > 0`;
- decisive 1m close through the level: `0.10 ATR5 <= s*(close - level) <= 0.50 ATR5`;
- directional flow: `s*delta1 > 0` AND `s*imb5 > 0.10`;
- no immediate absorption: the decision bar closes in the breakout half of its range
  (`(close - low)/(high - low) >= 0.5` for longs, mirrored), range > 0;
- **failed-breakout rejection**: while armed, if a completed 1m bar closes beyond the level and a later
  bar closes back inside the range before any entry, the armed state is marked FAILED and cannot
  trigger for the rest of its validity window (counter `FAILED_BREAKOUT`).

Stop: `level - 0.20 ATR5` (mirrored).

## 6. Family C — liquidity sweep reversal (both sides; historical features only)

V11-C structure kept (a sweep of the 60-min low by >= 0.10 ATR5 within the last 5 completed minutes;
`rsi7 <= 35` at arming, mirrored `>= 65`) and tightened with:

- extension beyond the level: `lo60 - sweep_low >= 0.10 ATR5` (the depth rule, explicit);
- failure to continue and reclaim: `close > lo60` AND `close > sweep_low + 0.5*(lo60 - sweep_low)`
  (price back through the level and above the mid-point of the sweep);
- exhaustion / turn evidence: `rsi7 <= 35` on the arming bar and `rsi7 > rsi7_prev` on the decision bar;
- directional reversal flow: `s*delta1 > 0` AND `s*imb5 > 0`.

Stop: `sweep extreme - 0.20 ATR5` (mirrored). Live microstructure is not part of this run and no
historical order-book / liquidation history is fabricated.

## 7. Stop geometry (CHANGE #2)

Structural invalidation (section 4-6) + 0.20 ATR5 buffer for every family. Pre-registered bounds on the
stop distance as % of the decision close: **minimum 0.55%** (a smaller structural stop is widened to
0.55%, noted `FLOORED`), normal 0.55-0.85%, **absolute maximum 1.00%**; a structural stop wider than
1.00% is rejected with `STOP_TOO_WIDE_V11_1`. Stops are never tightened to increase size.

## 8. Cost-aware entry filter

At the decision bar, with only decision-time information:
`expected_cost_bps = 2*taker_fee + 2*half_spread + entry_slippage + exit_slippage = 2*5.5 + 2*0.5 + 1 + 1 = 14 bps`
of notional (conservative taker fills both ways; the stop-slippage surcharge is not assumed because the
exit kind is unknown at decision time). `expected_cost_R = expected_cost_bps / stop_bps`. Reject when
`expected_cost_R > 0.25` with `COST_TOO_HIGH`. Consequence stated before the run: at the 0.55% floor,
14/55 = 0.2545R > 0.25R, so a floored stop is always rejected and the effective minimum accepted stop is
0.56%; the report counts those rejections.

## 9. Exits, risk, leverage (V11 rules, unchanged)

TP1 +0.60R close 40%; TP2 +1.20R close 40%; runner 20% trailed at the lowest/highest of the last 3
completed 5m bars - 0.10 ATR5 after TP2; break-even stop at entry +14 bps after TP1; stall review at 20
min (MFE < 0.30R and (score <= entry score - 15 or trigger extreme broken) -> close); hard maximum 60 min.
Funding at 8-h marks, taker costs as V11 (taker 5.5, half-spread 0.5, slippage 1 entry / 1 exit / 3 stop).
Risk 0.15% of 5,000 USDT = 7.50 USDT per trade from stop distance x quantity (`size_position`); leverage
ladder [1, 2, 3, 5], preferred 3x, maximum 5x, margin cap 25% of equity; leverage never changes risk.
One position, no averaging / pyramiding / martingale. x1.5 cost stress is reported.

## 10. Windows, protocol, success criteria

Windows identical to V11: TRAIN 2022-01-01 -> 2024-07-01, VALIDATION 2024-07-01 -> 2025-07-01, 30-day
warm-up, every load capped at 2025-07-01; the locked holdout 2025-07..2026-09 is not read. Dataset:
existing Bybit 1m trade dataset + Binance OI/funding PIT proxy; nothing downloaded or rebuilt.

ONE run (`btc-swing v111 research`), TRAIN and VALIDATION reported separately with the V11 benchmark
beside them. Classification (pre-registered):

- **C** when VALIDATION net R/trade <= 0 or PF <= 1.00. STOP: no shadow runner, no V11.2, no tuning.
- **A** when the PRIMARY criteria (validation net R > 0 AND PF > 1.05) hold on VALIDATION and TRAIN,
  all SECONDARY criteria hold on VALIDATION (gross R > 0.10, cost R < 0.25, >= 1.5 trades/day, median hold
  <= 45 min) and no single family contributes more than 90% of validation total net R.
- **B** otherwise (validation primary criteria hold, something else fails or is undisclosed).

Only A or B with positive validation net expectancy justifies building a V11.1 forward SHADOW (separate
namespace; never Demo). The report answers: (1) did wider stops lower cost/R, (2) did stricter entries
improve gross R, (3) did both improve net, (4) how much frequency was sacrificed, (5) which family
improved most, (6) is any improvement robust across TRAIN and VALIDATION.

## 11. Tests (minimum, `tests/v11_1`)

PIT / no lookahead of the derived evidence arrays; independent-evidence counting (group unit, four
required, volatility never counted); correlated indicators not double-counted; score threshold; 0.55%
floor; 1.00% cap rejection; cost_R calculation and COST_TOO_HIGH rejection; fixed-risk sizing and
leverage invariance; 60-min time stop; next-bar entry semantics; one-position rule; V10 isolation; V11
isolation (V11 files unchanged, no V11 state read, no forward paths).
