"""Event census, fixed-horizon markouts, matched controls, chronological overlap replay and the two
verdicts (EDGE / CADENCE) for the N1 / N2 / N4 screens. Pure functions over ParentEpisode lists."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np

from btc_swing.research.intraday_portfolio import metrics as mx
from btc_swing.research.intraday_portfolio.contract import MS_1M
from btc_swing.research.intraday_portfolio.episodes import ParentEpisode
from btc_swing.research.intraday_portfolio.ledger import BASE_COST_BPS, COST_SCENARIOS_BPS
from btc_swing.research.intraday_portfolio.nengines import (
    COOLDOWN_MIN,
    NContext,
    ScreenResult,
    bars5_to_15m_boundaries,
    horizon_outcome,
)

CADENCE_CORE_MIN_PER_DAY = 0.75  # owner's planning requirement for a core engine
CADENCE_SATELLITE_MIN_PER_DAY = 0.25  # predeclared lower cut for "satellite only"
MIN_MONTHS_FOR_CADENCE = 6
ENGINE_ORDER = ("N1", "N2S", "N4")  # fixed tie order for the overlap replay (never PnL-ranked)
N2_CONTROLS_PER_PARENT = 5
N2_CONTROL_EXCLUSION_MIN = 60


# ----------------------------------------------------------------------------- frequency census
def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date().isoformat()


def _month(ms: int) -> str:
    return _day(ms)[:7]


def _all_days(first_day: str, last_day: str) -> list[str]:
    d0, d1 = datetime.fromisoformat(first_day).date(), datetime.fromisoformat(last_day).date()
    return [
        (d0 + (d1 - d0) * 0).fromordinal(d0.toordinal() + k).isoformat()
        for k in range((d1 - d0).days + 1)
    ]


def _q(xs: list[int], f: float) -> int | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(f * len(s)))]


def stage_stats(
    times_ms: list[int], sides: list[str], first_day: str, last_day: str
) -> dict[str, Any]:
    """Counts per calendar day INCLUDING zero-event days, per month, p10/p50/p90, long/short."""
    days = _all_days(first_day, last_day)
    per_day = Counter(_day(t) for t in times_ms)
    daily = [per_day.get(d, 0) for d in days]
    months = sorted({d[:7] for d in days})
    per_month = Counter(_month(t) for t in times_ms)
    monthly = [per_month.get(m, 0) for m in months]
    sc = Counter(sides)
    return {
        "total": len(times_ms),
        "calendar_days": len(days),
        "per_calendar_day": len(times_ms) / len(days) if days else None,
        "zero_event_days": sum(1 for x in daily if x == 0),
        "daily_p10": _q(daily, 0.1),
        "daily_p50": _q(daily, 0.5),
        "daily_p90": _q(daily, 0.9),
        "months": len(months),
        "monthly_mean": statistics.fmean(monthly) if monthly else None,
        "monthly_min": min(monthly) if monthly else None,
        "monthly_max": max(monthly) if monthly else None,
        "zero_event_months": sum(1 for x in monthly if x == 0),
        "long": sc.get("LONG", 0),
        "short": sc.get("SHORT", 0),
        "per_month": {m: per_month.get(m, 0) for m in months},
    }


def census(
    res: ScreenResult, available_ids: set[str], first_day: str, last_day: str
) -> dict[str, dict[str, Any]]:
    ps = res.parents
    raw_t = [t for p in ps for t in (p.first_setup_time_ms, *p.repeated_trigger_times_ms)]
    raw_s = [p.side for p in ps for _ in (p.first_setup_time_ms, *p.repeated_trigger_times_ms)]
    elig = [p for p in ps if p.traded]
    avail = [p for p in elig if p.parent_id in available_ids]
    out = {
        "raw_flags": stage_stats(raw_t, raw_s, first_day, last_day),
        "independent_parents": stage_stats(
            [p.first_setup_time_ms for p in ps], [p.side for p in ps], first_day, last_day
        ),
        "eligible_entries": stage_stats(
            [p.first_setup_time_ms for p in elig], [p.side for p in elig], first_day, last_day
        ),
        "portfolio_available": stage_stats(
            [p.first_setup_time_ms for p in avail], [p.side for p in avail], first_day, last_day
        ),
    }
    out["raw_flags"]["total"] = res.raw_flags  # flags before dedup as counted by the screen
    out["meta"] = {
        "evaluated": res.evaluated_bars,
        "skipped_no_threshold": res.skipped_no_threshold,
        "threshold_days": res.threshold_days,
        "rejections": dict(Counter(r for p in ps for r in p.rejection_reasons)),
        **res.extra,
    }
    return out


def census_rows(engine: str, c: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for stage in ("raw_flags", "independent_parents", "eligible_entries", "portfolio_available"):
        s = {k: v for k, v in c[stage].items() if k != "per_month"}
        rows.append({"engine": engine, "stage": stage, **s})
    return rows


# ----------------------------------------------------------------------------- markouts
def markout_row(engine: str, subset: str, ps: list[ParentEpisode]) -> dict[str, Any]:
    g = [p.gross_bps for p in ps if p.gross_bps is not None]
    days = [
        _day(p.times.benchmark_entry_time or p.first_setup_time_ms)
        for p in ps
        if p.gross_bps is not None
    ]
    ne = [
        p.notes["gross_from_close_no_embargo_bps"]
        for p in ps
        if isinstance(p.notes.get("gross_from_close_no_embargo_bps"), float)
        and math.isfinite(p.notes["gross_from_close_no_embargo_bps"])
    ]
    d60 = [
        p.notes["gross_60m_diag_bps"]
        for p in ps
        if isinstance(p.notes.get("gross_60m_diag_bps"), float)
        and math.isfinite(p.notes["gross_60m_diag_bps"])
    ]
    imp = [abs(p.notes["impulse_bps"]) for p in ps if isinstance(p.notes.get("impulse_bps"), float)]
    return {
        "engine": engine,
        "subset": subset,
        "n": len(g),
        "gross_bps_mean": statistics.fmean(g) if g else None,
        "gross_bps_median": statistics.median(g) if g else None,
        "gross_bps_lb95_day_cluster": mx.cluster_lower_bound(g, days) if g else None,
        "win_rate_gross": sum(1 for x in g if x > 0) / len(g) if g else None,
        "gross_no_embargo_bps_mean": statistics.fmean(ne) if ne else None,
        "gross_60m_diag_bps_mean": statistics.fmean(d60) if d60 else None,
        "abs_impulse_bps_mean": statistics.fmean(imp) if imp else None,
        "mfe_bps_mean": statistics.fmean([p.mfe_bps for p in ps if p.mfe_bps is not None])
        if ps
        else None,
        "mae_bps_mean": statistics.fmean([p.mae_bps for p in ps if p.mae_bps is not None])
        if ps
        else None,
    }


def markout_rows(engine: str, trades: list[ParentEpisode]) -> list[dict[str, Any]]:
    rows = [markout_row(engine, "ALL", trades)]
    for side in ("LONG", "SHORT"):
        rows.append(markout_row(engine, side, [p for p in trades if p.side == side]))
    bl = mx.breakdowns(trades)["chronological_block"] if trades else {}
    # block membership recomputed the same way as metrics.breakdowns (edges by entry time)
    ts = sorted(int(p.times.benchmark_entry_time or p.first_setup_time_ms) for p in trades)
    edges = [ts[int(k * len(ts) / mx.BLOCKS)] for k in range(1, mx.BLOCKS)] if ts else []
    for k in range(1, mx.BLOCKS + 1):
        sub = [
            p
            for p in trades
            if sum(
                1 for e in edges if int(p.times.benchmark_entry_time or p.first_setup_time_ms) >= e
            )
            + 1
            == k
        ]
        rows.append(markout_row(engine, f"block_{k}", sub))
    _ = bl
    sessions = sorted({str(p.notes.get("session")) for p in trades if p.notes.get("session")})
    for s in sessions:
        rows.append(
            markout_row(engine, f"session_{s}", [p for p in trades if p.notes.get("session") == s])
        )
    return rows


# ----------------------------------------------------------------------------- N2 matched controls
@dataclass
class ControlResult:
    rows: list[dict[str, Any]]
    summary: dict[str, Any]


def _quintile_edges(x: np.ndarray) -> np.ndarray:
    v = x[np.isfinite(x)]
    return np.quantile(v, [0.2, 0.4, 0.6, 0.8]) if len(v) else np.zeros(0)


def _bin(x: float, edges: np.ndarray) -> int:
    return int(np.searchsorted(edges, x, side="right")) if math.isfinite(x) else -1


def matched_controls(
    ctx: NContext, primary: ScreenResult, other: ScreenResult, horizon_min: int
) -> ControlResult:
    """Simple price/volume baseline matched on UTC hour, preceding 15m Bybit return quintile and ATR
    quintile, outside +-60 min of ANY spot or perp flow flag; control side = sign of the preceding 15m
    return (momentum), outcome from the same post-embargo entry convention."""
    b5 = ctx.b5
    idx = bars5_to_15m_boundaries(b5)
    flags = sorted(
        t
        for r in (primary, other)
        for p in r.parents
        for t in (p.first_setup_time_ms, *p.repeated_trigger_times_ms)
    )
    fl = np.array(flags, dtype=np.int64)
    excl = N2_CONTROL_EXCLUSION_MIN * MS_1M
    cand: list[
        tuple[int, int, float, float, int, float]
    ] = []  # (close_ms, hour, prior_ret, atr_bps, side, gross)
    pr_all, atr_all = [], []
    for i in idx:
        if i < 3 or not b5.contiguous(i - 3, i):
            continue
        t_close = int(b5.close_ms[i])
        j = int(np.searchsorted(fl, t_close))
        near = (j < len(fl) and fl[j] - t_close < excl) or (j > 0 and t_close - fl[j - 1] < excl)
        if near:
            continue
        pr = float(b5.close[i]) / float(b5.close[i - 3]) - 1
        atr_bps = float(ctx.atr[i]) / float(b5.close[i]) * 1e4
        if pr == 0 or not math.isfinite(atr_bps):
            continue
        side = 1 if pr > 0 else -1
        oc = horizon_outcome(ctx.b1, t_close + MS_1M, horizon_min, side)
        if oc is None:
            continue
        g = side * (oc.exit_px / oc.entry_px - 1) * 1e4
        hour = datetime.fromtimestamp(t_close / 1000, tz=UTC).hour
        cand.append((t_close, hour, pr * 1e4, atr_bps, side, g))
        pr_all.append(pr * 1e4)
        atr_all.append(atr_bps)
    e_pr, e_atr = _quintile_edges(np.array(pr_all)), _quintile_edges(np.array(atr_all))
    by_key: dict[tuple[int, int, int], list[tuple[int, float]]] = defaultdict(list)
    for t_close, hour, pr, atr_bps, _side, g in cand:
        by_key[(hour, _bin(pr, e_pr), _bin(atr_bps, e_atr))].append((t_close, g))
    rows: list[dict[str, Any]] = []
    diffs: list[float] = []
    days: list[str] = []
    pg: list[float] = []
    cg: list[float] = []
    for p in primary.parents:
        if not p.traded or p.gross_bps is None:
            continue
        pr_note = p.notes.get("prior_15m_ret_bps")
        atr_bps = float(p.frozen_levels.get("atr_bps", math.nan))
        key = (
            int(p.notes["utc_hour"]),
            _bin(float(pr_note) if pr_note is not None else math.nan, e_pr),
            _bin(atr_bps, e_atr),
        )
        pool = sorted(by_key.get(key, []), key=lambda x: (abs(x[0] - p.first_setup_time_ms), x[0]))
        ctrl = pool[:N2_CONTROLS_PER_PARENT]
        cm = statistics.fmean([g for _, g in ctrl]) if ctrl else None
        rows.append(
            {
                "parent_id": p.parent_id,
                "engine": primary.engine,
                "n_controls": len(ctrl),
                "parent_gross_bps": p.gross_bps,
                "control_mean_gross_bps": cm,
                "control_times_ms": "|".join(str(t) for t, _ in ctrl),
            }
        )
        if cm is not None:
            diffs.append(p.gross_bps - cm)
            days.append(_day(p.first_setup_time_ms))
            pg.append(p.gross_bps)
            cg.append(cm)
    summary = {
        "engine": primary.engine,
        "candidate_control_boundaries": len(cand),
        "parents_with_controls": len(diffs),
        "parent_gross_bps_mean": statistics.fmean(pg) if pg else None,
        "control_gross_bps_mean": statistics.fmean(cg) if cg else None,
        "incremental_bps_mean": statistics.fmean(diffs) if diffs else None,
        "incremental_bps_lb95_day_cluster": mx.cluster_lower_bound(diffs, days) if diffs else None,
    }
    return ControlResult(rows, summary)


def spot_vs_perp(spot: ScreenResult, perp: ScreenResult) -> dict[str, Any]:
    """Overlap of flag times between the spot and perp flow screens, and the SPOT-ONLY cohort markout
    (spot flow in the top decile while perp flow is not): the incremental information question."""

    def flag_set(r: ScreenResult) -> set[int]:
        return {t for p in r.parents for t in (p.first_setup_time_ms, *p.repeated_trigger_times_ms)}

    fs, fp = flag_set(spot), flag_set(perp)
    spot_only = [p for p in spot.parents if p.traded and p.first_setup_time_ms not in fp]
    both = [p for p in spot.parents if p.traded and p.first_setup_time_ms in fp]
    return {
        "spot_flags": len(fs),
        "perp_flags": len(fp),
        "flags_in_both": len(fs & fp),
        "spot_only_share_of_spot_flags": (len(fs - fp) / len(fs)) if fs else None,
        "spot_only": markout_row(spot.engine, "SPOT_ONLY", spot_only),
        "spot_and_perp": markout_row(spot.engine, "SPOT_AND_PERP", both),
        "perp_all": markout_row(perp.engine, "ALL", [p for p in perp.parents if p.traded]),
    }


# ----------------------------------------------------------------------------- overlap replay
@dataclass
class OverlapRow:
    parent_id: str
    engine: str
    side: str
    entry_ms: int
    exit_ms: int
    net_bps_16: float | None
    available: bool
    blocked_by_engine: str | None
    blocked_by_id: str | None
    same_direction: bool | None
    engines_active_at_entry: str


def overlap_replay(
    by_engine: dict[str, list[ParentEpisode]], order: tuple[str, ...] = ENGINE_ORDER
) -> list[OverlapRow]:
    """Chronological ONE-position replay of eligible parents as SIGNALS only (fixed horizons): a
    parent is available when nothing is open at its entry; otherwise it is blocked by the open parent."""
    rank = {e: k for k, e in enumerate(order)}
    ps = sorted(
        (p for v in by_engine.values() for p in v if p.traded and p.exit is not None),
        key=lambda p: (int(p.times.benchmark_entry_time or 0), rank.get(p.engine, 99), p.parent_id),
    )
    open_: ParentEpisode | None = None
    active: list[
        ParentEpisode
    ] = []  # every eligible parent whose window covers the entry (telemetry)
    out: list[OverlapRow] = []
    for p in ps:
        t = int(p.times.benchmark_entry_time or 0)
        x = int(p.exit.time_ms or 0) if p.exit else t
        active = [a for a in active if a.exit is not None and int(a.exit.time_ms or 0) > t]
        if open_ is not None and open_.exit is not None and int(open_.exit.time_ms or 0) <= t:
            open_ = None
        blocked = open_ is not None
        out.append(
            OverlapRow(
                p.parent_id,
                p.engine,
                p.side,
                t,
                x,
                mx.net_at(p, BASE_COST_BPS),
                not blocked,
                open_.engine if blocked and open_ else None,
                open_.parent_id if blocked and open_ else None,
                (open_.side == p.side) if blocked and open_ else None,
                "|".join(sorted({a.engine for a in active})),
            )
        )
        if not blocked:
            open_ = p
        active.append(p)
    return out


def overlap_summary(
    by_engine: dict[str, list[ParentEpisode]], first_day: str, last_day: str
) -> list[dict[str, Any]]:
    n_days = len(_all_days(first_day, last_day))
    all_rows = overlap_replay(by_engine)
    avail_all = {r.parent_id for r in all_rows if r.available}
    rows: list[dict[str, Any]] = []
    for e in by_engine:
        mine = [r for r in all_rows if r.engine == e]
        blocked = [r for r in mine if not r.available]
        others = {k: v for k, v in by_engine.items() if k != e}
        avail_without = {r.parent_id for r in overlap_replay(others) if r.available}
        inc = len(avail_all) - len(avail_without)
        prof = sum(1 for r in mine if r.available and r.net_bps_16 is not None and r.net_bps_16 > 0)
        overl = [r for r in mine if r.engines_active_at_entry]
        rows.append(
            {
                "engine": e,
                "eligible": len(mine),
                "standalone_eligible_per_day": len(mine) / n_days if n_days else None,
                "available_in_union": sum(1 for r in mine if r.available),
                "available_per_day": sum(1 for r in mine if r.available) / n_days
                if n_days
                else None,
                "blocked": len(blocked),
                "overlap_rate": len(overl) / len(mine) if mine else None,
                "same_direction_overlap": sum(1 for r in blocked if r.same_direction),
                "opposite_direction_conflict": sum(1 for r in blocked if r.same_direction is False),
                "incremental_available_vs_others": inc,
                "incremental_available_per_day": inc / n_days if n_days else None,
                "profitable_available_at_16bps": prof,
                "profitable_available_per_day": prof / n_days if n_days else None,
            }
        )
    rows.append(
        {
            "engine": "UNION",
            "eligible": len(all_rows),
            "available_in_union": len(avail_all),
            "available_per_day": len(avail_all) / n_days if n_days else None,
            "profitable_available_at_16bps": sum(
                1 for r in all_rows if r.available and r.net_bps_16 is not None and r.net_bps_16 > 0
            ),
        }
    )
    return rows


# ----------------------------------------------------------------------------- verdicts
def edge_verdict(gate: dict[str, Any]) -> dict[str, str]:
    v = gate["verdict"]
    if v == "ELIGIBLE FOR RUNNER ENGINEERING":
        v = "ELIGIBLE FOR FULL POLICY REPLAY"
    return {"verdict": v, "reason": gate["reason"]}


def cadence_verdict(
    eligible_per_day: float | None, incremental_per_day: float | None, months: float
) -> dict[str, str]:
    if eligible_per_day is None or months < MIN_MONTHS_FOR_CADENCE:
        return {
            "verdict": "INCONCLUSIVE",
            "reason": f"{months:.1f} months of evaluable data (< {MIN_MONTHS_FOR_CADENCE})",
        }
    inc = (
        f"; incremental available {incremental_per_day:.3f}/day in the union"
        if incremental_per_day is not None
        else ""
    )
    if eligible_per_day >= CADENCE_CORE_MIN_PER_DAY:
        return {
            "verdict": "PLAUSIBLE CORE CADENCE",
            "reason": f"{eligible_per_day:.3f} independent eligible/day >= {CADENCE_CORE_MIN_PER_DAY}{inc}",
        }
    if eligible_per_day >= CADENCE_SATELLITE_MIN_PER_DAY:
        return {
            "verdict": "SATELLITE ONLY",
            "reason": f"{eligible_per_day:.3f} independent eligible/day in [{CADENCE_SATELLITE_MIN_PER_DAY}, {CADENCE_CORE_MIN_PER_DAY}){inc}",
        }
    return {
        "verdict": "TOO LOW",
        "reason": f"{eligible_per_day:.3f} independent eligible/day < {CADENCE_SATELLITE_MIN_PER_DAY}{inc}",
    }


def cost_rows(engine: str, trades: list[ParentEpisode]) -> list[dict[str, Any]]:
    rows = []
    for c in COST_SCENARIOS_BPS:
        s = mx.summarize(trades, c)
        rows.append(
            {
                "engine": engine,
                "cost_scenario_bps": c,
                "n": s.get("n"),
                "gross_bps_mean": s.get("gross_bps_mean"),
                "net_bps_mean": s.get("net_bps_mean"),
                "net_bps_lb95_day_cluster": s.get("net_bps_lower_bound_95_day_cluster"),
                "net_r_sum": s.get("net_r_sum"),
                "net_usdt": s.get("net_usdt"),
                "profit_factor": s.get("profit_factor"),
                "win_rate": s.get("win_rate"),
            }
        )
    return rows


def block_rows(engine: str, trades: list[ParentEpisode]) -> list[dict[str, Any]]:
    if not trades:
        return []
    bl = mx.breakdowns(trades, BASE_COST_BPS)["chronological_block"]
    pos_total = sum(v.get("net_usdt", 0.0) for v in bl.values() if (v.get("net_usdt") or 0) > 0)
    rows = []
    for k, v in bl.items():
        rows.append(
            {
                "engine": engine,
                "block": k,
                "n": v.get("n"),
                "gross_bps_mean": v.get("gross_bps_mean"),
                "net_bps_mean_16": v.get("net_bps_mean"),
                "net_usdt": v.get("net_usdt"),
                "profit_factor": v.get("profit_factor"),
                "positive": (v.get("net_bps_mean") or 0) > 0,
                "share_of_positive_pnl": (v.get("net_usdt", 0.0) / pos_total)
                if pos_total > 0 and (v.get("net_usdt") or 0) > 0
                else 0.0,
            }
        )
    return rows


__all__ = ["COOLDOWN_MIN"]
