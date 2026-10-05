"""Phase 2.4 — SHORT REGIME ELIGIBILITY. The final planned Phase 2 structural hypothesis.

CONTROL  = frozen Phase 2 (result hash must equal the Phase 2 validation run).
VARIANT  = NO_NEW_SHORT_IN_TREND_DOWN: `experiment.block_short_in_trend_down = true`. When a SHORT
           trigger fires while the PIT regime at that decision bar is TREND_DOWN, the trade is not
           opened (episode ends REGIME_BLOCKED). Uniform across all SHORT families; open positions
           are never closed by the rule; everything else frozen.
Analysis: removed CONTROL SHORT trades (matched on family + detection time to variant episodes
ending REGIME_BLOCKED), opportunity cost, remaining SHORT population, LONG control check, and the
split between the direct effect of the removed trades and the secondary single-slot sequencing
effect.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from btc_swing.core.config import BtcStrategyConfig
from btc_swing.features.context import AuxSeries
from btc_swing.features.view import MultiTfSeries
from btc_swing.research.phase2 import Phase2Inputs, Segment, truncation_audit
from btc_swing.research.phase21 import KEY, Arm, baseline_row_check, run_arm

log = logging.getLogger(__name__)
VARIANT = "NO_NEW_SHORT_IN_TREND_DOWN"
DAY_MS = 86_400_000


def _f(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


def _stats(t: pl.DataFrame) -> dict[str, Any]:
    if t.is_empty():
        return {"n": 0}
    pnl = t["POSITION_PNL"].to_numpy().astype(float)
    r = t["R_MULTIPLE"].to_numpy().astype(float)
    gw, gl = float(pnl[pnl > 0].sum()), float(-pnl[pnl <= 0].sum())
    return {
        "n": t.height,
        "win_rate": float((pnl > 0).mean()),
        "mean_R": float(r.mean()),
        "median_R": float(np.median(r)),
        "sum_R": float(r.sum()),
        "profit_factor": gw / gl if gl > 0 else math.inf,
        "mean_MFE_R": _f(t["MFE_R"].mean()),
        "mean_MAE_R": _f(t["MAE_R"].mean()),
        "mean_holding_hours": _f(t["holding_hours"].mean()),
        "sum_pnl": float(pnl.sum()),
        "target_first_share": _f((t["path_outcome"] == "target_first").cast(pl.Float64).mean())
        if "path_outcome" in t.columns
        else math.nan,
        "cf_hit_1_5R_share": _f(t["cf_hit_1.5R"].cast(pl.Float64).mean())
        if "cf_hit_1.5R" in t.columns
        else math.nan,
        "t_stat": float(r.mean() / (r.std(ddof=1) / math.sqrt(len(r))))
        if len(r) > 1 and r.std(ddof=1) > 0
        else math.nan,
    }


def _by(t: pl.DataFrame, col: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if t.is_empty() or col not in t.columns:
        return out
    for k in sorted(t[col].fill_null("n/a").unique().to_list()):
        sub = t.filter(pl.col(col).fill_null("n/a") == k)
        out.append({col: k, **_stats(sub)})
    return out


def _segment_of(ms: int, segments: list[Segment]) -> str:
    for s in segments:
        if s.start_ms <= ms < s.end_ms:
            return s.name
    return "other"


def _keyed_trades(arm: Arm) -> pl.DataFrame:
    eps = arm.episodes.select(
        ["episode_id", *KEY, "regime_at_detection", "outcome_class", "end_reason"]
    )
    return arm.trades.join(
        eps.drop("outcome_class", "end_reason")
        if "regime_at_detection" in arm.trades.columns
        else eps,
        on="episode_id",
        how="left",
        suffix="_ep",
    )


def removed_trade_analysis(control: Arm, variant: Arm, segments: list[Segment]) -> dict[str, Any]:
    ct = control.trades.join(
        control.episodes.select(["episode_id", *KEY]), on="episode_id", how="left"
    )
    ve = variant.episodes.select(
        [
            *KEY,
            pl.col("outcome_class").alias("variant_outcome"),
            pl.col("end_reason").alias("variant_end_reason"),
            pl.col("episode_id").alias("variant_episode_id"),
        ]
    )
    j = ct.join(ve, on=KEY, how="left").with_columns(
        pl.col("variant_outcome").fill_null("UNMATCHED")
    )
    j = j.with_columns(
        pl.col("entry_ms")
        .map_elements(lambda x: _segment_of(int(x), segments), return_dtype=pl.Utf8)
        .alias("segment")
    )
    shorts = j.filter(pl.col("side") == "SHORT")
    removed = shorts.filter(pl.col("variant_outcome") == "REGIME_BLOCKED")
    kept = shorts.filter(pl.col("variant_outcome") == "TRADED")
    unmatched_short = shorts.filter(~pl.col("variant_outcome").is_in(["REGIME_BLOCKED", "TRADED"]))
    out: dict[str, Any] = {
        "control_short_trades": shorts.height,
        "removed_by_rule": removed.height,
        "kept_in_variant": kept.height,
        "short_lost_to_sequencing_or_other": unmatched_short.height,
        "unmatched_short_outcomes": unmatched_short.group_by("variant_outcome")
        .len()
        .sort("variant_outcome")
        .to_dicts()
        if unmatched_short.height
        else [],
        "removed_regime_at_trigger": removed.group_by("regime_at_trigger")
        .len()
        .sort("len", descending=True)
        .to_dicts()
        if removed.height
        else [],
        "removed_stats": _stats(removed),
        "kept_stats": _stats(kept),
        "removed_by_family": _by(removed, "family"),
        "removed_by_segment": _by(removed, "segment"),
        "removed_by_regime_at_detection": _by(removed, "regime_at_detection"),
        "removed_by_family_segment": removed.group_by("family", "segment")
        .agg(
            pl.len().alias("n"),
            pl.col("R_MULTIPLE").mean().alias("mean_R"),
            pl.col("R_MULTIPLE").sum().alias("sum_R"),
            pl.col("POSITION_PNL").sum().alias("sum_pnl"),
        )
        .sort("family", "segment")
        .to_dicts()
        if removed.height
        else [],
    }
    if removed.height:
        r = removed["R_MULTIPLE"].to_numpy().astype(float)
        pnl = removed["POSITION_PNL"].to_numpy().astype(float)
        win, los = r[r > 0], r[r <= 0]
        neg_sorted = np.sort(los)
        out["opportunity_cost"] = {
            "winners_removed": len(win),
            "losers_removed": len(los),
            "positive_R_removed": float(win.sum()),
            "negative_R_avoided": float(-los.sum()),
            "net_R_effect_of_removal": float(-r.sum()),
            "net_pnl_effect_of_removal": float(-pnl.sum()),
            "winners_pnl_removed": float(pnl[pnl > 0].sum()),
            "losers_pnl_avoided": float(-pnl[pnl <= 0].sum()),
            "median_removed_R": float(np.median(r)),
            "worst3_share_of_negative_R": float(-neg_sorted[:3].sum() / -los.sum())
            if len(los) >= 3 and los.sum() < 0
            else math.nan,
            "best3_share_of_positive_R": float(np.sort(win)[-3:].sum() / win.sum())
            if len(win) >= 3 and win.sum() > 0
            else math.nan,
            "removed_R_quantiles": {
                q: float(np.quantile(r, q)) for q in (0.1, 0.25, 0.5, 0.75, 0.9)
            },
            "removed_without_worst3_mean_R": float(np.sort(r)[3:].mean())
            if len(r) > 3
            else math.nan,
        }
        out["removed_rows"] = removed.select(
            "trade_id",
            "family",
            "entry_ms",
            "segment",
            "regime_at_detection",
            "regime_at_trigger",
            "R_MULTIPLE",
            "POSITION_PNL",
            "MFE_R",
            "MAE_R",
            "holding_hours",
            "exit_reason",
            *[c for c in ("path_outcome", "cf_hit_1.5R", "cf_hit_2R") if c in removed.columns],
        ).sort("entry_ms")
    return out


def remaining_short(variant: Arm, control: Arm) -> dict[str, Any]:
    vt = (
        variant.trades.filter(pl.col("side") == "SHORT")
        if variant.trades.height
        else variant.trades
    )
    ct = (
        control.trades.filter(pl.col("side") == "SHORT")
        if control.trades.height
        else control.trades
    )
    out: dict[str, Any] = {
        "variant": _stats(vt),
        "control": _stats(ct),
        "variant_by_family": _by(vt, "family"),
        "control_by_family": _by(ct, "family"),
        "variant_by_regime_at_trigger": _by(vt, "regime_at_trigger"),
    }
    # drawdown-window contribution of SHORT trades in the variant
    t = variant.trades.sort("entry_ms")
    if t.height:
        eq = variant.cfg.risk.initial_equity + np.cumsum(t["POSITION_PNL"].to_numpy().astype(float))
        curve = np.concatenate([[variant.cfg.risk.initial_equity], eq])
        peak = np.maximum.accumulate(curve)
        trough_i = int(np.argmin(curve - peak))
        peak_i = int(np.argmax(curve[: trough_i + 1]))
        window = t.slice(peak_i, trough_i - peak_i)
        out["variant_dd_window"] = {
            "n_trades": window.height,
            "short_pnl_in_window": _f(
                window.filter(pl.col("side") == "SHORT")["POSITION_PNL"].sum()
            ),
            "long_pnl_in_window": _f(window.filter(pl.col("side") == "LONG")["POSITION_PNL"].sum()),
            "max_dd_currency": float((curve - peak).min()),
        }
    return out


def long_control_check(control: Arm, variant: Arm) -> dict[str, Any]:
    cl = control.trades.filter(pl.col("side") == "LONG").join(
        control.episodes.select(["episode_id", *KEY]), on="episode_id", how="left"
    )
    vl = variant.trades.filter(pl.col("side") == "LONG").join(
        variant.episodes.select(["episode_id", *KEY]), on="episode_id", how="left"
    )
    j = cl.join(vl, on=KEY, how="full", suffix="_v", coalesce=True)
    both = j.filter(pl.col("trade_id").is_not_null() & pl.col("trade_id_v").is_not_null())
    c_only = j.filter(pl.col("trade_id_v").is_null())
    v_only = j.filter(pl.col("trade_id").is_null())
    diff_r = (both["R_MULTIPLE_v"] - both["R_MULTIPLE"]).abs() if both.height else pl.Series([])
    return {
        "control_long": cl.height,
        "variant_long": vl.height,
        "paired_long": both.height,
        "paired_identical_R": int((diff_r <= 1e-9).sum()) if both.height else 0,
        "paired_different_R": int((diff_r > 1e-9).sum()) if both.height else 0,
        "control_only_long": c_only.height,
        "control_only_long_pnl": _f(c_only["POSITION_PNL"].sum()) if c_only.height else 0.0,
        "control_only_long_mean_R": _f(c_only["R_MULTIPLE"].mean()) if c_only.height else math.nan,
        "variant_only_long": v_only.height,
        "variant_only_long_pnl": _f(v_only["POSITION_PNL_v"].sum()) if v_only.height else 0.0,
        "variant_only_long_mean_R": _f(v_only["R_MULTIPLE_v"].mean())
        if v_only.height
        else math.nan,
        "control_long_stats": _stats(control.trades.filter(pl.col("side") == "LONG")),
        "variant_long_stats": _stats(variant.trades.filter(pl.col("side") == "LONG")),
    }


def sequencing_split(
    control: Arm, variant: Arm, removed: dict[str, Any], longs: dict[str, Any]
) -> dict[str, Any]:
    total = (variant.costs.get("net", 0.0) or 0.0) - (control.costs.get("net", 0.0) or 0.0)
    direct = (removed.get("opportunity_cost") or {}).get("net_pnl_effect_of_removal", 0.0) or 0.0
    # variant-only trades (new trades enabled by freed slots), any side
    ct = control.trades.join(
        control.episodes.select(["episode_id", *KEY]), on="episode_id", how="left"
    )
    vt = variant.trades.join(
        variant.episodes.select(["episode_id", *KEY]), on="episode_id", how="left"
    )
    v_only = vt.join(ct.select(KEY), on=KEY, how="anti")
    c_only_not_blocked = ct.join(vt.select(KEY), on=KEY, how="anti")
    blocked_keys = variant.episodes.filter(pl.col("outcome_class") == "REGIME_BLOCKED").select(KEY)
    c_only_not_blocked = c_only_not_blocked.join(blocked_keys, on=KEY, how="anti")
    return {
        "total_net_pnl_change": total,
        "direct_effect_removed_trades": direct,
        "secondary_effect": total - direct,
        "variant_only_trades": {
            "n": v_only.height,
            "pnl": _f(v_only["POSITION_PNL"].sum()) if v_only.height else 0.0,
            "by_side": v_only.group_by("side")
            .agg(
                pl.len().alias("n"),
                pl.col("POSITION_PNL").sum().alias("pnl"),
                pl.col("R_MULTIPLE").mean().alias("mean_R"),
            )
            .sort("side")
            .to_dicts()
            if v_only.height
            else [],
        },
        "control_only_trades_not_blocked": {
            "n": c_only_not_blocked.height,
            "pnl": _f(c_only_not_blocked["POSITION_PNL"].sum())
            if c_only_not_blocked.height
            else 0.0,
            "by_side": c_only_not_blocked.group_by("side")
            .agg(
                pl.len().alias("n"),
                pl.col("POSITION_PNL").sum().alias("pnl"),
                pl.col("R_MULTIPLE").mean().alias("mean_R"),
            )
            .sort("side")
            .to_dicts()
            if c_only_not_blocked.height
            else [],
        },
    }


@dataclass
class Phase24Result:
    manifest: dict[str, Any]
    control: Arm
    variant: Arm
    removed: dict[str, Any]
    remaining: dict[str, Any]
    longs: dict[str, Any]
    sequencing: dict[str, Any]
    pit: dict[str, Any]
    baseline_check: dict[str, Any]
    segments: list[Segment]


def run_phase24(
    cfg: BtcStrategyConfig,
    inp: Phase2Inputs,
    segments: list[Segment],
    null_k: int,
    seed: int,
    out_dir: Path,
    expected_control_hash: str | None,
) -> Phase24Result:
    start_ms, end_ms = segments[0].start_ms, segments[-1].end_ms
    aux = AuxSeries.build(inp.funding, inp.metrics, inp.premium, inp.mark, cfg.data.latency_minutes)
    series = MultiTfSeries(inp.bars, cfg.indicators, cfg.regime.trend_slope_bars)
    cfg_c = cfg.model_copy(
        update={
            "experiment": cfg.experiment.model_copy(
                update={"block_short_in_trend_down": False, "entry_mode": "CONFIRMED_TRIGGER"}
            )
        }
    )
    cfg_v = cfg.model_copy(
        update={
            "experiment": cfg.experiment.model_copy(
                update={"block_short_in_trend_down": True, "entry_mode": "CONFIRMED_TRIGGER"}
            )
        }
    )
    log.info("CONTROL arm")
    control = run_arm("CONTROL", cfg_c, inp, aux, series, segments, null_k, seed)
    log.info("%s arm", VARIANT)
    variant = run_arm(VARIANT, cfg_v, inp, aux, series, segments, null_k, seed)
    baseline_check = {
        "expected_phase2_result_hash": expected_control_hash,
        "control_result_hash": control.result.result_hash,
        "identical": expected_control_hash is not None
        and expected_control_hash == control.result.result_hash,
        "control_config_hash": cfg_c.config_hash,
        "variant_config_hash": cfg_v.config_hash,
        "row_check_vs_phase2_trades": baseline_row_check(
            control.result.trades, Path("data/btc/runs/phase2_validation/trades.parquet")
        ),
        "control_expectancy_R": control.metrics["overall"].get("expectancy_R"),
        "control_trades": control.result.manifest["n_trades"],
    }
    log.info("analysis")
    removed = removed_trade_analysis(control, variant, segments)
    longs = long_control_check(control, variant)
    res = Phase24Result(
        manifest={
            "phase": "2.4",
            "hypothesis": f"{VARIANT} vs CONTROL",
            "period_start_ms": start_ms,
            "period_end_ms": end_ms,
            "segments": [
                {"name": s.name, "start_ms": s.start_ms, "end_ms": s.end_ms} for s in segments
            ],
            "null_k": null_k,
            "seed": seed,
            "control_manifest": control.result.manifest,
            "variant_manifest": variant.result.manifest,
            "baseline_check": baseline_check,
            "config_yaml": cfg_c.canonical_yaml(),
            "generated_at": datetime.now(UTC).isoformat(),
        },
        control=control,
        variant=variant,
        removed=removed,
        remaining=remaining_short(variant, control),
        longs=longs,
        sequencing=sequencing_split(control, variant, removed, longs),
        pit={
            "visibility_rule": "bar visible iff close_time <= t; the regime used by the rule is the PIT regime at the trigger decision bar (completed 1d/4h bars only); fill at the next 5m open",
            "rule_scope": "entry eligibility only; open positions are never closed by a regime change",
            "control_deterministic": control.deterministic,
            "variant_deterministic": variant.deterministic,
            "variant_truncation": truncation_audit(
                cfg_v, inp, aux, variant.result, start_ms, start_ms + (end_ms - start_ms) // 2
            ),
        },
        baseline_check=baseline_check,
        segments=segments,
    )
    _persist(res, out_dir)
    return res


def _persist(res: Phase24Result, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(res.manifest, indent=1, sort_keys=True, default=str)
    )
    summary = {
        "baseline_check": res.baseline_check,
        "pit": res.pit,
        "removed": {k: v for k, v in res.removed.items() if k != "removed_rows"},
        "remaining": res.remaining,
        "longs": res.longs,
        "sequencing": res.sequencing,
        "arms": {
            a.name: {
                "metrics": a.metrics,
                "account": a.account,
                "costs": a.costs,
                "families": a.families,
                "null": a.null,
                "segments": a.segments,
            }
            for a in (res.control, res.variant)
        },
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True, default=str)
    )
    for a in (res.control, res.variant):
        for name, df in (
            ("trades", a.trades),
            ("episodes", a.episodes),
            ("daily_equity", a.result.daily_equity),
        ):
            if not df.is_empty():
                df.write_parquet(out_dir / f"{a.name}_{name}.parquet")
    rr = res.removed.get("removed_rows")
    if isinstance(rr, pl.DataFrame) and not rr.is_empty():
        rr.write_parquet(out_dir / "removed_short_trades.parquet")
