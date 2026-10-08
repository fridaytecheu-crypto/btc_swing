# BTC V5 — Bybit DEMO execution smoke test

Run `261008192324` · started 2026-10-08T19:23:24 UTC · finished 2026-10-08T19:23:24 UTC · endpoint `https://api-demo.bybit.com` (Bybit DEMO) · status **BLOCKED**

Execution validation only. Every order in this run is tagged EXECUTION_SMOKE and is excluded from the strategy journal, the paper ledger and every performance figure. No production endpoint can be configured (host allowlist `api-demo.bybit.com`); no real order is possible.

## Checklist

| check | result |
|---|---|
| authentication passed | NOT RUN |
| wallet query passed | NOT RUN |
| order create/cancel passed | NOT RUN |
| market fill passed | NOT RUN |
| stop/TP passed | NOT RUN |
| close passed | NOT RUN |
| reconciliation passed | NOT RUN |
| restart recovery tests | PASSED (tests/v5/test_demo_strategy.py: 14 passed in 9.55s) |
| production endpoint used | no (every request is checked against api-demo.bybit.com before it is sent) |
| real order placed | no |
| mode after the run | DISABLED |

## Steps

| # | step | result | detail | seconds |
|---|---|---|---|---|
| 1 | connectivity (public server time (no authentication)) | FAILED | "DemoTransportError: /v5/market/time: HTTP 403: {\n   error:The Amazon CloudFront distribution is configured to block access from your country.\n}" | 0.566 |

## Journal

- Smoke journal `data/btc/forward/demo/smoke_journal.jsonl`: 6 hash-chained records, chain valid: True. Requests are journaled without headers; the key and secret are scrubbed from every record.
- Authenticated requests sent in this run: 0. Orders sent: none.

## Notes

- Executed from the ephemeral cloud container: Bybit's CloudFront edge answers HTTP 403 'configured to block access from your country' for api-demo.bybit.com (and stream-demo.bybit.com, api-testnet.bybit.com, api.bybit.com). This is Bybit's own jurisdiction restriction, not the session's network allowlist; it cannot be changed from here and must not be circumvented.
- The sequence stopped at the first, UNAUTHENTICATED request (public server time). No signed request, no API key and no order left this container.
- Re-run on the persistent host where Bybit Demo is reachable and permitted: BTC_DATA_DIR=/var/lib/btc_swing btc-swing v5 demo smoke --mode EXECUTION_SMOKE
