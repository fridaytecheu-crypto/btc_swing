"""V7 1-minute microstructure feature frame (`v7-feat-1`, design section 3). Every value at row k uses
only rows <= k (completed 1m bar), completed 5m/15m/1h/4h bars, rolling baselines over PREVIOUS rows
and PIT large-trade thresholds from the ingest. Missing minutes are invalid observations."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray

from btc_swing.v6.features import (
    _agg,
    _series,
    ema,
    lagged,
    mask,
    rolling_max_prev,
    rolling_min_prev,
    rolling_sum,
    rolling_z,
    swing_points,
    wilder_atr,
    window_valid,
)
from btc_swing.v7.config import V7Config

F = NDArray[np.float64]
I = NDArray[np.int64]  # noqa: E741
MS_1M = 60_000
DAY_MS = 86_400_000
SLOTS = 1440


@dataclass
class V7Frame:
    open_ms: I
    close_ms: I
    valid: NDArray[np.bool_]
    cols: dict[str, F] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.close_ms)

    def idx_at(self, t_ms: int) -> int:
        return int(np.searchsorted(self.close_ms, t_ms, side="right")) - 1


def _rolling_quantile_prev(x: F, q: float, window: int, min_periods: int) -> F:
    s = _series(x).shift(1)
    return np.asarray(
        s.rolling_quantile(q, window_size=window, min_samples=min_periods).to_numpy(),
        dtype=np.float64,
    )


def _rolling_mean_prev(x: F, window: int, min_periods: int) -> F:
    s = _series(x).shift(1)
    return np.asarray(s.rolling_mean(window, min_samples=min_periods).to_numpy(), dtype=np.float64)


def _rolling_max_incl(x: F, w: int) -> F:
    return np.asarray(_series(x).rolling_max(w, min_samples=w).to_numpy(), dtype=np.float64)


def _rolling_min_incl(x: F, w: int) -> F:
    return np.asarray(_series(x).rolling_min(w, min_samples=w).to_numpy(), dtype=np.float64)


def _grid(rows: pl.DataFrame) -> tuple[I, pl.DataFrame]:
    b = rows.sort("open_time_ms")
    t0 = (int(b["open_time_ms"][0]) // DAY_MS) * DAY_MS
    t1 = ((int(b["open_time_ms"][-1]) // DAY_MS) + 1) * DAY_MS
    grid = np.arange(t0, t1, MS_1M, dtype=np.int64)
    return grid, pl.DataFrame({"open_time_ms": grid}).join(b, on="open_time_ms", how="left")


def _tercile(x: F, hist: int, min_periods: int) -> F:
    """0/1/2 = below the previous-`hist` 33rd percentile / between / above the 67th."""
    q33 = _rolling_quantile_prev(x, 1 / 3, hist, min_periods)
    q67 = _rolling_quantile_prev(x, 2 / 3, hist, min_periods)
    out = np.full(len(x), np.nan)
    ok = ~np.isnan(x) & ~np.isnan(q33) & ~np.isnan(q67)
    out[ok] = 1.0
    out[ok & (x <= q33)] = 0.0
    out[ok & (x >= q67)] = 2.0
    return out


def build_frame_v7(rows: pl.DataFrame, cfg: V7Config) -> V7Frame:
    fc = cfg.features
    grid, g = _grid(rows)
    n = len(grid)
    n_days = n // SLOTS
    t_open, t = grid, grid + MS_1M

    def col(name: str) -> F:
        return g[name].cast(pl.Float64).to_numpy().astype(np.float64)

    o, h, lo, c, vol = col("open"), col("high"), col("low"), col("close"), col("volume")
    tc = col("n_trades")
    valid = np.asarray(~np.isnan(c) & ~np.isnan(h) & ~np.isnan(lo) & (tc > 0), dtype=bool)
    nanv = np.where(valid, 1.0, np.nan)
    o, h, lo, c, vol, tc = o * nanv, h * nanv, lo * nanv, c * nanv, vol * nanv, tc * nanv
    cols: dict[str, F] = {"open": o, "high": h, "low": lo, "close": c, "volume": vol, "tc": tc}
    buy, sell = col("buy_qty") * nanv, col("sell_qty") * nanv
    # ---- intensity
    with np.errstate(invalid="ignore", divide="ignore"):
        ltc = np.log(np.where(tc > 0, tc, np.nan))
        cols["tc_z"] = rolling_z(ltc, fc.z_window_min, fc.z_min_periods)
        cols["tc_accel"] = tc - _rolling_mean_prev(tc, 5, 5)
        cols["burst_5"] = rolling_sum(tc, fc.burst_short_min) / (
            fc.burst_short_min * _rolling_mean_prev(tc, fc.burst_long_min, fc.burst_long_min)
        )
    # ---- imbalance / delta
    delta = buy - sell
    tot = buy + sell
    with np.errstate(invalid="ignore", divide="ignore"):
        cols["delta_1"] = delta
        cols["imb_1"] = np.where(tot > 0, delta / tot, np.nan)
        for w in (5, 15):
            d, s_ = rolling_sum(delta, w), rolling_sum(tot, w)
            cols[f"delta_{w}"] = d
            cols[f"imb_{w}"] = np.where(s_ > 0, d / s_, np.nan)
        cols["delta_accel"] = cols["delta_5"] - lagged(cols["delta_5"], 5)
        abs_accel = np.abs(cols["delta_accel"])
        cols["delta_accel_p90"] = _rolling_quantile_prev(
            abs_accel, 0.9, fc.z_window_min, fc.z_min_periods
        )
        # large trades (PIT thresholds from the ingest)
        for k in fc.large_trade_keys:
            lb = rolling_sum(col(f"large_buy_{k}") * nanv, 5)
            ls = rolling_sum(col(f"large_sell_{k}") * nanv, 5)
            cols[f"lt_imb_{k}_5"] = np.where(lb + ls > 0, (lb - ls) / (lb + ls), np.nan)
            cols[f"lt_share_{k}_5"] = np.where(
                rolling_sum(vol, 5) > 0, (lb + ls) / rolling_sum(vol, 5), np.nan
            )
        cols["lt_imb_5"] = cols[f"lt_imb_{fc.large_trade_anchor}_5"]
    # ---- volatility
    cols["atr1m"] = wilder_atr(h, lo, c, valid, fc.atr_period)
    lr = np.concatenate([[np.nan], np.diff(np.log(c))])
    lr2 = lr * lr
    for w, name in ((15, "rv_15"), (60, "rv_60")):
        m1, m2 = rolling_sum(lr, w) / w, rolling_sum(lr2, w) / w
        cols[name] = np.sqrt(np.maximum(m2 - m1 * m1, 0.0)) * math.sqrt(1440.0)
    cols["rv_tercile"] = _tercile(
        cols["rv_60"], fc.rv_tercile_history_min, fc.rv_tercile_history_min // 3
    )
    rng30 = _rolling_max_incl(h, fc.compression_range_min) - _rolling_min_incl(
        lo, fc.compression_range_min
    )
    cols["range_30"] = rng30
    for pct in (0.15, 0.20, 0.30):
        cols[f"comp_q{int(pct * 100)}"] = _rolling_quantile_prev(
            rng30, pct, fc.compression_history_min, fc.compression_history_min // 3
        )
    with np.errstate(invalid="ignore", divide="ignore"):
        cols["vol_expansion"] = ((_rolling_max_incl(h, 5) - _rolling_min_incl(lo, 5)) / 5.0) / (
            _rolling_mean_prev(h - lo, 60, 60)
        )
        cols["vol_exp_5"] = rolling_sum(vol, 5) / (5.0 * _rolling_mean_prev(vol, 60, 60))
    # ---- completed higher-timeframe bars (grid is day-aligned)
    for m, tag in ((5, "5m"), (15, "15m"), (60, "1h"), (240, "4h")):
        nb = n // m
        hh, ll, cc = (
            _agg(h[: nb * m], m, "max"),
            _agg(lo[: nb * m], m, "min"),
            _agg(c[: nb * m], m, "last"),
        )
        vb = np.asarray(_agg(valid[: nb * m].astype(np.float64), m, "min") == 1.0)
        j = (np.arange(n) + 1) // m - 1
        okj = j >= 0
        jj = np.maximum(j, 0)

        def at(arr: F, okj: NDArray[np.bool_] = okj, jj: I = jj) -> F:
            return np.where(okj, arr[jj], np.nan)

        if tag == "5m":
            cols["atr5m"] = at(wilder_atr(hh, ll, cc, vb, fc.atr_period))
            sh, sl = swing_points(hh, ll, fc.swing_k)
            cols["swing_high_5m"], cols["swing_low_5m"] = at(sh), at(sl)
            cols["close_5m"] = at(cc)
        if tag in ("15m", "1h"):
            ef = ema(np.where(vb, cc, np.nan), fc.ema_fast)
            es = ema(np.where(vb, cc, np.nan), fc.ema_slow)
            tr = np.zeros(nb)
            tr[(ef > es) & (cc > es)] = 1.0
            tr[(ef < es) & (cc < es)] = -1.0
            tr[np.isnan(ef) | np.isnan(es) | np.isnan(cc)] = np.nan
            if tag == "15m":
                cols["trend_15m"] = at(tr)
            else:
                slope = ef - lagged(ef, fc.trend_slope_bars_1h)
                reg = np.zeros(nb)
                reg[(tr == 1.0) & (slope > 0)] = 1.0
                reg[(tr == -1.0) & (slope < 0)] = -1.0
                reg[np.isnan(tr) | np.isnan(slope)] = np.nan
                cols["regime_1h"] = at(reg)
    # ---- price response to delta (ATR units per unit of typical signed flow)
    with np.errstate(invalid="ignore", divide="ignore"):
        ret5 = c / lagged(c, 5) - 1.0
        cols["ret_5"] = ret5
        cols["ret_15"] = c / lagged(c, 15) - 1.0
        ret5_atr = ret5 * c / cols["atr1m"]
        med_abs_d5 = _rolling_quantile_prev(
            np.abs(cols["delta_5"]), 0.5, fc.response_quantile_window_min, fc.z_min_periods
        )
        d5 = cols["delta_5"]
        impact = ret5_atr * med_abs_d5 / d5
        cols["impact_5"] = impact
        abs_d5 = np.abs(d5)
        p_strong = _rolling_quantile_prev(
            abs_d5, fc.strong_flow_pct, fc.response_quantile_window_min, fc.z_min_periods
        )
        strong_flow = abs_d5 >= p_strong
        abs_imp = np.where(
            strong_flow, np.abs(impact), np.nan
        )  # response measured on strong-flow minutes only
        p_weak = _rolling_quantile_prev(
            abs_imp, fc.weak_response_pct, fc.response_quantile_window_min, fc.z_min_periods // 4
        )
        p_str = _rolling_quantile_prev(
            abs_imp, fc.strong_response_pct, fc.response_quantile_window_min, fc.z_min_periods // 4
        )
        cols["strong_flow"] = strong_flow.astype(float)
        cols["resp_abs"] = np.abs(impact)
        cols["resp_weak_thr"], cols["resp_strong_thr"] = p_weak, p_str
        agree = np.sign(ret5) == np.sign(d5)
        cols["absorption"] = (strong_flow & (np.abs(impact) <= p_weak)).astype(float)
        cols["efficient"] = (strong_flow & (np.abs(impact) >= p_str) & agree).astype(float)
        cols["resp_z"] = rolling_z(
            np.log(np.where(abs_imp > 0, abs_imp, np.nan)),
            fc.response_quantile_window_min,
            fc.z_min_periods // 4,
        )
    # ---- structure
    w = fc.range_window_min
    okw = window_valid(valid, w + 1)
    cols["range_hi_60"] = mask(rolling_max_prev(h, w), okw)
    cols["range_lo_60"] = mask(rolling_min_prev(lo, w), okw)
    cols["break_up"] = (c > cols["range_hi_60"]).astype(float)
    cols["break_dn"] = (c < cols["range_lo_60"]).astype(float)
    bu5 = rolling_sum(lagged(cols["break_up"], 1), 5)  # breaks in the previous 5 minutes
    bd5 = rolling_sum(lagged(cols["break_dn"], 1), 5)
    lvl_up = lagged(cols["range_hi_60"], 1)
    lvl_dn = lagged(cols["range_lo_60"], 1)
    cols["failed_break_up"] = ((bu5 > 0) & (c < lvl_up) & (cols["break_up"] == 0)).astype(float)
    cols["failed_break_dn"] = ((bd5 > 0) & (c > lvl_dn) & (cols["break_dn"] == 0)).astype(float)
    cols["hi_240"] = mask(rolling_max_prev(h, 240), window_valid(valid, 241))
    cols["lo_240"] = mask(rolling_min_prev(lo, 240), window_valid(valid, 241))
    with np.errstate(invalid="ignore", divide="ignore"):
        rng4 = cols["hi_240"] - cols["lo_240"]
        cols["range_pos_4h"] = np.where(rng4 > 0, (c - cols["lo_240"]) / rng4, np.nan)
        iw = fc.impulse_window_min
        hi_i = _rolling_max_incl(h, iw)
        lo_i = _rolling_min_incl(lo, iw)
        # which came last: the high or the low (direction of the impulse)
        hv = np.lib.stride_tricks.sliding_window_view(np.where(np.isnan(h), -np.inf, h), iw)
        lv = np.lib.stride_tricks.sliding_window_view(np.where(np.isnan(lo), np.inf, lo), iw)
        arg_h = np.full(n, -1)
        arg_l = np.full(n, -1)
        arg_h[iw - 1 :] = np.argmax(hv, axis=1)
        arg_l[iw - 1 :] = np.argmin(lv, axis=1)
        up = arg_h > arg_l
        size = hi_i - lo_i
        cols["impulse_dir"] = np.where(size > 0, np.where(up, 1.0, -1.0), np.nan)
        cols["impulse_atr"] = size / cols["atr5m"]
        cols["retrace"] = np.where(
            size > 0, np.where(up, (hi_i - c) / size, (c - lo_i) / size), np.nan
        )
        cols["impulse_hi"], cols["impulse_lo"] = hi_i, lo_i
        cols["impulse_15"] = (c - lagged(c, 15)) / cols["atr5m"]
        d_sh = np.abs(c - cols["swing_high_5m"]) / cols["atr5m"]
        d_sl = np.abs(c - cols["swing_low_5m"]) / cols["atr5m"]
        cols["dist_struct"] = np.fmin(d_sh, d_sl)
    # prior UTC day extremes
    full_hi = np.nanmax(np.where(valid, h, np.nan).reshape(n_days, SLOTS), axis=1)
    full_lo = np.nanmin(np.where(valid, lo, np.nan).reshape(n_days, SLOTS), axis=1)
    cols["prev_day_hi"] = np.repeat(np.concatenate([[np.nan], full_hi[:-1]]), SLOTS)
    cols["prev_day_lo"] = np.repeat(np.concatenate([[np.nan], full_lo[:-1]]), SLOTS)
    # ---- calendar / matching keys
    cols["hour"] = ((t_open % DAY_MS) // 3_600_000).astype(np.float64)
    cols["day"] = (t_open // DAY_MS).astype(np.float64)
    cols["weekday"] = (((t_open // DAY_MS) + 3) % 7).astype(np.float64)
    cols["ret15_tercile"] = _tercile(
        cols["ret_15"], fc.rv_tercile_history_min, fc.rv_tercile_history_min // 3
    )
    rp = cols["range_pos_4h"]
    cols["struct_tercile"] = np.where(
        np.isnan(rp), np.nan, np.where(rp <= 1 / 3, 0.0, np.where(rp >= 2 / 3, 2.0, 1.0))
    )
    fr = V7Frame(t_open, t, valid, cols)
    fr.meta = {
        "rows": n,
        "days": n_days,
        "valid_rows": int(valid.sum()),
        "feature_version": "v7-feat-1",
        "first_open_ms": int(t_open[0]),
        "last_close_ms": int(t[-1]),
    }
    return fr
