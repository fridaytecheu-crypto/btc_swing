# BTC V6.1 — STRUCTURE + FLOW INTRADAY: pre-registered design

Status: DESIGN FROZEN BEFORE ANY V6.1 RESULT (committed before the research harness runs). V5, V5.1
and the frozen V6 results are untouched. V6.1 is an isolated paper-only track (`btc_swing/v61/`,
`config/btc_swing_v6_1.yaml`, CLI `btc-swing v61 ingest|research|render`, report
`reports/BTC_SWING_V6_1_STRUCTURE_FLOW_RESEARCH.md`, tables `reports/v61/`); it has no exchange
client, no credentials and no order code path. Nothing is activated. The locked historical holdout
2025-07-01 -> 2026-09-30 is NOT downloaded, read or run in this phase.

## 0. What V6 taught and what V6.1 changes

V6 (classification C) showed that opportunity scarcity is not the primary problem: the frozen
specification produced 1.79 trades per calendar day with one position, 1.98 with sequential
episodes and 2.85 with independent episodes, all at the same gross expectancy of about +0.04R before
costs of about 0.35R per trade (round trip 15 bps against a 48 bps median stop; net -0.30R; PF 0.55).
V6 traded FLOW EVENT -> ENTRY. V6.1 trades MARKET STRUCTURE -> GOOD LOCATION -> FLOW CONFIRMATION
-> ENTRY: flow, open interest and volume confirm a structural opportunity; they never create the
trade by themselves. Three further changes are first-class: Bybit-native research data (forward
execution is on Bybit), a pre-trade cost-share eligibility rule, and explicit maker/taker entry
modelling. Nothing of V6 is re-tuned; V6.1 families are new definitions.

## 1. Objective and hard requirements

- HARD product requirement: mean executed trades per CALENDAR day >= 2.0 on the final historical
  evaluation (every UTC day counts, zero-trade days included). Preferred 2-4 high-quality trades per
  day; 3-6 only if the data supports it. Active-day statistics may be reported but never replace the
  calendar-day figure.
- Mandatory: positive NET expectancy after realistic costs; profit factor > 1.0 after costs
  (materially stronger preferred, e.g. PF > 1.1).
- Expected holding period 30 minutes to 6 hours, most trades materially shorter than V5.
- One BTC position at a time; no pyramiding, no averaging, no martingale; daily risk limits.
- Primary risk 0.15% of the 5,000 USDT reference equity per trade; dynamic leverage is margin
  management only (frozen `size_position` ladder).

## 2. Scientific question

Can GOOD STRUCTURAL LOCATION plus FLOW CONFIRMATION produce enough gross edge to pay BTC intraday
trading costs while maintaining >= 2 trades per day? Trade count is never optimised for its own sake.

## 3. Data

### 3.1 Bybit-native 5m dataset (primary)
Source: the Bybit public trade archive `public.bybit.com/trading/BTCUSDT/` (USDT linear perpetual;
daily gzip CSV of tick trades: timestamp, side = taker side, size, price, tickDirection,
trdMatchID, notionals; files exist from 2020-03-25). Ingest (`btc-swing v61 ingest`,
`btc_swing/v61/ingest.py`): each day is streamed, sha256-hashed, aggregated to 5-minute rows and
discarded (raw gz not retained; manifest `data/btc/raw/manifest_bybit_trades_5m.jsonl` keeps key,
sha256, bytes, trade and row counts). Rows contain only trades with timestamp inside the bucket (PIT
by construction, immutable). Per 5m row: open/high/low/close, volume (BTC), quote volume, trade
count, buy/sell counts, aggressor buy/sell quantity and notional, big prints (single trade >= 1 BTC;
>= 100k USDT notional), max and average trade size, VWAP, first/last trade time. Delta, CVD,
realised volatility and the rest are derived in the feature frame. Window ingested: 2021-11-15 ->
2025-06-30 (45-day warm-up before development; the holdout from 2025-07-01 is refused by the
ingestor without an owner flag). 1m rows are not built in this phase (section 4).

### 3.2 Auxiliary series (Binance USDT-M, documented venue mismatch)
Bybit open-interest, funding and premium history are served by Bybit REST, which is geo-blocked from
the research container and in any case covers only a limited history for OI. V6.1 therefore joins the
Binance Vision 5-min metrics (open interest), 8h funding and 5m premium-index klines already on disk,
by observation time (`time <= T`). Mismatch: Binance OI/funding/premium describe Binance positioning,
not Bybit; they enter only as flow/positioning CONFIRMATION features (section 7), never as structure.
Price, flow and volume are Bybit-native. The same specification is additionally run on the Binance 5m
klines + aggTrades flow (V5/V6 datasets) as a robustness comparison, never as the primary source.

### 3.3 Availability table (filled by the harness)
The report states, for each series: first/last timestamp, share of 5m rows covered inside the
development and validation windows, and the days missing from the Bybit archive (never backfilled:
missing rows are invalid observations under the V5.1 data-quality rules).

### 3.4 Data-quality rules (inherited from V5.1/V6)
A 5m row is a valid observation iff it exists with trades > 0, volume > 0 and a finite close; every
rolling feature is NaN unless its whole lookback is valid; same-time-of-day baselines use previous
days only and ignore invalid rows; ATR is Wilder on clean bars; higher-timeframe values come from
COMPLETED 15m/1h/4h bars only.

## 4. Timeframes

1h = regime; 15m = structure/context; 5m = setup and trigger. 1m is NOT investigated in this phase:
a 1m layer needs a second fill model (quotes, queue position) that the trade archive cannot
calibrate, and it only makes sense once a 5m specification has shown gross edge. If V6.1 passes the
validation gate, 1m entry timing becomes a separate pre-registered follow-up, never a scalping
strategy.

## 5. Regime (1h, PIT at the last completed 1h bar)

`regime_1h` in {TREND_UP, TREND_DOWN, RANGE}: TREND_UP iff EMA21(1h) > EMA50(1h), close(1h) >
EMA50(1h) and EMA21(1h) is above its value 6 bars earlier; TREND_DOWN mirrored; otherwise RANGE.
Realised-volatility tercile (30-day rolling, as V6) is used for reporting only. Family gates: A and E
require TREND in the trade direction; D requires RANGE; B and C are allowed in every regime (reported
by regime).

## 6. Location features (PIT, 5m close; ATR15 = Wilder ATR(14) of completed 15m bars)

| feature | definition |
|---|---|
| `dist_vwap_atr` | (close - session VWAP of the UTC day) / ATR15 |
| `dist_ema21_15m_atr`, `dist_ema21_1h_atr` | (close - EMA21 of completed 15m / 1h bars) / ATR15 |
| `dist_prev1h_hi_atr`, `dist_prev1h_lo_atr` | distance to the previous completed 1h bar's high / low in ATR15 |
| `hi_4h`, `lo_4h`, `range_4h_atr` | rolling 48-bar high/low EXCLUDING the current bar; width in ATR15 |
| `range_pos_4h` | (close - lo_4h) / (hi_4h - lo_4h) |
| `hi_24h`, `lo_24h`, `range_pos_24h` | rolling 288-bar high/low excluding the current bar; position |
| `day_hi`, `day_lo` | the UTC session's high/low up to and including the current bar |
| `swing_high_5m/15m`, `swing_low_5m/15m` | last confirmed k=2 pivots (confirmed 2 bars after the pivot) |
| `impulse_*` | impulse high/low/origin over the last 24 bars (section 8, families A/E) and the retracement share |
| `ext_atr` | ATR15-normalised extension of the close from a reference level (family-specific) |
| `compression_6` | (max high - min low of the last 6 bars) / ATR15 |
| `edge_touches_hi/lo` | number of the last 48 bars whose high/low lies within 0.25 ATR15 of hi_4h / lo_4h (range validation) |

No other indicator is added in this phase.

## 7. Flow confirmation features and the structure-only control

Flow features (Bybit-native unless noted): `imbalance_5m/15m/1h` = (buy - sell) / (buy + sell);
`delta_5m/15m`, `delta_accel` = delta_5m - delta_5m[-1]; `cvd_slope_15m/1h`; `flow_flip` = sign of
delta_5m differs from the sign of delta_15m[-1]; `rel_vol_5m/15m` = volume over its same-time-of-day
20-day median; `big_imb_15m`; `avg_trade_rel` = average trade size over its 20-day same-slot median;
`oi_chg_15m/1h` (Binance OI); `prem_z`.

Every family is run twice with identical structure, location, stops, exits and costs: STRUCTURE
ONLY (the flow predicate is always true) and STRUCTURE + FLOW (the family's declared predicate must
hold at the trigger bar). The flow predicate per family is fixed in section 8. Decision rule,
declared now: flow stays in the V6.1 specification for a family only if it improves the net
expectancy of that family on BOTH development and validation; otherwise the structure-only version
is the family's specification. If flow improves neither, it is removed.

## 8. Setup families (Stage A = structure + location; Stage B = trigger; anti-chase states as V6)

Common: episode opens on a first-in-cluster Stage A event (no same family/side event in the previous
12 bars), max age 24 bars, entry window 6 bars after the first actionable bar, cooldown 3 bars
after a close, 3 after an invalidation, 12 after two consecutive losses of a family/side, fills at
the next 5m open, one position, pre-declared priority A, C, B, E, D. Stop = the farther of the
family's structural stop and a 1.0 ATR15 floor, capped at 3.0 ATR15; stop-floor buckets are a
reporting grid (section 10). Longs are written; shorts are the exact mirror.

**A. TREND_PULLBACK_RECLAIM** (regime TREND in the trade direction)
- Impulse: `impulse_high` = highest high of the last 24 bars; `impulse_low` = lowest low of the 24
  bars before that high (36-bar lookback); impulse size >= 1.5 ATR15.
- Location: close has retraced 38-62% of the impulse AND lies within 0.5 ATR15 of at least one
  structural level: EMA21(15m), session VWAP, or the prior 1h high broken by the impulse.
  Pullback lost momentum: the last 3 bars' total range <= 2.0 ATR5 (contraction) or their mean
  rel_vol_5m <= 1.0. Not the impulse itself: the setup is only valid >= 3 bars after the impulse high.
- Trigger: 5m close above the previous bar's high AND above EMA21(15m) (reclaim). Flow predicate:
  imbalance_5m >= 0.10 and delta_15m > delta_15m[-1].
- Stop: pullback low (lowest low since the impulse high) - 0.1 ATR5. Invalidation: retracement >
  78.6% or close below the pullback low by 0.25 ATR15.

**B. BREAKOUT_RETEST_CONTINUATION** (any regime)
- Range: hi_4h/lo_4h with width 1.5-6 ATR15 (established intraday range, not a trend leg).
- Breakout: 5m close > hi_4h + 0.1 ATR15 (valid break); the breakout bar is the reference.
- Retest: within 12 bars after the break, a bar whose low lies in [level - 0.3 ATR15, level + 0.5
  ATR15] and whose close >= level - 0.1 ATR15 (holds). EXTENDED and never actionable if price runs
  > 1.5 ATR15 above the level before any retest (anti-chase).
- Trigger: 5m close above the retest bar's high, with close <= level + 1.0 ATR15. Flow predicate:
  rel_vol_5m >= 1.2 on the trigger bar and imbalance_5m >= 0.10 and cvd_slope_15m > 0.
- Stop: lowest low of the retest - 0.1 ATR5 (below the level). Invalidation: close < level - 0.5 ATR15.

**C. LIQUIDITY_SWEEP_REVERSAL** (any regime; location first — not the V6 failed-breakout rule)
- Reference low: the lower of (lowest low of bars k-48..k-7) and the previous UTC day's low.
- Sweep bar: low < reference - 0.05 ATR15 AND close > reference (failed continuation, close back
  through structure). Reference low must have been in place (no lower low) for >= 6 bars.
- Trigger: within 6 bars, a 5m close above the sweep bar's high with close > reference. Flow
  predicate: sweep bar imbalance_5m <= -0.10 (aggression into the low) AND trigger bar
  imbalance_5m >= 0.10 (exhaustion/flip); OI confirmation (oi_chg_15m <= 0 at trigger) reported
  only.
- Stop: sweep low - 0.1 ATR5. Invalidation: close below the sweep low. Structural target (exit
  variant S): reference + 2 x sweep depth, but at least 1.0 ATR15 beyond the trigger close (a shallow
  sweep would otherwise put the target below the entry).

**D. RANGE_EDGE_MEAN_REVERSION** (regime RANGE only)
- Structural range: hi_4h/lo_4h with width 1.5-5 ATR15, each edge tested >= 2 times
  (`edge_touches`), opposite edge >= 1.2 ATR15 away.
- Location: range_pos_4h <= 0.20 (lower edge). Failed extension: bar low <= lo_4h + 0.1 ATR15
  (touch or sweep) with close >= lo_4h + 0.1 ATR15 (back inside).
- Trigger: within 6 bars, 5m close above the previous bar's high while range_pos_4h <= 0.30.
  Flow predicate: imbalance_5m >= 0.10 and delta_accel > 0.
- Stop: lowest low of the touch - 0.1 ATR5. Structural target: range midpoint (used by exit
  variant S). Invalidation: close < lo_4h - 0.5 ATR15, or regime leaves RANGE before the trigger.

**E. IMPULSE_CONSOLIDATION_BREAK** (regime TREND in the trade direction)
- Impulse: net move over the last 12 bars >= 2.0 ATR15 with efficiency (net / sum of |5m moves|)
  >= 0.6; impulse origin = lowest low of those 12 bars.
- Consolidation: the next 3-12 bars have total range <= 0.8 ATR15, mean rel_vol_5m <= 1.0 and a
  low above origin + 50% of the impulse (holds the upper half).
- Trigger: 5m close above the consolidation high with extension (close - origin) <= 3.5 ATR15
  (strict). Bars that make a new impulse extreme belong to the impulse: the consolidation count
  restarts after them (the impulse is complete only when a bar fails to extend). Flow predicate:
  imbalance_5m >= 0.15 and rel_vol_5m >= 1.2 (renewed flow).
- Stop: consolidation low - 0.1 ATR5. Invalidation: close < consolidation low - 0.25 ATR15, or
  the consolidation exceeds 12 bars.

## 9. Cost-aware eligibility (first-class rule)

`cost_bps` = fees of the entry mode (section 11) + exit taker fee + entry slippage (taker modes) +
stop slippage + spread; `cost_share_stop` = cost_bps / stop_bps; `cost_share_target` = cost_bps /
first-target distance in bps. A trigger is eligible only if cost_share_stop <= θ. Pre-registered
primary θ = 0.20; stability grid θ in {0.15, 0.20, 0.25, 0.30, none}. θ is NOT chosen by maximising
P&L: the primary is fixed now and the grid is reported. The report gives the distribution of
cost_bps, stop_bps, cost/stop and cost/target for all triggers and for executed trades.

## 10. Stops and stop-distance buckets

Structural stops as in section 8 with the 1.0 ATR15 floor and 3.0 ATR15 cap. Reporting grid of
minimum stop distance (floor in % of price) {structural (primary), 0.5%, 0.75%, 1.0%, 1.25%}: for
each, the number of trades, net R, PF, median stop bps and cost as a share of stop. Natural stop
distances are reported in buckets < 0.5%, 0.5-0.75%, 0.75-1.0%, 1.0-1.25%, > 1.25%.

## 11. Entry modes (explicit cost model; maker fills are never free)

- TAKER (primary for classification): fill at the next 5m open plus 1 bp slippage plus half a tick;
  taker fee 5.5 bps on every fill.
- MAKER: a limit at the trigger bar's close; filled only if a later bar trades THROUGH the limit
  (low <= limit - 1 tick for a long) within a 2-bar timeout; if the stop is hit on the fill bar the
  trade is a loss (adverse selection is modelled by the engine's bar logic); unfilled = no trade;
  maker fee 2.0 bps on the entry, taker 5.5 bps on exits.
- MAKER_FIRST: MAKER with the 2-bar timeout, then a TAKER fill at the next open if the setup is
  still valid (close still above the stop and within 1.0 ATR15 of the trigger close).
  Report fill rate, missed trades, fee/slippage R and net R for the three modes on development and
  validation. Decision rule, declared now: MAKER_FIRST replaces TAKER in the specification only if it
  improves net expectancy on both windows without reducing trades per day by more than 10%.

## 12. Exits and turnover

Variants (one chosen on development by the declared rule, then frozen): P1 partial 0.75R (30%) /
1.5R (30%) / runner on the 15m swing trail; P2 partial 1R (40%) / 2R (30%) / trail; S full exit at the
structural target (family-specific: impulse high for A/E, level + range width for B, reference +
sweep depth x 2 (at least 1 ATR15 beyond the entry) for C, range midpoint for D; for E the
consolidation break targets impulse extreme + 0.5 x impulse size) with breakeven after +1R; T trail (15m swing - 0.25
ATR15) activated after +1R MFE, no targets. Time stop: if MFE < 0.5R after T hours, exit at the
close; T in {2, 4, 6} h plus the 8 h hard cap always. Selection rule on development, declared now:
among (variant, time stop) pairs with >= 2.0 trades per calendar day, the highest net R per day
that is positive in >= 4 of 6 development folds; if none qualifies, the highest net R per day of all
(flagged as not meeting the frequency on development). Time-stop effect on trade availability and
expectancy is reported separately.

## 13. Frequency, one-position rule, risk

Frequency report (calendar-day first): mean and median trades per day, % of days with 0 / 1 / 2 /
3-4 / 5-6 / > 6 trades, mean time between entries, median hold; by year, side, family, regime,
weekday/weekend, UTC session. One position: valid triggers refused while a position is open (with
counterfactual simulation), the sequential counterfactual (episodes wait for the slot) and the
independent signal count are reported; if hold time suppresses frequency, the report quantifies it.
Risk: 0.15% primary; sensitivity 0.10 / 0.125 / 0.15 / 0.20 / 0.25% (R results identical, account
P&L and drawdown scale); daily overlay 5 full-risk losses or -0.75% realised stops new entries for
the UTC day; 12-bar cooldown after two consecutive losses in a family/side.

## 14. Research procedure and anti-overfitting

Development 2022-01-01 -> 2024-06-30; validation 2024-07-01 -> 2025-06-30; the locked holdout is
not touched. Steps, in order: (1) Bybit-native frame; (2) per family on development, structure-only
and structure + flow, taker entries, primary θ; (3) exit/time-stop selection on development by the
declared rule; (4) entry-mode comparison; (5) stop-floor grid, θ grid, 3-point grids of the
family thresholds (reported, nothing selected); (6) frozen combined specification on validation:
families keep flow or not by the section 7 rule evaluated on development AND validation (declared
rule, not a search); (7) Binance robustness run of the identical specification; (8) walk-forward
folds (6 development, 2 validation halves), regime/hour-matched nulls, bootstrap intervals; (9)
pass gate. Nothing is revisited after validation; the once-only holdout needs the owner's explicit
decision and a separate run.

## 15. Pass gate before the holdout (declared now)

A candidate may be proposed for the locked holdout only if validation shows: mean trades per
calendar day >= 2.0 AND net expectancy > 0 after realistic costs AND PF > 1.0 (materially stronger
preferred, PF > 1.1) AND the bootstrap interval / fold behaviour does not indicate one isolated
period (net R positive in >= 1 of 2 validation halves and >= 4 of 6 development folds) AND max
drawdown < 10% of equity at the primary risk AND no single family accounts for essentially all
the net profit (> 80%) without independent evidence (that family alone passing its own criteria on
both windows). Marginal candidates are not proposed.

## 16. Outputs

The report gives: family results independently; structure-only vs structure + flow; maker/taker
comparison; stop-distance / cost analysis; hold-time distribution; frequency distribution; net
expectancy and PF; walk-forward stability; Bybit-native vs Binance comparison; the exact combined
candidate if one survives; PASS/FAIL for >= 2 trades/day; PASS/FAIL for net edge; and the
recommendation whether the locked holdout deserves to be spent. The harness STOPS before the holdout.

## 17. Outcome (2026-10-12; `reports/BTC_SWING_V6_1_STRUCTURE_FLOW_RESEARCH.md`, tables `reports/v61/`)

Executed as pre-registered (config hash `028cc86200064b3c…`, Bybit-native dataset 2021-11-15 -> 2025-06-30,
1,324 days, 1.52 billion trades, manifest `manifests/v61_bybit_dataset_manifest.json`). Development chose exit
variant S with a 2 h time stop and TAKER entries by the declared rules (no (variant, time stop) pair met the
frequency-and-folds requirement, so the fallback was flagged); flow confirmation survived both windows only for
TREND_PULLBACK_RECLAIM.

| Bybit validation 2024-07 -> 2025-06 (final specification) | value |
|---|---|
| trades / per calendar day / zero-trade days | 147 / 0.40 / 74.8% |
| gross R / fee R / slippage R / net R per trade | -0.122 / -0.116 / -0.030 / -0.268 (95% CI -0.41..-0.11) |
| profit factor / win rate / max DD at 0.15% | 0.51 / 37% / 6.0% |
| median stop / cost share of stop / median hold | 94 bps / 0.16 / 50 min |
| families (net R) | breakout-retest -0.10, trend-pullback -0.25, sweep -0.33, range-edge -0.39, impulse-consolidation 0 trades |
| Binance robustness (same spec) | 131 trades, -0.277R, PF 0.49 |
| null (regime/hour-matched) | -0.241R: no edge versus random entries with the same geometry |
| cost-share grid (development) | θ none 4.21/day at -0.56R; 0.30: 1.31/day -0.27R; 0.20: 0.56/day -0.22R; 0.15: 0.26/day -0.18R |
| minimum-stop 1% (development) | 2.64/day at -0.17R, max DD 63% |
| one-position rule | 96 refusals vs 1,903 cost-share refusals; independent episodes 5.64/day at -0.52R |

**>= 2 trades/day: FAIL. Net edge: FAIL. Recommendation: do NOT spend the locked holdout.** The scientific
question of section 2 is answered negatively on this data: good structural location plus flow confirmation does
not produce gross edge (gross expectancy is negative before costs), and the cost-share rule that keeps costs
survivable removes most of the frequency. No threshold was changed after a result was read; the locked holdout
2025-07-01 -> 2026-09-30 remains unspent and was never downloaded by this track.
