"""V6 research orchestrator (Phases 2-5 of docs/BTC_V6_ACTIVE_INTRADAY_DESIGN.md).

Protocol (pre-registered, executed in this order, nothing revisited after a result is read):
  development  -> per-family runs, combined run, exit-variant comparison (pre-declared selection
                  rule), one-position diagnostics (sequential / independent), 3-point threshold
                  grids (stability only), risk grid (analytic, R-based), overlay grid, maker
                  variant for the pullback family, regime/time-matched nulls
  validation   -> the FROZEN specification only (variant chosen on development): per-family
                  criteria, combined statistics, the REQUIRED frequency report, the HARD gate
  once-only OOS -> only with the explicit flag, exactly once (a lock file refuses a second run)
Every table is persisted (parquet/JSON) under `reports/v6/`."""

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

from btc_swing.core.hashing import stable_hash
from btc_swing.core.versions import code_version
from btc_swing.v6.config import HISTORICAL_FAMILIES, V6Config, V6Family
from btc_swing.v6.data import iso, load_v6_inputs, ms
from btc_swing.v6.engine import V6Engine, V6Result
from btc_swing.v6.evaluation import (
    _f,
    combined_gate,
    family_criteria,
    fold_table,
    frequency_report,
    group_table,
    regime_null,
    regime_rows,
    trade_stats,
    with_calendar,
)
from btc_swing.v6.features import V6Frame, V6Inputs, build_frame

log = logging.getLogger(__name__)
DAY_MS = 86_400_000
OOS_LOCK = "OOS_ONCE_ONLY_LOCK.json"


@dataclass
class Window:
    name: str
    start_ms: int
    end_ms: int

    @property
    def days(self) -> float:
        return (self.end_ms - self.start_ms) / DAY_MS


@dataclass
class V6Research:
    manifest: dict[str, Any]
    development: dict[str, Any]
    validation: dict[str, Any]
    frozen: dict[str, Any]
    bottleneck: dict[str, Any]
    oos: dict[str, Any] | None = None
    frames: dict[str, pl.DataFrame] = field(default_factory=dict)


def windows(cfg: V6Config) -> dict[str, Window]:
    r = cfg.research
    return {
        "development": Window(
            "development", ms(r.development.start), ms(r.development.end_exclusive)
        ),
        "validation": Window("validation", ms(r.validation.start), ms(r.validation.end_exclusive)),
        "oos": Window(
            "oos_once_only", ms(r.oos_once_only.start), ms(r.oos_once_only.end_exclusive)
        ),
    }


# ----------------------------------------------------------------------------- run helpers
def _run(cfg: V6Config, fr: V6Frame, inp: V6Inputs, w: Window, **kw: Any) -> V6Result:
    eng = V6Engine(cfg, fr, inp.funding, data_hashes=inp.hashes, **kw)
    return eng.run(w.start_ms, w.end_ms)


def _summary(res: V6Result, fr: V6Frame, w: Window, cfg: V6Config) -> dict[str, Any]:
    t = res.trades
    st = trade_stats(t, w.start_ms, w.end_ms, cfg)
    fq = frequency_report(t, fr, w.start_ms, w.end_ms, cfg)
    tc = with_calendar(t)
    out: dict[str, Any] = {
        "manifest": res.manifest,
        "stats": st,
        "frequency": fq,
        "events_per_detector": res.events_per_detector,
        "refused": res.refused.group_by("reason").len().sort("len", descending=True).to_dicts()
        if res.refused.height
        else [],
        "refused_counterfactual": _counterfactual(res.refused),
        "episode_end_reasons": res.episodes.group_by("end_reason")
        .len()
        .sort("len", descending=True)
        .to_dicts()
        if res.episodes.height
        else [],
        "by_family_side": group_table(tc, ["family", "side"], cfg, w.start_ms, w.end_ms),
        "by_family": group_table(tc, ["family"], cfg, w.start_ms, w.end_ms),
        "by_side": group_table(tc, ["side"], cfg, w.start_ms, w.end_ms),
        "by_year": group_table(tc, ["year"], cfg, w.start_ms, w.end_ms),
        "by_regime": regime_rows(t, cfg, w.start_ms, w.end_ms),
        "by_session": group_table(tc, ["session"], cfg, w.start_ms, w.end_ms),
        "by_weekend": group_table(tc, ["weekend"], cfg, w.start_ms, w.end_ms),
        "by_exit_variant_reason": group_table(tc, ["exit_reason"], cfg, w.start_ms, w.end_ms),
        "funnel": _funnel(res, w),
    }
    return out


def _counterfactual(ref: pl.DataFrame) -> dict[str, Any]:
    if ref.is_empty():
        return {"n": 0}
    r = ref.filter(pl.col("counterfactual_net_r").is_not_nan())
    out: dict[str, Any] = {"n": ref.height}
    for reason, sub in r.group_by("reason"):
        x = sub["counterfactual_net_r"].to_numpy()
        out[str(reason[0])] = {
            "n": sub.height,
            "mean_net_r": float(x.mean()) if len(x) else math.nan,
            "sum_net_r": float(x.sum()) if len(x) else math.nan,
            "win_rate": float(np.mean(x > 0)) if len(x) else math.nan,
            "median_hold_min": _f(sub["counterfactual_hold_min"].median()) if len(x) else math.nan,
        }
    return out


def _funnel(res: V6Result, w: Window) -> dict[str, Any]:
    d = w.days
    ev = sum(v["events"] for v in res.events_per_detector.values())
    fic = sum(v["first_in_cluster"] for v in res.events_per_detector.values())
    eps = sum(v["episodes"] for v in res.events_per_detector.values())
    e = res.episodes
    actionable = int((e["first_actionable_k"] >= 0).sum()) if e.height else 0
    n_tr = res.trades.height
    n_ref = res.refused.height
    return {
        "days": d,
        "events_per_day": ev / d,
        "first_in_cluster_per_day": fic / d,
        "episodes_per_day": eps / d,
        "actionable_per_day": actionable / d,
        "triggers_per_day": (n_tr + n_ref) / d,
        "refused_per_day": n_ref / d,
        "trades_per_day": n_tr / d,
        "stage_b_conversion": actionable / eps if eps else math.nan,
        "trigger_to_trade": n_tr / (n_tr + n_ref) if (n_tr + n_ref) else math.nan,
    }


def _risk_grid(t: pl.DataFrame, cfg: V6Config) -> list[dict[str, Any]]:
    """R-denominated results are identical across risk levels (no margin cap binds at <= 0.25%);
    drawdown and account P&L scale with the risk fraction."""
    if t.is_empty():
        return []
    r = t["net_r"].to_numpy().astype(np.float64)
    eq_r = np.concatenate([[0.0], np.cumsum(r)])
    dd_r = float(np.max(np.maximum.accumulate(eq_r) - eq_r))
    out = []
    for rk in cfg.risk.risk_grid:
        out.append(
            {
                "risk_per_trade": rk,
                "primary": abs(rk - cfg.risk.risk_per_trade) < 1e-12,
                "net_return_frac": float(r.sum()) * rk,
                "max_dd_frac": dd_r * rk,
                "max_dd_r": dd_r,
                "worst_day_frac": _worst_day(t) * rk,
            }
        )
    return out


def _worst_day(t: pl.DataFrame) -> float:
    d = (
        t.with_columns((pl.col("entry_ms") // DAY_MS).alias("d"))
        .group_by("d")
        .agg(pl.col("net_r").sum())
    )
    return _f(d["net_r"].min()) if d.height else 0.0


# ----------------------------------------------------------------------------- development
def _variant_rows(
    cfg: V6Config, fr: V6Frame, inp: V6Inputs, w: Window
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = []
    for vname in cfg.exits.variants:
        for be in (True, False):
            res = _run(cfg, fr, inp, w, variant=vname, breakeven=be)
            st = trade_stats(res.trades, w.start_ms, w.end_ms, cfg)
            fq = frequency_report(res.trades, fr, w.start_ms, w.end_ms, cfg)
            rows.append(
                {
                    "variant": vname,
                    "breakeven_after_tp1": be,
                    "description": cfg.exits.variants[vname].description,
                    "n": st["n"],
                    "trades_per_calendar_day": fq["mean_trades_per_calendar_day"],
                    "net_r_sum": st.get("net_r_sum", 0.0),
                    "net_r_mean": st.get("net_r_mean", math.nan),
                    "net_r_per_day": st.get("net_r_sum", 0.0) / w.days,
                    "profit_factor": st.get("profit_factor", math.nan),
                    "median_hold_min": st.get("median_hold_min", math.nan),
                    "max_dd_frac": st.get("max_dd_frac", math.nan),
                    "win_rate": st.get("win_rate", math.nan),
                    "exit_reasons": st.get("exit_reasons", []),
                }
            )
    mn = cfg.research.combined_criteria.min_trades_per_calendar_day
    eligible = [r for r in rows if r["trades_per_calendar_day"] >= mn]
    rule = (
        f"pre-declared: among variants with >= {mn:.1f} trades per calendar day on development, the "
        "highest net R per day; if none qualifies, the highest net R per day of all (flagged)"
    )
    pool = eligible or rows
    best = max(pool, key=lambda r: (r["net_r_per_day"], r["trades_per_calendar_day"]))
    chosen = {
        "variant": best["variant"],
        "breakeven_after_tp1": best["breakeven_after_tp1"],
        "rule": rule,
        "frequency_requirement_met_on_development": bool(eligible),
        "net_r_per_day": best["net_r_per_day"],
    }
    return rows, chosen


def _grid_rows(cfg: V6Config, fr_builder: Any, inp: V6Inputs, w: Window) -> list[dict[str, Any]]:
    """One threshold at a time on development, the family alone; values are reported for stability
    (monotone / plateau), never selected."""
    fam = cfg.families
    grids: list[tuple[V6Family, str, str, list[float]]] = [
        (
            V6Family.MOMENTUM_CONTINUATION,
            "momentum_continuation",
            "rel_vol_15m_min",
            fam.momentum_continuation.rel_vol_grid,
        ),
        (
            V6Family.MOMENTUM_CONTINUATION,
            "momentum_continuation",
            "max_extension_atr5",
            fam.momentum_continuation.extension_grid,
        ),
        (
            V6Family.PULLBACK_CONTINUATION,
            "pullback_continuation",
            "impulse_min_atr1h",
            fam.pullback_continuation.impulse_grid,
        ),
        (
            V6Family.FAILED_BREAKOUT_REVERSAL,
            "failed_breakout_reversal",
            "rel_vol_5m_min",
            fam.failed_breakout_reversal.rel_vol_grid,
        ),
        (
            V6Family.FLOW_OI_IMPULSE,
            "flow_oi_impulse",
            "ret_15m_min_atr15",
            fam.flow_oi_impulse.ret_grid,
        ),
    ]
    rows = []
    for family, section, key, values in grids:
        for v in values:
            c2 = cfg.model_copy(
                update={
                    "families": fam.model_copy(
                        update={section: getattr(fam, section).model_copy(update={key: v})}
                    )
                }
            )
            fr2 = fr_builder(c2)
            res = _run(c2, fr2, inp, w, families=[family])
            st = trade_stats(res.trades, w.start_ms, w.end_ms, cfg)
            rows.append(
                {
                    "family": family.value,
                    "parameter": key,
                    "value": v,
                    "default": abs(v - getattr(getattr(fam, section), key)) < 1e-12,
                    "n": st["n"],
                    "trades_per_day": st["trades_per_day"],
                    "net_r_mean": st.get("net_r_mean", math.nan),
                    "net_r_sum": st.get("net_r_sum", 0.0),
                    "profit_factor": st.get("profit_factor", math.nan),
                }
            )
    return rows


def _overlay_rows(
    cfg: V6Config, fr: V6Frame, inp: V6Inputs, w: Window, variant: str, be: bool
) -> list[dict[str, Any]]:
    rows = []
    for nl in cfg.overlay.losses_grid:
        for dl in cfg.overlay.daily_loss_grid:
            res = _run(
                cfg,
                fr,
                inp,
                w,
                variant=variant,
                breakeven=be,
                overlay=True,
                overlay_losses=nl,
                overlay_daily_loss=dl,
            )
            st = trade_stats(res.trades, w.start_ms, w.end_ms, cfg)
            blocked = (
                int((res.refused["reason"] == "BLOCKED_DAILY_BUDGET").sum())
                if res.refused.height
                else 0
            )
            rows.append(
                {
                    "max_full_risk_losses_per_day": nl,
                    "max_daily_loss_frac": dl,
                    "default": nl == cfg.overlay.max_full_risk_losses_per_day
                    and abs(dl - cfg.overlay.max_daily_loss_frac) < 1e-12,
                    "n": st["n"],
                    "trades_per_day": st["trades_per_day"],
                    "net_r_sum": st.get("net_r_sum", 0.0),
                    "max_dd_frac": st.get("max_dd_frac", math.nan),
                    "worst_day_frac": _worst_day(res.trades) * cfg.risk.risk_per_trade
                    if res.trades.height
                    else 0.0,
                    "blocked_by_budget": blocked,
                    "days_blocked": int(res.daily["budget_blocked"].sum())
                    if res.daily.height
                    else 0,
                }
            )
    return rows


def _family_block(
    cfg: V6Config,
    fr: V6Frame,
    inp: V6Inputs,
    w: Window,
    fam: V6Family,
    variant: str | None,
    be: bool | None,
    null: bool,
    seed: int,
) -> dict[str, Any]:
    res = _run(cfg, fr, inp, w, families=[fam], variant=variant, breakeven=be)
    s = _summary(res, fr, w, cfg)
    s["folds_criteria"] = fold_table(
        res.trades, w.start_ms, w.end_ms, cfg.research.family_criteria.folds, cfg
    )
    s["folds_stability"] = fold_table(
        res.trades, w.start_ms, w.end_ms, cfg.research.stability_folds, cfg
    )
    st = s["stats"]
    s["cost_flag"] = (
        bool(st.get("cost_share_of_median_stop", 0) > cfg.costs.max_cost_share_of_stop)
        if st["n"]
        else False
    )
    if null and res.trades.height:
        eng = V6Engine(cfg, fr, inp.funding, families=[fam], variant=variant, breakeven=be)
        s["null"] = regime_null(
            eng, res.trades, fr, w.start_ms, w.end_ms, cfg.research.null_k, seed
        )
    s["_trades"] = res.trades
    s["_episodes"] = res.episodes
    return s


def run_development(
    cfg: V6Config, fr: V6Frame, inp: V6Inputs, w: Window, fr_builder: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    seed = cfg.research.seed
    dev: dict[str, Any] = {
        "window": {"start": iso(w.start_ms), "end_exclusive": iso(w.end_ms), "days": w.days}
    }
    log.info("development: per-family runs")
    dev["families"] = {
        f.value: _family_block(cfg, fr, inp, w, f, None, None, True, seed + i)
        for i, f in enumerate(HISTORICAL_FAMILIES)
    }
    log.info("development: combined default run")
    comb = _run(cfg, fr, inp, w)
    dev["combined_default"] = _summary(comb, fr, w, cfg)
    log.info("development: exit variants")
    dev["exit_variants"], chosen = _variant_rows(cfg, fr, inp, w)
    frozen = {
        "exit_variant": chosen["variant"],
        "breakeven_after_tp1": chosen["breakeven_after_tp1"],
        "selection": chosen,
        "overlay": {
            "max_full_risk_losses_per_day": cfg.overlay.max_full_risk_losses_per_day,
            "max_daily_loss_frac": cfg.overlay.max_daily_loss_frac,
            "note": "pre-registered default; grid reported for tail-risk only",
        },
        "risk_per_trade": cfg.risk.risk_per_trade,
        "families": [f.value for f in cfg.enabled_families()],
        "mode": "baseline (one position, cooldowns)",
    }
    v, be = frozen["exit_variant"], frozen["breakeven_after_tp1"]
    log.info("development: frozen combined + diagnostics")
    comb_f = _run(cfg, fr, inp, w, variant=v, breakeven=be)
    dev["combined_frozen"] = _summary(comb_f, fr, w, cfg)
    dev["combined_frozen"]["risk_grid"] = _risk_grid(comb_f.trades, cfg)
    dev["combined_frozen"]["folds_stability"] = fold_table(
        comb_f.trades, w.start_ms, w.end_ms, cfg.research.stability_folds, cfg
    )
    dev["modes"] = {}
    for mode in ("sequential", "independent"):
        r = _run(cfg, fr, inp, w, variant=v, breakeven=be, mode=mode)
        dev["modes"][mode] = _summary(r, fr, w, cfg)
    dev["one_position_rule"] = _one_position(comb_f, dev["modes"], w)
    log.info("development: grids / overlay / maker")
    dev["grids"] = _grid_rows(cfg, fr_builder, inp, w)
    dev["overlay_grid"] = _overlay_rows(cfg, fr, inp, w, v, be)
    mk = _run(
        cfg,
        fr,
        inp,
        w,
        families=[V6Family.PULLBACK_CONTINUATION],
        variant=v,
        breakeven=be,
        maker_entry=True,
    )
    dev["maker_pullback"] = trade_stats(mk.trades, w.start_ms, w.end_ms, cfg)
    dev["_trades_combined_frozen"] = comb_f.trades
    dev["_refused_combined_frozen"] = comb_f.refused
    dev["_episodes_combined_frozen"] = comb_f.episodes
    return dev, frozen


def _one_position(base: V6Result, modes: dict[str, Any], w: Window) -> dict[str, Any]:
    ref = base.refused
    n_blocked = int((ref["reason"] == "BLOCKED_POSITION_OPEN").sum()) if ref.height else 0
    n_cool = int((ref["reason"] == "BLOCKED_COOLDOWN_AFTER_CLOSE").sum()) if ref.height else 0
    cf = _counterfactual(ref)
    return {
        "trades_taken": base.trades.height,
        "valid_triggers_refused_position_open": n_blocked,
        "valid_triggers_refused_cooldown_after_close": n_cool,
        "suppressed_per_day": (n_blocked + n_cool) / w.days,
        "suppression_share": (n_blocked + n_cool) / max(base.trades.height + n_blocked + n_cool, 1),
        "counterfactual": cf,
        "sequential_mode_trades_per_day": modes["sequential"]["frequency"][
            "mean_trades_per_calendar_day"
        ],
        "sequential_mode_net_r_mean": modes["sequential"]["stats"].get("net_r_mean", math.nan),
        "independent_mode_trades_per_day": modes["independent"]["frequency"][
            "mean_trades_per_calendar_day"
        ],
        "independent_mode_net_r_mean": modes["independent"]["stats"].get("net_r_mean", math.nan),
        "note": "sequential = episodes wait for the slot and enter right after a close (no cooldown after close); independent = every valid trigger simulated on its own (overlaps allowed, no slot)",
    }


# ----------------------------------------------------------------------------- validation / OOS
def run_frozen_window(
    cfg: V6Config,
    fr: V6Frame,
    inp: V6Inputs,
    w: Window,
    frozen: dict[str, Any],
    dev: dict[str, Any] | None,
) -> dict[str, Any]:
    v, be = frozen["exit_variant"], frozen["breakeven_after_tp1"]
    seed = cfg.research.seed + 100
    out: dict[str, Any] = {
        "window": {"start": iso(w.start_ms), "end_exclusive": iso(w.end_ms), "days": w.days}
    }
    fams: dict[str, Any] = {}
    for i, f in enumerate(HISTORICAL_FAMILIES):
        blk = _family_block(cfg, fr, inp, w, f, v, be, True, seed + i)
        folds_dev = dev["families"][f.value]["folds_criteria"] if dev else []
        blk["criteria"] = family_criteria(blk["stats"], folds_dev, cfg)
        fams[f.value] = blk
    out["families"] = fams
    comb = _run(cfg, fr, inp, w, variant=v, breakeven=be)
    s = _summary(comb, fr, w, cfg)
    s["risk_grid"] = _risk_grid(comb.trades, cfg)
    s["gate"] = combined_gate(s["stats"], s["frequency"], s["by_year"], s["by_regime"], cfg)
    if comb.trades.height:
        eng = V6Engine(cfg, fr, inp.funding, variant=v, breakeven=be)
        s["null"] = regime_null(
            eng, comb.trades, fr, w.start_ms, w.end_ms, cfg.research.null_k, seed + 50
        )
    ov = _run(cfg, fr, inp, w, variant=v, breakeven=be, overlay=True)
    s["with_overlay"] = {
        "stats": trade_stats(ov.trades, w.start_ms, w.end_ms, cfg),
        "frequency": frequency_report(ov.trades, fr, w.start_ms, w.end_ms, cfg),
    }
    modes = {}
    for mode in ("sequential", "independent"):
        r = _run(cfg, fr, inp, w, variant=v, breakeven=be, mode=mode)
        modes[mode] = _summary(r, fr, w, cfg)
    s["modes"] = modes
    s["one_position_rule"] = _one_position(comb, modes, w)
    s["_trades"] = comb.trades
    s["_refused"] = comb.refused
    s["_episodes"] = comb.episodes
    out["combined"] = s
    return out


# ----------------------------------------------------------------------------- bottleneck
def bottleneck_analysis(val: dict[str, Any], cfg: V6Config) -> dict[str, Any]:
    c = val["combined"]
    st, fq, fn, op = c["stats"], c["frequency"], c["funnel"], c["one_position_rule"]
    need = cfg.research.combined_criteria.min_trades_per_calendar_day
    tpd = fq["mean_trades_per_calendar_day"]
    items: list[dict[str, Any]] = []

    def add(name: str, measure: str, value: float, verdict: str) -> None:
        items.append({"bottleneck": name, "measure": measure, "value": value, "verdict": verdict})

    add(
        "event scarcity",
        "first-in-cluster events per day (all families, both sides)",
        fn["first_in_cluster_per_day"],
        "binding" if fn["first_in_cluster_per_day"] < 2 * need else "not binding",
    )
    add(
        "Stage B confirmation",
        "episodes that ever became ACTIONABLE / episodes opened",
        fn["stage_b_conversion"],
        "binding"
        if (fn["stage_b_conversion"] or 0) < 0.35
        else "moderate"
        if (fn["stage_b_conversion"] or 0) < 0.6
        else "not binding",
    )
    add(
        "one-position rule",
        "valid triggers refused while a position was open, per day",
        op["valid_triggers_refused_position_open"] / fq["calendar_days"],
        "binding"
        if op["suppression_share"] > 0.3
        else "moderate"
        if op["suppression_share"] > 0.15
        else "not binding",
    )
    add(
        "hold duration",
        "median hold (min) of executed trades",
        st.get("median_hold_min", math.nan),
        "binding"
        if (st.get("median_hold_min") or 0) > 240
        else "moderate"
        if (st.get("median_hold_min") or 0) > 120
        else "not binding",
    )
    add(
        "costs",
        "round-trip cost as a share of the median stop",
        st.get("cost_share_of_median_stop", math.nan),
        "binding"
        if (st.get("cost_share_of_median_stop") or 0) > cfg.costs.max_cost_share_of_stop
        else "not binding",
    )
    add(
        "cooldowns",
        "valid triggers refused by the cooldown after close, per day",
        op["valid_triggers_refused_cooldown_after_close"] / fq["calendar_days"],
        "moderate"
        if op["valid_triggers_refused_cooldown_after_close"] / fq["calendar_days"] > 0.3
        else "not binding",
    )
    ends = {r["end_reason"]: r["len"] for r in c["episode_end_reasons"]}
    n_eps = sum(ends.values()) or 1
    anti = (ends.get("MAX_AGE", 0) + ends.get("ENTRY_WINDOW", 0)) / n_eps
    add(
        "anti-chase / expiry",
        "share of episodes that expired without an executable trigger (MAX_AGE + ENTRY_WINDOW)",
        anti,
        "binding" if anti > 0.4 else "moderate" if anti > 0.2 else "not binding",
    )
    inval = (
        sum(
            v
            for k, v in ends.items()
            if k
            in (
                "FAILED_BREAK",
                "RETRACE_TOO_DEEP",
                "PAUSE_TOO_LONG",
                "BACK_TO_ORIGIN",
                "BREAKOUT_SUCCEEDED",
                "NO_RANGE",
            )
        )
        / n_eps
    )
    add(
        "thesis invalidation",
        "share of episodes invalidated by their own rule before a trigger",
        inval,
        "moderate" if inval > 0.3 else "not binding",
    )
    add(
        "regime restrictions",
        "share of events requiring 1h trend alignment (A) — only family A restricts by regime",
        math.nan,
        "not binding (no regime gate besides family A's 'not opposed' filter)",
    )
    freq_ok = tpd >= need
    edge_ok = (st.get("net_r_mean") or -1) > 0 and (st.get("profit_factor") or 0) > 1
    summary = (
        "frequency met and edge positive"
        if freq_ok and edge_ok
        else "frequency met, NO net edge after costs"
        if freq_ok
        else "frequency NOT met, edge positive"
        if edge_ok
        else "frequency NOT met and NO net edge after costs"
    )
    return {
        "trades_per_calendar_day": tpd,
        "required": need,
        "net_r_mean": st.get("net_r_mean", math.nan),
        "profit_factor": st.get("profit_factor", math.nan),
        "summary": summary,
        "items": items,
        "independent_mode_trades_per_day": op["independent_mode_trades_per_day"],
        "independent_mode_net_r_mean": op["independent_mode_net_r_mean"],
        "sequential_mode_trades_per_day": op["sequential_mode_trades_per_day"],
        "sequential_mode_net_r_mean": op["sequential_mode_net_r_mean"],
    }


# ----------------------------------------------------------------------------- orchestration
def _strip(d: Any) -> Any:
    if isinstance(d, dict):
        return {k: _strip(v) for k, v in d.items() if not k.startswith("_")}
    if isinstance(d, list):
        return [_strip(v) for v in d]
    if isinstance(d, float) and (math.isnan(d) or math.isinf(d)):
        return None
    return d


def run_v6_research(cfg: V6Config, data_dir: Path, out_dir: Path, oos: bool = False) -> V6Research:
    ws = windows(cfg)
    dev_w, val_w, oos_w = ws["development"], ws["validation"], ws["oos"]
    out_dir.mkdir(parents=True, exist_ok=True)
    if oos and (out_dir / OOS_LOCK).exists():
        raise RuntimeError(
            f"the once-only OOS window was already run ({out_dir / OOS_LOCK}); a second run is refused"
        )
    end = oos_w.end_ms if oos else val_w.end_ms
    log.info("loading inputs %s -> %s", iso(dev_w.start_ms), iso(end))
    inp = load_v6_inputs(cfg, data_dir, dev_w.start_ms, end)
    log.info("feature frame")
    fr = build_frame(inp, cfg)

    def fr_builder(c2: V6Config) -> V6Frame:
        # thresholds live in the detectors, not in the frame: the frame is shared
        return fr

    dev, frozen = run_development(cfg, fr, inp, dev_w, fr_builder)
    log.info("validation with the frozen specification")
    val = run_frozen_window(cfg, fr, inp, val_w, frozen, dev)
    bott = bottleneck_analysis(val, cfg)
    oos_res = None
    if oos:
        log.info("ONCE-ONLY OOS run")
        oos_res = run_frozen_window(cfg, fr, inp, oos_w, frozen, dev)
        (out_dir / OOS_LOCK).write_text(
            json.dumps(
                {
                    "ran_at": datetime.now(UTC).isoformat(),
                    "config_hash": cfg.config_hash,
                    "code_version": code_version(),
                    "frozen": frozen,
                    "window": {"start": iso(oos_w.start_ms), "end_exclusive": iso(oos_w.end_ms)},
                },
                indent=1,
                sort_keys=True,
            )
        )
    manifest = {
        "strategy_name": cfg.strategy_name,
        "config_hash": cfg.config_hash,
        "code_version": code_version(),
        "data_hashes": inp.hashes,
        "frame_meta": fr.meta,
        "windows": {
            k: {"start": iso(w.start_ms), "end_exclusive": iso(w.end_ms), "days": w.days}
            for k, w in ws.items()
        },
        "oos_run": bool(oos),
        "ran_at": datetime.now(UTC).isoformat(),
        "frozen": frozen,
    }
    manifest["research_hash"] = stable_hash(
        {
            "dev": _strip(dev["combined_frozen"]["manifest"]),
            "val": _strip(val["combined"]["manifest"]),
            "frozen": frozen,
        }
    )
    res = V6Research(manifest, dev, val, frozen, bott, oos_res)
    _persist(res, out_dir)
    return res


def _persist(res: V6Research, out_dir: Path) -> None:
    (out_dir / "manifest.json").write_text(
        json.dumps(_strip(res.manifest), indent=1, sort_keys=True)
    )
    summary = {
        "development": _strip(res.development),
        "validation": _strip(res.validation),
        "frozen": res.frozen,
        "bottleneck": _strip(res.bottleneck),
        "oos": _strip(res.oos) if res.oos else None,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True, default=str)
    )
    for name, df in (
        ("dev_trades_combined_frozen", res.development.get("_trades_combined_frozen")),
        ("dev_refused_combined_frozen", res.development.get("_refused_combined_frozen")),
        ("dev_episodes_combined_frozen", res.development.get("_episodes_combined_frozen")),
        ("val_trades_combined", res.validation["combined"].get("_trades")),
        ("val_refused_combined", res.validation["combined"].get("_refused")),
        ("val_episodes_combined", res.validation["combined"].get("_episodes")),
    ):
        if isinstance(df, pl.DataFrame) and df.height:
            df.write_parquet(out_dir / f"{name}.parquet")
    for fam, blk in res.validation["families"].items():
        t = blk.get("_trades")
        if isinstance(t, pl.DataFrame) and t.height:
            t.write_parquet(out_dir / f"val_trades_{fam}.parquet")
    if res.oos:
        t = res.oos["combined"].get("_trades")
        if isinstance(t, pl.DataFrame) and t.height:
            t.write_parquet(out_dir / "oos_trades_combined.parquet")
