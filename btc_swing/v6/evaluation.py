"""V6 evaluation: trade statistics net of costs, the REQUIRED frequency report (calendar-day
first), stability folds, time/regime-matched nulls, per-family criteria and the HARD combined
acceptance gate. Every rule applied here was declared in `config/btc_swing_v6.yaml` before the
first result was read."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import polars as pl

from btc_swing.v6.config import V6Config
from btc_swing.v6.features import REGIMES, V6Frame, regime_name

DAY_MS = 86_400_000


def _f(x: object) -> float:
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan
    return v


def _days(start_ms: int, end_ms: int) -> float:
    return (end_ms - start_ms) / DAY_MS


# ----------------------------------------------------------------------------- core stats
def bootstrap_mean(x: np.ndarray, n: int, seed: int) -> tuple[float, float]:
    if len(x) < 2:
        return math.nan, math.nan
    rng = np.random.RandomState(seed)
    idx = rng.randint(0, len(x), size=(n, len(x)))
    means = x[idx].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def drawdown(equity: np.ndarray) -> float:
    if len(equity) == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    return float(np.max(peak - equity))


def trade_stats(
    t: pl.DataFrame, start_ms: int, end_ms: int, cfg: V6Config, seed: int = 7
) -> dict[str, Any]:
    days = _days(start_ms, end_ms)
    if t.is_empty():
        return {"n": 0, "trades_per_day": 0.0, "days": days}
    r = t["net_r"].to_numpy().astype(np.float64)
    g = t["gross_r"].to_numpy().astype(np.float64)
    wins, losses = r[r > 0], r[r < 0]
    pf = float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else math.inf
    lo, hi = bootstrap_mean(r, cfg.research.bootstrap_resamples, seed)
    eq = cfg.risk.initial_equity + np.cumsum(t["net_pnl"].to_numpy())
    dd = drawdown(np.concatenate([[cfg.risk.initial_equity], eq]))
    cost_r = -(t["fee_r"].to_numpy() + t["slip_r"].to_numpy()).astype(np.float64)
    gross_pos = float(np.sum(np.abs(g)))
    out: dict[str, Any] = {
        "n": t.height,
        "days": days,
        "trades_per_day": t.height / days if days > 0 else math.nan,
        "net_r_sum": float(r.sum()),
        "net_r_mean": float(r.mean()),
        "net_r_median": float(np.median(r)),
        "net_r_boot_lo": lo,
        "net_r_boot_hi": hi,
        "gross_r_mean": float(g.mean()),
        "fee_r_mean": _f(t["fee_r"].mean()),
        "slip_r_mean": _f(t["slip_r"].mean()),
        "fund_r_mean": _f(t["fund_r"].mean()),
        "cost_r_mean": float(cost_r.mean()),
        "cost_share_of_gross": float(cost_r.sum() / gross_pos) if gross_pos > 0 else math.nan,
        "win_rate": float(np.mean(r > 0)),
        "profit_factor": pf,
        "avg_win_r": float(wins.mean()) if len(wins) else math.nan,
        "avg_loss_r": float(losses.mean()) if len(losses) else math.nan,
        "max_dd_usdt": dd,
        "max_dd_frac": dd / cfg.risk.initial_equity,
        "net_pnl_sum": _f(t["net_pnl"].sum()),
        "median_hold_min": _f(t["hold_min"].median()),
        "mean_hold_min": _f(t["hold_min"].mean()),
        "median_stop_bps": _f(t["stop_bps"].median()),
        "mean_mfe_r": _f(t["mfe_r"].mean()),
        "mean_mae_r": _f(t["mae_r"].mean()),
        "stop_is_floor_share": _f(t["stop_is_floor"].mean()),
        "best5_share_of_net": float(np.sort(r)[-5:].sum() / r.sum())
        if r.sum() > 0 and len(r) >= 5
        else math.nan,
        "net_r_mean_without_best5": float(np.sort(r)[:-5].mean()) if len(r) > 5 else math.nan,
        "round_trip_cost_bps": 2 * cfg.costs.taker_fee_bps
        + cfg.costs.entry_slippage_bps
        + cfg.costs.stop_slippage_bps,
    }
    out["cost_share_of_median_stop"] = (
        out["round_trip_cost_bps"] / out["median_stop_bps"]
        if out["median_stop_bps"] > 0
        else math.nan
    )
    out["exit_reasons"] = t.group_by("exit_reason").len().sort("len", descending=True).to_dicts()
    return out


def group_table(
    t: pl.DataFrame, by: list[str], cfg: V6Config, start_ms: int, end_ms: int
) -> list[dict[str, Any]]:
    if t.is_empty():
        return []
    out = []
    for key, sub in t.group_by(by, maintain_order=True):
        st = trade_stats(sub, start_ms, end_ms, cfg)
        row = dict(zip(by, key, strict=False))
        row.update(
            {
                k: st.get(k)
                for k in (
                    "n",
                    "trades_per_day",
                    "net_r_sum",
                    "net_r_mean",
                    "profit_factor",
                    "win_rate",
                    "median_hold_min",
                    "median_stop_bps",
                    "cost_share_of_gross",
                    "fee_r_mean",
                )
            }
        )
        out.append(row)
    return sorted(out, key=lambda r_: tuple(str(r_[b]) for b in by))


def with_calendar(t: pl.DataFrame) -> pl.DataFrame:
    if t.is_empty():
        return t
    return t.with_columns(
        (pl.col("entry_ms") // DAY_MS).alias("entry_day"),
        pl.from_epoch(pl.col("entry_ms"), time_unit="ms").dt.year().alias("year"),
        pl.from_epoch(pl.col("entry_ms"), time_unit="ms").dt.weekday().alias("weekday"),
        (pl.from_epoch(pl.col("entry_ms"), time_unit="ms").dt.weekday() >= 6).alias("weekend"),
        (pl.from_epoch(pl.col("entry_ms"), time_unit="ms").dt.hour() // 8).alias("session"),
        pl.col("regime_code").map_elements(regime_name, return_dtype=pl.String).alias("regime"),
    )


# ----------------------------------------------------------------------------- frequency report
def frequency_report(
    t: pl.DataFrame, fr: V6Frame, start_ms: int, end_ms: int, cfg: V6Config
) -> dict[str, Any]:
    """Calendar-day statistics first (every UTC day in the window counts, zero-trade days
    included), then the pre-registered active-day view, inter-entry and holding times."""
    d0, d1 = start_ms // DAY_MS, (end_ms - 1) // DAY_MS
    days = np.arange(d0, d1 + 1)
    per_day = np.zeros(len(days), dtype=np.int64)
    if not t.is_empty():
        ed = (t["entry_ms"].to_numpy() // DAY_MS).astype(np.int64)
        ok = (ed >= d0) & (ed <= d1)
        np.add.at(per_day, ed[ok] - d0, 1)
    tgt = cfg.research.combined_criteria.target_trades_per_day
    buckets = {
        "0": float(np.mean(per_day == 0)),
        "1": float(np.mean(per_day == 1)),
        "2": float(np.mean(per_day == 2)),
        "3-6": float(np.mean((per_day >= 3) & (per_day <= 6))),
        ">6": float(np.mean(per_day > 6)),
    }
    # active-day flag per calendar day from the frame (first row of the day)
    k0 = np.searchsorted(fr.open_ms, days * DAY_MS)
    k0 = np.minimum(k0, len(fr) - 1)
    act = fr.cols["active_day"][k0]
    act_known = ~np.isnan(act)
    active = act_known & (act == 1.0)
    out: dict[str, Any] = {
        "calendar_days": len(days),
        "trades": int(per_day.sum()),
        "mean_trades_per_calendar_day": float(per_day.mean()),
        "median_trades_per_calendar_day": float(np.median(per_day)),
        "pct_days": buckets,
        "max_trades_in_a_day": int(per_day.max()) if len(per_day) else 0,
        "days_with_trades": int((per_day > 0).sum()),
        "active_day_definition": cfg.research.active_day.description,
        "active_days": int(active.sum()),
        "active_days_share": float(active.sum() / max(act_known.sum(), 1)),
        "mean_trades_per_active_day": float(per_day[active].mean()) if active.any() else math.nan,
        "mean_trades_per_quiet_day": float(per_day[act_known & ~active].mean())
        if (act_known & ~active).any()
        else math.nan,
        "target_trades_per_day": tgt,
        "share_active_days_in_target": float(
            np.mean((per_day[active] >= tgt[0]) & (per_day[active] <= tgt[1]))
        )
        if active.any()
        else math.nan,
        "hard_minimum_trades_per_day": cfg.research.combined_criteria.min_trades_per_calendar_day,
        "meets_hard_minimum": bool(
            per_day.mean() >= cfg.research.combined_criteria.min_trades_per_calendar_day
        ),
    }
    if not t.is_empty():
        e = np.sort(t["entry_ms"].to_numpy().astype(np.int64))
        gaps = np.diff(e) / 3_600_000.0
        out["mean_hours_between_entries"] = float(gaps.mean()) if len(gaps) else math.nan
        out["median_hours_between_entries"] = float(np.median(gaps)) if len(gaps) else math.nan
        out["mean_hold_min"] = _f(t["hold_min"].mean())
        out["median_hold_min"] = _f(t["hold_min"].median())
        out["share_hold_gt_4h"] = _f((t["hold_min"] > 240).mean())
        out["share_time_limit_exits"] = _f((t["exit_reason"] == "TIME_LIMIT").mean())
        tc = with_calendar(t)
        out["by_year"] = [
            {
                "year": int(k[0]),
                "trades": n,
                "trades_per_day": n / max(_year_days(int(k[0]), start_ms, end_ms), 1),
            }
            for k, n in sorted(((k, s.height) for k, s in tc.group_by("year")), key=lambda x: x[0])
        ]
        wk_days = sum(1 for d in days if ((d + 3) % 7) < 5)
        we_days = len(days) - wk_days
        n_we = int(tc["weekend"].sum())
        out["weekday_trades_per_day"] = (tc.height - n_we) / max(wk_days, 1)
        out["weekend_trades_per_day"] = n_we / max(we_days, 1)
        out["by_session_share"] = {
            {0: "asia_00-08", 1: "europe_08-16", 2: "us_16-24"}[int(k[0])]: s.height / tc.height
            for k, s in tc.group_by("session")
        }
        out["by_side"] = {str(k[0]): s.height for k, s in tc.group_by("side")}
        out["by_family"] = {str(k[0]): s.height for k, s in tc.group_by("family")}
    return out


def _year_days(year: int, start_ms: int, end_ms: int) -> float:
    import datetime as dt

    y0 = int(dt.datetime(year, 1, 1, tzinfo=dt.UTC).timestamp() * 1000)
    y1 = int(dt.datetime(year + 1, 1, 1, tzinfo=dt.UTC).timestamp() * 1000)
    return max(0.0, (min(y1, end_ms) - max(y0, start_ms)) / DAY_MS)


# ----------------------------------------------------------------------------- folds / nulls
def fold_table(
    t: pl.DataFrame, start_ms: int, end_ms: int, n_folds: int, cfg: V6Config
) -> list[dict[str, Any]]:
    out = []
    step = (end_ms - start_ms) / n_folds
    for i in range(n_folds):
        a, b = int(start_ms + i * step), int(start_ms + (i + 1) * step)
        sub = (
            t.filter((pl.col("entry_ms") >= a) & (pl.col("entry_ms") < b))
            if not t.is_empty()
            else t
        )
        st = trade_stats(sub, a, b, cfg)
        out.append(
            {
                "fold": i + 1,
                "start_ms": a,
                "end_ms": b,
                "n": st["n"],
                "trades_per_day": st["trades_per_day"],
                "net_r_sum": st.get("net_r_sum", 0.0),
                "net_r_mean": st.get("net_r_mean", math.nan),
                "profit_factor": st.get("profit_factor", math.nan),
            }
        )
    return out


def regime_null(
    eng: Any, t: pl.DataFrame, fr: V6Frame, start_ms: int, end_ms: int, k: int, seed: int
) -> dict[str, Any]:
    """Time- and regime-matched null: for every real trade, `k` random bars with the same regime
    code and the same UTC hour inside the window, same side, same stop distance (in ATR(15m)),
    same exits and costs. Reports the real mean net R against the null distribution."""
    if t.is_empty():
        return {"n": 0}
    rng = np.random.RandomState(seed)
    i0, i1 = fr.idx_at(start_ms) + 1, fr.idx_at(end_ms - 1)
    codes = fr.regime_code[i0:i1]
    hours = fr.cols["utc_hour"][i0:i1]
    atr15 = fr.cols["atr15m"]
    pools: dict[tuple[int, int], np.ndarray] = {}
    sims: list[float] = []
    per_trade_means: list[float] = []
    from btc_swing.core.enums import Side
    from btc_swing.v6.families import Episode, Trigger

    for row in t.iter_rows(named=True):
        key = (
            int(row["regime_code"]),
            int((row["entry_ms"] % DAY_MS) // 3_600_000),
        )
        if key not in pools:
            pools[key] = np.flatnonzero((codes == key[0]) & (hours == key[1])) + i0
        pool = pools[key]
        pool = pool[(pool > i0) & (pool < i1 - 2)]
        if len(pool) == 0:
            continue
        side = Side(row["side"])
        s = side.sign
        vals = []
        for j in rng.choice(pool, size=k, replace=True):
            a = float(atr15[j])
            if math.isnan(a) or math.isnan(float(fr.cols["close"][j])):
                continue
            ref = float(fr.cols["close"][j])
            stop = ref - s * float(row["stop_atr15"]) * a
            ep = Episode(-1, eng.families[0], side, int(j), int(fr.close_ms[j]), 0.0, {})
            pos = eng._simulate(
                ep, Trigger(ref, stop, "NULL"), int(j), -1, eng.cfg.risk.initial_equity
            )
            if pos is not None:
                vals.append(pos.net_pnl() / max(pos.sizing.risk_amount, 1e-9))
        if vals:
            sims.extend(vals)
            per_trade_means.append(float(np.mean(vals)))
    if not sims:
        return {"n": 0}
    real = _f(t["net_r"].mean())
    arr = np.asarray(sims)
    return {
        "n_real": t.height,
        "n_null": len(arr),
        "real_mean_net_r": real,
        "null_mean_net_r": float(arr.mean()),
        "null_sd_of_mean": float(arr.std() / math.sqrt(max(t.height, 1))),
        "edge_vs_null_r": real - float(arr.mean()),
        "z_vs_null": (real - float(arr.mean())) / (arr.std() / math.sqrt(max(t.height, 1)))
        if arr.std() > 0
        else math.nan,
        "null_share_positive": float(np.mean(arr > 0)),
    }


# ----------------------------------------------------------------------------- criteria
def family_criteria(
    stats_val: dict[str, Any], folds_dev: list[dict[str, Any]], cfg: V6Config
) -> dict[str, Any]:
    c = cfg.research.family_criteria
    pos_folds = sum(1 for f in folds_dev if f["n"] > 0 and f["net_r_sum"] > 0)
    rows = [
        (
            f"net expectancy (validation, net of costs) > {c.min_net_expectancy_r:.2f}R",
            stats_val.get("net_r_mean", math.nan),
            stats_val.get("net_r_mean", -1) > c.min_net_expectancy_r,
        ),
        (
            f"bootstrap 95% lower bound > {c.min_bootstrap_lower_r:.2f}R",
            stats_val.get("net_r_boot_lo", math.nan),
            stats_val.get("net_r_boot_lo", -1) > c.min_bootstrap_lower_r,
        ),
        (
            f"profit factor > {c.min_profit_factor:.2f}",
            stats_val.get("profit_factor", math.nan),
            stats_val.get("profit_factor", 0) > c.min_profit_factor,
        ),
        (
            f"trades/day >= {c.min_trades_per_day:.2f}",
            stats_val.get("trades_per_day", 0.0),
            stats_val.get("trades_per_day", 0.0) >= c.min_trades_per_day,
        ),
        (
            "cost share of gross < %.0f%%" % (100 * c.max_cost_share_of_gross),
            stats_val.get("cost_share_of_gross", math.nan),
            (stats_val.get("cost_share_of_gross") or 9) < c.max_cost_share_of_gross,
        ),
        (
            f"positive in >= {c.min_positive_folds} of {c.folds} development folds",
            pos_folds,
            pos_folds >= c.min_positive_folds,
        ),
    ]
    return {
        "rows": [{"criterion": a, "value": b, "passed": bool(p)} for a, b, p in rows],
        "passed": all(p for _, _, p in rows),
    }


def combined_gate(
    stats: dict[str, Any],
    freq: dict[str, Any],
    by_year: list[dict[str, Any]],
    by_regime: list[dict[str, Any]],
    cfg: V6Config,
) -> dict[str, Any]:
    c = cfg.research.combined_criteria
    tot = stats.get("net_r_sum", 0.0)

    def share(rows: list[dict[str, Any]]) -> float:
        if tot <= 0 or not rows:
            return math.nan
        return max(float(r["net_r_sum"]) for r in rows) / tot

    hard = [
        (
            f"HARD: mean trades per calendar day >= {c.min_trades_per_calendar_day:.1f}",
            freq["mean_trades_per_calendar_day"],
            freq["mean_trades_per_calendar_day"] >= c.min_trades_per_calendar_day,
        ),
        (
            f"HARD: net expectancy after fees/slippage > {c.min_net_expectancy_r:.2f}R",
            stats.get("net_r_mean", math.nan),
            stats.get("net_r_mean", -1) > c.min_net_expectancy_r,
        ),
        (
            f"HARD: profit factor after costs > {c.min_profit_factor_hard:.2f}",
            stats.get("profit_factor", math.nan),
            stats.get("profit_factor", 0) > c.min_profit_factor_hard,
        ),
        (
            "HARD: max drawdown < %.0f%% of equity at the primary risk"
            % (100 * c.max_drawdown_frac),
            stats.get("max_dd_frac", math.nan),
            stats.get("max_dd_frac", 1) < c.max_drawdown_frac,
        ),
    ]
    robust = [
        (
            f"robust: profit factor > {c.min_profit_factor_robust:.2f}",
            stats.get("profit_factor", math.nan),
            stats.get("profit_factor", 0) > c.min_profit_factor_robust,
        ),
        (
            "robust: no single year > %.0f%% of profit" % (100 * c.max_year_profit_share),
            share(by_year),
            not (share(by_year) > c.max_year_profit_share),
        ),
        (
            "robust: no single regime > %.0f%% of profit" % (100 * c.max_regime_profit_share),
            share(by_regime),
            not (share(by_regime) > c.max_regime_profit_share),
        ),
        (
            f"robust: median hold within {c.median_hold_minutes} min",
            stats.get("median_hold_min", math.nan),
            c.median_hold_minutes[0]
            <= (stats.get("median_hold_min") or -1)
            <= c.median_hold_minutes[1],
        ),
        (
            "target: 3-6 trades/day on active days (share of active days in target)",
            freq.get("share_active_days_in_target", math.nan),
            (freq.get("share_active_days_in_target") or 0) >= 0.5,
        ),
    ]
    rows = [{"criterion": a, "value": b, "passed": bool(p)} for a, b, p in hard + robust]
    hard_ok = all(p for _, _, p in hard)
    robust_ok = all(p for _, _, p in robust)
    cls = "A" if hard_ok and robust_ok else ("B" if hard_ok else "C")
    return {"rows": rows, "hard_passed": hard_ok, "robust_passed": robust_ok, "classification": cls}


def regime_rows(t: pl.DataFrame, cfg: V6Config, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    if t.is_empty():
        return []
    tc = with_calendar(t)
    rows = group_table(tc, ["regime"], cfg, start_ms, end_ms)
    order = {r: i for i, r in enumerate(REGIMES)}
    return sorted(rows, key=lambda r: order.get(str(r["regime"]), 99))
