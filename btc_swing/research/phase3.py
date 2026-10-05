"""Phase 3 — UNTOUCHED CONFIRMATORY VALIDATION of the Phase 2.4 approved variant.

This is a confirmatory test, not a research phase. Two arms, both FROZEN:

CONTROL          = the frozen Phase 2 baseline (result hash 92ebe5d7... on 2022-01..2024-12).
APPROVED_VARIANT = CONTROL + the single owner-approved change
                   `experiment.block_short_in_trend_down = true` (Phase 2.4 classification A).

Protocol (encoded here so it cannot drift after results are seen):
  1. `freeze` writes `manifests/phase3_freeze_manifest.json`: both configs and hashes, the code
     commit, strategy versions, dataset hashes and coverage, the exact holdout dates, the proof that
     the local archive held no 2025+ period before Phase 3, and the pre-declared criteria with their
     numeric thresholds. It is committed BEFORE the holdout is evaluated.
  2. `run` refuses to start unless the live configs, dataset hashes and strategy code match the
     freeze; it first re-runs both arms on 2022-01..2024-12 and checks the result hashes against
     Phase 2 / Phase 2.4 (frozen-strategy proof), then runs each arm ONCE on the holdout.
  3. The classification is a pure function of the pre-declared criteria (`classify`).

Nothing in this module changes a threshold, a family, an exit, a regime rule or the risk model.
"""

from __future__ import annotations

import json
import logging
import math
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from btc_swing.backtest.engine import BacktestEngine
from btc_swing.core import versions
from btc_swing.core.config import BtcStrategyConfig
from btc_swing.core.enums import Regime
from btc_swing.features.context import AuxSeries
from btc_swing.features.view import MultiTfSeries
from btc_swing.research.phase2 import (
    Phase2Inputs,
    Segment,
    _ms,
    resample_oracle,
    truncation_audit,
)
from btc_swing.research.phase21 import Arm, run_arm
from btc_swing.research.phase24 import (
    _by,
    long_control_check,
    remaining_short,
    removed_trade_analysis,
    sequencing_split,
)

log = logging.getLogger(__name__)

VARIANT = "APPROVED_VARIANT"
VARIANT_RULE = "NO_NEW_SHORT_IN_TREND_DOWN (experiment.block_short_in_trend_down = true)"
FREEZE_PATH = Path("manifests/phase3_freeze_manifest.json")

# Holdout: 2025-01-01 -> the latest complete month published by the archive at run time.
HOLDOUT_START = "2025-01-01"
HOLDOUT_END = "2026-10-01"  # exclusive; 2026-09 is the last complete monthly archive period
SEGMENTS = [
    Segment("holdout_2025", _ms("2025-01-01"), _ms("2026-01-01")),
    Segment("holdout_2026_ytd", _ms("2026-01-01"), _ms(HOLDOUT_END)),
]

# Frozen references (committed in the Phase 2 / Phase 2.4 reports and manifests).
PHASE2_WINDOW = (_ms("2022-01-01"), _ms("2025-01-01"))
PHASE2_CONTROL_RESULT_HASH = "92ebe5d7fc65fc978ba4d3d222723e30c31d1db26e74d6e6585c786528c4ea56"
PHASE24_VARIANT_RESULT_HASH = "870c556507e7000dbfc3af06ee220eea259c08dd95a74fea2f8444b0a92029f6"
PHASE24_CONTROL_CONFIG_HASH = "5bfc1a7a7f3cca02ccab5c49fb7ef282b7a5998b487f532d1633012a03a8a4ce"
PHASE24_VARIANT_CONFIG_HASH = "ecb9d8a9060acf77f98a3f56539052b0ddc268c328850900bce448a7848f668d"
PHASE24_VARIANT_MAX_DD = 0.06482147409778874  # trade-curve max drawdown of the variant, 2022-2024

# Pre-declared confirmation criteria (owner's list, operationalised BEFORE any 2025+ result).
THRESHOLDS: dict[str, float] = {
    "c3_max_shortfall_vs_control_R": 0.05,
    "c5_drawdown_cap_multiplier": 1.5,
    "c5_drawdown_cap_frac": PHASE24_VARIANT_MAX_DD * 1.5,
    "c6_outlier_trades_removed": 2,
    "c7_min_trades_per_segment": 10,
    "c7_min_segment_expectancy_R": -0.25,
}
CRITERIA_TEXT: list[str] = [
    "1. net expectancy of APPROVED_VARIANT on the full holdout is positive (mean net R > 0)",
    "2. profit factor of APPROVED_VARIANT on the full holdout is > 1.0",
    "3. APPROVED_VARIANT is better than or materially no worse than CONTROL: net expectancy >= CONTROL net expectancy - 0.05R",
    "4. SHORT performance improves relative to CONTROL: APPROVED_VARIANT SHORT net expectancy > CONTROL SHORT net expectancy (an arm with no SHORT trades counts as 0R); the rule must have blocked at least one CONTROL trade, otherwise the criterion is NOT TESTABLE and cannot be met",
    "5. max drawdown (trade curve) of APPROVED_VARIANT <= 1.5 x the Phase 2.4 variant drawdown (6.48%), i.e. <= 9.72%",
    "6. not outlier-driven: (a) APPROVED_VARIANT net expectancy stays > 0 after removing its two best trades, and (b) if the variant beats CONTROL in net P&L, the lead survives after removing the two largest avoided losses among the blocked trades",
    "7. no catastrophic deterioration between 2025 and 2026 YTD: every chronological segment with >= 10 trades has net expectancy >= -0.25R and max drawdown <= 9.72%; a segment with < 10 trades is reported but cannot fail the criterion",
]
CLASSIFICATION_RULE = (
    "A — CONFIRMED: READY FOR PAPER FORWARD TESTING if all seven criteria are met; "
    "C — FAILED OUT-OF-SAMPLE if criterion 1 or 2 fails; "
    "B — MIXED: EDGE POSSIBLE BUT NOT CONFIRMED otherwise."
)
ALL_REGIMES = [r.value for r in Regime]


def _f(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, check=True, timeout=20
        ).stdout.strip()
    except Exception:
        return ""


def arm_configs(cfg: BtcStrategyConfig) -> tuple[BtcStrategyConfig, BtcStrategyConfig]:
    """CONTROL and APPROVED_VARIANT configs; every other experiment switch at its CONTROL value."""
    base_exp = {
        "entry_mode": "CONFIRMED_TRIGGER",
        "block_short_in_trend_down": False,
    }
    cfg_c = cfg.model_copy(
        update={
            "experiment": cfg.experiment.model_copy(update=base_exp),
            "exits": cfg.exits.model_copy(update={"breakeven_after_tp1": True}),
        }
    )
    cfg_v = cfg_c.model_copy(
        update={
            "experiment": cfg_c.experiment.model_copy(update={"block_short_in_trend_down": True})
        }
    )
    return cfg_c, cfg_v


# --------------------------------------------------------------------------- freeze
def _raw_archive_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"available": False}
    periods: list[str] = []
    for line in path.read_text().splitlines():
        if line.strip():
            periods.append(str(json.loads(line)["period"]))
    return {
        "available": True,
        "path": str(path),
        "entries": len(periods),
        "min_period": min(periods) if periods else None,
        "max_period": max(periods) if periods else None,
        "entries_at_or_after_2025": sum(1 for p in periods if p >= "2025"),
        "committed_in": _git("log", "-1", "--format=%h", "--", str(path)),
    }


def _prior_run_windows(runs_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not runs_dir.exists():
        return out
    for d in sorted(runs_dir.iterdir()):
        mp = d / "manifest.json"
        if not mp.exists():
            continue
        try:
            m = json.loads(mp.read_text())
        except json.JSONDecodeError:
            continue
        end = m.get("period_end_ms") or (m.get("control_manifest") or {}).get("period_end_ms")
        out.append(
            {
                "run": d.name,
                "period_end": datetime.fromtimestamp(int(end) / 1000, tz=UTC).date().isoformat()
                if end
                else None,
            }
        )
    return out


def _phase24_reference(summary_path: Path) -> dict[str, Any]:
    if not summary_path.exists():
        return {"available": False}
    s = json.loads(summary_path.read_text())
    arms = s.get("arms", {})
    ref: dict[str, Any] = {"available": True, "path": str(summary_path), "arms": {}}
    for name, a in arms.items():
        ref["arms"][name] = {
            "overall": a["metrics"]["overall"],
            "by_side": a["metrics"]["by_side"],
            "by_regime": a["metrics"]["by_regime"],
            "families": a["families"],
            "max_drawdown_frac_trade_curve": a["account"].get("max_drawdown_frac_trade_curve"),
            "segments": {
                k: {
                    "overall": v["metrics"]["overall"],
                    "families": v["families"],
                }
                for k, v in a["segments"].items()
            },
        }
    ref["baseline_check"] = s.get("baseline_check", {})
    return ref


def build_freeze(
    cfg: BtcStrategyConfig,
    inp: Phase2Inputs,
    raw_manifest_path: Path,
    runs_dir: Path,
    phase24_summary: Path,
    ingest_stats: dict[str, Any] | None,
) -> dict[str, Any]:
    cfg_c, cfg_v = arm_configs(cfg)
    return {
        "phase": "3",
        "purpose": "untouched confirmatory validation of the Phase 2.4 approved variant; one run per arm",
        "created_at": datetime.now(UTC).isoformat(),
        "code_commit": _git("rev-parse", "HEAD"),
        "code_version": versions.code_version(),
        "strategy_name": versions.STRATEGY_NAME,
        "strategy_version": versions.STRATEGY_VERSION,
        "rule_versions": {
            "features": versions.FEATURE_SET_VERSION,
            "regime": versions.REGIME_RULE_VERSION,
            "setups": versions.SETUP_RULE_VERSION,
            "episodes": versions.EPISODE_RULE_VERSION,
            "risk": versions.RISK_RULE_VERSION,
            "costs": versions.COST_MODEL_VERSION,
            "backtest": versions.BACKTEST_VERSION,
        },
        "holdout": {
            "start": HOLDOUT_START,
            "end_exclusive": HOLDOUT_END,
            "segments": [
                {"name": s.name, "start_ms": s.start_ms, "end_ms": s.end_ms} for s in SEGMENTS
            ],
        },
        "arms": {
            "CONTROL": {
                "config_hash": cfg_c.config_hash,
                "config_yaml": cfg_c.canonical_yaml(),
                "expected_config_hash_phase24": PHASE24_CONTROL_CONFIG_HASH,
                "expected_result_hash_2022_2024": PHASE2_CONTROL_RESULT_HASH,
            },
            VARIANT: {
                "rule": VARIANT_RULE,
                "config_hash": cfg_v.config_hash,
                "config_yaml": cfg_v.canonical_yaml(),
                "expected_config_hash_phase24": PHASE24_VARIANT_CONFIG_HASH,
                "expected_result_hash_2022_2024": PHASE24_VARIANT_RESULT_HASH,
            },
        },
        "experiment_switches": {
            "entry_mode": cfg_c.experiment.entry_mode,
            "breakeven_after_tp1": cfg_c.exits.breakeven_after_tp1,
            "block_short_in_trend_down": {"CONTROL": False, VARIANT: True},
        },
        "dataset_hashes": dict(inp.hashes),
        "coverage_holdout": inp.coverage,
        "ingest_stats_2025_plus": ingest_stats,
        "raw_archive_before_phase3": _raw_archive_state(raw_manifest_path),
        "prior_run_windows": _prior_run_windows(runs_dir),
        "criteria": CRITERIA_TEXT,
        "thresholds": THRESHOLDS,
        "classification_rule": CLASSIFICATION_RULE,
        "phase24_reference": _phase24_reference(phase24_summary),
    }


def verify_freeze(
    freeze: dict[str, Any], cfg: BtcStrategyConfig, inp: Phase2Inputs
) -> dict[str, Any]:
    cfg_c, cfg_v = arm_configs(cfg)
    frozen_commit = str(freeze.get("code_commit", ""))
    diff = (
        _git(
            "diff",
            "--stat",
            frozen_commit,
            "HEAD",
            "--",
            "btc_swing",
            "config",
            "pyproject.toml",
            "uv.lock",
        )
        if frozen_commit
        else "unknown"
    )
    checks = {
        "control_config_hash_matches_freeze": cfg_c.config_hash
        == freeze["arms"]["CONTROL"]["config_hash"],
        "variant_config_hash_matches_freeze": cfg_v.config_hash
        == freeze["arms"][VARIANT]["config_hash"],
        "control_config_hash_matches_phase24": cfg_c.config_hash == PHASE24_CONTROL_CONFIG_HASH,
        "variant_config_hash_matches_phase24": cfg_v.config_hash == PHASE24_VARIANT_CONFIG_HASH,
        "dataset_hashes_match_freeze": dict(inp.hashes) == dict(freeze["dataset_hashes"]),
        "strategy_code_unchanged_since_freeze": diff == "",
        "strategy_code_diff_since_freeze": diff,
        "frozen_commit": frozen_commit,
        "run_commit": _git("rev-parse", "HEAD"),
        "run_code_version": versions.code_version(),
    }
    checks["ok"] = all(
        bool(checks[k])
        for k in (
            "control_config_hash_matches_freeze",
            "variant_config_hash_matches_freeze",
            "dataset_hashes_match_freeze",
            "strategy_code_unchanged_since_freeze",
        )
    )
    return checks


# --------------------------------------------------------------------------- analyses
def frozen_strategy_proof(
    cfg_c: BtcStrategyConfig,
    cfg_v: BtcStrategyConfig,
    inp: Phase2Inputs,
    aux: AuxSeries,
    series: MultiTfSeries,
) -> dict[str, Any]:
    """Re-run both arms on the Phase 2 window with the current code and compare result hashes."""
    s, e = PHASE2_WINDOW
    rc = BacktestEngine(cfg_c, inp.bars, inp.funding, inp.hashes, aux, series).run(s, e)
    rv = BacktestEngine(cfg_v, inp.bars, inp.funding, inp.hashes, aux, series).run(s, e)
    return {
        "window": "2022-01-01 -> 2025-01-01",
        "control_result_hash": rc.result_hash,
        "control_expected": PHASE2_CONTROL_RESULT_HASH,
        "control_identical": rc.result_hash == PHASE2_CONTROL_RESULT_HASH,
        "control_trades": rc.manifest["n_trades"],
        "variant_result_hash": rv.result_hash,
        "variant_expected": PHASE24_VARIANT_RESULT_HASH,
        "variant_identical": rv.result_hash == PHASE24_VARIANT_RESULT_HASH,
        "variant_trades": rv.manifest["n_trades"],
    }


def _quarter(ms: int) -> str:
    d = datetime.fromtimestamp(ms / 1000, tz=UTC)
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def _month(ms: int) -> str:
    d = datetime.fromtimestamp(ms / 1000, tz=UTC)
    return f"{d.year}-{d.month:02d}"


def period_table(trades: pl.DataFrame, kind: str) -> list[dict[str, Any]]:
    if trades.is_empty():
        return []
    fn = _quarter if kind == "quarter" else _month
    t = trades.with_columns(
        pl.col("entry_ms").map_elements(lambda x: fn(int(x)), return_dtype=pl.Utf8).alias(kind)
    )
    return _by(t, kind)


def side_dd_contribution(trades: pl.DataFrame, initial_equity: float) -> dict[str, Any]:
    if trades.is_empty():
        return {"n_trades": 0}
    t = trades.sort("entry_ms")
    curve = np.concatenate(
        [[initial_equity], initial_equity + np.cumsum(t["POSITION_PNL"].to_numpy().astype(float))]
    )
    peak = np.maximum.accumulate(curve)
    trough_i = int(np.argmin(curve - peak))
    peak_i = int(np.argmax(curve[: trough_i + 1]))
    window = t.slice(peak_i, trough_i - peak_i)
    return {
        "n_trades": window.height,
        "max_dd_currency": float((curve - peak).min()),
        "max_dd_frac": float(((curve - peak) / peak).min()),
        "short_pnl_in_window": _f(window.filter(pl.col("side") == "SHORT")["POSITION_PNL"].sum())
        if window.height
        else 0.0,
        "long_pnl_in_window": _f(window.filter(pl.col("side") == "LONG")["POSITION_PNL"].sum())
        if window.height
        else 0.0,
        "peak_entry": _month(int(t["entry_ms"][peak_i])) if peak_i < t.height else None,
        "trough_entry": _month(int(t["entry_ms"][trough_i - 1])) if trough_i >= 1 else None,
    }


def leverage_audit(arm: Arm) -> dict[str, Any]:
    t = arm.trades
    if t.is_empty():
        return {"n": 0}
    return {
        "n": t.height,
        "leverage_distribution": t.group_by("leverage").len().sort("leverage").to_dicts(),
        "mean_leverage": _f(t["leverage"].mean()),
        "max_leverage": _f(t["leverage"].max()),
        "mean_margin_pct_equity": 100 * _f((t["margin"] / t["equity_at_entry"]).mean()),
        "max_margin_pct_equity": 100 * _f((t["margin"] / t["equity_at_entry"]).max()),
        "mean_account_risk_pct": 100 * _f(t["risk_frac"].mean()),
        "max_account_risk_pct": 100 * _f(t["risk_frac"].max()),
        "n_risk_capped": int(t["risk_capped"].sum()),
        "min_stop_to_liq_ratio": _f(t["stop_to_liquidation_ratio"].min()),
        "median_stop_to_liq_ratio": _f(t["stop_to_liquidation_ratio"].median()),
        "min_liq_distance_pct": 100 * _f(t["liquidation_distance_pct"].min()),
        "min_liq_distance_atr": _f(t["liquidation_distance_atr"].min()),
        "worst_MAE_pct": 100 * _f(t["MAE_PCT"].min()),
        "liquidations": int((t["exit_reason"] == "LIQUIDATION").sum()),
        "max_planned_account_loss_at_stop_pct": 100 * _f(t["max_account_loss_at_stop"].max()),
        "max_account_loss_if_liquidated_pct": 100 * _f(t["max_account_loss_at_liquidation"].max()),
        "liquidation_basis": str(t["liquidation_basis"][0]),
        "n_risk_rejected_episodes": int(
            arm.episodes.filter(pl.col("outcome_class") == "RISK_REJECTED").height
        )
        if arm.episodes.height
        else 0,
    }


def regime_table(arm: Arm) -> list[dict[str, Any]]:
    rows = {d["regime_at_entry"]: d for d in _by(arm.trades, "regime_at_entry")}
    return [rows.get(r, {"regime_at_entry": r, "n": 0}) for r in ALL_REGIMES]


def blocked_rows(removed: dict[str, Any]) -> pl.DataFrame:
    rr = removed.get("removed_rows")
    return rr if isinstance(rr, pl.DataFrame) else pl.DataFrame()


def outlier_checks(control: Arm, variant: Arm, removed: dict[str, Any]) -> dict[str, Any]:
    k = int(THRESHOLDS["c6_outlier_trades_removed"])
    out: dict[str, Any] = {"k": k}
    vt = variant.trades
    if vt.height > k:
        r = np.sort(vt["R_MULTIPLE"].to_numpy().astype(float))
        out["variant_expectancy_without_best_k"] = float(r[:-k].mean())
        out["variant_best_k_R"] = [float(x) for x in r[-k:]]
    else:
        out["variant_expectancy_without_best_k"] = math.nan
    total = (variant.costs.get("net", 0.0) or 0.0) - (control.costs.get("net", 0.0) or 0.0)
    out["net_pnl_lead_vs_control"] = total
    rr = blocked_rows(removed)
    if rr.height:
        pnl = np.sort(rr["POSITION_PNL"].to_numpy().astype(float))
        worst = pnl[:k]
        avoided = float(-worst[worst < 0].sum())
        out["largest_avoided_losses_pnl"] = [float(x) for x in worst]
        out["lead_without_largest_avoided_losses"] = total - avoided
    else:
        out["largest_avoided_losses_pnl"] = []
        out["lead_without_largest_avoided_losses"] = total
    return out


def evaluate_criteria(
    control: Arm, variant: Arm, removed: dict[str, Any], outliers: dict[str, Any]
) -> list[dict[str, Any]]:
    co, vo = control.metrics["overall"], variant.metrics["overall"]
    net_c = co.get("expectancy_R", 0.0) or 0.0
    net_v = vo.get("expectancy_R", 0.0) or 0.0
    pf_v = vo.get("profit_factor", 0.0) or 0.0
    sc = control.metrics["by_side"].get("SHORT", {}).get("expectancy_R", 0.0) or 0.0
    sv = variant.metrics["by_side"].get("SHORT", {}).get("expectancy_R", 0.0) or 0.0
    n_blocked = int(removed.get("removed_by_rule", 0) or 0)
    dd_v = abs(variant.account.get("max_drawdown_frac_trade_curve", 0.0) or 0.0)
    cap = THRESHOLDS["c5_drawdown_cap_frac"]
    exp_wo = outliers.get("variant_expectancy_without_best_k", math.nan)
    lead = outliers.get("net_pnl_lead_vs_control", 0.0)
    lead_wo = outliers.get("lead_without_largest_avoided_losses", 0.0)
    c6a = bool(not math.isnan(exp_wo) and exp_wo > 0)
    c6b = bool(lead <= 0 or lead_wo >= 0)
    seg_rows: list[str] = []
    c7 = True
    for name, sg in variant.segments.items():
        o = sg["metrics"]["overall"]
        n = int(o.get("n", 0) or 0)
        e = o.get("expectancy_R", 0.0) or 0.0
        dd = abs(sg["account"].get("max_drawdown_frac_trade_curve", 0.0) or 0.0)
        if n >= THRESHOLDS["c7_min_trades_per_segment"]:
            ok = e >= THRESHOLDS["c7_min_segment_expectancy_R"] and dd <= cap
            c7 = c7 and ok
            seg_rows.append(
                f"{name}: n={n}, {e:+.3f}R, DD {dd * 100:.2f}% -> {'ok' if ok else 'FAIL'}"
            )
        else:
            seg_rows.append(f"{name}: n={n} (< 10, reported only), {e:+.3f}R, DD {dd * 100:.2f}%")
    return [
        {
            "id": 1,
            "text": CRITERIA_TEXT[0],
            "met": net_v > 0,
            "evidence": f"APPROVED_VARIANT net expectancy {net_v:+.3f}R (n={vo.get('n', 0)})",
        },
        {
            "id": 2,
            "text": CRITERIA_TEXT[1],
            "met": pf_v > 1.0,
            "evidence": f"profit factor {pf_v:.2f}",
        },
        {
            "id": 3,
            "text": CRITERIA_TEXT[2],
            "met": net_v >= net_c - THRESHOLDS["c3_max_shortfall_vs_control_R"],
            "evidence": f"CONTROL {net_c:+.3f}R vs APPROVED_VARIANT {net_v:+.3f}R (difference {net_v - net_c:+.3f}R; floor -0.050R)",
        },
        {
            "id": 4,
            "text": CRITERIA_TEXT[3],
            "met": n_blocked > 0 and sv > sc,
            "evidence": f"SHORT CONTROL {sc:+.3f}R (n={control.metrics['by_side'].get('SHORT', {}).get('n', 0)}) vs APPROVED_VARIANT {sv:+.3f}R (n={variant.metrics['by_side'].get('SHORT', {}).get('n', 0)}); trades blocked by the rule: {n_blocked}"
            + ("" if n_blocked else " -> NOT TESTABLE"),
        },
        {
            "id": 5,
            "text": CRITERIA_TEXT[4],
            "met": dd_v <= cap,
            "evidence": f"max DD {dd_v * 100:.2f}% vs cap {cap * 100:.2f}%",
        },
        {
            "id": 6,
            "text": CRITERIA_TEXT[5],
            "met": c6a and c6b,
            "evidence": f"(a) expectancy without best {outliers.get('k')} trades {exp_wo:+.3f}R; (b) net P&L lead {lead:+.0f} USDT, without the {outliers.get('k')} largest avoided losses {lead_wo:+.0f} USDT",
        },
        {
            "id": 7,
            "text": CRITERIA_TEXT[6],
            "met": c7,
            "evidence": "; ".join(seg_rows),
        },
    ]


def classify(criteria: list[dict[str, Any]]) -> str:
    met = {c["id"]: bool(c["met"]) for c in criteria}
    if all(met.values()):
        return "A — CONFIRMED: READY FOR PAPER FORWARD TESTING"
    if not met[1] or not met[2]:
        return "C — FAILED OUT-OF-SAMPLE"
    return "B — MIXED: EDGE POSSIBLE BUT NOT CONFIRMED"


# --------------------------------------------------------------------------- runner
@dataclass
class Phase3Result:
    manifest: dict[str, Any]
    freeze: dict[str, Any]
    freeze_check: dict[str, Any]
    proof: dict[str, Any]
    control: Arm
    variant: Arm
    removed: dict[str, Any]
    remaining: dict[str, Any]
    longs: dict[str, Any]
    sequencing: dict[str, Any]
    pit: dict[str, Any]
    quarters: dict[str, list[dict[str, Any]]]
    months: dict[str, list[dict[str, Any]]]
    dd_sides: dict[str, dict[str, Any]]
    leverage: dict[str, dict[str, Any]]
    regimes: dict[str, list[dict[str, Any]]]
    outliers: dict[str, Any]
    criteria: list[dict[str, Any]]
    classification: str
    segments: list[Segment]


def run_phase3(
    cfg: BtcStrategyConfig,
    inp: Phase2Inputs,
    freeze: dict[str, Any],
    null_k: int,
    seed: int,
    out_dir: Path,
) -> Phase3Result:
    freeze_check = verify_freeze(freeze, cfg, inp)
    if not freeze_check["ok"]:
        raise RuntimeError(f"freeze verification failed: {json.dumps(freeze_check, indent=1)}")
    segments = SEGMENTS
    start_ms, end_ms = segments[0].start_ms, segments[-1].end_ms
    cfg_c, cfg_v = arm_configs(cfg)
    aux = AuxSeries.build(inp.funding, inp.metrics, inp.premium, inp.mark, cfg.data.latency_minutes)
    series = MultiTfSeries(inp.bars, cfg.indicators, cfg.regime.trend_slope_bars)
    log.info("frozen-strategy proof (2022-2024 reruns)")
    proof = frozen_strategy_proof(cfg_c, cfg_v, inp, aux, series)
    if not (proof["control_identical"] and proof["variant_identical"]):
        raise RuntimeError(f"frozen-strategy proof failed: {json.dumps(proof, indent=1)}")
    log.info("CONTROL arm (holdout)")
    control = run_arm("CONTROL", cfg_c, inp, aux, series, segments, null_k, seed, phase="3")
    log.info("%s arm (holdout)", VARIANT)
    variant = run_arm(VARIANT, cfg_v, inp, aux, series, segments, null_k, seed, phase="3")
    log.info("analysis")
    removed = removed_trade_analysis(control, variant, segments)
    longs = long_control_check(control, variant)
    outliers = outlier_checks(control, variant, removed)
    criteria = evaluate_criteria(control, variant, removed, outliers)
    natives_holdout = {
        tf: df.filter(pl.col("open_time_ms") >= start_ms) if not df.is_empty() else df
        for tf, df in inp.natives.items()
    }
    res = Phase3Result(
        manifest={
            "phase": "3",
            "hypothesis": f"{VARIANT} ({VARIANT_RULE}) retains its benefit on untouched 2025+ data",
            "period_start_ms": start_ms,
            "period_end_ms": end_ms,
            "segments": [
                {"name": s.name, "start_ms": s.start_ms, "end_ms": s.end_ms} for s in segments
            ],
            "null_k": null_k,
            "seed": seed,
            "freeze_manifest": str(FREEZE_PATH),
            "freeze_created_at": freeze.get("created_at"),
            "freeze_commit": freeze.get("code_commit"),
            "freeze_check": freeze_check,
            "frozen_strategy_proof": proof,
            "control_manifest": control.result.manifest,
            "variant_manifest": variant.result.manifest,
            "config_yaml_control": cfg_c.canonical_yaml(),
            "config_yaml_variant": cfg_v.canonical_yaml(),
            "runs_per_arm_on_holdout": 1,
            "generated_at": datetime.now(UTC).isoformat(),
        },
        freeze=freeze,
        freeze_check=freeze_check,
        proof=proof,
        control=control,
        variant=variant,
        removed=removed,
        remaining=remaining_short(variant, control),
        longs=longs,
        sequencing=sequencing_split(control, variant, removed, longs),
        pit={
            "visibility_rule": "bar visible iff close_time <= t; aux features iff time + latency <= t; funding applied in (prev close, t]; the regime used by the rule is the PIT regime at the trigger decision bar (completed 1d/4h bars only); fill at the next 5m open",
            "rule_scope": "entry eligibility only; open positions are never closed by a regime change",
            "control_deterministic": control.deterministic,
            "variant_deterministic": variant.deterministic,
            "variant_truncation": truncation_audit(
                cfg_v, inp, aux, variant.result, start_ms, start_ms + (end_ms - start_ms) // 2
            ),
            "resample_oracle_holdout": resample_oracle(
                inp.bars.filter(pl.col("open_time_ms") >= start_ms - 86_400_000 * 40),
                natives_holdout,
            ),
            "liquidation_basis": str(variant.trades["liquidation_basis"][0])
            if variant.trades.height
            else "n/a",
        },
        quarters={a.name: period_table(a.trades, "quarter") for a in (control, variant)},
        months={a.name: period_table(a.trades, "month") for a in (control, variant)},
        dd_sides={
            a.name: side_dd_contribution(a.trades, a.cfg.risk.initial_equity)
            for a in (control, variant)
        },
        leverage={a.name: leverage_audit(a) for a in (control, variant)},
        regimes={a.name: regime_table(a) for a in (control, variant)},
        outliers=outliers,
        criteria=criteria,
        classification=classify(criteria),
        segments=segments,
    )
    _persist(res, out_dir)
    return res


def _persist(res: Phase3Result, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(res.manifest, indent=1, sort_keys=True, default=str)
    )
    summary = {
        "classification": res.classification,
        "criteria": res.criteria,
        "freeze_check": res.freeze_check,
        "frozen_strategy_proof": res.proof,
        "pit": res.pit,
        "removed": {k: v for k, v in res.removed.items() if k != "removed_rows"},
        "remaining": res.remaining,
        "longs": res.longs,
        "sequencing": res.sequencing,
        "quarters": res.quarters,
        "months": res.months,
        "dd_sides": res.dd_sides,
        "leverage": res.leverage,
        "regimes": res.regimes,
        "outliers": res.outliers,
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
    rr = blocked_rows(res.removed)
    if not rr.is_empty():
        rr.write_parquet(out_dir / "blocked_short_trades.parquet")


__all__ = [
    "SEGMENTS",
    "THRESHOLDS",
    "VARIANT",
    "Phase3Result",
    "arm_configs",
    "build_freeze",
    "classify",
    "evaluate_criteria",
    "run_phase3",
    "verify_freeze",
]
