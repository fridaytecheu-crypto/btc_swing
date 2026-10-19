"""BTC Intraday Portfolio Research harness (E1 accepted range expansion, E2 failed auction into an
established range, E4 forced-flow response event study).

RESEARCH ONLY. This package has no exchange client, no credentials, no order type and no import of any
execution module; nothing in it can submit an order. It reads existing datasets and raw stores and writes
only under `reports/intraday_portfolio_research/<RUN_ID>/`. Frozen runners (V10, V11, V11-WARM,
V11-LITE), their configs, freezes, journals and state are never touched."""

RESEARCH_ONLY = True
ORDER_CAPABLE = False
