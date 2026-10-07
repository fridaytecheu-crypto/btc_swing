# BTC V5 — Forward observation runbook

Observational experiment: the FROZEN V5 strategy (classified C on history) is evaluated
prospectively on genuinely new Bybit public data. No order is placed, no credential exists, no
rule changes during the observation window. Target: >= 2 weeks, preferably 4 weeks, and enough
prospective signals/trades to be interpretable. Do not evaluate early; do not touch thresholds,
families, stops or exits because of early performance.

## 1. What is frozen (manifests/v5_forward_freeze.json)
- V5 config hash `d18ebf19bd0cde67be1c27683d3e7ad0385d2456a6edaa1fe55146f013096177`
  (`config/btc_swing_v5.yaml`: events, features `v5-feat-1`, events `v5-event-1`, exits
  `v5-exit-1`, execution geometry, stop logic, costs, 0.25% risk).
- Forward config hash (collection/scheduling only, `config/btc_swing_v5_forward.yaml`).
- Code commit at freeze and the observation start timestamp (2026-10-07 14:59:22 UTC).
- `btc-swing v5 forward freeze` is idempotent; every forward command re-checks the V5 hash and
  refuses to run if the strategy file changed.

## 2. Processes and data flow
```
Bybit public WS  --> collector (raw, immutable hourly JSONL, gzip after the hour closes)
                       data/btc/forward/bybit/BTCUSDT/<YYYY-MM-DD>/<HH>.jsonl[.gz] + state.json
raw --> RawProcessor --> completed 5m rows      data/btc/forward/derived/forward_5m/<day>.parquet
seed (Bybit public trading archive, days before the collector's first day)
                                                data/btc/forward/seed/days/<day>.parquet (+ raw .csv.gz, sha256 manifest)
seed + forward 5m rows --> FROZEN V5 feature frame + detectors + engine (re-run from the start every cycle)
   --> signal snapshots (append-only)           data/btc/forward/signals/signals.jsonl
   --> matured outcomes (append-only, >= 12 h)  data/btc/forward/signals/outcomes.jsonl
   --> paper ledger (closed trades append-only) data/btc/forward/paper/paper_trades.jsonl, paper_state.json
   --> cycle log                                data/btc/forward/logs/cycles.jsonl, forward_run.log
daily, 00:05 UTC --> immutable snapshot         reports/forward/<YYYY-MM-DD>.md + .json
```
Topics: publicTrade, orderbook.1, orderbook.50, tickers (mark, index, OI, funding, best bid/ask,
24h volume), allLiquidation, kline.5 (confirmed candles, cross-check). Every message is stored
verbatim with ts_received, exchange ts, channel, schema version and the payload sha256.
Guarantees: reconnect with backoff, 30 s stale timeout + ping, duplicate suppression (collector
and processor), order-book sequence-gap detection, state file for resumption, latency logging.

## 3. Commands
```
export BTC_DATA_DIR=./data
uv run btc-swing v5 forward freeze           # once; records hashes, commit, start timestamp
uv run btc-swing v5 forward seed             # warm-up days from the Bybit archive (idempotent)
nohup uv run btc-swing v5 forward run > forward_run.out 2>&1 &     # collector + 5-minute cycle + daily report
uv run btc-swing v5 forward status           # collector status, last message, open paper position, signals/trades today, cumulative result
uv run btc-swing v5 forward cycle            # one manual cycle (safe while the runner is stopped)
uv run btc-swing v5 forward report [--day YYYY-MM-DD]   # immutable snapshot (PARTIAL while the day runs)
```
Stop: `kill -TERM $(cat data/btc/forward/forward_run.pid)`; the collector flushes and saves state.
Restart: run the same `forward run` command. The processor replays raw files from its stored
offset; closed paper trades and signal snapshots are append-only and are re-derived identically
(the pipeline reports an integrity error if a closed trade ever re-derives differently). If the
derived bars must be rebuilt (e.g. after a processor code fix), delete
`data/btc/forward/derived/forward_5m` and `processor_state.json`: bars are a pure function of raw.
Never run two runners against the same data directory.

## 4. Warm-up and what can fire when
- Price indicators (ATR, EMA, 4H trend) and flow z-scores are warm from day 0 thanks to the seed
  (45 archive days of Bybit trades, same taker-side convention as the live stream).
- Open interest, funding, premium/basis have NO public history: their z-scores need 10 days
  (2880 bars) of forward data (funding: 90 funding observations = 30 days). Family B (absorption)
  can fire from day 0; families A, C and D need the OI z-score (day >= 10); family C's crowding
  filter uses funding/premium z only once available (NaN = not crowded, as in the frozen code).
  The daily snapshot lists which z-scores are available (`warmup`).
- Order-book features are diagnostics: the live 50-level book spans ~0.01-0.05% around mid, so
  "depth within 1%" is the whole visible book (flagged by `book_*_range_pct`); it is NOT the
  historical +-1..5% archive measure and no event uses it.
- Collector gaps up to 24 h are carried as flagged zero-volume bars (previous close) so the
  frozen row-based lags keep a regular grid; the first partial day of the observation is such a
  gap (archive days on/after the collector's first day are never used, so the warm-up series
  stays fixed). Signals within 12 bars of a gap carry `after_gap_12_bars = true`.

## 5. Liquidations (real, forward only)
`allLiquidation` messages are stored raw and aggregated per 5m bar: long/short liquidation
quantity and notional, counts, largest single order, 5m burst, intensity (BTC/h), ratio to traded
volume, and correlations with the OI change and aggressive flow in the daily snapshot. They are
diagnostics; family A keeps its frozen OI-flush proxy definition during the observation.

## 6. Signal snapshot contents (written before any outcome is known)
signal_id (family|side|close_ms), timestamp, family, direction, strength, first-in-cluster,
regime, every `v5-feat-1` feature value, 1H/4H context (ATR, EMA21, trend, swings, alignment),
plan: event close (hypothetical entry reference), entry zone, structural stop, volatility-floor
stop, stop chosen at the event close, stop % / ATR, TP1, TP2, risk amount, hypothetical quantity,
expected round-trip cost (% of notional and % of stop), exit rule; flow source; after-gap flag;
snapshot sha256; the frozen config hash. The executable fill (next 5m open after the 15m
confirmation and the zone trigger) is produced by the paper engine, not guessed in the snapshot.

## 7. Outcomes and paper ledger
After 12 h: signed forward returns at 15m/30m/1h/2h/4h/8h/12h, MFE/MAE in ATR, and a hypothetical
trade entered at the next 5m open with the snapshot's stop geometry, the frozen exits and costs
(gross R, fees, slippage, funding, net R). Paper ledger: the frozen V5 engine (one net position,
15m confirmation, zone trigger, volatility-floored structural stop, TP1/TP2/trail, 24 h cap,
taker fees, slippage, archive-style funding from the live funding rate), 0.25% risk on a fixed
10,000 USDT equity; equity, drawdown, open position marked at the last completed 5m close.

## 8. Daily report (immutable)
`reports/forward/<day>.md` + `.json`, written once at 00:05 UTC for the previous day (a report
generated during the day is saved as `<day>_partial_<HHMM>`): collector health (messages, gaps,
reconnects, latency), bars/gaps, warm-up, signals detected (list), hypothetical trades, open
and closed paper positions, daily and cumulative P&L, gross/net expectancy, family breakdown,
matured outcomes, liquidation diagnostics.

## 9. Hosting note
This repository's cloud session container is ephemeral; a 2-4 week run needs a persistent host
(any Linux machine with Python 3.12, `uv sync --all-extras`, outbound access to
`stream.bybit.com` and `public.bybit.com`, ~0.5 GB/day of compressed raw data). Copy the whole
`data/btc/forward` directory to move an observation between hosts (append-only artefacts).

## 10. Non-negotiables during the observation
No orders, no API keys, no real money; no threshold/family/stop/exit change; no ML/GPT ranking;
no early stop because of performance; the first two weeks are not evaluated.
