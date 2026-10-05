from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest

from btc_swing.backtest.engine import BacktestEngine
from btc_swing.core.config import BtcStrategyConfig
from btc_swing.research.metrics import compute_metrics
from btc_swing.research.report import render_report


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


@pytest.fixture(scope="module")
def result(synthetic_data: dict[str, object]):  # type: ignore[no-untyped-def]
    cfg = synthetic_data["cfg"]
    bars, funding = synthetic_data["bars"], synthetic_data["funding"]
    assert isinstance(cfg, BtcStrategyConfig) and isinstance(bars, pl.DataFrame)
    eng = BacktestEngine(cfg, bars, funding, {"perp": "abc"})  # type: ignore[arg-type]
    return cfg, bars, funding, eng.run(_ms(2023, 3, 1), _ms(2023, 9, 1))


def test_run_produces_trades_and_is_deterministic(result) -> None:  # type: ignore[no-untyped-def]
    cfg, bars, funding, res = result
    assert res.manifest["n_trades"] > 0
    assert res.manifest["n_episodes"] >= res.manifest["n_trades"]
    assert res.decisions.height == res.manifest["n_5m_evaluations"]
    res2 = BacktestEngine(cfg, bars, funding).run(_ms(2023, 3, 1), _ms(2023, 9, 1))
    assert res2.result_hash == res.result_hash


def test_accounting_identities_and_risk_limits(result) -> None:  # type: ignore[no-untyped-def]
    cfg, _, _, res = result
    t = res.trades
    for r in t.to_dicts():
        assert r["POSITION_PNL"] == pytest.approx(
            r["gross_pnl"] - r["fees"] + r["funding"], abs=1e-9
        )
        assert r["RETURN_ON_MARGIN"] == pytest.approx(r["POSITION_PNL"] / r["margin"])
        assert r["ACCOUNT_RETURN"] == pytest.approx(r["POSITION_PNL"] / r["equity_at_entry"])
        assert r["R_MULTIPLE"] == pytest.approx(r["POSITION_PNL"] / r["risk_amount"])
        sign = 1 if r["side"] == "LONG" else -1
        assert r["BTC_RETURN"] == pytest.approx(
            sign * (r["avg_exit_price"] - r["entry_price"]) / r["entry_price"]
        )
        assert r["leverage"] <= cfg.risk.max_leverage <= 10
        assert r["leverage"] in cfg.risk.allowed_leverage
        assert r["stop_to_liquidation_ratio"] >= cfg.risk.min_stop_to_liquidation_ratio
        assert r["margin"] <= cfg.risk.initial_equity * cfg.risk.margin_cap_frac * (1 + 1e-9)
        if not r["risk_capped"]:
            # planned risk at the decision close; realised differs only by slippage/gap
            assert abs(r["risk_frac"] - cfg.risk.risk_per_trade) < 0.0015
        assert r["holding_hours"] <= cfg.exits.max_hold_hours + 1
        assert r["MAE_R"] <= 0 <= r["MFE_R"]
        assert r["fees"] > 0
        assert r["exit_reason"] in {
            "STOP",
            "TRAIL",
            "TP2",
            "TIME_LIMIT",
            "END_OF_DATA",
            "LIQUIDATION",
        }
    # every trade belongs to exactly one episode that ended CLOSED
    ep = res.episodes.filter(pl.col("trade_id").is_not_null())
    assert ep.height == t.height
    assert set(ep["final_state"].to_list()) == {"CLOSED"}


def test_no_look_ahead_by_truncation(result) -> None:  # type: ignore[no-untyped-def]
    """Decisions up to T must be identical whether or not data after T exists."""
    cfg, bars, funding, res = result
    cut = _ms(2023, 6, 15)
    trunc = bars.filter(pl.col("open_time_ms") + 300_000 <= cut)
    res_t = BacktestEngine(cfg, trunc, funding).run(_ms(2023, 3, 1), cut)
    cols = ["t_ms", "regime", "episode_state", "family", "position_open"]
    full = res.decisions.filter(pl.col("t_ms") <= cut).select(cols)
    part = res_t.decisions.filter(pl.col("t_ms") <= cut).select(cols)
    assert full.height == part.height > 1000
    assert full.equals(part)


def test_metrics_and_report_render(result) -> None:  # type: ignore[no-untyped-def]
    cfg, _, _, res = result
    m = compute_metrics(res.trades, res.episodes, res.daily_equity, res.manifest, cfg.research)
    assert m["overall"]["n"] == res.manifest["n_trades"]
    assert "expectancy_R" in m["overall"] and "sharpe_daily_annualised" in m["account"]
    assert set(m["by_side"]) <= {"LONG", "SHORT"}
    txt = render_report(res.manifest, m, cfg.canonical_yaml(), "test", ["caveat"])
    for needle in (
        "BTC_RETURN",
        "RETURN_ON_MARGIN",
        "ACCOUNT_RETURN",
        "liquidation",
        "LONG vs SHORT",
        "By regime",
    ):
        assert needle in txt
