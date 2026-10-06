# BTC Swing V5 — Microstructure & Liquidation Driven Active Swing (frozen design)

Status: pre-registered design, committed BEFORE the first V5 outcome analysis (the commit hash is
recorded in the report and run manifest). Every number here is also in `config/btc_swing_v5.yaml`.
Paper/backtest research only: no live or paper trading, no order placement, no authenticated
credentials. V1–V4 artefacts are closed, immutable and untouched.

**Central question.** Do BTC liquidation, aggressive-flow, absorption and open-interest
microstructure events contain a repeatable directional edge over the next 1–8 hours that
survives realistic execution costs and occurs frequently enough to support an active swing style
of roughly 1–3 trades per day?

## 1. Lessons from V1–V4

V1 (structural setups) had a small gross edge that costs erased; V2 (learned ranking of V1
candidates) found no signal; V3 (active price-structure generator) had the right activity profile
but no gross edge and cost-destroying stops; V4 (coarse 1H positioning events) fixed the stop
geometry but the events carried no pooled forward edge, and deleveraging events continued rather
than reversed. V5 therefore (a) sources the thesis from 5-minute trade-flow, open-interest and
order-book microstructure, (b) measures event edge before any execution (Stage A) with
bootstrap intervals and strength monotonicity, (c) keeps the V4 stop philosophy (structural +
volatility floor), and (d) builds the forward data foundation (Bybit public collector) that the
historical archive lacks.

## 2. Historical data availability (audited 2026-10-06, Binance Vision public archive)

| Field | Historical source | Coverage | Status in V5 |
|---|---|---|---|
| Per-trade aggressor flag (aggTrades) | `futures/um/daily/aggTrades` | 2019-12-31 → present, 15–30 MB/day | Ingested as immutable 5-minute flow aggregates (raw sha256 recorded, raw not retained: ~35 GB); validated against kline taker-buy volume (correlation 0.99999 on the sample day) |
| Individual trades | `futures/um/daily/trades` | 2019-09 → present | Not ingested (superset of aggTrades for our purpose) |
| Order-book depth | `futures/um/daily/bookDepth` (depth at ±1..5% of mid, ~30 s snapshots) | 2023-01-01 → present | Ingested; features NaN before 2023-01-01 |
| Best bid/ask (bookTicker) | `futures/um/daily/bookTicker` | 2023-05-16 → 2024-03-30 only | Not usable across the window; not ingested |
| Liquidations / forced orders | `liquidationSnapshot` | NOT published for BTCUSDT UM (404) | **Unavailable historically.** Forward collector only. Family A uses an explicitly labelled OI-flush PROXY in history |
| Open interest, long/short, taker ratios | `metrics` (5-minute rows) | 2020-09 → present | Already ingested (V1) |
| Mark price, premium index, funding | klines / premiumIndexKlines / fundingRate | 2020-01 → present | Already ingested (V1) |
| Index price | `indexPriceKlines` 5m | 2020-01 → present | Ingested (basis = perp / index - 1) |
| Volume, taker-buy volume | klines 5m | 2020-01 → present | Already ingested (V1) |
| Bybit historical trades | public.bybit.com (daily CSV) | reachable | Not needed historically (Binance aggTrades cover the window); noted for the forward venue |

Nothing is synthesised: no order book is reconstructed from candles, no liquidation series is
approximated from price, no field is backfilled. Order-book features exist only from 2023-01-01
and are reported as diagnostics; no event definition requires them, so every family is evaluated
on the full 2022-01..2026-09 window with genuinely available flow/OI/derivatives data.

## 3. Forward data (Bybit public, no authentication)

A public WebSocket collector (`btc_swing/v5/collector.py`) subscribes to
`wss://stream.bybit.com/v5/public/linear` topics `publicTrade.BTCUSDT`, `orderbook.50.BTCUSDT`,
`tickers.BTCUSDT` (mark price, index price, funding, open interest, volume) and
`allLiquidation.BTCUSDT`, and persists immutable raw events (received timestamp, exchange
timestamp, symbol, channel, raw payload, schema version, sha256 of the payload) as hourly JSONL
files with a state file for resumability. It implements reconnect with backoff, heartbeat
monitoring (stale timeout), duplicate suppression (topic + exchange timestamp + update id),
order-book sequence-gap detection and latency statistics. A 40-second probe from the research
container received 1205 messages with ~70 ms exchange-to-receipt latency. The collector never
places orders.

## 4. PIT feature frame (5-minute granularity, past-only rolling statistics)

Row k describes the completed 5m bar closing at T_k and uses only rows with time <= T_k.
Rolling z-scores use the previous `z_window_bars` = 8640 5m rows (30 days), excluding the current
row, with at least `z_min_periods` = 2880; the funding z uses the previous 90 funding
observations. Features (`v5-feat-1`):
- TRADE FLOW (aggTrades 5m aggregates; kline taker-buy volume is the fallback for a missing day):
  buy/sell quantity, delta = buy - sell, delta over 15m and 1h, imbalance_1h = delta_1h /
  (buy+sell)_1h, CVD (cumulative delta), CVD slope 1h and 4h, flow acceleration (delta_15m minus
  the previous 15m delta), big-trade imbalance over 1h (trades >= 1 BTC), z-scores of
  imbalance_1h, cvd_slope_1h, flow acceleration and big-trade imbalance; price-flow divergence =
  z(ret_1h) - z(cvd_slope_1h).
- ORDER BOOK (from 2023-01-01, last snapshot <= T_k): bid/ask depth within 1% and 5%,
  imbalance_1 = (bid1 - ask1)/(bid1 + ask1), imbalance_5, change of imbalance_1 over 1h,
  z-scores of bid1, ask1 and total depth (liquidity pull = negative depth z on the far side,
  stacking = positive). Diagnostics only.
- LIQUIDATIONS: none historically (see section 2). Forward only.
- OPEN INTEREST (metrics 5m): level; change 5m, 15m, 1h; acceleration (chg_15m minus previous
  chg_15m); z-score of chg_1h and chg_15m.
- DERIVATIVES: funding, change, z (90 obs); premium 1h mean and z; basis (perp close / index
  close - 1), change over 1h, z.
- PRICE / VOLUME: returns 5m/15m/1h/4h with z-scores of ret_15m and ret_1h; realised volatility
  1h and 24h; ATR14(1h) (from the 1H series); 5m and 1h volume z-scores; range position over 48h;
  24h high/low breaks; 4H trend state (EMA21/EMA50/close) and 1H alignment.

## 5. Event families (exactly four; evaluated at every completed 5m bar; SHORT mirrors LONG)

All thresholds are z-units of the trailing 30-day distribution unless stated. s = +1 LONG, -1
SHORT. "Aligned" means the sign matches the trade direction.

### A. LIQUIDATION_CONTINUATION (historical OI-flush PROXY; forward: liquidation stream)
- LONG (short squeeze continuing): s·ret_1h_z >= 2.0; oi_chg_1h_z <= -1.5 (open interest
  flushed); vol_1h_z >= 1.5; s·imbalance_1h_z >= 1.0 (aggressive buying still dominant).
- Strength = vol_1h_z - oi_chg_1h_z. Direction = with the impulse.
- Forward (collector only): the same template with the liquidation-stream burst z-score replacing
  the OI-flush proxy; not evaluated historically because the data does not exist.

### B. ABSORPTION_REVERSAL
- LONG: s·imbalance_1h_z <= -2.0 (heavy aggressive selling); vol_1h_z >= 1.0; and price did not
  fall proportionally: s·ret_1h_z >= -0.5 (impact absorbed).
- Strength = (-s·imbalance_1h_z) - |ret_1h_z| (more selling with less price impact = stronger).

### C. FLOW_OI_CONTINUATION
- LONG: s·ret_1h_z >= 1.0; s·imbalance_1h_z >= 1.0; s·cvd_slope_1h_z >= 1.0; oi_chg_1h_z >= 1.0
  (OI expanding); not crowded: s·fund_z <= 2.0 and s·prem_z <= 2.0.
- Strength = (s·imbalance_1h_z + s·cvd_slope_1h_z + oi_chg_1h_z) / 3.

### D. FLOW_DIVERGENCE_REVERSAL
- SHORT: the 5m high exceeds the highest high of the previous 288 bars (new 24h high) while
  cvd_slope_1h_z <= 0 (flow not confirming) and oi_chg_1h_z <= 0 (no new positioning).
- Strength = -(cvd_slope_1h_z + oi_chg_1h_z) / 2. LONG mirror at a new 24h low.

Stage A records every event; "first-in-cluster" = no event of the same family and side in the
previous 12 bars (1 h). The lifecycle (Stage B) additionally de-duplicates on the event anchor
(the event bar's close) for 24 h.

## 6. Stage A — forward labels and gate (before any execution)

For every event, from the event bar's close: signed forward returns at 15m, 30m, 1h, 2h, 4h,
8h, 12h; MFE and MAE over 12 h in ATR(1h) units (maximum continuation / maximum reversal).
Reported per family and side: count, mean, median, hit rate, t-statistic, 1000-resample
bootstrap 95% interval of the mean (1h and 4h), year-by-year and quarter-by-quarter means,
strength terciles (fixed within-family quantiles of the strength distribution, 1 = weakest), and
the unconditional distribution of 5m-bar forward returns. Time-matched and regime-matched random
bars (same count) give the Stage A null.
Pre-declared Stage A gate, per family (first-in-cluster events): n >= 100; mean signed return > 0
with t >= 2.0 at 1 h OR 4 h; positive mean at that horizon in >= 3 of the 5 calendar years; top
strength tercile mean >= bottom tercile mean at that horizon. A family passing the gate is
"structurally promising". No post-hoc subgroup is declared successful.

## 7. Stage B — generic execution (frozen, identical for all four families)

- Context: 4H/1H from the feature frame (reported); 15m = one confirmation; 5m = execution.
- Confirmation (one condition): the first completed 15m bar after the event whose close is beyond
  the previous 15m close in the trade direction. Thesis window 2 h after the event.
- Entry zone = [event close - 0.5 ATR(1h), event close + 1.0 ATR(1h)] for LONG (mirror SHORT);
  execution = a completed 5m close inside the zone after confirmation (at most 6 bars), fill at
  the next 5m open plus slippage. Reported: event time, entry time, delay, entry price, movement
  already missed (signed, in ATR).
- Stop = the farther of (lowest 5m low of the 12 bars before the event bar - 0.25 ATR(1h)) and
  (trigger close - 1.25 ATR(1h)), capped at 3.0 ATR(1h). One method, not searched.
- Exits: TP1 +1R (40%, stop to breakeven), TP2 +2R (30%), remainder trails the last confirmed 1H
  swing low - 0.5 ATR(1h) (mirror) after TP1, applied from the next bar; time cap 24 h;
  stop-first; liquidation on mark price. Counterfactual 1R/1.5R/2R/3R-before-stop recorded.
- Costs (primary, frozen): taker 5 bps per fill, slippage 2 bps entries / 5 bps stop-type exits,
  archive funding. Bybit-style sensitivity (taker 5.5 bps, maker 2 bps on targets) reported,
  never used to classify.
- Risk: 0.25% per trade on a fixed 10,000 USDT research equity; one net position; no pyramiding;
  leverage ladder 1/2/3/5/10x under the V1 liquidation constraints; caps reported. Safety
  overlay (3 full-risk losses or -0.75% realised in a UTC day -> no new entries) reported separately.

## 8. Evaluation, nulls, outliers, classification

Chronological reporting 2022, 2023, 2024, 2025, 2026 YTD and quarterly; events/day, qualified
setups/day and trades/day reported separately; days with 0/1/2/3+ trades; holding distribution;
gross, cost drag and net R per family; stop % / ATR / cost-to-stop; MFE/MAE; exits; expectancy
without the best 1/3/5 and worst 1/3/5 trades and the median R; nulls (time-matched and
regime-matched random entries with the same side, stop %, ATR, sizing, exits and costs, K = 10).
Pre-declared classification (encoded in `btc_swing/v5/evaluation.py`), raw 0.25% stream:
1. at least one family passes the Stage A gate; 2. pooled gross expectancy >= +0.15R; 3. pooled
net expectancy >= +0.08R; 4. PF >= 1.20; 5. net > 0 in >= 3 of 5 years and >= 60% of quarters with
>= 10 trades; 6. cost drag <= 50% of gross; 7. max drawdown <= 12%; 8. 0.5–3 trades/day and
median hold 1–8 h; 9. net without the 5 best trades >= +0.04R and no family > 60% of net P&L;
10. beats both nulls.
A — MICROSTRUCTURE EDGE STRONG ENOUGH FOR FORWARD PAPER TEST: all ten.
B — MICROSTRUCTURE EDGE EXISTS, CONTROLLED V5.1 RESEARCH JUSTIFIED: criterion 1 AND pooled gross
>= +0.10R AND criterion 10, but any other fails.
C — NO ROBUST MICROSTRUCTURE EDGE: otherwise.
No threshold, stop, window, exit or family is changed after seeing results; no ML.

## 9. Bybit Demo execution abstraction (designed, not activated)

`btc_swing/v5/execution.py` defines the `ExecutionAdapter` protocol (place_order, cancel_order,
position_state, set_stop, set_take_profit, account_balance, fills, funding) with typed request
and response records, a `DryRunAdapter` that only records intents, and a `BybitDemoAdapter`
skeleton holding the endpoint configuration (`api-demo.bybit.com`) whose network methods raise
`NotActivated` until an owner-approved activation; no credentials are read or required in V5.

## 10. Deliverables

`btc_swing/v5/` (ingest, features, events, engine, stage_a, null, evaluation, research, report,
collector, execution), `config/btc_swing_v5.yaml`, `tests/v5/`, CLI `btc-swing v5 ingest|collect|
research`, `reports/BTC_SWING_V5_MICROSTRUCTURE_RESEARCH.md` (36 sections), run manifest.
