"""Metrics, breakdowns, frequency, cost scenarios and the predeclared early economic gates. Observations
are PARENT episodes (never overlapping repeated triggers). Day-cluster inference: the one-sided 95 % lower
bound of the mean net uses a cluster-robust standard error with UTC-day clusters (deterministic)."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from btc_swing.research.intraday_portfolio.episodes import ParentEpisode
from btc_swing.research.intraday_portfolio.ledger import (
    BASE_COST_BPS,
    COST_SCENARIOS_BPS,
    STRESS_COST_BPS,
    benchmark_net_bps,
    risk_usdt,
)

BLOCKS = 4
BLOCKS_POSITIVE_MIN = 3
GATES: dict[str, float] = {
    "mean_gross_bps_min": 25.0,
    "mean_net_bps_min_at_16": 9.0,
    "pf_min": 1.20,
    "stress_cost_bps": STRESS_COST_BPS,
    "blocks": float(BLOCKS),
    "blocks_positive_min": float(BLOCKS_POSITIVE_MIN),
    "max_block_share_of_positive_pnl": 0.5,
}
SAMPLE_MIN_PARENTS = 300
SAMPLE_MIN_MONTHS = 6


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date().isoformat()


def net_at(p: ParentEpisode, cost_bps: float) -> float | None:
    if p.gross_bps is None:
        return None
    f = p.notes.get("funding_bps_proxy", 0.0)
    fv = float(f) if isinstance(f, int | float) and math.isfinite(float(f)) else 0.0
    return benchmark_net_bps(p.gross_bps, cost_bps, fv)


def cluster_lower_bound(values: list[float], days: list[str], z: float = 1.645) -> float | None:
    """One-sided 95 % lower bound of the mean with UTC-day clusters (cluster-robust SE)."""
    n = len(values)
    if n < 2:
        return None
    m = statistics.fmean(values)
    by_day: dict[str, float] = defaultdict(float)
    for v, d in zip(values, days, strict=True):
        by_day[d] += v - m
    g = len(by_day)
    if g < 2:
        return None
    se = math.sqrt(sum(x * x for x in by_day.values()) * g / (g - 1)) / n
    return m - z * se


def summarize(trades: list[ParentEpisode], cost_bps: float = BASE_COST_BPS) -> dict[str, Any]:
    nets = [v for v in (net_at(p, cost_bps) for p in trades) if v is not None]
    if not nets:
        return {"n": 0}
    gross = [p.gross_bps for p in trades if p.gross_bps is not None]
    rs = [(net_at(p, cost_bps) or 0.0) / p.stop_bps for p in trades if p.stop_bps]
    usd = [risk_usdt(r) or 0.0 for r in rs]
    w = [x for x in nets if x > 0]
    l = [x for x in nets if x <= 0]  # noqa: E741
    cum = peak = dd = 0.0
    for x in usd:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    days = [
        _day(int(p.times.benchmark_entry_time or p.first_setup_time_ms))
        for p in trades
        if net_at(p, cost_bps) is not None
    ]
    return {
        "n": len(nets),
        "cost_bps": cost_bps,
        "gross_bps_mean": statistics.fmean(gross) if gross else None,
        "cost_bps_mean": statistics.fmean(
            ((p.gross_bps or 0.0) - (net_at(p, cost_bps) or 0.0))
            for p in trades
            if p.gross_bps is not None
        ),
        "net_bps_mean": statistics.fmean(nets),
        "net_bps_median": statistics.median(nets),
        "net_r_sum": sum(rs),
        "net_r_mean": statistics.fmean(rs) if rs else None,
        "net_usdt": sum(usd),
        "win_rate": len(w) / len(nets),
        "profit_factor": (sum(w) / -sum(l)) if l and sum(l) < 0 else (math.inf if w else None),
        "mfe_bps_mean": statistics.fmean([p.mfe_bps for p in trades if p.mfe_bps is not None])
        if trades
        else None,
        "mae_bps_mean": statistics.fmean([p.mae_bps for p in trades if p.mae_bps is not None])
        if trades
        else None,
        "hold_min_mean": statistics.fmean([p.hold_min for p in trades if p.hold_min is not None])
        if trades
        else None,
        "max_dd_usdt": dd,
        "turnover_trades_per_day_active": len(nets) / max(1, len(set(days))),
        "capital_employed_usdt_per_trade": 5000.0
        * 0.001
        / (statistics.fmean([p.stop_bps for p in trades if p.stop_bps]) / 1e4)
        if any(p.stop_bps for p in trades)
        else None,
        "net_bps_lower_bound_95_day_cluster": cluster_lower_bound(nets, days),
        "stop_bps_mean": statistics.fmean([p.stop_bps for p in trades if p.stop_bps])
        if any(p.stop_bps for p in trades)
        else None,
    }


def funnel(
    parents: list[ParentEpisode], first_day: str | None, last_day: str | None
) -> dict[str, Any]:
    traded = [p for p in parents if p.traded]
    eligible = [p for p in parents if p.eligibility_state in ("ELIGIBLE", "CLOSED")]
    n_days = 0
    if first_day and last_day:
        n_days = (datetime.fromisoformat(last_day) - datetime.fromisoformat(first_day)).days + 1
    per_day: Counter[str] = Counter(
        _day(int(p.times.benchmark_entry_time or p.first_setup_time_ms)) for p in traded
    )
    daily = sorted(per_day.values()) + [0] * max(0, n_days - len(per_day))
    daily.sort()

    def q(xs: list[int], f: float) -> float | None:
        return xs[min(len(xs) - 1, int(f * len(xs)))] if xs else None

    return {
        "raw_alerts": len(parents) + sum(p.repeated_triggers for p in parents),
        "unique_setups": len(parents),
        "parent_episodes": len(parents),
        "repeated_triggers_absorbed": sum(p.repeated_triggers for p in parents),
        "eligible_entries": len(eligible),
        "fills_benchmark": len(traded),
        "completed_trades": len(traded),
        "calendar_days": n_days,
        "candidates_per_day": len(parents) / n_days if n_days else None,
        "eligible_per_day": len(eligible) / n_days if n_days else None,
        "trades_per_day": len(traded) / n_days if n_days else None,
        "zero_trade_days": n_days - len(per_day) if n_days else None,
        "daily_frequency_p10_p50_p90": [q(daily, 0.1), q(daily, 0.5), q(daily, 0.9)],
        "rejection_rate": 1 - len(eligible) / len(parents) if parents else None,
        "rejection_reasons": dict(Counter(r for p in parents for r in p.rejection_reasons)),
    }


def breakdowns(
    trades: list[ParentEpisode], cost_bps: float = BASE_COST_BPS
) -> dict[str, dict[str, Any]]:
    def by(key: Any) -> dict[str, Any]:
        g: dict[str, list[ParentEpisode]] = defaultdict(list)
        for p in trades:
            g[str(key(p))].append(p)
        return {k: summarize(v, cost_bps) for k, v in sorted(g.items())}

    ts = sorted(int(p.times.benchmark_entry_time or p.first_setup_time_ms) for p in trades)
    edges = [ts[int(k * len(ts) / BLOCKS)] for k in range(1, BLOCKS)] if ts else []

    def block(p: ParentEpisode) -> str:
        t = int(p.times.benchmark_entry_time or p.first_setup_time_ms)
        return f"block_{sum(1 for e in edges if t >= e) + 1}"

    def weekday(p: ParentEpisode) -> str:
        return (
            "weekend"
            if datetime.fromtimestamp(p.first_setup_time_ms / 1000, tz=UTC).weekday() >= 5
            else "weekday"
        )

    atrs = sorted(
        p.frozen_levels.get("atr_setup", math.nan) / (p.entry_benchmark or 1) * 1e4
        for p in trades
        if p.entry_benchmark
    )
    cuts = [atrs[int(len(atrs) / 3)], atrs[int(2 * len(atrs) / 3)]] if len(atrs) >= 3 else []

    def vol(p: ParentEpisode) -> str:
        if not cuts or not p.entry_benchmark:
            return "n/a"
        a = p.frozen_levels.get("atr_setup", math.nan) / p.entry_benchmark * 1e4
        return "low_vol" if a <= cuts[0] else "high_vol" if a >= cuts[1] else "mid_vol"

    return {
        "side": by(lambda p: p.side),
        "chronological_block": by(block),
        "weekday_weekend": by(weekday),
        "volatility_bucket": by(vol),
        "engine": by(lambda p: p.engine),
        "exit_reason": by(lambda p: p.exit.reason if p.exit else "NONE"),
    }


def gates(
    trades: list[ParentEpisode],
    first_day: str | None,
    last_day: str | None,
    n_parents: int | None = None,
) -> dict[str, Any]:
    """Predeclared gates. The sample requirement counts PARENT episodes (>= 300 over >= 6 months, the
    owner's wording); the number of completed trades is reported next to it and never substituted."""
    s16 = summarize(trades, BASE_COST_BPS)
    s24 = summarize(trades, STRESS_COST_BPS)
    bl = breakdowns(trades, BASE_COST_BPS)["chronological_block"]
    pos_blocks = sum(1 for v in bl.values() if v.get("n") and (v.get("net_bps_mean") or 0) > 0)
    pos_pnl = {
        k: v.get("net_usdt", 0.0)
        for k, v in bl.items()
        if v.get("n") and (v.get("net_usdt") or 0) > 0
    }
    tot_pos = sum(pos_pnl.values())
    max_share = max(pos_pnl.values()) / tot_pos if tot_pos > 0 else None
    nets = sorted(((net_at(p, BASE_COST_BPS) or 0.0) for p in trades), reverse=True)
    without_top5 = statistics.fmean(nets[5:]) if len(nets) > 5 else None
    months = 0.0
    if first_day and last_day:
        months = (
            (datetime.fromisoformat(last_day) - datetime.fromisoformat(first_day)).days + 1
        ) / 30.44
    n = s16.get("n", 0)
    n_par = n if n_parents is None else n_parents
    checks = {
        "mean_gross_ge_25bps": (s16.get("gross_bps_mean") or -1) >= GATES["mean_gross_bps_min"],
        "mean_net_ge_9bps_at_16": (s16.get("net_bps_mean") or -1)
        >= GATES["mean_net_bps_min_at_16"],
        "net_r_gt_0": (s16.get("net_r_sum") or 0) > 0,
        "pf_ge_1_20": (s16.get("profit_factor") or 0) >= GATES["pf_min"],
        "day_cluster_lower_bound_gt_0": (s16.get("net_bps_lower_bound_95_day_cluster") or -1) > 0,
        "stress_24_mean_net_gt_0": (s24.get("net_bps_mean") or -1) > 0,
        "stress_24_pf_gt_1": (s24.get("profit_factor") or 0) > 1,
        "blocks_positive_ge_3_of_4": pos_blocks >= GATES["blocks_positive_min"],
        "no_block_gt_50pct_of_positive_pnl": max_share is not None
        and max_share <= GATES["max_block_share_of_positive_pnl"],
        "sample_ge_300_parents": n_par >= SAMPLE_MIN_PARENTS,
        "span_ge_6_months": months >= SAMPLE_MIN_MONTHS,
    }
    econ_pass = all(
        checks[k]
        for k in (
            "mean_gross_ge_25bps",
            "mean_net_ge_9bps_at_16",
            "net_r_gt_0",
            "pf_ge_1_20",
            "day_cluster_lower_bound_gt_0",
            "stress_24_mean_net_gt_0",
            "stress_24_pf_gt_1",
            "blocks_positive_ge_3_of_4",
            "no_block_gt_50pct_of_positive_pnl",
        )
    )
    sample_pass = checks["sample_ge_300_parents"] and checks["span_ge_6_months"]
    if n == 0:
        verdict, reason = "INCONCLUSIVE", "no completed trades"
    elif not sample_pass:
        verdict = "INCONCLUSIVE"  # INSUFFICIENT EVIDENCE: the requirement is never lowered
        reason = (
            f"sample {n_par} parents ({n} completed trades) over {months:.1f} months below the "
            ">=300 parents / >=6 months requirement"
            + (
                "; economics pass on the available sample"
                if econ_pass
                else "; economics fail on the available sample"
            )
        )
    elif econ_pass:
        verdict, reason = (
            "ELIGIBLE FOR RUNNER ENGINEERING",
            f"all predeclared economic gates pass ({n_par} parents, {n} trades, {months:.1f} months)",
        )
    else:
        failed = [k for k, v in checks.items() if not v]
        hard_fail = (s16.get("net_bps_mean") or 0) <= 0 and (
            s16.get("net_bps_lower_bound_95_day_cluster") or 0
        ) <= 0
        verdict = "REJECT" if hard_fail else "CONTINUE RESEARCH"
        reason = (
            f"{n_par} parents, {n} completed trades over {months:.1f} months; failed gates: "
            + ", ".join(failed)
            + (
                " (mean net <= 0 and the day-cluster lower bound <= 0 at 16 bps)"
                if hard_fail
                else ""
            )
        )
    return {
        "checks": checks,
        "verdict": verdict,
        "reason": reason,
        "n_parents": n_par,
        "n_completed_trades": n,
        "summary_16": s16,
        "summary_24": s24,
        "positive_blocks": pos_blocks,
        "max_block_share_of_positive_pnl": max_share,
        "fragility_mean_net_without_top5_bps": without_top5,
        "months": months,
        "scenarios": {str(c): summarize(trades, c) for c in COST_SCENARIOS_BPS},
    }
