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
- `DISABLED` (default): no state-changing request is possible; the forward observation runs as before. The only authenticated traffic allowed in this mode is the explicit read-only preflight (`btc-swing v5 demo preflight`): a GET-only client (POST refused in code) plus the private DEMO WebSocket auth.
- `EXECUTION_SMOKE`: only via `btc-swing v5 demo smoke --mode EXECUTION_SMOKE` (per-invocation opt-in; the config file stays `DISABLED`), and only when the last preflight (within 24 h) has `DEMO_EXECUTION_PREFLIGHT` PASSED. The forward-host gate is NOT required for the smoke.
- `STRATEGY_DEMO`: only when the owner edits `mode: STRATEGY_DEMO` in `config/btc_swing_v5_demo.yaml` AND a PASSED smoke report exists; `FORWARD_HOST_PREFLIGHT` is re-evaluated live when the executor is built and STRATEGY_DEMO fails closed (the observation continues) unless it passes; the forward runner then builds the executor, runs restart recovery and calls it after every 5-minute cycle. It never starts automatically.

## 3. Preflight (mode stays DISABLED): two separated gates
`btc-swing v5 demo preflight` (with `BYBIT_EXECUTION_MODE` unset or `DISABLED`) evaluates two gates.

**`DEMO_EXECUTION_PREFLIGHT`** (required for EXECUTION_SMOKE and for STRATEGY_DEMO). First, without
any authenticated request: not an ephemeral cloud-session container (no order from the cloud
environment), config mode and `BYBIT_EXECUTION_MODE` DISABLED, demo-only endpoint allowlist (REST
`https://api-demo.bybit.com`, WS `wss://stream-demo.bybit.com`), demo credentials present, Bybit
DEMO REST reachable (unauthenticated probe). Only if all pass: signed GET checks with a GET-only
client (account info = API key authentication, API key information, UNIFIED wallet, BTCUSDT
position, BTCUSDT open orders, live instrument rules incl. minimum quantity, quantity step, minimum
notional, tick and leverage filter, ticker), DEMO account confirmation (the key authenticates on the
demo host, where production keys are rejected), private DEMO WebSocket authentication, flat position
and no open orders, and "production authenticated endpoint never used" (every journaled request went
to `api-demo.bybit.com`, the WS endpoint is the demo one). It then computes, from the LIVE rules and
price, whether the reference equity represents the frozen TP1/TP2/remainder structure.

**`FORWARD_HOST_PREFLIGHT`** (required only for STRATEGY_DEMO; never for EXECUTION_SMOKE): persistent
host, forward runner running on this host, exactly one forward runner, systemd deployment active,
forward freeze V5 hash `d18ebf19bd0c…` and observation start 2026-10-07T14:59:22.972Z unchanged.
Local checks only, re-evaluated live whenever a STRATEGY_DEMO executor is built.

So a machine used only for demo connectivity and the smoke test (e.g. the owner's Mac, with no
forward runner) can pass the first gate and run EXECUTION_SMOKE, while STRATEGY_DEMO stays refused
there. The command exits 0 only if `DEMO_EXECUTION_PREFLIGHT` PASSED. Reports:
`reports/forward/demo_preflight/<run>.md|.json`.

## 4. EXECUTION_SMOKE sequence
connectivity (public time, unauthenticated) -> authentication (signed account query) -> account
confirmation (API key information on the demo host) -> private DEMO WebSocket auth -> wallet ->
instrument -> ticker -> position must be flat (a foreign position aborts without being touched) ->
leverage query -> set leverage if required -> far limit order create -> cancel -> minimum-size
market order and fill -> stop loss -> position TP and a reduce-only limit TP -> position read
(stop/TP match) -> close (reduce-only market) -> flat + executions, fees and closed PnL ->
reconciliation of Bybit's closed PnL with the journaled fills -> recovery check (re-submitting a
used orderLinkId must be rejected by Bybit as a duplicate, no open smoke order, flat position,
journal fills equal Bybit executions).
It aborts at the first failure and always closes a smoke position it opened. Reports: summary
`reports/forward/BYBIT_DEMO_EXECUTION_SMOKE.md`, immutable per run `reports/forward/demo_smoke/<run>.md|.json`.

## 5. STRATEGY_DEMO behaviour
- Trigger source: a subclass of the frozen engine records each trigger the frozen `_size` accepts; its result hash must equal the paper engine's (else no new trade). A demo entry is sent only for a trigger on the bar that just closed (frozen fill = next 5m open), within 120 s, whose signal id is in the immutable forward signal journal. No V5 signal -> no strategy order.
- Sizing: frozen `size_position` (same leverage ladder, margin cap, liquidation constraints) on the reference equity (`reference_equity_usdt`, 100 USDT), never on the demo wallet; quantity floored to the exchange step; never rounded up.
- Orders: deterministic `orderLinkId = V5D-<sha256(signal_id)[:20]>-<EN|T1|T2|TC|XC>`; write-ahead state before every order; Bybit rejects a duplicate id, so a restart can find but never re-create an order.
- Exits (frozen rules): exchange stop at the frozen stop (LastPrice trigger); TP1 +1R (40%) and TP2 +2R (30%) as reduce-only limits from the actual fill; breakeven after TP1; the frozen engine's own 1H swing trail after TP1 (applied one bar later, like the engine); 24 h time cap closed at market.
- Fail-safes (no NEW trade; an open position keeps being managed; alert): freeze/config mismatch, engine result mismatch, collector stale (> 120 s), runner stale (last bar > 15 min), gap-filled bar in the last 12 bars, late trigger, signal missing from the forward journal, reconciliation required, unexpected open position, order acknowledgement missing, duplicate signal, exchange unreachable. Clearing a reconciliation flag needs `btc-swing v5 demo reconcile-ack --note "…"` (journaled).
- Restart recovery (`recover()`, also run at runner start): queries the position and open orders; SUBMITTING with no order on Bybit -> abandoned, never resubmitted; order found -> adopted; fill resolved from the order and executions (partial fills adopted at the real size); stop and TP legs re-attached only if missing (looked up by id); unexpected position or strategy orders -> reconciliation required, never touched.
- Reconciliation per closed trade (`data/btc/forward/demo/demo_trades.jsonl`): SIGNAL (time, expected entry, stop, targets), PAPER (fill, costs, P&L scaled to the reference equity, R), DEMO (submitted price, fill, fill latency, fees, funding, exit fills, net P&L, R on actual risk and on planned risk) and the differences: entry fill (bps), fees (R), R, P&L.

## 6. Reference equity and other findings
1. **Reference equity is 2000 USDT** (owner decision 2026-10-08, `reference_equity_usdt` in `config/btc_swing_v5_demo.yaml`; 0.25% risk = 5 USDT per trade). It is execution-validation sizing only, not a real-money capital recommendation. The preflight recomputes the table below from LIVE rules; the figures here use Bybit's documented BTCUSDT rules (minimum 0.001 BTC, step 0.001 BTC, minimum notional 5 USDT), not confirmed live because Bybit blocks the cloud container, and the last collected mainnet price 81,659 USDT:

| V5 stop (research distribution) | frozen qty at 2000 USDT | TP1 / TP2 / remainder at the exchange step | all legs placeable | min equity: one lot | min equity: all three legs | min equity: exact 40/30/30 |
|---|---|---|---|---|---|---|
| p10 0.75% | 0.0082 -> 0.008 BTC | 0.003 / 0.002 / 0.003 | yes | ~245 | ~975 | ~2,440 |
| median 1.40% | 0.0044 -> 0.004 BTC | 0.001 / 0.001 / 0.002 | yes (25/25/50, not 40/30/30) | ~460 | ~1,830 | ~4,575 |
| p90 2.53% | 0.0024 -> 0.002 BTC | 0.000 / 0.000 / 0.002 | no (TP legs below minimum) | ~830 | ~3,310 | ~8,270 |

   The frozen fractions are never changed: a TP leg below the minimum is not placed (`TP_LEG_NOT_PLACED`) and flooring shifts quantity into the trailing remainder. Exact reproduction of 40/30/30 needs 10 lots (0.010 BTC), i.e. about 4,600 USDT at the median stop and about 8,300 USDT at the p90 stop at this price; all three legs at the p90 stop need about 3,300 USDT.
2. **Partial take-profits** are separate reduce-only orders; see the table for when they are representable.
3. **Bybit blocks this cloud container by country** (CloudFront 403 for `api-demo`, `stream-demo`, `api-testnet` and `api.bybit.com`). The smoke test must run on the persistent host, in a jurisdiction where Bybit permits access; do not route around the restriction.

## 7. Running on the persistent host
```
# 1) read-only preflight (mode stays DISABLED); DEMO_EXECUTION_PREFLIGHT must be PASSED
sudo systemd-run --pipe --wait --uid=btcswing -p WorkingDirectory=/opt/btc_swing \
  -p EnvironmentFile=/etc/btc-v5-demo.env -p Environment=BTC_DATA_DIR=/var/lib/btc_swing \
  /opt/btc_swing/.venv/bin/btc-swing v5 demo preflight
# 2) only then the smoke run below
sudo install -m 600 -o root -g root deploy/btc-v5-demo.env.example /etc/btc-v5-demo.env   # then put the DEMO key/secret in it
# the service unit already reads EnvironmentFile=-/etc/btc-v5-demo.env (unused while the mode is DISABLED)
# one-off smoke as the service user, with the two variables taken from that file:
sudo systemd-run --pipe --wait --uid=btcswing -p WorkingDirectory=/opt/btc_swing \
  -p EnvironmentFile=/etc/btc-v5-demo.env -p Environment=BTC_DATA_DIR=/var/lib/btc_swing \
  /opt/btc_swing/.venv/bin/btc-swing v5 demo smoke --mode EXECUTION_SMOKE
btc-swing v5 demo status ; btc-swing v5 demo verify-journal ; btc-swing v5 forward status-text
```
STRATEGY_DEMO is a separate owner decision after a PASSED smoke report (edit the mode, restart the service).
