"""Phase 2.1 — ENTRY MECHANICS. One pre-registered structural hypothesis.

CONTROL     = the frozen Phase 2 strategy (entry_mode CONFIRMED_TRIGGER); its result hash must equal
              the Phase 2 validation run.
ZONE_ENTRY  = identical in every respect except the WATCH -> TRIGGERED transition: enter on the next
              5m open after the first entry-TF bar that reaches the pre-defined zone while the plan is
              still valid (no 15m confirmation, no 5m trigger).

Both arms run on the same data, same period, same series/aux objects. Episodes are matched across
arms on (family, detected_at_ms): detection is unchanged, so an episode exists in both arms whenever
the single slot was free in both at that time. Matched analysis separates "better timing" from
"different population".
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

from btc_swing.backtest.engine import BacktestEngine, BacktestResult
from btc_swing.core.config import BtcStrategyConfig
from btc_swing.features.context import AuxSeries
from btc_swing.features.view import MultiTfSeries
from btc_swing.research.labels import label_episodes
from btc_swing.research.metrics import compute_metrics
from btc_swing.research.null_benchmark import run_null
from btc_swing.research.phase2 import (
    DAY_MS,
    Phase2Inputs,
    Segment,
    account_stats,
    cost_impact,
    family_table,
    truncation_audit,
)

log = logging.getLogger(__name__)
KEY = ["family", "detected_at_ms"]
CONFIRM_COLS = [
    "confirmed_after_entry",
    "confirmed_at_entry",
    "bars_to_confirmation",
    "hours_to_confirmation",
    "mfe_before_confirm_R",
    "mae_before_confirm_R",
    "early_exit_reason",
]


def _f(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


@dataclass
class Arm:
    name: str
    cfg: BtcStrategyConfig
    result: BacktestResult
    episodes: pl.DataFrame  # labelled
    trades: pl.DataFrame  # joined with episode labels (path_outcome etc.)
    metrics: dict[str, Any]
    account: dict[str, Any]
    costs: dict[str, Any]
    families: list[dict[str, Any]]
    null: dict[str, Any]
    segments: dict[str, dict[str, Any]]
    deterministic: bool


def _segment_stats(
    arm_trades: pl.DataFrame,
    arm_eps: pl.DataFrame,
    daily: pl.DataFrame,
    cfg: BtcStrategyConfig,
    base_manifest: dict[str, Any],
    sg: Segment,
    eng: BacktestEngine,
    decisions: pl.DataFrame,
    null_k: int,
    seed: int,
) -> dict[str, Any]:
    tr = (
        arm_trades.filter((pl.col("entry_ms") >= sg.start_ms) & (pl.col("entry_ms") < sg.end_ms))
        if arm_trades.height
        else arm_trades
    )
    ep = (
        arm_eps.filter(
            (pl.col("detected_at_ms") >= sg.start_ms) & (pl.col("detected_at_ms") < sg.end_ms)
        )
        if arm_eps.height
        else arm_eps
    )
    dl = daily.filter((pl.col("t_ms") > sg.start_ms) & (pl.col("t_ms") <= sg.end_ms))
    man = {
        **base_manifest,
        "n_days": (sg.end_ms - sg.start_ms) / DAY_MS,
        "initial_equity": cfg.risk.initial_equity,
        "final_equity": cfg.risk.initial_equity
        + (_f(tr["POSITION_PNL"].sum()) if tr.height else 0.0),
    }
    return {
        "metrics": compute_metrics(tr, ep, dl, man, cfg.research),
        "account": account_stats(tr, dl, cfg.risk.initial_equity, sg.start_ms, sg.end_ms),
        "costs": cost_impact(tr),
        "families": family_table(tr, cfg.research.min_cell_n),
        "episode_outcomes": ep.group_by("outcome_class").len().sort("outcome_class").to_dicts()
        if ep.height
        else [],
        "null": run_null(eng, tr, decisions, sg.start_ms, sg.end_ms, null_k, seed).summary
        if tr.height
        else {"n_trades": 0},
    }


def run_arm(
    name: str,
    cfg: BtcStrategyConfig,
    inp: Phase2Inputs,
    aux: AuxSeries,
    series: MultiTfSeries,
    segments: list[Segment],
    null_k: int,
    seed: int,
    phase: str = "2.1",
) -> Arm:
    start_ms, end_ms = segments[0].start_ms, segments[-1].end_ms
    eng = BacktestEngine(cfg, inp.bars, inp.funding, inp.hashes, aux, series)
    res = eng.run(start_ms, end_ms, notes={"phase": phase, "arm": name})
    rerun = BacktestEngine(cfg, inp.bars, inp.funding, inp.hashes, aux, series).run(
        start_ms, end_ms
    )
    eps = label_episodes(res.episodes, series, cfg.exits.max_hold_hours)
    lab_cols = [
        "episode_id",
        "detected_at_ms",
        "path_outcome",
        "bars_to_outcome",
        "fwd_ret_24h",
        "fwd_ret_72h",
        "end_reason",
        "outcome_class",
    ]
    trades = (
        res.trades.join(
            eps.select([c for c in lab_cols if c in eps.columns]), on="episode_id", how="left"
        )
        if res.trades.height
        else res.trades
    )
    metrics = compute_metrics(
        res.trades, res.episodes, res.daily_equity, res.manifest, cfg.research
    )
    segs = {
        sg.name: _segment_stats(
            trades, eps, res.daily_equity, cfg, res.manifest, sg, eng, res.decisions, null_k, seed
        )
        for sg in segments
    }
    null = (
        run_null(eng, res.trades, res.decisions, start_ms, end_ms, null_k, seed).summary
        if res.trades.height
        else {"n_trades": 0}
    )
    return Arm(
        name,
        cfg,
        res,
        eps,
        trades,
        metrics,
        account_stats(res.trades, res.daily_equity, cfg.risk.initial_equity, start_ms, end_ms),
        cost_impact(res.trades),
        family_table(res.trades, cfg.research.min_cell_n),
        null,
        segs,
        rerun.result_hash == res.result_hash,
    )


# --------------------------------------------------------------------------- baseline row check
def baseline_row_check(control_trades: pl.DataFrame, phase2_trades_path: Path) -> dict[str, Any]:
    """Row-level identity of CONTROL trades against the persisted Phase 2 trades on the columns that
    existed in Phase 2 (schema additions in later phases cannot hide a behavioural change)."""
    if not phase2_trades_path.exists():
        return {"available": False}
    ref = pl.read_parquet(phase2_trades_path)
    common = [c for c in ref.columns if c in control_trades.columns]
    if ref.height != control_trades.height:
        return {
            "available": True,
            "identical": False,
            "n_phase2": ref.height,
            "n_control": control_trades.height,
        }
    a = ref.select(common).sort("trade_id")
    b = control_trades.select(common).sort("trade_id")
    mismatches: list[str] = []
    for c in common:
        x, y = a[c], b[c]
        if x.dtype.is_numeric() and y.dtype.is_numeric():
            xa, ya = x.cast(pl.Float64).to_numpy(), y.cast(pl.Float64).to_numpy()
            same = bool(
                np.all(
                    np.isclose(xa, ya, rtol=1e-9, atol=1e-9, equal_nan=True)
                    | (np.isnan(xa) & np.isnan(ya))
                )
            )
        else:
            same = (
                x.fill_null("__null__").cast(pl.Utf8).to_list()
                == y.fill_null("__null__").cast(pl.Utf8).to_list()
            )
        if not same:
            mismatches.append(c)
    return {
        "available": True,
        "identical": not mismatches,
        "n_rows": ref.height,
        "n_columns_compared": len(common),
        "mismatching_columns": mismatches,
    }


# --------------------------------------------------------------------------- matched episodes
def _episode_keyed(arm: Arm) -> pl.DataFrame:
    ep = arm.episodes.select(
        [
            *KEY,
            "episode_id",
            "side",
            "outcome_class",
            "end_reason",
            "regime_at_detection",
            "path_outcome",
            "fwd_ret_24h",
            "fwd_ret_72h",
            "stop_price",
            "entry_zone_low",
            "entry_zone_high",
        ]
    )
    tr_cols = [
        "episode_id",
        "entry_ms",
        "exit_ms",
        "entry_price",
        "stop_distance_pct",
        "stop_distance",
        "MFE_R",
        "MAE_R",
        "MFE_PCT",
        "MAE_PCT",
        "R_MULTIPLE",
        "POSITION_PNL",
        "fees",
        "slippage",
        "funding",
        "holding_hours",
        "exit_reason",
        "leverage",
        "risk_amount",
        "gross_pnl",
    ]
    tr_cols += [c for c in CONFIRM_COLS if arm.trades.height and c in arm.trades.columns]
    if arm.trades.height:
        ep = ep.join(arm.trades.select(tr_cols), on="episode_id", how="left")
    else:
        for c in tr_cols[1:]:
            ep = ep.with_columns(pl.lit(None).alias(c))
    return ep


def matched_analysis(control: Arm, variant: Arm) -> dict[str, Any]:
    c = _episode_keyed(control)
    v = _episode_keyed(variant)
    j = c.join(v, on=KEY, how="full", suffix="_v", coalesce=True)
    out: dict[str, Any] = {
        "control_episodes": c.height,
        "variant_episodes": v.height,
        "matched": j.filter(
            pl.col("episode_id").is_not_null() & pl.col("episode_id_v").is_not_null()
        ).height,
        "control_only": j.filter(pl.col("episode_id_v").is_null()).height,
        "variant_only": j.filter(pl.col("episode_id").is_null()).height,
    }
    m = j.filter(pl.col("episode_id").is_not_null() & pl.col("episode_id_v").is_not_null())
    out["transition_matrix"] = (
        m.group_by("outcome_class", "outcome_class_v")
        .len()
        .sort("outcome_class", "outcome_class_v")
        .to_dicts()
        if m.height
        else []
    )
    # variant trades decomposed by what CONTROL did on the same episode
    if variant.trades.height:
        vt = (
            v.filter(pl.col("entry_ms").is_not_null())
            .join(
                c.select(
                    [
                        *KEY,
                        pl.col("outcome_class").alias("control_outcome"),
                        pl.col("end_reason").alias("control_end_reason"),
                    ]
                ),
                on=KEY,
                how="left",
            )
            .with_columns(pl.col("control_outcome").fill_null("UNMATCHED"))
        )
        out["variant_trades_by_control_outcome"] = _pop_table(vt, "control_outcome")
    # both traded: paired comparison
    both = m.filter(pl.col("entry_ms").is_not_null() & pl.col("entry_ms_v").is_not_null())
    pairs = pl.DataFrame()
    if both.height:
        sgn = pl.when(pl.col("side") == "LONG").then(1.0).otherwise(-1.0)
        pairs = both.select(
            "family",
            "side",
            "detected_at_ms",
            "regime_at_detection",
            "path_outcome",
            pl.col("entry_ms").alias("control_entry_ms"),
            pl.col("entry_ms_v").alias("zone_entry_ms"),
            ((pl.col("entry_ms") - pl.col("entry_ms_v")) / 3_600_000.0).alias("hours_earlier"),
            pl.col("entry_price").alias("control_entry_price"),
            pl.col("entry_price_v").alias("zone_entry_price"),
            (sgn * (pl.col("entry_price") - pl.col("entry_price_v")) / pl.col("entry_price")).alias(
                "price_improvement_pct"
            ),
            pl.col("stop_distance_pct").alias("control_stop_pct"),
            pl.col("stop_distance_pct_v").alias("zone_stop_pct"),
            pl.col("MFE_R").alias("control_MFE_R"),
            pl.col("MFE_R_v").alias("zone_MFE_R"),
            pl.col("MAE_R").alias("control_MAE_R"),
            pl.col("MAE_R_v").alias("zone_MAE_R"),
            pl.col("R_MULTIPLE").alias("control_R"),
            pl.col("R_MULTIPLE_v").alias("zone_R"),
            pl.col("POSITION_PNL").alias("control_pnl"),
            pl.col("POSITION_PNL_v").alias("zone_pnl"),
            pl.col("holding_hours").alias("control_hold_h"),
            pl.col("holding_hours_v").alias("zone_hold_h"),
            pl.col("exit_reason").alias("control_exit"),
            pl.col("exit_reason_v").alias("zone_exit"),
            *[
                pl.col(f"{c}_v").alias(f"zone_{c}")
                for c in CONFIRM_COLS
                if f"{c}_v" in both.columns
            ],
        ).sort("detected_at_ms")
        d = (pairs["zone_R"] - pairs["control_R"]).to_numpy().astype(float)
        out["both_traded"] = {
            "n": pairs.height,
            "control_mean_R": _f(pairs["control_R"].mean()),
            "zone_mean_R": _f(pairs["zone_R"].mean()),
            "paired_mean_diff_R": float(d.mean()),
            "paired_t": float(d.mean() / (d.std(ddof=1) / math.sqrt(len(d))))
            if len(d) > 1 and d.std(ddof=1) > 0
            else math.nan,
            "frac_zone_better_R": float((d > 0).mean()),
            "control_win_rate": _f((pairs["control_pnl"] > 0).cast(pl.Float64).mean()),
            "zone_win_rate": _f((pairs["zone_pnl"] > 0).cast(pl.Float64).mean()),
            "mean_hours_earlier": _f(pairs["hours_earlier"].mean()),
            "median_hours_earlier": _f(pairs["hours_earlier"].median()),
            "frac_zone_earlier": _f((pairs["hours_earlier"] > 0).cast(pl.Float64).mean()),
            "mean_price_improvement_pct": _f(pairs["price_improvement_pct"].mean()),
            "frac_better_price": _f((pairs["price_improvement_pct"] > 0).cast(pl.Float64).mean()),
            "control_mean_stop_pct": _f(pairs["control_stop_pct"].mean()),
            "zone_mean_stop_pct": _f(pairs["zone_stop_pct"].mean()),
            "control_mean_MFE_R": _f(pairs["control_MFE_R"].mean()),
            "zone_mean_MFE_R": _f(pairs["zone_MFE_R"].mean()),
            "control_mean_MAE_R": _f(pairs["control_MAE_R"].mean()),
            "zone_mean_MAE_R": _f(pairs["zone_MAE_R"].mean()),
            "control_sum_pnl": _f(pairs["control_pnl"].sum()),
            "zone_sum_pnl": _f(pairs["zone_pnl"].sum()),
            "target_first_share": _f(
                (pairs["path_outcome"] == "target_first").cast(pl.Float64).mean()
            ),
            "by_family": pairs.group_by("family")
            .agg(
                pl.len().alias("n"),
                pl.col("control_R").mean().alias("control_mean_R"),
                pl.col("zone_R").mean().alias("zone_mean_R"),
                pl.col("hours_earlier").mean().alias("mean_hours_earlier"),
                pl.col("price_improvement_pct").mean().alias("mean_price_improvement_pct"),
            )
            .sort("family")
            .to_dicts(),
        }
    out["pairs"] = pairs
    return out


def _pop_table(df: pl.DataFrame, col: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in sorted(df[col].unique().to_list()):
        t = df.filter(pl.col(col) == key)
        pnl = t["POSITION_PNL"].to_numpy().astype(float)
        r = t["R_MULTIPLE"].to_numpy().astype(float)
        gw, gl = float(pnl[pnl > 0].sum()), float(-pnl[pnl <= 0].sum())
        rows.append(
            {
                col: key,
                "n": t.height,
                "win_rate": float((pnl > 0).mean()),
                "expectancy_R": float(r.mean()),
                "median_R": float(np.median(r)),
                "profit_factor": gw / gl if gl > 0 else math.inf,
                "mean_MFE_R": _f(t["MFE_R"].mean()),
                "mean_MAE_R": _f(t["MAE_R"].mean()),
                "sum_pnl": float(pnl.sum()),
                "fees_plus_slippage_per_trade": _f((t["fees"] + t["slippage"]).mean()),
                "funding_per_trade": _f(t["funding"].mean()),
                "target_first_share": _f(
                    (t["path_outcome"] == "target_first").cast(pl.Float64).mean()
                )
                if "path_outcome" in t.columns
                else math.nan,
                "mean_holding_hours": _f(t["holding_hours"].mean()),
            }
        )
    return rows


def never_triggered_recovery(control: Arm, variant: Arm) -> dict[str, Any]:
    c = _episode_keyed(control).filter(pl.col("outcome_class") == "NEVER_TRIGGERED")
    v = _episode_keyed(variant)
    j = c.select(
        [
            *KEY,
            "side",
            pl.col("end_reason").alias("control_end_reason"),
            pl.col("fwd_ret_24h").alias("control_fwd_24h"),
            pl.col("path_outcome").alias("control_path_outcome"),
        ]
    ).join(v, on=KEY, how="left")
    out: dict[str, Any] = {
        "control_never_triggered": c.height,
        "matched_in_variant": j.filter(pl.col("episode_id").is_not_null()).height,
        "variant_outcomes": j.filter(pl.col("episode_id").is_not_null())
        .group_by("outcome_class")
        .len()
        .sort("outcome_class")
        .to_dicts(),
        "control_fwd_24h_mean_all": _f(c["fwd_ret_24h"].mean()),
        "control_target_first_share_all": _f(
            (c["path_outcome"] == "target_first").cast(pl.Float64).mean()
        ),
    }
    rec = j.filter(pl.col("entry_ms").is_not_null())
    if rec.height:
        out["recovered_trades"] = _pop_table(
            rec.with_columns(pl.lit("RECOVERED").alias("grp")), "grp"
        )[0]
        out["recovered_by_family"] = _pop_table(rec, "family")
        out["recovered_by_control_end_reason"] = _pop_table(rec, "control_end_reason")
        out["recovered_fwd_24h_mean"] = _f(rec["control_fwd_24h"].mean())
        out["recovered_expectancy_R_gross"] = _f((rec["gross_pnl"] / rec["risk_amount"]).mean())
    else:
        out["recovered_trades"] = {"n": 0}
    return out


# --------------------------------------------------------------------------- runner
@dataclass
class Phase21Result:
    manifest: dict[str, Any]
    control: Arm
    variant: Arm
    matched: dict[str, Any]
    recovery: dict[str, Any]
    pit: dict[str, Any]
    baseline_check: dict[str, Any]
    segments: list[Segment]


def run_phase21(
    cfg: BtcStrategyConfig,
    inp: Phase2Inputs,
    segments: list[Segment],
    null_k: int,
    seed: int,
    out_dir: Path,
    expected_control_hash: str | None,
) -> Phase21Result:
    start_ms, end_ms = segments[0].start_ms, segments[-1].end_ms
    aux = AuxSeries.build(inp.funding, inp.metrics, inp.premium, inp.mark, cfg.data.latency_minutes)
    series = MultiTfSeries(inp.bars, cfg.indicators, cfg.regime.trend_slope_bars)
    cfg_c = cfg.model_copy(
        update={"experiment": cfg.experiment.model_copy(update={"entry_mode": "CONFIRMED_TRIGGER"})}
    )
    cfg_v = cfg.model_copy(
        update={"experiment": cfg.experiment.model_copy(update={"entry_mode": "ZONE_ENTRY"})}
    )
    log.info("CONTROL arm")
    control = run_arm("CONTROL", cfg_c, inp, aux, series, segments, null_k, seed)
    log.info("ZONE_ENTRY arm")
    variant = run_arm("ZONE_ENTRY", cfg_v, inp, aux, series, segments, null_k, seed)
    baseline_check = {
        "expected_phase2_result_hash": expected_control_hash,
        "control_result_hash": control.result.result_hash,
        "identical": expected_control_hash is not None
        and expected_control_hash == control.result.result_hash,
        "control_config_hash": cfg_c.config_hash,
        "variant_config_hash": cfg_v.config_hash,
        "note": "the config hash differs from Phase 2 only because the `experiment` section was added to the schema; the CONTROL result hash is the equivalence proof",
    }
    log.info("matched analysis")
    matched = matched_analysis(control, variant)
    recovery = never_triggered_recovery(control, variant)
    log.info("PIT audit (variant)")
    pit = {
        "visibility_rule": "bar visible iff close_time <= t; zone test uses the completed 5m bar at t; fill at the next 5m open",
        "zone_definition": "fixed at detection (SetupPlan is frozen); never moved after observing later prices",
        "control_deterministic": control.deterministic,
        "variant_deterministic": variant.deterministic,
        "variant_truncation": truncation_audit(
            cfg_v, inp, aux, variant.result, start_ms, start_ms + (end_ms - start_ms) // 2
        ),
    }
    manifest = {
        "phase": "2.1",
        "hypothesis": "ZONE_ENTRY vs CONFIRMED_TRIGGER",
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
    }
    res = Phase21Result(
        manifest, control, variant, matched, recovery, pit, baseline_check, segments
    )
    _persist(res, out_dir)
    return res


def _persist(res: Phase21Result, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(res.manifest, indent=1, sort_keys=True, default=str)
    )
    summary = {
        "baseline_check": res.baseline_check,
        "pit": res.pit,
        "matched": {k: v for k, v in res.matched.items() if k != "pairs"},
        "recovery": res.recovery,
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
