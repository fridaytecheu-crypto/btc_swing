"""V2 offline research pipeline (orchestrator). Candidates -> labels -> features -> walk-forward
models -> evaluation -> persisted artefacts. The report is rendered by `v2/report.py`."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from btc_swing.backtest.engine import BacktestEngine
from btc_swing.core import versions
from btc_swing.core.config import BtcStrategyConfig
from btc_swing.features.context import AuxSeries
from btc_swing.features.view import MultiTfSeries
from btc_swing.research.null_benchmark import run_null
from btc_swing.research.phase2 import Phase2Inputs
from btc_swing.research.phase3 import arm_configs
from btc_swing.v2.candidates import candidates_frame, generate_candidates
from btc_swing.v2.config import (
    CANDIDATE_RULE_VERSION,
    FEATURE_SET_VERSION,
    LABEL_VERSION,
    V2_VERSION,
    V2Config,
)
from btc_swing.v2.evaluation import (
    breakdown,
    classify,
    derivatives_diagnostics,
    evaluate_criteria,
    rank_metrics,
    slice_by_half,
    slice_per_fold,
    slice_table,
    top_n_per_month,
)
from btc_swing.v2.features import DERIVATIVES_FEATURES, FEATURE_NAMES
from btc_swing.v2.labels import label_candidates
from btc_swing.v2.walkforward import (
    Fold,
    ModelSpec,
    WalkForwardResult,
    coefficient_stability,
    make_folds,
    walk_forward,
)

log = logging.getLogger(__name__)


def _ms(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp() * 1000)


@dataclass
class V2Result:
    manifest: dict[str, Any]
    candidates: pl.DataFrame  # all candidates (features, aux, risk flag)
    labelled: pl.DataFrame  # candidates x labels (complete labels only)
    evaluable: pl.DataFrame  # labelled rows with walk-forward predictions (M1 score etc.)
    folds: list[Fold]
    models: dict[str, WalkForwardResult]
    rank: dict[str, dict[str, Any]]
    slices: dict[str, list[dict[str, Any]]]
    halves: dict[str, Any]
    per_fold_top: list[dict[str, Any]]
    top_n: list[dict[str, Any]]
    breakdowns: dict[str, list[dict[str, Any]]]
    derivatives: list[dict[str, Any]]
    coef_stability: list[dict[str, Any]]
    baselines: dict[str, Any]
    null: dict[str, Any]
    pit: dict[str, Any]
    gate: dict[str, Any]
    criteria: list[dict[str, Any]]
    classification: str
    population: dict[str, Any] = field(default_factory=dict)


def _merge_predictions(base: pl.DataFrame, wf: WalkForwardResult, prefix: str) -> pl.DataFrame:
    if wf.predictions.is_empty():
        return base
    p = wf.predictions.rename(
        {c: (f"{prefix}_{c}" if c != "candidate_id" else c) for c in wf.predictions.columns}
    )
    return base.join(p, on="candidate_id", how="left")


def run_v2_research(
    cfg2: V2Config, cfg1: BtcStrategyConfig, inp: Phase2Inputs, out_dir: Path
) -> V2Result:
    start_ms, end_ms = _ms(cfg2.candidates.start), _ms(cfg2.candidates.end_exclusive)
    cfg_c, _ = arm_configs(cfg1)  # the frozen V1 CONTROL execution framework
    aux = AuxSeries.build(
        inp.funding, inp.metrics, inp.premium, inp.mark, cfg1.data.latency_minutes
    )
    series = MultiTfSeries(inp.bars, cfg1.indicators, cfg1.regime.trend_slope_bars)
    eng = BacktestEngine(cfg_c, inp.bars, inp.funding, inp.hashes, aux, series)
    log.info("candidate generation %s -> %s", cfg2.candidates.start, cfg2.candidates.end_exclusive)
    run = generate_candidates(
        cfg_c, series, aux, start_ms, end_ms, cfg2.candidates.cooldown_after_candidate_bars
    )
    cands = candidates_frame(run)
    log.info("labels")
    labels = label_candidates(eng, run.candidates)
    labelled = cands.join(labels, on="candidate_id", how="inner").filter(pl.col("label_complete"))
    labelled = labelled.with_columns(
        (pl.col("R_MULTIPLE") > 0).cast(pl.Float64).alias("y_pos"),
        pl.col("R_MULTIPLE").clip(cfg2.labels.clip_low, cfg2.labels.clip_high).alias("y_r_clipped"),
    )
    feats = [f"x_{n}" for n in FEATURE_NAMES]
    feats_nodrv = [f"x_{n}" for n in FEATURE_NAMES if n not in DERIVATIVES_FEATURES]
    folds = make_folds(
        cfg2.walkforward.first_test_start,
        cfg2.candidates.end_exclusive,
        cfg2.walkforward.block_months,
    )
    log.info(
        "walk-forward: %d folds, %d labelled candidates, %d features",
        len(folds),
        labelled.height,
        len(feats),
    )
    models: dict[str, WalkForwardResult] = {}
    models["M1_logistic"] = walk_forward(
        labelled, ModelSpec("M1_logistic", "classifier", feats), cfg2, folds, "y_pos"
    )
    models["M2_ridge"] = walk_forward(
        labelled, ModelSpec("M2_ridge", "regressor", feats), cfg2, folds, "y_r_clipped"
    )
    models["M1_no_derivatives"] = walk_forward(
        labelled, ModelSpec("M1_no_derivatives", "classifier", feats_nodrv), cfg2, folds, "y_pos"
    )
    ev = labelled
    for name, wf in models.items():
        ev = _merge_predictions(ev, wf, name)
    ev = ev.filter(pl.col("M1_logistic_score").is_not_null())
    ev = ev.with_columns(
        pl.col("M1_logistic_score").alias("score"), pl.col("M1_logistic_fold").alias("fold")
    )
    for q in cfg2.slices:
        col = f"thr_{int(q * 100):02d}"
        ev = ev.with_columns(pl.col(f"M1_logistic_{col}").alias(col))
    oof_start = int(ev.select(pl.col("trigger_ms").min()).item()) if ev.height else start_ms
    eq = cfg1.risk.initial_equity
    # ranking metrics / slices for M1
    rank: dict[str, dict[str, Any]] = {"M1_logistic": rank_metrics(ev, "score")}
    slices: dict[str, list[dict[str, Any]]] = {
        "M1_logistic": slice_table(ev, cfg2, eq, oof_start, end_ms, "score")
    }
    # M2 ranking (expected net R) and ablation, as alternative score columns with their own thresholds
    for name in ("M2_ridge", "M1_no_derivatives"):
        if f"{name}_score" in ev.columns:
            tmp = ev.with_columns(
                pl.col(f"{name}_score").alias("score_alt"),
                *[
                    pl.col(f"{name}_thr_{int(q * 100):02d}").alias(f"thr_{int(q * 100):02d}")
                    for q in cfg2.slices
                ],
            ).filter(pl.col("score_alt").is_not_null())
            rank[name] = rank_metrics(tmp, "score_alt")
            slices[name] = slice_table(tmp, cfg2, eq, oof_start, end_ms, "score_alt")
    # M3 gate (pre-declared) and fit
    top_q = cfg2.criteria.top_slice
    top_m1 = next((s for s in slices["M1_logistic"] if abs(s["q"] - top_q) < 1e-9), {})
    all_m1 = slices["M1_logistic"][0]
    gain = (top_m1.get("mean_R", math.nan) or math.nan) - (
        all_m1.get("mean_R", math.nan) or math.nan
    )
    gate = {
        "spearman_M1": rank["M1_logistic"].get("spearman"),
        "top_quartile_gain_R": gain,
        "threshold_gain_R": cfg2.models.gate_min_top_quartile_gain_r,
        "passed": bool(
            (rank["M1_logistic"].get("spearman") or 0) > 0
            and not math.isnan(gain)
            and gain > cfg2.models.gate_min_top_quartile_gain_r
        ),
    }
    if gate["passed"]:
        log.info("M3 gate passed -> HistGradientBoosting")
        models["M3_hgb"] = walk_forward(
            labelled, ModelSpec("M3_hgb", "classifier", feats), cfg2, folds, "y_pos"
        )
        ev = _merge_predictions(ev, models["M3_hgb"], "M3_hgb")
        tmp = ev.with_columns(
            pl.col("M3_hgb_score").alias("score_alt"),
            *[
                pl.col(f"M3_hgb_thr_{int(q * 100):02d}").alias(f"thr_{int(q * 100):02d}")
                for q in cfg2.slices
            ],
        ).filter(pl.col("score_alt").is_not_null())
        rank["M3_hgb"] = rank_metrics(tmp, "score_alt")
        slices["M3_hgb"] = slice_table(tmp, cfg2, eq, oof_start, end_ms, "score_alt")
    # halves, per-fold stability, top-n, breakdowns (M1)
    split_ms = _ms("2025-01-01")
    halves = slice_by_half(ev, top_q, split_ms, "score")
    per_fold_top = slice_per_fold(ev, top_q, "score")
    top_n = [top_n_per_month(ev, n, "score") for n in cfg2.top_n_per_month]
    breakdowns = {
        "side": breakdown(ev, top_q, "side"),
        "family": breakdown(ev, top_q, "family"),
        "regime_at_trigger": breakdown(ev, top_q, "regime_at_trigger"),
    }
    # baselines
    log.info("baselines")
    blocked = ev.filter(~pl.col("short_in_trend_down"))
    v1 = eng.run(start_ms, end_ms, notes={"phase": "v2-baseline", "arm": "V1_CONTROL"})
    v1_trades = v1.trades.filter(pl.col("entry_ms") >= oof_start) if v1.trades.height else v1.trades
    baselines = {
        "all_candidates": {**_pop(ev), "account": slices["M1_logistic"][0]["account"]},
        "hard_block_only": {**_pop(blocked), "account": _acct(blocked, eq, oof_start, end_ms)},
        "model_plus_hard_block": {
            "top_slice": {
                **_pop(blocked.filter(pl.col("score") >= pl.col(f"thr_{int(top_q * 100):02d}"))),
                "account": _acct(
                    blocked.filter(pl.col("score") >= pl.col(f"thr_{int(top_q * 100):02d}")),
                    eq,
                    oof_start,
                    end_ms,
                ),
            },
            "rank": rank_metrics(blocked, "score") if blocked.height else {"n": 0},
        },
        "v1_single_slot_traded": {
            **_pop(v1_trades.rename({"entry_ms": "trigger_ms"}) if v1_trades.height else v1_trades),
            "result_hash": v1.result_hash,
            "n_trades_full_window": v1.manifest["n_trades"],
        },
    }
    log.info("null benchmark (K=%d)", cfg2.null_k)
    null_in = ev.filter(pl.col("score") >= pl.col(f"thr_{int(top_q * 100):02d}"))
    null_top = (
        run_null(
            eng, _as_trades(null_in), run.decisions, oof_start, end_ms, cfg2.null_k, cfg2.seed
        ).summary
        if null_in.height
        else {"n_trades": 0}
    )
    null_all = (
        run_null(
            eng,
            _as_trades(ev),
            run.decisions,
            oof_start,
            end_ms,
            max(1, cfg2.null_k // 2),
            cfg2.seed,
        ).summary
        if ev.height
        else {"n_trades": 0}
    )
    null = {"top_slice": null_top, "all_candidates": null_all}
    # PIT audit: regenerate candidates + features with later data removed
    log.info("PIT truncation audit")
    pit = _truncation_audit(cfg_c, cfg2, inp, cands, start_ms, start_ms + (end_ms - start_ms) // 2)
    pit["feature_set"] = FEATURE_SET_VERSION
    pit["n_features"] = len(FEATURE_NAMES)
    criteria = evaluate_criteria(
        rank["M1_logistic"], slices["M1_logistic"], halves, per_fold_top, null_top, cfg2
    )
    cls = classify(criteria, top_m1.get("mean_R", math.nan), all_m1.get("mean_R", math.nan))
    population = {
        "n_candidates": cands.height,
        "n_risk_rejected": int((~cands["risk_accepted"]).sum()) if cands.height else 0,
        "n_labelled_complete": labelled.height,
        "n_evaluable_walk_forward": ev.height,
        "n_bars": run.n_bars,
        "episode_outcomes": run.episode_outcomes,
        "by_family": cands.group_by("family").len().sort("family").to_dicts()
        if cands.height
        else [],
        "by_regime_at_trigger": cands.group_by("regime_at_trigger")
        .len()
        .sort("regime_at_trigger")
        .to_dicts()
        if cands.height
        else [],
        "by_year": labelled.with_columns(
            pl.from_epoch("trigger_ms", time_unit="ms").dt.year().alias("year")
        )
        .group_by("year")
        .agg(
            pl.len().alias("n"),
            pl.col("R_MULTIPLE").mean().alias("mean_R"),
            pl.col("y_pos").mean().alias("pos_rate"),
        )
        .sort("year")
        .to_dicts()
        if labelled.height
        else [],
        "label_stats_all": _pop(labelled),
    }
    manifest = {
        "project": "btc_swing_v2",
        "version": V2_VERSION,
        "feature_set_version": FEATURE_SET_VERSION,
        "label_version": LABEL_VERSION,
        "candidate_rule_version": CANDIDATE_RULE_VERSION,
        "v2_config_hash": cfg2.config_hash,
        "v2_config_yaml": cfg2.canonical_yaml(),
        "v1_config_hash": cfg_c.config_hash,
        "v1_strategy_version": versions.STRATEGY_VERSION,
        "code_version": versions.code_version(),
        "data_hashes": dict(inp.hashes),
        "window": {
            "start": cfg2.candidates.start,
            "end_exclusive": cfg2.candidates.end_exclusive,
            "first_test_start": cfg2.walkforward.first_test_start,
        },
        "folds": [
            {
                "index": f.index,
                "name": f.name,
                "test_start_ms": f.test_start_ms,
                "test_end_ms": f.test_end_ms,
            }
            for f in folds
        ],
        "feature_names": FEATURE_NAMES,
        "models": {
            k: {
                "spec": {
                    "name": v.spec.name,
                    "kind": v.spec.kind,
                    "n_features": len(v.spec.features),
                },
                "folds": v.fold_info,
            }
            for k, v in models.items()
        },
        "gate": gate,
        "generated_at": datetime.now(UTC).isoformat(),
        "validation_note": "2025-01..2026-09 was inspected in V1 Phase 3; no V2 result on it is untouched/out-of-sample confirmation.",
    }
    res = V2Result(
        manifest=manifest,
        candidates=cands,
        labelled=labelled,
        evaluable=ev,
        folds=folds,
        models=models,
        rank=rank,
        slices=slices,
        halves=halves,
        per_fold_top=per_fold_top,
        top_n=top_n,
        breakdowns=breakdowns,
        derivatives=derivatives_diagnostics(ev, DERIVATIVES_FEATURES),
        coef_stability=coefficient_stability(models["M1_logistic"].coefficients, feats),
        baselines=baselines,
        null=null,
        pit=pit,
        gate=gate,
        criteria=criteria,
        classification=cls,
        population=population,
    )
    _persist(res, out_dir)
    return res


def _pop(df: pl.DataFrame) -> dict[str, Any]:
    from btc_swing.research.phase24 import _stats

    return _stats(df) if df.height else {"n": 0}


def _acct(df: pl.DataFrame, eq: float, s: int, e: int) -> dict[str, Any]:
    from btc_swing.v2.evaluation import sequential_account

    return sequential_account(df, eq, s, e)


def _as_trades(df: pl.DataFrame) -> pl.DataFrame:
    """Candidate label rows in the shape `run_null` expects (trade_id, side, geometry, regime)."""
    return df.select(
        pl.col("candidate_id").alias("trade_id"),
        "side",
        "stop_distance_pct",
        "stop_distance",
        "stop_distance_atr",
        "equity_at_entry",
        "holding_hours",
        "regime_at_entry",
        "R_MULTIPLE",
        "POSITION_PNL",
        "BTC_RETURN",
    )


def _truncation_audit(
    cfg_c: BtcStrategyConfig,
    cfg2: V2Config,
    inp: Phase2Inputs,
    cands: pl.DataFrame,
    start_ms: int,
    cut_ms: int,
) -> dict[str, Any]:
    trunc = inp.bars.filter(pl.col("open_time_ms") + 300_000 <= cut_ms)
    aux_t = AuxSeries.build(
        _cut(inp.funding, "time_ms", cut_ms),
        _cut(inp.metrics, "time_ms", cut_ms),
        _cut(inp.premium, "close_time_ms", cut_ms),
        _cut(inp.mark, "close_time_ms", cut_ms),
        cfg_c.data.latency_minutes,
    )
    series_t = MultiTfSeries(trunc, cfg_c.indicators, cfg_c.regime.trend_slope_bars)
    run_t = generate_candidates(
        cfg_c, series_t, aux_t, start_ms, cut_ms, cfg2.candidates.cooldown_after_candidate_bars
    )
    a = cands.filter(pl.col("trigger_ms") <= cut_ms).sort("trigger_ms")
    b = candidates_frame(run_t).sort("trigger_ms") if run_t.candidates else pl.DataFrame()
    xcols = [c for c in a.columns if c.startswith("x_")]
    same_n = a.height == b.height
    feat_identical = False
    max_diff = math.nan
    if same_n and a.height:
        xa, xb = a.select(xcols).to_numpy().astype(float), b.select(xcols).to_numpy().astype(float)
        both_nan = np.isnan(xa) & np.isnan(xb)
        diff = np.abs(xa - xb)
        diff[both_nan] = 0.0
        max_diff = float(np.nanmax(diff)) if diff.size else 0.0
        feat_identical = bool(np.all((diff <= 1e-9) | both_nan)) and bool(
            np.array_equal(a["trigger_ms"].to_numpy(), b["trigger_ms"].to_numpy())
        )
    return {
        "cut": datetime.fromtimestamp(cut_ms / 1000, tz=UTC).isoformat(),
        "candidates_full_run": a.height,
        "candidates_truncated_run": b.height,
        "identical": same_n and feat_identical,
        "max_abs_feature_diff": max_diff,
        "visibility_rule": "bars with close_time <= t; aux rows with time + latency <= t; features from the MarketView at the trigger bar; labels simulated from the next 5m open",
    }


def _cut(df: pl.DataFrame | None, col: str, cut_ms: int) -> pl.DataFrame | None:
    if df is None or df.is_empty() or col not in df.columns:
        return df
    return df.filter(pl.col(col) <= cut_ms)


def _persist(res: V2Result, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(res.manifest, indent=1, sort_keys=True, default=str)
    )
    summary = {
        "classification": res.classification,
        "criteria": res.criteria,
        "gate": res.gate,
        "population": res.population,
        "rank": res.rank,
        "slices": res.slices,
        "halves": res.halves,
        "per_fold_top": res.per_fold_top,
        "top_n": res.top_n,
        "breakdowns": res.breakdowns,
        "derivatives": res.derivatives,
        "coef_stability": res.coef_stability,
        "baselines": res.baselines,
        "null": res.null,
        "pit": res.pit,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True, default=str)
    )
    for name, df in (
        ("candidates", res.candidates),
        ("labelled", res.labelled),
        ("evaluable", res.evaluable),
    ):
        if not df.is_empty():
            df.write_parquet(out_dir / f"{name}.parquet")
    for name, wf in res.models.items():
        if not wf.predictions.is_empty():
            wf.predictions.write_parquet(out_dir / f"pred_{name}.parquet")
