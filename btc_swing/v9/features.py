"""V9 feature frame (design section 2). 1m rows on a day-aligned grid; 5m / 15m / 1h bars from
completed 1m bars; every value at a bar uses only that bar and earlier bars."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray

from btc_swing.v6.features import (
    _agg,
    _series,
    ema,
    mask,
    rolling_max_prev,
    rolling_min_prev,
    wilder_atr,
    window_valid,
)
from btc_swing.v9.config import V9Config

F = NDArray[np.float64]
I = NDArray[np.int64]  # noqa: E741
B = NDArray[np.bool_]
MS_1M = 60_000
DAY_MS = 86_400_000
SLOTS_1M = 1440
SLOTS_5M = 288


@dataclass
class V9Frame:
    t_open: I  # 1m grid
    valid: B
    m: dict[str, F] = field(default_factory=dict)  # 1m columns
    b5: dict[str, F] = field(default_factory=dict)  # 5m columns (n // 5)
    meta: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.t_open)

    @property
    def n5(self) -> int:
        return len(self.t_open) // 5


def _grid(rows: pl.DataFrame) -> tuple[I, pl.DataFrame]:
    b = rows.sort("open_time_ms")
    t0 = (int(b["open_time_ms"][0]) // DAY_MS) * DAY_MS
    t1 = ((int(b["open_time_ms"][-1]) // DAY_MS) + 1) * DAY_MS
    grid = np.arange(t0, t1, MS_1M, dtype=np.int64)
    return grid, pl.DataFrame({"open_time_ms": grid}).join(b, on="open_time_ms", how="left")


def _rolling_mean_prev(x: F, window: int, min_periods: int) -> F:
    s = _series(x).shift(1)
    return np.asarray(s.rolling_mean(window, min_samples=min_periods).to_numpy(), dtype=np.float64)


def _rolling_median_prev(x: F, window: int, min_periods: int) -> F:
    s = _series(x).shift(1)
    return np.asarray(
        s.rolling_median(window, min_samples=min_periods).to_numpy(), dtype=np.float64
    )


def slot_median_prev_days(x: F, slots: int, window_days: int, min_days: int) -> F:
    """Same-slot median over the previous `window_days` days (NaN ignored; >= min_days values)."""
    n_days = len(x) // slots
    m = x.reshape(n_days, slots)
    out = np.full_like(m, np.nan)
    if n_days > window_days:
        w = np.lib.stride_tricks.sliding_window_view(m, window_days, axis=0)  # (D-w+1, S, w)
        cnt = (~np.isnan(w)).sum(axis=2)
        with np.errstate(all="ignore"):
            med = np.nanmedian(w, axis=2)
        med = np.where(cnt >= min_days, med, np.nan)
        out[window_days:] = med[:-1]
    return out.reshape(-1)


def _bars(x: F, m: int, how: str) -> F:
    nb = len(x) // m
    return _agg(x[: nb * m], m, how)


def build_frame_v9(rows: pl.DataFrame, cfg: V9Config) -> V9Frame:
    grid, g = _grid(rows)
    n = len(grid)
    n_days = n // SLOTS_1M

    def col(name: str) -> F:
        return g[name].cast(pl.Float64).to_numpy().astype(np.float64)

    o, h, lo, c, vol = col("open"), col("high"), col("low"), col("close"), col("volume")
    tc = col("n_trades")
    valid = np.asarray(~np.isnan(c) & ~np.isnan(h) & ~np.isnan(lo) & (tc > 0), dtype=bool)
    nanv = np.where(valid, 1.0, np.nan)
    o, h, lo, c, vol = o * nanv, h * nanv, lo * nanv, c * nanv, vol * nanv
    buy, sell = col("buy_qty") * nanv, col("sell_qty") * nanv
    tr = cfg.trigger
    m: dict[str, F] = {"open": o, "high": h, "low": lo, "close": c, "volume": vol}
    with np.errstate(invalid="ignore", divide="ignore"):
        m["rvol1"] = vol / _rolling_mean_prev(vol, tr.rvol_baseline_min, tr.rvol_baseline_min)
        m["accel1"] = vol / _rolling_mean_prev(vol, tr.accel_baseline_min, tr.accel_baseline_min)
        tot = buy + sell
        m["imb1"] = np.where(tot > 0, (buy - sell) / tot, np.nan)
        m["delta1"] = buy - sell
        m["delta_med60"] = _rolling_median_prev(
            np.abs(m["delta1"]), tr.delta_median_window_min, tr.delta_median_window_min // 2
        )
    okw = window_valid(valid, tr.lookback_min + 1)
    hi_prev = mask(rolling_max_prev(h, tr.lookback_min), okw)
    lo_prev = mask(rolling_min_prev(lo, tr.lookback_min), okw)
    m["brk_up"] = (c > hi_prev).astype(np.float64)
    m["brk_dn"] = (c < lo_prev).astype(np.float64)
    # ---- 5m bars (day-aligned grid)
    v5 = np.asarray(_bars(valid.astype(np.float64), 5, "min") == 1.0)
    nan5 = np.where(v5, 1.0, np.nan)
    h5, l5, c5 = _bars(h, 5, "max") * nan5, _bars(lo, 5, "min") * nan5, _bars(c, 5, "last") * nan5
    o5, vol5 = _bars(o, 5, "first") * nan5, _bars(vol, 5, "sum") * nan5
    b5: dict[str, F] = {"open": o5, "high": h5, "low": l5, "close": c5, "volume": vol5}
    b5["valid"] = v5.astype(np.float64)
    b5["atr5"] = wilder_atr(h5, l5, c5, v5, cfg.impulse.atr_period)
    with np.errstate(invalid="ignore", divide="ignore"):
        base = slot_median_prev_days(
            vol5, SLOTS_5M, cfg.impulse.rvol_baseline_days, cfg.impulse.rvol_min_days
        )
        b5["rvol5"] = np.where(base > 0, vol5 / base, np.nan)
    n5 = len(h5)
    # ---- 15m trend (last completed 15m bar at each 5m bar)
    v15 = np.asarray(_bars(v5.astype(np.float64), 3, "min") == 1.0)
    c15 = _bars(c5, 3, "last")
    c15 = np.where(v15, c15, np.nan)
    ef, es = ema(c15, cfg.trend.ema_fast), ema(c15, cfg.trend.ema_slow)
    tr15 = np.zeros(len(c15))
    tr15[(ef > es) & (c15 > ef)] = 1.0
    tr15[(ef < es) & (c15 < ef)] = -1.0
    tr15[np.isnan(ef) | np.isnan(es) | np.isnan(c15)] = np.nan
    j = (np.arange(n5) + 1) // 3 - 1
    b5["trend"] = np.where(j >= 0, tr15[np.maximum(j, 0)], np.nan)
    b5["ema_fast_15m"] = np.where(j >= 0, ef[np.maximum(j, 0)], np.nan)
    b5["ema_slow_15m"] = np.where(j >= 0, es[np.maximum(j, 0)], np.nan)
    # ---- 1h ATR for the sizing ladder's liquidation check
    v60 = np.asarray(_bars(v5.astype(np.float64), 12, "min") == 1.0)
    h60, l60, c60 = _bars(h5, 12, "max"), _bars(l5, 12, "min"), _bars(c5, 12, "last")
    atr1h = wilder_atr(h60, l60, c60, v60, cfg.impulse.atr_period)
    k = (np.arange(n5) + 1) // 12 - 1
    b5["atr1h"] = np.where(k >= 0, atr1h[np.maximum(k, 0)], np.nan)
    fr = V9Frame(grid, valid, m, b5)
    fr.meta = {
        "rows_1m": n,
        "days": n_days,
        "valid_rows_1m": int(valid.sum()),
        "bars_5m": n5,
        "valid_bars_5m": int(v5.sum()),
        "feature_version": "v9-feat-1",
        "first_open_ms": int(grid[0]),
        "last_close_ms": int(grid[-1]) + MS_1M,
    }
    return fr
