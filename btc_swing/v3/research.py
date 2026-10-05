"""V3 research orchestrator: one pre-registered configuration, run once (raw 0.25%), plus the
reporting-only streams (safety overlay, 0.5% risk, leverage caps), PIT audits and null
benchmarks. Persists manifest, summary and frames; the report is rendered by `v3/report.py`."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import polars as pl

from btc_swing.core.config import BtcStrategyConfig
from btc_swing.core.enums import Timeframe
from btc_swing.core.timeframes import tf_ms
from btc_swing.features.context import AuxSeries
from btc_swing.features.view import MultiTfSeries
from btc_swing.research.phase2 import Phase2Inputs, load_inputs, resample_oracle
from btc_swing.v3.config import V3Config
from btc_swing.v3.engine import V3Engine, V3Result
from btc_swing.v3.evaluation import (
    account_stats,
    chronology,
    classify,
    cost_impact,
    derivatives_context,
    evaluate_criteria,
    exits_table,
    family_table,
    frequency,
    holding,
    leverage_audit,
    mfe_mae,
    outliers,
    regime_table,
)
from btc_swing.v3.null import run_v3_null

log = logging.getLogger(__name__)


def _ms(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp() * 1000)


@dataclass
class V3Research:
    manifest: dict[str, Any]
    raw: V3Result
    overlay: V3Result
    half_pct: V3Result
    leverage_caps: list[dict[str, Any]]
    overall: dict[str, Any]
    costs: dict[str, Any]
    account: dict[str, Any]
    account_overlay: dict[str, Any]
    account_half: dict[str, Any]
    freq: dict[str, Any]
    hold: dict[str, Any]
    chrono: dict[str, Any]
    families: list[dict[str, Any]]
    regimes: list[dict[str, Any]]
    derivatives: list[dict[str, Any]]
    mfe: dict[str, Any]
    exits: list[dict[str, Any]]
    leverage: dict[str, Any]
    outl: dict[str, Any]
    null: dict[str, Any]
    pit: dict[str, Any]
    coverage: dict[str, Any]
    episodes: dict[str, Any]
    criteria: list[dict[str, Any]]
    classification: str
    sides: dict[str, Any] = field(default_factory=dict)


def load_v3_inputs(cfg: V3Config, data_dir: Path) -> Phase2Inputs:
    return load_inputs(
        cast(BtcStrategyConfig, cfg),
        data_dir,
        _ms(cfg.research.start),
        _ms(cfg.research.end_exclusive),
    )


def _engine(
    cfg: V3Config, inp: Phase2Inputs, aux: AuxSeries, series: MultiTfSeries, overlay: bool = False
) -> V3Engine:
    return V3Engine(cfg, inp.bars, inp.funding, inp.hashes, aux, series, safety_overlay=overlay)


def _stats_all(t: pl.DataFrame) -> dict[str, Any]:
    from btc_swing.research.phase24 import _stats

    return _stats(t) if t.height else {"n": 0}


def run_v3_research(cfg: V3Config, inp: Phase2Inputs, out_dir: Path) -> V3Research:
    start_ms, end_ms = _ms(cfg.research.start), _ms(cfg.research.end_exclusive)
    aux = AuxSeries.build(inp.funding, inp.metrics, inp.premium, inp.mark, cfg.data.latency_minutes)
    series = MultiTfSeries(inp.bars, cfg.indicators, cfg.regime.d1_slope_bars)
    log.info("V3 raw run (0.25%%)")
    eng = _engine(cfg, inp, aux, series)
    raw = eng.run(start_ms, end_ms, notes={"stream": "raw_default_risk"})
    log.info("determinism rerun")
    rerun = _engine(cfg, inp, aux, series).run(start_ms, end_ms)
    log.info("safety overlay run")
    overlay = _engine(cfg, inp, aux, series, overlay=True).run(
        start_ms, end_ms, notes={"stream": "safety_overlay"}
    )
    log.info("0.5%% risk run (reporting only)")
    cfg_half = cfg.model_copy(
        update={
            "risk": cfg.risk.model_copy(update={"risk_per_trade": cfg.risk.report_risk_per_trade})
        }
    )
    half = _engine(cfg_half, inp, aux, series).run(
        start_ms, end_ms, notes={"stream": "report_risk_0_5pct"}
    )
    log.info("leverage caps")
    caps: list[dict[str, Any]] = []
    for lv in cfg.research.leverage_caps:
        allowed = [x for x in cfg.risk.allowed_leverage if x <= lv] or [lv]
        c2 = cfg.model_copy(
            update={
                "risk": cfg.risk.model_copy(
                    update={"max_leverage": lv, "allowed_leverage": allowed}
                )
            }
        )
        r = _engine(c2, inp, aux, series).run(start_ms, end_ms)
        t = r.trades
        acct = account_stats(t, r.daily_equity, cfg.risk.initial_equity, start_ms, end_ms)
        caps.append(
            {
                "max_leverage": lv,
                "n_trades": t.height,
                "risk_rejected": r.blocked.get("RISK_REJECTED", 0),
                "mean_leverage": _fl(t["leverage"].mean()) if t.height else math.nan,
                "mean_R": _fl(t["R_MULTIPLE"].mean()) if t.height else math.nan,
                "total_return": acct.get("total_return"),
                "max_dd": acct.get("max_drawdown_frac_trade_curve"),
                "min_liq_distance_pct": 100 * _fl(t["liquidation_distance_pct"].min())
                if t.height
                else math.nan,
                "liquidations": int((t["exit_reason"] == "LIQUIDATION").sum()) if t.height else 0,
                "result_hash": r.result_hash,
            }
        )
    t = raw.trades
    overall = _stats_all(t)
    costs = cost_impact(t) if t.height else {}
    account = account_stats(t, raw.daily_equity, cfg.risk.initial_equity, start_ms, end_ms)
    account_overlay = account_stats(
        overlay.trades, overlay.daily_equity, cfg.risk.initial_equity, start_ms, end_ms
    )
    account_half = account_stats(
        half.trades, half.daily_equity, cfg.risk.initial_equity, start_ms, end_ms
    )
    freq = frequency(t, start_ms, end_ms)
    hold = holding(t)
    chrono = chronology(t, cfg.risk.initial_equity, start_ms, end_ms)
    fams = family_table(t, cfg.risk.initial_equity, start_ms, end_ms)
    outl = outliers(t, chrono, fams)
    log.info("null benchmark (K=%d)", cfg.research.null_k)
    null = (
        run_v3_null(eng, t, raw.decisions, start_ms, end_ms, cfg.research.null_k, cfg.research.seed)
        if t.height
        else {"n_trades": 0}
    )
    log.info("PIT audit")
    cut = start_ms + (end_ms - start_ms) // 2
    trunc = inp.bars.filter(pl.col("open_time_ms") + tf_ms(Timeframe.M5) <= cut)
    res_t = V3Engine(cfg, trunc, inp.funding, inp.hashes, aux).run(start_ms, cut)
    cols = ["t_ms", "regime", "position_open"]
    a = raw.decisions.filter(pl.col("t_ms") <= cut).select(cols)
    b = res_t.decisions.filter(pl.col("t_ms") <= cut).select(cols)
    pit = {
        "visibility_rule": "bar visible iff close_time <= t; aux rows iff time + latency <= t; funding in (prev close, t]; fill at the next 5m open; trail moves applied from the next bar",
        "deterministic": rerun.result_hash == raw.result_hash,
        "truncation": {
            "cut": datetime.fromtimestamp(cut / 1000, tz=UTC).isoformat(),
            "rows_compared": a.height,
            "identical": a.height == b.height and a.equals(b),
        },
        "resample_oracle": resample_oracle(inp.bars, inp.natives),
        "liquidation_basis": raw.manifest["liquidation_basis"],
    }
    ep = raw.episodes
    episodes = {
        "n": ep.height,
        "by_outcome": ep.group_by("end_reason").len().sort("len", descending=True).to_dicts()
        if ep.height
        else [],
        "by_family_outcome": ep.group_by("family", "outcome_class")
        .len()
        .sort("family", "outcome_class")
        .to_dicts()
        if ep.height
        else [],
        "detected_by_family": ep.group_by("family").len().sort("family").to_dicts()
        if ep.height
        else [],
        "blocked": raw.blocked,
    }
    sides = (
        {s: _stats_all(t.filter(pl.col("side") == s)) for s in ("LONG", "SHORT")}
        if t.height
        else {}
    )
    for s, d in sides.items():
        if d.get("n"):
            ci = cost_impact(t.filter(pl.col("side") == s))
            d.update(
                {
                    "gross_mean_R": ci.get("expectancy_R_before_costs"),
                    "cost_drag_R": ci.get("cost_drag_R_per_trade"),
                }
            )
    criteria = evaluate_criteria(cfg, overall, costs, account, freq, hold, chrono, outl, null)
    cls = classify(cfg, criteria, overall, chrono, freq, outl)
    manifest = {
        "project": "btc_swing_v3",
        "config_hash": cfg.config_hash,
        "config_yaml": cfg.canonical_yaml(),
        "raw_manifest": raw.manifest,
        "overlay_manifest": overlay.manifest,
        "half_pct_manifest": half.manifest,
        "determinism_identical": rerun.result_hash == raw.result_hash,
        "coverage": inp.coverage,
        "generated_at": datetime.now(UTC).isoformat(),
        "validation_note": "2022-01..2026-09 is development data (inspected in V1/V2); no V3 result on it is untouched out-of-sample.",
    }
    res = V3Research(
        manifest,
        raw,
        overlay,
        half,
        caps,
        overall,
        costs,
        account,
        account_overlay,
        account_half,
        freq,
        hold,
        chrono,
        fams,
        regime_table(t),
        derivatives_context(t),
        mfe_mae(t),
        exits_table(t),
        leverage_audit(t),
        outl,
        null,
        pit,
        inp.coverage,
        episodes,
        criteria,
        cls,
        sides,
    )
    _persist(res, out_dir)
    return res


def _fl(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


def _persist(res: V3Research, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(res.manifest, indent=1, sort_keys=True, default=str)
    )
    summary = {
        k: getattr(res, k)
        for k in (
            "classification",
            "criteria",
            "overall",
            "costs",
            "account",
            "account_overlay",
            "account_half",
            "freq",
            "hold",
            "chrono",
            "families",
            "regimes",
            "derivatives",
            "mfe",
            "exits",
            "leverage",
            "outl",
            "null",
            "pit",
            "coverage",
            "episodes",
            "sides",
            "leverage_caps",
        )
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True, default=str)
    )
    for name, r in (("raw", res.raw), ("overlay", res.overlay), ("half_pct", res.half_pct)):
        for kind, df in (
            ("trades", r.trades),
            ("episodes", r.episodes),
            ("daily_equity", r.daily_equity),
        ):
            if not df.is_empty():
                df.write_parquet(out_dir / f"{name}_{kind}.parquet")
    if not res.raw.decisions.is_empty():
        res.raw.decisions.write_parquet(out_dir / "raw_decisions.parquet")
