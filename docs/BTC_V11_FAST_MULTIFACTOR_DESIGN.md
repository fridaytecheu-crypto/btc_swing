# BTC V11 — FAST MULTI-FACTOR INTRADAY: pre-registered design (frozen before any result)

Status: DESIGN FROZEN BEFORE ANY V11 RESULT. V11 is a NEW, fully isolated generation
(`btc_swing/v11/`, `config/btc_swing_v11.yaml`, `reports/v11/`, `reports/forward_v11/`,
`manifests/v11_forward_freeze.json`, data `<BTC_DATA_DIR>/btc/forward_v11/`). RESEARCH + SHADOW ONLY:
no exchange client, no credentials, no order path, no Demo authority. V10 keeps running on Bybit Demo
exactly as-is; no V10 file, config, journal, freeze, counter or position is touched by V11, and V11 never
reads V10 execution state. V5-V9 untouched. The locked holdout 2025-07-01 -> 2026-09-30 is never read
(every historical load is capped at 2025-07-01 even though the Binance derivatives series on disk
extend further).

## 0. Objective and the question

BTCUSDT linear perpetual, LONG and SHORT, one position at a time, typical hold 10-45 min, hard
maximum 60 min, roughly 3-8 executed opportunities per calendar day when conditions allow (an
objective to measure, never a reason to weaken rules; no trade is forced). Decisions are immediate: a
valid setup NOW triggers NOW; no ranking, no waiting for a better future candidate, no top-N.
Question: can combining independent technical + flow + microstructure information produce positive
NET expectancy after realistic costs at a materially higher frequency than V10 (~0.5-1 executed/day)?

## 1. Phase 1 audit (2026-10-13)

| item | finding | use |
|---|---|---|
| Bybit-native 1m bars with taker flow (`data/btc/datasets/bybit_trades_1m/`, V7 ingest) | 1,324 days 2021-11-15 -> 2025-06-30, OHLCV, trade count, taker buy/sell qty, no holdout day on disk | PRIMARY historical source: price, volume, flow (groups A-E, historical part of D) |
| Binance USDT-M 5m metrics (open interest) and funding (`data/btc/datasets/{metrics,funding}`) | PIT rows with `provider_available_time`; cover 2021-10 -> 2026-09 (loads capped at 2025-07-01) | group F historically (OI level / change, funding, price-vs-OI divergence); cross-venue proxy, stated as such |
| Bybit allLiquidation, order book | no historical archive exists; live only (V10 feed audit) | FORWARD-ONLY (group G and the liquidation part of F); never backfilled or fabricated |
| volume profile (HVN / LVN / POC) | no implementation in the repository | NOT used (nothing invented) |
| cost assumptions in the repo (V9 / V10, Bybit VIP0) | taker 5.5 bps per side, half-spread 0.5 bps per side, slippage 1 bps entry / target / time exits, 3 bps stop exits; funding at 8-h marks | PRIMARY cost model; sensitivity x1.5 pre-declared |
| prior fast attempts | V6 1.8/day -0.30R, V6.1 0.4/day -0.27R, V9 0.9/day -0.19R (floor-stop 4.7/day -0.28R), V10 shadow -3.5R over 5 trades (day 1) | benchmark context only |

Windows (already-consumed development history; nothing new is spent): TRAIN 2022-01-01 -> 2024-06-30
(912 days) and VALIDATION 2024-07-01 -> 2025-06-30 (365 days). ONE frozen specification is run once on
both; nothing is selected between them; forward shadow is the true out-of-sample test.

## 2. Timeframes and data flow

All bars are built from the 1m Bybit rows on a UTC-day-aligned grid: 5m and 15m bars are aligned
aggregates of completed 1m bars; the session is the UTC day. A decision at 1m close T uses only bars
whose close <= T, Binance rows whose `provider_available_time` <= T, and (forward only) live events with
exchange timestamp <= T. Live microstructure (10 s / 30 s / 60 s) is sampled at the 1m close that
triggers.

## 3. Indicator groups (exact formulas; `v11-feat-1`)

Notation: `c, h, l, v` 1m close/high/low/volume; `c5, h5, l5, v5` completed 5m bars; `ATR5` =
Wilder ATR(14) on 5m, `ATR1` on 1m; `buy, sell` taker base volume; sub-scores are in [0, 1] and are
sums of weighted votes (vote = 1 when the condition holds); every indicator belongs to one group only.

**A. TREND / REGIME (weight 20).** `vwap` = session cumulative sum(p x v) / sum(v) over 1m rows since
00:00 UTC (NaN in the first 15 min); `ema9, ema21, ema50` on completed 5m closes; `struct15` from
15m swing points (k = 2): +1 if the last two swing highs and lows are both rising (HH & HL), -1 if both
falling (LH & LL), else 0; `dist_vwap` = (c - vwap) / ATR5; `vwap_slope` = (vwap - vwap 15 min ago) /
ATR5; `ema21_slope` = (ema21 - ema21 three 5m bars ago) / ATR5.
A_long = 0.20[c > vwap] + 0.20[ema9 > ema21] + 0.20[ema21 > ema50] + 0.25[struct15 = +1] +
0.15[ema21_slope > 0]; if dist_vwap > 2.5 the score is halved (over-extended chase). A_short mirrored
(c < vwap, ema9 < ema21, ema21 < ema50, struct15 = -1, ema21_slope < 0; dist_vwap < -2.5 halves).

**B. MOMENTUM (weight 15).** `rsi7, rsi14` (Wilder) on 5m closes; `adx14` (Wilder) on 5m; `roc3` =
(c5 / c5[-3] - 1) in ATR5 units; `accel5` = ret5 - ret5[-1] (5m returns); `accel1` = ret1 - ret1[-1].
B_long = 0.20[rsi14 > 50] + 0.20[rsi7 > rsi7[-1]] + 0.20[adx14 > 20] + 0.20[roc3 > 0] +
0.20[accel5 > 0]; if rsi7 > 85 the score is halved (exhausted). B_short mirrored (rsi14 < 50, rsi7
falling, roc3 < 0, accel5 < 0; rsi7 < 15 halves). RSI is never a stand-alone buy/sell signal.

**C. VOLATILITY (weight 10, direction-neutral).** `bb_mid, bb_sd` = 20-bar mean / std of c5;
Bollinger = mid +- 2 sd; Keltner = mid +- 1.5 ATR5; `squeeze_on` = BB upper < KC upper AND BB lower >
KC lower; `squeeze_release` = squeeze_on was true within the last 6 completed 5m bars and is false now;
`bbw` = (upper - lower) / mid, `bbw_rising` = bbw > bbw[-1]; `atr_pct` = percentile rank of ATR5
against the ATR5 values of the previous 7 days (previous rows only).
C = 0.40[0.2 <= atr_pct <= 0.8] + 0.30[bbw_rising] + 0.30[|c - bb_mid| <= 2.2 bb_sd].

**D. VOLUME / TRADE FLOW (weight 15).** `rvol5` = v5 / median of the same 5m slot over the previous
20 days (>= 10 observations); `vol_spike_pct` = percentile rank of v5 against the previous 7 days of
5m volumes; `delta1, delta5, delta15` = buy - sell over 1 / 5 / 15 completed minutes; `imb5, imb15` =
delta / (buy + sell); `cum_delta` = session cumulative delta; `intensity1` = trades in the minute /
mean trades of the previous 20 minutes.
D_long = 0.25[imb5 > 0.10] + 0.20[delta15 > 0] + 0.25[rvol5 >= 1.2] + 0.15[intensity1 >= 1.2] +
0.15[cum_delta > cum_delta 15 min ago]. D_short mirrored.

**E. LOCATION / STRUCTURE (weight 15).** `swing_hi5, swing_lo5` (5m, k = 2), `swing_hi15, swing_lo15`
(15m, k = 2), `hi60, lo60` = highest high / lowest low of the previous 60 completed 1m bars, `rng_hi15,
rng_lo15` = extremes of the previous 15 completed 1m bars, impulse / pullback (section 4A), distances
in ATR5. E_long = 0.30[-0.5 <= dist_vwap <= 2.0] + 0.30[c > swing_lo5] + 0.40[(hi60 - c) >= 0.5 ATR5
OR c > hi60] (room to move or already through). E_short mirrored.

**F. DERIVATIVES (weight 10; Binance PIT proxy historically; liquidations forward-only).** `oi` = last
Binance open interest with provider_available_time <= T; `oi_chg_1h`, `oi_chg_15m`; `funding` = last
rate; `ret_1h` = 1h price return; `div_bear` = ret_1h > 0 AND oi_chg_1h < 0 (rally on falling OI);
`div_bull` = ret_1h < 0 AND oi_chg_1h < 0. F_long = 0.40[oi_chg_1h > 0 AND ret_1h > 0] +
0.30[funding <= +0.0003] + 0.30[NOT div_bear]. F_short = 0.40[oi_chg_1h > 0 AND ret_1h < 0] +
0.30[funding >= -0.0003] + 0.30[NOT div_bull]. Forward only, additive: a short-liquidation burst
(V10 definition, rank >= 0.98 within 120 s) adds nothing to F historically; in shadow it is recorded in
G (below) so the historical and forward F are identical.

**G. LIVE MICROSTRUCTURE (weight 15; FORWARD-ONLY).** From a V11-owned instance of the V10 feature
engine (same code, separate object, separate feed, no shared state): `imb_10, imb_30, imb_60`,
`delta_30`, `intensity_pct_10`, `depth_imb`, `bid_repl, ask_repl`, `spread_bps`, `absorption_30`,
`efficient_30`, `liq_burst_pct_120`, `liq_imb_120`. G_long = 0.25[imb_30 > 0.10] + 0.15[imb_10 > 0] +
0.20[depth_imb > 0 OR bid_repl >= 1.0] + 0.20[efficient_30 with delta_30 > 0] + 0.20[short-liquidation
burst: liq_burst_pct_120 >= 0.98 AND liq_imb_120 >= 0.5]. G_short mirrored. Historically G is
UNAVAILABLE and the score is renormalised over the available groups (A-F, total weight 85).

HISTORICALLY TESTABLE: A, B, C, D, E, F (OI / funding via the Binance proxy). FORWARD-ONLY: G and the
liquidation vote. No missing group is filled with neutral values.

## 4. Scores and the three families

Score_side = 100 x sum over available groups of (w_g x s_g) / sum of available w_g. Weights: A 20,
B 15, C 10, D 15, E 15, F 10, G 15. Pre-registered thresholds: CANDIDATE score >= 65 (logged),
ARMED score >= 70 plus the family structure, ENTRY score >= 75 plus the 1m trigger. Every candidate /
armed / entry record stores all seven group contributions and the indicator values.

**V11-A TREND PULLBACK CONTINUATION (LONG; SHORT mirrored).** Structure: struct15 = +1; impulse = a
leg of at most 8 completed 5m bars from its lowest low (origin) to its highest high (peak) with size >=
1.0 ATR5; pullback = 2-6 completed 5m bars after the peak, no bar above the peak, retrace 20-60% of the
leg, pullback low above the origin; pullback volume contraction: mean v5 over the pullback <= 0.9 x
mean v5 over the leg; momentum turning: accel5 > 0 or rsi7 rising. ARMED when structure holds and
score >= 70 (valid for 30 min, re-evaluated every 5m close). TRIGGER (1m): close > max(h of the
previous 3 completed 1m bars). ENTRY: trigger AND score >= 75.

**V11-B BREAKOUT / VOLATILITY EXPANSION (LONG; SHORT mirrored).** Structure: squeeze_release OR
atr_pct <= 0.30 within the last 6 completed 5m bars, AND c within 0.3 ATR5 below hi60 (near the
boundary). ARMED when structure holds and score >= 70 (valid 30 min). TRIGGER (1m): close > hi60 AND
rvol5 >= 1.2 (actual participation) AND close - hi60 <= 0.5 ATR5 (not over-extended). ENTRY: trigger AND
score >= 75; forward only, G must not show absorption (absorption_30 false) at the trigger.

**V11-C LIQUIDITY SWEEP / REVERSAL (LONG; SHORT mirrored).** Structure: a 1m bar within the last 5
completed minutes made low < lo60 - 0.1 ATR5 (sweep) while rsi7 (5m) <= 35 (exhaustion), the move
failed to continue (no 1m close below the sweep low afterwards). ARMED at the sweep bar when score >=
70 (valid 10 min). TRIGGER (1m): close > lo60 (reclaim of structure). ENTRY: trigger AND score >= 75;
forward only, absorption_30 true or a long-liquidation burst strengthens G (no extra gate).

A candidate that satisfies several families is taken by the first family in the order A, B, C (one
decision per 1m close). While a V11 position is open, signals are counted as "skipped while open".

## 5. Stops, exits, time management (`v11-exit-1`)

- STOP = structural invalidation - buffer: A: min(pullback low, swing_lo5) - 0.20 ATR5; B: hi60 (the
  breakout level) - 0.25 ATR5; C: sweep low - 0.15 ATR5 (mirrored for shorts). Distance floor 0.35% of
  entry (a closer structural stop is widened to the floor); normal range 0.45-0.70% (reported); hard cap
  1.00%: a wider valid structure REJECTS the signal (`STOP_TOO_WIDE_FOR_V11`). A valid stop is never
  tightened to obtain leverage.
- TP1 = +0.60R close 40%; TP2 = +1.20R close 40%; runner 20%: after TP2 the stop trails at the lowest
  low of the last 3 completed 5m bars - 0.10 ATR5 (highest high for shorts), updated at each 5m close,
  never loosened. After TP1 the stop of the remaining 60% moves to NET break-even: entry x (1 + s x
  0.0014) (two taker fees 11 bps + spread 1 bps + slippage 2 bps), so a break-even exit is net >= 0.
- EARLY-STALL REVIEW at 20 min after entry: exit at market if MFE < 0.30R AND (score_now <=
  score_entry - 15 OR the trigger is invalidated: a 1m close below the trigger bar's low for longs).
- HARD TIME STOP 60 min: exit at market. Stops and targets resolve on 1m highs/lows, stop first.
  Historical entry = next 1m open after the decision close; shadow entry = last trade + half-spread +
  1 bps at the decision.

## 6. Risk, leverage, safety

Reference equity 5,000 USDT; risk 0.15% = 7.50 USDT at the stop: qty = 7.50 / stop distance.
Leverage is for capital efficiency only: the frozen ladder `size_position` with allowed [1, 2, 3, 5],
maximum 5, margin cap 25%, liquidation buffer 3 stops / 3 ATR1h; the smallest sufficient leverage is
used (3x typical), never more quantity than the fixed-risk calculation. No martingale, averaging or
pyramiding; one V11 position. Shadow statistics model notional, leverage and margin per trade. Later
Demo safety (NOT activated, no authority created): max 10 Demo executions per rolling 24 h, daily
realised stop -40 USDT, 4 consecutive full-risk losses, one open position.

## 7. Cost model

Primary (audited repo assumption): taker 5.5 bps per side, half-spread 0.5 bps per side, slippage
1 bps on entry / target / time / stall exits, 3 bps on stop exits, Binance funding at 8-h marks while
open. Reported per trade: USDT, R, and cost as % of gross edge. Pre-declared sensitivity: x1.5 on
fees + spread + slippage. The main result is always NET.

## 8. Historical protocol and report

One run of the frozen specification on TRAIN and VALIDATION, each reported separately (no selection).
Report: total trades, trades per calendar day and per active day, median trades/day, zero-trade days,
LONG/SHORT, A/B/C, win rate, gross R, net R, average winner / loser, payoff ratio, PF, total net R,
max drawdown, median / p75 / p90 hold, exits by type (stop, TP1-then-stop, TP2, runner trail, time,
early-stall), costs per trade, skipped-while-open, breakdowns by family, side, trend regime (struct15),
volatility regime (atr_pct terciles), hour of day; the six questions of the brief; classification A /
B / C. No threshold, length, weight, stop or target is changed after the result.

## 9. Forward shadow

Independent runner (`btc-swing v11 forward run`, LaunchAgent `com.btcswing.v11-forward`): own public
WebSocket connection (V10 feed class reused as a library with a V11 raw root), own 1m bars, own frame
recomputed at each completed 1m bar on a rolling 3-day window with the SAME feature code as the
historical run, own V10-type microstructure engine instance for G, own journals (candidates, episodes,
signals with every group contribution and indicator value, shadow trades, near-misses, outcomes at
+5/15/30/45/60 min appended separately), own state, pid, lock, diagnostics and freeze
(`manifests/v11_forward_freeze.json`, strategy sections pinned). V11 never reads V10 positions,
orders, journals, counters or authority, never places an order (no exchange client is imported) and
runs in parallel with V10 Demo. Shadow verification before the freeze; owner review afterwards.

## 10. Tests (minimum)

Indicator PIT / no look-ahead, score calculation and renormalisation, LONG/SHORT symmetry, structural
stop + floor + 1.00% rejection, fixed-risk sizing with 3x/5x not altering risk, TP1/TP2 quantities,
60-min hard stop, early-stall rule, costs, one-position rule, independent V11 state, V10/V11
isolation (no V10 path written, no V10 import of execution modules), no exchange order path, freeze
hash enforcement, restart recovery, journal integrity.

## 11. Self-review of contradictions (before coding)

- "Do not require all indicators to agree" vs ARMED/ENTRY thresholds: thresholds act on the weighted
  score, so a family can trigger with a weak group; only the family STRUCTURE and the 1m trigger are
  hard. Consistent.
- Historical G unavailable vs score thresholds: renormalisation keeps 65/70/75 meaningful on A-F; a
  forward entry additionally needs G to carry its share. The forward population is therefore a
  subset-like population of the historical one, stated as a limitation, not hidden.
- Family B needs rvol5 >= 1.2 as structure AND D scores rvol5: one indicator in one group (D); the
  structural use is a participation gate of the family, not a second score. Accepted.
- Stop floor 0.35% with ATR5 ~0.13%: most A/C structural stops (0.1-0.3%) will be floored; the floor is
  part of the pre-registration (V9 showed rejecting them kills frequency, flooring keeps the risk
  honest). Cap 1.00% rejects wide B/C structures.
- 60-min hard stop vs TP2 +1.2R at 0.5% stops: +0.6% in 60 min is reachable in trending minutes; the
  distribution of exits is a reported result, not an assumption.
