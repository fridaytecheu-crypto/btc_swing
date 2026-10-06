"""V5 research orchestrator: data availability -> 5m feature frame -> Stage A (event edge, nulls,
gate) -> Stage B (one raw run plus reporting-only streams: overlay, 0.5% risk, Bybit-style cost
sensitivity, leverage caps) -> audits -> nulls -> criteria. Persists manifest, summary and frames.
Nothing here changes a rule after a result is seen."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import polars as pl

from btc_swing.core.config import BtcStrategyConfig
from btc_swing.core.enums import Timeframe
from btc_swing.core.timeframes import tf_ms
from btc_swing.features.context import AuxSeries
from btc_swing.features.view import MultiTfSeries
from btc_swing.research.phase2 import Phase2Inputs, account_stats, load_inputs, resample_oracle
from btc_swing.v5.config import V5Config
from btc_swing.v5.engine import V5Engine, V5Result
from btc_swing.v5.evaluation import (
    chronology,
    classify,
    cost_impact,
    derivatives_context,
    entry_delay,
    evaluate_criteria,
    exits_table,
    family_table,
    frequency,
    funnel,
    holding,
    leverage_audit,
    mfe_mae,
    outliers_ext,
    regime_table,
    sides,
    stop_geometry,
    strength_vs_outcome,
)
from btc_swing.v5.events import build_v5_detectors
from btc_swing.v5.features import FEATURE_COLUMNS, FeatureFrame, V5Inputs, build_feature_frame
from btc_swing.v5.ingest import load_v5_dataset
from btc_swing.v5.null import run_v5_null
from btc_swing.v5.stage_a import (
    scan_events,
    stage_a_gate,
    stage_a_null,
    stage_a_summary,
    unconditional,
)

log = logging.getLogger(__name__)
DAY_MS = 86_400_000
DESIGN_FREEZE_COMMIT = "4e9412a"


def _ms(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp() * 1000)


def _iso(ms: float) -> str:
    return (
        datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")
        if not math.isnan(ms)
        else ""
    )


@dataclass
class V5Research:
    manifest: dict[str, Any]
    raw: V5Result
    overlay: V5Result
    half_pct: V5Result
    bybit: V5Result
    leverage_caps: list[dict[str, Any]]
    events: pl.DataFrame
    stage_a: dict[str, Any]
    stage_a_null: dict[str, Any]
    gate: dict[str, Any]
    overall: dict[str, Any]
    costs: dict[str, Any]
    costs_bybit: dict[str, Any]
    account: dict[str, Any]
    account_overlay: dict[str, Any]
    account_half: dict[str, Any]
    freq: dict[str, Any]
    funnel: dict[str, Any]
    hold: dict[str, Any]
    chrono: dict[str, Any]
    families: list[dict[str, Any]]
    sides: dict[str, Any]
    regimes: list[dict[str, Any]]
    derivatives: list[dict[str, Any]]
    strength: list[dict[str, Any]]
    geometry: dict[str, Any]
    delay: dict[str, Any]
    mfe: dict[str, Any]
    exits: list[dict[str, Any]]
    leverage: dict[str, Any]
    outl: dict[str, Any]
    null: dict[str, Any]
    pit: dict[str, Any]
    coverage: dict[str, Any]
    availability: dict[str, Any]
    episodes: dict[str, Any]
    feature_quality: dict[str, Any]
    collector: dict[str, Any]
    criteria: list[dict[str, Any]]
    classification: str


def load_v5_inputs(cfg: V5Config, data_dir: Path) -> tuple[Phase2Inputs, V5Inputs]:
    end_ms = _ms(cfg.research.end_exclusive)
    inp = load_inputs(cast(BtcStrategyConfig, cfg), data_dir, _ms(cfg.research.start), end_ms)
    flow = load_v5_dataset(data_dir, "aggtrades_flow")
    book = load_v5_dataset(data_dir, "book_depth")
    index = load_v5_dataset(data_dir, "index_klines")
    if not flow.is_empty():
        flow = flow.filter(pl.col("open_time_ms") < end_ms)
    if not book.is_empty():
        book = book.filter(pl.col("time_ms") < end_ms)
    if not index.is_empty():
        index = index.filter(pl.col("open_time_ms") < end_ms)
    return inp, V5Inputs(flow, book, index)


def _stats_all(t: pl.DataFrame) -> dict[str, Any]:
    from btc_swing.research.phase24 import _stats

    return _stats(t) if t.height else {"n": 0}


def _fl(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


def data_availability(
    ff: FeatureFrame, extra: V5Inputs, start_ms: int, end_ms: int
) -> dict[str, Any]:
    """What each V5-specific dataset actually covers inside the study window (no backfill)."""
    k0, k1 = max(0, ff.idx_at(start_ms)), ff.idx_at(end_ms - 1)
    n = k1 - k0 + 1
    out: dict[str, Any] = {"bars_in_window": n}

    def rng(df: pl.DataFrame, col: str) -> dict[str, Any]:
        if df.is_empty():
            return {"rows": 0, "first": "", "last": ""}
        return {
            "rows": df.height,
            "first": _iso(_fl(df[col].min())),
            "last": _iso(_fl(df[col].max())),
        }

    fs = ff.cols["flow_source"][k0 : k1 + 1]
    out["aggtrades_flow"] = {
        **rng(extra.flow, "open_time_ms"),
        "share_of_bars_in_window": float(np.mean(fs == 1.0)),
        "share_kline_fallback": float(np.mean(fs == 0.0)),
        "note": "5m aggregates of archive aggTrades (taker side from is_buyer_maker); raw trade rows not retained, sha256 of every archive file recorded",
    }
    b1 = ff.cols["bid1"][k0 : k1 + 1]
    out["book_depth"] = {
        **rng(extra.book, "time_ms"),
        "share_of_bars_in_window": float(np.mean(~np.isnan(b1))),
        "note": "archive bookDepth snapshots (+-1..5% depth, ~30 s cadence) from 2023-01-01; last snapshot <= T; diagnostics only, no event uses it",
    }
    ix = ff.cols["index_close"][k0 : k1 + 1]
    out["index_klines"] = {
        **rng(extra.index, "open_time_ms"),
        "share_of_bars_in_window": float(np.mean(~np.isnan(ix))),
        "note": "5m index klines for the basis feature",
    }
    oi = ff.cols["oi"][k0 : k1 + 1]
    out["open_interest"] = {
        "share_of_bars_in_window": float(np.mean(~np.isnan(oi))),
        "note": "archive metrics (5m), observation time + latency <= T",
    }
    out["liquidations"] = {
        "rows": 0,
        "share_of_bars_in_window": 0.0,
        "note": "NOT AVAILABLE historically (no archive dataset for BTCUSDT UM); forward only via the Bybit collector; family A uses the labelled OI-flush proxy",
    }
    out["funding"] = {
        "share_of_bars_in_window": float(np.mean(~np.isnan(ff.cols["fund_z"][k0 : k1 + 1])))
    }
    out["premium"] = {
        "share_of_bars_in_window": float(np.mean(~np.isnan(ff.cols["prem_z"][k0 : k1 + 1])))
    }
    return out


def run_v5_research(
    cfg: V5Config,
    inp: Phase2Inputs,
    extra: V5Inputs,
    out_dir: Path,
    collector_stats: dict[str, Any] | None = None,
) -> V5Research:
    start_ms, end_ms = _ms(cfg.research.start), _ms(cfg.research.end_exclusive)
    aux = AuxSeries.build(inp.funding, inp.metrics, inp.premium, inp.mark, cfg.data.latency_minutes)
    series = MultiTfSeries(inp.bars, cfg.indicators, 5)
    log.info("feature frame (5m)")
    ff = build_feature_frame(series, aux, cfg, extra)
    fq = _feature_quality(ff, start_ms, end_ms)
    avail = data_availability(ff, extra, start_ms, end_ms)
    log.info("Stage A: event scan")
    dets = build_v5_detectors(cfg, ff)
    events = scan_events(ff, dets, cfg, start_ms, end_ms)
    uncond = unconditional(ff, cfg, start_ms, end_ms)
    sa = stage_a_summary(events, cfg, uncond)
    sa_null = stage_a_null(events, ff, cfg, start_ms, end_ms)
    gate = stage_a_gate(sa, cfg)
    log.info("Stage B: raw run (%d events, gate passed=%s)", events.height, gate.get("passed"))
    eng = V5Engine(cfg, inp.bars, inp.funding, inp.hashes, aux, series, ff)
    raw = eng.run(start_ms, end_ms, notes={"stream": "raw_default_risk"})
    log.info("determinism rerun")
    rerun = V5Engine(cfg, inp.bars, inp.funding, inp.hashes, aux, series, ff).run(start_ms, end_ms)
    log.info("overlay / 0.5%% / Bybit sensitivity")
    overlay = V5Engine(
        cfg, inp.bars, inp.funding, inp.hashes, aux, series, ff, safety_overlay=True
    ).run(start_ms, end_ms, notes={"stream": "safety_overlay"})
    cfg_half = cfg.model_copy(
        update={
            "risk": cfg.risk.model_copy(update={"risk_per_trade": cfg.risk.report_risk_per_trade})
        }
    )
    half = V5Engine(cfg_half, inp.bars, inp.funding, inp.hashes, aux, series, ff).run(
        start_ms, end_ms, notes={"stream": "report_risk_0_5pct"}
    )
    bybit = V5Engine(
        cfg, inp.bars, inp.funding, inp.hashes, aux, series, ff, costs_cfg=cfg.cost_sensitivity
    ).run(start_ms, end_ms, notes={"stream": "cost_sensitivity_bybit"})
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
        r = V5Engine(c2, inp.bars, inp.funding, inp.hashes, aux, series, ff).run(start_ms, end_ms)
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
            }
        )
    t = raw.trades
    overall = _stats_all(t)
    costs = cost_impact(t) if t.height else {}
    costs_bybit = cost_impact(bybit.trades) if bybit.trades.height else {}
    account = account_stats(t, raw.daily_equity, cfg.risk.initial_equity, start_ms, end_ms)
    account_overlay = account_stats(
        overlay.trades, overlay.daily_equity, cfg.risk.initial_equity, start_ms, end_ms
    )
    account_half = account_stats(
        half.trades, half.daily_equity, cfg.risk.initial_equity, start_ms, end_ms
    )
    freq = frequency(t, start_ms, end_ms)
    fun = funnel(events, raw.episodes, t, raw.blocked, start_ms, end_ms)
    hold = holding(t)
    chrono = chronology(t, cfg.risk.initial_equity, start_ms, end_ms)
    fams = family_table(t, events, raw.episodes, cfg.risk.initial_equity, start_ms, end_ms)
    outl = outliers_ext(
        t,
        chrono,
        [
            {
                "family": f["family"],
                "side": f["side"],
                "n": f.get("n", 0),
                "sum_pnl": f.get("sum_pnl", 0.0),
            }
            for f in fams
        ],
    )
    log.info("null benchmark (K=%d)", cfg.research.null_k)
    null = (
        run_v5_null(eng, t, raw.decisions, start_ms, end_ms, cfg.research.null_k, cfg.research.seed)
        if t.height
        else {"n_trades": 0}
    )
    log.info("PIT audit")
    pit = _pit_audit(cfg, inp, extra, aux, raw, events, start_ms, end_ms)
    ep = raw.episodes
    episodes = {
        "n": ep.height,
        "by_outcome": ep.group_by("end_reason").len().sort("len", descending=True).to_dicts()
        if ep.height
        else [],
        "by_family": ep.group_by("family").len().sort("family").to_dicts() if ep.height else [],
        "blocked": raw.blocked,
        "events_total": events.height,
        "events_first_in_cluster": int(events["first_in_cluster"].sum()) if events.height else 0,
    }
    criteria = evaluate_criteria(cfg, gate, overall, costs, account, freq, hold, chrono, outl, null)
    cls = classify(cfg, criteria, costs)
    manifest = {
        "project": "btc_swing_v5",
        "config_hash": cfg.config_hash,
        "config_yaml": cfg.canonical_yaml(),
        "design_freeze_commit": DESIGN_FREEZE_COMMIT,
        "raw_manifest": raw.manifest,
        "overlay_manifest": overlay.manifest,
        "half_pct_manifest": half.manifest,
        "bybit_manifest": bybit.manifest,
        "determinism_identical": rerun.result_hash == raw.result_hash,
        "coverage": inp.coverage,
        "availability": avail,
        "feature_columns": FEATURE_COLUMNS,
        "generated_at": datetime.now(UTC).isoformat(),
        "validation_note": "2022-01..2026-09 is development data inspected by V1-V4; no V5 result on it is untouched out-of-sample. Dataset start dates are disclosed; nothing is backfilled.",
    }
    res = V5Research(
        manifest,
        raw,
        overlay,
        half,
        bybit,
        caps,
        events,
        sa,
        sa_null,
        gate,
        overall,
        costs,
        costs_bybit,
        account,
        account_overlay,
        account_half,
        freq,
        fun,
        hold,
        chrono,
        fams,
        sides(t),
        regime_table(t),
        derivatives_context(t),
        strength_vs_outcome(t),
        stop_geometry(t, cfg),
        entry_delay(t),
        mfe_mae(t),
        exits_table(t),
        leverage_audit(t),
        outl,
        null,
        pit,
        inp.coverage,
        avail,
        episodes,
        fq,
        collector_stats or {},
        criteria,
        cls,
    )
    _persist(res, out_dir, ff)
    return res


def _feature_quality(ff: FeatureFrame, start_ms: int, end_ms: int) -> dict[str, Any]:
    k0, k1 = max(0, ff.idx_at(start_ms)), ff.idx_at(end_ms - 1)
    out: dict[str, Any] = {"rows_in_window": k1 - k0 + 1}
    for c in FEATURE_COLUMNS:
        a = ff.cols[c][k0 : k1 + 1]
        out[c] = {
            "missing_share": float(np.isnan(a).mean()),
            "mean": float(np.nanmean(a)) if (~np.isnan(a)).any() else math.nan,
        }
    return out


def _pit_audit(
    cfg: V5Config,
    inp: Phase2Inputs,
    extra: V5Inputs,
    aux: AuxSeries,
    raw: V5Result,
    events: pl.DataFrame,
    start_ms: int,
    end_ms: int,
) -> dict[str, Any]:
    cut = start_ms + (end_ms - start_ms) // 2
    trunc = inp.bars.filter(pl.col("open_time_ms") + tf_ms(Timeframe.M5) <= cut)

    def _cut(df: pl.DataFrame | None, col: str) -> pl.DataFrame | None:
        return (
            df
            if df is None or df.is_empty() or col not in df.columns
            else df.filter(pl.col(col) <= cut)
        )

    def _cut2(df: pl.DataFrame, col: str) -> pl.DataFrame:
        return df if df.is_empty() else df.filter(pl.col(col) <= cut)

    aux_t = AuxSeries.build(
        _cut(inp.funding, "time_ms"),
        _cut(inp.metrics, "time_ms"),
        _cut(inp.premium, "close_time_ms"),
        _cut(inp.mark, "close_time_ms"),
        cfg.data.latency_minutes,
    )
    extra_t = V5Inputs(
        _cut2(extra.flow, "open_time_ms"),
        _cut2(extra.book, "time_ms"),
        _cut2(extra.index, "open_time_ms"),
    )
    series_t = MultiTfSeries(trunc, cfg.indicators, 5)
    ff_t = build_feature_frame(series_t, aux_t, cfg, extra_t)
    eng_t = V5Engine(cfg, trunc, _cut(inp.funding, "time_ms"), inp.hashes, aux_t, series_t, ff_t)
    res_t = eng_t.run(start_ms, cut)
    cols = ["t_ms", "regime", "position_open"]
    a = raw.decisions.filter(pl.col("t_ms") <= cut).select(cols)
    b = res_t.decisions.filter(pl.col("t_ms") <= cut).select(cols)
    ev_t = scan_events(ff_t, build_v5_detectors(cfg, ff_t), cfg, start_ms, cut)
    sel = ["family", "side", "t_ms", "strength"]
    ev_a = events.filter(pl.col("t_ms") <= cut - DAY_MS).select(sel) if events.height else events
    ev_b = ev_t.filter(pl.col("t_ms") <= cut - DAY_MS).select(sel) if ev_t.height else ev_t
    ev_same = ev_a.height == ev_b.height and (
        ev_a.height == 0
        or (
            bool(np.allclose(ev_a["strength"].to_numpy(), ev_b["strength"].to_numpy(), atol=1e-9))
            and ev_a["t_ms"].to_list() == ev_b["t_ms"].to_list()
        )
    )
    return {
        "visibility_rule": "5m/1H/4H bars with close_time <= t; aggTrades aggregates of the same bar; last order-book snapshot <= t; metrics/funding/premium rows with time + latency <= t; index kline of the same bar; rolling z-scores over previous rows only; fill at the next 5m open; trail moves applied from the next bar",
        "truncation": {
            "cut": datetime.fromtimestamp(cut / 1000, tz=UTC).isoformat(),
            "rows_compared": a.height,
            "decisions_identical": a.height == b.height and a.equals(b),
            "events_compared": ev_a.height,
            "events_identical": ev_same,
        },
        "resample_oracle": resample_oracle(inp.bars, inp.natives),
        "liquidation_basis": raw.manifest["liquidation_basis"],
    }


def _persist(res: V5Research, out_dir: Path, ff: FeatureFrame) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(res.manifest, indent=1, sort_keys=True, default=str)
    )
    summary = {
        k: getattr(res, k)
        for k in (
            "classification",
            "criteria",
            "gate",
            "stage_a",
            "stage_a_null",
            "overall",
            "costs",
            "costs_bybit",
            "account",
            "account_overlay",
            "account_half",
            "freq",
            "funnel",
            "hold",
            "chrono",
            "families",
            "sides",
            "regimes",
            "derivatives",
            "strength",
            "geometry",
            "delay",
            "mfe",
            "exits",
            "leverage",
            "outl",
            "null",
            "pit",
            "coverage",
            "availability",
            "episodes",
            "feature_quality",
            "collector",
            "leverage_caps",
        )
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True, default=str)
    )
    for name, r in (
        ("raw", res.raw),
        ("overlay", res.overlay),
        ("half_pct", res.half_pct),
        ("bybit", res.bybit),
    ):
        for kind, df in (
            ("trades", r.trades),
            ("episodes", r.episodes),
            ("daily_equity", r.daily_equity),
        ):
            if not df.is_empty():
                df.write_parquet(out_dir / f"{name}_{kind}.parquet")
    if not res.raw.decisions.is_empty():
        res.raw.decisions.write_parquet(out_dir / "raw_decisions.parquet")
    if not res.events.is_empty():
        res.events.write_parquet(out_dir / "events.parquet")
    ff.to_frame().write_parquet(out_dir / "feature_frame_5m.parquet")
