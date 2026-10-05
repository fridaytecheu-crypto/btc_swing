from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import polars as pl
import pytest

from btc_swing.backtest.engine import BacktestEngine
from btc_swing.backtest.ledger import Position
from btc_swing.core.config import BtcStrategyConfig, load_btc_config
from btc_swing.core.enums import Regime, SetupFamily, Side
from btc_swing.features.context import AuxSeries
from btc_swing.ingest.normalise import parse_metrics_csv
from btc_swing.research.labels import label_episodes
from btc_swing.research.null_benchmark import run_null, simulate_entry
from btc_swing.risk.sizing import Sizing, liquidation_price
from btc_swing.setups.registry import build_detectors
from tests.conftest import ROOT, SYNTH_OVERRIDES


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


def test_registry_builds_all_eight_families(btc_cfg: BtcStrategyConfig) -> None:
    dets = build_detectors(btc_cfg)
    assert {d.family for d in dets} == set(SetupFamily)
    for d in dets:
        assert d.family.side is d.side  # type: ignore[attr-defined]


def test_metrics_csv_tolerates_empty_fields() -> None:
    df = parse_metrics_csv(
        b"create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
        b"sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
        b"2021-11-08 00:00:00,BTCUSDT,1.5,100.0,,,2.1,\n"
    )
    r = df.row(0, named=True)
    assert r["open_interest"] == 1.5 and r["top_trader_long_short_ratio_accounts"] is None


def test_aux_snapshot_is_pit() -> None:
    funding = pl.DataFrame(
        {"time_ms": [1_000_000, 2_000_000, 3_000_000], "funding_rate": [0.1, 0.2, 0.3]}
    )
    metrics = pl.DataFrame(
        {
            "time_ms": [500_000, 1_500_000, 2_500_000],
            "open_interest": [10.0, 11.0, 12.0],
            "open_interest_value": [1.0, 1.0, 1.0],
            "long_short_ratio_accounts": [1.0, 1.0, 1.0],
            "top_trader_long_short_ratio_positions": [1.0, 1.0, 1.0],
            "taker_long_short_volume_ratio": [1.0, 1.0, 1.0],
        }
    )
    aux = AuxSeries.build(funding, metrics, None, None, latency_minutes=0)
    from btc_swing.features.context import _last_at

    assert _last_at(aux.funding_t, aux.funding_rate, 1_999_999)[0] == 0.1
    assert _last_at(aux.funding_t, aux.funding_rate, 2_000_000)[0] == 0.2
    assert _last_at(aux.metrics_t, aux.oi, 2_499_999)[0] == 11.0
    assert math.isnan(_last_at(aux.metrics_t, aux.oi, 499_999)[0])
    # with latency the same observation becomes visible later
    aux2 = AuxSeries.build(funding, metrics, None, None, latency_minutes=1)
    assert _last_at(aux2.funding_t, aux2.funding_rate, 2_000_000, aux2.latency_ms)[0] == 0.1


def test_mark_price_alignment_and_liquidation_basis(
    bars_5m: pl.DataFrame, btc_cfg: BtcStrategyConfig
) -> None:
    sub = bars_5m.head(288 * 70)
    # mark = traded price shifted down 5% on one bar -> a long position must liquidate on the mark, not the trade
    mark = sub.select("open_time_ms", "close_time_ms", "open", "high", "low", "close")
    aux = AuxSeries.build(None, None, None, mark, 0)
    eng = BacktestEngine(btc_cfg, sub, None, {}, aux)
    assert eng.has_mark
    _o, _h, lo, _c = eng.aux.mark_aligned(eng.series.base.close_ms)
    assert np.allclose(lo, eng.series.base.low)
    bar = 288 * 65
    entry = float(eng.series.base.open[bar])
    sz = Sizing(
        True,
        "OK",
        10_000,
        50,
        0.005,
        entry,
        entry * 0.98,
        entry * 0.02,
        0.1,
        0.1 * entry,
        5.0,
        0.02 * entry,
        liquidation_price(entry, Side.LONG, 5.0, 0.005),
        0,
        0,
        0,
        False,
        0.005,
        0.02,
    )
    pos = Position(
        1,
        1,
        SetupFamily.TREND_PULLBACK_LONG,
        Side.LONG,
        Regime.TREND_UP,
        bar,
        0,
        entry,
        sz,
        0.1,
        0.1,
        entry * 0.98,
        entry * 0.98,
        "t",
        entry * 0.02,
        1.0,
        entry * 1.03,
        entry * 1.06,
        None,
        sz.liquidation_price,
        10_000,
        [1.0],
    )
    # force the mark low far below liquidation on this bar while the traded low stays above the stop
    eng.mark_l[bar] = sz.liquidation_price * 0.99
    closed = eng._process_bar(
        pos, bar, int(eng.series.base.close_ms[bar]), entry, entry * 1.001, entry * 0.999, entry
    )
    assert closed and pos.exits[-1].reason.value == "LIQUIDATION"
    assert pos.net_pnl == pytest.approx(-sz.margin)


def test_labels_and_null_benchmark_on_synthetic(synthetic_data: dict[str, object]) -> None:
    cfg = synthetic_data["cfg"]
    bars, funding = synthetic_data["bars"], synthetic_data["funding"]
    assert isinstance(cfg, BtcStrategyConfig) and isinstance(bars, pl.DataFrame)
    eng = BacktestEngine(cfg, bars, funding)  # type: ignore[arg-type]
    res = eng.run(_ms(2023, 3, 1), _ms(2023, 9, 1))
    assert res.trades.height > 0
    lab = label_episodes(res.episodes, eng.series, cfg.exits.max_hold_hours)
    assert lab.height == res.episodes.height
    assert set(lab["path_outcome"].unique().to_list()) <= {
        "target_first",
        "invalidation_first",
        "neither",
        "no_target",
    }
    assert lab["fwd_ret_24h"].drop_nulls().len() > 0
    assert set(lab["outcome_class"].unique().to_list()) <= {
        "TRADED",
        "INVALIDATED",
        "NEVER_TRIGGERED",
        "RISK_REJECTED",
    }
    # null: a matched random entry follows the same exit rules and costs
    tr = res.trades.row(0, named=True)
    pos = simulate_entry(
        eng, 288 * 70, Side(tr["side"]), float(tr["stop_distance_pct"]), 100.0, 10_000.0
    )
    assert pos is not None and not pos.is_open and pos.fees > 0
    nl = run_null(
        eng, res.trades.head(5), res.decisions, _ms(2023, 3, 1), _ms(2023, 9, 1), k=3, seed=1
    )
    assert nl.summary["n_trades"] == 5 and "time" in nl.summary and nl.samples.height > 0
    # determinism of the null
    nl2 = run_null(
        eng, res.trades.head(5), res.decisions, _ms(2023, 3, 1), _ms(2023, 9, 1), k=3, seed=1
    )
    assert nl2.samples.equals(nl.samples)


def test_leverage_override_never_exceeds_hard_cap() -> None:
    cfg = load_btc_config(ROOT / "config" / "btc_swing.default.yaml", overrides=SYNTH_OVERRIDES)
    c1 = cfg.model_copy(
        update={
            "risk": cfg.risk.model_copy(update={"max_leverage": 1.0, "allowed_leverage": [1.0]})
        }
    )
    assert c1.risk.max_leverage == 1.0
    with pytest.raises(ValueError):
        load_btc_config(
            ROOT / "config" / "btc_swing.default.yaml",
            overrides={"risk": {"max_leverage": 25, "allowed_leverage": [1, 25]}},
        )


def test_zone_entry_confirm_exit_mechanics(synthetic_data: dict[str, object]) -> None:
    """Phase 2.2: zone entry, then confirmation monitoring with early exits."""
    from btc_swing.core.enums import ExitReason

    cfg = synthetic_data["cfg"]
    bars, funding = synthetic_data["bars"], synthetic_data["funding"]
    assert isinstance(cfg, BtcStrategyConfig) and isinstance(bars, pl.DataFrame)
    cfg_v = cfg.model_copy(
        update={
            "experiment": cfg.experiment.model_copy(
                update={"entry_mode": "ZONE_ENTRY_CONFIRM_EXIT"}
            )
        }
    )
    res = BacktestEngine(cfg_v, bars, funding).run(_ms(2023, 3, 1), _ms(2023, 9, 1))  # type: ignore[arg-type]
    t = res.trades
    assert t.height > 0
    window = cfg.episode.entry_ready_timeout_bars
    for r in t.to_dicts():
        if r["early_exit_reason"] == ExitReason.EARLY_EXIT_NO_CONFIRMATION.value:
            # exit at the open of the bar after the deadline: window bars + 1 after the entry open
            assert abs(r["holding_hours"] - (window + 1) * 5 / 60) < 1e-6
            assert not r["confirmed_after_entry"]
        if r["confirmed_after_entry"] and not r["confirmed_at_entry"]:
            assert 0 <= r["bars_to_confirmation"] < window
            assert r["early_exit_reason"] is None
        if r["early_exit_reason"] == ExitReason.EARLY_EXIT_INVALIDATION.value:
            assert r["holding_hours"] <= (window + 1) * 5 / 60 + 1e-9
    # unconfirmed trades never live past the window unless a normal exit took them out earlier
    unconfirmed = t.filter(~pl.col("confirmed_after_entry"))
    assert (unconfirmed["holding_hours"] <= (window + 1) * 5 / 60 + 1e-9).all()
    # the control arm is untouched by the new fields
    res_c = BacktestEngine(cfg, bars, funding).run(_ms(2023, 3, 1), _ms(2023, 9, 1))  # type: ignore[arg-type]
    assert "early_exit_reason" not in res_c.trades.columns
    assert "confirmed_after_entry" not in res_c.trades.columns
