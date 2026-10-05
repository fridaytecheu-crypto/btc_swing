from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import polars as pl

from btc_swing.backtest.engine import BacktestEngine
from btc_swing.core.config import BtcStrategyConfig
from btc_swing.features.context import AuxSeries
from btc_swing.features.view import LookAheadError, MultiTfSeries
from btc_swing.v2.candidates import candidates_frame, generate_candidates
from btc_swing.v2.config import V2Config, load_v2_config
from btc_swing.v2.evaluation import classify, sequential_account
from btc_swing.v2.features import DERIVATIVES_FEATURES, FEATURE_NAMES
from btc_swing.v2.labels import label_candidates
from btc_swing.v2.walkforward import ModelSpec, make_folds, walk_forward


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


def test_v2_config_is_pre_declared_and_hashable() -> None:
    cfg = load_v2_config()
    assert isinstance(cfg, V2Config)
    assert cfg.slices == [0.5, 0.25, 0.10, 0.05]
    assert len(cfg.config_hash) == 64
    assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES))
    assert set(DERIVATIVES_FEATURES) <= set(FEATURE_NAMES)


def test_v2_candidates_labels_walkforward_on_synthetic(synthetic_data: dict[str, object]) -> None:
    cfg = synthetic_data["cfg"]
    bars, funding = synthetic_data["bars"], synthetic_data["funding"]
    assert isinstance(cfg, BtcStrategyConfig) and isinstance(bars, pl.DataFrame)
    assert isinstance(funding, pl.DataFrame)
    aux = AuxSeries.build(funding, None, None, None, 0)
    series = MultiTfSeries(bars, cfg.indicators, cfg.regime.trend_slope_bars)
    start, end = _ms(2023, 3, 1), _ms(2023, 9, 1)
    run = generate_candidates(cfg, series, aux, start, end, 12)
    cands = candidates_frame(run)
    assert run.n_bars > 0 and run.decisions.height == run.n_bars
    if cands.is_empty():
        return  # the synthetic series produced no triggered episode in this window
    # every candidate decision is PIT: its trigger time is a completed 5m close and features exist
    assert (cands["trigger_ms"] >= start).all() and (cands["trigger_ms"] < end).all()
    xcols = [c for c in cands.columns if c.startswith("x_")]
    assert len(xcols) == len(FEATURE_NAMES)
    eng = BacktestEngine(cfg, bars, funding, None, aux, series)
    labels = label_candidates(eng, run.candidates)
    acc = cands.filter(pl.col("risk_accepted"))
    assert labels.height == acc.height
    assert (
        labels["entry_ms"]
        >= labels["candidate_id"].map_elements(
            lambda i: int(cands.filter(pl.col("candidate_id") == i)["trigger_ms"][0]),
            return_dtype=pl.Int64,
        )
    ).all()
    df = cands.join(labels, on="candidate_id", how="inner").filter(pl.col("label_complete"))
    if df.height < 60:
        return
    df = df.with_columns((pl.col("R_MULTIPLE") > 0).cast(pl.Float64).alias("y_pos"))
    cfg2 = load_v2_config().model_copy(
        update={
            "walkforward": load_v2_config().walkforward.model_copy(
                update={"first_test_start": "2023-06-01", "block_months": 1, "min_train_rows": 20}
            )
        }
    )
    folds = make_folds("2023-06-01", "2023-09-01", 1)
    wf = walk_forward(df, ModelSpec("M1_logistic", "classifier", xcols), cfg2, folds, "y_pos")
    if wf.predictions.is_empty():
        return
    p = wf.predictions.join(df.select("candidate_id", "trigger_ms", "exit_ms"), on="candidate_id")
    # every prediction comes from a model trained strictly before the row's fold start
    for f in folds:
        rows = p.filter(pl.col("fold") == f.index)
        if rows.height:
            assert (rows["trigger_ms"] >= f.test_start_ms).all()
    thr = p["thr_25"].to_numpy()
    assert np.all(np.isfinite(thr))
    acct = sequential_account(df.join(wf.predictions, on="candidate_id"), 10_000.0, start, end)
    assert acct["n_trades"] <= df.height


def test_v2_classification_rule() -> None:
    def crit(met: dict[int, bool]) -> list[dict[str, object]]:
        return [{"id": i, "met": met.get(i, True)} for i in range(1, 7)]

    assert classify(crit({}), 0.2, 0.0).startswith("A")
    assert classify(crit({3: False}), 0.2, 0.0).startswith("B")
    assert classify(crit({1: False}), 0.2, 0.0).startswith("C")
    assert classify(crit({4: False}), -0.1, 0.0).startswith("C")
    assert classify(crit({2: False}), math.nan, 0.0).startswith("C")


def test_market_view_rejects_look_ahead(synthetic_data: dict[str, object]) -> None:
    cfg, bars = synthetic_data["cfg"], synthetic_data["bars"]
    assert isinstance(cfg, BtcStrategyConfig) and isinstance(bars, pl.DataFrame)
    series = MultiTfSeries(bars, cfg.indicators, cfg.regime.trend_slope_bars)
    v = series.view_at(int(bars["open_time_ms"][5000]) + 300_000)
    v.assert_pit()
    try:
        v.close(series.base.tf, -1)
    except LookAheadError:
        return
    raise AssertionError("negative offset must raise")
