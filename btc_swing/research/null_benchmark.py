"""Null benchmark: random entries with the SAME geometry, costs and exit rules as each real trade.

For every real trade we draw K random entry bars from the same study window (time-matched) and,
separately, from bars whose regime equals the trade's regime at entry (regime-matched). Each
random entry copies the trade's side, stop distance (as % of price), setup-timeframe ATR (for the
trail) and is sized with the frozen risk rule, then runs through the identical exit engine
(stop-first, TP1/TP2, breakeven, structural trail, time cap, fees, slippage, funding). The null
answers: does the setup's timing add information beyond being long/short BTC with this risk
geometry? A second, cost-free null compares raw signed BTC returns over the matched holding time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import polars as pl

from btc_swing.backtest.engine import BacktestEngine
from btc_swing.backtest.ledger import Position
from btc_swing.core.enums import ExitReason, Regime, SetupFamily, Side
from btc_swing.execution.costs import FundingSchedule
from btc_swing.risk.sizing import liquidation_price, size_position


@dataclass
class NullResult:
    summary: dict[str, Any]
    samples: pl.DataFrame


def simulate_entry(
    eng: BacktestEngine, bar: int, side: Side, stop_pct: float, atr_setup: float, equity: float
) -> Position | None:
    """Enter at open[bar] (decision at close[bar-1]) and run the engine's exit logic."""
    cfg = eng.cfg
    base = eng.series.base
    n = len(base)
    if bar <= 0 or bar >= n:
        return None
    ref = float(base.close[bar - 1])
    stop_ref = ref * (1.0 - side.sign * stop_pct)
    view = eng.series.view_at(int(base.close_ms[bar - 1]))
    atr_liq = (
        view.ind(cfg.risk.liquidation_atr_tf, "atr")
        if view.warm(cfg.risk.liquidation_atr_tf)
        else math.nan
    )
    sz = size_position(equity, ref, stop_ref, side, atr_liq, cfg.risk)
    if not sz.accepted:
        return None
    o = float(base.open[bar])
    fill = eng.costs.entry_fill(o, side)
    stop = fill * (1.0 - side.sign * stop_pct)
    dist = side.sign * (fill - stop)
    pos = Position(
        trade_id=-1,
        episode_id=-1,
        family=SetupFamily.TREND_PULLBACK_LONG
        if side is Side.LONG
        else SetupFamily.TREND_PULLBACK_SHORT,
        side=side,
        regime_at_entry=Regime.UNCLEAR,
        entry_bar=bar,
        entry_ms=int(base.open_ms[bar]),
        entry_price=fill,
        sizing=sz,
        qty_initial=sz.qty,
        qty_open=sz.qty,
        stop=stop,
        initial_stop=stop,
        stop_reason="NULL_MATCHED_STOP_PCT",
        stop_distance=dist,
        stop_distance_atr=dist / atr_setup if atr_setup > 0 else math.nan,
        tp1=fill + side.sign * cfg.exits.tp1_r * dist,
        tp2=fill + side.sign * cfg.exits.tp2_r * dist,
        structural_target=None,
        liq_price=liquidation_price(fill, side, sz.leverage, cfg.risk.maintenance_margin_rate),
        equity_at_entry=equity,
        r_levels=list(cfg.exits.evaluate_r_levels),
        entry_ref_price=o,
    )
    pos.entry_fee = eng.costs.fee(sz.qty * fill)
    pos.trail_active = False
    for i in range(bar, n):
        t = int(base.close_ms[i])
        closed = eng._process_bar(
            pos,
            i,
            t,
            float(base.open[i]),
            float(base.high[i]),
            float(base.low[i]),
            float(base.close[i]),
        )
        if pos.is_open:
            prev_t = int(base.close_ms[i - 1])
            for _ft, rate in eng.funding.events_between(prev_t, t):
                pos.funding += FundingSchedule.payment(
                    rate, pos.qty_open, float(base.close[i]), side
                )
        if closed or not pos.is_open:
            return pos
        if pos.pending_stop is not None:
            pos.stop = pos.pending_stop
            pos.stop_moved = True
            pos.pending_stop = None
        eng._update_trail(pos, eng.series.view_at(t), float(base.close[i]))
    if pos.is_open:
        last = n - 1
        eng._exit(
            pos,
            last,
            int(base.close_ms[last]),
            float(base.close[last]),
            pos.qty_open,
            ExitReason.END_OF_DATA,
            stop_like=True,
        )
    return pos


def run_null(
    eng: BacktestEngine,
    trades: pl.DataFrame,
    decisions: pl.DataFrame,
    start_ms: int,
    end_ms: int,
    k: int,
    seed: int,
) -> NullResult:
    base = eng.series.base
    rng = np.random.RandomState(seed)
    lo = int(np.searchsorted(base.close_ms, start_ms, side="left")) + 1
    hi = int(np.searchsorted(base.close_ms, end_ms, side="right")) - 300  # leave room for exits
    if trades.is_empty() or hi <= lo:
        return NullResult({"n_trades": 0}, pl.DataFrame())
    # regime per base bar (from the decision journal, PIT by construction)
    reg_t = decisions["t_ms"].to_numpy().astype(np.int64)
    reg_v = decisions["regime"].to_list()
    bar_of_t = np.searchsorted(base.close_ms, reg_t, side="left")
    regime_bars: dict[str, np.ndarray] = {}
    for r in set(reg_v):
        idx = np.array(
            [b for b, v in zip(bar_of_t, reg_v, strict=True) if v == r and lo <= b < hi],
            dtype=np.int64,
        )
        regime_bars[r] = idx
    samples: list[dict[str, Any]] = []
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
        reg = str(tr["regime_at_entry"])
        for variant in ("time", "regime"):
            pool = (
                regime_bars.get(reg, np.zeros(0, dtype=np.int64)) if variant == "regime" else None
            )
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
                raw = side.sign * (float(base.close[j]) / float(base.open[b]) - 1.0)
                samples.append(
                    {
                        "trade_id": tr["trade_id"],
                        "variant": variant,
                        "rep": rep,
                        "entry_bar": b,
                        "side": side.value,
                        "R_MULTIPLE": row["R_MULTIPLE"],
                        "POSITION_PNL": row["POSITION_PNL"],
                        "ACCOUNT_RETURN": row["ACCOUNT_RETURN"],
                        "BTC_RETURN": row["BTC_RETURN"],
                        "holding_hours": row["holding_hours"],
                        "raw_btc_return_matched_hold": raw,
                        "win": row["POSITION_PNL"] > 0,
                    }
                )
    df = pl.DataFrame(samples) if samples else pl.DataFrame()
    summary = _summarise(df, trades, k)
    return NullResult(summary, df)


def _f(x: Any) -> float:
    try:
        return float(cast(float, x))
    except (TypeError, ValueError):
        return math.nan


def _summarise(df: pl.DataFrame, trades: pl.DataFrame, k: int) -> dict[str, Any]:
    out: dict[str, Any] = {"n_trades": trades.height, "k": k}
    strat_mean_r = _f(trades["R_MULTIPLE"].mean())
    strat_btc = _f(trades["BTC_RETURN"].mean())
    strat_win = _f((trades["POSITION_PNL"] > 0).cast(pl.Float64).mean())
    out["strategy"] = {"mean_R": strat_mean_r, "win_rate": strat_win, "mean_BTC_RETURN": strat_btc}
    if df.is_empty():
        return out
    for variant in ("time", "regime"):
        d = df.filter(pl.col("variant") == variant)
        if d.is_empty():
            continue
        # replicate-level means: one matched null trade per real trade per rep
        reps = (
            d.group_by("rep")
            .agg(
                pl.col("R_MULTIPLE").mean().alias("mean_R"),
                pl.col("win").cast(pl.Float64).mean().alias("win_rate"),
                pl.col("BTC_RETURN").mean().alias("mean_BTC"),
                pl.col("raw_btc_return_matched_hold").mean().alias("mean_raw_btc"),
                pl.col("ACCOUNT_RETURN").sum().alias("sum_account_return"),
            )
            .sort("rep")
        )
        mr = reps["mean_R"].to_numpy().astype(float)
        raw = reps["mean_raw_btc"].to_numpy().astype(float)
        out[variant] = {
            "n_samples": d.height,
            "null_mean_R": float(mr.mean()),
            "null_mean_R_sd_across_reps": float(mr.std(ddof=1)) if len(mr) > 1 else math.nan,
            "null_win_rate": _f(reps["win_rate"].mean()),
            "null_mean_BTC_RETURN": _f(reps["mean_BTC"].mean()),
            "null_mean_raw_btc_matched_hold": float(raw.mean()),
            "null_sum_account_return_mean": _f(reps["sum_account_return"].mean()),
            "frac_reps_with_mean_R_ge_strategy": float((mr >= strat_mean_r).mean()),
            "strategy_minus_null_mean_R": strat_mean_r - float(mr.mean()),
            "z_vs_null_reps": (
                (strat_mean_r - float(mr.mean())) / float(mr.std(ddof=1))
                if len(mr) > 1 and mr.std(ddof=1) > 0
                else math.nan
            ),
            "null_R_quantiles": {
                q: float(np.quantile(d["R_MULTIPLE"].to_numpy(), q))
                for q in (0.05, 0.25, 0.5, 0.75, 0.95)
            },
        }
    return out
