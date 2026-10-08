"""Fill-confirmation semantics (regression for the real DEMO smoke run 261008210024).

There, the market order reported `Filled` (qty 0.001 @ 81775.0) while `/v5/execution/list` was
still empty (n_exec 0, fee 0); the executions (fee 0.04497625) appeared only later, so recovery found
a journal/exchange mismatch and the own net PnL lacked both fees (-0.0171 vs Bybit -0.1070431).
`Filled` is now provisional; accounting is final only once the executions are visible."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from btc_swing.v5.demo.client import BybitDemoClient
from btc_swing.v5.demo.config import SMOKE_TAG, ExecutionMode, load_demo_config
from btc_swing.v5.demo.credentials import DemoCredentials
from btc_swing.v5.demo.fills import (
    ExecutionConfirmationTimeoutError,
    aggregate_executions,
    backoff_schedule,
    confirm_executions,
    latest_confirmed,
)
from btc_swing.v5.demo.journal import HashChainJournal, verify_chain
from btc_swing.v5.demo.smoke import SmokeResult, render_smoke, run_execution_smoke
from btc_swing.v5.demo.ws import ExecutionStream
from tests.v5.fake_bybit import FakeBybitDemo
from tests.v5.test_demo_strategy import _exec, _trig

ROOT = Path(__file__).resolve().parents[2]
CFG_PATH = ROOT / "config" / "btc_swing_v5_demo.yaml"


def _x(xid: str, qty: str, px: str, fee: str | None, t: int, link: str = "L1") -> dict[str, Any]:
    e = {
        "execId": xid,
        "orderId": "O1",
        "orderLinkId": link,
        "execQty": qty,
        "execPrice": px,
        "execTime": str(t),
        "execType": "Trade",
    }
    if fee is not None:
        e["execFee"] = fee
    return e


def _client(tmp: Path, fake: FakeBybitDemo) -> BybitDemoClient:
    cfg = load_demo_config(CFG_PATH, ExecutionMode.EXECUTION_SMOKE)
    j = HashChainJournal(tmp / "journal.jsonl", "smoke")
    return BybitDemoClient(
        cfg,
        cfg.mode,
        j,
        SMOKE_TAG,
        DemoCredentials(fake.api_key, fake.api_secret),
        fake.transport(),
    )


def _smoke(tmp: Path, fake: FakeBybitDemo, **kw: Any) -> tuple[SmokeResult, BybitDemoClient]:
    cl = _client(tmp, fake)
    sleeps: list[float] = kw.pop("sleeps", [])
    res = run_execution_smoke(
        cl, cl.cfg, sleep=sleeps.append, ws_auth=lambda: {"ok": True, "conn_id": "f"}, **kw
    )
    return res, cl


def _step(res: SmokeResult, name: str) -> dict[str, Any]:
    return next(s for s in res.steps if s["step"] == name)


def _kinds(cl: BybitDemoClient) -> list[str]:
    return [r["kind"] for r in cl.journal.records()]


# ---------------------------------------------------------------------------- aggregation


def test_multiple_partial_executions_are_aggregated_by_weighted_price() -> None:
    rows = [
        _x("a", "0.002", "100.0", "0.0011", 3),
        _x("b", "0.001", "103.0", "0.00056", 1),
        _x("c", "0.001", "99.0", "0.00054", 2),
        _x("z", "0.005", "1.0", "9", 4, link="OTHER") | {"orderId": "O9"},  # another order
        {**_x("f", "0.001", "1.0", "0.1", 5), "execType": "Funding"},  # not an order execution
    ]
    a = aggregate_executions(rows, "O1", "L1")
    assert a["n_exec"] == 3 and a["total_qty"] == pytest.approx(0.004)
    assert a["avg_price"] == pytest.approx((0.002 * 100 + 0.001 * 103 + 0.001 * 99) / 0.004)
    assert a["total_fee"] == pytest.approx(0.0011 + 0.00056 + 0.00054)
    assert a["exec_ids"] == ["b", "c", "a"]  # by execTime
    assert (a["first_exec_ms"], a["last_exec_ms"]) == (1, 3)


def test_duplicate_exec_ids_are_counted_once() -> None:
    rows = [_x("a", "0.001", "100", "0.05", 1), _x("a", "0.001", "100", "0.05", 1)]
    a = aggregate_executions([*rows, _x("b", "0.001", "102", "0.05", 2)], "O1", "L1")
    assert a["n_exec"] == 2 and a["duplicates_dropped"] == 1
    assert a["total_qty"] == pytest.approx(0.002) and a["total_fee"] == pytest.approx(0.10)
    ok = confirm_executions(lambda: rows, 0.001, "O1", "L1", 1.0, sleep=lambda s: None)
    assert ok["total_qty"] == pytest.approx(0.001) and ok["total_fee"] == pytest.approx(0.05)


def test_order_filled_before_execution_list_is_populated_then_appears_after_retries() -> None:
    # the real case: Filled 0.001 @ 81775.0 while execution/list is still empty
    calls: list[int] = []
    sleeps: list[float] = []

    def fetch() -> list[dict[str, Any]]:
        calls.append(1)
        if len(calls) < 4:
            return []
        return [_x("e1", "0.001", "81775.0", "0.04497625", 1)]

    a = confirm_executions(fetch, 0.001, "O1", "L1", 20.0, sleep=sleeps.append)
    assert a["attempts"] == 4 and len(calls) == 4 and sleeps == [0.25, 0.5, 1.0]
    assert a["avg_price"] == 81775.0 and a["total_fee"] == pytest.approx(0.04497625)
    assert a["exec_ids"] == ["e1"] and a["source"] == "rest"


def test_partial_visibility_missing_fee_and_qty_mismatch_are_not_definitive() -> None:
    seq = [
        [_x("e1", "0.001", "100", "0.01", 1)],  # only half of the fill visible
        [_x("e1", "0.001", "100", "0.01", 1), _x("e2", "0.001", "101", None, 2)],  # fee missing
        [_x("e1", "0.001", "100", "0.01", 1), _x("e2", "0.001", "101", "0.01", 2)],
    ]
    it = iter(seq)
    a = confirm_executions(lambda: next(it), 0.002, "O1", "L1", 20.0, sleep=lambda s: None)
    assert a["attempts"] == 3 and a["total_fee"] == pytest.approx(0.02)


def test_execution_confirmation_timeout_is_bounded_and_fails_closed() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def fetch() -> list[dict[str, Any]]:
        calls.append(1)
        if len(calls) % 2:
            raise OSError("transient")
        return []

    with pytest.raises(ExecutionConfirmationTimeoutError) as ei:
        confirm_executions(fetch, 0.001, "O1", "L1", 5.0, sleep=sleeps.append)
    assert len(calls) == len(backoff_schedule(5.0)) + 1  # bounded even with a no-op sleep
    assert sum(sleeps) == pytest.approx(5.0)
    assert ei.value.last["reason"] == "no execution visible yet" and ei.value.last["errors"]


def test_latest_definitive_record_is_used_not_the_provisional_one(tmp_path: Path) -> None:
    j = HashChainJournal(tmp_path / "j.jsonl", "x")
    j.append(
        "ORDER_FILLED_PROVISIONAL",
        {"orderLinkId": "L1", "cumExecQty": 0.001, "avgPrice": 81775.0, "cumExecFee": "0"},
        SMOKE_TAG,
    )
    assert latest_confirmed(j.records(), "L1", SMOKE_TAG) is None
    j.append("EXECUTION_CONFIRMED", {"orderLinkId": "L1", "total_fee": 0.04497625}, SMOKE_TAG)
    j.append("EXECUTION_CONFIRMED", {"orderLinkId": "L2", "total_fee": 1.0}, SMOKE_TAG)
    assert latest_confirmed(j.records(), "L1", SMOKE_TAG) == {
        "orderLinkId": "L1",
        "total_fee": 0.04497625,
    }
    assert verify_chain(tmp_path / "j.jsonl")["ok"]  # appended, never rewritten


def test_real_run_numbers_need_both_fees_and_tolerance_cannot_hide_a_fee() -> None:
    tol = load_demo_config(CFG_PATH).smoke.pnl_tolerance_usdt
    gross, bybit = -0.0171, -0.1070431
    entry_fee = 0.04497625
    assert abs(bybit - gross) > tol  # the old own_net (fees 0) must fail reconciliation
    exit_fee = bybit * -1 + gross - entry_fee  # = 0.04496685 implied by Bybit
    assert abs((gross - entry_fee - exit_fee) - bybit) < 1e-12
    assert tol < min(entry_fee, exit_fee) / 10  # a single missing fee is always detected


# ---------------------------------------------------------------------------- smoke end-to-end


def test_smoke_with_delayed_executions_passes_and_journals_provisional_then_confirmed(
    tmp_path: Path,
) -> None:
    fake = FakeBybitDemo(price=81775.0, exec_lag_queries=3, closed_pnl_lag_queries=2)
    res, cl = _smoke(tmp_path, fake)
    assert res.status == "PASSED", [s for s in res.steps if not s["ok"]]
    m, c = _step(res, "market_fill")["detail"], _step(res, "close")["detail"]
    assert m["provisional"]["cumExecQty"] == pytest.approx(0.001)
    assert m["fee"] == pytest.approx(0.001 * 81775.0 * 0.00055) and m["n_exec"] == 1
    assert m["confirm_attempts"] > 1 and c["confirm_attempts"] > 1
    k = _kinds(cl)
    i_prov = k.index("ORDER_FILLED_PROVISIONAL")
    assert k.index("EXECUTION_CONFIRMED") > i_prov and k.count("EXECUTION_CONFIRMED") == 2
    rec = _step(res, "recovery")["detail"]["journal_vs_executions"]
    assert rec["entry"]["match"] and rec["exit"]["match"]
    assert verify_chain(tmp_path / "journal.jsonl")["ok"]


def test_smoke_entry_and_exit_fees_both_in_own_net_and_match_bybit(tmp_path: Path) -> None:
    fake = FakeBybitDemo(price=81775.0, exec_lag_queries=1)
    res, _ = _smoke(tmp_path, fake)
    assert res.status == "PASSED"
    c = _step(res, "closed_pnl")["detail"]
    e, x = _step(res, "market_fill")["detail"], _step(res, "close")["detail"]
    assert c["entry_fees"] == pytest.approx(e["fee"]) and e["fee"] > 0
    assert c["exit_fees"] == pytest.approx(x["fee"]) and x["fee"] > 0
    assert c["own_net"] == pytest.approx(c["own_gross"] - e["fee"] - x["fee"])
    r = _step(res, "reconcile")["detail"]
    assert r["closed_pnl_vs_own_net_diff_usdt"] < 1e-9 and r["tolerance_usdt"] == 0.001
    md = render_smoke(res.as_dict() | {"journal": "j", "journal_chain": {"records": 1, "ok": True}})
    assert "entry fees" in md and "Execution accounting" in md


def test_smoke_multiple_partial_executions_and_duplicate_rows(tmp_path: Path) -> None:
    fake = FakeBybitDemo(price=81775.0, min_qty=0.003, duplicate_exec_rows=True, exec_split_next=3)
    res, _ = _smoke(tmp_path, fake)
    assert res.status == "PASSED", [s for s in res.steps if not s["ok"]]
    m = _step(res, "market_fill")["detail"]
    assert m["n_exec"] == 3 and m["qty"] == pytest.approx(0.003)
    px = [81775.0, 81775.1, 81775.2]
    assert m["avg_price"] == pytest.approx(sum(px) / 3)
    assert m["fee"] == pytest.approx(sum(0.001 * p * 0.00055 for p in px))  # not doubled
    rec = _step(res, "recovery")["detail"]["journal_vs_executions"]["entry"]
    assert rec["match"] and rec["duplicates_dropped"] == 3


def test_smoke_execution_confirmation_timeout_fails_closed_and_cleans_up(tmp_path: Path) -> None:
    fake = FakeBybitDemo(price=81775.0, exec_lag_queries=10**6)
    sleeps: list[float] = []
    res, cl = _smoke(tmp_path, fake, sleeps=sleeps)
    assert res.status == "FAILED" and res.steps[-1]["step"] == "market_fill"
    assert "ExecutionConfirmationTimeoutError" in res.steps[-1]["error"]
    assert "EXECUTION_CONFIRMATION_TIMEOUT" in _kinds(cl)
    assert "EXECUTION_CONFIRMED" not in _kinds(cl)
    assert fake.position["size"] == 0 and any(c["action"] == "close" for c in res.cleanup)
    assert sum(sleeps) <= cl.cfg.smoke.exec_confirm_timeout_s + 1e-9


def test_recovery_fails_if_bybit_executions_differ_from_the_confirmed_record(
    tmp_path: Path,
) -> None:
    fake = FakeBybitDemo(price=81775.0)
    cl = _client(tmp_path, fake)
    orig = cl.wallet_balance
    calls: list[int] = []

    def wallet() -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 2:  # reconcile step, i.e. after the confirmed records were journaled
            entry = next(e for e in fake.executions if e["orderLinkId"].endswith("MKT"))
            fake.executions.append(entry | {"execId": "phantom", "execFee": "0.01"})
        return orig()

    cl.wallet_balance = wallet  # type: ignore[method-assign]
    res = run_execution_smoke(
        cl, cl.cfg, sleep=lambda s: None, ws_auth=lambda: {"ok": True, "conn_id": "f"}
    )
    assert res.status == "FAILED" and res.steps[-1]["step"] == "recovery"
    assert (
        "latest EXECUTION_CONFIRMED and Bybit executions differ for entry" in res.steps[-1]["error"]
    )


def test_wallet_change_uses_usdt_not_total_multi_asset_equity(tmp_path: Path) -> None:
    fake = FakeBybitDemo(price=81775.0, other_assets_drift_per_query=235.0)
    res, _ = _smoke(tmp_path, fake)
    assert res.status == "PASSED"
    r = _step(res, "reconcile")["detail"]
    c = _step(res, "closed_pnl")["detail"]
    assert r["wallet_change_basis"] == "USDT walletBalance"
    assert r["wallet_change"] == pytest.approx(c["bybit_closed_pnl"]) and r["wallet_change"] < 0
    assert abs(r["usdt_wallet_change_minus_closed_pnl"]) < 1e-9
    assert r["total_equity_change_all_assets_info_only"] > 200  # the misleading +470-style figure


# ---------------------------------------------------------------------------- strategy executor


def test_strategy_entry_waits_for_executions_and_journals_confirmed(tmp_path: Path) -> None:
    fake = FakeBybitDemo(exec_lag_queries=2)
    ex = _exec(tmp_path, fake)
    ex.enter(_trig(fake), [])
    p = ex.state["position"]
    assert p["status"] == "ACTIVE" and p["execution_confirmed"]
    assert p["entry_fee"] == pytest.approx(p["filled_qty"] * fake.price * 0.00055)
    kinds = [r["kind"] for r in ex.journal.records()]
    assert kinds.index("ORDER_FILLED_PROVISIONAL") < kinds.index("EXECUTION_CONFIRMED")
    assert not ex.state["reconcile_required"]


def test_strategy_recovery_after_provisional_record_confirms_from_executions(
    tmp_path: Path,
) -> None:
    from tests.v5.test_demo_strategy import Crash

    fake = FakeBybitDemo(exec_lag_queries=10**6)
    ex = _exec(tmp_path, fake)
    import btc_swing.v5.demo.strategy as strat

    real = strat.confirm_executions

    def die(*a: Any, **k: Any) -> Any:
        raise Crash("process died after the provisional fill record")

    strat.confirm_executions = die  # type: ignore[assignment]
    try:
        with pytest.raises(Crash):
            ex.enter(_trig(fake), [])
    finally:
        strat.confirm_executions = real  # type: ignore[assignment]
    kinds = [r["kind"] for r in ex.journal.records()]
    assert kinds[-1] == "ORDER_FILLED_PROVISIONAL" and "EXECUTION_CONFIRMED" not in kinds
    for e in fake.executions:  # executions now visible on the exchange
        e["_hidden"] = 0
    ex2 = _exec(tmp_path, fake)
    out = ex2.recover()
    p = ex2.state["position"]
    assert out["ok"] and p["status"] == "ACTIVE" and p["execution_confirmed"]
    assert p["entry_fee"] > 0 and not ex2.state["reconcile_required"]
    assert [r["kind"] for r in ex2.journal.records()].count("EXECUTION_CONFIRMED") == 1


def test_strategy_confirmation_timeout_protects_position_and_requires_reconciliation(
    tmp_path: Path,
) -> None:
    fake = FakeBybitDemo(exec_lag_queries=10**6)
    ex = _exec(tmp_path, fake)
    ex.enter(_trig(fake), [])
    p = ex.state["position"]
    assert p["status"] == "ACTIVE" and p["execution_confirmed"] is False
    assert ex.state["reconcile_required"] and fake.position["sl"] is not None
    assert any(a["reason"] == "ENTRY_EXECUTION_UNCONFIRMED" for a in ex.state["alerts"])


def test_strategy_finalize_waits_for_exit_executions(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    ex = _exec(tmp_path, fake)
    ex.enter(_trig(fake), [])
    fake.exec_lag_queries = 10**6  # the stop-out executions are not visible yet
    fake.move_price(fake.price * 0.97)
    out = ex.manage(None, fake.now_ms)
    assert out["action"] == "finalize_pending" and ex.state["position"] is not None
    for e in fake.executions:
        e["_hidden"] = 0
    out = ex.manage(None, fake.now_ms)
    assert out["action"] == "closed" and ex.state["position"] is None
    assert out["trade"]["fees"] > 0 and out["trade"]["execution_ids"]


# ---------------------------------------------------------------------------- private WS stream


class _StreamWS:
    def __init__(self, replies: list[dict[str, Any]], auth_ok: bool = True) -> None:
        self.replies, self.auth_ok, self.sent = replies, auth_ok, []

    async def __aenter__(self) -> _StreamWS:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    async def send(self, m: str) -> None:
        d = json.loads(m)
        self.sent.append(d)
        if d.get("op") == "auth":
            self.replies.append({"op": "auth", "success": self.auth_ok, "ret_msg": ""})
        elif d.get("op") == "subscribe":
            self.replies.append({"op": "subscribe", "success": True})

    async def recv(self) -> str:
        while not self.replies:
            await asyncio.sleep(0.01)
        return json.dumps(self.replies.pop(0))


def test_private_execution_stream_is_preferred_and_rest_is_fallback() -> None:
    cfg = load_demo_config(CFG_PATH)
    ws = _StreamWS([])
    st = ExecutionStream(cfg, DemoCredentials("k-1", "s-1"), connect=lambda url, **k: ws)
    status = st.start()
    try:
        assert status["available"] and status["topic"] == "execution.linear"
        assert "s-1" not in json.dumps(status)
        ws.replies.append(
            {"topic": "execution.linear", "data": [_x("w1", "0.001", "81775.0", "0.0449", 1)]}
        )
        rest_calls: list[int] = []

        def rest() -> list[dict[str, Any]]:
            rest_calls.append(1)
            return []

        def rows() -> list[dict[str, Any]] | None:
            return st.rows()

        a = confirm_executions(
            rest,
            0.001,
            "O1",
            "L1",
            5.0,
            sleep=lambda s: __import__("time").sleep(0.05),
            ws_rows=rows,
        )
        assert a["source"] == "ws" and a["exec_ids"] == ["w1"]
    finally:
        st.stop()
    assert st.rows() is None  # stopped stream -> callers fall back to REST
    bad = ExecutionStream(
        cfg,
        DemoCredentials("k-1", "s-1"),
        connect=lambda url, **k: _StreamWS([], False),
        timeout_s=1,
    )
    assert not bad.start()["available"] and bad.rows() is None
    bad.stop()
    a = confirm_executions(
        lambda: [_x("r1", "0.001", "1", "0.1", 1)], 0.001, "O1", "L1", 1.0, ws_rows=bad.rows
    )
    assert a["source"] == "rest"
