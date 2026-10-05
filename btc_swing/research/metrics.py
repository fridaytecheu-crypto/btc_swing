"""Research metrics for a backtest result. Risk-adjusted expectancy (in R and in account %) is the
primary metric; return on margin is reported but never used as the objective."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import polars as pl

from btc_swing.core.config import ResearchCfg


def _f(x: Any) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return math.nan
    return v


def _summary(t: pl.DataFrame, min_n: int) -> dict[str, Any]:
    n = t.height
    if n == 0:
        return {"n": 0, "reliable": False}
    r = t["R_MULTIPLE"].to_numpy().astype(float)
    acc = t["ACCOUNT_RETURN"].to_numpy().astype(float)
    pnl = t["POSITION_PNL"].to_numpy().astype(float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gross_win, gross_loss = float(wins.sum()), float(-losses.sum())
    out: dict[str, Any] = {
        "n": n,
        "reliable": n >= min_n,
        "win_rate": float((pnl > 0).mean()),
        "avg_win_R": float(r[pnl > 0].mean()) if len(wins) else math.nan,
        "avg_loss_R": float(r[pnl <= 0].mean()) if len(losses) else math.nan,
        "avg_win_account_pct": float(acc[pnl > 0].mean()) if len(wins) else math.nan,
        "avg_loss_account_pct": float(acc[pnl <= 0].mean()) if len(losses) else math.nan,
        "expectancy_R": float(r.mean()),
        "expectancy_account_pct": float(acc.mean()),
        "median_R": float(np.median(r)),
        "std_R": float(r.std(ddof=1)) if n > 1 else math.nan,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else math.inf,
        "sum_BTC_RETURN": _f((t)["BTC_RETURN"].sum()),
        "mean_BTC_RETURN": _f((t)["BTC_RETURN"].mean()),
        "mean_RETURN_ON_MARGIN": _f((t)["RETURN_ON_MARGIN"].mean()),
        "sum_ACCOUNT_RETURN": float(acc.sum()),
        "total_POSITION_PNL": float(pnl.sum()),
        "total_fees": _f((t)["fees"].sum()),
        "total_funding": _f((t)["funding"].sum()),
        "mean_holding_hours": _f((t)["holding_hours"].mean()),
        "median_holding_hours": _f((t)["holding_hours"].median()),
        "mean_MFE_R": _f((t)["MFE_R"].mean()),
        "mean_MAE_R": _f((t)["MAE_R"].mean()),
        "worst_MAE_R": _f((t)["MAE_R"].min()),
        "mean_leverage": _f((t)["leverage"].mean()),
        "max_leverage": _f((t)["leverage"].max()),
        "t_stat_R": float(r.mean() / (r.std(ddof=1) / math.sqrt(n)))
        if n > 1 and r.std(ddof=1) > 0
        else math.nan,
    }
    return out


def _by(t: pl.DataFrame, col: str, min_n: int) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if t.is_empty() or col not in t.columns:
        return out
    for key in sorted(t[col].unique().to_list()):
        out[str(key)] = _summary(t.filter(pl.col(col) == key), min_n)
    return out


def drawdown(equity: np.ndarray) -> tuple[float, float]:
    """(max drawdown as fraction, max drawdown in currency)."""
    if len(equity) == 0:
        return math.nan, math.nan
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    return float(dd.min()), float((equity - peak).min())


def compute_metrics(
    trades: pl.DataFrame,
    episodes: pl.DataFrame,
    daily: pl.DataFrame,
    manifest: dict[str, Any],
    cfg: ResearchCfg,
) -> dict[str, Any]:
    n_days = max(_f(manifest.get("n_days")), 1e-9)
    m: dict[str, Any] = {
        "overall": _summary(trades, cfg.min_cell_n),
        "by_side": _by(trades, "side", cfg.min_cell_n),
        "by_regime": _by(trades, "regime_at_entry", cfg.min_cell_n),
        "by_family": _by(trades, "family", cfg.min_cell_n),
        "by_exit_reason": _by(trades, "exit_reason", cfg.min_cell_n),
        "min_cell_n": cfg.min_cell_n,
    }
    # frequency (naturally produced, no quota)
    n_ep = episodes.height
    n_tr = trades.height
    m["frequency"] = {
        "n_days": n_days,
        "setups_total": n_ep,
        "setups_per_day": n_ep / n_days,
        "entries_total": n_tr,
        "entries_per_day": n_tr / n_days,
        "entries_per_week": 7.0 * n_tr / n_days,
        "setup_to_entry_conversion": (n_tr / n_ep) if n_ep else math.nan,
        "episode_end_reasons": (
            episodes.group_by("end_reason").len().sort("end_reason").to_dicts() if n_ep else []
        ),
    }
    # account curve
    if daily.height:
        eq = daily["equity_mtm"].to_numpy().astype(float)
        rets = np.diff(eq) / eq[:-1]
        dd_frac, dd_cur = drawdown(np.concatenate([[manifest["initial_equity"]], eq]))
        sharpe = (
            float(rets.mean() / rets.std(ddof=1) * math.sqrt(cfg.sharpe_periods_per_year))
            if len(rets) > 2 and rets.std(ddof=1) > 0
            else math.nan
        )
        active = rets[rets != 0]
        m["account"] = {
            "initial_equity": manifest["initial_equity"],
            "final_equity": manifest["final_equity"],
            "total_account_return": manifest["final_equity"] / manifest["initial_equity"] - 1.0,
            "max_drawdown_frac": dd_frac,
            "max_drawdown_currency": dd_cur,
            "sharpe_daily_annualised": sharpe,
            "n_daily_marks": int(daily.height),
            "frac_days_with_exposure": float(active.size) / max(len(rets), 1),
            "sharpe_caveat": "daily marks incl. unrealised P&L; most days flat -> interpret with care",
        }
    else:
        m["account"] = {
            "initial_equity": manifest["initial_equity"],
            "final_equity": manifest["final_equity"],
        }
    # leverage / liquidation risk
    if n_tr:
        m["leverage_risk"] = {
            "min_stop_to_liquidation_ratio": _f((trades)["stop_to_liquidation_ratio"].min()),
            "min_liquidation_distance_pct": _f((trades)["liquidation_distance_pct"].min()),
            "min_liquidation_distance_atr": _f((trades)["liquidation_distance_atr"].min()),
            "worst_MAE_pct": _f((trades)["MAE_PCT"].min()),
            "worst_MAE_R": _f((trades)["MAE_R"].min()),
            "max_account_loss_single_trade": _f((trades)["ACCOUNT_RETURN"].min()),
            "max_planned_account_loss_at_stop": _f((trades)["max_account_loss_at_stop"].max()),
            "max_account_loss_if_liquidated": _f(trades["max_account_loss_at_liquidation"].max()),
            "n_liquidations": int((trades["exit_reason"] == "LIQUIDATION").sum()),
            "n_risk_capped": int(trades["risk_capped"].sum()),
            "leverage_distribution": trades.group_by("leverage").len().sort("leverage").to_dicts(),
        }
        r_cols = [c for c in trades.columns if c.startswith("cf_hit_")]
        m["target_evaluation"] = {
            c.replace("cf_hit_", "hit_rate_"): _f((trades)[c].cast(pl.Float64).mean())
            for c in r_cols
        }
        m["target_evaluation"]["tp1_hit_rate"] = _f((trades)["tp1_hit"].cast(pl.Float64).mean())
        m["target_evaluation"]["tp2_hit_rate"] = _f((trades)["tp2_hit"].cast(pl.Float64).mean())
        st = trades["structural_target_r"].drop_nulls()
        m["target_evaluation"]["structural_target_median_R"] = (
            _f(st.median()) if st.len() else math.nan
        )
        r = trades["R_MULTIPLE"].to_numpy().astype(float)
        edges = [-math.inf, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 3.0, math.inf]
        hist = np.histogram(r, bins=edges)[0]
        m["r_distribution"] = {
            "bins": [f"{edges[i]:g}..{edges[i + 1]:g}" for i in range(len(edges) - 1)],
            "counts": [int(x) for x in hist],
            "quantiles": {q: float(np.quantile(r, q)) for q in (0.05, 0.25, 0.5, 0.75, 0.95)},
        }
        m["holding_time"] = {
            "mean_hours": _f((trades)["holding_hours"].mean()),
            "median_hours": _f((trades)["holding_hours"].median()),
            "p90_hours": _f((trades)["holding_hours"].quantile(0.9)),
            "max_hours": _f((trades)["holding_hours"].max()),
            "frac_under_1h": _f((trades["holding_hours"] < 1).cast(pl.Float64).mean()),
            "frac_1h_to_1d": _f(
                ((trades["holding_hours"] >= 1) & (trades["holding_hours"] < 24))
                .cast(pl.Float64)
                .mean()
            ),
            "frac_1d_to_3d": _f(
                ((trades["holding_hours"] >= 24) & (trades["holding_hours"] < 72))
                .cast(pl.Float64)
                .mean()
            ),
            "frac_over_3d": _f((trades["holding_hours"] >= 72).cast(pl.Float64).mean()),
        }
    return m
