# BTC V5 — Bybit DEMO execution validation

Execution validation of the FROZEN V5 forward system on Bybit DEMO. Not strategy research: no
threshold, family, filter, stop, TP, leverage rule or risk number changes, and demo results are
never used to change V5 during the observation window. The observation start
(2026-10-07 14:59:22 UTC), the freeze (`manifests/v5_forward_freeze.json`), the frozen V5 hash
`d18ebf19bd0c…` and the forward paper ledger are untouched; the paper ledger keeps running in
parallel and is the reference every demo trade is compared with.

## 1. Safety properties (enforced in code, covered by tests)
| property | where |
|---|---|
| Only `https://api-demo.bybit.com` can be configured or reached; production, regional mirrors and testnet are rejected; every request URL is re-checked before it is sent | `btc_swing/v5/demo/config.py` (`assert_demo_url`), `client.py` |
| Default mode `DISABLED`; in `DISABLED` no client can be constructed, so no authenticated request is possible; the program never rewrites the config file | `config/btc_swing_v5_demo.yaml`, `client.py` |
| Credentials only from `BYBIT_DEMO_API_KEY` / `BYBIT_DEMO_API_SECRET`; missing or empty -> fail closed; never printed (`repr` hidden); headers never journaled; key/secret scrubbed from every journal record | `credentials.py`, `client.py` |
| `EXECUTION_SMOKE` orders are tagged (`SMOKE-…` ids, journal tag) and go to a separate journal; the strategy journal refuses smoke records; reconciliation ignores smoke ids | `smoke.py`, `journal.py`, `ids.py` |
| `STRATEGY_DEMO` requires a PASSED smoke report and refuses a demo risk different from the frozen V5 risk | `strategy.py` |
| Every journal is append-only, fsync'ed and hash-chained (`btc-swing v5 demo verify-journal`) | `journal.py` |

## 2. Modes
- `DISABLED` (default): nothing demo-related is constructed; the forward observation runs as before.
- `EXECUTION_SMOKE`: only via `btc-swing v5 demo smoke --mode EXECUTION_SMOKE` (per-invocation opt-in; the config file stays `DISABLED`).
- `STRATEGY_DEMO`: only when the owner edits `mode: STRATEGY_DEMO` in `config/btc_swing_v5_demo.yaml` AND a PASSED smoke report exists; the forward runner then builds the executor, runs restart recovery and calls it after every 5-minute cycle. It never starts automatically.

## 3. EXECUTION_SMOKE sequence
connectivity (public time, unauthenticated) -> authentication (signed account query) -> wallet ->
instrument -> ticker -> position must be flat (a foreign position aborts without being touched) ->
set leverage -> far limit order create -> cancel -> minimum-size market order and fill -> stop loss
-> position TP and a reduce-only limit TP -> position read (stop/TP match) -> close (reduce-only
market) -> flat + closed-PnL record -> reconciliation of Bybit's closed PnL with the journaled fills.
It aborts at the first failure and always closes a smoke position it opened. Reports: summary
`reports/forward/BYBIT_DEMO_EXECUTION_SMOKE.md`, immutable per run `reports/forward/demo_smoke/<run>.md|.json`.

## 4. STRATEGY_DEMO behaviour
- Trigger source: a subclass of the frozen engine records each trigger the frozen `_size` accepts; its result hash must equal the paper engine's (else no new trade). A demo entry is sent only for a trigger on the bar that just closed (frozen fill = next 5m open), within 120 s, whose signal id is in the immutable forward signal journal. No V5 signal -> no strategy order.
- Sizing: frozen `size_position` (same leverage ladder, margin cap, liquidation constraints) on the reference equity (`reference_equity_usdt`, 100 USDT), never on the demo wallet; quantity floored to the exchange step; never rounded up.
- Orders: deterministic `orderLinkId = V5D-<sha256(signal_id)[:20]>-<EN|T1|T2|TC|XC>`; write-ahead state before every order; Bybit rejects a duplicate id, so a restart can find but never re-create an order.
- Exits (frozen rules): exchange stop at the frozen stop (LastPrice trigger); TP1 +1R (40%) and TP2 +2R (30%) as reduce-only limits from the actual fill; breakeven after TP1; the frozen engine's own 1H swing trail after TP1 (applied one bar later, like the engine); 24 h time cap closed at market.
- Fail-safes (no NEW trade; an open position keeps being managed; alert): freeze/config mismatch, engine result mismatch, collector stale (> 120 s), runner stale (last bar > 15 min), gap-filled bar in the last 12 bars, late trigger, signal missing from the forward journal, reconciliation required, unexpected open position, order acknowledgement missing, duplicate signal, exchange unreachable. Clearing a reconciliation flag needs `btc-swing v5 demo reconcile-ack --note "…"` (journaled).
- Restart recovery (`recover()`, also run at runner start): queries the position and open orders; SUBMITTING with no order on Bybit -> abandoned, never resubmitted; order found -> adopted; fill resolved from the order and executions (partial fills adopted at the real size); stop and TP legs re-attached only if missing (looked up by id); unexpected position or strategy orders -> reconciliation required, never touched.
- Reconciliation per closed trade (`data/btc/forward/demo/demo_trades.jsonl`): SIGNAL (time, expected entry, stop, targets), PAPER (fill, costs, P&L scaled to the reference equity, R), DEMO (submitted price, fill, fill latency, fees, funding, exit fills, net P&L, R on actual risk and on planned risk) and the differences: entry fill (bps), fees (R), R, P&L.

## 5. Findings the owner must decide on (nothing was changed)
1. **100 USDT reference equity is below Bybit's minimum order size for almost every V5 trade.** At 0.25% risk the risk budget is 0.25 USDT; with the V5 median stop of 1.40% of price and BTC near 86,000 USDT, the frozen size is about 0.0002 BTC, while Bybit's BTCUSDT minimum order is 0.001 BTC (documented value; read at runtime from `instruments-info`, not confirmed live from the cloud container). Rounding up would raise the risk to about 1.2% per trade, so the executor journals `SKIPPED_BELOW_MIN_QTY` with the reference equity that would be needed instead. Needed for one minimum lot at 0.25% risk: about 260 USDT (p10 stop 0.75%), 480 USDT (median 1.40%), 870 USDT (p90 2.53%). With 100 USDT, STRATEGY_DEMO would place no order at all.
2. **Partial take-profits need at least 3-4 minimum lots.** TP1 (40%) and TP2 (30%) are separate reduce-only orders; a leg smaller than the minimum lot is not placed (journaled `TP_LEG_NOT_PLACED`) and that quantity stays under stop, breakeven/trail and the time cap. Representing both legs at the median stop needs about 1,700 USDT reference equity.
3. **Bybit blocks this cloud container by country** (CloudFront 403 for `api-demo`, `stream-demo`, `api-testnet` and `api.bybit.com`). The smoke test must run on the persistent host, in a jurisdiction where Bybit permits access; do not route around the restriction.

## 6. Running on the persistent host
```
sudo install -m 600 -o root -g root deploy/btc-v5-demo.env.example /etc/btc-v5-demo.env   # then put the DEMO key/secret in it
# the service unit already reads EnvironmentFile=-/etc/btc-v5-demo.env (unused while the mode is DISABLED)
# one-off smoke as the service user, with the two variables taken from that file:
sudo systemd-run --pipe --wait --uid=btcswing -p WorkingDirectory=/opt/btc_swing \
  -p EnvironmentFile=/etc/btc-v5-demo.env -p Environment=BTC_DATA_DIR=/var/lib/btc_swing \
  /opt/btc_swing/.venv/bin/btc-swing v5 demo smoke --mode EXECUTION_SMOKE
btc-swing v5 demo status ; btc-swing v5 demo verify-journal ; btc-swing v5 forward status-text
```
STRATEGY_DEMO is a separate owner decision after a PASSED smoke report (edit the mode, restart the service).
