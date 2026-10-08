"""STRATEGY_DEMO executor against the in-process fake Bybit DEMO: sizing, one position, duplicate
protection, fail-safes, frozen exit management, paper-vs-demo reconciliation and restart recovery
(before acknowledgement, after acknowledgement, after partial fill, with an open position, after
stop/TP placement, unexpected position)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from btc_swing.v5.config import load_v5_config
from btc_swing.v5.demo.client import BybitDemoClient
from btc_swing.v5.demo.config import SMOKE_TAG, STRATEGY_TAG, ExecutionMode, load_demo_config
from btc_swing.v5.demo.credentials import DemoCredentials
from btc_swing.v5.demo.ids import strategy_link_id
from btc_swing.v5.demo.journal import HashChainJournal, verify_chain
from btc_swing.v5.demo.strategy import StrategyDemoExecutor, Trigger
from tests.v5.fake_bybit import FakeBybitDemo

ROOT = Path(__file__).resolve().parents[2]
V5 = load_v5_config(ROOT / "config" / "btc_swing_v5.yaml")


class Crash(BaseException):
    """Simulated process death (not catchable by the executor's error handling)."""


def _dcfg(ref_equity: float = 5000.0) -> Any:
    d = load_demo_config(ROOT / "config" / "btc_swing_v5_demo.yaml", ExecutionMode.STRATEGY_DEMO)
    return d.model_copy(update={"reference_equity_usdt": ref_equity})


def _exec(
    tmp: Path, fake: FakeBybitDemo, ref_equity: float = 5000.0, activated_at_ms: int | None = 0
) -> StrategyDemoExecutor:
    dcfg = _dcfg(ref_equity)
    j = HashChainJournal(tmp / "strategy_journal.jsonl", "strategy_demo", forbid_tags=(SMOKE_TAG,))
    cl = BybitDemoClient(
        dcfg,
        dcfg.mode,
        j,
        STRATEGY_TAG,
        DemoCredentials(fake.api_key, fake.api_secret),
        fake.transport(),
        clock=lambda: fake.now_ms / 1000,
    )
    return StrategyDemoExecutor(
        V5,
        dcfg,
        cl,
        j,
        tmp / "state.json",
        tmp / "demo_trades.jsonl",
        tmp / "signals.jsonl",
        tmp / "paper_trades.jsonl",
        {"status": "PASSED"},
        clock=lambda: fake.now_ms / 1000,
        sleep=lambda s: None,
        activated_at_ms=activated_at_ms,
    )


def _trig(
    fake: FakeBybitDemo, side: str = "LONG", event_offset: int = 0, stop_frac: float = 0.014
) -> Trigger:
    s = 1.0 if side == "LONG" else -1.0
    return Trigger(
        t_ms=fake.now_ms - 1000,
        family="FLOW_OI_CONTINUATION",
        side=side,
        event_ms=fake.now_ms - 3_600_000 + event_offset,
        trigger_close=fake.price,
        stop=round(fake.price * (1 - s * stop_frac), 1),
        atr_setup=fake.price * 0.01,
        atr_liq=math.nan,
    )


def _entry_orders(fake: FakeBybitDemo, trig: Trigger) -> list[dict[str, Any]]:
    return [
        o
        for o in fake.orders.values()
        if o["orderLinkId"] == strategy_link_id(trig.signal_id, "EN")
    ]


def test_requires_strategy_mode_and_passed_smoke(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    dcfg = _dcfg()
    j = HashChainJournal(tmp_path / "j.jsonl", "s", forbid_tags=(SMOKE_TAG,))
    cl = BybitDemoClient(
        dcfg,
        dcfg.mode,
        j,
        STRATEGY_TAG,
        DemoCredentials(fake.api_key, fake.api_secret),
        fake.transport(),
    )
    with pytest.raises(RuntimeError, match="PASSED EXECUTION_SMOKE"):
        StrategyDemoExecutor(
            V5,
            dcfg,
            cl,
            j,
            tmp_path / "s.json",
            tmp_path / "t.jsonl",
            tmp_path / "x",
            tmp_path / "y",
            {"status": "FAILED"},
        )
    with pytest.raises(RuntimeError, match="STRATEGY_DEMO"):
        StrategyDemoExecutor(
            V5,
            dcfg.model_copy(update={"mode": ExecutionMode.EXECUTION_SMOKE}),
            cl,
            j,
            tmp_path / "s.json",
            tmp_path / "t.jsonl",
            tmp_path / "x",
            tmp_path / "y",
            {"status": "PASSED"},
        )


def test_reference_equity_100_is_below_exchange_minimum_and_never_rounded_up(
    tmp_path: Path,
) -> None:
    fake = FakeBybitDemo()
    ex = _exec(tmp_path, fake, ref_equity=100.0)
    out = ex.enter(_trig(fake), [])
    assert out["action"] == "SKIPPED_BELOW_MIN_QTY"
    assert (
        out["sizing"]["risk_amount"] == pytest.approx(0.25)
        and float(out["sizing"]["qty"]) < fake.min_qty
    )
    assert out["sizing"]["reference_equity_needed_for_min_qty"] == pytest.approx(
        fake.min_qty * 840.0 / 0.0025, rel=1e-3
    )
    assert not [o for o in fake.orders.values()]  # no order of any kind
    assert ex.state["position"] is None


def test_entry_sizing_stop_and_targets_follow_the_frozen_rules(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    ex = _exec(tmp_path, fake)
    t = _trig(fake)
    out = ex.enter(t, [])
    assert out["action"] == "ENTERED"
    p = ex.state["position"]
    assert p["status"] == "ACTIVE" and p["planned_risk_usdt"] == pytest.approx(5000 * 0.0025)
    # frozen size_position on the reference equity: risk / stop distance, floored to the step
    assert p["filled_qty"] == pytest.approx(
        math.floor(12.5 / (fake.price - t.stop) / 0.001) * 0.001
    )
    assert fake.position["sl"] == pytest.approx(t.stop)
    r = p["entry_price"] - t.stop
    legs = p["legs"]
    assert float(legs["T1"]["price"]) == pytest.approx(
        p["entry_price"] + 1.0 * r, abs=0.1
    ) and float(legs["T2"]["price"]) == pytest.approx(p["entry_price"] + 2.0 * r, abs=0.1)
    assert float(legs["T1"]["qty"]) == pytest.approx(
        math.floor(p["filled_qty"] * 0.4 / 0.001) * 0.001
    )
    assert len(_entry_orders(fake, t)) == 1 and fake.hosts() == {"api-demo.bybit.com"}
    assert verify_chain(tmp_path / "strategy_journal.jsonl")["ok"]
    text = (tmp_path / "strategy_journal.jsonl").read_text()
    assert fake.api_secret not in text and fake.api_key not in text


def test_one_position_duplicates_and_failsafes(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    ex = _exec(tmp_path, fake)
    t1 = _trig(fake)
    ex.enter(t1, [])
    t2 = _trig(fake, event_offset=300_000)
    out = ex.step([t2], [], None, fake.now_ms)
    assert (
        out["entries"][0]["action"] == "BLOCKED"
        and "POSITION_OPEN" in out["entries"][0]["blockers"]
    )
    assert not _entry_orders(fake, t2)
    # same signal again -> duplicate, no order
    assert ex.enter(t1, [])["action"] == "SKIPPED_DUPLICATE_SIGNAL"
    assert len(_entry_orders(fake, t1)) == 1
    # fail-safe blocks a new trade but the open position keeps being managed
    fake.move_price(float(ex.state["position"]["legs"]["T1"]["price"]) + 1.0)  # TP1 fills
    t3 = _trig(fake, event_offset=600_000)
    out = ex.step([t3], ["COLLECTOR_STALE"], None, fake.now_ms)
    assert out["entries"][0]["action"] == "BLOCKED" and not _entry_orders(fake, t3)
    p = ex.state["position"]
    assert (
        p["tp1_done"]
        and p["breakeven_done"]
        and fake.position["sl"] == pytest.approx(round(p["entry_price"], 1), abs=0.1)
    )


def test_management_trail_time_cap_and_paper_reconciliation(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    ex = _exec(tmp_path, fake)
    t = _trig(fake)
    ex.enter(t, [])
    p = ex.state["position"]
    paper = {
        "family": t.family,
        "side": t.side,
        "entry_ms": t.t_ms,
        "entry_price": fake.price * 1.0002,
        "avg_exit_price": fake.price * 1.005,
        "exit_reason": "TIME_LIMIT",
        "fees": 10.0,
        "slippage": 2.0,
        "funding": 0.0,
        "POSITION_PNL": 30.0,
        "R_MULTIPLE": 1.2,
        "R_MULTIPLE_GROSS": 1.6,
        "risk_amount": 25.0,
    }
    (tmp_path / "paper_trades.jsonl").write_text(json.dumps(paper) + "\n")
    fake.move_price(float(p["legs"]["T1"]["price"]) + 1.0)
    ex.manage(
        trail_candidate=p["entry_price"] + 100.0, now_ms=fake.now_ms
    )  # TP1 -> breakeven, trail stored for next bar
    assert ex.state["position"]["stop"] == pytest.approx(p["entry_price"]) and ex.state["position"][
        "pending_trail"
    ] == pytest.approx(p["entry_price"] + 100.0)
    ex.manage(
        trail_candidate=None, now_ms=fake.now_ms + 300_000
    )  # applied one cycle later (frozen: from the next bar)
    assert ex.state["position"]["stop"] == pytest.approx(
        p["entry_price"] + 100.0
    ) and fake.position["sl"] == pytest.approx(round(p["entry_price"] + 100.0, 1), abs=0.1)
    fake.now_ms = int(p["fill_ms"]) + 24 * 3_600_000 + 1
    out = ex.manage(trail_candidate=None, now_ms=fake.now_ms)
    assert out["action"] == "closed" and fake.position["size"] == 0 and ex.state["position"] is None
    tr = json.loads((tmp_path / "demo_trades.jsonl").read_text().splitlines()[-1])
    cmp_ = tr["paper_comparison"]
    assert cmp_["paper_found"] and set(cmp_["diff"]) == {
        "entry_fill_diff_bps",
        "fee_diff_R",
        "R_diff",
        "pnl_diff_scaled_usdt",
    }
    assert (
        tr["fees"] > 0
        and tr["R_net_vs_actual"] is not None
        and abs(tr["bybit_closed_pnl_sum"] - tr["net_pnl"]) < 0.05
    )
    assert {e["link"] for e in tr["exits"]} >= {
        strategy_link_id(t.signal_id, "T1"),
        strategy_link_id(t.signal_id, "TC"),
    }


def test_stop_out_is_detected_and_finalised(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    ex = _exec(tmp_path, fake)
    t = _trig(fake, side="SHORT")
    ex.enter(t, [])
    fake.move_price(t.stop + 5.0)
    out = ex.manage(None, fake.now_ms)
    assert out["action"] == "closed"
    tr = json.loads((tmp_path / "demo_trades.jsonl").read_text().splitlines()[-1])
    assert tr["net_pnl"] < 0 and tr["R_net_vs_actual"] == pytest.approx(-1.0, abs=0.15)
    assert ex.state["demo_closed_trades"] == 1


# ----------------------------------------------------------------------------- restart recovery
def test_recovery_crash_before_send_never_resubmits(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    ex = _exec(tmp_path, fake)
    t = _trig(fake)

    def boom(*a: Any, **k: Any) -> Any:
        raise Crash("process died before the request left")

    ex.client.create_order = boom  # type: ignore[method-assign]
    with pytest.raises(Crash):
        ex.enter(t, [])
    assert json.loads((tmp_path / "state.json").read_text())["position"]["status"] == "SUBMITTING"
    ex2 = _exec(tmp_path, fake)  # restart
    out = ex2.recover()
    assert out["ok"] and ex2.state["position"] is None and not fake.orders
    assert ex2.enter(t, [])["action"] == "SKIPPED_DUPLICATE_SIGNAL" and not fake.orders


def test_recovery_ack_lost_in_transit_adopts_the_order(tmp_path: Path) -> None:
    fake = FakeBybitDemo(drop_ack_next_create=True)
    ex = _exec(tmp_path, fake)
    t = _trig(fake)
    out = ex.enter(t, [])
    assert out["action"] == "ENTERED" and ex.state["position"]["status"] == "ACTIVE"
    assert len(_entry_orders(fake, t)) == 1


def test_recovery_crash_right_after_send_before_acknowledgement(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    ex = _exec(tmp_path, fake)
    t = _trig(fake)
    real = ex.client.create_order

    def send_then_die(*a: Any, **k: Any) -> Any:
        real(*a, **k)
        raise Crash("process died before the acknowledgement was processed")

    ex.client.create_order = send_then_die  # type: ignore[method-assign]
    with pytest.raises(Crash):
        ex.enter(t, [])
    ex2 = _exec(tmp_path, fake)
    ex2.recover()
    p = ex2.state["position"]
    assert (
        p["status"] == "ACTIVE"
        and len(_entry_orders(fake, t)) == 1
        and fake.position["sl"] == pytest.approx(t.stop)
    )


def test_recovery_after_acknowledgement_before_fill_resolution(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    ex = _exec(tmp_path, fake)
    t = _trig(fake)

    def die(*a: Any, **k: Any) -> Any:
        raise Crash("process died after the acknowledgement")

    ex._resolve_fill = die  # type: ignore[method-assign]
    with pytest.raises(Crash):
        ex.enter(t, [])
    assert json.loads((tmp_path / "state.json").read_text())["position"]["status"] == "ACKED"
    ex2 = _exec(tmp_path, fake)
    ex2.recover()
    assert ex2.state["position"]["status"] == "ACTIVE" and len(_entry_orders(fake, t)) == 1
    assert (
        fake.position["sl"] is not None
        and len([o for o in fake.orders.values() if o["reduceOnly"]]) == 2
    )


def test_recovery_after_partial_fill(tmp_path: Path) -> None:
    fake = FakeBybitDemo(partial_fill_frac=0.5)
    ex = _exec(tmp_path, fake)
    t = _trig(fake)
    ex.enter(t, [])
    p = ex.state["position"]
    assert (
        p["partial"]
        and p["filled_qty"] == pytest.approx(fake.position["size"])
        and p["filled_qty"] < p["planned_qty"]
    )
    assert float(p["legs"]["T1"]["qty"]) <= p["filled_qty"] * 0.4 + 1e-9
    n_orders = len(fake.orders)
    ex2 = _exec(tmp_path, fake)
    out = ex2.recover()
    assert out["ok"] and not ex2.state["reconcile_required"] and len(fake.orders) == n_orders


def test_recovery_with_open_position_and_after_stop_tp_placement(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    ex = _exec(tmp_path, fake)
    t = _trig(fake)
    real = ex.client.create_order

    def die_on_t2(side: str, qty: str, order_type: str, link: str, **k: Any) -> Any:
        if link == strategy_link_id(t.signal_id, "T2"):
            raise Crash("process died after the stop and TP1 were placed")
        return real(side, qty, order_type, link, **k)

    ex.client.create_order = die_on_t2  # type: ignore[method-assign,assignment]
    with pytest.raises(Crash):
        ex.enter(t, [])
    assert (
        fake.position["sl"] is not None
        and strategy_link_id(t.signal_id, "T1") in fake.orders
        and strategy_link_id(t.signal_id, "T2") not in fake.orders
    )
    ex2 = _exec(tmp_path, fake)
    ex2.recover()  # places T2 once; T1 and the entry are not duplicated
    links = [o["orderLinkId"] for o in fake.orders.values()]
    assert (
        links.count(strategy_link_id(t.signal_id, "T2")) == 1
        and links.count(strategy_link_id(t.signal_id, "T1")) == 1
        and len(_entry_orders(fake, t)) == 1
    )
    n = len(fake.orders)
    ex3 = _exec(tmp_path, fake)  # restart again with a fully protected open position
    ex3.recover()
    assert (
        len(fake.orders) == n
        and ex3.state["position"]["status"] == "ACTIVE"
        and not ex3.state["reconcile_required"]
    )


def test_recovery_unexpected_position_requires_reconciliation(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    fake.position.update({"side": "Sell", "size": 0.01, "avg": 61000.0})
    ex = _exec(tmp_path, fake)
    ex.recover()
    assert ex.state["reconcile_required"]
    out = ex.step([_trig(fake)], [], None, fake.now_ms)
    assert (
        out["entries"][0]["action"] == "BLOCKED"
        and "RECONCILIATION_REQUIRED" in out["entries"][0]["blockers"]
    )
    assert fake.position["size"] == 0.01 and not fake.orders  # unknown position never touched
    ex.acknowledge_reconciliation("test: position closed manually")
    assert not ex.state["reconcile_required"]


def test_capturing_engine_is_the_frozen_engine(synthetic_data: dict[str, object]) -> None:
    import polars as pl

    from btc_swing.features.context import AuxSeries
    from btc_swing.features.view import MultiTfSeries
    from btc_swing.v5.demo.strategy import CapturingEngine
    from btc_swing.v5.engine import V5Engine
    from btc_swing.v5.features import V5Inputs, build_feature_frame
    from tests.v5.test_v5 import _ms, _synthetic_flow, _synthetic_metrics

    bars, funding = synthetic_data["bars"], synthetic_data["funding"]
    assert isinstance(bars, pl.DataFrame) and isinstance(funding, pl.DataFrame)
    aux = AuxSeries.build(funding, _synthetic_metrics(bars), None, None, 0)
    series = MultiTfSeries(bars, V5.indicators, 5)
    ff = build_feature_frame(
        series, aux, V5, V5Inputs(_synthetic_flow(bars), pl.DataFrame(), pl.DataFrame())
    )
    s, e = _ms(2023, 3, 1), _ms(2023, 6, 1)
    ref = V5Engine(V5, bars, funding, None, aux, series, ff).run(s, e)
    cap = CapturingEngine(V5, bars, funding, None, aux, series, ff)
    got = cap.run(s, e)
    assert got.result_hash == ref.result_hash  # identical decisions, trades and episodes
    assert ref.trades.height > 0 and len(cap.captured) >= ref.trades.height
    trig_keys = {(c["family"], c["side"], c["t_ms"]) for c in cap.captured}
    # the paper fill bar opens exactly when the trigger bar closes: the demo/paper matching key
    for r in ref.trades.iter_rows(named=True):
        assert (r["family"], r["side"], int(r["entry_ms"])) in trig_keys
