# V11-LITE-WARM — Bybit DEMO execution layer (paper money only)

Owner request 2026-10-16. An EXECUTION layer on top of the frozen V11-LITE-WARM shadow. It is not a
strategy change: the lite strategy hash (`c66ef46a493f…`), the bookkeeping version (`v11lw-book-2`), the V11
strategy hash (`a324dbcb0394…`), the shadow freeze and every shadow journal are unchanged. Bybit DEMO only
(`api-demo.bybit.com`, enforced by the shared V5 demo client); no production endpoint, key or path exists.
Nothing is activated by the code: Demo runs only after the owner's explicit `btc-swing v11lw demo activate`.

## 1. The shadow stays the source of truth

- The shadow engine thread journals every bar and every decision exactly as before. Only AFTER that, the
  optional observer `V11LiteWarmLive.demo_sink` receives COPIES (64-row column tails of high / low / close /
  ATR5 and a deep copy of the decision record). The sink returns nothing; its exceptions are swallowed; it
  only enqueues.
- A separate Demo worker thread drains that queue (Demo HTTP latency never delays a shadow tick); a Demo
  supervisor thread attaches the worker only while the host-bound `STRATEGY_DEMO_V11LW_ACTIVATED` event is
  effective and detaches it after deactivation once flat. Observation root only (never the verification root).
- Every eligible shadow trade is still recorded by the shadow. A Demo blocker only produces a
  `DEMO_ENTRY_BLOCKED` record in the Demo journal (with the exact blocker list); it never removes or changes a
  shadow record.
- Tests: the live `_step` replay produces byte-identical shadow journals (content of every record), state and
  decisions with no observer, with an executing Demo, with every Demo blocker active, and with a hostile
  observer that corrupts its copies and raises.

## 2. Execution of a shadow decision (no decision of its own)

Entry source: a shadow `SIGNAL_EXECUTED` decision strictly after `activated_at`, at most `entry_max_delay_s`
(90 s) old. Size = floor(5 USDT / |entry_ref - stop|, qty step) (0.10 % of the 5000 USDT reference equity, the
shadow's own stop distance); leverage 1 (margin efficiency only). Market entry (`V11LWD-<sha>-EN`), definitive
execution confirmation (orderId / execId / fee), then the exchange stop at the shadow's stop and reduce-only
limit TP1 (40 %) / TP2 (40 %) at the shadow's TP1 / TP2 (`-T1` / `-T2`). Management mirrors the frozen V11
exits on the shadow's bars: break-even (entry_ref + 14 bps) after TP1, runner trail (last 3 completed 5m bars
∓ 0.1 ATR5) after TP2 on the frozen 5m alignment, 20-min stall review, 60-min hard stop on the frozen clock
(decision bar close) — market close `-TC`. No averaging, pyramiding or martingale: one LITE Demo position at
most. Write-ahead state before every order; duplicate orderLinkIds are idempotent.

## 3. Safety rules (Demo only; the shadow is never blocked)

| blocker | rule |
|---|---|
| `DEMO_CAP_24H` | 10 Demo entries per rolling 24 h |
| `DAILY_STOP` | realised Demo net <= -25 USDT in the UTC day |
| `HALTED:3_CONSECUTIVE_FULL_LOSSES` | 3 consecutive closed trades with net R (vs 5 USDT) <= -0.8; cleared only by `v11lw demo resume` |
| `RECONCILIATION_REQUIRED` | any local / exchange mismatch (size before protection / close / management, unknown position at start, unconfirmed fill, lost ack); nothing on the exchange is touched until `v11lw demo reconcile-ack` (a mismatch still present is detected again) |
| `DEMO_POSITION_OPEN` | one LITE Demo position at most |
| `ENTRY_TOO_LATE`, `BELOW_MIN_QTY`, `EXCHANGE_UNREACHABLE` | stale decision, size below the exchange minimum, API down |
| `EXCHANGE_POSITION_NOT_FLAT`, `FOREIGN_OPEN_ORDERS` | isolated account not flat / an order that is not V11LWD- |
| `V10_POSITION_OR_ORDERS_ACTIVE`, `V10_RECONCILIATION_PENDING`, `OWNERSHIP_UNPROVEN` | shared account only (section 4) |

Owner acknowledgements (`reconcile-ack`, `resume`) are journal events applied by the executor in journal
order, so an acknowledgement never clears a mismatch detected after it. The Demo journal is hash-chained and
safe for several writers (file lock + chain-tail re-read per append).

## 4. Account modes and the V10 conflict gate

- `isolated` (DEFAULT, preferred): a separate Bybit Demo account / API key in `BYBIT_DEMO_V11LW_API_KEY` /
  `BYBIT_DEMO_V11LW_API_SECRET` (`~/.config/btc_swing/v11lw_demo.env`, mode 600; loaded only by the V11-LITE
  run script). Activation requires PROOF of isolation: the Bybit user id behind the LITE key differs from the
  one behind V10's key (both read with GET-only clients; ids are never printed), and the LITE account is flat
  with no open order.
- `shared` (V10's account; only with `account_mode: shared` AND `activate --shared-account`): no LITE entry
  while V10's local Demo state has a position or `reconcile_required`, while any `V10D-` order is open, while
  the exchange shows a position / order LITE cannot attribute, or while V10's state cannot be read.
  Re-checked immediately before each order. LITE only ever creates or cancels `V11LWD-` orders, never closes
  a position whose size differs from its own, and its close accounting ignores every non-LITE execution.
  Residual risks even with the gate (documented, not solvable from the LITE side): V10 itself refuses to
  enter while a LITE position or TP order exists (`EXCHANGE_POSITION_NOT_FLAT` / `FOREIGN_OPEN_ORDERS` in
  V10's executor), a V10 restart while LITE holds a position sets V10 `RECONCILIATION_REQUIRED`
  (`UNEXPECTED_OPEN_POSITION_AT_START`), V10's close accounting attributes linkless stop fills in its window,
  and a V10 entry racing a LITE entry nets in Bybit's one-way mode. Sharing is therefore NOT recommended.

## 5. Activation gates (`btc-swing v11lw demo activate --note ...`)

Not a cloud container; authority lease held by this host; V11-LITE runner alive; raw feed <= 120 s old;
scored inputs warm (every pre-declared field except the 1h ATR, every group, the live microstructure
contribution); observation window active; V11-LITE freeze / strategy hashes valid; Demo execution manifest
(`manifests/v11_lite_warm_demo_execution_freeze.json`) matches the Demo config and the unchanged strategy;
hash-chained journals valid; no V11-LITE Demo reconciliation / position / halt; Demo credentials present
(env, never printed); Bybit Demo reachable (read-only); the shared demo config file stays DISABLED; risk
0.10 % of 5000 USDT, leverage <= 2; then the isolated-account proof or the shared-account flag + V10
conflict gate. The event is host-bound. The runner attaches within ~30 s; decisions before `activated_at`
are refused (`PRE_ACTIVATION_SIGNAL_REFUSED`).

## 6. Files and commands

Package `btc_swing/v11_lite_warm/demo/` (config, conflict, executor, worker, activation, gates, freeze,
report, cli); config `config/btc_swing_v11_lite_warm_demo.yaml`; manifest
`manifests/v11_lite_warm_demo_execution_freeze.json`; state / journal / trades
`<BTC_DATA_DIR>/btc/forward_v11_lite_warm/demo/{strategy_state.json,strategy_journal.jsonl,demo_trades.jsonl}`;
report `reports/forward_v11_lite_warm/demo_report.{json,md}` (event counts: submitted / acknowledged /
confirmed / filled / partial fills / stops / TP orders / TP fills / time and stall exits / closes / API
errors / manage errors / reconciliation; performance: closed trades, wins / losses, win rate, gross, fees,
funding, net, PF, max DD, by side / selected_template / exit reason, slippage, current position; FULL SHADOW
vs EXECUTABLE DEMO SUBSET with the blocker of every shadow trade not executed). CLI: `btc-swing v11lw demo
status [--live] | freeze | activate --note [--shared-account] | deactivate --note | reconcile-ack --note |
resume --note | report`. V10, V11, V11-WARM, V5 / V5.1 code, configs, freezes and journals are untouched;
no V10 module is imported by the Demo package (V10 files are only read by the conflict gate).
