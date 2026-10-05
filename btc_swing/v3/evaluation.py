"""V3 evaluation: frequency, holding periods, chronology, families, regimes, derivatives context,
MFE/MAE, exits, costs, leverage, outlier dependence and the pre-declared classification. Pure
functions of the trade frame produced by `V3Engine`."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl
from scipy.stats import spearmanr

from btc_swing.research.phase2 import DAY_MS, account_stats, cost_impact
from btc_swing.research.phase24 import _by, _stats
from btc_swing.v3.config import V3Config, V3Family, V3Regime

MONTH_MS = 30.4375 * DAY_MS
HOLD_BUCKETS = [(0, 1), (1, 2), (2, 6), (6, 12), (12, 24), (24, 48), (48, math.inf)]
DERIV_KEYS = [
    "f_funding_rate_last",
    "f_funding_rate_mean_3",
    "f_oi_change_1h_pct",
    "f_oi_change_4h_pct",
    "f_oi_change_24h_pct",
    "f_long_short_ratio_accounts",
    "f_top_trader_ls_positions",
    "f_taker_long_short_vol_ratio",
    "f_taker_buy_ratio_1h",
    "f_taker_buy_ratio_4h",
    "f_premium_index",
    "f_premium_mean_1h",
    "f_last_minus_mark_pct",
    "f_volume_accel_5m",
    "f_volume_accel_1h",
]


def _f(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


def _year(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).year


def _quarter(ms: int) -> str:
    d = datetime.fromtimestamp(ms / 1000, tz=UTC)
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def with_periods(t: pl.DataFrame) -> pl.DataFrame:
    if t.is_empty():
        return t
    return t.with_columns(
        pl.col("entry_ms")
        .map_elements(lambda x: _year(int(x)), return_dtype=pl.Int64)
        .alias("year"),
        pl.col("entry_ms")
        .map_elements(lambda x: _quarter(int(x)), return_dtype=pl.Utf8)
        .alias("quarter"),
        (pl.col("entry_ms") // DAY_MS).alias("day"),
    )


# --------------------------------------------------------------------------- frequency / holding
def frequency(t: pl.DataFrame, start_ms: int, end_ms: int) -> dict[str, Any]:
    days = (end_ms - start_ms) / DAY_MS
    out: dict[str, Any] = {"days": days, "n": t.height}
    if t.is_empty():
        return out
    out.update(
        {
            "trades_per_day": t.height / days,
            "trades_per_week": 7 * t.height / days,
            "trades_per_month": t.height / (days / 30.4375),
            "median_hours_between_entries": float(
                np.median(np.diff(np.sort(t["entry_ms"].to_numpy().astype(float))) / 3_600_000.0)
            )
            if t.height > 1
            else math.nan,
            "days_distribution": _day_distribution(t, start_ms, end_ms),
            "by_year": {},
        }
    )
    tp = with_periods(t)
    for y in sorted(tp["year"].unique().to_list()):
        ys, ye = max(start_ms, _ms_of(y, 1, 1)), min(end_ms, _ms_of(y + 1, 1, 1))
        sub = tp.filter(pl.col("year") == y)
        d = (ye - ys) / DAY_MS
        out["by_year"][str(y)] = {
            "n": sub.height,
            "days": d,
            "trades_per_day": sub.height / d,
            "trades_per_month": sub.height / (d / 30.4375),
            "days_distribution": _day_distribution(sub, ys, ye),
        }
    return out


def _ms_of(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


def _day_distribution(t: pl.DataFrame, start_ms: int, end_ms: int) -> dict[str, float]:
    n_days = math.ceil((end_ms - start_ms) / DAY_MS)
    counts = np.zeros(n_days, dtype=int)
    for e in t["entry_ms"].to_numpy():
        k = int((int(e) - start_ms) // DAY_MS)
        if 0 <= k < n_days:
            counts[k] += 1
    return {
        "days": n_days,
        "share_0": float(np.mean(counts == 0)),
        "share_1": float(np.mean(counts == 1)),
        "share_2": float(np.mean(counts == 2)),
        "share_3plus": float(np.mean(counts >= 3)),
        "max_per_day": int(counts.max()) if n_days else 0,
    }


def holding(t: pl.DataFrame) -> dict[str, Any]:
    if t.is_empty():
        return {"n": 0}
    h = t["holding_hours"].to_numpy().astype(float)
    return {
        "n": len(h),
        "quantiles": {q: float(np.quantile(h, q)) for q in (0.1, 0.25, 0.5, 0.75, 0.9)},
        "mean": float(h.mean()),
        "buckets": [
            {
                "bucket": f"{a}-{b}h" if b != math.inf else f">{a}h",
                "share": float(np.mean((h >= a) & (h < b))),
                "n": int(np.sum((h >= a) & (h < b))),
            }
            for a, b in HOLD_BUCKETS
        ],
        "share_under_30min": float(np.mean(h < 0.5)),
        "by_exit": {
            k: _f(v)
            for k, v in t.group_by("exit_reason").agg(pl.col("holding_hours").median()).iter_rows()
        },
    }


# --------------------------------------------------------------------------- chronology
def _period_stats(
    sub: pl.DataFrame, initial_equity: float, start_ms: int, end_ms: int
) -> dict[str, Any]:
    st = _stats(sub)
    if not sub.height:
        return st
    ci = cost_impact(sub)
    pnl = sub.sort("entry_ms")["POSITION_PNL"].to_numpy().astype(float)
    curve = np.concatenate([[initial_equity], initial_equity + np.cumsum(pnl)])
    dd = float(((curve - np.maximum.accumulate(curve)) / np.maximum.accumulate(curve)).min())
    days = (end_ms - start_ms) / DAY_MS
    st.update(
        {
            "gross_mean_R": ci.get("expectancy_R_before_costs"),
            "cost_drag_R": ci.get("cost_drag_R_per_trade"),
            "max_dd": dd,
            "trades_per_day": sub.height / days if days > 0 else math.nan,
            "return": float(curve[-1] / initial_equity - 1.0),
            "sum_pnl": float(pnl.sum()),
        }
    )
    return st


def chronology(
    t: pl.DataFrame, initial_equity: float, start_ms: int, end_ms: int
) -> dict[str, Any]:
    out: dict[str, Any] = {"years": {}, "quarters": {}}
    if t.is_empty():
        return out
    tp = with_periods(t)
    for y in range(_year(start_ms), _year(end_ms - 1) + 1):
        ys, ye = max(start_ms, _ms_of(y, 1, 1)), min(end_ms, _ms_of(y + 1, 1, 1))
        out["years"][str(y)] = _period_stats(tp.filter(pl.col("year") == y), initial_equity, ys, ye)
    for q in sorted(tp["quarter"].unique().to_list()):
        yq, qn = int(q[:4]), int(q[-1])
        qs = _ms_of(yq, 3 * (qn - 1) + 1, 1)
        qe = _ms_of(yq + (1 if qn == 4 else 0), 1 if qn == 4 else 3 * qn + 1, 1)
        out["quarters"][q] = _period_stats(
            tp.filter(pl.col("quarter") == q), initial_equity, max(start_ms, qs), min(end_ms, qe)
        )
    return out


# --------------------------------------------------------------------------- families / regimes
def dd_window_ids(t: pl.DataFrame, initial_equity: float) -> set[int]:
    if t.is_empty():
        return set()
    s = t.sort("entry_ms")
    curve = np.concatenate(
        [[initial_equity], initial_equity + np.cumsum(s["POSITION_PNL"].to_numpy().astype(float))]
    )
    peak = np.maximum.accumulate(curve)
    trough = int(np.argmin(curve - peak))
    pk = int(np.argmax(curve[: trough + 1]))
    return set(int(x) for x in s["trade_id"].to_numpy()[pk:trough])


def family_table(
    t: pl.DataFrame, initial_equity: float, start_ms: int, end_ms: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if t.is_empty():
        return rows
    months = (end_ms - start_ms) / MONTH_MS
    ddw = dd_window_ids(t, initial_equity)
    total_pnl = _f(t["POSITION_PNL"].sum())
    for fam in [f.value for f in V3Family]:
        for side in ("ALL", "LONG", "SHORT"):
            sub = (
                t.filter(pl.col("family") == fam)
                if side == "ALL"
                else t.filter((pl.col("family") == fam) & (pl.col("side") == side))
            )
            st = _stats(sub)
            if sub.height:
                ci = cost_impact(sub)
                st.update(
                    {
                        "gross_mean_R": ci.get("expectancy_R_before_costs"),
                        "cost_drag_R": ci.get("cost_drag_R_per_trade"),
                        "median_hold_h": _f(sub["holding_hours"].median()),
                        "trades_per_month": sub.height / months,
                        "dd_window_pnl": _f(
                            sub.filter(pl.col("trade_id").is_in(list(ddw)))["POSITION_PNL"].sum()
                        ),
                        "pnl_share": st["sum_pnl"] / total_pnl if total_pnl else math.nan,
                    }
                )
            rows.append({"family": fam, "side": side, **st})
    return rows


def regime_table(t: pl.DataFrame) -> list[dict[str, Any]]:
    col = "regime_at_trigger" if "regime_at_trigger" in t.columns else "regime_at_entry"
    rows = {d[col]: d for d in _by(t, col)} if t.height else {}
    return [rows.get(r.value, {col: r.value, "n": 0}) for r in V3Regime]


def derivatives_context(t: pl.DataFrame) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if t.is_empty():
        return out
    r = t["R_MULTIPLE"].to_numpy().astype(float)
    win = t["POSITION_PNL"].to_numpy().astype(float) > 0
    for k in DERIV_KEYS:
        if k not in t.columns:
            continue
        x = t[k].to_numpy().astype(float)
        ok = ~np.isnan(x)
        rho = (
            float(spearmanr(x[ok], r[ok]).statistic)
            if ok.sum() > 10 and np.std(x[ok]) > 0
            else math.nan
        )
        out.append(
            {
                "feature": k[2:],
                "n": int(ok.sum()),
                "mean_winners": float(np.nanmean(x[win])) if win.any() else math.nan,
                "mean_losers": float(np.nanmean(x[~win])) if (~win).any() else math.nan,
                "spearman_vs_net_R": rho,
            }
        )
    return out


def mfe_mae(t: pl.DataFrame) -> dict[str, Any]:
    if t.is_empty():
        return {"n": 0}
    out: dict[str, Any] = {
        "n": t.height,
        "mean_MFE_R": _f(t["MFE_R"].mean()),
        "median_MFE_R": _f(t["MFE_R"].median()),
        "mean_MAE_R": _f(t["MAE_R"].mean()),
        "median_MAE_R": _f(t["MAE_R"].median()),
        "worst_MAE_R": _f(t["MAE_R"].min()),
        "mfe_capture": _f(t["R_MULTIPLE"].mean()) / _f(t["MFE_R"].mean())
        if _f(t["MFE_R"].mean())
        else math.nan,
        "tp1_hit_rate": _f(t["tp1_hit"].cast(pl.Float64).mean()),
        "tp2_hit_rate": _f(t["tp2_hit"].cast(pl.Float64).mean()),
    }
    for c in t.columns:
        if c.startswith("cf_hit_"):
            out[f"hit_rate_{c[7:]}"] = _f(t[c].cast(pl.Float64).mean())
    return out


def exits_table(t: pl.DataFrame) -> list[dict[str, Any]]:
    return _by(t, "exit_reason")


def leverage_audit(t: pl.DataFrame) -> dict[str, Any]:
    if t.is_empty():
        return {"n": 0}
    return {
        "n": t.height,
        "leverage_distribution": t.group_by("leverage").len().sort("leverage").to_dicts(),
        "mean_leverage": _f(t["leverage"].mean()),
        "max_leverage": _f(t["leverage"].max()),
        "mean_notional": _f(t["notional"].mean()),
        "mean_margin_pct_equity": 100 * _f((t["margin"] / t["equity_at_entry"]).mean()),
        "max_margin_pct_equity": 100 * _f((t["margin"] / t["equity_at_entry"]).max()),
        "mean_account_risk_pct": 100 * _f(t["risk_frac"].mean()),
        "max_account_risk_pct": 100 * _f(t["risk_frac"].max()),
        "n_risk_capped": int(t["risk_capped"].sum()),
        "mean_stop_distance_pct": _f(t["stop_distance_pct"].mean()) * 100,
        "min_stop_to_liq_ratio": _f(t["stop_to_liquidation_ratio"].min()),
        "median_stop_to_liq_ratio": _f(t["stop_to_liquidation_ratio"].median()),
        "min_liq_distance_pct": 100 * _f(t["liquidation_distance_pct"].min()),
        "liquidations": int((t["exit_reason"] == "LIQUIDATION").sum()),
        "max_account_loss_if_liquidated_pct": 100 * _f(t["max_account_loss_at_liquidation"].max()),
    }


def outliers(t: pl.DataFrame, chrono: dict[str, Any], fams: list[dict[str, Any]]) -> dict[str, Any]:
    if t.is_empty():
        return {"n": 0}
    r = np.sort(t["R_MULTIPLE"].to_numpy().astype(float))
    pnl = t["POSITION_PNL"].to_numpy().astype(float)
    total = float(pnl.sum())
    gross_profit = float(pnl[pnl > 0].sum())
    q = {k: v.get("sum_pnl", 0.0) for k, v in chrono.get("quarters", {}).items()}
    max_q = max(q.items(), key=lambda kv: kv[1]) if q else ("n/a", 0.0)
    fam_all = [f for f in fams if f["side"] == "ALL" and f.get("n")]
    max_f = max(fam_all, key=lambda d: d.get("sum_pnl", 0.0)) if fam_all else None
    return {
        "mean_R": float(r.mean()),
        "mean_R_without_best3": float(r[:-3].mean()) if len(r) > 3 else math.nan,
        "mean_R_without_best5": float(r[:-5].mean()) if len(r) > 5 else math.nan,
        "mean_R_without_worst5": float(r[5:].mean()) if len(r) > 5 else math.nan,
        "best5_share_of_gross_profit": float(np.sort(pnl)[-5:].sum() / gross_profit)
        if gross_profit > 0 and len(pnl) >= 5
        else math.nan,
        "best_quarter": max_q[0],
        "best_quarter_share_of_pnl": (max_q[1] / total) if total > 0 else math.nan,
        "best_family": max_f["family"] if max_f else None,
        "best_family_share_of_pnl": (max_f.get("sum_pnl", 0.0) / total)
        if (max_f and total > 0)
        else math.nan,
    }


# --------------------------------------------------------------------------- criteria
def evaluate_criteria(
    cfg: V3Config,
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
    exp = _f(overall.get("mean_R"))
    pf = _f(overall.get("profit_factor"))
    years = chrono.get("years", {})
    pos_years = sum(1 for v in years.values() if v.get("n", 0) and _f(v.get("mean_R")) > 0)
    n_years = len(years)
    qs = [v for v in chrono.get("quarters", {}).values() if v.get("n", 0) >= c.min_quarter_trades]
    q_share = float(np.mean([_f(v.get("mean_R")) > 0 for v in qs])) if qs else math.nan
    gross = _f(costs.get("expectancy_R_before_costs"))
    net = _f(costs.get("expectancy_R_net"))
    drag_ok = not math.isnan(gross) and gross > 0 and net >= c.max_cost_drag_share * gross
    dd = abs(_f(account.get("max_drawdown_frac_trade_curve")))
    tpd = _f(freq.get("trades_per_day"))
    med_h = _f(hold.get("quantiles", {}).get(0.5))
    fam_share = _f(outl.get("best_family_share_of_pnl"))
    wo5 = _f(outl.get("mean_R_without_best5"))
    q_share_pnl = _f(outl.get("best_quarter_share_of_pnl"))
    nt = _f((null.get("time") or {}).get("null_mean_R"))
    nr = _f((null.get("regime") or {}).get("null_mean_R"))
    drift_t = _f((null.get("time") or {}).get("null_mean_drift"))
    move = _f((null.get("strategy") or {}).get("mean_signed_btc_move"))
    beats_null = (
        (not math.isnan(nt) and exp > nt)
        and (not math.isnan(nr) and exp > nr)
        and (not math.isnan(drift_t) and not math.isnan(move) and move > drift_t)
    )

    def ok(x: float) -> bool:
        return not math.isnan(x)

    return [
        {
            "id": 1,
            "text": f"net expectancy >= +{c.min_expectancy_r:.2f}R",
            "met": ok(exp) and exp >= c.min_expectancy_r,
            "evidence": f"{exp:+.3f}R (n={overall.get('n', 0)})",
        },
        {
            "id": 2,
            "text": f"profit factor >= {c.min_profit_factor:.2f}",
            "met": ok(pf) and pf >= c.min_profit_factor,
            "evidence": f"PF {pf:.2f}",
        },
        {
            "id": 3,
            "text": f"net expectancy > 0 in >= {c.min_positive_years} of {n_years} years and in >= {c.min_positive_quarter_share:.0%} of quarters with >= {c.min_quarter_trades} trades",
            "met": pos_years >= c.min_positive_years
            and ok(q_share)
            and q_share >= c.min_positive_quarter_share,
            "evidence": f"{pos_years}/{n_years} years positive; {q_share:.0%} of {len(qs)} qualifying quarters",
        },
        {
            "id": 4,
            "text": f"cost drag <= {c.max_cost_drag_share:.0%} of gross expectancy",
            "met": drag_ok,
            "evidence": f"gross {gross:+.3f}R, net {net:+.3f}R",
        },
        {
            "id": 5,
            "text": f"max drawdown (trade curve) <= {c.max_drawdown:.0%}",
            "met": ok(dd) and dd <= c.max_drawdown,
            "evidence": f"{dd:.2%}",
        },
        {
            "id": 6,
            "text": f"frequency {c.min_trades_per_day}-{c.max_trades_per_day} trades/day and median hold {c.median_hold_hours[0]:.0f}-{c.median_hold_hours[1]:.0f} h",
            "met": ok(tpd)
            and c.min_trades_per_day <= tpd <= c.max_trades_per_day
            and ok(med_h)
            and c.median_hold_hours[0] <= med_h <= c.median_hold_hours[1],
            "evidence": f"{tpd:.2f} trades/day; median hold {med_h:.1f} h",
        },
        {
            "id": 7,
            "text": f"no family > {c.max_family_pnl_share:.0%} of net P&L; expectancy without the 5 best trades >= +{c.min_expectancy_without_best5_r:.2f}R; no quarter > {c.max_quarter_pnl_share:.0%} of net P&L",
            "met": (math.isnan(fam_share) or fam_share <= c.max_family_pnl_share)
            and ok(wo5)
            and wo5 >= c.min_expectancy_without_best5_r
            and (math.isnan(q_share_pnl) or q_share_pnl <= c.max_quarter_pnl_share),
            "evidence": f"best family share {fam_share:.0%}; without best 5 {wo5:+.3f}R; best quarter share {q_share_pnl:.0%}",
        },
        {
            "id": 8,
            "text": "beats the time-matched and regime-matched nulls and the matched BTC drift",
            "met": beats_null,
            "evidence": f"strategy {exp:+.3f}R vs null time {nt:+.3f}R / regime {nr:+.3f}R; signed BTC move {move:+.4f} vs drift {drift_t:+.4f}",
        },
    ]


def classify(
    cfg: V3Config,
    criteria: list[dict[str, Any]],
    overall: dict[str, Any],
    chrono: dict[str, Any],
    freq: dict[str, Any],
    outl: dict[str, Any],
) -> str:
    met = {c["id"]: bool(c["met"]) for c in criteria}
    if all(met.values()):
        return "A — STRUCTURAL EDGE DEMONSTRATED, FREEZE FOR FORWARD PAPER TEST"
    c = cfg.research.criteria
    exp, pf = _f(overall.get("mean_R")), _f(overall.get("profit_factor"))
    pos_years = sum(
        1 for v in chrono.get("years", {}).values() if v.get("n", 0) and _f(v.get("mean_R")) > 0
    )
    b = (
        exp > 0
        and pf > c.b_min_profit_factor
        and pos_years >= c.b_min_positive_years
        and _f(freq.get("trades_per_day")) >= c.b_min_trades_per_day
        and _f(outl.get("mean_R_without_best5")) > 0
        and met[8]
    )
    if b:
        return "B — PROMISING STRUCTURAL SIGNAL, CONTROLLED V3.1 RESEARCH JUSTIFIED"
    return "C — NO ROBUST STRUCTURAL EDGE"


__all__ = [
    "account_stats",
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
]
