# BTC V6 — ACTIVE INTRADAY: Phase 1 audit and design (STOP point)

Status: DESIGN ONLY. No V6 code, config, freeze, journal or runner change exists yet. V5 and V5.1
are untouched (V5.1 remains the authoritative Bybit DEMO strategy). This document records the data
audit, why V5/V5.1 is quiet, the proposed V6 design, the research protocol and the two decisions
the owner must take before Phase 2 (research harness) starts.

## 0. Two findings that stop Phase 1 here

1. **No historical liquidation data exists anywhere we can reach.** Binance Vision has no
   `liquidationSnapshot` dataset (its USDT-M futures bucket lists only aggTrades, bookDepth,
   bookTicker, klines, mark/index/premium klines, metrics, trades; verified 2026-10-10), the Bybit
   public archive has trades only, and our own Bybit `allLiquidation` collection started on
   2026-10-06. Families D (LIQUIDATION_FLOW_REVERSAL) and E (LIQUIDATION_CONTINUATION) therefore
   CANNOT be researched historically: no train/validation/OOS split is possible for them. They can
   only be studied prospectively from the live stream (the live stream is rich: 36 liquidation
   events in the 17 live 5m bars of the cloud copy, about 2 per bar), which needs weeks to months of
   collection before any statistic is meaningful. V5's "liquidation" family was an OI-flush proxy
   for exactly this reason.
2. **No truly untouched historical window exists.** V1-V5 inspected 2022-01..2026-09 (V5 ran its
   Stage A/B on the whole range after the 2025-01..2026-09 holdout was spent by V1). V6 has a
   different signal engine, so this data is "aggregate-inspected" rather than fitted, but CLAUDE.md
   requires a NEW owner pre-registration and a new window for any new hypothesis. The only data
   nobody has ever evaluated is the live Bybit stream from 2026-10-06 onward.

Proposed resolutions (owner decision required, section 9): research A/B/C/F on history with a
chronological development / validation / once-only OOS split, treat the once-only OOS window as
"never evaluated by V6" (not "never seen by anyone") and let the forward shadow period be the
genuinely untouched test; study D/E prospectively only, as shadow-mode diagnostics that gate a later
pre-registration.

## 1. Data available (audited 2026-10-10)

| dataset (Binance USDT-M perp, `data/btc/datasets/`) | coverage | cadence | quality / notes |
|---|---|---|---|
| perp klines 5m / 1h / 4h / 1d | 2021-10-01 -> 2026-09-30 | native | 525,888 5m rows, complete |
| aggTrades flow 5m (`aggtrades_flow`) | 2021-12-01 -> 2026-09-30 | 5m sums | taker buy/sell qty+notional, n trades, big trades (>=1 BTC, >=100k USDT), max trade, vwap; 100% bar coverage (V5 audit) |
| metrics (open interest, L/S ratios, taker L/S ratio) | 2021-10-01 -> 2026-09-30 | 5 min | 288 rows/day; OI in BTC; observation time as published |
| funding | 2021-10-01 -> 2026-09-30 | 8 h | settled rates |
| premium index klines 5m, mark price klines 5m, index klines 5m | 2021-10-01 -> 2026-09-30 | 5m | basis / premium features |
| book depth (`book_depth`) | 2023-01-01 -> 2026-09-30 | ~30 s snapshots | depth within +-1..5% of mid (11 levels), 40M rows; no best bid/ask, no queue dynamics |
| book ticker (Binance Vision, NOT ingested) | daily files from 2023-05-16 | tick-level best bid/ask | available for spread calibration (download needed) |
| liquidations | NONE historically | — | see finding 1 |
| Bybit public trading archive (`public.bybit.com/trading/BTCUSDT/`) | daily trade CSVs back to at least 2022-01-01 (HTTP 200) | tick | Bybit-native flow history is downloadable (tens of MB/day; ~60-100 GB for the full period, 24 GB free disk here -> stream-and-aggregate only); no OI/funding/liquidation/book history |

Live Bybit stream (`forward/bybit/BTCUSDT`, since 2026-10-06; owned by the V5 runner): publicTrade,
orderbook.1 and .50, tickers (mark, index, funding, OI), allLiquidation, kline.5. Derived 5m rows
carry trades/flow, big trades, liquidation counts/qty/notional per side and max single, mark/index
OHLC, OI, funding, book best levels, spread (bps), depth within +-1% and total, latency.

Venue mismatch (inherited from V5, still true): research = Binance history, forward = Bybit live.
Flow, book and liquidation microstructure differ between venues; price/OI/funding behave similarly.
Mitigation options: (a) accept and disclose (V5 did); (b) build a Bybit-native 5m flow dataset by
streaming the Bybit trade archive day by day (hours of download/aggregation; owner call).

## 2. Why V5/V5.1 produces few trades (from the V5 report, not re-derived)

| stage | per day |
|---|---|
| raw events (every 5m bar, both sides, 4 families) | 5.18 |
| first-in-cluster (no same family/side event in the previous 12 bars) | 1.37 |
| episodes opened (one slot, cooldowns, 24 h anchor de-dup) | 1.14 |
| qualified (15m confirmation inside 2 h) | 1.07 |
| trades | 0.70 (44% of days 0 trades, max 4/day) |

Causes, in order of weight:
1. **Holding time, not thresholds, is the main throttle.** Median hold 13.2 h, 53% of trades held
   more than 12 h, 30% ran to the 24 h cap; median time between entries 27.7 h. With one position
   the slot is occupied most of the day. (Note: the 24 h time-limit exits were the profitable ones,
   +0.85R mean, 74% win; the trail gave back most of the MFE: mfe capture about 0.)
2. **30-day z-score extremes with 3-5 conjunctive conditions.** Thresholds at |z| >= 1..2 on 8640-row
   baselines fire on daily-scale anomalies, not intraday structure; 5 raw events/day collapse to 1.4
   after the 12-bar clustering because events arrive in bursts.
3. **Confirmation geometry built for swings.** 15m confirmation within 2 h, then a 5m close inside a
   zone of -0.5/+1.0 ATR(1h), structural stop with a 1.25 ATR(1h) floor (median stop 1.4%): a swing
   geometry with swing-length holds.
4. **Cost burden.** 0.13R/trade (fees 0.085R + slippage 0.048R) on a 0.115R gross edge: at 1.4%
   stops the round trip (17 bps) is 12% of a stop; intraday stops will be tighter, so V6 costs per R
   will be HIGHER unless entries are maker-like or stops are structurally wider than V3's 0.66%.

Implication for V6: activity has to come from shorter episodes (minutes to hours), several episodes
per day across families and sides, 5m/15m structure instead of 30-day z extremes, and exits that
close intraday; the cost problem (V3: 0.34R/trade at 0.66% stops) is the central risk, so the
research must be driven by NET R and by stop distance versus cost from day one.

## 3. Candidate feature set (PIT at 5m close; 15m/1h/4h from completed bars only)

- Price/structure: returns 5m/15m/1h/4h; ATR(5m, 15m, 1h); realised vol 1h/4h and its 30-day rank;
  rolling 5m highs/lows over 1h/4h/24h (breakout references); swing highs/lows (k=2) on 5m/15m/1h;
  session VWAP (UTC day) and 15m/1h EMA distances in ATR units; range compression (ATR(5m) over
  1h vs 24h); 1h trend state (EMA21/50) and 4h trend state as regime filters only.
- Volume: 5m volume, relative volume vs the same-time-of-day median of the last 20 days (intraday
  seasonality matters at 5m; V5's log-volume z ignored it), 15m/1h volume, volume acceleration.
- Order flow: taker buy/sell qty, imbalance 5m/15m/1h, CVD and CVD slope 15m/1h, flow
  acceleration, big-trade imbalance (>=1 BTC), notional-weighted imbalance.
- Positioning: OI change 5m/15m/1h and acceleration (5-min metrics), funding, premium/basis
  (context only; 8 h / slow).
- Liquidations (live only): long/short count, qty, notional per 5m; burst = notional vs 1h/24h
  rolling median; imbalance; notional relative to bar volume; post-burst price response. Research
  status: forward-only (finding 1).
- Order book (live: spread, depth within +-1%, imbalance; history: +-1..5% depth notional at 30 s):
  diagnostic first; enters a rule only if it survives validation (section 7).

Every feature gets the V5.1 data-quality treatment from the start: gap rows are never
observations, lookback windows must be clean, baselines exclude invalid observations, ATR from clean
bars, same-time-of-day baselines computed on valid days only.

## 4. Proposed setup families

Each family = Stage A event (interesting) + Stage B trigger (executable) + anti-chase state machine
(ACTIONABLE / WAIT / EXTENDED / INVALIDATED / EXPIRED) + family-specific invalidation.

| family | Stage A (event) | Stage B (entry) | researchable on history |
|---|---|---|---|
| A MOMENTUM_CONTINUATION | 15m close beyond the 4h (48-bar) 5m high/low with relative volume >= x, taker imbalance and CVD slope in the break direction, OI rising, 1h trend not opposed; extension since event < e ATR(5m) | first 5m close back inside [break level, break level + z ATR] after at most 6 bars, or a 5m higher-low/lower-high; stop below the 15m structure (floor 1.0 ATR(15m)) | yes |
| B PULLBACK_CONTINUATION | impulse: 1h return >= r ATR(1h) with flow and OI confirming; price retraces 30-60% of the impulse toward VWAP/EMA/break level on contracting 5m volume | taker imbalance turns back with the trend and a 5m close reclaims the 15m EMA / prior swing; stop beyond the pullback extreme | yes |
| C FAILED_BREAKOUT_REVERSAL | new 24h high (low) with heavy aggressive buying (selling) but the next 1-3 5m closes back inside the range, CVD slope and OI not confirming | 5m close below (above) the breakout bar's low (high) within 6 bars; stop beyond the sweep extreme | yes |
| D LIQUIDATION_FLOW_REVERSAL | long-liquidation notional burst (>= k x 24h median) + heavy sell aggression + price progress stalls (lower low fails by < 0.25 ATR) | flow reversal (imbalance flips, CVD slope turns) with a 5m reclaim; stop beyond the flush low | NO (forward only) |
| E LIQUIDATION_CONTINUATION | burst + displacement >= d ATR + volume expansion + OI falling + continued aggression | first shallow pullback (<= 0.5 ATR) holds, 5m continuation close | NO (forward only) |
| F FLOW_OI_IMPULSE | 15m coherent expansion: return, imbalance, CVD slope and OI change all beyond their same-time-of-day 20-day baselines, premium not crowded | 5m close in the direction after a <= 2-bar pause; stop at the impulse origin (floor 1 ATR(15m)) | yes |

Common rules (to be fixed before any result is seen): one V6 position (long/short), no pyramiding,
no averaging, episode max age 2 h, entry window 6 bars after confirmation, cooldowns after close
(6 bars), after invalidation (3), after two consecutive losses in a family/side (12), daily budget
(section 5); priority between simultaneous episodes by a pre-declared family order, never by
in-sample performance.

## 5. Risk, daily budget, exits, costs (research grid, pre-declared)

- Risk per trade grid: 0.10%, 0.125%, 0.15%, 0.20%, 0.25% (reporting; classification at the
  pre-declared primary 0.15%). Leverage dynamic: the frozen `size_position` ladder [1,2,3,5,10],
  margin cap 25%, liquidation >= 3 stops and >= 3 ATR(4h); leverage sets margin, never the loss at
  the stop. Reference equity 5000 USDT virtual for the shadow.
- Daily overlay grid: 4 / 5 / 6 full-risk losses per day; -0.5% / -0.75% realised daily stop; a
  12-bar cooldown after two consecutive full-risk losses, family- and side-specific. Chosen by
  tail-risk and losing-streak behaviour on the development set only, then frozen.
- Exit variants compared on development only: A TP1 +1R 40% / TP2 +2R 30% / runner; B +0.75R 30% /
  +1.5R 30% / runner; C single +1.5R target + 15m-structure trail; D pure structure (15m swing
  trail from entry). Each with and without breakeven after TP1. Time cap 8 h (intraday).
- Costs (primary, Bybit-like, taker on every fill): fee 5.5 bps per side, slippage 1 bp entry /
  3 bps stop (tighter than V5 because sizes are ~0.03-0.1 BTC), spread 1 tick, funding at 8 h
  settlements. Maker variant reported separately and only for limit-style entries that are
  executable (pullback limits), never for stops. Report gross R, fee R, slippage R, funding R,
  net R per trade and the stop distance in bps; a family whose round-trip cost exceeds 25% of its
  median stop is flagged before any result is read.

## 6. Research periods and protocol (pre-registered on owner approval)

- Development: 2022-01-01 -> 2024-06-30 (2.5 y). Validation: 2024-07-01 -> 2025-06-30 (1 y).
  Once-only OOS: 2025-07-01 -> 2026-09-30 (15 months) — run exactly once with the frozen
  specification; no change afterwards. Forward shadow from the V6 freeze = the genuinely untouched
  test. Walk-forward inside development (6 quarterly folds) for parameter stability; results also
  by year, by regime (1h/4h trend x realised-vol tercile), weekday vs weekend, UTC session.
- Anti-overfitting: every threshold declared in the design with a 3-point grid at most, chosen on
  development only and required to be monotone/stable across neighbours; no per-family re-tuning on
  validation; families killed on development or validation stay dead; no exit, risk or overlay
  choice may be revisited after the OOS run; nulls (time- and regime-matched random entries with the
  same stop/exits/costs) per family; bootstrap intervals on net R; first-in-cluster statistics.
- PIT: 5m decisions use bars with close <= T, 15m/1h/4h completed bars only, metrics/funding by
  observation time, same-time-of-day baselines from previous days only; replay tests for leakage
  (truncate-and-recompute) as in V5.1.

## 7. Acceptance criteria (declared before results)

Per family (validation, net of primary costs): net expectancy > 0 with bootstrap 95% lower bound
> -0.02R, PF > 1.10, trades/day >= 0.5, cost share of gross < 60%, positive in >= 2 of 3 development
folds. Combined V6 (once-only OOS): net R > 0, PF > 1.15, max DD < 10% of equity at the primary
risk, no single year or regime > 60% of profit, LONG and SHORT each non-negative or explicitly
one-sided by design, median hold 15 min - 6 h, trades/day on active days in 2-6, median >= 1.5.
Classification: A robust and suitable (all met), B promising (met on validation, OOS marginal),
C reject. Order-book features are admitted only if they improve validation net R and OOS net R
for the same family with the same thresholds.

## 8. Isolation and provenance (to implement in Phase 2/7)

Package `btc_swing/v6/`, config `config/btc_swing_v6.yaml` (`strategy_name: btc_swing_v6_active_intraday`,
own hash), data `<data>/btc/forward_v6/` (own features state, episodes, signals, paper ledger,
reports `reports/forward_v6/`, hash-chained journals, freeze `manifests/v6_forward_freeze.json`,
activation journal never used for orders), prefixes `V6D-` (reserved; no demo executor in this
track), signal/trade records with strategy_version, family, side, signal_id, episode_id, timestamps,
feature snapshot, entry, stop, targets, risk, leverage, exit reason. The runner evaluates V5 (owns the
raw processing), then V5.1 (active DEMO), then V6 (PAPER ONLY: no client, no credentials, no
create_order/set_leverage/trading-stop code path reachable). A combined status command shows V5.1
(active position, latest signal, real DEMO PnL) and V6 (latest shadow signal, open paper position,
closed paper trades, paper PnL, trades today). V6 ignores V5.1's position (independent measurement)
but enforces its own one-position rule.

## 9. Owner decisions needed before Phase 2

1. Families D and E: accept "forward-only" status (implemented as shadow diagnostics that log
   liquidation bursts and the subsequent price/flow response, no paper trades until a later
   pre-registration on collected live data), or drop them from V6?
2. Pre-register the research windows of section 6 (development / validation / once-only OOS), noting
   that the OOS window has been aggregate-inspected by V1-V5 and only the forward shadow is untouched.
3. Venue: research on Binance history (default, as V5) or additionally build a Bybit-native 5m flow
   history from the Bybit trade archive (several hours of streaming aggregation; no OI/funding/
   liquidation history exists on Bybit either)?
4. Primary risk per trade for classification: 0.15% proposed (grid reported).

Nothing in this phase touched V5, V5.1, the freezes, journals, the Mac runner or Bybit.
