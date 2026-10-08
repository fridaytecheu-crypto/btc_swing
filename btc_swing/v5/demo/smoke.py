"""EXECUTION_SMOKE: verify the Bybit DEMO integration before any strategy-driven order.

Every order is tagged EXECUTION_SMOKE (orderLinkId `SMOKE-<run>-<step>`, journal tag), journaled
to the separate smoke journal and NEVER enters the strategy journal or paper/demo performance.
The sequence aborts at the first failure; if a smoke position was opened it is always closed in a
cleanup step. If the account already holds a BTCUSDT position the run aborts without touching it.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from btc_swing.v5.demo.client import (
    BybitDemoClient,
    DemoApiError,
    DemoTransportError,
    fmt_price,
    fmt_qty,
)
from btc_swing.v5.demo.config import DEMO_REST_BASE, SMOKE_TAG, DemoExecConfig
from btc_swing.v5.demo.fills import (
    ExecutionConfirmationTimeoutError,
    aggregate_executions,
    backoff_schedule,
    confirm_executions,
    confirmed_record,
    latest_confirmed,
)
from btc_swing.v5.demo.ids import smoke_link_id

STEPS = [
    ("connectivity", "public server time (no authentication)"),
    ("authentication", "signed account query"),
    ("account_confirmed", "API key information on the DEMO host (account is DEMO)"),
    ("private_ws", "private DEMO WebSocket authentication"),
    ("wallet", "wallet balance"),
    ("instrument", "instrument information"),
    ("ticker", "current price"),
    ("position_before", "current position (must be flat)"),
    ("leverage_query", "query current leverage"),
    ("set_leverage", "set a safe demo leverage if required"),
    ("limit_create", "place a far-from-market limit order"),
    ("limit_cancel", "cancel the limit order"),
    (
        "market_fill",
        "place the minimum-size market order; confirm the fill from its executions (qty, price, fee)",
    ),
    ("attach_stop", "attach a stop loss"),
    ("attach_tp", "attach a take profit (position TP and a reduce-only limit TP)"),
    ("position_read", "read position state with stop and TP"),
    (
        "close",
        "close the demo position (reduce-only market); confirm the close from its executions",
    ),
    ("closed_pnl", "verify flat position; closed PnL of the close order; own net after fees"),
    (
        "reconcile",
        "own net (gross - entry fees - exit fees) vs Bybit closed PnL; USDT wallet delta",
    ),
    (
        "recovery",
        "restart reconciliation: duplicate orderLinkId rejected, no open smoke order, flat, latest EXECUTION_CONFIRMED = executions",
    ),
]


@dataclass
class SmokeResult:
    run_id: str
    started_at: str
    status: str = "RUNNING"  # PASSED | FAILED | BLOCKED
    steps: list[dict[str, Any]] = field(default_factory=list)
    cleanup: list[dict[str, Any]] = field(default_factory=list)
    finished_at: str | None = None
    endpoint: str = DEMO_REST_BASE
    authenticated_requests: int = 0
    orders_sent: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def run_execution_smoke(
    client: BybitDemoClient,
    cfg: DemoExecConfig,
    sleep: Callable[[float], None] = time.sleep,
    ws_auth: Callable[[], dict[str, Any]] | None = None,
    exec_rows: Callable[[], list[dict[str, Any]] | None] | None = None,
) -> SmokeResult:
    """`exec_rows` serves rows of a running private execution WebSocket stream (preferred source
    for fill confirmation); without it, `/v5/execution/list` is polled (bounded)."""
    run_id = datetime.now(UTC).strftime("%y%m%d%H%M%S")
    res = SmokeResult(run_id, _now())
    ctx: dict[str, Any] = {"position_opened": False}
    sc = cfg.smoke

    def wait_order(link: str, final: set[str]) -> dict[str, Any]:
        deadline = time.time() + sc.fill_timeout_s
        last: dict[str, Any] | None = None
        while time.time() < deadline:
            last = client.order(link)
            if last and last.get("orderStatus") in final:
                return last
            sleep(sc.poll_interval_s)
        raise TimeoutError(
            f"order {link} not in {sorted(final)} after {sc.fill_timeout_s}s (last {last and last.get('orderStatus')})"
        )

    def confirm_fill(leg: str, link: str, o: dict[str, Any]) -> dict[str, Any]:
        """`Filled` is provisional: journal it, then wait (bounded) for definitive executions and
        append EXECUTION_CONFIRMED. Never rewrites the provisional record; fails closed on timeout."""
        qty = float(o.get("cumExecQty") or 0)
        if qty <= 0:
            raise RuntimeError(f"{leg} order reported Filled with zero quantity")
        oid = str(o.get("orderId") or "") or None
        prov = {
            "leg": leg,
            "orderLinkId": link,
            "orderId": oid,
            "orderStatus": o.get("orderStatus"),
            "cumExecQty": qty,
            "avgPrice": float(o.get("avgPrice") or 0),
            "cumExecFee": o.get("cumExecFee"),
        }
        client.journal.append("ORDER_FILLED_PROVISIONAL", prov, SMOKE_TAG)

        def rows() -> list[dict[str, Any]] | None:
            if exec_rows is None:
                return None
            r = exec_rows()
            return None if r is None else [x for x in r if x.get("orderLinkId") == link]

        try:
            agg = confirm_executions(
                lambda: client.executions(link_id=link),
                qty,
                oid,
                link,
                sc.exec_confirm_timeout_s,
                sleep=sleep,
                ws_rows=rows,
            )
        except ExecutionConfirmationTimeoutError as e:
            client.journal.append(
                "EXECUTION_CONFIRMATION_TIMEOUT", {**prov, "last": e.last}, SMOKE_TAG
            )
            raise
        rec = confirmed_record(agg, link, oid, leg)
        client.journal.append("EXECUTION_CONFIRMED", rec, SMOKE_TAG)
        return {
            "link": link,
            "order_id": rec["orderId"],
            "qty": rec["total_qty"],
            "avg_price": rec["avg_price"],
            "fee": rec["total_fee"],
            "n_exec": rec["n_exec"],
            "exec_ids": rec["execIds"],
            "first_exec_ms": rec["first_exec_ms"],
            "fill_ms": rec["last_exec_ms"],
            "confirm_source": rec["source"],
            "confirm_attempts": rec["attempts"],
            "provisional": {k: prov[k] for k in ("cumExecQty", "avgPrice", "cumExecFee")},
        }

    def s_connectivity() -> dict[str, Any]:
        return client.sync_time()

    def s_auth() -> dict[str, Any]:
        info = client.account_info()
        return {
            "unified_margin_status": info.get("unifiedMarginStatus"),
            "margin_mode": info.get("marginMode"),
        }

    def s_wallet() -> dict[str, Any]:
        w = client.wallet_balance()
        ctx["wallet_before"] = w
        return w

    def s_instrument() -> dict[str, Any]:
        it = client.instrument()
        if (
            it.get("status") not in (None, "Trading")
            or math.isnan(it["min_qty"])
            or math.isnan(it["tick"])
        ):
            raise RuntimeError(f"instrument not tradable: {it}")
        ctx["inst"] = it
        return it

    def s_ticker() -> dict[str, Any]:
        t = client.ticker()
        if math.isnan(t["last"]) or t["last"] <= 0:
            raise RuntimeError("no last price")
        ctx["tick"] = t
        return t

    def s_pos_before() -> dict[str, Any]:
        p = client.position()
        if p["size"] > 0:
            ctx["foreign_position"] = True
            raise RuntimeError(
                f"account already holds a {cfg.symbol} position ({p['side']} {p['size']}): smoke aborted without touching it"
            )
        return p

    def s_leverage() -> dict[str, Any]:
        return client.set_leverage(sc.leverage)

    def s_limit_create() -> dict[str, Any]:
        it, t = ctx["inst"], ctx["tick"]
        link = smoke_link_id(run_id, "LIM")
        px = fmt_price(t["last"] * (1 - sc.limit_offset_frac), it["tick"], -1)
        r = client.create_order(
            "Buy", fmt_qty(it["min_qty"], it["qty_step"]), "Limit", link, price=px, tif="PostOnly"
        )
        res.orders_sent.append(link)
        o = wait_order(link, {"New", "PartiallyFilled"})
        ctx["limit_link"] = link
        return {
            "order_id": r.get("orderId"),
            "orderLinkId": link,
            "price": px,
            "status": o.get("orderStatus"),
        }

    def s_limit_cancel() -> dict[str, Any]:
        link = ctx["limit_link"]
        client.cancel_order(link)
        o = wait_order(link, {"Cancelled", "Deactivated"})
        if float(o.get("cumExecQty") or 0) > 0:
            raise RuntimeError("far limit order unexpectedly filled")
        return {"orderLinkId": link, "status": o.get("orderStatus")}

    def s_market() -> dict[str, Any]:
        it = ctx["inst"]
        link = smoke_link_id(run_id, "MKT")
        t_sub = client.now_ms()
        client.create_order("Buy", fmt_qty(it["min_qty"], it["qty_step"]), "Market", link)
        res.orders_sent.append(link)
        ctx["position_opened"] = True
        o = wait_order(link, {"Filled"})
        ctx["entry"] = {**confirm_fill("entry", link, o), "submitted_ms": t_sub}
        fill_ms = ctx["entry"]["fill_ms"]
        return {
            **ctx["entry"],
            "fill_latency_ms": (fill_ms - t_sub) if fill_ms else None,
            "status": o.get("orderStatus"),
        }

    def s_stop() -> dict[str, Any]:
        it, e = ctx["inst"], ctx["entry"]
        sl = fmt_price(e["avg_price"] * (1 - sc.stop_offset_frac), it["tick"], -1)
        client.trading_stop(stop_loss=sl)
        ctx["sl"] = sl
        return {"stop_loss": sl}

    def s_tp() -> dict[str, Any]:
        it, e = ctx["inst"], ctx["entry"]
        tp = fmt_price(e["avg_price"] * (1 + sc.tp_offset_frac), it["tick"], 1)
        client.trading_stop(take_profit=tp)
        link = smoke_link_id(run_id, "TPL")
        px = fmt_price(e["avg_price"] * (1 + 2 * sc.tp_offset_frac), it["tick"], 1)
        client.create_order(
            "Sell", fmt_qty(e["qty"], it["qty_step"]), "Limit", link, price=px, reduce_only=True
        )
        res.orders_sent.append(link)
        o = wait_order(link, {"New", "PartiallyFilled", "Untriggered"})
        ctx["tp"], ctx["tp_link"] = tp, link
        return {
            "position_take_profit": tp,
            "reduce_only_tp_link": link,
            "reduce_only_tp_price": px,
            "status": o.get("orderStatus"),
        }

    def s_position() -> dict[str, Any]:
        p = client.position()
        ok = p["size"] > 0 and p["stop_loss"] is not None and p["take_profit"] is not None
        if not ok:
            raise RuntimeError(f"position state incomplete: {p}")
        if (
            abs(float(p["stop_loss"]) - float(ctx["sl"])) > 1e-6
            or abs(float(p["take_profit"]) - float(ctx["tp"])) > 1e-6
        ):
            raise RuntimeError(f"stop/TP on exchange differ from requested: {p}")
        return p

    def s_close() -> dict[str, Any]:
        client.cancel_order(ctx["tp_link"])
        wait_order(ctx["tp_link"], {"Cancelled", "Deactivated"})
        p = client.position()
        link = smoke_link_id(run_id, "CLS")
        client.create_order(
            "Sell", fmt_qty(p["size"], ctx["inst"]["qty_step"]), "Market", link, reduce_only=True
        )
        res.orders_sent.append(link)
        o = wait_order(link, {"Filled"})
        ctx["position_opened"] = False
        ctx["exit"] = confirm_fill("exit", link, o)
        return ctx["exit"]

    def s_closed_pnl() -> dict[str, Any]:
        p = client.position()
        if p["size"] > 0:
            raise RuntimeError(f"position still open after close: {p}")
        e, x = ctx["entry"], ctx["exit"]
        # the closed-PnL record of THE close order (by orderId); it can lag like executions do
        mine: list[dict[str, Any]] = []
        for wait in [*backoff_schedule(sc.exec_confirm_timeout_s), None]:
            rows = client.closed_pnl(start_ms=e["submitted_ms"] - 60_000)
            mine = [r for r in rows if str(r.get("orderId") or "") == str(x["order_id"])]
            if mine or wait is None:
                break
            sleep(wait)
        if not mine:
            raise RuntimeError(f"no closed-PnL record for close order {x['order_id']}")
        gross = (x["avg_price"] - e["avg_price"]) * x["qty"]
        own_net = gross - e["fee"] - x["fee"]
        ctx["closed"] = {
            "bybit_closed_pnl": sum(float(r.get("closedPnl") or 0) for r in mine),
            "closed_pnl_records": len(mine),
            "own_gross": gross,
            "entry_fees": e["fee"],
            "exit_fees": x["fee"],
            "own_fees": e["fee"] + x["fee"],
            "own_net": own_net,
        }
        return ctx["closed"]

    def s_reconcile() -> dict[str, Any]:
        e, x, c = ctx["entry"], ctx["exit"], ctx["closed"]
        diff = abs(c["bybit_closed_pnl"] - c["own_net"])
        tol = sc.pnl_tolerance_usdt  # numerical only: far below a single entry/exit fee
        if abs(e["qty"] - x["qty"]) > 1e-12:
            raise RuntimeError("entry and exit quantities differ")
        if diff > tol:
            raise RuntimeError(
                f"Bybit closed PnL {c['bybit_closed_pnl']:.7f} differs from own net "
                f"{c['own_net']:.7f} (gross {c['own_gross']:.7f} - entry fees {c['entry_fees']:.8f} "
                f"- exit fees {c['exit_fees']:.8f}) by {diff:.7f} USDT (tolerance {tol})"
            )
        w, wb = client.wallet_balance(), ctx["wallet_before"]
        usdt_change = w["usdt_wallet"] - wb["usdt_wallet"]
        return {
            "closed_pnl_vs_own_net_diff_usdt": diff,
            "tolerance_usdt": tol,
            "wallet_after": w,
            # BTCUSDT is USDT-margined: the relevant delta is the USDT wallet balance (realised PnL
            # and fees). totalEquity covers every coin of the multi-asset demo account and moves
            # with their prices, so it is reported for information only.
            "wallet_change": usdt_change,
            "wallet_change_basis": "USDT walletBalance",
            "usdt_equity_change": w["usdt_equity"] - wb["usdt_equity"],
            "total_equity_change_all_assets_info_only": w["total_equity"] - wb["total_equity"],
            "usdt_wallet_change_minus_closed_pnl": usdt_change - c["bybit_closed_pnl"],
        }

    def s_account() -> dict[str, Any]:
        info = client.api_key_info()
        return {
            "host": cfg.rest_base,
            "key_info": info,
            "note": "the key authenticates on the DEMO host; production keys are rejected there",
        }

    def s_ws() -> dict[str, Any]:
        if ws_auth is None:
            raise RuntimeError("no private WebSocket check configured")
        r = ws_auth()
        if not r.get("ok"):
            raise RuntimeError(f"private WS auth failed: {r.get('ret_msg')}")
        return r

    def s_leverage_query() -> dict[str, Any]:
        p = client.position()
        ctx["lev_before"] = p.get("leverage")
        return {"leverage": p.get("leverage")}

    def s_recovery() -> dict[str, Any]:
        it, t = ctx["inst"], client.ticker()
        dup_rejected = False
        try:
            client.create_order(
                "Buy",
                fmt_qty(it["min_qty"], it["qty_step"]),
                "Limit",
                ctx["limit_link"],
                price=fmt_price(t["last"] * (1 - sc.limit_offset_frac), it["tick"], -1),
                tif="PostOnly",
            )
        except DemoApiError as e:
            dup_rejected = e.ret_code == 110072
            if not dup_rejected:
                raise
        if not dup_rejected:
            client.cancel_order(ctx["limit_link"])
            raise RuntimeError(
                "a reused orderLinkId was ACCEPTED (deterministic ids would not prevent duplicates)"
            )
        open_smoke = [
            o.get("orderLinkId")
            for o in client.open_orders()
            if str(o.get("orderLinkId", "")).startswith(f"SMOKE-{run_id}")
        ]
        if open_smoke:
            raise RuntimeError(f"open smoke orders remain: {open_smoke}")
        p = client.position()
        if p["size"] > 0:
            raise RuntimeError(f"position not flat: {p}")
        checks = {}
        records = client.journal.records()
        for leg in ("entry", "exit"):
            link = ctx[leg]["link"]
            # compare Bybit with the latest DEFINITIVE record, never with the provisional fill
            rec = latest_confirmed(records, link, SMOKE_TAG)
            if rec is None:
                raise RuntimeError(f"no EXECUTION_CONFIRMED record for {leg} ({link})")
            agg = aggregate_executions(client.executions(link_id=link), rec["orderId"], link)
            q, avg, fee = agg["total_qty"], agg["avg_price"], agg["total_fee"]
            ok = (
                q > 0
                and abs(q - rec["total_qty"]) < 1e-9
                and avg is not None
                and abs(avg - rec["avg_price"]) <= max(1e-9, rec["avg_price"] * 1e-9)
                and abs(fee - rec["total_fee"]) < 1e-9
                and sorted(agg["exec_ids"]) == sorted(rec["execIds"])
            )
            checks[leg] = {
                "exec_qty": q,
                "exec_avg": avg,
                "exec_fee": fee,
                "exec_ids": agg["exec_ids"],
                "duplicates_dropped": agg["duplicates_dropped"],
                "confirmed": {
                    k: rec[k] for k in ("total_qty", "avg_price", "total_fee", "execIds")
                },
                "match": ok,
            }
            if not ok:
                raise RuntimeError(
                    f"latest EXECUTION_CONFIRMED and Bybit executions differ for {leg}: {checks[leg]}"
                )
        return {
            "duplicate_order_link_id_rejected": True,
            "open_smoke_orders": 0,
            "position_flat": True,
            "journal_vs_executions": checks,
        }

    funcs: dict[str, Callable[[], dict[str, Any]]] = {
        "connectivity": s_connectivity,
        "authentication": s_auth,
        "account_confirmed": s_account,
        "private_ws": s_ws,
        "wallet": s_wallet,
        "instrument": s_instrument,
        "ticker": s_ticker,
        "position_before": s_pos_before,
        "leverage_query": s_leverage_query,
        "set_leverage": s_leverage,
        "limit_create": s_limit_create,
        "limit_cancel": s_limit_cancel,
        "market_fill": s_market,
        "attach_stop": s_stop,
        "attach_tp": s_tp,
        "position_read": s_position,
        "close": s_close,
        "closed_pnl": s_closed_pnl,
        "reconcile": s_reconcile,
        "recovery": s_recovery,
    }
    client.journal.append(
        "smoke_start",
        {"run_id": run_id, "endpoint": cfg.rest_base, "symbol": cfg.symbol},
        SMOKE_TAG,
    )
    for name, desc in STEPS:
        t0 = time.time()
        try:
            detail = funcs[name]()
            res.steps.append(
                {
                    "step": name,
                    "description": desc,
                    "ok": True,
                    "detail": detail,
                    "elapsed_s": round(time.time() - t0, 3),
                }
            )
        except (
            DemoApiError,
            DemoTransportError,
            TimeoutError,
            ExecutionConfirmationTimeoutError,
            RuntimeError,
            KeyError,
            ValueError,
        ) as e:
            geo = isinstance(e, DemoTransportError) and e.geo_blocked
            res.steps.append(
                {
                    "step": name,
                    "description": desc,
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}"[:400],
                    "geo_blocked": geo,
                    "elapsed_s": round(time.time() - t0, 3),
                }
            )
            res.status = (
                "BLOCKED"
                if isinstance(e, DemoTransportError) and name == "connectivity"
                else "FAILED"
            )
            break
    else:
        res.status = "PASSED"
    # cleanup: never leave a smoke order or position behind (only SMOKE-tagged objects are touched)
    if res.status != "PASSED" and not ctx.get("foreign_position"):
        for link in reversed(res.orders_sent):
            try:
                o = client.order(link)
                if o and o.get("orderStatus") in ("New", "PartiallyFilled", "Untriggered"):
                    client.cancel_order(link)
                    res.cleanup.append({"action": "cancel", "orderLinkId": link})
            except (DemoApiError, DemoTransportError) as e:
                res.cleanup.append({"action": "cancel", "orderLinkId": link, "error": str(e)[:200]})
        if ctx.get("position_opened"):
            try:
                p = client.position()
                if p["size"] > 0:
                    link = smoke_link_id(run_id, "CLN")
                    client.create_order(
                        "Sell" if p["side"] == "Buy" else "Buy",
                        fmt_qty(p["size"], ctx["inst"]["qty_step"]),
                        "Market",
                        link,
                        reduce_only=True,
                    )
                    res.cleanup.append({"action": "close", "orderLinkId": link, "size": p["size"]})
            except (DemoApiError, DemoTransportError) as e:
                res.cleanup.append({"action": "close", "error": str(e)[:200]})
    res.finished_at = _now()
    res.authenticated_requests = sum(
        1
        for r in client.journal.records()
        if r["kind"] in ("api", "api_error")
        and r["data"].get("request", {}).get("auth")
        and r["tag"] == SMOKE_TAG
        and r["data"]["request"].get("path")
    )
    client.journal.append(
        "smoke_end",
        {"run_id": run_id, "status": res.status, "orders": res.orders_sent, "cleanup": res.cleanup},
        SMOKE_TAG,
    )
    return res


def write_smoke_report(
    res: SmokeResult,
    journal_path: Path,
    chain: dict[str, Any],
    out_dir: Path,
    summary_path: Path,
    extra: dict[str, Any],
) -> Path:
    """Immutable per-run report (<out_dir>/<run_id>.md/.json) and the summary file (latest run)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    d = res.as_dict() | {"journal": str(journal_path), "journal_chain": chain, **extra}
    (out_dir / f"{res.run_id}.json").write_text(
        json.dumps(d, indent=1, sort_keys=True, default=str)
    )
    md = render_smoke(d)
    (out_dir / f"{res.run_id}.md").write_text(md)
    summary_path.write_text(md)
    return out_dir / f"{res.run_id}.md"


def render_smoke(d: dict[str, Any]) -> str:
    by = {s["step"]: s for s in d["steps"]}

    def mark(name: str) -> str:
        s = by.get(name)
        return "PASSED" if s and s["ok"] else ("FAILED" if s else "NOT RUN")

    def both(*names: str) -> str:
        marks = [mark(n) for n in names]
        return (
            "PASS"
            if all(m == "PASSED" for m in marks)
            else ("FAIL" if any(m == "FAILED" for m in marks) else "NOT RUN")
        )

    def one(name: str) -> str:
        return {"PASSED": "PASS", "FAILED": "FAIL"}.get(mark(name), "NOT RUN")

    checks = [
        ("DEMO AUTH", one("authentication")),
        ("DEMO ACCOUNT CONFIRMED", one("account_confirmed")),
        ("WALLET", one("wallet")),
        ("INSTRUMENT RULES", one("instrument")),
        ("PRIVATE WS", one("private_ws")),
        ("LIMIT CREATE/CANCEL", both("limit_create", "limit_cancel")),
        ("MARKET FILL", one("market_fill")),
        ("STOP", both("attach_stop", "position_read")),
        ("TAKE PROFIT", both("attach_tp", "position_read")),
        ("CLOSE POSITION", one("close")),
        ("FEES/FILLS RETRIEVED", one("closed_pnl")),
        ("RECOVERY/RECONCILIATION", both("reconcile", "recovery")),
    ]
    lines = [
        "# BTC V5 — Bybit DEMO execution smoke test",
        "",
        f"Run `{d['run_id']}` · started {d['started_at'][:19]} UTC · finished {str(d['finished_at'])[:19]} UTC · endpoint `{d['endpoint']}` (Bybit DEMO) · status **{d['status']}**",
        "",
        "Execution validation only. Every order in this run is tagged EXECUTION_SMOKE and is excluded from the strategy journal, the paper ledger and every performance figure. No production endpoint can be configured (host allowlist `api-demo.bybit.com`); no real order is possible.",
        "",
        "## Checklist",
        "",
        "| check | result |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in checks],
        f"| PRODUCTION AUTH ENDPOINT USED | {d.get('production_endpoint_used', 'NO')} |",
        f"| REAL-MONEY ORDER PLACED | {d.get('real_order_placed', 'NO')} |",
        f"| offline restart-recovery tests | {d.get('restart_recovery_tests', 'see test suite')} |",
        f"| mode after the run | {d.get('mode_after', 'DISABLED')} |",
        f"| live BTCUSDT min qty / qty step / min notional | {d.get('live_rules', 'not retrieved')} |",
        f"| minimum reference equity for the frozen TP1/TP2 structure at 0.25% risk | {d.get('min_reference_equity', 'not computed (no live rules)')} |",
        "",
        "## Steps",
        "",
        "| # | step | result | detail | seconds |",
        "|---|---|---|---|---|",
    ]
    for i, s in enumerate(d["steps"], 1):
        det = s.get("detail") if s["ok"] else s.get("error")
        txt = json.dumps(det, default=str)[:300].replace("|", "/")
        lines.append(
            f"| {i} | {s['step']} ({s['description']}) | {'ok' if s['ok'] else 'FAILED'} | {txt} | {s['elapsed_s']} |"
        )
    acct = _accounting_lines(by, d.get("execution_stream"))
    if acct:
        lines += ["", "## Execution accounting (definitive, from executions)", "", *acct]
    if d.get("cleanup"):
        lines += ["", "Cleanup actions: " + json.dumps(d["cleanup"], default=str)]
    lines += [
        "",
        "## Journal",
        "",
        f"- Smoke journal `{d['journal']}`: {d['journal_chain'].get('records')} hash-chained records, chain valid: {d['journal_chain'].get('ok')}. Requests are journaled without headers; the key and secret are scrubbed from every record.",
        f"- Authenticated requests sent in this run: {d.get('authenticated_requests', 0)}. Orders sent: {', '.join(d.get('orders_sent') or []) or 'none'}.",
    ]
    if d.get("notes"):
        lines += ["", "## Notes", "", *[f"- {n}" for n in d["notes"]]]
    return "\n".join(lines) + "\n"


def _accounting_lines(by: dict[str, dict[str, Any]], stream: dict[str, Any] | None) -> list[str]:
    out: list[str] = []
    if stream is not None:
        src = "private execution WebSocket" if stream.get("available") else "REST polling only"
        out.append(f"- Confirmation source preference: {src} ({stream.get('error') or 'ok'}).")
    for step, leg in (("market_fill", "ENTRY"), ("close", "CLOSE")):
        s = by.get(step)
        det = (s or {}).get("detail") or {}
        if not s or not s["ok"] or "fee" not in det:
            continue
        prov = det.get("provisional") or {}
        out.append(
            f"- {leg} `{det['link']}`: qty {det['qty']} @ {det['avg_price']} · fee {det['fee']:.8f} USDT"
            f" · {det['n_exec']} execution(s) {det['exec_ids']} · confirmed via {det['confirm_source']}"
            f" after {det['confirm_attempts']} attempt(s) · provisional Filled record: qty"
            f" {prov.get('cumExecQty')} @ {prov.get('avgPrice')}, cumExecFee {prov.get('cumExecFee')}"
        )
    c = (by.get("closed_pnl") or {}).get("detail") or {}
    if "own_net" in c:
        out.append(
            f"- gross {c['own_gross']:.7f} - entry fees {c['entry_fees']:.8f} - exit fees"
            f" {c['exit_fees']:.8f} = own net {c['own_net']:.7f} USDT; Bybit closed PnL"
            f" {c['bybit_closed_pnl']:.7f} USDT"
        )
    r = (by.get("reconcile") or {}).get("detail") or {}
    if "wallet_change" in r:
        out.append(
            f"- difference {r['closed_pnl_vs_own_net_diff_usdt']:.7f} USDT (tolerance"
            f" {r['tolerance_usdt']}); USDT wallet change {r['wallet_change']:.7f} USDT"
            f" (total multi-asset equity change {r['total_equity_change_all_assets_info_only']:.4f},"
            " information only: it moves with the prices of the other demo coins)"
        )
    return out
