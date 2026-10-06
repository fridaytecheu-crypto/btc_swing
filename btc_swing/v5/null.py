"""V5 null benchmarks: time-matched and regime-matched random entries with each real trade's
side, stop distance, ATR, sizing and the V5 exits/costs, plus the signed BTC drift over the
matched hold (K replications per trade)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import polars as pl

from btc_swing.core.enums import Side
from btc_swing.v5.engine import V5Engine, simulate_entry


def _f(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


def run_v5_null(
    eng: V5Engine,
    trades: pl.DataFrame,
    decisions: pl.DataFrame,
    start_ms: int,
    end_ms: int,
    k: int,
    seed: int,
) -> dict[str, Any]:
    base = eng.series.base
    rng = np.random.RandomState(seed)
    lo = int(np.searchsorted(base.close_ms, start_ms, side="left")) + 1
    hi = int(np.searchsorted(base.close_ms, end_ms, side="right")) - 300
    if trades.is_empty() or hi <= lo:
        return {"n_trades": 0}
    reg_t = decisions["t_ms"].to_numpy().astype(np.int64)
    reg_v = decisions["regime"].to_list()
    bar_of_t = np.searchsorted(base.close_ms, reg_t, side="left")
    regime_bars: dict[str, np.ndarray] = {}
    for r in set(reg_v):
        regime_bars[r] = np.array(
            [b for b, v in zip(bar_of_t, reg_v, strict=True) if v == r and lo <= b < hi],
            dtype=np.int64,
        )
    rows: list[dict[str, Any]] = []
    for tr in trades.iter_rows(named=True):
        side = Side(tr["side"])
        stop_pct = float(tr["stop_distance_pct"])
        atr_setup = (
            float(tr["stop_distance"]) / float(tr["stop_distance_atr"])
            if tr["stop_distance_atr"]
            else 1.0
        )
        equity = float(tr["equity_at_entry"])
        hold_bars = max(1, round(float(tr["holding_hours"]) * 12))
        reg = str(tr["regime_at_trigger"] or tr["regime_at_entry"])
        for variant in ("time", "regime"):
            pool = regime_bars.get(reg) if variant == "regime" else None
            for rep in range(k):
                if variant == "regime":
                    if pool is None or len(pool) == 0:
                        continue
                    b = int(pool[rng.randint(len(pool))])
                else:
                    b = int(rng.randint(lo, hi))
                pos = simulate_entry(eng, b, side, stop_pct, atr_setup, equity)
                if pos is None:
                    continue
                row = pos.to_row()
                j = min(len(base) - 1, b + hold_bars)
                rows.append(
                    {
                        "variant": variant,
                        "rep": rep,
                        "R_MULTIPLE": row["R_MULTIPLE"],
                        "R_GROSS": row["R_MULTIPLE_GROSS"],
                        "win": row["POSITION_PNL"] > 0,
                        "drift": side.sign * (float(base.close[j]) / float(base.open[b]) - 1.0),
                    }
                )
    df = pl.DataFrame(rows) if rows else pl.DataFrame()
    r_real = trades["R_MULTIPLE"].to_numpy().astype(float)
    sgn = np.where(trades["side"].to_numpy() == "LONG", 1.0, -1.0)
    move = sgn * (
        trades["avg_exit_price"].to_numpy().astype(float)
        / trades["entry_price"].to_numpy().astype(float)
        - 1.0
    )
    out: dict[str, Any] = {
        "n_trades": trades.height,
        "k": k,
        "strategy": {
            "mean_R": float(r_real.mean()),
            "mean_R_gross": _f(trades["R_MULTIPLE_GROSS"].mean()),
            "win_rate": _f((trades["POSITION_PNL"] > 0).cast(pl.Float64).mean()),
            "mean_signed_btc_move": float(move.mean()),
        },
    }
    if df.is_empty():
        return out
    for variant in ("time", "regime"):
        d = df.filter(pl.col("variant") == variant)
        if d.is_empty():
            continue
        reps = (
            d.group_by("rep")
            .agg(
                pl.col("R_MULTIPLE").mean().alias("mean_R"),
                pl.col("R_GROSS").mean().alias("mean_R_gross"),
                pl.col("win").cast(pl.Float64).mean().alias("win_rate"),
                pl.col("drift").mean().alias("drift"),
            )
            .sort("rep")
        )
        mr = reps["mean_R"].to_numpy().astype(float)
        out[variant] = {
            "n_samples": d.height,
            "null_mean_R": float(mr.mean()),
            "null_mean_R_gross": _f(reps["mean_R_gross"].mean()),
            "null_sd_across_reps": float(mr.std(ddof=1)) if len(mr) > 1 else math.nan,
            "null_win_rate": _f(reps["win_rate"].mean()),
            "null_mean_drift": _f(reps["drift"].mean()),
            "strategy_minus_null_R": float(r_real.mean() - mr.mean()),
            "z_vs_null_reps": float((r_real.mean() - mr.mean()) / mr.std(ddof=1))
            if len(mr) > 1 and mr.std(ddof=1) > 0
            else math.nan,
            "frac_reps_ge_strategy": float((mr >= r_real.mean()).mean()),
        }
    return out
