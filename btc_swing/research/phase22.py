"""Phase 2.2 — EARLY ZONE ENTRY + CONFIRMATION-BASED EARLY EXIT. One pre-registered hypothesis.

CONTROL  = frozen Phase 2 (CONFIRMED_TRIGGER); result hash must equal the Phase 2 validation run.
VARIANT  = ZONE_ENTRY_CONFIRM_EXIT: zone entry (as Phase 2.1), then the ORIGINAL confirm-TF
           confirmation predicate is monitored; confirmation within the existing confirmation-
           lifecycle timeout (`episode.entry_ready_timeout_bars` = 24 x 5m = 2 h) converts the trade
           to the normal lifecycle; a 5m close beyond the plan's invalidation level before
           confirmation, or no confirmation by the deadline, exits at the next 5m open.
Reuses the Phase 2.1 arm/matching machinery and adds confirmation-timing, early-exit and
recovery breakdowns, plus a reference to the Phase 2.1 ZONE_ENTRY numbers when available.
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
from btc_swing.research.phase21 import (
    KEY,
    Arm,
    _episode_keyed,
    _pop_table,
    baseline_row_check,
    matched_analysis,
    run_arm,
)

log = logging.getLogger(__name__)
VARIANT = "ZONE_ENTRY_CONFIRM_EXIT"


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
        "profit_factor": gw / gl if gl > 0 else math.inf,
        "mean_MFE_R": _f(t["MFE_R"].mean()),
        "mean_MAE_R": _f(t["MAE_R"].mean()),
        "mean_holding_hours": _f(t["holding_hours"].mean()),
        "median_holding_hours": _f(t["holding_hours"].median()),
        "fees_plus_slippage_per_trade": _f((t["fees"] + t["slippage"]).mean()),
        "funding_per_trade": _f(t["funding"].mean()),
        "sum_pnl": float(pnl.sum()),
        "target_first_share": _f((t["path_outcome"] == "target_first").cast(pl.Float64).mean())
        if "path_outcome" in t.columns
        else math.nan,
    }


def confirmation_timing(variant: Arm) -> dict[str, Any]:
    t = variant.trades
    if t.is_empty() or "confirmed_after_entry" not in t.columns:
        return {"n": 0}
    conf = t.filter(pl.col("confirmed_after_entry"))
    at_entry = t.filter(pl.col("confirmed_at_entry"))
    later = conf.filter(~pl.col("confirmed_at_entry"))
    out: dict[str, Any] = {
        "n_trades": t.height,
        "n_confirmed": conf.height,
        "n_confirmed_at_entry": at_entry.height,
        "n_confirmed_later": later.height,
        "n_early_exit_no_confirmation": t.filter(
            pl.col("early_exit_reason") == "EARLY_EXIT_NO_CONFIRMATION"
        ).height,
        "n_early_exit_invalidation": t.filter(
            pl.col("early_exit_reason") == "EARLY_EXIT_INVALIDATION"
        ).height,
        "n_unconfirmed_other_exit": t.filter(
            ~pl.col("confirmed_after_entry") & pl.col("early_exit_reason").is_null()
        ).height,
        "confirmed_share": conf.height / t.height,
        "hours_to_confirmation_mean": _f(later["hours_to_confirmation"].mean())
        if later.height
        else math.nan,
        "hours_to_confirmation_median": _f(later["hours_to_confirmation"].median())
        if later.height
        else math.nan,
        "hours_to_confirmation_p90": _f(later["hours_to_confirmation"].quantile(0.9))
        if later.height
        else math.nan,
        "mfe_before_confirm_R_mean": _f(later["mfe_before_confirm_R"].mean())
        if later.height
        else math.nan,
        "mae_before_confirm_R_mean": _f(later["mae_before_confirm_R"].mean())
        if later.height
        else math.nan,
        "confirmed": _stats(conf),
        "confirmed_at_entry": _stats(at_entry),
        "confirmed_later": _stats(later),
        "unconfirmed": _stats(t.filter(~pl.col("confirmed_after_entry"))),
        "by_family": [],
    }
    for fam in sorted(t["family"].unique().to_list()):
        ft = t.filter(pl.col("family") == fam)
        fc = ft.filter(pl.col("confirmed_after_entry"))
        out["by_family"].append(
            {
                "family": fam,
                "n": ft.height,
                "confirmed_share": fc.height / ft.height,
                "early_exit_no_conf": ft.filter(
                    pl.col("early_exit_reason") == "EARLY_EXIT_NO_CONFIRMATION"
                ).height,
                "early_exit_inval": ft.filter(
                    pl.col("early_exit_reason") == "EARLY_EXIT_INVALIDATION"
                ).height,
                "confirmed_mean_R": _f(fc["R_MULTIPLE"].mean()) if fc.height else math.nan,
                "unconfirmed_mean_R": _f(
                    ft.filter(~pl.col("confirmed_after_entry"))["R_MULTIPLE"].mean()
                )
                if ft.height > fc.height
                else math.nan,
            }
        )
    return out


def _with_control_outcome(variant: Arm, control: Arm, t: pl.DataFrame) -> pl.DataFrame:
    c = _episode_keyed(control).select(
        [
            *KEY,
            pl.col("outcome_class").alias("control_outcome"),
            pl.col("end_reason").alias("control_end_reason"),
        ]
    )
    eps = variant.episodes.select(["episode_id", *KEY])
    return (
        t.join(eps, on="episode_id", how="left")
        .join(c, on=KEY, how="left")
        .with_columns(pl.col("control_outcome").fill_null("UNMATCHED"))
    )


def early_exit_outcomes(variant: Arm, control: Arm, reason: str) -> dict[str, Any]:
    t = (
        variant.trades.filter(pl.col("early_exit_reason") == reason)
        if variant.trades.height
        else variant.trades
    )
    if t.is_empty():
        return {"n": 0}
    tt = _with_control_outcome(variant, control, t)
    return {
        **_stats(t),
        "by_control_outcome": _pop_table(tt, "control_outcome"),
        "by_family": _pop_table(tt, "family"),
        "sum_fees_plus_slippage": _f((t["fees"] + t["slippage"]).sum()),
        "sum_funding": _f(t["funding"].sum()),
        "frac_profitable": _f((t["POSITION_PNL"] > 0).cast(pl.Float64).mean()),
        "r_quantiles": {
            q: float(np.quantile(t["R_MULTIPLE"].to_numpy(), q))
            for q in (0.1, 0.25, 0.5, 0.75, 0.9)
        },
    }


def recovered_never_triggered(control: Arm, variant: Arm) -> dict[str, Any]:
    c = _episode_keyed(control).filter(pl.col("outcome_class") == "NEVER_TRIGGERED")
    v = _episode_keyed(variant)
    j = c.select([*KEY, pl.col("end_reason").alias("control_end_reason")]).join(
        v, on=KEY, how="left"
    )
    rec = j.filter(pl.col("entry_ms").is_not_null())
    out: dict[str, Any] = {
        "control_never_triggered": c.height,
        "matched_in_variant": j.filter(pl.col("episode_id").is_not_null()).height,
        "became_trades": rec.height,
    }
    if rec.height:
        conf = rec.filter(pl.col("confirmed_after_entry") == True)  # noqa: E712
        out.update(
            {
                "later_confirmed": conf.height,
                "early_exit_no_confirmation": rec.filter(
                    pl.col("early_exit_reason") == "EARLY_EXIT_NO_CONFIRMATION"
                ).height,
                "early_exit_invalidation": rec.filter(
                    pl.col("early_exit_reason") == "EARLY_EXIT_INVALIDATION"
                ).height,
                "all": _stats(rec),
                "confirmed": _stats(conf),
                "unconfirmed": _stats(rec.filter(pl.col("confirmed_after_entry") != True)),  # noqa: E712
                "by_family": _pop_table(rec, "family"),
                "by_control_end_reason": _pop_table(rec, "control_end_reason"),
            }
        )
    return out


def load_phase21_reference(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    s = json.loads(path.read_text())
    arm = s.get("arms", {}).get("ZONE_ENTRY")
    if not arm:
        return None
    o = arm["metrics"]["overall"]
    return {
        "source": str(path),
        "n_trades": o.get("n"),
        "expectancy_R": o.get("expectancy_R"),
        "profit_factor": o.get("profit_factor"),
        "win_rate": o.get("win_rate"),
        "max_drawdown_frac": arm["account"].get("max_drawdown_frac_trade_curve"),
        "total_return": arm["account"].get("total_return"),
        "net": arm["costs"].get("net"),
        "cost_drag_R_per_trade": arm["costs"].get("cost_drag_R_per_trade"),
        "segments": {
            sn: {
                "expectancy_R": sg["metrics"]["overall"].get("expectancy_R"),
                "n": sg["metrics"]["overall"].get("n"),
                "max_drawdown_frac": sg["account"].get("max_drawdown_frac_trade_curve"),
            }
            for sn, sg in arm["segments"].items()
        },
        "invalidated_group": next(
            (
                d
                for d in s.get("matched", {}).get("variant_trades_by_control_outcome", [])
                if d.get("control_outcome") == "INVALIDATED"
            ),
            None,
        ),
        "recovered": s.get("recovery", {}).get("recovered_trades"),
    }


@dataclass
class Phase22Result:
    manifest: dict[str, Any]
    control: Arm
    variant: Arm
    matched: dict[str, Any]
    timing: dict[str, Any]
    early_no_conf: dict[str, Any]
    early_inval: dict[str, Any]
    recovery: dict[str, Any]
    pit: dict[str, Any]
    baseline_check: dict[str, Any]
    phase21_ref: dict[str, Any] | None
    segments: list[Segment]
    confirmation_window_bars: int


def run_phase22(
    cfg: BtcStrategyConfig,
    inp: Phase2Inputs,
    segments: list[Segment],
    null_k: int,
    seed: int,
    out_dir: Path,
    expected_control_hash: str | None,
    phase21_summary: Path | None,
) -> Phase22Result:
    start_ms, end_ms = segments[0].start_ms, segments[-1].end_ms
    aux = AuxSeries.build(inp.funding, inp.metrics, inp.premium, inp.mark, cfg.data.latency_minutes)
    series = MultiTfSeries(inp.bars, cfg.indicators, cfg.regime.trend_slope_bars)
    cfg_c = cfg.model_copy(
        update={"experiment": cfg.experiment.model_copy(update={"entry_mode": "CONFIRMED_TRIGGER"})}
    )
    cfg_v = cfg.model_copy(
        update={"experiment": cfg.experiment.model_copy(update={"entry_mode": VARIANT})}
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
    log.info("analysis")
    matched = matched_analysis(control, variant)
    res = Phase22Result(
        manifest={
            "phase": "2.2",
            "hypothesis": f"{VARIANT} vs CONFIRMED_TRIGGER",
            "confirmation_window_bars": cfg.episode.entry_ready_timeout_bars,
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
        matched=matched,
        timing=confirmation_timing(variant),
        early_no_conf=early_exit_outcomes(variant, control, "EARLY_EXIT_NO_CONFIRMATION"),
        early_inval=early_exit_outcomes(variant, control, "EARLY_EXIT_INVALIDATION"),
        recovery=recovered_never_triggered(control, variant),
        pit={
            "visibility_rule": "bar visible iff close_time <= t; zone test, confirmation test and invalidation test use completed bars at t; fills at the next 5m open",
            "zone_definition": "fixed at detection (SetupPlan frozen); never moved",
            "confirmation_predicate": "the Phase 2 ZoneSetupDetector.confirmed() predicate, unchanged",
            "control_deterministic": control.deterministic,
            "variant_deterministic": variant.deterministic,
            "variant_truncation": truncation_audit(
                cfg_v, inp, aux, variant.result, start_ms, start_ms + (end_ms - start_ms) // 2
            ),
        },
        baseline_check=baseline_check,
        phase21_ref=load_phase21_reference(phase21_summary) if phase21_summary else None,
        segments=segments,
        confirmation_window_bars=cfg.episode.entry_ready_timeout_bars,
    )
    _persist(res, out_dir)
    return res


def _persist(res: Phase22Result, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(res.manifest, indent=1, sort_keys=True, default=str)
    )
    summary = {
        "baseline_check": res.baseline_check,
        "pit": res.pit,
        "matched": {k: v for k, v in res.matched.items() if k != "pairs"},
        "timing": res.timing,
        "early_exit_no_confirmation": res.early_no_conf,
        "early_exit_invalidation": res.early_inval,
        "recovery": res.recovery,
        "phase21_reference": res.phase21_ref,
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
            ("decisions", a.result.decisions),
            ("daily_equity", a.result.daily_equity),
        ):
            if not df.is_empty():
                df.write_parquet(out_dir / f"{a.name}_{name}.parquet")
    pairs = res.matched.get("pairs")
    if isinstance(pairs, pl.DataFrame) and not pairs.is_empty():
        pairs.write_parquet(out_dir / "matched_pairs.parquet")
