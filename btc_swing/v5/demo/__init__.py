"""BTC V5 — Bybit DEMO execution validation (operational; NOT part of the frozen V5 strategy).

Separate from the forward observation: it never changes a V5 rule, the forward journals or the
paper ledger. Authenticated REST goes ONLY to https://api-demo.bybit.com and private streams ONLY to
wss://stream-demo.bybit.com; credentials come only from BYBIT_DEMO_API_KEY / BYBIT_DEMO_API_SECRET;
the execution mode comes only from BYBIT_EXECUTION_MODE (default DISABLED). In DISABLED mode the
client may only send read-only GET requests; every state-changing request is refused before any
network I/O."""
