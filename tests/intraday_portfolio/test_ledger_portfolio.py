"""Ledger and portfolio invariants: no spread double-counting, funding never silently zero, quote touch
is not a fill, one-position portfolio, deterministic metrics / gates."""

from __future__ import annotations

import math

import numpy as np

from btc_swing.research.intraday_portfolio.contract import MS_1M, EventTimes
from btc_swing.research.intraday_portfolio.episodes import Exit, ParentEpisode
from btc_swing.research.intraday_portfolio.ledger import (
    TAKER_FEE_BPS,
    CostBreakdown,
    benchmark_net_bps,
    executable_net_bps,
    funding_bps,
    gross_bps,
    passive_entry_bounded,
    risk_usdt,
)
from btc_swing.research.intraday_portfolio.metrics import (
    cluster_lower_bound,
    funnel,
    gates,
    summarize,
)
from btc_swing.research.intraday_portfolio.portfolio import simulate


def test_executable_ledger_never_subtracts_spread_twice() -> None:
    mid_in, mid_out, spread = 10_000.0, 10_050.0, 2.0  # 2 USDT = 2 bps at 10k
    ask_in, bid_out = mid_in + spread / 2, mid_out - spread / 2
    g_mid = gross_bps(1, mid_in, mid_out)
    # benchmark: gross at mid minus a scenario that CONTAINS the spread (commission 11 + spread 2)
    bench = benchmark_net_bps(g_mid, 2 * TAKER_FEE_BPS + 2.0, 0.0)
    # executable: fills already include the spread -> commission only
    exe = executable_net_bps(1, ask_in, bid_out, 0.0)
    assert math.isclose(bench, exe, abs_tol=0.02)
    assert exe < g_mid - 2 * TAKER_FEE_BPS  # the spread was paid exactly once


def test_cost_breakdown_sums_to_scenario() -> None:
    cb = CostBreakdown.taker_taker(16.0)
    assert math.isclose(cb.commission_bps, 11.0)
    assert math.isclose(cb.commission_bps + cb.spread_bps + cb.shortfall_bps + cb.latency_bps, 16.0)


def test_funding_missing_is_nan_not_zero_and_sign_correct() -> None:
    assert math.isnan(funding_bps(1, 0, 10, np.zeros(0, dtype=np.int64), np.zeros(0)))
    t = np.array([100, 200, 300], dtype=np.int64)
    r = np.array([0.0001, 0.0002, -0.0001])
    assert funding_bps(1, 150, 250, t, r) == 2.0  # long pays the 0.0002 at 200
    assert funding_bps(-1, 150, 250, t, r) == -2.0  # short receives it
    assert funding_bps(1, 201, 250, t, r) == 0.0  # none spanned -> 0.0 (data present)


def test_passive_entry_quote_touch_is_not_a_fill() -> None:
    r = passive_entry_bounded(1, 100.0, False)
    assert r.status == "UNFILLED" and r.fill_frac == 0.0
    r2 = passive_entry_bounded(1, 100.0, True)
    assert r2.status == "BOUNDED_MODEL_DEPENDENT" and r2.fill_frac == 1.0
    r3 = passive_entry_bounded(1, None, None)
    assert r3.status == "BOUNDED_MODEL_DEPENDENT" and r3.fill_price is None
    assert risk_usdt(2.0) == 10.0 and risk_usdt(None) is None


def _parent(
    pid: str, engine: str, side: str, t0: int, hold_min: int, net_bps: float, stop_bps: float = 40.0
) -> ParentEpisode:
    p = ParentEpisode(
        parent_id=pid,
        engine=engine,
        labels=[engine, side],
        side=side,
        first_setup_time_ms=t0,
        receipt_time_ms=None,
        times=EventTimes(t0, None, None, t0, t0, None),
        frozen_levels={"atr_setup": 40.0},
        regime="RANGE",
        er=0.1,
    )
    p.entry_benchmark, p.stop_bps, p.stop = 10_000.0, stop_bps, 10_000.0 * (1 - stop_bps / 1e4)
    p.exit = Exit("TARGET", t0 + hold_min * MS_1M, 10_000.0 * (1 + net_bps / 1e4))
    p.gross_bps, p.cost_bps = net_bps + 16.0, 16.0
    p.net_bps, p.net_r, p.hold_min = net_bps, net_bps / stop_bps, float(hold_min)
    p.mfe_bps, p.mae_bps = max(net_bps, 0.0), min(net_bps, 0.0)
    p.eligibility_state = "CLOSED"
    return p


def test_portfolio_one_position_occupancy_and_opposite_abstain() -> None:
    t0 = 1_700_000_000_000
    a = _parent("A", "E1", "LONG", t0, 60, 30.0)
    b = _parent("B", "E2", "LONG", t0 + 10 * MS_1M, 30, 20.0)  # while A is open -> OCCUPIED
    c = _parent("C", "E2", "SHORT", t0 + 60 * MS_1M, 30, 20.0)  # A closed exactly now -> free
    d1 = _parent("D1", "E1", "LONG", t0 + 200 * MS_1M, 30, 20.0)
    d2 = _parent("D2", "E2", "SHORT", t0 + 200 * MS_1M, 30, 20.0)  # opposite, same time -> abstain
    r = _parent("R", "E1", "LONG", t0 + 300 * MS_1M, 30, 20.0)
    r.eligibility_state, r.exit, r.net_bps = "REJECTED", None, None
    res = simulate("E1+E2", [r, d2, d1, c, b, a])  # unsorted input, deterministic output
    st = {x.parent_id: x.status for x in res.rows}
    assert st == {
        "A": "TRADED",
        "B": "OCCUPIED",
        "C": "TRADED",
        "D1": "ABSTAIN_OPPOSITE",
        "D2": "ABSTAIN_OPPOSITE",
        "R": "NOT_ELIGIBLE",
    }
    assert next(x for x in res.rows if x.parent_id == "B").blocked_by == "A"
    assert len(res.trades) == 2 and res.occupancy_loss() == 1
    assert simulate("again", [a, b, c, d1, d2, r]).rows == res.rows


def test_metrics_gates_and_cluster_bound() -> None:
    t0 = 1_700_000_000_000
    ps = [
        _parent(f"P{k}", "E2", "LONG", t0 + k * 86_400_000, 30, 30.0 if k % 2 else -10.0)
        for k in range(40)
    ]
    s = summarize(ps, 16.0)
    assert s["n"] == 40 and math.isclose(s["net_bps_mean"], 10.0)
    assert math.isclose(s["win_rate"], 0.5) and s["profit_factor"] == 3.0
    assert math.isclose(s["net_usdt"], 5.0 * sum(p.net_r for p in ps if p.net_r is not None))
    lb = cluster_lower_bound([float(x) for x in range(10)], [str(k) for k in range(10)])
    assert lb is not None and lb < 4.5
    g = gates(ps, "2023-11-01", "2024-01-09")
    assert g["verdict"] == "INCONCLUSIVE"  # 40 parents < 300 even though economics pass
    assert g["checks"]["sample_ge_300_parents"] is False
    fn = funnel([*ps, ps[0]], "2023-11-01", "2024-01-09")
    assert fn["completed_trades"] == 41 and fn["calendar_days"] == 70
    assert gates([], None, None)["verdict"] == "INCONCLUSIVE"
