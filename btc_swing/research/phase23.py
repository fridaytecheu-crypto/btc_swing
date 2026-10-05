"""Phase 2.3 — POST-TP1 EXIT DESIGN. One pre-registered hypothesis.

CONTROL  = frozen Phase 2 (`exits.breakeven_after_tp1: true`); result hash must equal Phase 2.
VARIANT  = STRUCTURAL_TRAIL_AFTER_TP1: identical except `exits.breakeven_after_tp1: false`. After
           TP1 the initial stop stays until the existing `structure_atr` trail moves it.
Entries are identical, so the analysis is paired on the same trades (matched on family +
detection time). The two failure modes are quantified against each other: CONTROL trades stopped
at breakeven that later resumed to 2R/3R, and CONTROL trades protected at breakeven that become
losses under the variant.
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
VARIANT = "STRUCTURAL_TRAIL_AFTER_TP1"
PAIR_COLS = [
    "trade_id",
    "entry_ms",
    "exit_ms",
    "entry_price",
    "initial_stop",
    "final_stop",
    "stop_distance",
    "R_MULTIPLE",
    "R_MULTIPLE_GROSS",
    "POSITION_PNL",
    "fees",
    "slippage",
    "funding",
    "holding_hours",
    "exit_reason",
    "stop_source_at_exit",
    "stopped_at_breakeven",
    "tp1_hit",
    "tp2_hit",
    "tp1_ms",
    "hours_to_tp1",
    "MFE_R",
    "MAE_R",
    "mfe_after_tp1_R",
    "mae_after_tp1_R",
    "realised_R_after_tp1",
]


def _f(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


def _keyed_trades(arm: Arm) -> pl.DataFrame:
    eps = arm.episodes.select(["episode_id", *KEY, "side", "regime_at_detection"])
    cols = [c for c in PAIR_COLS if c in arm.trades.columns]
    return arm.trades.select(["episode_id", *cols]).join(eps, on="episode_id", how="left")


def resumed_after_exit(
    series: MultiTfSeries,
    side: str,
    entry_ms: int,
    exit_ms: int,
    entry: float,
    stop_dist: float,
    initial_stop: float,
    max_hold_hours: int,
) -> dict[str, Any]:
    """After the CONTROL exit, did price reach entry+2R / +3R before touching the initial stop,
    within the trade's remaining max-hold horizon?"""
    base = series.base
    s = 1.0 if side == "LONG" else -1.0
    i0 = int(np.searchsorted(base.close_ms, exit_ms, side="right"))
    i_end = int(np.searchsorted(base.open_ms, entry_ms + max_hold_hours * 3_600_000, side="left"))
    out = {
        "resumed_2R": False,
        "resumed_3R": False,
        "stopped_first": False,
        "bars_checked": max(0, min(i_end, len(base)) - i0),
    }
    t2, t3 = entry + s * 2 * stop_dist, entry + s * 3 * stop_dist
    for j in range(i0, min(i_end, len(base))):
        hi, lo = float(base.high[j]), float(base.low[j])
        adverse = lo if s > 0 else hi
        fav = hi if s > 0 else lo
        if s * (adverse - initial_stop) <= 0:
            out["stopped_first"] = True
            break
        if not out["resumed_2R"] and s * (fav - t2) >= 0:
            out["resumed_2R"] = True
        if s * (fav - t3) >= 0:
            out["resumed_3R"] = True
            break
    return out


def paired_analysis(
    control: Arm, variant: Arm, series: MultiTfSeries, max_hold_hours: int
) -> dict[str, Any]:
    c, v = _keyed_trades(control), _keyed_trades(variant)
    j = c.join(v, on=KEY, how="inner", suffix="_v")
    out: dict[str, Any] = {
        "control_trades": c.height,
        "variant_trades": v.height,
        "paired": j.height,
        "identical_entries": int((j["entry_ms"] == j["entry_ms_v"]).sum()) if j.height else 0,
    }
    if not j.height:
        return out
    same = j.filter(pl.col("entry_ms") == pl.col("entry_ms_v"))
    out["paired_identical_entry"] = same.height
    tp1 = same.filter(pl.col("tp1_hit") & pl.col("tp1_hit_v"))
    out["tp1_pairs"] = tp1.height
    out["tp1_only_one_arm"] = same.filter(pl.col("tp1_hit") != pl.col("tp1_hit_v")).height
    # resume labels for CONTROL trades (computed from the 5m series after the CONTROL exit)
    rows = []
    for r in tp1.iter_rows(named=True):
        res = resumed_after_exit(
            series,
            r["side"],
            int(r["entry_ms"]),
            int(r["exit_ms"]),
            float(r["entry_price"]),
            float(r["stop_distance"]),
            float(r["initial_stop"]),
            max_hold_hours,
        )
        rows.append({"trade_id": r["trade_id"], **res})
    lab = (
        pl.DataFrame(rows)
        if rows
        else pl.DataFrame(
            {
                "trade_id": [],
                "resumed_2R": [],
                "resumed_3R": [],
                "stopped_first": [],
                "bars_checked": [],
            }
        )
    )
    tp1 = tp1.join(lab, on="trade_id", how="left")
    d = (tp1["R_MULTIPLE_v"] - tp1["R_MULTIPLE"]).to_numpy().astype(float)
    pairs = (
        tp1.select(
            "family",
            "side",
            "regime_at_detection",
            "entry_ms",
            "entry_price",
            "stop_distance",
            pl.col("hours_to_tp1"),
            pl.col("exit_reason").alias("control_exit"),
            pl.col("stop_source_at_exit").alias("control_stop_source"),
            pl.col("exit_reason_v").alias("variant_exit"),
            pl.col("stop_source_at_exit_v").alias("variant_stop_source"),
            pl.col("stopped_at_breakeven").alias("control_stopped_at_breakeven"),
            pl.col("mfe_after_tp1_R").alias("control_mfe_after_tp1_R"),
            pl.col("mfe_after_tp1_R_v").alias("variant_mfe_after_tp1_R"),
            pl.col("mae_after_tp1_R").alias("control_mae_after_tp1_R"),
            pl.col("mae_after_tp1_R_v").alias("variant_mae_after_tp1_R"),
            pl.col("MFE_R").alias("control_MFE_R"),
            pl.col("MFE_R_v").alias("variant_MFE_R"),
            pl.col("holding_hours").alias("control_hold_h"),
            pl.col("holding_hours_v").alias("variant_hold_h"),
            pl.col("funding").alias("control_funding"),
            pl.col("funding_v").alias("variant_funding"),
            pl.col("tp2_hit").alias("control_tp2"),
            pl.col("tp2_hit_v").alias("variant_tp2"),
            pl.col("R_MULTIPLE").alias("control_R"),
            pl.col("R_MULTIPLE_v").alias("variant_R"),
            pl.col("POSITION_PNL").alias("control_pnl"),
            pl.col("POSITION_PNL_v").alias("variant_pnl"),
            "resumed_2R",
            "resumed_3R",
            "stopped_first",
        )
        .with_columns((pl.col("variant_R") - pl.col("control_R")).alias("diff_R"))
        .sort("entry_ms")
    )
    out["pairs"] = pairs
    out["tp1_summary"] = {
        "n": pairs.height,
        "control_mean_R": _f(pairs["control_R"].mean()),
        "variant_mean_R": _f(pairs["variant_R"].mean()),
        "paired_mean_diff_R": float(d.mean()) if len(d) else math.nan,
        "paired_t": float(d.mean() / (d.std(ddof=1) / math.sqrt(len(d))))
        if len(d) > 1 and d.std(ddof=1) > 0
        else math.nan,
        "frac_variant_better": float((d > 1e-9).mean()) if len(d) else math.nan,
        "frac_variant_worse": float((d < -1e-9).mean()) if len(d) else math.nan,
        "frac_identical": float((np.abs(d) <= 1e-9).mean()) if len(d) else math.nan,
        "control_sum_pnl": _f(pairs["control_pnl"].sum()),
        "variant_sum_pnl": _f(pairs["variant_pnl"].sum()),
        "control_mean_hold_h": _f(pairs["control_hold_h"].mean()),
        "variant_mean_hold_h": _f(pairs["variant_hold_h"].mean()),
        "control_funding_sum": _f(pairs["control_funding"].sum()),
        "variant_funding_sum": _f(pairs["variant_funding"].sum()),
        "control_mean_mfe_after_tp1_R": _f(pairs["control_mfe_after_tp1_R"].mean()),
        "variant_mean_mfe_after_tp1_R": _f(pairs["variant_mfe_after_tp1_R"].mean()),
        "control_mean_mae_after_tp1_R": _f(pairs["control_mae_after_tp1_R"].mean()),
        "variant_mean_mae_after_tp1_R": _f(pairs["variant_mae_after_tp1_R"].mean()),
        "control_tp2": int(pairs["control_tp2"].sum()),
        "variant_tp2": int(pairs["variant_tp2"].sum()),
        "control_exit_mix": pairs.group_by("control_exit", "control_stop_source")
        .len()
        .sort("len", descending=True)
        .to_dicts(),
        "variant_exit_mix": pairs.group_by("variant_exit", "variant_stop_source")
        .len()
        .sort("len", descending=True)
        .to_dicts(),
        "mfe_capture_control": _f(pairs["control_R"].mean()) / _f(pairs["variant_MFE_R"].mean())
        if _f(pairs["variant_MFE_R"].mean())
        else math.nan,
        "mfe_capture_variant": _f(pairs["variant_R"].mean()) / _f(pairs["variant_MFE_R"].mean())
        if _f(pairs["variant_MFE_R"].mean())
        else math.nan,
    }
    be = pairs.filter(pl.col("control_stopped_at_breakeven"))
    out["breakeven_stops"] = {
        "n": be.height,
        "resumed_2R": int(be["resumed_2R"].sum()) if be.height else 0,
        "resumed_3R": int(be["resumed_3R"].sum()) if be.height else 0,
        "stopped_first": int(be["stopped_first"].sum()) if be.height else 0,
        "neither": int((~be["resumed_2R"] & ~be["stopped_first"]).sum()) if be.height else 0,
        "control_mean_R": _f(be["control_R"].mean()),
        "variant_mean_R": _f(be["variant_R"].mean()),
        "variant_sum_pnl_minus_control": _f((be["variant_pnl"] - be["control_pnl"]).sum()),
        "variant_mean_R_when_resumed_2R": _f(be.filter(pl.col("resumed_2R"))["variant_R"].mean())
        if be.height
        else math.nan,
        "variant_mean_R_when_stopped_first": _f(
            be.filter(pl.col("stopped_first"))["variant_R"].mean()
        )
        if be.height
        else math.nan,
        "variant_reached_2R_or_more": int((be["variant_R"] >= 2.0).sum()) if be.height else 0,
        "variant_reached_3R_or_more": int((be["variant_R"] >= 3.0).sum()) if be.height else 0,
        "by_family": be.group_by("family")
        .agg(
            pl.len().alias("n"),
            pl.col("resumed_2R").sum().alias("resumed_2R"),
            pl.col("control_R").mean().alias("control_mean_R"),
            pl.col("variant_R").mean().alias("variant_mean_R"),
            (pl.col("variant_pnl") - pl.col("control_pnl")).sum().alias("pnl_diff"),
        )
        .sort("family")
        .to_dicts()
        if be.height
        else [],
    }
    worse = be.filter(pl.col("variant_R") < pl.col("control_R") - 1e-9)
    meaningful = be.filter(pl.col("variant_R") <= -0.25)
    out["downside"] = {
        "protected_by_breakeven_n": be.height,
        "worse_under_variant_n": worse.height,
        "worse_mean_diff_R": _f(worse["diff_R"].mean()),
        "worse_total_pnl_impact": _f((worse["variant_pnl"] - worse["control_pnl"]).sum()),
        "meaningful_loss_n": meaningful.height,
        "meaningful_loss_mean_variant_R": _f(meaningful["variant_R"].mean()),
        "meaningful_loss_mean_control_R": _f(meaningful["control_R"].mean()),
        "meaningful_loss_total_pnl_impact": _f(
            (meaningful["variant_pnl"] - meaningful["control_pnl"]).sum()
        ),
        "variant_initial_stop_after_tp1_n": int((pairs["variant_stop_source"] == "INITIAL").sum()),
        "better_under_variant_n": be.filter(
            pl.col("variant_R") > pl.col("control_R") + 1e-9
        ).height,
        "better_total_pnl_impact": _f(
            (
                be.filter(pl.col("variant_R") > pl.col("control_R") + 1e-9)["variant_pnl"]
                - be.filter(pl.col("variant_R") > pl.col("control_R") + 1e-9)["control_pnl"]
            ).sum()
        ),
    }
    # all paired trades (not only TP1): divergence summary
    out["all_pairs"] = {
        "n": same.height,
        "n_different_R": int(((same["R_MULTIPLE_v"] - same["R_MULTIPLE"]).abs() > 1e-9).sum()),
        "control_sum_pnl": _f(same["POSITION_PNL"].sum()),
        "variant_sum_pnl": _f(same["POSITION_PNL_v"].sum()),
    }
    return out


def mfe_capture(arm: Arm) -> dict[str, Any]:
    t = arm.trades
    if t.is_empty():
        return {}
    tp1 = t.filter(pl.col("tp1_hit"))
    win = t.filter(pl.col("POSITION_PNL") > 0)
    ratio = (
        (tp1["R_MULTIPLE"] / tp1["MFE_R"]).to_numpy().astype(float) if tp1.height else np.zeros(0)
    )
    return {
        "tp1_frequency": tp1.height / t.height,
        "tp2_frequency": _f(t["tp2_hit"].cast(pl.Float64).mean()),
        "stop_outs_after_tp1": int(
            tp1.filter(
                pl.col("exit_reason").is_in(["STOP", "TRAIL"])
                & (pl.col("realised_R_after_tp1") < 0)
            ).height
        ),
        "breakeven_stops_after_tp1": int(tp1["stopped_at_breakeven"].sum()),
        "initial_stop_after_tp1": int((tp1["stop_source_at_exit"] == "INITIAL").sum()),
        "trail_stops_after_tp1": int((tp1["stop_source_at_exit"] == "TRAIL").sum()),
        "mean_realised_R_tp1": _f(tp1["R_MULTIPLE"].mean()),
        "mean_MFE_R_tp1": _f(tp1["MFE_R"].mean()),
        "capture_ratio_tp1": _f(tp1["R_MULTIPLE"].mean()) / _f(tp1["MFE_R"].mean())
        if tp1.height
        else math.nan,
        "median_per_trade_capture_tp1": float(np.median(ratio)) if len(ratio) else math.nan,
        "mean_realised_winner_R": _f(win["R_MULTIPLE"].mean()),
        "mean_MFE_R_all": _f(t["MFE_R"].mean()),
        "capture_ratio_winners": _f(win["R_MULTIPLE"].mean()) / _f(t["MFE_R"].mean())
        if t.height
        else math.nan,
        "mean_holding_hours_tp1": _f(tp1["holding_hours"].mean()),
        "funding_tp1_sum": _f(tp1["funding"].sum()),
        "fees_plus_slippage_tp1_sum": _f((tp1["fees"] + tp1["slippage"]).sum()),
    }


@dataclass
class Phase23Result:
    manifest: dict[str, Any]
    control: Arm
    variant: Arm
    paired: dict[str, Any]
    capture: dict[str, dict[str, Any]]
    pit: dict[str, Any]
    baseline_check: dict[str, Any]
    segments: list[Segment]


def run_phase23(
    cfg: BtcStrategyConfig,
    inp: Phase2Inputs,
    segments: list[Segment],
    null_k: int,
    seed: int,
    out_dir: Path,
    expected_control_hash: str | None,
) -> Phase23Result:
    start_ms, end_ms = segments[0].start_ms, segments[-1].end_ms
    aux = AuxSeries.build(inp.funding, inp.metrics, inp.premium, inp.mark, cfg.data.latency_minutes)
    series = MultiTfSeries(inp.bars, cfg.indicators, cfg.regime.trend_slope_bars)
    cfg_c = cfg.model_copy(
        update={"exits": cfg.exits.model_copy(update={"breakeven_after_tp1": True})}
    )
    cfg_v = cfg.model_copy(
        update={"exits": cfg.exits.model_copy(update={"breakeven_after_tp1": False})}
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
    }
    log.info("paired analysis")
    paired = paired_analysis(control, variant, series, cfg.exits.max_hold_hours)
    res = Phase23Result(
        manifest={
            "phase": "2.3",
            "hypothesis": f"{VARIANT} (breakeven_after_tp1=false) vs CONTROL",
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
        paired=paired,
        capture={"CONTROL": mfe_capture(control), VARIANT: mfe_capture(variant)},
        pit={
            "visibility_rule": "bar visible iff close_time <= t; the breakeven move and the structural trail are both applied from the bar after the decision; stop-first ordering inside a bar",
            "control_deterministic": control.deterministic,
            "variant_deterministic": variant.deterministic,
            "variant_truncation": truncation_audit(
                cfg_v, inp, aux, variant.result, start_ms, start_ms + (end_ms - start_ms) // 2
            ),
            "resume_label": "computed after the run from 5m highs/lows following the CONTROL exit, within the trade's max-hold horizon, initial stop as failure condition; descriptive only, never used by the engine",
        },
        baseline_check=baseline_check,
        segments=segments,
    )
    _persist(res, out_dir)
    return res


def _persist(res: Phase23Result, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(res.manifest, indent=1, sort_keys=True, default=str)
    )
    summary = {
        "baseline_check": res.baseline_check,
        "pit": res.pit,
        "paired": {k: v for k, v in res.paired.items() if k != "pairs"},
        "capture": res.capture,
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
    pairs = res.paired.get("pairs")
    if isinstance(pairs, pl.DataFrame) and not pairs.is_empty():
        pairs.write_parquet(out_dir / "tp1_pairs.parquet")
