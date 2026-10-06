"""V4 evaluation: generic reporting helpers are reused from the V3 evaluation module (frequency,
holding, chronology, leverage, MFE/MAE, exits, outliers); V4 adds the family table with event
counts and stop/cost geometry, the stop-geometry and cost-to-risk distributions, and the
pre-declared classification."""

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
from btc_swing.v4.config import V4Config, V4Family


def _f(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


def family_table(
    t: pl.DataFrame, events: pl.DataFrame, initial_equity: float, start_ms: int, end_ms: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    months = (end_ms - start_ms) / MONTH_MS
    ddw = dd_window_ids(t, initial_equity) if t.height else set()
    total_pnl = _f(t["POSITION_PNL"].sum()) if t.height else 0.0
    for fam in [f.value for f in V4Family]:
        for side in ("ALL", "LONG", "SHORT"):
            sub = (
                t.filter(pl.col("family") == fam)
                if side == "ALL"
                else t.filter((pl.col("family") == fam) & (pl.col("side") == side))
            )
            n_ev = (
                events.filter(pl.col("family") == fam).height
                if side == "ALL"
                else events.filter((pl.col("family") == fam) & (pl.col("side") == side)).height
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
                        "dd_window_pnl": _f(
                            sub.filter(pl.col("trade_id").is_in(list(ddw)))["POSITION_PNL"].sum()
                        ),
                        "pnl_share": st["sum_pnl"] / total_pnl if total_pnl else math.nan,
                    }
                )
            rows.append({"family": fam, "side": side, "events": n_ev, **st})
    return rows


def stop_geometry(t: pl.DataFrame, cfg: V4Config) -> dict[str, Any]:
    if t.is_empty():
        return {"n": 0}
    sp = t["stop_distance_pct"].to_numpy().astype(float) * 100
    sa = t["stop_distance_atr"].to_numpy().astype(float)
    cc = cfg.costs
    rt = (
        2 * cc.taker_fee_bps + cc.entry_slippage_bps + cc.stop_slippage_bps
    ) / 100.0  # % of notional
    cost_share = rt / sp
    drag = t["R_MULTIPLE_GROSS"].to_numpy().astype(float) - t["R_MULTIPLE"].to_numpy().astype(float)
    return {
        "n": t.height,
        "median_stop_pct": float(np.median(sp)),
        "mean_stop_pct": float(sp.mean()),
        "p10_stop_pct": float(np.quantile(sp, 0.1)),
        "p90_stop_pct": float(np.quantile(sp, 0.9)),
        "median_stop_atr": float(np.nanmedian(sa)),
        "share_vol_floor": _f((t["stop_source_rule"] == "VOL_FLOOR").cast(pl.Float64).mean())
        if "stop_source_rule" in t.columns
        else math.nan,
        "round_trip_cost_pct": rt,
        "median_cost_pct_of_stop": float(np.median(cost_share)),
        "p90_cost_pct_of_stop": float(np.quantile(cost_share, 0.9)),
        "share_cost_over_25pct_of_stop": float(np.mean(cost_share > 0.25)),
        "median_cost_drag_R": float(np.nanmedian(drag)),
        "mean_cost_drag_R": float(np.nanmean(drag)),
        "by_family": [
            {"family": k, "median_stop_pct": float(np.median(v)), "n": len(v)}
            for k, v in (
                (fam, sp[(t["family"] == fam).to_numpy()])
                for fam in sorted(t["family"].unique().to_list())
            )
            if len(v)
        ],
    }


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
                    }
                    for k in (1, 2, 3)
                ],
            }
        )
    return out


def evaluate_criteria(
    cfg: V4Config,
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

    def ok(x: float) -> bool:
        return not math.isnan(x)

    rows = [
        {
            "id": 0,
            "text": f"Stage A gate: pooled signed forward return at 4 h and 8 h > 0 with t >= {c.stage_a_min_t}, top strength tercile >= bottom at 8 h",
            "met": bool(gate.get("passed")),
            "evidence": f"4h mean {_f(gate.get('mean_4h')):+.4f} (t {_f(gate.get('t_4h')):.2f}); 8h mean {_f(gate.get('mean_8h')):+.4f} (t {_f(gate.get('t_8h')):.2f}); terciles top {_f(gate.get('top_tercile_8h')):+.4f} vs bottom {_f(gate.get('bottom_tercile_8h')):+.4f}",
        },
        {
            "id": 1,
            "text": f"gross expectancy >= +{c.min_gross_expectancy_r:.2f}R",
            "met": ok(gross) and gross >= c.min_gross_expectancy_r,
            "evidence": f"{gross:+.3f}R",
        },
        {
            "id": 2,
            "text": f"net expectancy >= +{c.min_net_expectancy_r:.2f}R",
            "met": ok(net) and net >= c.min_net_expectancy_r,
            "evidence": f"{net:+.3f}R (n={overall.get('n', 0)})",
        },
        {
            "id": 3,
            "text": f"profit factor >= {c.min_profit_factor:.2f}",
            "met": ok(pf) and pf >= c.min_profit_factor,
            "evidence": f"{pf:.2f}",
        },
        {
            "id": 4,
            "text": f"net > 0 in >= {c.min_positive_years} of {len(years)} years and >= {c.min_positive_quarter_share:.0%} of quarters with >= {c.min_quarter_trades} trades",
            "met": pos_years >= c.min_positive_years
            and ok(q_share)
            and q_share >= c.min_positive_quarter_share,
            "evidence": f"{pos_years}/{len(years)} years; {q_share:.0%} of {len(qs)} quarters",
        },
        {
            "id": 5,
            "text": f"cost drag <= {c.max_cost_drag_share:.0%} of gross expectancy",
            "met": drag_ok,
            "evidence": f"gross {gross:+.3f}R, net {net:+.3f}R",
        },
        {
            "id": 6,
            "text": f"max drawdown <= {c.max_drawdown:.0%}",
            "met": ok(dd) and dd <= c.max_drawdown,
            "evidence": f"{dd:.2%}",
        },
        {
            "id": 7,
            "text": f"{c.min_trades_per_day}-{c.max_trades_per_day} trades/day and median hold {c.median_hold_hours[0]:.0f}-{c.median_hold_hours[1]:.0f} h",
            "met": ok(tpd)
            and c.min_trades_per_day <= tpd <= c.max_trades_per_day
            and ok(med_h)
            and c.median_hold_hours[0] <= med_h <= c.median_hold_hours[1],
            "evidence": f"{tpd:.2f} trades/day; median hold {med_h:.1f} h",
        },
        {
            "id": 8,
            "text": f"net without the 5 best trades >= +{c.min_expectancy_without_best5_r:.2f}R and no family > {c.max_family_pnl_share:.0%} of net P&L",
            "met": ok(wo5)
            and wo5 >= c.min_expectancy_without_best5_r
            and (math.isnan(fam_share) or fam_share <= c.max_family_pnl_share),
            "evidence": f"without best 5 {wo5:+.3f}R; best family share {fam_share:.0%}",
        },
        {
            "id": 9,
            "text": "beats the time-matched and regime-matched nulls (net R)",
            "met": beats,
            "evidence": f"strategy {net:+.3f}R vs null time {nt:+.3f}R / regime {nr:+.3f}R",
        },
    ]
    return rows


def classify(cfg: V4Config, criteria: list[dict[str, Any]], costs: dict[str, Any]) -> str:
    met = {c["id"]: bool(c["met"]) for c in criteria}
    if all(met.values()):
        return "A — EVENT EDGE DEMONSTRATED, FREEZE FOR FORWARD PAPER TEST"
    gross = _f(costs.get("expectancy_R_before_costs"))
    if (
        met[0]
        and not math.isnan(gross)
        and gross >= cfg.research.criteria.b_min_gross_expectancy_r
        and met[9]
    ):
        return "B — EVENT EDGE EXISTS, CONTROLLED V4.1 SELECTION RESEARCH JUSTIFIED"
    return "C — NO ROBUST EVENT EDGE"


__all__ = [
    "DAY_MS",
    "chronology",
    "classify",
    "cost_impact",
    "derivatives_context",
    "evaluate_criteria",
    "exits_table",
    "family_table",
    "frequency",
    "holding",
    "leverage_audit",
    "mfe_mae",
    "outliers",
    "regime_table",
    "sides",
    "stop_geometry",
    "strength_vs_outcome",
]
