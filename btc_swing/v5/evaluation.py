"""V5 evaluation: generic reporting helpers are reused from the V3 evaluation module (frequency,
holding, chronology, leverage, MFE/MAE, exits); V5 adds the event -> qualified setup -> trade
funnel, the family table with event counts, entry-delay statistics, stop geometry, extended
outlier robustness (best/worst 1/3/5, median R) and the pre-declared ten-criterion
classification."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import polars as pl

from btc_swing.research.phase2 import DAY_MS, cost_impact
from btc_swing.research.phase24 import _by, _stats
from btc_swing.v3.evaluation import (
    MONTH_MS,
    chronology,
    dd_window_ids,
    derivatives_context,
    exits_table,
    frequency,
    holding,
    leverage_audit,
    mfe_mae,
    outliers,
)
from btc_swing.v5.config import V5Config, V5Family


def _f(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


def _count(df: pl.DataFrame, fam: str, side: str, extra: pl.Expr | None = None) -> int:
    if df.is_empty() or "family" not in df.columns:
        return 0
    e = pl.col("family") == fam
    if side != "ALL":
        e = e & (pl.col("side") == side)
    if extra is not None:
        e = e & extra
    return df.filter(e).height


def family_table(
    t: pl.DataFrame,
    events: pl.DataFrame,
    episodes: pl.DataFrame,
    initial_equity: float,
    start_ms: int,
    end_ms: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    months = (end_ms - start_ms) / MONTH_MS
    ddw = dd_window_ids(t, initial_equity) if t.height else set()
    total_pnl = _f(t["POSITION_PNL"].sum()) if t.height else 0.0
    for fam in [f.value for f in V5Family]:
        for side in ("ALL", "LONG", "SHORT"):
            sub = (
                (
                    t.filter(pl.col("family") == fam)
                    if side == "ALL"
                    else t.filter((pl.col("family") == fam) & (pl.col("side") == side))
                )
                if t.height
                else t
            )
            st: dict[str, Any] = _stats(sub) if sub.height else {"n": 0}
            if sub.height:
                ci = cost_impact(sub)
                st.update(
                    {
                        "gross_mean_R": ci.get("expectancy_R_before_costs"),
                        "cost_drag_R": ci.get("cost_drag_R_per_trade"),
                        "median_hold_h": _f(sub["holding_hours"].median()),
                        "trades_per_month": sub.height / months,
                        "median_stop_pct": 100 * _f(sub["stop_distance_pct"].median()),
                        "median_stop_atr": _f(sub["stop_distance_atr"].median()),
                        "median_entry_delay_min": _f(sub["entry_delay_min"].median()),
                        "median_missed_atr": _f(sub["missed_move_atr"].median()),
                        "dd_window_pnl": _f(
                            sub.filter(pl.col("trade_id").is_in(list(ddw)))["POSITION_PNL"].sum()
                        ),
                        "pnl_share": st["sum_pnl"] / total_pnl if total_pnl else math.nan,
                    }
                )
            rows.append(
                {
                    "family": fam,
                    "side": side,
                    "events": _count(events, fam, side),
                    "events_first_in_cluster": _count(
                        events, fam, side, pl.col("first_in_cluster")
                    ),
                    "episodes": _count(episodes, fam, side),
                    "qualified": _count(episodes, fam, side, pl.col("confirmed"))
                    if "confirmed" in episodes.columns
                    else 0,
                    **st,
                }
            )
    return rows


def funnel(
    events: pl.DataFrame,
    episodes: pl.DataFrame,
    t: pl.DataFrame,
    blocked: dict[str, int],
    start_ms: int,
    end_ms: int,
) -> dict[str, Any]:
    days = (end_ms - start_ms) / DAY_MS
    n_ev = events.height
    n_fic = int(events["first_in_cluster"].sum()) if n_ev else 0
    n_ep = episodes.height
    n_conf = int(episodes["confirmed"].sum()) if n_ep and "confirmed" in episodes.columns else 0
    n_ready = (
        int(episodes["reached_entry_ready"].sum())
        if n_ep and "reached_entry_ready" in episodes.columns
        else 0
    )
    n_trig = n_ep - int(episodes["expired"].sum()) if n_ep and "expired" in episodes.columns else 0
    out: dict[str, Any] = {
        "days": days,
        "events": n_ev,
        "events_per_day": n_ev / days,
        "first_in_cluster": n_fic,
        "first_in_cluster_per_day": n_fic / days,
        "episodes": n_ep,
        "episodes_per_day": n_ep / days,
        "confirmed": n_conf,
        "confirmed_per_day": n_conf / days,
        "entry_ready": n_ready,
        "entry_ready_per_day": n_ready / days,
        "episodes_not_expired": n_trig,
        "trades": t.height,
        "trades_per_day": t.height / days,
        "blocked": dict(blocked),
        "episode_end_reasons": episodes.group_by("end_reason")
        .len()
        .sort("len", descending=True)
        .to_dicts()
        if n_ep
        else [],
    }
    return out


def stop_geometry(t: pl.DataFrame, cfg: V5Config) -> dict[str, Any]:
    if t.is_empty():
        return {"n": 0}
    sp = t["stop_distance_pct"].to_numpy().astype(float) * 100
    sa = t["stop_distance_atr"].to_numpy().astype(float)
    cc = cfg.costs
    rt = (2 * cc.taker_fee_bps + cc.entry_slippage_bps + cc.stop_slippage_bps) / 100.0
    cost_share = rt / sp
    drag = t["R_MULTIPLE_GROSS"].to_numpy().astype(float) - t["R_MULTIPLE"].to_numpy().astype(float)
    return {
        "n": t.height,
        "median_stop_pct": float(np.median(sp)),
        "mean_stop_pct": float(sp.mean()),
        "p10_stop_pct": float(np.quantile(sp, 0.1)),
        "p90_stop_pct": float(np.quantile(sp, 0.9)),
        "median_stop_atr": float(np.nanmedian(sa)),
        "p10_stop_atr": float(np.nanquantile(sa, 0.1)),
        "p90_stop_atr": float(np.nanquantile(sa, 0.9)),
        "share_vol_floor": _f((t["stop_source_rule"] == "VOL_FLOOR").cast(pl.Float64).mean()),
        "share_capped": float(np.mean(sa >= cfg.execution.max_stop_atr * 0.98)),
        "round_trip_cost_pct": rt,
        "median_cost_pct_of_stop": float(np.median(cost_share)),
        "p90_cost_pct_of_stop": float(np.quantile(cost_share, 0.9)),
        "share_cost_over_25pct_of_stop": float(np.mean(cost_share > 0.25)),
        "median_cost_drag_R": float(np.nanmedian(drag)),
        "mean_cost_drag_R": float(np.nanmean(drag)),
        "by_family": [
            {
                "family": fam,
                "n": int(m.sum()),
                "median_stop_pct": float(np.median(sp[m])),
                "median_stop_atr": float(np.nanmedian(sa[m])),
                "share_vol_floor": float(
                    (t["stop_source_rule"].to_numpy()[m] == "VOL_FLOOR").mean()
                ),
            }
            for fam in sorted(t["family"].unique().to_list())
            for m in [(t["family"] == fam).to_numpy()]
            if m.any()
        ],
    }


def entry_delay(t: pl.DataFrame) -> dict[str, Any]:
    if t.is_empty():
        return {"n": 0}

    def q(col: str) -> dict[str, float]:
        x = t[col].to_numpy().astype(float)
        x = x[~np.isnan(x)]
        if not len(x):
            return {}
        return {
            "mean": float(x.mean()),
            "p10": float(np.quantile(x, 0.1)),
            "p50": float(np.median(x)),
            "p90": float(np.quantile(x, 0.9)),
            "max": float(x.max()),
        }

    out: dict[str, Any] = {
        "n": t.height,
        "confirm_delay_min": q("confirm_delay_min"),
        "entry_delay_min": q("entry_delay_min"),
        "missed_move_atr": q("missed_move_atr"),
        "missed_move_pct": q("missed_move_pct"),
        "share_missed_over_half_atr": _f((t["missed_move_atr"] > 0.5).cast(pl.Float64).mean()),
        "share_entered_below_event_close": _f((t["missed_move_atr"] < 0).cast(pl.Float64).mean()),
        "by_family": [],
    }
    for fam in sorted(t["family"].unique().to_list()):
        sub = t.filter(pl.col("family") == fam)
        out["by_family"].append(
            {
                "family": fam,
                "n": sub.height,
                "median_entry_delay_min": _f(sub["entry_delay_min"].median()),
                "median_confirm_delay_min": _f(sub["confirm_delay_min"].median()),
                "median_missed_atr": _f(sub["missed_move_atr"].median()),
                "mean_missed_atr": _f(sub["missed_move_atr"].mean()),
            }
        )
    return out


def regime_table(t: pl.DataFrame) -> list[dict[str, Any]]:
    return _by(t, "regime_at_trigger") if t.height else []


def sides(t: pl.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for s in ("LONG", "SHORT"):
        sub = t.filter(pl.col("side") == s) if t.height else t
        d: dict[str, Any] = _stats(sub) if sub.height else {"n": 0}
        if sub.height:
            ci = cost_impact(sub)
            d.update(
                {
                    "gross_mean_R": ci.get("expectancy_R_before_costs"),
                    "cost_drag_R": ci.get("cost_drag_R_per_trade"),
                    "median_hold_h": _f(sub["holding_hours"].median()),
                }
            )
        out[s] = d
    return out


def strength_vs_outcome(t: pl.DataFrame) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if t.is_empty() or "event_strength" not in t.columns:
        return out
    for fam in sorted(t["family"].unique().to_list()):
        sub = t.filter(pl.col("family") == fam)
        st = sub["event_strength"].to_numpy().astype(float)
        if sub.height < 30 or np.isnan(st).all():
            out.append({"family": fam, "n": sub.height})
            continue
        q1, q2 = np.nanquantile(st, [1 / 3, 2 / 3])
        terc = np.where(st <= q1, 1, np.where(st <= q2, 2, 3))
        tsub = sub.with_columns(pl.Series("tercile", terc))
        out.append(
            {
                "family": fam,
                "n": sub.height,
                "terciles": [
                    {
                        "tercile": k,
                        "n": tsub.filter(pl.col("tercile") == k).height,
                        "mean_R": _f(tsub.filter(pl.col("tercile") == k)["R_MULTIPLE"].mean()),
                        "mean_R_gross": _f(
                            tsub.filter(pl.col("tercile") == k)["R_MULTIPLE_GROSS"].mean()
                        ),
                        "win_rate": _f(
                            (tsub.filter(pl.col("tercile") == k)["POSITION_PNL"] > 0)
                            .cast(pl.Float64)
                            .mean()
                        ),
                    }
                    for k in (1, 2, 3)
                ],
            }
        )
    return out


def outliers_ext(
    t: pl.DataFrame, chrono: dict[str, Any], fams: list[dict[str, Any]]
) -> dict[str, Any]:
    out = outliers(t, chrono, fams)
    if t.is_empty():
        return out
    r = np.sort(t["R_MULTIPLE"].to_numpy().astype(float))
    g = np.sort(t["R_MULTIPLE_GROSS"].to_numpy().astype(float))
    n = len(r)
    for k in (1, 3, 5):
        out[f"mean_R_without_best{k}"] = float(r[:-k].mean()) if n > k else math.nan
        out[f"mean_R_without_worst{k}"] = float(r[k:].mean()) if n > k else math.nan
        out[f"mean_R_gross_without_best{k}"] = float(g[:-k].mean()) if n > k else math.nan
    out["mean_R_without_best5_worst5"] = float(r[5:-5].mean()) if n > 10 else math.nan
    out["median_R"] = float(np.median(r))
    out["median_R_gross"] = float(np.median(g))
    out["best_trade_R"], out["worst_trade_R"] = float(r[-1]), float(r[0])
    out["share_trades_positive_R"] = float((r > 0).mean())
    return out


def evaluate_criteria(
    cfg: V5Config,
    gate: dict[str, Any],
    overall: dict[str, Any],
    costs: dict[str, Any],
    account: dict[str, Any],
    freq: dict[str, Any],
    hold: dict[str, Any],
    chrono: dict[str, Any],
    outl: dict[str, Any],
    null: dict[str, Any],
) -> list[dict[str, Any]]:
    c = cfg.research.criteria
    gross = _f(costs.get("expectancy_R_before_costs"))
    net = _f(overall.get("mean_R"))
    pf = _f(overall.get("profit_factor"))
    years = chrono.get("years", {})
    pos_years = sum(1 for v in years.values() if v.get("n", 0) and _f(v.get("mean_R")) > 0)
    qs = [v for v in chrono.get("quarters", {}).values() if v.get("n", 0) >= c.min_quarter_trades]
    q_share = float(np.mean([_f(v.get("mean_R")) > 0 for v in qs])) if qs else math.nan
    drag_ok = not math.isnan(gross) and gross > 0 and net >= c.max_cost_drag_share * gross
    dd = abs(_f(account.get("max_drawdown_frac_trade_curve")))
    tpd = _f(freq.get("trades_per_day"))
    med_h = _f(hold.get("quantiles", {}).get(0.5))
    fam_share = _f(outl.get("best_family_share_of_pnl"))
    wo5 = _f(outl.get("mean_R_without_best5"))
    nt = _f((null.get("time") or {}).get("null_mean_R"))
    nr = _f((null.get("regime") or {}).get("null_mean_R"))
    beats = (not math.isnan(nt) and net > nt) and (not math.isnan(nr) and net > nr)
    g = cfg.stage_a.gate

    def ok(x: float) -> bool:
        return not math.isnan(x)

    return [
        {
            "id": 1,
            "text": f"Stage A gate passed by >= 1 family (first-in-cluster n >= {g.min_events}; mean > 0 with t >= {g.min_t} at 1 h or 4 h; positive in >= {g.min_positive_years} years; top tercile >= bottom)",
            "met": bool(gate.get("passed")),
            "evidence": f"{gate.get('n_passed', 0)} of 4 families passed: {', '.join(gate.get('passed_families', [])) or 'none'}",
        },
        {
            "id": 2,
            "text": f"pooled gross expectancy >= +{c.min_gross_expectancy_r:.2f}R",
            "met": ok(gross) and gross >= c.min_gross_expectancy_r,
            "evidence": f"{gross:+.3f}R",
        },
        {
            "id": 3,
            "text": f"pooled net expectancy >= +{c.min_net_expectancy_r:.2f}R",
            "met": ok(net) and net >= c.min_net_expectancy_r,
            "evidence": f"{net:+.3f}R (n={overall.get('n', 0)})",
        },
        {
            "id": 4,
            "text": f"profit factor >= {c.min_profit_factor:.2f}",
            "met": ok(pf) and pf >= c.min_profit_factor,
            "evidence": f"{pf:.2f}",
        },
        {
            "id": 5,
            "text": f"net > 0 in >= {c.min_positive_years} of {len(years)} years and >= {c.min_positive_quarter_share:.0%} of quarters with >= {c.min_quarter_trades} trades",
            "met": pos_years >= c.min_positive_years
            and ok(q_share)
            and q_share >= c.min_positive_quarter_share,
            "evidence": f"{pos_years}/{len(years)} years; {q_share:.0%} of {len(qs)} quarters"
            if ok(q_share)
            else f"{pos_years}/{len(years)} years; no quarter with >= {c.min_quarter_trades} trades",
        },
        {
            "id": 6,
            "text": f"cost drag <= {c.max_cost_drag_share:.0%} of gross expectancy",
            "met": drag_ok,
            "evidence": f"gross {gross:+.3f}R, net {net:+.3f}R",
        },
        {
            "id": 7,
            "text": f"max drawdown <= {c.max_drawdown:.0%}",
            "met": ok(dd) and dd <= c.max_drawdown,
            "evidence": f"{dd:.2%}",
        },
        {
            "id": 8,
            "text": f"{c.min_trades_per_day}-{c.max_trades_per_day} trades/day and median hold {c.median_hold_hours[0]:.0f}-{c.median_hold_hours[1]:.0f} h",
            "met": ok(tpd)
            and c.min_trades_per_day <= tpd <= c.max_trades_per_day
            and ok(med_h)
            and c.median_hold_hours[0] <= med_h <= c.median_hold_hours[1],
            "evidence": f"{tpd:.2f} trades/day; median hold {med_h:.1f} h",
        },
        {
            "id": 9,
            "text": f"net without the 5 best trades >= +{c.min_expectancy_without_best5_r:.2f}R and no family > {c.max_family_pnl_share:.0%} of net P&L",
            "met": ok(wo5)
            and wo5 >= c.min_expectancy_without_best5_r
            and (math.isnan(fam_share) or fam_share <= c.max_family_pnl_share),
            "evidence": f"without best 5 {wo5:+.3f}R; best family share {fam_share:.0%}"
            if ok(fam_share)
            else f"without best 5 {wo5:+.3f}R",
        },
        {
            "id": 10,
            "text": "beats the time-matched and regime-matched nulls (net R)",
            "met": beats,
            "evidence": f"strategy {net:+.3f}R vs null time {nt:+.3f}R / regime {nr:+.3f}R",
        },
    ]


def classify(cfg: V5Config, criteria: list[dict[str, Any]], costs: dict[str, Any]) -> str:
    met = {c["id"]: bool(c["met"]) for c in criteria}
    if all(met.values()):
        return "A — MICROSTRUCTURE EDGE STRONG ENOUGH FOR FORWARD PAPER TEST"
    gross = _f(costs.get("expectancy_R_before_costs"))
    if (
        met[1]
        and not math.isnan(gross)
        and gross >= cfg.research.criteria.b_min_gross_expectancy_r
        and met[10]
    ):
        return "B — MICROSTRUCTURE EDGE EXISTS, CONTROLLED V5.1 RESEARCH JUSTIFIED"
    return "C — NO ROBUST MICROSTRUCTURE EDGE"


__all__ = [
    "DAY_MS",
    "chronology",
    "classify",
    "cost_impact",
    "derivatives_context",
    "entry_delay",
    "evaluate_criteria",
    "exits_table",
    "family_table",
    "frequency",
    "funnel",
    "holding",
    "leverage_audit",
    "mfe_mae",
    "outliers_ext",
    "regime_table",
    "sides",
    "stop_geometry",
    "strength_vs_outcome",
]
