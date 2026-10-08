# BTC V5 — Bybit Demo READ-ONLY connectivity verification (2026-10-08T06:33:29+00:00)

Execution validation only; the frozen V5 strategy, forward journals and paper ledger are untouched. Execution mode `DISABLED` (BYBIT_EXECUTION_MODE; in DISABLED only read-only GET requests may be sent). No order was submitted.

| result | value |
|---|---|
| DEMO AUTH | FAILED |
| WALLET QUERY | FAILED |
| POSITION QUERY | FAILED |
| OPEN ORDERS QUERY | FAILED |
| API KEY INFO | FAILED |
| DEMO ACCOUNT CONFIRMED | NO |
| PRODUCTION AUTH ENDPOINT USED | NO |
| hosts contacted | none |
| credentials present | no |
| journal | `/home/user/btc_swing/data/btc/demo_execution/readonly_verification.jsonl` (1 records, hash chain OK) |

| check | passed | detail |
|---|---|---|
| DEMO AUTH | no | not attempted: missing environment variable(s): BYBIT_DEMO_API_KEY, BYBIT_DEMO_API_SECRET — failing closed |
| API KEY INFO | no | not attempted: missing environment variable(s): BYBIT_DEMO_API_KEY, BYBIT_DEMO_API_SECRET — failing closed |
| WALLET QUERY | no | not attempted: missing environment variable(s): BYBIT_DEMO_API_KEY, BYBIT_DEMO_API_SECRET — failing closed |
| POSITION QUERY | no | not attempted: missing environment variable(s): BYBIT_DEMO_API_KEY, BYBIT_DEMO_API_SECRET — failing closed |
| OPEN ORDERS QUERY | no | not attempted: missing environment variable(s): BYBIT_DEMO_API_KEY, BYBIT_DEMO_API_SECRET — failing closed |
| DEMO ACCOUNT CONFIRMED | no | not attempted: missing environment variable(s): BYBIT_DEMO_API_KEY, BYBIT_DEMO_API_SECRET — failing closed |

**Blocker:** credentials missing: missing environment variable(s): BYBIT_DEMO_API_KEY, BYBIT_DEMO_API_SECRET — failing closed


## Environment findings (this run: Claude Code cloud container, 2026-10-08 06:33 UTC)

- `BYBIT_DEMO_API_KEY` and `BYBIT_DEMO_API_SECRET` were NOT present in this session's environment
  (variables added to a cloud environment reach NEW sessions only). The verification failed closed:
  zero network requests, zero authenticated requests, mode DISABLED.
- Network: every Bybit REST host answers this container with
  `HTTP 403 — "The Amazon CloudFront distribution is configured to block access from your country."`
  (unauthenticated probe of `https://api-demo.bybit.com/v5/market/time`, CloudFront POP IAD12). Bybit
  blocks the container's egress region, so the Demo verification cannot pass from this cloud
  container even with credentials. It must run on the persistent host (or any host in a region Bybit
  serves): `BYBIT_EXECUTION_MODE=DISABLED uv run btc-swing v5 demo verify-readonly`.
- No order was submitted; no production endpoint was contacted.
