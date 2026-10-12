# BTC V7 — MICROSTRUCTURE SPECIALIST: pre-registered design (Phase 1: edge discovery)

Status: DESIGN FROZEN BEFORE ANY V7 RESULT. V5, V5.1, V6 and V6.1 are untouched; the V7 track is
isolated in `btc_swing/v7/` (config `config/btc_swing_v7.yaml`, CLI `btc-swing v7 ingest|discover|render`,
report `reports/BTC_SWING_V7_EDGE_DISCOVERY.md`, tables `reports/v7/`). Paper research only: no exchange
client, no credentials, no order path, nothing activated. The locked historical holdout 2025-07-01 ->
2026-09-30 is neither downloaded nor evaluated by this track.

## 0. Objective and the order of work

BTCUSDT only. Product objective: 3-6 executed trades per calendar day on average (not a quota; a day may
have 0-2), typical holds 10 minutes to 3 hours. The MOST IMPORTANT RULE: no fixed indicator strategy is
invented first. Phase 1 is MICROSTRUCTURE EDGE DISCOVERY on the Bybit-native trade archive (1.52 billion
trades already streamed for V6.1; re-streamed here into 1-minute microstructure bars). Only effects
that are repeatable in the discovery window, survive matched controls and are economically sensible are
translated into the three deterministic candidate engines (Phase 2, a separate pre-registration of
thresholds taken from the broad stable regions found in Phase 1, frozen before validation).

## 1. Windows (pre-registered)

| window | span | use |
|---|---|---|
| DISCOVERY | 2022-01-01 -> 2024-06-30 | every threshold, event definition and model choice |
| VALIDATION 1 | 2024-07-01 -> 2024-12-31 | frozen specification only |
| RECENT VALIDATION | 2025-01-01 -> 2025-06-30 | frozen specification only; "does it survive 2025" |
| LOCKED HOLDOUT | 2025-07-01 -> 2026-09-30 | not touched |

Phase 1 exception, declared now: the discovery report evaluates the pre-declared ANCHOR event definitions
(section 5, the owner's initial threshold hypotheses) on the two validation windows as well, because the
owner asked whether the effects survive 2025. Nothing in the threshold neighbourhoods is selected from
validation data; the neighbourhood tables are discovery-only.

## 2. Data

- Primary: Bybit-native trades (`public.bybit.com/trading/BTCUSDT/`), re-streamed day by day into
  immutable 1-minute bars (`data/btc/datasets/bybit_trades_1m/`, raw gz not retained, sha256 manifest
  `data/btc/raw/manifest_bybit_trades_1m.jsonl`; days >= 2025-07-01 refused without an owner flag).
  Per 1m row: OHLC, volume, quote volume, trade count, buy/sell counts, aggressor buy/sell quantity and
  notional, max trade, VWAP, and LARGE-TRADE aggressor buy/sell quantity for three PIT thresholds.
- Large-trade thresholds (PIT by construction): per UTC day the ingestor records the trade-size
  quantiles (p50, p90, p95, p97.5, p99) of that day; the threshold applied to day D is the median of
  the previous 7 days' quantile (days D-7..D-1), so no trade of day D influences its own threshold.
  Research percentiles: p90, p95, p97.5 (declared; never chosen from validation performance).
- 5m setup bars, 15m structure and 1h regime are derived from the 1m bars (completed bars only).
- Binance open interest / funding are NOT used in Phase 1 (the question is microstructure); they may
  be reported as context in Phase 2.
- Data-quality rules as V5.1/V6.1: a 1m row is valid iff it exists with trades > 0 and a finite close;
  rolling features are NaN unless the lookback is valid; missing minutes are never backfilled.

## 3. Core features (concise; definitions fixed now; all PIT at the 1m close)

| group | feature | definition |
|---|---|---|
| intensity | `tc` | trades in the minute |
| | `tc_accel` | tc - mean(tc of the previous 5 minutes) |
| | `tc_z` | (log tc - rolling mean of log tc over the previous 1440 minutes) / rolling std (previous rows only) |
| | `burst_5` | sum(tc last 5 min) / (5 x mean tc over the previous 60 min) |
| imbalance | `imb_1`, `imb_5`, `imb_15` | (buy - sell) / (buy + sell) over 1, 5, 15 minutes |
| large trades | `lt_imb_q{90,95,975}_5` | (large buy - large sell) / (large buy + large sell) over 5 minutes at each PIT threshold; `lt_share_5` = large volume / total volume |
| delta | `delta_1`, `delta_5`, `delta_15` | signed aggressor volume sums; `delta_accel` = delta_5 - delta_5[-5] |
| price response | `impact_5` | (close - close[-5]) / ATR1m(14) divided by (delta_5 / rolling median of \|delta_5\| over the previous 1440 min): displacement in ATR units per unit of typical signed flow |
| | `resp_z` | z-score of \|impact_5\| against the previous 1440 minutes; `absorption` = strong flow (\|delta_5\| above its rolling 75th percentile) with weak response (\|impact_5\| below its rolling 25th percentile); `efficient` = strong flow with strong response (above the 75th) and sign agreement |
| volatility | `atr1m`, `atr5m` | Wilder ATR(14) on 1m / completed 5m bars |
| | `rv_15`, `rv_60` | realised volatility of 1m log returns over 15 / 60 minutes |
| | `compression_pct` | percentile rank of the last 30 minutes' range against the ranges of the previous 7 days' 30-minute windows (sampled every 5 minutes; previous rows only) |
| | `vol_expansion` | ATR1m(5) / ATR1m(60) |
| structure (5m/15m) | `swing_high/low_5m` (k=2), `range_hi/lo_60` (previous 60 min, excl. current), `break_up/dn` (close beyond the 60-min range), `failed_break` (broke within the last 5 bars and closed back inside), `impulse_15` (15m return in ATR5m units), `retrace` (retracement share of the last 2 h impulse), `dist_struct` (distance of close from the nearest swing in ATR5m) |
| regime (15m/1h) | `trend_15m`, `regime_1h` as in V6.1 (EMA21/50 with close confirmation; 1h adds the slope condition) |
| calendar | hour of day, weekday |

## 4. Discovery protocol

Events are 1m rows where a feature state holds (section 5). Forward outcomes are computed from the
following minutes only: forward return, favourable excursion (MFE) and adverse excursion (MAE) at 15,
30, 60, 120 and 180 minutes (in % and in ATR5m units), and the probability that price moves +X before
-Y (X, Y in {0.25, 0.40, 0.50, 0.75, 1.00} %) within 180 minutes, for the direction implied by the
feature (long and short separately; symmetric features are mirrored).

Matched controls: every event is compared with K = 5 control minutes drawn at random (seed declared) from
the same window with the same hour of day, the same recent-volatility tercile (rv_60 against the previous
7 days), the same recent-return tercile (15m return), the same 1h regime and the same structure type
(position tercile in the 4h range), that are not events of the same definition within +-60 minutes.
Reported per event definition and horizon: event mean, control mean, difference with a bootstrap 95%
interval, effect size (difference / control standard deviation), the same for MFE, MAE and the
+X-before-Y probabilities, split long/short, by 1h regime and by hour block, plus the natural event
frequency per calendar day (all events and first-in-cluster events with a 15-minute cluster).

Decision standard for "repeatable effect" (declared now): the signed 60-minute forward return of events
exceeds matched controls by at least 0.05 ATR5m with a bootstrap interval excluding zero in the
discovery window, with the same sign in at least 4 of the 5 discovery half-years, and the effect
survives on the anchor definition in both validation windows (same sign, interval not clearly
reversed). Anything weaker is reported as "no incremental information".

## 5. Event definitions studied (anchors from the owner's hypotheses; neighbourhoods on discovery only)

Single features (each at its anchor and a 3-point neighbourhood):
- INTENSITY_BURST: tc_z >= {1.0, 1.5, 2.0}; direction = sign(imb_5).
- IMBALANCE: \|imb_5\| >= {0.20, 0.30, 0.45}; direction = sign(imb_5).
- LARGE_TRADE_IMBALANCE: \|lt_imb_q95_5\| >= {0.15, 0.25, 0.40} (also q90 and q97.5 at the anchor 0.25); direction = sign.
- DELTA_ACCEL: delta_accel beyond its rolling 90th percentile of \|delta_accel\|; direction = sign.
- EFFICIENT_FLOW: strong flow + strong response (`efficient`); direction = sign(delta_5).
- ABSORPTION: strong flow + weak response (`absorption`); direction = AGAINST the flow (reversal hypothesis).
- COMPRESSION_BREAK: compression_pct <= {15, 20, 30} % and a 1m close beyond the 60-min range; direction = break direction.
- FAILED_BREAK: failed_break; direction = against the break.

Combinations (economically motivated, mapped to the three candidate engines):
- E1 TREND_PULLBACK_RELEASE: 15m trend + impulse_15 >= 1.5 ATR + retrace in {25-65}% + no close beyond the impulse origin, then tc_z >= 1.0 and imb_5 in the trend direction >= 0.20 and impact_5 > 0 (optional lt_imb >= 0.15).
- E2 FAILED_AUCTION_ABSORPTION: sweep of the previous 60-min / prior-day extreme with \|imb_5\| >= 0.20 in the sweep direction AND absorption (weak response), then a close back inside; direction = against the sweep.
- E3 COMPRESSION_EXPANSION: compression_pct <= 20% + break + tc_z >= 1.0 + volume expansion (volume last 5 min >= 1.5 x previous 60-min mean per 5 min) + \|imb_5\| >= 0.20 + efficient response; anti-chase: distance from the range edge <= {0.5, 0.75, 1.0} ATR5m.

## 6. What the discovery report must answer

(1) which features predict future displacement, (2) effect sizes, (3) long vs short, (4) strongest
horizon, (5) survival on 2025 data, (6) whether absorption is detectable, (7) whether intensity bursts
have continuation edge, (8) whether large-trade clusters add information beyond plain imbalance, (9)
whether the combinations improve materially over single features, (10) natural event frequency per day.
The report STOPS there. Phase 2 (engines, stops from structure with a volatility floor, exits A/B/C with
60/120/180-minute time stops, taker then maker-first costs, one position, 0.125% or 0.15% risk chosen
before validation, daily loss protection, frequency and success criteria of the owner's brief) is
written as a separate pre-registration only after the owner has read the discovery report.

## 7. Anti-overfitting

Neighbourhoods are 3 points; no grid beyond section 5; combinations limited to the three engines;
validation windows see only anchor definitions in Phase 1; the holdout is never read. The research tries
to DISPROVE: matched controls, half-year sign consistency, long/short separation, regime and hour splits.

## 8. Methods amendments recorded during harness construction (before any discovery result was read)

A technical dry run of the pipeline on shifted windows (2022 only, pipeline check) exposed three
mechanical problems; they were fixed before the pre-registered run and are recorded here:
1. Matched controls. The +-60-minute exclusion around every event row left NO control candidates for
   frequent states (an imbalance of |imb_5| >= 0.2 holds on about 60% of minutes). Controls are now
   minutes where the state does NOT hold, outside a +-2-minute buffer around event rows; the five
   matching keys (hour, volatility tercile, 15m-return tercile, 1h regime, structure tercile) are
   unchanged.
2. Bootstrap. Events and controls within a day overlap in time at 60-180-minute horizons, so an
   observation-level bootstrap understates uncertainty. Intervals are now a cluster bootstrap by UTC
   day (days resampled with replacement, difference of means recomputed).
3. E1 "positive price response to delta" was implemented as a sign identity (always true for a
   rising price). It now requires price and flow both moving with the trend and a response above the
   rolling weak-response threshold.
4. Outcomes in ATR units are undefined (excluded) when ATR5m is below 0.01% of price, a degenerate
   bar state that produced a single 6.8-ATR outlier in the dry run.
The report also states every effect in basis points next to ATR units so it can be read against the
~15 bps round-trip cost. No threshold, window or decision standard changed.

## 9. Phase 1 outcome (2026-10-12; `reports/BTC_SWING_V7_EDGE_DISCOVERY.md`, tables `reports/v7/`)

Executed as pre-registered on the Bybit-native 1m dataset (1,324 days, 1.52 bn trades, manifest
`manifests/v7_bybit_1m_dataset_manifest.json`); 25 event definitions, 1.9 M minutes, day-cluster
bootstrap, matched controls. Headline: **the microstructure states studied carry almost no incremental
information about future displacement once hour, volatility, recent return, regime and structure are
held fixed.** Trade-intensity bursts, taker imbalance, large-trade imbalance, delta acceleration and
efficient flow all show Δ60 within +-0.03 ATR5m (under 1 bp) of their matched controls. Three
definitions meet the declared discovery standard, and they are small:

| definition | first-in-cluster/day | Δ60 ATR5m [95% CI] | Δ60 bps | half-years + | val 1 Δ60 | 2025 Δ60 | survives both |
|---|---|---|---|---|---|---|---|
| FAILED_BREAK (reversal after a failed 60-min range break) | 25.8 | 0.119 [0.077, 0.161] | 2.1 | 5/5 | +0.108 [0.036, 0.176] | +0.063 [-0.007, 0.142] | yes |
| E1 TREND_PULLBACK_RELEASE | 3.6 | 0.165 [0.032, 0.300] | 3.0 | 4/5 (2022H1 negative) | +0.183 [-0.078, 0.427] | +0.238 [-0.023, 0.476] | yes (intervals include 0) |
| ABSORPTION (strong flow, weak response, traded against the flow) | 26.2 | 0.064 [0.029, 0.093] | 1.6 | 4/5 | -0.049 [-0.118, 0.019] | +0.019 [-0.047, 0.086] | no |

Cost-aware reading: the only bracket with positive expectancy after the ~15 bps round trip is E1 with
+-1.0% levels (P(+1% before -1%) 60.4% vs 55.3% control, about +6 bps net per resolved event), at
3.6 raw first-in-cluster events per day; FAILED_BREAK improves the +X-before-Y probabilities by about
2 pp but its brackets stay negative after costs; absorption is detectable only as a ~1.5 bp mean
effect that fails validation 1. Compression breaks REVERSE on average (Δ60 -0.07 to -0.15 ATR), so
E3 as hypothesised has no support; E2 (sweep + absorption) is positive on discovery but negative on
both validation windows. Large-trade clusters add nothing beyond plain imbalance; intensity bursts
have no continuation edge. Answers to the ten questions are in report sections 6-11. Phase 1 stops
here; whether an E1-only Phase 2 (one engine, wide structural stops, expected frequency well below
the 3-6/day objective) is worth pre-registering is the owner's decision.
