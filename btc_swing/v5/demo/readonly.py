"""READ-ONLY connectivity verification of the Bybit Demo account (no order, no state change).

Steps: authenticate + API key info (GET /v5/user/query-api), UNIFIED wallet balance
(GET /v5/account/wallet-balance), BTCUSDT position (GET /v5/position/list), open orders
(GET /v5/order/realtime). The Demo account is confirmed when the signed requests succeed on
api-demo.bybit.com (Demo-trading keys are valid only on the Demo host). Missing credentials fail
closed before any network I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from btc_swing.v5.demo.client import ApiResult, BybitDemoClient, Transport
from btc_swing.v5.demo.guard import (
    ALLOWED_REST_HOSTS,
    DEMO_REST_BASE,
    FORBIDDEN_REST_HOSTS,
    DemoCredentials,
    ExecutionMode,
    MissingDemoCredentialsError,
    mode_from_env,
)
from btc_swing.v5.demo.journal import HashChainJournal

TAG = "READONLY_VERIFICATION"


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReadOnlyResult:
    started_at: str
    mode: str
    credentials_present: bool
    checks: list[Check]
    hosts_contacted: list[str]
    production_auth_endpoint_used: bool
    journal_path: str
    journal_ok: bool
    journal_records: int
    blocker: str | None = None

    def line(self, name: str) -> str:
        c = next((x for x in self.checks if x.name == name), None)
        return "PASSED" if c and c.passed else "FAILED"


def _first(res: ApiResult, key: str) -> list[dict[str, Any]]:
    r = res.result if isinstance(res.result, dict) else {}
    v = r.get(key)
    return v if isinstance(v, list) else []


def run_readonly_verification(
    journal_path: Path,
    transport: Transport | None = None,
    env: dict[str, str] | None = None,
    symbol: str = "BTCUSDT",
) -> ReadOnlyResult:
    started = datetime.now(UTC).isoformat(timespec="seconds")
    journal = HashChainJournal(journal_path)
    mode = mode_from_env(env)
    checks: list[Check] = []
    names = [
        "DEMO AUTH",
        "API KEY INFO",
        "WALLET QUERY",
        "POSITION QUERY",
        "OPEN ORDERS QUERY",
        "DEMO ACCOUNT CONFIRMED",
    ]

    def done(blocker: str | None, hosts: set[str]) -> ReadOnlyResult:
        ok, n, _ = HashChainJournal.verify(journal_path)
        return ReadOnlyResult(
            started,
            mode.value,
            blocker is None or "credential" not in blocker,
            checks,
            sorted(hosts),
            any(h in FORBIDDEN_REST_HOSTS or h not in ALLOWED_REST_HOSTS for h in hosts),
            str(journal_path),
            ok,
            n,
            blocker,
        )

    try:
        creds = DemoCredentials.from_env(env)
    except MissingDemoCredentialsError as e:
        journal.append({"tag": TAG, "kind": "FAIL_CLOSED", "mode": mode.value, "reason": str(e)})
        for n in names:
            checks.append(Check(n, False, f"not attempted: {e}"))
        return done(f"credentials missing: {e}", set())
    if transport is None:
        from btc_swing.v5.demo.client import HttpxTransport

        transport = HttpxTransport()
    client = BybitDemoClient(creds, mode, journal, transport, TAG, DEMO_REST_BASE)
    journal.append(
        {"tag": TAG, "kind": "START", "mode": mode.value, "base": DEMO_REST_BASE, "symbol": symbol}
    )

    api = client.get("/v5/user/query-api")
    auth_ok = api.ok
    info = api.result if isinstance(api.result, dict) else {}
    checks.append(
        Check(
            "DEMO AUTH",
            auth_ok,
            "signed request accepted by api-demo.bybit.com" if auth_ok else api.summary(),
            {
                "http_status": api.http_status,
                "ret_code": api.ret_code,
                "latency_ms": api.latency_ms,
            },
        )
    )
    keep = (
        "readOnly",
        "permissions",
        "type",
        "isMaster",
        "unified",
        "uta",
        "expiredAt",
        "createdAt",
        "note",
        "vipLevel",
        "mktMakerLevel",
    )
    checks.append(
        Check(
            "API KEY INFO",
            auth_ok,
            ("read-only key" if str(info.get("readOnly")) == "1" else "read-write key")
            if auth_ok
            else api.summary(),
            {k: info.get(k) for k in keep if k in info},
        )
    )
    w = client.get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
    acct = (_first(w, "list") or [{}])[0]
    usdt: dict[str, Any] = next(
        (c for c in acct.get("coin", []) or [] if c.get("coin") == "USDT"), {}
    )
    checks.append(
        Check(
            "WALLET QUERY",
            w.ok,
            f"UNIFIED totalEquity {acct.get('totalEquity')} USD, USDT equity {usdt.get('equity')}"
            if w.ok
            else w.summary(),
            {
                "accountType": acct.get("accountType"),
                "totalEquity": acct.get("totalEquity"),
                "totalAvailableBalance": acct.get("totalAvailableBalance"),
                "usdt_equity": usdt.get("equity"),
                "usdt_wallet_balance": usdt.get("walletBalance"),
            },
        )
    )
    p = client.get("/v5/position/list", {"category": "linear", "symbol": symbol})
    pos = [x for x in _first(p, "list") if float(x.get("size") or 0) != 0]
    checks.append(
        Check(
            "POSITION QUERY",
            p.ok,
            (
                f"{len(pos)} open {symbol} position(s)"
                + (
                    f": {pos[0].get('side')} {pos[0].get('size')} @ {pos[0].get('avgPrice')}"
                    if pos
                    else " (flat)"
                )
            )
            if p.ok
            else p.summary(),
            {
                "positions": [
                    {
                        k: x.get(k)
                        for k in (
                            "side",
                            "size",
                            "avgPrice",
                            "leverage",
                            "positionIdx",
                            "liqPrice",
                            "stopLoss",
                            "takeProfit",
                            "unrealisedPnl",
                        )
                    }
                    for x in _first(p, "list")
                ]
            },
        )
    )
    o = client.get("/v5/order/realtime", {"category": "linear", "symbol": symbol})
    orders = _first(o, "list")
    checks.append(
        Check(
            "OPEN ORDERS QUERY",
            o.ok,
            f"{len(orders)} open {symbol} order(s)" if o.ok else o.summary(),
            {
                "orders": [
                    {
                        k: x.get(k)
                        for k in (
                            "orderId",
                            "orderLinkId",
                            "side",
                            "orderType",
                            "qty",
                            "price",
                            "orderStatus",
                            "reduceOnly",
                            "stopOrderType",
                        )
                    }
                    for x in orders
                ]
            },
        )
    )
    all_on_demo = client.hosts_contacted <= ALLOWED_REST_HOSTS
    checks.append(
        Check(
            "DEMO ACCOUNT CONFIRMED",
            auth_ok and all_on_demo,
            "authenticated on api-demo.bybit.com only (Demo-trading keys are valid only on the Demo host)"
            if auth_ok and all_on_demo
            else "not confirmed: authentication on the Demo host did not succeed",
            {"hosts_contacted": sorted(client.hosts_contacted)},
        )
    )
    journal.append(
        {
            "tag": TAG,
            "kind": "END",
            "mode": mode.value,
            "checks": {c.name: c.passed for c in checks},
        }
    )
    blocker = (
        None if all(c.passed for c in checks) else next(c.detail for c in checks if not c.passed)
    )
    return done(blocker, client.hosts_contacted)


def render_readonly_report(r: ReadOnlyResult) -> str:
    out = [
        f"# BTC V5 — Bybit Demo READ-ONLY connectivity verification ({r.started_at})\n",
        "Execution validation only; the frozen V5 strategy, forward journals and paper ledger are untouched. "
        f"Execution mode `{r.mode}` (BYBIT_EXECUTION_MODE; in DISABLED only read-only GET requests may be sent). No order was submitted.\n",
        "| result | value |",
        "|---|---|",
        f"| DEMO AUTH | {r.line('DEMO AUTH')} |",
        f"| WALLET QUERY | {r.line('WALLET QUERY')} |",
        f"| POSITION QUERY | {r.line('POSITION QUERY')} |",
        f"| OPEN ORDERS QUERY | {r.line('OPEN ORDERS QUERY')} |",
        f"| API KEY INFO | {r.line('API KEY INFO')} |",
        f"| DEMO ACCOUNT CONFIRMED | {'YES' if r.line('DEMO ACCOUNT CONFIRMED') == 'PASSED' else 'NO'} |",
        f"| PRODUCTION AUTH ENDPOINT USED | {'YES' if r.production_auth_endpoint_used else 'NO'} |",
        f"| hosts contacted | {', '.join(r.hosts_contacted) or 'none'} |",
        f"| credentials present | {'yes' if r.credentials_present else 'no'} |",
        f"| journal | `{r.journal_path}` ({r.journal_records} records, hash chain {'OK' if r.journal_ok else 'BROKEN'}) |",
        "",
        "| check | passed | detail |",
        "|---|---|---|",
    ]
    for c in r.checks:
        out.append(f"| {c.name} | {'yes' if c.passed else 'no'} | {c.detail} |")
    if r.blocker:
        out.append(f"\n**Blocker:** {r.blocker}\n")
    return "\n".join(out) + "\n"


__all__ = ["ExecutionMode", "ReadOnlyResult", "render_readonly_report", "run_readonly_verification"]
