from __future__ import annotations

import math

import numpy as np
import polars as pl

from btc_swing.core.enums import Side
from btc_swing.v61.config import V61Family
from btc_swing.v61.engine import Costs61, V61Engine
from btc_swing.v61.families import Episode, LiquiditySweepReversal, Trigger61, build_detectors61
from btc_swing.v61.features import build_frame_v61
from tests.v61.helpers import cfg, synthetic_inputs


def _frame(seed: int = 21, n_days: int = 60):
    c = cfg()
    inp = synthetic_inputs(n_days=n_days, seed=seed)
    return c, inp, build_frame_v61(inp, c)


def test_sweep_reversal_event_is_location_first() -> None:
    c = cfg()
    n = 40 * 288
    rng = np.random.RandomState(9)
    price = 60_000 + np.cumsum(rng.normal(0, 8, n))
    k0 = 34 * 288 + 20
    ref = price[k0 - 48 : k0 - 6].min() - 60.0  # make sure the reference is the structural low
    price[k0 - 30] = ref + 60.0  # (lows carry a spread in the fixture; keep the structure simple)
    inp = synthetic_inputs(n_days=40, price=price)
    fr = build_frame_v61(inp, c)
    # force a sweep bar at k0: low below the reference, close back above it
    reference = min(np.min(fr.cols["low"][k0 - 48 : k0 - 6]), fr.cols["prev_day_lo"][k0])
    fr.cols["low"][k0] = reference - 0.3 * fr.cols["atr15m"][k0]
    fr.cols["close"][k0] = reference + 0.2 * fr.cols["atr15m"][k0]
    fr.cols["low"][k0 - 6 : k0] = np.maximum(fr.cols["low"][k0 - 6 : k0], reference + 1.0)
    det = LiquiditySweepReversal(c, fr, Side.LONG, flow_on=False)
    assert det.events[k0]
    assert math.isclose(det.refs["reference"][k0], reference)
    ep = det.open_episode(k0, 1)
    fr.cols["close"][k0 + 1] = fr.cols["high"][k0] + 1.0  # close above the sweep bar's high
    fr.cols["high"][k0 + 1] = fr.cols["close"][k0 + 1] + 5.0
    trg = det.step(ep, k0 + 1)
    assert trg is not None and trg.reason == "SWEEP_RECLAIM"
    assert trg.structural_stop < fr.cols["low"][k0]
    assert trg.target > trg.ref_close


def test_flow_switch_only_gates_the_trigger() -> None:
    c, _inp, fr = _frame(seed=4, n_days=60)
    dets_on = build_detectors61(c, fr, flow=True)
    dets_off = build_detectors61(c, fr, flow=False)
    for a, b in zip(dets_on, dets_off, strict=True):
        assert np.array_equal(
            a.events, b.events
        )  # Stage A is identical; flow only confirms Stage B
        assert a.flow_on and not b.flow_on
    mixed = build_detectors61(c, fr, flow={V61Family.TREND_PULLBACK_RECLAIM: False})
    assert not mixed[0].flow_on and mixed[2].flow_on


def test_costs_round_trip_and_maker_entry() -> None:
    c = cfg()
    k = Costs61.from_cfg(c)
    assert math.isclose(k.round_trip_bps(False), 5.5 + 1.0 + 5.5 + 3.0, abs_tol=0.05)  # + spread
    assert math.isclose(k.round_trip_bps(True), 2.0 + 5.5 + 3.0, abs_tol=0.05)
    assert k.taker_entry_fill(100_000.0, Side.LONG) > 100_000.0


def test_engine_determinism_accounting_and_cost_share_refusals() -> None:
    c, inp, fr = _frame(seed=21, n_days=70)
    start, end = int(fr.close_ms[45 * 288]), int(fr.close_ms[-1]) + 1
    r1 = V61Engine(c, fr, inp.funding, cost_share_max=0.0).run(start, end)
    r2 = V61Engine(c, fr, inp.funding, cost_share_max=0.0).run(start, end)
    assert r1.result_hash == r2.result_hash
    t = r1.trades
    assert t.height > 0
    lhs = t["gross_pnl"] - t["slippage_cost"] - t["fees"] + t["funding"]
    assert np.allclose(lhs.to_numpy(), t["net_pnl"].to_numpy(), atol=1e-9)
    assert (t["hold_min"] <= c.exits.max_hold_hours * 60 + 5).all()
    assert np.allclose(t["risk_amount"].to_numpy(), c.risk.initial_equity * c.risk.risk_per_trade)
    assert t["leverage"].is_in([1, 2, 3, 5, 10]).all()
    # the eligibility rule refuses high cost shares and never increases trades
    r3 = V61Engine(c, fr, inp.funding, cost_share_max=0.15).run(start, end)
    assert r3.trades.height <= r1.trades.height
    if r3.refused.height:
        assert "COST_SHARE_TOO_HIGH" in set(r3.refused["reason"].unique())
    assert (r3.trades["cost_share_stop"] <= 0.15 + 1e-9).all() if r3.trades.height else True
    # every trigger is recorded with its cost shares
    assert r1.triggers.height >= r1.trades.height
    assert {"cost_share_stop", "stop_bps", "cost_bps"} <= set(r1.triggers.columns)


def test_maker_fill_model() -> None:
    c, inp, fr = _frame(seed=8, n_days=60)
    start, end = int(fr.close_ms[45 * 288]), int(fr.close_ms[-1]) + 1
    tk = V61Engine(c, fr, inp.funding, entry_mode="TAKER", cost_share_max=0.0).run(start, end)
    mk = V61Engine(c, fr, inp.funding, entry_mode="MAKER", cost_share_max=0.0).run(start, end)
    mf = V61Engine(c, fr, inp.funding, entry_mode="MAKER_FIRST", cost_share_max=0.0).run(start, end)
    assert tk.trades.height >= mk.trades.height  # unfilled limits are missed trades
    if mk.trades.height:
        assert mk.trades["entry_maker"].all()
        # a maker fill happens at the limit (the trigger close) with the maker fee
        row = mk.trades.row(0, named=True)
        assert math.isclose(row["entry_fill"], row["entry_ref"])
        k = fr.idx_at(row["trigger_ms"])
        assert math.isclose(row["entry_fill"], fr.cols["close"][k])
    unfilled = int((mk.refused["reason"] == "MAKER_UNFILLED").sum()) if mk.refused.height else 0
    assert mk.trades.height + unfilled <= tk.trades.height + len(
        tk.refused
    )  # misses never create trades
    if mf.trades.height:
        assert set(mf.trades["entry_maker"].unique()) <= {True, False}


def test_time_stop_and_structural_target_variants() -> None:
    c, inp, fr = _frame(seed=13, n_days=60)
    start, end = int(fr.close_ms[45 * 288]), int(fr.close_ms[-1]) + 1
    short_ts = V61Engine(c, fr, inp.funding, time_stop_hours=2, cost_share_max=0.0).run(start, end)
    if short_ts.trades.height:
        ts = short_ts.trades.filter(pl.col("exit_reason") == "TIME_STOP")
        assert (ts["hold_min"] >= 120 - 1).all() if ts.height else True
        assert (ts["mfe_r"] < c.exits.time_stop_min_mfe_r + 1e-9).all() if ts.height else True
    s_var = V61Engine(c, fr, inp.funding, variant="S", cost_share_max=0.0).run(start, end)
    if s_var.trades.height:
        assert set(s_var.trades["exit_reason"].unique()) <= {
            "TARGET",
            "STOP",
            "BREAKEVEN",
            "TRAIL",
            "TIME_STOP",
            "TIME_LIMIT",
            "END_OF_DATA",
            "LIQUIDATION",
        }
        assert s_var.trades["target"].is_not_nan().all()


def test_simulated_stop_loss_is_one_r_plus_costs() -> None:
    c, inp, fr = _frame()
    eng = V61Engine(c, fr, inp.funding)
    k = 50 * 288 + 10
    ref = float(fr.cols["close"][k])
    a15 = float(fr.cols["atr15m"][k])
    stop_struct = ref - 1.5 * a15
    ep = Episode(1, V61Family.TREND_PULLBACK_RECLAIM, Side.LONG, k, int(fr.close_ms[k]), 1.0, {})
    fr.cols["low"][k + 1] = stop_struct - 50.0
    pos = eng._simulate(
        ep, Trigger61(ref, stop_struct, ref + 3 * a15, "TEST", True), k, 1, c.risk.initial_equity
    )
    assert pos is not None and pos.exits[-1].reason == "STOP"
    row = pos.row()
    assert -1.15 < row["gross_r"] < -0.85
    assert row["net_r"] < -1.0
    assert (
        ep.state.value == "WATCH" and ep.trade_id == -1
    )  # the simulation never touches the live episode
