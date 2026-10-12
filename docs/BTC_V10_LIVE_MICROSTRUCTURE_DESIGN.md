# BTC V10 — LIVE MICROSTRUCTURE SNIPER: forward-only design (pre-registered before any observation)

Status: FORWARD-ONLY. V10 has NO historical backtest and claims NO historical profitability. Every V10
statistic begins at the prospective `observation_start` recorded in `manifests/v10_forward_freeze.json`;
anything computed before it is warm-up or harness verification and is stored outside the observation
journals. V5, V5.1, V6, V6.1, V7, V8 and V9 are untouched. Isolation: `btc_swing/v10/`,
`config/btc_swing_v10.yaml`, data `<BTC_DATA_DIR>/btc/forward_v10/`, reports `reports/forward_v10/`,
manifest `manifests/v10_forward_freeze.json`, prefixes `V10D-` (Demo orders), `V10S-` (signals),
`V10E-` (episodes). Paper/Demo only: Bybit DEMO endpoint only, demo keys only from the environment,
never production, never real money.

## 0. Objective

BTCUSDT linear perpetual. A simple live microstructure sniper on the data Bybit actually sends us
prospectively: public trades (taker side), the real liquidation feed, the 50-level order book, price.
Desired activity about 3-6 candidate opportunities per day; not a quota; nothing is manufactured. The
first days exist to (1) collect high-quality forward data, (2) observe the real candidate frequency,
(3) place small-risk Bybit DEMO trades when frozen rules fire (only after owner activation), (4) learn
which family carries signal prospectively. No claim is made from a few Demo trades; no rule is tuned
during the first observation period.

## 1. Phase 1 — live feed audit (done 2026-10-12 on the stored cloud recording, 2.9 GB, ~2 h of
coverage in 5 hourly files 2026-10-06..08, plus the Bybit v5 documentation)

| source | what the collector stores | audit result | usable |
|---|---|---|---|
| PUBLIC TRADES `publicTrade.BTCUSDT` | raw message per push; per trade `T` fill time ms, `S` taker side (doc: "Side of taker"), `v` size, `p` price, `i` trade id, `seq`, `L` tick, `BT` block | 105,908 trades, 0 duplicate ids, 1 non-monotone seq (reconnect), 10-25 trades/s in covered hours, batched messages sorted by time; receive-minus-exchange latency p50 42 ms, p99 ~300 ms | YES (primary) |
| LIQUIDATIONS `allLiquidation.BTCUSDT` | per event `T`, `s`, `S`, `v` size, `p` bankruptcy price; pushed at most every 500 ms (doc) | 45 events in ~2 h of active coverage (15-48/h); doc: `S`=`Buy` means a LONG position was liquidated; EMPIRICAL CHECK: all 13 `Buy` events were preceded by a negative 60-s price move (median -11 bps, 100% negative) and all 32 `Sell` events by a positive one (median +9 bps, 0% negative). Side interpretation is therefore verified: `Buy` -> long liquidated (forced sell), `Sell` -> short liquidated (forced buy). Notional p50 ~2.5k, p90 ~33k USD in quiet hours: fixed USD thresholds are NOT used; everything is normalised to recent live history | YES (primary for family A) |
| ORDER BOOK `orderbook.50.BTCUSDT` (+ `orderbook.1`) | snapshots and deltas with `u`, `seq`, `cts`; the collector checks `u` continuity per topic | 197,737 deltas, 13 snapshots, 2 sequence anomalies (one out-of-order pair at a reconnect, gap rate 1e-5), delta cadence p50 20 ms; reconstruction always 50 bid / 50 ask levels, never crossed; spread p50 0.012 bps (one tick); `orderbook.1` is a separate 10-ms best-level stream (100,056 snapshots) | YES, with gating: book features are used only while the local book is valid (snapshot seen, no gap since, last update < 2 s old); on a gap the book is invalid until the next snapshot (resubscribe forces one) |
| PRICE | trades give last price; `kline.5` gives confirmed 5m bars; `tickers` gives mark/index/bid1/ask1 | 1m and 5m bars are built in-process from trades (PIT: a bar is complete when a later trade or the wall clock passes its end); 15m swings from completed 5m bars; ATR(14) on completed 5m and 1m bars | YES |
| coverage | the cloud recording has 27-hour gaps between test runs (expected: cloud runs were tests) | the Mac is the authoritative host; V10 runs its own public WebSocket connection and its own raw store, so V5/V5.1 files are never written by V10 | — |

Missing or unreliable: nothing required is missing. Caveats recorded now: (a) liquidation events are
sparse in quiet hours (0-15/h) and bursty in moves (up to ~50/h), so family A will be rare by
construction; (b) the book is reliable only via the validity gate; (c) latency ~40 ms p50 from the cloud
container; the Mac's own figure is measured by the runner.

## 2. Phase 2 — features (concise; all computed from in-memory event buffers at a 1-second cadence)

Windows: 10 s, 30 s, 60 s for flow; 30 s and 120 s for liquidations. Every normalised quantity uses a
history of samples taken every 10 s over the previous 4 hours (previous samples only; a feature is NaN
until 30 minutes of history exist, i.e. 180 samples).

| group | feature | definition |
|---|---|---|
| A flow | `buy_w`, `sell_w`, `delta_w`, `imb_w`, `n_w`, `intensity_w` | taker buy / sell volume, delta, (buy-sell)/(buy+sell), trade count, trades per second over window w; `abs_delta_pct_w` = percentile rank of \|delta_w\| in the 4-h history; `intensity_pct_10` likewise |
| B liquidations | `liq_long_w`, `liq_short_w`, `liq_n_w`, `liq_imb_w`, `liq_burst_pct_w` | long / short liquidation notional (size x bankruptcy price), count, (short-long)/(short+long), percentile rank of the total notional in the 4-h history (bursts = rank >= 0.98) |
| C price response | `ret_w` (bps), `impact_w`, `absorption_w`, `efficient_w`, `turn` | `impact_30` = (ret_30 / ATR1m) / (delta_30 / median \|delta_30\| of the history); `absorption_30` = \|delta_30\| rank >= 0.75 AND \|impact_30\| rank <= 0.25 (large flow, little progress); `efficient_30` = \|delta_30\| rank >= 0.75 AND \|impact_30\| rank >= 0.75 AND sign(ret_30) = sign(delta_30); `turn` = last price has moved >= 0.10 ATR1m away from the extreme reached since ARMED |
| D order book (gated) | `bid1`, `ask1`, `spread_bps`, `bid_depth_10`, `ask_depth_10`, `depth_imb`, `bid_repl_30`, `ask_repl_30` | best levels; depth within 10 bps of mid; (bid-ask)/(bid+ask); replenishment ratio = depth now / depth 30 s ago on the same side (< 0.7 depleting, > 1.0 replenishing); all NaN while the book is invalid |
| E structure | `atr5`, `atr1`, `swing_hi_5m`, `swing_lo_5m`, `range_hi_15`, `range_lo_15`, `dist_lo`, `dist_hi` | ATR(14) on completed 5m / 1m bars; last confirmed 5m swing (k=2); highest high / lowest low of the previous 15 completed 1m bars; distance of last price from the 60-minute low / high in ATR5m |

No EMA zoo, no RSI/MACD/Bollinger, no OI/funding gates, no large-trade clusters, no ML.

## 3. Phase 3 — exactly three families (LONG described; SHORT is the exact mirror)

State machine per family and side: IDLE -> ARMED -> TRIGGERED -> EXECUTED, or ARMED -> INVALIDATED /
EXPIRED. ARMED = the interesting event begins; TRIGGERED = confirmation; EXECUTED = the order decision
is made immediately (no waiting for other candidates, no ranking, no daily top-N). Thresholds below are
pre-registered values chosen from the audit scale and the V7 definitions; they are NOT tuned on forward
results during the first observation period.

**V10-A LIQUIDATION ABSORPTION REVERSAL (LONG).** ARM when `liq_burst_pct_30` >= 0.98 with long
liquidations dominating (`liq_imb_30` <= -0.5, i.e. longs >= 75% of the burst) AND `imb_30` <= -0.30
(strong aggressive selling). While ARMED (max 180 s): INVALIDATE if price falls more than 0.5 ATR5m
below the price at ARM (proportional downside progress continued: no absorption); otherwise the
absorption condition must be observed on at least one tick (`absorption_30`). TRIGGER when absorption
was observed AND `imb_10` >= +0.10 (sell flow weakens, buy flow recovers) AND `turn` (price >= 0.10
ATR1m above the low since ARM). EXPIRE at 180 s.

**V10-B AGGRESSIVE FLOW ABSORPTION (LONG).** No liquidation burst required. ARM when
`abs_delta_pct_30` >= 0.95 AND `imb_30` <= -0.30 AND `dist_lo` <= 0.5 (price within 0.5 ATR5m of the
60-minute low or of the last 5m swing low). While ARMED (max 180 s): INVALIDATE on > 0.5 ATR5m further
downside; absorption must be observed; if the book is valid, `bid_repl_30` must be >= 0.8 at the trigger
tick (bid liquidity stable or replenishing; when the book is invalid this condition is skipped and the
signal is flagged `book_unavailable`). TRIGGER when absorption observed AND `imb_10` >= +0.10 AND
`turn`. EXPIRE at 180 s.

**V10-C LIQUIDITY BREAK / CONTINUATION (LONG).** ARM when `abs_delta_pct_30` >= 0.95 AND `imb_30` >=
+0.30 AND `efficient_30` AND `intensity_pct_10` >= 0.90 AND (book valid -> `ask_repl_30` <= 0.7, ask
liquidity consumed or pulled; book invalid -> skipped and flagged). TRIGGER when the last price breaks
the recent local structure: price > `range_hi_15` AND no chase: price - `range_hi_15` <= 0.5 ATR5m AND
price - price at ARM <= 1.0 ATR5m. INVALIDATE if `imb_30` turns negative. EXPIRE at 120 s.

Near-misses: on every tick each family/side computes its ARM and TRIGGER condition vectors; an event
that satisfies all but one ARM condition (or all but one TRIGGER condition while ARMED) is written to
`nearmiss.jsonl` with the failing condition and the feature snapshot, de-duplicated per family/side over
60 s. Near-miss logging never affects decisions.

## 4. Execution, stop, target, risk, safety

- Trigger -> decision immediately on the same tick: freeze the feature snapshot, run the safety checks
  (section 5), size, and (shadow) record the hypothetical fill or (Demo, only after owner activation)
  send a MARKET order. Timestamps recorded per signal: `event_ts` (exchange ts of the arming message),
  `armed_ts`, `trigger_ts`, `decision_ts`, and for Demo `order_sent_ts`, `ack_ts`, `fill_ts`; latency
  lines are reported (decision - trigger, ack - sent, fill - sent).
- ENTRY: MARKET / TAKER. Shadow fill model: last trade price + half-spread (book) + 1 bps slippage;
  taker fee 5.5 bps per side; stop fills at stop - 3 bps; targets at target - 1 bps; time exits at the
  last price - 1 bps (mirrored for SHORT). Maker-first is not used.
- STOP: microstructure invalidation + structure + ATR floor: level = min(low since ARM, last 5m swing
  low) - 0.10 ATR5m; distance floor 0.70% of price (widened to the floor when closer); cap 1.50% (a
  wider structural stop REJECTS the signal, reason STOP_TOO_WIDE). Not optimised from forward results.
- TARGET / EXIT (frozen): TP1 = +1R close 50%, TP2 = +2R close the remaining 50%; time stop 150 minutes
  if open P&L < +0.5R at that time (close at market), hard maximum 240 minutes; no trailing.
- RISK: reference equity 5,000 USDT; 0.10% per trade = 5 USDT planned stop risk; leverage from the
  frozen ladder [1, 2, 3, 5, 10] via `btc_swing/risk/sizing.py::size_position` (margin cap 25%,
  liquidation buffer 3 stops / 3 ATR1h); leverage never raises the stop risk; no pyramiding, averaging
  or martingale; max ONE V10 BTC position at a time.
- DAILY SAFETY: max 5 EXECUTED V10 trades per rolling 24 h; daily stop at -0.50% of reference equity
  (-25 USDT) realised on the UTC day or 3 consecutive full-risk losses, whichever first; afterwards new
  entries are BLOCKED until the next UTC day while signals, near-misses and outcomes keep being logged.

## 5. Safety checks at every decision (all must pass; fail closed; every failure is a BLOCKED record)

feature warm-up complete; trade feed age < 5 s; liquidation feed subscribed (age is informational);
book valid for families that use it (else flagged, not blocked); no open V10 position; daily caps not
hit; stop within [0.70%, 1.50%]; sizing accepted (qty >= min qty at 5 USDT risk); in Demo mode
additionally: executor built (activation effective for this host), exchange reachable, exchange position
flat, no reconciliation pending.

## 6. V5.1 / execution conflict (Demo activation gates; fail closed)

V10 never executes on the same BTCUSDT Demo position while V5.1 is active. `btc-swing v10 demo
activate` requires ALL of: V5.1 STRATEGY_DEMO not effective on this host (its journal's last event is
not ACTIVATED for this host) AND V5 STRATEGY_DEMO likewise; no V5.1 and no V5 strategy position record;
Bybit Demo BTCUSDT position flat; no open BTCUSDT orders; no V10 reconciliation pending; Demo credentials
present; authority lease held by this Mac; V10 runner alive and its collector fresh; V10 freeze present
with matching config hash; full test suite green; shadow verification report PASSED. V5.1 is never
deactivated automatically; the owner command is `uv run btc-swing v51 demo deactivate --note "<why>"`
(refused while a V5.1 position is open).

## 7. Provenance and records

Every V10 record carries `strategy = BTC_V10_LIVE_MICROSTRUCTURE_SNIPER`, `family`, `side`, timestamps,
`episode_id` (`V10E-...`), `signal_id` (`V10S-...`), the feature snapshot, `entry`, `stop`, `target`
levels, `qty`, `leverage`, `risk_usdt`, `execution_mode` (SHADOW | DEMO), and the `exit_reason` when
closed. Journals are hash-chained JSONL (`episodes.jsonl` for every ARMED / TRIGGERED / INVALIDATED /
EXPIRED / BLOCKED transition, `signals.jsonl` for decisions, `nearmiss.jsonl`, `shadow_trades.jsonl`,
`demo/strategy_journal.jsonl`, `demo/demo_trades.jsonl`); raw feed messages are stored immutably under
`forward_v10/raw/` (hourly JSONL, sha256 per payload). Forward outcomes (+5, +15, +30, +60, +120, +180
min: return, MFE, MAE, max up, max down from the own 1-minute bars) are appended to `outcomes.jsonl` by a
separate pass after enough time has elapsed; the original decision record is never rewritten.

## 8. Shadow first, freeze, activation

- SHADOW_ONLY: the runner evaluates and records everything, places no order. Minimum a few hours and
  enough events to verify: trade feed correct, liquidation interpretation correct (side check repeated
  live), book valid, no duplicate signals, no timestamp leakage (every feature uses events with exchange
  ts <= tick), no impossible order prices (entry between bid1 and ask1 +- 10 bps; stop on the correct
  side), risk sizing correct (5 USDT), plausible signal frequency. `btc-swing v10 forward shadow-report`
  writes the verification report.
- FREEZE: `btc-swing v10 forward freeze` writes `manifests/v10_forward_freeze.json` (config hash, code
  commit, feature / family / risk versions, frozen_at, observation_start = frozen_at) exactly once; the
  runner refuses to start without it or with a different config hash; no V10 signal may carry a
  timestamp before `observation_start` (pre-freeze shadow runs write to a separate verification root).
- DEMO ACTIVATION: after shadow verification the assistant STOPS and reports (shadow duration, ARMED
  events, triggers, family distribution, triggers/day, duplicates, feed gaps, book integrity,
  liquidation integrity, hypothetical trades and outcomes, risk check, V5.1 state, the exact activation
  command). V10 Demo is activated only by the owner on the authoritative Mac.

## 9. Commands

`uv run btc-swing v10 forward run [--shadow-verification] [--duration S]`, `v10 forward status --live`,
`v10 forward diagnostic`, `v10 forward freeze`, `v10 forward outcomes`, `v10 forward shadow-report`,
`v10 forward report`, `v10 demo status|activate|deactivate|reconcile-ack`. macOS LaunchAgent
`com.btcswing.v10-forward` (separate from the V5 agent; `deploy/macos/run_forward_v10.sh`).

## 10. Versions

`V10_VERSION = 10.0.0-live-sniper`, feature version `v10-feat-1`, family version `v10-fam-1`, risk
version `v10-risk-1`. Any change to a family rule after the freeze is a new version and a new freeze
after an owner decision; the first observation period is never tuned.
