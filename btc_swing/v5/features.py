"""V5 PIT feature frame at 5-minute granularity (`v5-feat-1`, docs/BTC_SWING_V5_DESIGN.md §4).

Row k describes the completed 5m bar closing at T_k and uses only rows with time <= T_k: 5m/1H/4H
bars with close_time <= T_k, aggTrades 5m aggregates of the same bar, the last order-book snapshot
with time <= T_k, metrics/funding/premium rows with time + latency <= T_k and the index kline of
the same bar. Rolling z-scores use the previous `z_window_bars` rows EXCLUDING row k with at least
`z_min_periods` rows; the funding z uses the previous `funding_z_window` observations. A row is
fully known at T_k and never changes later.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
import polars as pl
from numpy.typing import NDArray

from btc_swing.core.enums import Timeframe
from btc_swing.features.context import AuxSeries
from btc_swing.features.view import MultiTfSeries, TfSeries
from btc_swing.v5.config import V5Config

F = NDArray[np.float64]
I = NDArray[np.int64]  # noqa: E741
H1, H4, M5 = Timeframe.H1, Timeframe.H4, Timeframe.M5
MS_5M = 300_000
MS_1H = 3_600_000


# ----------------------------------------------------------------------------- rolling helpers
def rolling_z(x: F, window: int, min_periods: int) -> F:
    """z[k] = (x[k] - mean(x[k-window:k])) / std(x[k-window:k]); NaNs ignored; past rows only."""
    s = pl.Series(x.astype(np.float64)).fill_nan(None).shift(1)
    mean = s.rolling_mean(window, min_samples=min_periods).to_numpy().astype(np.float64)
    std = s.rolling_std(window, min_samples=min_periods).to_numpy().astype(np.float64)
    out = np.full(len(x), np.nan)
    ok = ~np.isnan(mean) & ~np.isnan(std) & (std > 0) & ~np.isnan(x)
    out[ok] = (x[ok] - mean[ok]) / std[ok]
    return out


def rolling_sum(x: F, window: int) -> F:
    """Sum over the last `window` rows INCLUDING the current; NaN until the window is full."""
    return (
        pl.Series(x.astype(np.float64))
        .rolling_sum(window, min_samples=window)
        .to_numpy()
        .astype(np.float64)
    )


def rolling_max_prev(x: F, window: int) -> F:
    s = pl.Series(x.astype(np.float64)).fill_nan(None).shift(1)
    return s.rolling_max(window, min_samples=window).to_numpy().astype(np.float64)


def rolling_min_prev(x: F, window: int) -> F:
    s = pl.Series(x.astype(np.float64)).fill_nan(None).shift(1)
    return s.rolling_min(window, min_samples=window).to_numpy().astype(np.float64)


def lagged(x: F, k: int) -> F:
    out = np.full(len(x), np.nan)
    out[k:] = x[:-k]
    return out


def _last_at_many(times: I, values: F, t: I, lag: int = 0) -> F:
    out = np.full(len(t), np.nan)
    if len(times) == 0:
        return out
    pos = np.searchsorted(times, t - lag, side="right") - 1
    ok = pos >= 0
    out[ok] = values[pos[ok]]
    return out


def _exact_at(times: I, values: F, t: I) -> F:
    """values at rows whose time equals t exactly (same-bar join), NaN otherwise."""
    out = np.full(len(t), np.nan)
    if len(times) == 0:
        return out
    pos = np.searchsorted(times, t)
    ok = (pos < len(times)) & (times[np.minimum(pos, len(times) - 1)] == t)
    out[ok] = values[pos[ok]]
    return out


def _nan_to_zero(x: F) -> F:
    return np.where(np.isnan(x), 0.0, x)


class V5Regime(StrEnum):
    UP_EXPANSION = "UP_EXPANSION"
    UP_COMPRESSION = "UP_COMPRESSION"
    UP_NORMAL = "UP_NORMAL"
    DOWN_EXPANSION = "DOWN_EXPANSION"
    DOWN_COMPRESSION = "DOWN_COMPRESSION"
    DOWN_NORMAL = "DOWN_NORMAL"
    NEUTRAL_EXPANSION = "NEUTRAL_EXPANSION"
    NEUTRAL_COMPRESSION = "NEUTRAL_COMPRESSION"
    NEUTRAL_NORMAL = "NEUTRAL_NORMAL"
    UNCLEAR = "UNCLEAR"


@dataclass
class FeatureFrame:
    close_ms: I
    cols: dict[str, F] = field(default_factory=dict)
    regime: list[V5Regime] = field(default_factory=list)
    regime_code: I = field(default_factory=lambda: np.zeros(0, dtype=np.int64))

    def idx_at(self, t_ms: int) -> int:
        return int(np.searchsorted(self.close_ms, t_ms, side="right")) - 1

    def v(self, name: str, k: int) -> float:
        return float(self.cols[name][k]) if 0 <= k < len(self.close_ms) else math.nan

    def row(self, k: int) -> dict[str, float]:
        return {c: float(a[k]) for c, a in self.cols.items()}

    def to_frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "close_ms": self.close_ms,
                **{c: a for c, a in self.cols.items()},
                "regime": [r.value for r in self.regime],
            }
        )


@dataclass
class V5Inputs:
    """Optional V5-specific datasets (empty frames allowed; the frame records what was used)."""

    flow: pl.DataFrame
    book: pl.DataFrame
    index: pl.DataFrame

    @classmethod
    def empty(cls) -> V5Inputs:
        return cls(pl.DataFrame(), pl.DataFrame(), pl.DataFrame())


def _trend_state_arr(h4: TfSeries) -> F:
    c, ef, es = h4.close, h4.ind["ema_fast"], h4.ind["ema_slow"]
    out = np.zeros(len(c))
    out[(ef > es) & (c > es)] = 1.0
    out[(ef < es) & (c < es)] = -1.0
    out[np.isnan(ef) | np.isnan(es)] = np.nan
    return out


def book_levels(book: pl.DataFrame) -> pl.DataFrame:
    """Pivot archive bookDepth rows (time_ms, percentage, depth) to one row per snapshot with
    bid1/ask1/bid5/ask5 depth (base quantity within 1% and 5% of the mid). Empty in -> empty out."""
    if book.is_empty():
        return pl.DataFrame()
    piv = (
        book.filter(pl.col("percentage").is_in([-5, -1, 1, 5]))
        .select("time_ms", "percentage", "depth")
        .pivot(on="percentage", index="time_ms", values="depth", aggregate_function="first")
        .sort("time_ms")
    )
    ren = {"-1": "bid1", "1": "ask1", "-5": "bid5", "5": "ask5"}
    piv = piv.rename({k: v for k, v in ren.items() if k in piv.columns})
    for c in ("bid1", "ask1", "bid5", "ask5"):
        if c not in piv.columns:
            piv = piv.with_columns(pl.lit(math.nan).alias(c))
    return piv.select("time_ms", "bid1", "ask1", "bid5", "ask5")


def build_feature_frame(
    series: MultiTfSeries, aux: AuxSeries, cfg: V5Config, extra: V5Inputs | None = None
) -> FeatureFrame:
    fc = cfg.features
    extra = extra or V5Inputs.empty()
    base, h1, h4 = series.base, series.series[H1], series.series[H4]
    t = base.close_ms.astype(np.int64)
    t_open = base.open_ms.astype(np.int64)
    n = len(t)
    win, mp = fc.z_window_bars, fc.z_min_periods
    c, h, lo = (
        base.close.astype(np.float64),
        base.high.astype(np.float64),
        base.low.astype(np.float64),
    )
    vol = base.volume.astype(np.float64)
    cols: dict[str, F] = {"close": c, "high": h, "low": lo, "volume": vol}
    # ---- 1H / 4H context at T_k (last completed bar)
    j1 = np.searchsorted(h1.close_ms, t, side="right") - 1
    j4 = np.searchsorted(h4.close_ms, t, side="right") - 1
    ok1, ok4 = j1 >= 0, j4 >= 0
    jj1, jj4 = np.maximum(j1, 0), np.maximum(j4, 0)
    cols["atr"] = np.where(ok1, h1.ind["atr"][jj1], np.nan)
    cols["ema21_1h"] = np.where(ok1, h1.ind["ema_fast"][jj1], np.nan)
    cols["close_1h"] = np.where(ok1, h1.close[jj1], np.nan)
    cols["swing_low_1h"] = np.where(ok1, h1.ind["swing_low"][jj1], np.nan)
    cols["swing_high_1h"] = np.where(ok1, h1.ind["swing_high"][jj1], np.nan)
    cols["align_1h"] = np.sign(cols["close_1h"] - cols["ema21_1h"])
    cols["trend_4h"] = np.where(ok4, _trend_state_arr(h4)[jj4], np.nan)
    # ---- price / volume
    for k, name in ((1, "ret_5m"), (3, "ret_15m"), (12, "ret_1h"), (48, "ret_4h")):
        cols[name] = c / lagged(c, k) - 1.0
    cols["ret_15m_z"] = rolling_z(cols["ret_15m"], win, mp)
    cols["ret_1h_z"] = rolling_z(cols["ret_1h"], win, mp)
    lr = np.concatenate([[np.nan], np.diff(np.log(c))])
    lr2 = lr * lr
    for w, name in ((12, "rv_1h"), (288, "rv_24h")):
        m1, m2 = rolling_sum(_nan_to_zero(lr), w) / w, rolling_sum(_nan_to_zero(lr2), w) / w
        cols[name] = np.sqrt(np.maximum(m2 - m1 * m1, 0.0)) * math.sqrt(288.0)
    cols["rv_24h_z"] = rolling_z(cols["rv_24h"], win, mp)
    lv = np.log(np.where(vol > 0, vol, np.nan))
    cols["vol_5m_z"] = rolling_z(lv, win, mp)
    v1h = rolling_sum(vol, 12)
    cols["vol_1h_z"] = rolling_z(np.log(np.where(v1h > 0, v1h, np.nan)), win, mp)
    hi48, lo48 = (
        rolling_max_prev(h, fc.range_window_bars),
        rolling_min_prev(lo, fc.range_window_bars),
    )
    rng = hi48 - lo48
    cols["range_pos_48h"] = np.where(rng > 0, (c - lo48) / rng, np.nan)
    cols["hi_break_ref"] = rolling_max_prev(h, fc.break_window_bars)
    cols["lo_break_ref"] = rolling_min_prev(lo, fc.break_window_bars)
    cols["break_up"] = (h > cols["hi_break_ref"]).astype(np.float64)
    cols["break_dn"] = (lo < cols["lo_break_ref"]).astype(np.float64)
    sl = cfg.execution.struct_lookback_bars
    cols["lo_struct"] = rolling_min_prev(lo, sl)  # lowest low of the `sl` bars BEFORE the bar
    cols["hi_struct"] = rolling_max_prev(h, sl)
    # ---- trade flow (aggTrades 5m aggregates; kline taker-buy volume as the fallback)
    kb = base.ind.get("taker_buy_volume", np.full(n, np.nan))
    k_buy = kb.astype(np.float64)
    k_sell = vol - k_buy
    flow = extra.flow
    if not flow.is_empty():
        f = flow.sort("open_time_ms")
        ft = f["open_time_ms"].to_numpy().astype(np.int64)

        def fcol(name: str) -> F:
            return _exact_at(ft, f[name].to_numpy().astype(np.float64), t_open)

        a_buy, a_sell = fcol("buy_qty"), fcol("sell_qty")
        src = ~np.isnan(a_buy)
        buy, sell = np.where(src, a_buy, k_buy), np.where(src, a_sell, k_sell)
        big_buy, big_sell = fcol("big_buy_qty"), fcol("big_sell_qty")
        cols["n_trades"] = fcol("n_trades")
        cols["max_trade_qty"] = fcol("max_trade_qty")
    else:
        src = np.zeros(n, dtype=bool)
        buy, sell = k_buy, k_sell
        big_buy = big_sell = np.full(n, np.nan)
        cols["n_trades"] = base.ind.get("trades", np.full(n, np.nan)).astype(np.float64)
        cols["max_trade_qty"] = np.full(n, np.nan)
    cols["flow_source"] = src.astype(np.float64)  # 1 = aggTrades, 0 = kline fallback
    cols["buy_qty"], cols["sell_qty"] = buy, sell
    delta = buy - sell
    cols["delta_5m"] = delta
    cols["delta_15m"] = rolling_sum(delta, 3)
    cols["delta_1h"] = rolling_sum(delta, 12)
    tot_1h = rolling_sum(buy + sell, 12)
    cols["imbalance_1h"] = np.where(tot_1h > 0, cols["delta_1h"] / tot_1h, np.nan)
    cols["cvd"] = np.cumsum(_nan_to_zero(delta))
    cols["cvd_slope_1h"] = cols["delta_1h"] / 12.0
    cols["cvd_slope_4h"] = rolling_sum(delta, 48) / 48.0
    cols["flow_accel"] = cols["delta_15m"] - lagged(cols["delta_15m"], 3)
    bb, bs = rolling_sum(_nan_to_zero(big_buy), 12), rolling_sum(_nan_to_zero(big_sell), 12)
    big_ok = rolling_sum((~np.isnan(big_buy)).astype(np.float64), 12) == 12
    with np.errstate(invalid="ignore", divide="ignore"):
        cols["big_imb_1h"] = np.where(big_ok & (bb + bs > 0), (bb - bs) / (bb + bs), np.nan)
    for name in ("imbalance_1h", "cvd_slope_1h", "cvd_slope_4h", "flow_accel", "big_imb_1h"):
        cols[f"{name}_z"] = rolling_z(cols[name], win, mp)
    cols["divergence"] = cols["ret_1h_z"] - cols["cvd_slope_1h_z"]
    # ---- order book (archive bookDepth, from 2023-01-01; last snapshot <= T_k; diagnostics)
    lv_ = book_levels(extra.book)
    if not lv_.is_empty():
        bt = lv_["time_ms"].to_numpy().astype(np.int64)
        for name in ("bid1", "ask1", "bid5", "ask5"):
            arr = _last_at_many(bt, lv_[name].to_numpy().astype(np.float64), t)
            # a snapshot older than one hour is stale: treat as missing
            age = t - _last_at_many(bt, bt.astype(np.float64), t)
            cols[name] = np.where(age <= MS_1H, arr, np.nan)
    else:
        for name in ("bid1", "ask1", "bid5", "ask5"):
            cols[name] = np.full(n, np.nan)
    b1, a1, b5, a5 = cols["bid1"], cols["ask1"], cols["bid5"], cols["ask5"]
    cols["book_imb_1"] = np.where(b1 + a1 > 0, (b1 - a1) / (b1 + a1), np.nan)
    cols["book_imb_5"] = np.where(b5 + a5 > 0, (b5 - a5) / (b5 + a5), np.nan)
    cols["book_imb_1_chg_1h"] = cols["book_imb_1"] - lagged(cols["book_imb_1"], 12)
    cols["depth_total"] = b5 + a5
    for name in ("bid1", "ask1", "depth_total"):
        cols[f"{name}_z"] = rolling_z(cols[name], win, mp)
    # ---- open interest (metrics 5m rows, observation time + latency <= T_k)
    oi = _last_at_many(aux.metrics_t, aux.oi, t, aux.latency_ms)
    oi[oi <= 0] = np.nan
    cols["oi"] = oi
    for k, name in ((1, "oi_chg_5m"), (3, "oi_chg_15m"), (12, "oi_chg_1h")):
        cols[name] = oi / lagged(oi, k) - 1.0
    cols["oi_accel"] = cols["oi_chg_15m"] - lagged(cols["oi_chg_15m"], 3)
    cols["oi_chg_1h_z"] = rolling_z(cols["oi_chg_1h"], win, mp)
    cols["oi_chg_15m_z"] = rolling_z(cols["oi_chg_15m"], win, mp)
    # ---- funding
    if len(aux.funding_t):
        fr = aux.funding_rate.astype(np.float64)
        fz = rolling_z(fr, fc.funding_z_window, max(10, fc.funding_z_window // 2))
        fchg = np.concatenate([[np.nan], np.diff(fr)])
        cols["fund"] = _last_at_many(aux.funding_t, fr, t, aux.latency_ms)
        cols["fund_chg"] = _last_at_many(aux.funding_t, fchg, t, aux.latency_ms)
        cols["fund_z"] = _last_at_many(aux.funding_t, fz, t, aux.latency_ms)
    else:
        cols["fund"] = cols["fund_chg"] = cols["fund_z"] = np.full(n, np.nan)
    # ---- premium (1h mean of premium-index closes with close_time + latency <= T_k)
    if len(aux.premium_t):
        pc = aux.premium_close.astype(np.float64)
        cols["prem"] = _last_at_many(aux.premium_t, pc, t, aux.latency_ms)
        cs_p = np.concatenate([[0.0], np.cumsum(pc)])
        ppos = np.searchsorted(aux.premium_t, t - aux.latency_ms, side="right") - 1
        plo = np.searchsorted(aux.premium_t, t - aux.latency_ms - MS_1H, side="right")
        pm = np.full(n, np.nan)
        okp = (ppos >= plo) & (ppos >= 0)
        pm[okp] = (cs_p[ppos[okp] + 1] - cs_p[plo[okp]]) / (ppos[okp] - plo[okp] + 1)
        cols["prem_1h"] = pm
    else:
        cols["prem"] = cols["prem_1h"] = np.full(n, np.nan)
    cols["prem_z"] = rolling_z(cols["prem_1h"], win, mp)
    # ---- basis vs index (same-bar index kline close)
    if not extra.index.is_empty():
        ix = extra.index.sort("open_time_ms")
        idx_close = _exact_at(
            ix["open_time_ms"].to_numpy().astype(np.int64),
            ix["close"].to_numpy().astype(np.float64),
            t_open,
        )
    else:
        idx_close = np.full(n, np.nan)
    cols["index_close"] = idx_close
    cols["basis"] = np.where(idx_close > 0, c / idx_close - 1.0, np.nan)
    cols["basis_chg_1h"] = cols["basis"] - lagged(cols["basis"], 12)
    cols["basis_z"] = rolling_z(cols["basis"], win, mp)
    ff = FeatureFrame(t, cols)
    ff.regime, ff.regime_code = _regimes(ff)
    return ff


REGIME_LIST = list(V5Regime)


def _regimes(ff: FeatureFrame) -> tuple[list[V5Regime], I]:
    tr, vz = ff.cols["trend_4h"], ff.cols["rv_24h_z"]
    code = np.full(len(tr), REGIME_LIST.index(V5Regime.UNCLEAR), dtype=np.int64)
    ok = ~np.isnan(tr) & ~np.isnan(vz)
    a = np.where(tr > 0, "UP", np.where(tr < 0, "DOWN", "NEUTRAL"))
    b = np.where(vz >= 1.0, "EXPANSION", np.where(vz <= -1.0, "COMPRESSION", "NORMAL"))
    names = np.char.add(np.char.add(a.astype(str), "_"), b.astype(str))
    lookup = {r.value: i for i, r in enumerate(REGIME_LIST)}
    for i in np.flatnonzero(ok):
        code[i] = lookup[str(names[i])]
    return [REGIME_LIST[int(i)] for i in code], code


FEATURE_COLUMNS: list[str] = [
    "ret_5m", "ret_15m", "ret_1h", "ret_4h", "ret_15m_z", "ret_1h_z",
    "rv_1h", "rv_24h", "rv_24h_z", "vol_5m_z", "vol_1h_z", "range_pos_48h",
    "break_up", "break_dn", "trend_4h", "align_1h",
    "flow_source", "buy_qty", "sell_qty", "delta_5m", "delta_15m", "delta_1h", "imbalance_1h",
    "cvd", "cvd_slope_1h", "cvd_slope_4h", "flow_accel", "big_imb_1h",
    "imbalance_1h_z", "cvd_slope_1h_z", "cvd_slope_4h_z", "flow_accel_z", "big_imb_1h_z",
    "divergence",
    "bid1", "ask1", "bid5", "ask5", "book_imb_1", "book_imb_5", "book_imb_1_chg_1h",
    "depth_total", "bid1_z", "ask1_z", "depth_total_z",
    "oi", "oi_chg_5m", "oi_chg_15m", "oi_chg_1h", "oi_accel", "oi_chg_1h_z", "oi_chg_15m_z",
    "fund", "fund_chg", "fund_z", "prem", "prem_1h", "prem_z",
    "index_close", "basis", "basis_chg_1h", "basis_z",
]  # fmt: skip
