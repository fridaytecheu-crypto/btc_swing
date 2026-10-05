from __future__ import annotations

import math

import polars as pl
import pytest

from btc_swing.core.config import load_btc_config
from btc_swing.core.enums import Side
from btc_swing.execution.costs import CostModel, FundingSchedule
from btc_swing.risk.sizing import liquidation_price, size_position
from tests.conftest import ROOT

CFG = load_btc_config(ROOT / "config" / "btc_swing.default.yaml")


def test_risk_chain_equity_to_leverage() -> None:
    r = CFG.risk
    s = size_position(10_000.0, 50_000.0, 49_000.0, Side.LONG, 800.0, r)  # 2% stop
    assert s.accepted and not s.risk_capped
    assert s.risk_amount == pytest.approx(10_000 * r.risk_per_trade)
    assert s.qty == pytest.approx(s.risk_amount / 1_000.0)
    assert s.notional == pytest.approx(s.qty * 50_000)
    # notional = 2500 = 25% of equity -> fits 1x under the 25% margin cap
    assert s.leverage == 1.0 and s.margin == pytest.approx(s.notional)
    assert s.stop_to_liquidation_ratio >= r.min_stop_to_liquidation_ratio
    assert s.max_account_loss_at_stop == pytest.approx(r.risk_per_trade)


def test_tighter_stop_needs_more_leverage_not_more_risk() -> None:
    r = CFG.risk
    s = size_position(10_000.0, 50_000.0, 49_750.0, Side.LONG, 800.0, r)  # 0.5% stop
    assert s.accepted and not s.risk_capped
    assert s.risk_amount == pytest.approx(50.0)
    assert s.notional == pytest.approx(10_000.0)  # 1x equity
    assert s.leverage == 5.0  # smallest allowed leverage with notional/L <= 2500
    assert s.leverage <= r.max_leverage <= 10
    assert s.liquidation_distance >= r.min_stop_to_liquidation_ratio * s.stop_distance


def test_margin_cap_scales_position_down_instead_of_exceeding_max_leverage() -> None:
    r = CFG.risk
    s = size_position(10_000.0, 50_000.0, 49_950.0, Side.LONG, 800.0, r)  # 0.1% stop
    assert s.accepted and s.risk_capped
    assert s.leverage <= 10
    assert s.notional == pytest.approx(10 * 2_500.0)
    assert s.risk_amount < 10_000 * r.risk_per_trade


def test_liquidation_buffer_rejects_wide_stop_at_high_leverage() -> None:
    # 20% stop: at 1x liquidation is ~99.5% away (ok); force allowed leverage 10 only -> ~9.5% liq distance < 3*20%
    r = CFG.risk.model_copy(update={"allowed_leverage": [10.0]})
    s = size_position(10_000.0, 50_000.0, 40_000.0, Side.LONG, 800.0, r)
    assert not s.accepted and "LIQUIDATION" in s.reason


def test_liquidation_formula_both_sides() -> None:
    assert liquidation_price(100.0, Side.LONG, 10.0, 0.005) == pytest.approx(90.5)
    assert liquidation_price(100.0, Side.SHORT, 10.0, 0.005) == pytest.approx(109.5)
    assert liquidation_price(100.0, Side.LONG, 1.0, 0.005) == pytest.approx(0.5)
    s = size_position(10_000.0, 50_000.0, 51_000.0, Side.SHORT, 800.0, CFG.risk)
    assert s.accepted and s.liquidation_price > 50_000


def test_wrong_side_stop_rejected() -> None:
    s = size_position(10_000.0, 50_000.0, 51_000.0, Side.LONG, 800.0, CFG.risk)
    assert not s.accepted and math.isnan(s.liquidation_price)


def test_costs_direction_and_funding_sign() -> None:
    cm = CostModel(CFG.costs)
    assert cm.entry_fill(100.0, Side.LONG) > 100.0 > cm.entry_fill(100.0, Side.SHORT)
    assert cm.stop_fill(100.0, Side.LONG) < 100.0 < cm.stop_fill(100.0, Side.SHORT)
    assert cm.fee(10_000.0) == pytest.approx(10_000 * CFG.costs.taker_fee_bps / 1e4)
    assert FundingSchedule.payment(0.0001, 1.0, 50_000.0, Side.LONG) == pytest.approx(-5.0)
    assert FundingSchedule.payment(0.0001, 1.0, 50_000.0, Side.SHORT) == pytest.approx(5.0)
    fs = FundingSchedule(
        pl.DataFrame({"time_ms": [1000, 2000, 3000], "funding_rate": [0.1, 0.2, 0.3]})
    )
    assert fs.events_between(1000, 2000) == [(2000, 0.2)]  # (after, through]
    assert fs.events_between(0, 3000) == [(1000, 0.1), (2000, 0.2), (3000, 0.3)]
