from __future__ import annotations

import math

import numpy as np
import polars as pl

from btc_swing.core.enums import Side
from btc_swing.v6.config import V6Family
from btc_swing.v6.engine import Costs, V6Engine
from btc_swing.v6.families import Episode, Trigger
from btc_swing.v6.features import build_frame
from tests.v6.helpers import cfg, synthetic_inputs


def _frame(seed: int = 21, n_days: int = 45):
    c = cfg()
    inp = synthetic_inputs(n_days=n_days, seed=seed)
    return c, inp, build_frame(inp, c)


def test_costs_model() -> None:
    c = cfg()
    k = Costs.from_cfg(c)
    assert k.entry_fill(100_000.0, Side.LONG) > 100_000.0 > k.entry_fill(100_000.0, Side.SHORT)
    assert k.stop_fill(100_000.0, Side.LONG) < 100_000.0 < k.stop_fill(100_000.0, Side.SHORT)
    assert math.isclose(k.fee(10_000.0), 5.5)
    assert math.isclose(k.entry_fill(100_000.0, Side.LONG), 100_000 * (1 + 1e-4) + 0.05)


def test_determinism_and_accounting_identity() -> None:
    c, inp, fr = _frame()
    start, end = int(fr.close_ms[30 * 288]), int(fr.close_ms[-1]) + 1
    r1 = V6Engine(c, fr, inp.funding).run(start, end)
    r2 = V6Engine(c, fr, inp.funding).run(start, end)
    assert r1.result_hash == r2.result_hash
    t = r1.trades
    if t.height:
        lhs = t["gross_pnl"] - t["slippage_cost"] - t["fees"] + t["funding"]
        assert np.allclose(lhs.to_numpy(), t["net_pnl"].to_numpy(), atol=1e-9)
        rr = t["gross_r"] + t["slip_r"] + t["fee_r"] + t["fund_r"]
        assert np.allclose(rr.to_numpy(), t["net_r"].to_numpy(), atol=1e-9)
        assert (t["hold_min"] <= c.exits.max_hold_hours * 60 + 5).all()
        assert (t["stop_bps"] > 0).all()
        assert t["leverage"].is_in([1, 2, 3, 5, 10]).all()
        # leverage never changes the loss at the stop: risk_amount is the configured fraction
        assert np.allclose(
            t["risk_amount"].to_numpy(), c.risk.initial_equity * c.risk.risk_per_trade
        )


def test_simulated_stop_loss_costs_about_one_r_plus_costs() -> None:
    c, inp, fr = _frame()
    eng = V6Engine(c, fr, inp.funding)
    k = 32 * 288 + 10
    ref = float(fr.cols["close"][k])
    a15 = float(fr.cols["atr15m"][k])
    # force a stop just under the next bar's low: the trade must stop out on bar k+1
    stop_struct = ref - 1.5 * a15
    ep = Episode(1, V6Family.MOMENTUM_CONTINUATION, Side.LONG, k, int(fr.close_ms[k]), 1.0, {})
    fr.cols["low"][k + 1] = stop_struct - 50.0
    pos = eng._simulate(ep, Trigger(ref, stop_struct, "TEST"), k, 1, c.risk.initial_equity)
    assert pos is not None and pos.exits[-1].reason == "STOP"
    r = pos.net_pnl() / pos.sizing.risk_amount
    row = pos.row(fr.close_ms)
    # gross loss ~ -1R (the fill moves the stop distance slightly), plus fees and slippage
    assert -1.15 < row["gross_r"] < -0.85
    cost_share = (
        2 * c.costs.taker_fee_bps + c.costs.entry_slippage_bps + c.costs.stop_slippage_bps
    ) / row["stop_bps"]
    assert math.isclose(-(row["fee_r"] + row["slip_r"]), cost_share, rel_tol=0.15)
    assert r < -1.0
    assert pos.gross_pnl() < 0 and pos.fees() > 0 and pos.slippage() > 0


def test_one_position_rule_counts_refusals_and_modes() -> None:
    c, inp, fr = _frame(seed=7, n_days=60)
    start, end = int(fr.close_ms[30 * 288]), int(fr.close_ms[-1]) + 1
    base = V6Engine(c, fr, inp.funding).run(start, end)
    seq = V6Engine(c, fr, inp.funding, mode="sequential").run(start, end)
    ind = V6Engine(c, fr, inp.funding, mode="independent").run(start, end)
    if base.refused.height:
        assert set(base.refused["reason"].unique()) <= {
            "BLOCKED_POSITION_OPEN",
            "BLOCKED_COOLDOWN_AFTER_CLOSE",
            "RISK_REJECTED",
            "END_OF_WINDOW",
        }
        cf = base.refused.filter(pl.col("reason") == "BLOCKED_POSITION_OPEN")
        assert cf["counterfactual_net_r"].is_not_nan().all()
    # independent mode never has fewer trades than the one-position baseline
    assert ind.trades.height >= base.trades.height
    assert seq.refused.height == 0 or set(seq.refused["reason"].unique()) <= {
        "RISK_REJECTED",
        "END_OF_WINDOW",
    }
    # baseline never overlaps positions
    t = base.trades.sort("entry_ms")
    if t.height > 1:
        assert (t["entry_ms"][1:].to_numpy() >= t["exit_ms"][:-1].to_numpy()).all()


def test_exit_variants_differ_only_in_exits() -> None:
    c, inp, fr = _frame(seed=11, n_days=50)
    start, end = int(fr.close_ms[30 * 288]), int(fr.close_ms[-1]) + 1
    a = V6Engine(c, fr, inp.funding, variant="A").run(start, end)
    d = V6Engine(c, fr, inp.funding, variant="D").run(start, end)
    if a.trades.height and d.trades.height:
        assert set(a.trades["exit_variant"].unique()) == {"A"}
        assert set(d.trades["exit_variant"].unique()) == {"D"}
        assert "TP1" not in set(d.trades["exit_reasons"].str.split(",").explode().unique())
        # the first trade shares its entry (same Stage A/B rules)
        assert a.trades["entry_ms"][0] == d.trades["entry_ms"][0]


def test_daily_budget_overlay_blocks() -> None:
    c, inp, fr = _frame(seed=13, n_days=60)
    start, end = int(fr.close_ms[30 * 288]), int(fr.close_ms[-1]) + 1
    ov = V6Engine(c, fr, inp.funding, overlay=True, overlay_losses=1, overlay_daily_loss=1e-6).run(
        start, end
    )
    base = V6Engine(c, fr, inp.funding).run(start, end)
    assert ov.trades.height <= base.trades.height
    if ov.refused.height:
        assert (
            "BLOCKED_DAILY_BUDGET" in set(ov.refused["reason"].unique())
            or ov.trades.height == base.trades.height
        )
