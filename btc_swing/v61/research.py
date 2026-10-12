"""V6.1 research orchestrator (docs/BTC_V6_1_STRUCTURE_FLOW_DESIGN.md section 14), executed in the
declared order; nothing is revisited after a result is read. STOPS before the locked holdout."""

from __future__ import annotations

import itertools
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import polars as pl

from btc_swing.core.hashing import stable_hash
from btc_swing.core.versions import code_version
from btc_swing.v6.config import V6Config, load_v6_config
from btc_swing.v6.data import iso, load_v6_inputs, ms
from btc_swing.v6.evaluation import (
    _f,
    fold_table,
    frequency_report,
    group_table,
    regime_rows,
    trade_stats,
    with_calendar,
)
from btc_swing.v6.features import V6Frame
from btc_swing.v61.config import ALL_FAMILIES, EntryMode, V61Config, V61Family
from btc_swing.v61.engine import V61Engine, V61Result
from btc_swing.v61.features import V61Inputs, build_frame_v61, normalise_binance, normalise_bybit
from btc_swing.v61.ingest import dataset_hash, load_bybit_5m

log = logging.getLogger(__name__)
DAY_MS = 86_400_000


def _c6(cfg: V61Config) -> V6Config:
    """The V6 evaluation helpers only read risk/costs/bootstrap fields that V6.1 shares."""
    return cast(V6Config, cfg)


@dataclass
class Window:
    name: str
    start_ms: int
    end_ms: int

    @property
    def days(self) -> float:
        return (self.end_ms - self.start_ms) / DAY_MS


@dataclass
class V61Research:
    manifest: dict[str, Any]
    development: dict[str, Any]
    validation: dict[str, Any]
    frozen: dict[str, Any]
    final: dict[str, Any]
    verdict: dict[str, Any]
    frames: dict[str, pl.DataFrame] = field(default_factory=dict)


def windows(cfg: V61Config) -> dict[str, Window]:
    r = cfg.research
    return {
        "development": Window(
            "development", ms(r.development.start), ms(r.development.end_exclusive)
        ),
        "validation": Window("validation", ms(r.validation.start), ms(r.validation.end_exclusive)),
        "holdout": Window(
            "locked_holdout", ms(r.locked_holdout.start), ms(r.locked_holdout.end_exclusive)
        ),
    }


# ----------------------------------------------------------------------------- data
def load_frames(
    cfg: V61Config, data_dir: Path, start_ms: int, end_ms: int
) -> tuple[dict[str, V6Frame], dict[str, V61Inputs], dict[str, Any]]:
    """Bybit-native (primary) and Binance (robustness) frames over the same span; the locked
    holdout is never read (`end_ms` <= validation end)."""
    v6cfg = load_v6_config("config/btc_swing_v6.yaml")
    aux = load_v6_inputs(
        v6cfg, data_dir, start_ms, end_ms
    )  # Binance klines/flow/metrics/funding/premium
    lo = start_ms - cfg.research.warmup_days * DAY_MS
    byb = load_bybit_5m(data_dir)
    byb = (
        byb.filter((pl.col("open_time_ms") >= lo) & (pl.col("open_time_ms") < end_ms))
        if not byb.is_empty()
        else byb
    )
    inputs = {
        "bybit": V61Inputs(
            normalise_bybit(byb),
            aux.metrics,
            aux.funding,
            aux.premium,
            "bybit",
            {**aux.hashes, "bybit_trades_5m": dataset_hash(data_dir)},
        ),
        "binance": V61Inputs(
            normalise_binance(aux.bars, aux.flow),
            aux.metrics,
            aux.funding,
            aux.premium,
            "binance",
            dict(aux.hashes),
        ),
    }
    frames = {k: build_frame_v61(v, cfg) for k, v in inputs.items()}
    avail = _availability(frames, inputs, start_ms, end_ms)
    return frames, inputs, avail


def _availability(
    frames: dict[str, V6Frame], inputs: dict[str, V61Inputs], start_ms: int, end_ms: int
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, fr in frames.items():
        k0, k1 = max(0, fr.idx_at(start_ms) + 1), fr.idx_at(end_ms - 1)
        v = fr.valid[k0 : k1 + 1]
        oi = fr.cols["oi"][k0 : k1 + 1]
        missing_days = sorted({int(d) for d in fr.cols["day"][k0 : k1 + 1][~v]})
        out[name] = {
            "rows_in_window": int(k1 - k0 + 1),
            "valid_share": float(v.mean()) if len(v) else math.nan,
            "invalid_rows": int((~v).sum()),
            "days_with_invalid_rows": len(missing_days),
            "first_missing_days": [iso(d * DAY_MS) for d in missing_days[:10]],
            "oi_share": float((~np.isnan(oi)).mean()) if len(oi) else math.nan,
            "first_row": iso(fr.open_ms[0]),
            "last_close": iso(fr.close_ms[-1]),
            "meta": fr.meta,
        }
    out["venue_mismatch"] = (
        "price/volume/aggressor flow are Bybit-native in the primary frame; open interest, funding and premium are "
        "Binance USDT-M series in BOTH frames (Bybit history not reachable); the Binance frame uses Binance klines + aggTrades flow"
    )
    return out


# ----------------------------------------------------------------------------- helpers
def _run(cfg: V61Config, fr: V6Frame, inp: V61Inputs, w: Window, **kw: Any) -> V61Result:
    return V61Engine(cfg, fr, inp.funding, data_hashes=inp.hashes, **kw).run(w.start_ms, w.end_ms)


def frequency61(t: pl.DataFrame, fr: V6Frame, w: Window, cfg: V61Config) -> dict[str, Any]:
    """V6 calendar-day report plus the V6.1 buckets (0 / 1 / 2 / 3-4 / 5-6 / >6)."""
    from btc_swing.v6.config import load_v6_config as _l6

    fq = frequency_report(t, fr, w.start_ms, w.end_ms, _l6("config/btc_swing_v6.yaml"))
    d0, d1 = w.start_ms // DAY_MS, (w.end_ms - 1) // DAY_MS
    per_day = np.zeros(d1 - d0 + 1, dtype=np.int64)
    if not t.is_empty():
        ed = (t["entry_ms"].to_numpy() // DAY_MS).astype(np.int64)
        ok = (ed >= d0) & (ed <= d1)
        np.add.at(per_day, ed[ok] - d0, 1)
    fq["pct_days_v61"] = {
        "0": float(np.mean(per_day == 0)),
        "1": float(np.mean(per_day == 1)),
        "2": float(np.mean(per_day == 2)),
        "3-4": float(np.mean((per_day >= 3) & (per_day <= 4))),
        "5-6": float(np.mean((per_day >= 5) & (per_day <= 6))),
        ">6": float(np.mean(per_day > 6)),
    }
    g = cfg.research.gate
    fq["hard_minimum_trades_per_day"] = g.min_trades_per_calendar_day
    fq["meets_hard_minimum"] = bool(per_day.mean() >= g.min_trades_per_calendar_day)
    fq["in_preferred_band"] = bool(
        g.preferred_trades_per_day[0] <= per_day.mean() <= g.preferred_trades_per_day[1]
    )
    return fq


def hold_distribution(t: pl.DataFrame) -> dict[str, Any]:
    if t.is_empty():
        return {"n": 0}
    h = t["hold_min"].to_numpy()
    edges = [0, 30, 60, 120, 240, 360, 480, 1e9]
    labels = ["<30m", "30-60m", "1-2h", "2-4h", "4-6h", "6-8h", ">8h"]
    return {
        "n": len(h),
        "median_min": float(np.median(h)),
        "mean_min": float(h.mean()),
        "p10_min": float(np.percentile(h, 10)),
        "p90_min": float(np.percentile(h, 90)),
        "buckets": {
            lab: float(np.mean((h >= a) & (h < b)))
            for lab, a, b in zip(labels, edges[:-1], edges[1:], strict=False)
        },
        "share_time_stop": _f((t["exit_reason"] == "TIME_STOP").mean()),
        "share_time_limit": _f((t["exit_reason"] == "TIME_LIMIT").mean()),
    }


def stop_buckets(t: pl.DataFrame, cfg: V61Config) -> list[dict[str, Any]]:
    if t.is_empty():
        return []
    edges = [0.0, *cfg.stops.bucket_edges_pct, 1e9]
    out = []
    for a, b in itertools.pairwise(edges):
        sub = t.filter((pl.col("stop_pct") >= a) & (pl.col("stop_pct") < b))
        if sub.is_empty():
            out.append({"bucket": f"{a:g}-{b:g}%" if b < 1e8 else f">{a:g}%", "n": 0})
            continue
        r = sub["net_r"].to_numpy()
        w_, l_ = r[r > 0], r[r < 0]
        out.append(
            {
                "bucket": f"{a:g}-{b:g}%" if b < 1e8 else f">{a:g}%",
                "n": sub.height,
                "net_r_mean": float(r.mean()),
                "profit_factor": float(w_.sum() / -l_.sum())
                if len(l_) and l_.sum() < 0
                else math.inf,
                "median_stop_bps": _f(sub["stop_bps"].median()),
                "cost_share_stop_median": _f(sub["cost_share_stop"].median()),
                "fee_slip_r_mean": -_f((sub["fee_r"] + sub["slip_r"]).mean()),
                "median_hold_min": _f(sub["hold_min"].median()),
            }
        )
    return out


def cost_distribution(res: V61Result) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, df in (("all_triggers", res.triggers), ("executed_trades", res.trades)):
        if df.is_empty():
            out[name] = {"n": 0}
            continue
        q = [5, 25, 50, 75, 95]
        d: dict[str, Any] = {"n": df.height}
        if "cost_bps" not in df.columns and {"cost_share_stop", "stop_bps"} <= set(df.columns):
            df = df.with_columns((pl.col("cost_share_stop") * pl.col("stop_bps")).alias("cost_bps"))
        for col in ("cost_bps", "stop_bps", "target_bps", "cost_share_stop", "cost_share_target"):
            if col in df.columns:
                x = df[col].to_numpy().astype(np.float64)
                x = x[np.isfinite(x)]
                d[col] = {f"p{p}": float(np.percentile(x, p)) for p in q} if len(x) else {}
            elif col == "cost_bps":
                d[col] = {"p50": float(res.manifest.get("round_trip_cost_bps", math.nan))}
        out[name] = d
    return out


def _summary(res: V61Result, fr: V6Frame, w: Window, cfg: V61Config) -> dict[str, Any]:
    t = res.trades
    tc = with_calendar(t)
    out: dict[str, Any] = {
        "manifest": res.manifest,
        "stats": trade_stats(t, w.start_ms, w.end_ms, _c6(cfg)),
        "frequency": frequency61(t, fr, w, cfg),
        "hold": hold_distribution(t),
        "stop_buckets": stop_buckets(t, cfg),
        "costs": cost_distribution(res),
        "events_per_detector": res.events_per_detector,
        "refused": res.refused.group_by("reason").len().sort("len", descending=True).to_dicts()
        if res.refused.height
        else [],
        "episode_end_reasons": res.episodes.group_by("end_reason")
        .len()
        .sort("len", descending=True)
        .to_dicts()
        if res.episodes.height
        else [],
        "flow_rejections": int(res.episodes["flow_rejects"].sum())
        if res.episodes.height and "flow_rejects" in res.episodes.columns
        else 0,
        "by_family": group_table(tc, ["family"], _c6(cfg), w.start_ms, w.end_ms),
        "by_family_side": group_table(tc, ["family", "side"], _c6(cfg), w.start_ms, w.end_ms),
        "by_side": group_table(tc, ["side"], _c6(cfg), w.start_ms, w.end_ms),
        "by_year": group_table(tc, ["year"], _c6(cfg), w.start_ms, w.end_ms),
        "by_regime": regime_rows(t, _c6(cfg), w.start_ms, w.end_ms),
        "by_regime_1h": group_table(tc, ["regime_1h"], _c6(cfg), w.start_ms, w.end_ms)
        if not t.is_empty()
        else [],
        "by_session": group_table(tc, ["session"], _c6(cfg), w.start_ms, w.end_ms),
        "by_exit_reason": group_table(tc, ["exit_reason"], _c6(cfg), w.start_ms, w.end_ms),
        "one_position": _one_position(res, w),
    }
    return out


def _one_position(res: V61Result, w: Window) -> dict[str, Any]:
    ref = res.refused
    n_pos = int((ref["reason"] == "BLOCKED_POSITION_OPEN").sum()) if ref.height else 0
    n_cool = int((ref["reason"] == "BLOCKED_COOLDOWN_AFTER_CLOSE").sum()) if ref.height else 0
    n_cost = int((ref["reason"] == "COST_SHARE_TOO_HIGH").sum()) if ref.height else 0
    cf = (
        ref.filter(
            (pl.col("reason") == "BLOCKED_POSITION_OPEN")
            & pl.col("counterfactual_net_r").is_not_nan()
        )
        if ref.height
        else ref
    )
    return {
        "trades": res.trades.height,
        "refused_position_open": n_pos,
        "refused_cooldown": n_cool,
        "refused_cost_share": n_cost,
        "suppressed_per_day": (n_pos + n_cool) / w.days,
        "suppression_share": (n_pos + n_cool) / max(res.trades.height + n_pos + n_cool, 1),
        "counterfactual_mean_net_r": _f(cf["counterfactual_net_r"].mean())
        if cf.height
        else math.nan,
        "counterfactual_median_hold_min": _f(cf["counterfactual_hold_min"].median())
        if cf.height
        else math.nan,
    }


# ----------------------------------------------------------------------------- development
def family_pairs(
    cfg: V61Config, fr: V6Frame, inp: V61Inputs, w: Window, **kw: Any
) -> dict[str, Any]:
    """Every family alone, STRUCTURE ONLY vs STRUCTURE + FLOW."""
    out: dict[str, Any] = {}
    for fam in ALL_FAMILIES:
        pair: dict[str, Any] = {}
        for label, flow in (("structure_only", False), ("structure_flow", True)):
            res = _run(cfg, fr, inp, w, families=[fam], flow=flow, **kw)
            s = _summary(res, fr, w, cfg)
            s["folds"] = fold_table(
                res.trades,
                w.start_ms,
                w.end_ms,
                cfg.research.dev_folds if w.name == "development" else cfg.research.val_folds,
                _c6(cfg),
            )
            s["_trades"] = res.trades
            pair[label] = s
        so, sf = pair["structure_only"]["stats"], pair["structure_flow"]["stats"]
        pair["flow_improves_net_r"] = (
            bool((sf.get("net_r_mean") or -9) > (so.get("net_r_mean") or -9))
            if sf.get("n", 0) and so.get("n", 0)
            else False
        )
        pair["flow_delta_net_r"] = (sf.get("net_r_mean") or math.nan) - (
            so.get("net_r_mean") or math.nan
        )
        pair["flow_trades_ratio"] = (
            (sf.get("n", 0) / so.get("n", 1)) if so.get("n", 0) else math.nan
        )
        out[fam.value] = pair
    return out


def _exit_rows(
    cfg: V61Config, fr: V6Frame, inp: V61Inputs, w: Window, flow: dict[V61Family, bool]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = []
    for vname in cfg.exits.variants:
        for ts in cfg.exits.time_stop_hours_grid:
            res = _run(cfg, fr, inp, w, flow=flow, variant=vname, time_stop_hours=ts)
            st = trade_stats(res.trades, w.start_ms, w.end_ms, _c6(cfg))
            fq = frequency61(res.trades, fr, w, cfg)
            folds = fold_table(res.trades, w.start_ms, w.end_ms, cfg.research.dev_folds, _c6(cfg))
            rows.append(
                {
                    "variant": vname,
                    "time_stop_h": ts,
                    "description": cfg.exits.variants[vname].description,
                    "n": st["n"],
                    "trades_per_calendar_day": fq["mean_trades_per_calendar_day"],
                    "net_r_mean": st.get("net_r_mean", math.nan),
                    "net_r_per_day": st.get("net_r_sum", 0.0) / w.days,
                    "profit_factor": st.get("profit_factor", math.nan),
                    "win_rate": st.get("win_rate", math.nan),
                    "median_hold_min": st.get("median_hold_min", math.nan),
                    "max_dd_frac": st.get("max_dd_frac", math.nan),
                    "positive_folds": sum(1 for f in folds if f["n"] > 0 and f["net_r_sum"] > 0),
                    "share_time_stop": _f((res.trades["exit_reason"] == "TIME_STOP").mean())
                    if res.trades.height
                    else math.nan,
                }
            )
    g = cfg.research.gate
    eligible = [
        r
        for r in rows
        if r["trades_per_calendar_day"] >= g.min_trades_per_calendar_day
        and r["positive_folds"] >= g.exit_selection_min_positive_folds
    ]
    pool = eligible or rows
    best = max(pool, key=lambda r: (r["net_r_per_day"], r["trades_per_calendar_day"]))
    chosen = {
        "variant": best["variant"],
        "time_stop_h": best["time_stop_h"],
        "rule": f"declared: among (variant, time stop) with >= {g.min_trades_per_calendar_day} trades/calendar day AND net R positive in >= {g.exit_selection_min_positive_folds} of {cfg.research.dev_folds} development folds, the highest net R per day; else the highest net R per day of all (flagged)",
        "frequency_and_fold_requirement_met_on_development": bool(eligible),
    }
    return rows, chosen


def _entry_rows(
    cfg: V61Config,
    fr: V6Frame,
    inp: V61Inputs,
    w: Window,
    flow: dict[V61Family, bool],
    variant: str,
    ts: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = []
    modes: list[EntryMode] = ["TAKER", "MAKER", "MAKER_FIRST"]
    for mode in modes:
        res = _run(cfg, fr, inp, w, flow=flow, variant=variant, time_stop_hours=ts, entry_mode=mode)
        st = trade_stats(res.trades, w.start_ms, w.end_ms, _c6(cfg))
        ref = res.refused
        rows.append(
            {
                "entry_mode": mode,
                "n": st["n"],
                "trades_per_day": st["trades_per_day"],
                "maker_fills_share": _f(res.trades["entry_maker"].mean())
                if res.trades.height
                else math.nan,
                "unfilled": int((ref["reason"] == "MAKER_UNFILLED").sum()) if ref.height else 0,
                "setup_gone_after_timeout": int((ref["reason"] == "MAKER_FIRST_SETUP_GONE").sum())
                if ref.height
                else 0,
                "net_r_mean": st.get("net_r_mean", math.nan),
                "fee_r_mean": st.get("fee_r_mean", math.nan),
                "slip_r_mean": st.get("slip_r_mean", math.nan),
                "gross_r_mean": st.get("gross_r_mean", math.nan),
                "profit_factor": st.get("profit_factor", math.nan),
                "cost_share_stop_median": _f(res.trades["cost_share_stop"].median())
                if res.trades.height
                else math.nan,
            }
        )
    taker = rows[0]
    mf = rows[2]
    adopt = bool(
        mf["n"]
        and taker["n"]
        and mf["net_r_mean"] > taker["net_r_mean"]
        and mf["trades_per_day"] >= 0.9 * taker["trades_per_day"]
    )
    decision = {
        "entry_mode": "MAKER_FIRST" if adopt else "TAKER",
        "rule": "declared: MAKER_FIRST replaces TAKER only if it improves net expectancy on development without reducing trades/day by more than 10% (confirmed on validation before adoption)",
        "development_supports_maker_first": adopt,
    }
    return rows, decision


def _grid_rows(
    cfg: V61Config,
    fr: V6Frame,
    inp: V61Inputs,
    w: Window,
    flow: dict[V61Family, bool],
    variant: str,
    ts: float,
    mode: EntryMode,
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {
        "cost_share": [],
        "stop_floor": [],
        "family_thresholds": [],
    }
    for theta in cfg.costs.cost_share_grid:
        res = _run(
            cfg,
            fr,
            inp,
            w,
            flow=flow,
            variant=variant,
            time_stop_hours=ts,
            entry_mode=mode,
            cost_share_max=theta,
        )
        st = trade_stats(res.trades, w.start_ms, w.end_ms, _c6(cfg))
        out["cost_share"].append(
            {
                "theta": theta,
                "primary": abs(theta - cfg.costs.cost_share_stop_max) < 1e-12,
                "n": st["n"],
                "trades_per_day": st["trades_per_day"],
                "net_r_mean": st.get("net_r_mean", math.nan),
                "net_r_sum": st.get("net_r_sum", 0.0),
                "profit_factor": st.get("profit_factor", math.nan),
                "median_stop_bps": st.get("median_stop_bps", math.nan),
                "cost_share_of_gross": st.get("cost_share_of_gross", math.nan),
                "refused_cost_share": int((res.refused["reason"] == "COST_SHARE_TOO_HIGH").sum())
                if res.refused.height
                else 0,
            }
        )
    for floor in cfg.stops.floor_pct_grid:
        res = _run(
            cfg,
            fr,
            inp,
            w,
            flow=flow,
            variant=variant,
            time_stop_hours=ts,
            entry_mode=mode,
            stop_floor_pct=floor,
        )
        st = trade_stats(res.trades, w.start_ms, w.end_ms, _c6(cfg))
        out["stop_floor"].append(
            {
                "min_stop_pct": floor,
                "primary": floor == 0.0,
                "n": st["n"],
                "trades_per_day": st["trades_per_day"],
                "net_r_mean": st.get("net_r_mean", math.nan),
                "profit_factor": st.get("profit_factor", math.nan),
                "median_stop_bps": st.get("median_stop_bps", math.nan),
                "cost_share_of_median_stop": st.get("cost_share_of_median_stop", math.nan),
                "median_hold_min": st.get("median_hold_min", math.nan),
                "max_dd_frac": st.get("max_dd_frac", math.nan),
            }
        )
    fam = cfg.families
    grids: list[tuple[V61Family, str, str, list[float]]] = [
        (
            V61Family.TREND_PULLBACK_RECLAIM,
            "trend_pullback_reclaim",
            "impulse_min_atr15",
            fam.trend_pullback_reclaim.impulse_grid,
        ),
        (
            V61Family.BREAKOUT_RETEST_CONTINUATION,
            "breakout_retest_continuation",
            "flow_rel_vol",
            fam.breakout_retest_continuation.rel_vol_grid,
        ),
        (
            V61Family.LIQUIDITY_SWEEP_REVERSAL,
            "liquidity_sweep_reversal",
            "sweep_min_atr15",
            fam.liquidity_sweep_reversal.sweep_grid,
        ),
        (
            V61Family.RANGE_EDGE_MEAN_REVERSION,
            "range_edge_mean_reversion",
            "edge_zone",
            fam.range_edge_mean_reversion.edge_zone_grid,
        ),
        (
            V61Family.IMPULSE_CONSOLIDATION_BREAK,
            "impulse_consolidation_break",
            "impulse_min_atr15",
            fam.impulse_consolidation_break.impulse_grid,
        ),
    ]
    for family, section, key, values in grids:
        for v in values:
            c2 = cfg.model_copy(
                update={
                    "families": fam.model_copy(
                        update={section: getattr(fam, section).model_copy(update={key: v})}
                    )
                }
            )
            res = V61Engine(
                c2,
                fr,
                inp.funding,
                families=[family],
                flow=flow.get(family, True),
                variant=variant,
                time_stop_hours=ts,
                entry_mode=mode,
            ).run(w.start_ms, w.end_ms)
            st = trade_stats(res.trades, w.start_ms, w.end_ms, _c6(cfg))
            out["family_thresholds"].append(
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
    return out


def _risk_grid(t: pl.DataFrame, cfg: V61Config) -> list[dict[str, Any]]:
    if t.is_empty():
        return []
    r = t["net_r"].to_numpy().astype(np.float64)
    eq_r = np.concatenate([[0.0], np.cumsum(r)])
    dd_r = float(np.max(np.maximum.accumulate(eq_r) - eq_r))
    d = (
        t.with_columns((pl.col("entry_ms") // DAY_MS).alias("d"))
        .group_by("d")
        .agg(pl.col("net_r").sum())
    )
    worst = _f(d["net_r"].min())
    return [
        {
            "risk_per_trade": rk,
            "primary": abs(rk - cfg.risk.risk_per_trade) < 1e-12,
            "net_return_frac": float(r.sum()) * rk,
            "max_dd_frac": dd_r * rk,
            "max_dd_r": dd_r,
            "worst_day_frac": worst * rk,
        }
        for rk in cfg.risk.risk_grid
    ]


def run_development(
    cfg: V61Config, fr: V6Frame, inp: V61Inputs, w: Window
) -> tuple[dict[str, Any], dict[str, Any]]:
    dev: dict[str, Any] = {
        "window": {"start": iso(w.start_ms), "end_exclusive": iso(w.end_ms), "days": w.days}
    }
    log.info("development: families, structure-only vs structure+flow")
    dev["families"] = family_pairs(cfg, fr, inp, w)
    flow_dev = {
        fam: bool(dev["families"][fam.value]["flow_improves_net_r"]) for fam in ALL_FAMILIES
    }
    dev["flow_choice_development"] = {k.value: v for k, v in flow_dev.items()}
    log.info("development: exits x time stops")
    dev["exit_rows"], exit_choice = _exit_rows(cfg, fr, inp, w, flow_dev)
    v, ts = exit_choice["variant"], exit_choice["time_stop_h"]
    log.info("development: entry modes")
    dev["entry_rows"], entry_choice = _entry_rows(cfg, fr, inp, w, flow_dev, v, ts)
    mode: EntryMode = entry_choice["entry_mode"]
    log.info("development: grids")
    dev["grids"] = _grid_rows(cfg, fr, inp, w, flow_dev, v, ts, mode)
    comb = _run(cfg, fr, inp, w, flow=flow_dev, variant=v, time_stop_hours=ts, entry_mode=mode)
    dev["combined"] = _summary(comb, fr, w, cfg)
    dev["combined"]["folds"] = fold_table(
        comb.trades, w.start_ms, w.end_ms, cfg.research.dev_folds, _c6(cfg)
    )
    dev["combined"]["risk_grid"] = _risk_grid(comb.trades, cfg)
    for m in ("sequential", "independent"):
        r = _run(
            cfg, fr, inp, w, flow=flow_dev, variant=v, time_stop_hours=ts, entry_mode=mode, mode=m
        )
        dev["combined"][f"mode_{m}"] = {
            "trades_per_day": frequency61(r.trades, fr, w, cfg)["mean_trades_per_calendar_day"],
            "net_r_mean": trade_stats(r.trades, w.start_ms, w.end_ms, _c6(cfg)).get(
                "net_r_mean", math.nan
            ),
        }
    ov = _run(
        cfg, fr, inp, w, flow=flow_dev, variant=v, time_stop_hours=ts, entry_mode=mode, overlay=True
    )
    dev["combined"]["with_overlay"] = trade_stats(ov.trades, w.start_ms, w.end_ms, _c6(cfg))
    if comb.trades.height:
        dev["combined"]["null"] = _null(
            cfg, fr, inp, comb, w, flow_dev, v, ts, mode, cfg.research.seed
        )
    dev["_trades_combined"] = comb.trades
    dev["_refused_combined"] = comb.refused
    dev["_triggers_combined"] = comb.triggers
    frozen = {
        "flow_development": {k.value: v_ for k, v_ in flow_dev.items()},
        "exit_variant": v,
        "time_stop_h": ts,
        "exit_selection": exit_choice,
        "entry_mode": mode,
        "entry_selection": entry_choice,
        "cost_share_stop_max": cfg.costs.cost_share_stop_max,
        "stop_floor_pct": 0.0,
        "risk_per_trade": cfg.risk.risk_per_trade,
        "families": [f.value for f in cfg.enabled_families()],
    }
    return dev, frozen


def _null(
    cfg: V61Config,
    fr: V6Frame,
    inp: V61Inputs,
    res: V61Result,
    w: Window,
    flow: dict[V61Family, bool],
    v: str,
    ts: float,
    mode: EntryMode,
    seed: int,
) -> dict[str, Any]:
    """Regime- and hour-matched null: for every real trade, `null_k` random bars with the same
    regime code and UTC hour inside the window, same side, same stop distance in ATR15, same exits
    and costs (taker), simulated on their own."""
    from btc_swing.core.enums import Side
    from btc_swing.v61.families import Episode as Ep61
    from btc_swing.v61.families import Trigger61

    t = res.trades
    if t.is_empty():
        return {"n": 0}
    eng = V61Engine(
        cfg, fr, inp.funding, flow=flow, variant=v, time_stop_hours=ts, entry_mode="TAKER"
    )
    rng = np.random.RandomState(seed)
    i0, i1 = fr.idx_at(w.start_ms) + 1, fr.idx_at(w.end_ms - 1)
    codes = fr.regime_code[i0:i1]
    hours = fr.cols["utc_hour"][i0:i1]
    atr15 = fr.cols["atr15m"]
    close = fr.cols["close"]
    pools: dict[tuple[int, int], np.ndarray] = {}
    sims: list[float] = []
    for row in t.iter_rows(named=True):
        key = (int(row["regime_code"]), int((row["entry_ms"] % DAY_MS) // 3_600_000))
        if key not in pools:
            pools[key] = np.flatnonzero((codes == key[0]) & (hours == key[1])) + i0
        pool = pools[key]
        pool = pool[(pool > i0) & (pool < i1 - 2)]
        if len(pool) == 0:
            continue
        side = Side(row["side"])
        s_ = side.sign
        fam = V61Family(row["family"])
        for j in rng.choice(pool, size=cfg.research.null_k, replace=True):
            a = float(atr15[j])
            ref = float(close[j])
            if math.isnan(a) or math.isnan(ref):
                continue
            stop = ref - s_ * float(row["stop_atr15"]) * a
            target = (
                ref + s_ * float(row["target_bps"]) / 1e4 * ref
                if not math.isnan(float(row["target_bps"]))
                else math.nan
            )
            ep = Ep61(-1, fam, side, int(j), int(fr.close_ms[j]), 0.0, {})
            pos = eng._simulate(
                ep, Trigger61(ref, stop, target, "NULL", True), int(j), -1, cfg.risk.initial_equity
            )
            if pos is not None:
                sims.append(pos.net_pnl() / max(pos.sizing.risk_amount, 1e-9))
    if not sims:
        return {"n": 0}
    arr = np.asarray(sims)
    real = _f(t["net_r"].mean())
    sd = arr.std() / math.sqrt(max(t.height, 1))
    return {
        "n_real": t.height,
        "n_null": len(arr),
        "real_mean_net_r": real,
        "null_mean_net_r": float(arr.mean()),
        "null_sd_of_mean": float(sd),
        "edge_vs_null_r": real - float(arr.mean()),
        "z_vs_null": (real - float(arr.mean())) / sd if sd > 0 else math.nan,
        "null_share_positive": float(np.mean(arr > 0)),
    }


# ----------------------------------------------------------------------------- validation / final
def run_validation(
    cfg: V61Config, fr: V6Frame, inp: V61Inputs, w: Window, frozen: dict[str, Any]
) -> dict[str, Any]:
    v, ts, mode = frozen["exit_variant"], frozen["time_stop_h"], frozen["entry_mode"]
    val: dict[str, Any] = {
        "window": {"start": iso(w.start_ms), "end_exclusive": iso(w.end_ms), "days": w.days}
    }
    log.info("validation: families (both flow settings, frozen exits/entry)")
    val["families"] = family_pairs(cfg, fr, inp, w, variant=v, time_stop_hours=ts, entry_mode=mode)
    flow_dev = {V61Family(k): bool(b) for k, b in frozen["flow_development"].items()}
    comb = _run(cfg, fr, inp, w, flow=flow_dev, variant=v, time_stop_hours=ts, entry_mode=mode)
    val["combined_dev_flow"] = _summary(comb, fr, w, cfg)
    val["combined_dev_flow"]["folds"] = fold_table(
        comb.trades, w.start_ms, w.end_ms, cfg.research.val_folds, _c6(cfg)
    )
    val["entry_rows"], _ = _entry_rows(cfg, fr, inp, w, flow_dev, v, ts)
    val["_trades_combined_dev_flow"] = comb.trades
    return val


def final_specification(
    cfg: V61Config, dev: dict[str, Any], val: dict[str, Any], frozen: dict[str, Any]
) -> dict[str, Any]:
    """Declared rule: flow stays for a family only if it improved net R on BOTH windows; a family is
    kept only if its chosen version has trades on both windows."""
    flow_final: dict[str, bool] = {}
    for fam in ALL_FAMILIES:
        d, v_ = dev["families"][fam.value], val["families"][fam.value]
        flow_final[fam.value] = bool(d["flow_improves_net_r"] and v_["flow_improves_net_r"])
    entry = frozen["entry_mode"]
    if entry == "MAKER_FIRST":
        rows = {r["entry_mode"]: r for r in val["entry_rows"]}
        mf, tk = rows["MAKER_FIRST"], rows["TAKER"]
        if not (
            mf["n"]
            and tk["n"]
            and mf["net_r_mean"] > tk["net_r_mean"]
            and mf["trades_per_day"] >= 0.9 * tk["trades_per_day"]
        ):
            entry = "TAKER"
    return {
        **frozen,
        "flow_final": flow_final,
        "entry_mode_final": entry,
        "rule": "flow kept per family only if it improved net expectancy on development AND validation; maker-first kept only if confirmed on validation",
    }


def run_final(
    cfg: V61Config,
    frames: dict[str, V6Frame],
    inputs: dict[str, V61Inputs],
    ws: dict[str, Window],
    final: dict[str, Any],
) -> dict[str, Any]:
    flow = {V61Family(k): b for k, b in final["flow_final"].items()}
    v, ts, mode = final["exit_variant"], final["time_stop_h"], final["entry_mode_final"]
    out: dict[str, Any] = {}
    for venue in ("bybit", "binance"):
        fr, inp = frames[venue], inputs[venue]
        for wn in ("development", "validation"):
            w = ws[wn]
            res = _run(cfg, fr, inp, w, flow=flow, variant=v, time_stop_hours=ts, entry_mode=mode)
            s = _summary(res, fr, w, cfg)
            s["folds"] = fold_table(
                res.trades,
                w.start_ms,
                w.end_ms,
                cfg.research.dev_folds if wn == "development" else cfg.research.val_folds,
                _c6(cfg),
            )
            s["risk_grid"] = _risk_grid(res.trades, cfg)
            if venue == "bybit" and wn == "validation":
                for m in ("sequential", "independent"):
                    r = _run(
                        cfg,
                        fr,
                        inp,
                        w,
                        flow=flow,
                        variant=v,
                        time_stop_hours=ts,
                        entry_mode=mode,
                        mode=m,
                    )
                    s[f"mode_{m}"] = {
                        "trades_per_day": frequency61(r.trades, fr, w, cfg)[
                            "mean_trades_per_calendar_day"
                        ],
                        "net_r_mean": trade_stats(r.trades, w.start_ms, w.end_ms, _c6(cfg)).get(
                            "net_r_mean", math.nan
                        ),
                    }
                ov = _run(
                    cfg,
                    fr,
                    inp,
                    w,
                    flow=flow,
                    variant=v,
                    time_stop_hours=ts,
                    entry_mode=mode,
                    overlay=True,
                )
                s["with_overlay"] = trade_stats(ov.trades, w.start_ms, w.end_ms, _c6(cfg))
                if res.trades.height:
                    s["null"] = _null(
                        cfg, fr, inp, res, w, flow, v, ts, mode, cfg.research.seed + 7
                    )
            s["_trades"] = res.trades
            s["_refused"] = res.refused
            s["_triggers"] = res.triggers
            out[f"{venue}_{wn}"] = s
    return out


def verdict(cfg: V61Config, final_runs: dict[str, Any], final: dict[str, Any]) -> dict[str, Any]:
    g = cfg.research.gate
    s = final_runs["bybit_validation"]
    st, fq = s["stats"], s["frequency"]
    folds_dev = final_runs["bybit_development"]["folds"]
    folds_val = s["folds"]
    pos_dev = sum(1 for f in folds_dev if f["n"] > 0 and f["net_r_sum"] > 0)
    pos_val = sum(1 for f in folds_val if f["n"] > 0 and f["net_r_sum"] > 0)
    tot = st.get("net_r_sum", 0.0)
    fam_share = (
        max((float(r["net_r_sum"]) for r in s["by_family"]), default=0.0) / tot
        if tot > 0 and s["by_family"]
        else math.nan
    )
    rows = [
        (
            f"FREQUENCY: mean trades per calendar day >= {g.min_trades_per_calendar_day:.1f}",
            fq["mean_trades_per_calendar_day"],
            fq["mean_trades_per_calendar_day"] >= g.min_trades_per_calendar_day,
        ),
        (
            f"NET EDGE: net expectancy after costs > {g.min_net_expectancy_r:.2f}R",
            st.get("net_r_mean", math.nan),
            (st.get("net_r_mean") or -9) > g.min_net_expectancy_r,
        ),
        (
            f"NET EDGE: profit factor after costs > {g.min_profit_factor:.2f}",
            st.get("profit_factor", math.nan),
            (st.get("profit_factor") or 0) > g.min_profit_factor,
        ),
        (
            f"preferred: profit factor > {g.preferred_profit_factor:.2f}",
            st.get("profit_factor", math.nan),
            (st.get("profit_factor") or 0) > g.preferred_profit_factor,
        ),
        (
            f"not one isolated period: net R positive in >= {g.min_positive_dev_folds} of {cfg.research.dev_folds} development folds",
            pos_dev,
            pos_dev >= g.min_positive_dev_folds,
        ),
        (
            f"not one isolated period: net R positive in >= {g.min_positive_val_halves} of {cfg.research.val_folds} validation halves",
            pos_val,
            pos_val >= g.min_positive_val_halves,
        ),
        (
            "bootstrap 95% lower bound of net R > 0",
            st.get("net_r_boot_lo", math.nan),
            (st.get("net_r_boot_lo") or -9) > 0,
        ),
        (
            "drawdown: max DD < %.0f%% of equity at the primary risk" % (100 * g.max_drawdown_frac),
            st.get("max_dd_frac", math.nan),
            (st.get("max_dd_frac") if st.get("max_dd_frac") is not None else 9)
            < g.max_drawdown_frac,
        ),
        (
            "no single family > %.0f%% of net profit" % (100 * g.max_single_family_share),
            fam_share,
            not (fam_share > g.max_single_family_share) if not math.isnan(fam_share) else tot <= 0,
        ),
    ]
    out_rows = [{"criterion": a, "value": b, "passed": bool(p)} for a, b, p in rows]
    freq_pass = out_rows[0]["passed"]
    edge_pass = out_rows[1]["passed"] and out_rows[2]["passed"]
    robust = all(r["passed"] for r in out_rows[4:])
    recommend = freq_pass and edge_pass and robust and out_rows[3]["passed"]
    return {
        "rows": out_rows,
        "frequency_pass": freq_pass,
        "net_edge_pass": edge_pass,
        "robustness_pass": robust,
        "preferred_pf_pass": out_rows[3]["passed"],
        "recommend_spending_holdout": recommend,
        "recommendation": (
            "SPEND the locked holdout once with this exact specification"
            if recommend
            else "DO NOT spend the locked holdout: "
            + (
                "frequency and net edge both fail"
                if not freq_pass and not edge_pass
                else "frequency fails"
                if not freq_pass
                else "net edge fails"
                if not edge_pass
                else "edge is marginal or not robust across folds/families"
            )
        ),
    }


# ----------------------------------------------------------------------------- orchestration
def _strip(d: Any) -> Any:
    if isinstance(d, dict):
        return {k: _strip(v) for k, v in d.items() if not str(k).startswith("_")}
    if isinstance(d, list):
        return [_strip(v) for v in d]
    if isinstance(d, float) and (math.isnan(d) or math.isinf(d)):
        return None
    return d


def run_v61_research(cfg: V61Config, data_dir: Path, out_dir: Path) -> V61Research:
    ws = windows(cfg)
    dev_w, val_w = ws["development"], ws["validation"]
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("loading frames %s -> %s (holdout never read)", iso(dev_w.start_ms), iso(val_w.end_ms))
    frames, inputs, avail = load_frames(cfg, data_dir, dev_w.start_ms, val_w.end_ms)
    fr, inp = frames["bybit"], inputs["bybit"]
    dev, frozen = run_development(cfg, fr, inp, dev_w)
    log.info("validation")
    val = run_validation(cfg, fr, inp, val_w, frozen)
    final = final_specification(cfg, dev, val, frozen)
    log.info("final specification on both venues and windows")
    final_runs = run_final(cfg, frames, inputs, ws, final)
    vd = verdict(cfg, final_runs, final)
    manifest = {
        "strategy_name": cfg.strategy_name,
        "config_hash": cfg.config_hash,
        "code_version": code_version(),
        "data_hashes": inp.hashes,
        "availability": avail,
        "windows": {
            k: {"start": iso(w.start_ms), "end_exclusive": iso(w.end_ms), "days": w.days}
            for k, w in ws.items()
        },
        "holdout_run": False,
        "ran_at": datetime.now(UTC).isoformat(),
        "frozen_after_development": frozen,
        "final_specification": final,
    }
    manifest["research_hash"] = stable_hash(
        {
            "dev": _strip(dev["combined"]["manifest"]),
            "final": _strip(final_runs["bybit_validation"]["manifest"]),
            "spec": _strip(final),
        }
    )
    res = V61Research(manifest, dev, val, frozen, {"runs": final_runs, "spec": final}, vd)
    _persist(res, out_dir)
    return res


def _persist(res: V61Research, out_dir: Path) -> None:
    (out_dir / "manifest.json").write_text(
        json.dumps(_strip(res.manifest), indent=1, sort_keys=True, default=str)
    )
    summary = {
        "development": _strip(res.development),
        "validation": _strip(res.validation),
        "frozen": _strip(res.frozen),
        "final": _strip(res.final),
        "verdict": _strip(res.verdict),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True, default=str)
    )
    tables: list[tuple[str, Any]] = [
        ("dev_trades_combined", res.development.get("_trades_combined")),
        ("dev_refused_combined", res.development.get("_refused_combined")),
        ("dev_triggers_combined", res.development.get("_triggers_combined")),
    ]
    for key, s in res.final["runs"].items():
        tables += [
            (f"final_{key}_trades", s.get("_trades")),
            (f"final_{key}_refused", s.get("_refused")),
            (f"final_{key}_triggers", s.get("_triggers")),
        ]
    for fam, pair in res.validation["families"].items():
        for lab in ("structure_only", "structure_flow"):
            tables.append((f"val_{fam}_{lab}_trades", pair[lab].get("_trades")))
    for name, df in tables:
        if isinstance(df, pl.DataFrame) and df.height:
            df.write_parquet(out_dir / f"{name}.parquet")
