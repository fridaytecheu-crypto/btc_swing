"""Definitive execution accounting for filled orders (Bybit DEMO).

Bybit order creation is asynchronous: an order can already report `orderStatus = Filled` (with
`cumExecQty` / `avgPrice`) while `/v5/execution/list` does not yet contain its execution records,
so fees read at that moment are 0. `Filled` is therefore only a PROVISIONAL fill state. Execution
accounting is final only once the executions themselves are visible:

- at least one execution exists for the order (matched by orderId or orderLinkId);
- the aggregated `execQty` equals the confirmed filled quantity;
- a weighted average `execPrice` can be computed;
- every execution carries its `execFee` and `execId`.

Sources, in order of preference: the authenticated private `execution` WebSocket stream (when one is
running) and, as fallback, polling `/v5/execution/list`. Rows from both are merged and de-duplicated
by `execId`. Polling uses a bounded backoff schedule; on expiry `ExecutionConfirmationTimeoutError` is
raised and the caller fails closed. Nothing ever waits forever.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

NON_ORDER_EXEC_TYPES = frozenset({"Funding", "Settle", "Delivery"})
QTY_EPS = 1e-9


class ExecutionConfirmationTimeoutError(RuntimeError):
    """Executions for a filled order did not become definitive before the bounded timeout."""

    def __init__(self, msg: str, last: dict[str, Any]) -> None:
        super().__init__(msg)
        self.last = last


def _num(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def aggregate_executions(
    rows: list[dict[str, Any]], order_id: str | None = None, link_id: str | None = None
) -> dict[str, Any]:
    """Aggregate the trade executions of ONE order; duplicates (same execId) are counted once.

    total_qty = sum(execQty); avg_price = sum(execPrice * execQty) / total_qty;
    total_fee = sum(execFee).
    """
    seen: dict[str, dict[str, Any]] = {}
    duplicates = 0
    missing_id = 0
    for e in rows:
        if e.get("execType") in NON_ORDER_EXEC_TYPES:
            continue
        mine = (order_id and e.get("orderId") == order_id) or (
            link_id and e.get("orderLinkId") == link_id
        )
        if not mine:
            continue
        xid = str(e.get("execId") or "")
        if not xid:
            missing_id += 1
            continue
        if xid in seen:
            duplicates += 1
            continue
        seen[xid] = e
    execs = sorted(seen.values(), key=lambda e: (int(e.get("execTime") or 0), str(e["execId"])))
    qtys = [_num(e.get("execQty")) for e in execs]
    prices = [_num(e.get("execPrice")) for e in execs]
    fees = [_num(e.get("execFee")) for e in execs]
    numbers_ok = all(q is not None and q > 0 for q in qtys) and all(
        p is not None and p > 0 for p in prices
    )
    total_qty = sum(q or 0.0 for q in qtys)
    notional = sum((q or 0.0) * (p or 0.0) for q, p in zip(qtys, prices, strict=True))
    times = [int(e.get("execTime") or 0) for e in execs]
    return {
        "order_id": order_id
        or next((str(e.get("orderId")) for e in execs if e.get("orderId")), None),
        "order_link_id": link_id,
        "n_exec": len(execs),
        "exec_ids": [str(e["execId"]) for e in execs],
        "total_qty": total_qty,
        "avg_price": notional / total_qty if numbers_ok and total_qty > 0 else None,
        "total_fee": sum(f or 0.0 for f in fees),
        "fees_available": bool(execs) and all(f is not None for f in fees),
        "numbers_ok": numbers_ok,
        "first_exec_ms": min(times) if times else None,
        "last_exec_ms": max(times) if times else None,
        "duplicates_dropped": duplicates,
        "rows_without_exec_id": missing_id,
    }


def is_definitive(agg: dict[str, Any], expected_qty: float) -> tuple[bool, str]:
    if agg["n_exec"] < 1:
        return False, "no execution visible yet"
    if agg["rows_without_exec_id"]:
        return False, "execution row(s) without execId"
    if not agg["numbers_ok"] or agg["avg_price"] is None:
        return False, "execution price/quantity not usable"
    if abs(agg["total_qty"] - expected_qty) > QTY_EPS:
        return False, f"execQty {agg['total_qty']:.9f} != filled {expected_qty:.9f}"
    if not agg["fees_available"]:
        return False, "execution fee not available"
    return True, "definitive"


def backoff_schedule(
    timeout_s: float, first_s: float = 0.25, factor: float = 2.0, max_s: float = 2.0
) -> list[float]:
    """Sleep intervals between attempts; their sum is >= timeout_s and the list is finite."""
    out: list[float] = []
    total, nxt = 0.0, first_s
    while total < timeout_s:
        step = min(nxt, max_s, timeout_s - total)
        out.append(step)
        total += step
        nxt *= factor
    return out


def confirm_executions(
    fetch_rest: Callable[[], list[dict[str, Any]]],
    expected_qty: float,
    order_id: str | None,
    link_id: str | None,
    timeout_s: float,
    sleep: Callable[[float], None] = time.sleep,
    ws_rows: Callable[[], list[dict[str, Any]] | None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Wait (bounded) until the order's executions are definitive; return the aggregate.

    The number of attempts is fixed by the backoff schedule, so the wait is bounded even when
    `sleep` is a no-op; the wall-clock deadline is checked as well."""
    if not order_id and not link_id:
        raise ValueError("confirm_executions needs an orderId or an orderLinkId")
    schedule = backoff_schedule(timeout_s)
    deadline = clock() + timeout_s
    errors: list[str] = []
    agg: dict[str, Any] = aggregate_executions([], order_id, link_id)
    why = "not attempted"
    sources: set[str] = set()
    for attempt in range(len(schedule) + 1):
        rows: list[dict[str, Any]] = []
        ws = ws_rows() if ws_rows is not None else None
        if ws:
            rows += ws
            agg = aggregate_executions(rows, order_id, link_id)
            ok, why = is_definitive(agg, expected_qty)
            if ok:
                return agg | {"source": "ws", "attempts": attempt + 1, "errors": errors}
        try:
            rest = fetch_rest()
            rows += rest
            if rest:
                sources.add("rest")
        except Exception as e:  # transport/API errors are retried within the bound
            errors.append(f"{type(e).__name__}: {e}"[:200])
        if ws:
            sources.add("ws")
        agg = aggregate_executions(rows, order_id, link_id)
        ok, why = is_definitive(agg, expected_qty)
        if ok:
            src = "+".join(sorted(sources)) or "rest"
            return agg | {"source": src, "attempts": attempt + 1, "errors": errors[-5:]}
        if attempt >= len(schedule) or clock() >= deadline:
            break
        sleep(schedule[attempt])
    raise ExecutionConfirmationTimeoutError(
        f"executions for {link_id or order_id} not definitive after {timeout_s}s: {why}",
        agg | {"reason": why, "errors": errors[-5:]},
    )


def confirmed_record(
    agg: dict[str, Any], link_id: str | None, order_id: str | None, leg: str
) -> dict[str, Any]:
    """Payload of the append-only EXECUTION_CONFIRMED journal event."""
    return {
        "leg": leg,
        "orderLinkId": link_id,
        "orderId": order_id or agg.get("order_id"),
        "execIds": list(agg["exec_ids"]),
        "total_qty": agg["total_qty"],
        "avg_price": agg["avg_price"],
        "total_fee": agg["total_fee"],
        "n_exec": agg["n_exec"],
        "first_exec_ms": agg["first_exec_ms"],
        "last_exec_ms": agg["last_exec_ms"],
        "duplicates_dropped": agg["duplicates_dropped"],
        "source": agg.get("source"),
        "attempts": agg.get("attempts"),
    }


def latest_confirmed(
    records: list[dict[str, Any]], link_id: str, tag: str | None = None
) -> dict[str, Any] | None:
    """The latest definitive EXECUTION_CONFIRMED record for an orderLinkId (provisional fill records
    are ignored)."""
    out: dict[str, Any] | None = None
    for r in records:
        if r.get("kind") != "EXECUTION_CONFIRMED" or (tag is not None and r.get("tag") != tag):
            continue
        if r.get("data", {}).get("orderLinkId") == link_id:
            out = dict(r["data"])
    return out


def dedupe_executions(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Unique executions by execId (first occurrence kept) and the number of duplicates dropped."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    dups = 0
    for e in rows:
        xid = str(e.get("execId") or "")
        if xid and xid in seen:
            dups += 1
            continue
        if xid:
            seen.add(xid)
        out.append(e)
    return out, dups
