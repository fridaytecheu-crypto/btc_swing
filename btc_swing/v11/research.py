"""V11 historical experiment (design section 8): ONE frozen specification run on TRAIN and VALIDATION,
each with its own warm-up; statistics, breakdowns, the pre-declared x1.5 cost sensitivity, and the
A / B / C classification. No parameter is changed by this module."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from btc_swing.core.versions import code_version
from btc_swing.v7.ingest import dataset_hash_1m
from btc_swing.v11.config import V11_VERSION, V11Config
from btc_swing.v11.data import DAY_MS, load_inputs, ms
from btc_swing.v11.engine import FAMILIES, Trade, V11Engine
from btc_swing.v11.features import build_frame_v11
from btc_swing.v11.scoring import score_frame

log = logging.getLogger(__name__)
WINDOWS = ("train", "validation")


def _clean(v: Any) -> Any:
    if isinstance(v, bool | np.bool_):
        return bool(v)
    if isinstance(v, float | np.floating):
        x = float(v)
        return None if (math.isnan(x) or math.isinf(x)) else x
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_clean(x) for x in v]
    return v


def _pct(xs: list[float], p: float) -> float:
    return float(np.percentile(xs, p)) if xs else math.nan


def trade_stats(
    trades: list[Trade], days: int, eq: float, counters: dict[str, int], blockers: dict[str, int]
) -> dict[str, Any]:
    n = len(trades)
    out: dict[str, Any] = {
        "n": n,
        "days": days,
        "trades_per_day": n / days,
        "counters": counters,
        "blockers": blockers,
    }
    if n == 0:
        return out
    net_r = np.array([t.net_r for t in trades])
    gross_r = np.array([t.gross_r for t in trades])
    cost_r = np.array([t.cost_r for t in trades])
    net_usd = np.array([t.net_usd for t in trades])
    gross_usd = np.array([t.gross_usd for t in trades])
    cost_usd = np.array([t.fee_usd + t.slip_usd - t.funding_usd for t in trades])
    hold = [t.hold_min for t in trades]
    day_idx = np.array([t.pos["entry_ms"] // DAY_MS for t in trades])
    counts = np.bincount(day_idx - day_idx.min())
    per_day = np.zeros(days, dtype=np.int64)
    k = min(days, len(counts))
    per_day[:k] = counts[:k]
    active = int((per_day > 0).sum())
    wins = net_usd[net_usd > 0]
    losses = net_usd[net_usd <= 0]
    curve = np.concatenate([[0.0], np.cumsum(net_usd)])
    dd = np.maximum.accumulate(curve) - curve
    risk = np.median([t.pos["risk_usdt"] for t in trades])
    exits = {
        k: int(sum(1 for t in trades if t.exit_reason == k))
        for k in sorted({t.exit_reason for t in trades})
    }
    out.update(
        {
            "trades_per_active_day": n / active if active else math.nan,
            "median_trades_per_day": float(np.median(per_day)),
            "zero_trade_days": int((per_day == 0).sum()),
            "zero_trade_day_share": float(np.mean(per_day == 0)),
            "n_long": int(sum(1 for t in trades if t.pos["side"] > 0)),
            "n_short": int(sum(1 for t in trades if t.pos["side"] < 0)),
            "by_family_n": {
                f: int(sum(1 for t in trades if t.pos["family"] == f)) for f in FAMILIES
            },
            "win_rate": float(np.mean(net_r > 0)),
            "gross_r": float(gross_r.mean()),
            "net_r": float(net_r.mean()),
            "cost_r": float(cost_r.mean()),
            "avg_winner_r": float(net_r[net_r > 0].mean()) if (net_r > 0).any() else math.nan,
            "avg_loser_r": float(net_r[net_r <= 0].mean()) if (net_r <= 0).any() else math.nan,
            "payoff_ratio": float(net_r[net_r > 0].mean() / -net_r[net_r <= 0].mean())
            if (net_r > 0).any() and (net_r <= 0).any() and net_r[net_r <= 0].mean() != 0
            else math.nan,
            "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else math.inf,
            "total_net_r": float(net_r.sum()),
            "total_net_usd": float(net_usd.sum()),
            "max_dd_usd": float(dd.max()),
            "max_dd_pct": float(dd.max() / eq * 100.0),
            "max_dd_r": float(dd.max() / risk) if risk > 0 else math.nan,
            "median_hold_min": float(np.median(hold)),
            "p75_hold_min": _pct(hold, 75),
            "p90_hold_min": _pct(hold, 90),
            "mean_hold_min": float(np.mean(hold)),
            "exits": exits,
            "cost_usd_per_trade": float(cost_usd.mean()),
            "cost_pct_of_gross": float(100.0 * cost_usd.mean() / gross_usd.mean())
            if gross_usd.mean() > 0
            else math.nan,
            "gross_usd_per_trade": float(gross_usd.mean()),
            "net_usd_per_trade": float(net_usd.mean()),
            "mean_leverage": float(np.mean([t.pos["leverage"] for t in trades])),
            "mean_margin_usd": float(np.mean([t.pos["margin"] for t in trades])),
            "median_stop_pct": float(np.median([t.pos["stop_pct"] for t in trades])),
            "mfe_r_mean": float(np.mean([t.pos["mfe_r"] for t in trades])),
            "mae_r_mean": float(np.mean([t.pos["mae_r"] for t in trades])),
            "skipped_while_open": counters.get("skipped_while_open", 0),
        }
    )
    return out


def _sub(trades: list[Trade], key: Any) -> dict[str, Any]:
    if not trades:
        return {"n": 0}
    net = np.array([t.net_r for t in trades])
    usd = np.array([t.net_usd for t in trades])
    w, l_ = usd[usd > 0].sum(), -usd[usd <= 0].sum()
    return {
        "n": len(trades),
        "net_r": float(net.mean()),
        "win_rate": float(np.mean(net > 0)),
        "pf": float(w / l_) if l_ > 0 else math.inf,
        "total_net_r": float(net.sum()),
        "median_hold_min": float(np.median([t.hold_min for t in trades])),
    }


def breakdowns(trades: list[Trade]) -> dict[str, Any]:
    def ind(t: Trade, k: str) -> Any:
        return (t.decision.get("indicators") or {}).get(k)

    out: dict[str, Any] = {}
    out["family"] = {f: _sub([t for t in trades if t.pos["family"] == f], f) for f in FAMILIES}
    out["side"] = {
        s: _sub([t for t in trades if t.pos["side"] == (1 if s == "LONG" else -1)], s)
        for s in ("LONG", "SHORT")
    }
    out["family_side"] = {
        f"{f}-{s}": _sub(
            [
                t
                for t in trades
                if t.pos["family"] == f and t.pos["side"] == (1 if s == "LONG" else -1)
            ],
            f,
        )
        for f in FAMILIES
        for s in ("LONG", "SHORT")
    }
    out["trend_regime"] = {
        lab: _sub([t for t in trades if ind(t, "struct15") == v], lab)
        for lab, v in (("struct_up", 1.0), ("struct_down", -1.0), ("struct_neutral", 0.0))
    }
    out["vol_regime"] = {
        lab: _sub([t for t in trades if ind(t, "vol_tercile") == v], lab)
        for lab, v in (("low_vol", 0.0), ("mid_vol", 1.0), ("high_vol", 2.0))
    }
    out["hour_bucket"] = {
        lab: _sub(
            [t for t in trades if ind(t, "hour") is not None and lo <= ind(t, "hour") < hi], lab
        )
        for lab, lo, hi in (("00-07", 0, 8), ("08-15", 8, 16), ("16-23", 16, 24))
    }
    out["exit_reason"] = {
        k: _sub([t for t in trades if t.exit_reason == k], k)
        for k in sorted({t.exit_reason for t in trades})
    }
    return out


@dataclass
class WindowResult:
    name: str
    stats: dict[str, Any]
    stress: dict[str, Any]
    breakdown: dict[str, Any]
    trades: list[dict[str, Any]]
    frame_meta: dict[str, Any]
    score_dist: dict[str, Any]


def run_window(cfg: V11Config, data_dir: Path, name: str, start: str, end: str) -> WindowResult:
    a, b = ms(start), ms(end)
    inp = load_inputs(cfg, data_dir, a, b)
    if inp.rows.is_empty():
        raise RuntimeError("no 1m rows for the window")
    fr = build_frame_v11(inp.rows, cfg, inp.metrics, inp.funding)
    sc = score_frame(fr, cfg)
    ft = inp.funding["time_ms"].to_numpy().astype(np.int64) if not inp.funding.is_empty() else None
    frr = (
        inp.funding["funding_rate"].to_numpy().astype(np.float64)
        if not inp.funding.is_empty()
        else None
    )
    days = max(1, round((b - a) / DAY_MS))
    i0 = int(np.searchsorted(fr.t_open, a))
    sl, ss = sc["score_long"][i0:], sc["score_short"][i0:]
    score_dist = {
        "long_p50_p90_p99": [float(x) for x in np.nanpercentile(sl, [50, 90, 99])],
        "short_p50_p90_p99": [float(x) for x in np.nanpercentile(ss, [50, 90, 99])],
        "share_ge_candidate": float(
            np.mean((sl >= cfg.thresholds.candidate) | (ss >= cfg.thresholds.candidate))
        ),
        "share_ge_entry": float(
            np.mean((sl >= cfg.thresholds.entry) | (ss >= cfg.thresholds.entry))
        ),
        "available_weight": float(np.nanmedian(sc["avail_weight_long"][i0:])),
    }
    results = {}
    for tag, mult in (("primary", 1.0), ("stress", cfg.costs.stress_multiplier)):
        eng = V11Engine(cfg, fr, sc, mult, ft, frr)
        eng.run(a, b)
        results[tag] = (
            eng,
            trade_stats(
                eng.trades, days, cfg.risk.initial_equity, dict(eng.counters), dict(eng.blockers)
            ),
        )
        log.info(
            "%s %s: trades %d (%.2f/day) net %.3fR pf %.2f",
            name,
            tag,
            len(eng.trades),
            len(eng.trades) / days,
            results[tag][1].get("net_r", math.nan),
            results[tag][1].get("profit_factor", math.nan),
        )
    eng = results["primary"][0]
    st = results["primary"][1]
    st["by_family_funnel"] = eng.by_family
    return WindowResult(
        name,
        st,
        results["stress"][1],
        breakdowns(eng.trades),
        [_clean(t.as_dict()) for t in eng.trades],
        fr.meta,
        score_dist,
    )


def classify(cfg: V11Config, res: dict[str, WindowResult]) -> dict[str, Any]:
    tr, va = res["train"].stats, res["validation"].stats

    def g(st: dict[str, Any], k: str) -> float:
        v = st.get(k)
        return float(v) if v is not None else math.nan

    val_pos = g(va, "net_r") > 0 and g(va, "profit_factor") > 1.0
    both_pos = val_pos and g(tr, "net_r") > 0 and g(tr, "profit_factor") > 1.0
    freq_ok = g(tr, "trades_per_day") >= 3.0 and g(va, "trades_per_day") >= 3.0
    hold_ok = g(tr, "median_hold_min") <= 45 and g(va, "median_hold_min") <= 45
    strong = (
        both_pos
        and g(tr, "profit_factor") >= 1.10
        and g(va, "profit_factor") >= 1.10
        and freq_ok
        and hold_ok
    )
    if not val_pos:
        cls, why = (
            "C",
            "no historical net edge: validation net R <= 0 or PF <= 1 after primary costs",
        )
    elif strong:
        cls, why = (
            "A",
            "net positive with PF >= 1.10 on TRAIN and VALIDATION, >= 3 trades/day and median hold <= 45 min",
        )
    else:
        cls, why = (
            "B",
            "validation net positive but not every A condition holds (train edge, PF >= 1.10, frequency >= 3/day, hold <= 45 min)",
        )
    return {
        "classification": cls,
        "reason": why,
        "validation_net_positive": val_pos,
        "both_windows_positive": both_pos,
        "frequency_ok": freq_ok,
        "hold_ok": hold_ok,
    }


def run_research(cfg: V11Config, data_dir: Path, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    w = cfg.windows
    res = {
        "train": run_window(cfg, data_dir, "train", w.train.start, w.train.end_exclusive),
        "validation": run_window(
            cfg, data_dir, "validation", w.validation.start, w.validation.end_exclusive
        ),
    }
    manifest = {
        "generated_utc": datetime.now(tz=UTC).isoformat(),
        "v11_version": V11_VERSION,
        "config_hash": cfg.config_hash,
        "strategy_hash": cfg.strategy_hash,
        "code_version": code_version(),
        "dataset_hash_1m": dataset_hash_1m(data_dir),
        "windows": {
            "train": [w.train.start, w.train.end_exclusive],
            "validation": [w.validation.start, w.validation.end_exclusive],
        },
        "locked_holdout": "not read (every load capped at " + w.data_cap + ")",
        "historically_testable_groups": [
            "trend",
            "momentum",
            "volatility",
            "volume",
            "location",
            "derivatives (Binance OI/funding proxy)",
        ],
        "forward_only_groups": ["microstructure (order book, live taker windows, allLiquidation)"],
    }
    out = {
        "manifest": manifest,
        "windows": {
            k: {
                "stats": v.stats,
                "stress": v.stress,
                "breakdown": v.breakdown,
                "frame": v.frame_meta,
                "score_dist": v.score_dist,
            }
            for k, v in res.items()
        },
        "classification": classify(cfg, res),
    }
    (out_dir / "backtest.json").write_text(json.dumps(_clean(out), indent=1, sort_keys=True))
    for k, v in res.items():
        (out_dir / f"trades_{k}.json").write_text(json.dumps(_clean(v.trades)))
    return out
