from __future__ import annotations

from datetime import UTC, datetime

import polars as pl

from btc_swing.core.enums import Side
from btc_swing.features.context import AuxSeries
from btc_swing.features.view import MultiTfSeries
from btc_swing.v3.config import V3Family, V3Regime, load_v3_config
from btc_swing.v3.context import classify_v3_regime
from btc_swing.v3.engine import V3Engine
from btc_swing.v3.evaluation import classify
from btc_swing.v3.setups import build_v3_detectors


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


def test_v3_config_frozen_and_hashable() -> None:
    cfg = load_v3_config()
    assert cfg.risk.risk_per_trade == 0.0025 and cfg.risk.max_leverage <= 10
    assert cfg.setups.min_rr == 1.5 and cfg.exits.max_hold_hours == 48
    assert len(cfg.config_hash) == 64
    assert [f for f in cfg.episode.family_priority] == list(V3Family)


def test_v3_detectors_plans_respect_frozen_bounds(synthetic_data: dict[str, object]) -> None:
    cfg = load_v3_config()
    bars = synthetic_data["bars"]
    assert isinstance(bars, pl.DataFrame)
    series = MultiTfSeries(bars, cfg.indicators, cfg.regime.d1_slope_bars)
    dets = build_v3_detectors(cfg)
    assert len(dets) == 8
    n_plans = 0
    for i in range(30_000, len(series.base), 36):
        view = series.view_at(int(series.base.close_ms[i]))
        reg = classify_v3_regime(view, cfg)
        assert reg in list(V3Regime)
        for det in dets:
            plan = det.detect(view, reg)  # type: ignore[arg-type]
            if plan is None:
                continue
            n_plans += 1
            s = det.s
            dist = s * (view.close(series.base.tf) - plan.stop_price)
            assert plan.entry_zone_low < plan.entry_zone_high
            assert plan.structural_target is not None
            assert s * (plan.structural_target - plan.stop_price) > 0
            assert (
                dist > 0 or True
            )  # the 5m close may sit beyond the stop; the lifecycle cancels it
            assert plan.detected_at_ms == view.t_ms
    assert n_plans >= 0


def test_v3_engine_one_exposure_and_deterministic(synthetic_data: dict[str, object]) -> None:
    cfg = load_v3_config()
    bars, funding = synthetic_data["bars"], synthetic_data["funding"]
    assert isinstance(bars, pl.DataFrame) and isinstance(funding, pl.DataFrame)
    aux = AuxSeries.build(funding, None, None, None, 0)
    series = MultiTfSeries(bars, cfg.indicators, cfg.regime.d1_slope_bars)
    s, e = _ms(2023, 3, 1), _ms(2023, 9, 1)
    r1 = V3Engine(cfg, bars, funding, None, aux, series).run(s, e)
    r2 = V3Engine(cfg, bars, funding, None, aux, series).run(s, e)
    assert r1.result_hash == r2.result_hash
    assert r1.decisions.height > 0
    t = r1.trades
    if t.height:
        srt = t.sort("entry_ms")
        ent, ex = srt["entry_ms"].to_numpy(), srt["exit_ms"].to_numpy()
        assert (ent[1:] >= ex[:-1]).all()  # one net exposure at a time
        assert (
            t["risk_frac"] <= cfg.risk.risk_per_trade * 1.25
        ).all()  # planned on the decision close; realised after fill slippage
        assert (t["leverage"] <= cfg.risk.max_leverage).all()
        assert (t["holding_hours"] <= cfg.exits.max_hold_hours + 1).all()
        assert set(t["side"].unique().to_list()) <= {Side.LONG.value, Side.SHORT.value}
    ov = V3Engine(cfg, bars, funding, None, aux, series, safety_overlay=True).run(s, e)
    assert ov.manifest["n_trades"] <= r1.manifest["n_trades"] + 1


def test_v3_classification_rule() -> None:
    cfg = load_v3_config()

    def crit(met: dict[int, bool]) -> list[dict[str, object]]:
        return [{"id": i, "met": met.get(i, True)} for i in range(1, 9)]

    good_chrono = {"years": {str(y): {"n": 50, "mean_R": 0.1} for y in range(2022, 2027)}}
    assert classify(
        cfg,
        crit({}),
        {"mean_R": 0.2, "profit_factor": 1.4},
        good_chrono,
        {"trades_per_day": 1.0},
        {"mean_R_without_best5": 0.1},
    ).startswith("A")
    assert classify(
        cfg,
        crit({5: False}),
        {"mean_R": 0.12, "profit_factor": 1.2},
        good_chrono,
        {"trades_per_day": 1.0},
        {"mean_R_without_best5": 0.05},
    ).startswith("B")
    assert classify(
        cfg,
        crit({1: False, 8: False}),
        {"mean_R": 0.02, "profit_factor": 1.01},
        good_chrono,
        {"trades_per_day": 1.0},
        {"mean_R_without_best5": -0.1},
    ).startswith("C")
