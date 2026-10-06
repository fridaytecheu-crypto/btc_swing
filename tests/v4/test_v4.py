from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import polars as pl

from btc_swing.core.enums import Side
from btc_swing.features.context import AuxSeries
from btc_swing.features.view import MultiTfSeries
from btc_swing.v4.config import V4Family, load_v4_config
from btc_swing.v4.engine import V4Engine
from btc_swing.v4.evaluation import classify
from btc_swing.v4.events import build_v4_detectors
from btc_swing.v4.features import FEATURE_COLUMNS, build_feature_frame, rolling_z
from btc_swing.v4.null import run_v4_null
from btc_swing.v4.stage_a import scan_events, stage_a_gate, stage_a_summary, unconditional


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


def test_v4_config_frozen() -> None:
    cfg = load_v4_config()
    assert cfg.risk.risk_per_trade == 0.0025 and cfg.geometry.vol_floor_atr == 1.5
    assert cfg.exits.tp1_r == 1.0 and cfg.exits.tp2_r == 2.0 and cfg.exits.max_hold_hours == 48
    assert list(cfg.episode.family_priority) == list(V4Family)
    assert len(cfg.config_hash) == 64


def test_rolling_z_is_causal() -> None:
    x = np.arange(100, dtype=float)
    z = rolling_z(x, 10, 5)
    assert np.isnan(z[:5]).all()
    # the current value never enters its own window: with a linear series every z is identical
    assert np.allclose(z[20:], z[20], atol=1e-9)
    x2 = x.copy()
    x2[50] = 1e6
    z2 = rolling_z(x2, 10, 5)
    assert np.allclose(
        z[:50], z2[:50], atol=1e-9, equal_nan=True
    )  # a later shock never changes earlier rows


def test_v4_feature_frame_and_stage_a_pit(synthetic_data: dict[str, object]) -> None:
    cfg = load_v4_config()
    bars, funding = synthetic_data["bars"], synthetic_data["funding"]
    assert isinstance(bars, pl.DataFrame) and isinstance(funding, pl.DataFrame)
    aux = AuxSeries.build(funding, None, None, None, 0)
    series = MultiTfSeries(bars, cfg.indicators, 5)
    ff = build_feature_frame(series, aux, cfg)
    assert set(FEATURE_COLUMNS) <= set(ff.cols)
    assert len(ff.regime) == len(ff.close_ms)
    dets = build_v4_detectors(cfg, ff)
    assert len(dets) == 6
    s, e = _ms(2023, 3, 1), _ms(2023, 9, 1)
    ev = scan_events(ff, dets, series, cfg, s, e)
    unc = unconditional(ff, series, cfg, s, e)
    sa = stage_a_summary(ev, cfg, unc)
    gate = stage_a_gate(sa, cfg)
    assert "passed" in gate and unc["n_bars"] > 0
    # truncation: events before a cut are identical when later data is removed
    cut = _ms(2023, 6, 1)
    trunc = bars.filter(pl.col("open_time_ms") + 300_000 <= cut)
    series_t = MultiTfSeries(trunc, cfg.indicators, 5)
    ff_t = build_feature_frame(
        series_t,
        AuxSeries.build(funding.filter(pl.col("time_ms") <= cut), None, None, None, 0),
        cfg,
    )
    ev_t = scan_events(ff_t, build_v4_detectors(cfg, ff_t), series_t, cfg, s, cut)
    if ev.height:
        a = ev.filter(pl.col("t_ms") <= cut - 2 * 86_400_000)
        b = ev_t.filter(pl.col("t_ms") <= cut - 2 * 86_400_000) if ev_t.height else ev_t
        assert a.height == b.height
        if a.height:
            assert a["t_ms"].to_list() == b["t_ms"].to_list()


def _synthetic_metrics(bars: pl.DataFrame) -> pl.DataFrame:
    """Random-walk open interest and ratios on the 5m grid so that V4 events can fire in tests."""
    rng = np.random.RandomState(3)
    n = bars.height
    oi = 100_000.0 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    return pl.DataFrame(
        {
            "time_ms": bars["close_time_ms"],
            "open_interest": oi,
            "open_interest_value": oi * 30_000.0,
            "long_short_ratio_accounts": 1.0 + rng.normal(0, 0.1, n),
            "top_trader_long_short_ratio_positions": 1.0 + rng.normal(0, 0.1, n),
            "taker_long_short_volume_ratio": 1.0 + rng.normal(0, 0.2, n),
        }
    )


def test_v4_engine_stops_floor_and_one_exposure(synthetic_data: dict[str, object]) -> None:
    cfg = load_v4_config()
    bars, funding = synthetic_data["bars"], synthetic_data["funding"]
    assert isinstance(bars, pl.DataFrame) and isinstance(funding, pl.DataFrame)
    aux = AuxSeries.build(funding, _synthetic_metrics(bars), None, None, 0)
    series = MultiTfSeries(bars, cfg.indicators, 5)
    ff = build_feature_frame(series, aux, cfg)
    s, e = _ms(2023, 3, 1), _ms(2023, 9, 1)
    r1 = V4Engine(cfg, bars, funding, None, aux, series, ff).run(s, e)
    r2 = V4Engine(cfg, bars, funding, None, aux, series, ff).run(s, e)
    assert r1.result_hash == r2.result_hash and r1.decisions.height > 0
    assert r1.episodes.height > 0  # events fire on the synthetic OI walk; episode rows serialise
    t = r1.trades
    if t.height:
        srt = t.sort("entry_ms")
        assert (srt["entry_ms"].to_numpy()[1:] >= srt["exit_ms"].to_numpy()[:-1]).all()
        # the stop distance is at least the volatility floor (ATR at detection), up to fill slippage
        assert (t["stop_distance_atr"] >= cfg.geometry.vol_floor_atr * 0.9).all()
        assert (t["stop_distance_atr"] <= cfg.geometry.max_stop_atr * 1.1).all()
        assert (t["leverage"] <= cfg.risk.max_leverage).all()
        assert set(t["side"].unique().to_list()) <= {Side.LONG.value, Side.SHORT.value}
        nl = run_v4_null(
            V4Engine(cfg, bars, funding, None, aux, series, ff), t.head(3), r1.decisions, s, e, 2, 1
        )
        assert nl["n_trades"] == 3


def test_v4_classification_rule() -> None:
    cfg = load_v4_config()

    def crit(met: dict[int, bool]) -> list[dict[str, object]]:
        return [{"id": i, "met": met.get(i, True)} for i in range(0, 10)]

    assert classify(cfg, crit({}), {"expectancy_R_before_costs": 0.2}).startswith("A")
    assert classify(
        cfg, crit({2: False, 6: False}), {"expectancy_R_before_costs": 0.12}
    ).startswith("B")
    assert classify(cfg, crit({0: False}), {"expectancy_R_before_costs": 0.2}).startswith("C")
    assert classify(cfg, crit({9: False}), {"expectancy_R_before_costs": 0.2}).startswith("C")
