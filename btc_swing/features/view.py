"""Multi-timeframe PIT view.

`MultiTfSeries` precomputes, for every timeframe, the completed-bar arrays and causal indicators
over the whole study window. Because every indicator is causal and every higher-timeframe bar is
labelled by its close time, a `MarketView` at decision time T is just an index per timeframe:
the last bar with close_time <= T. Looking up bars/indicators through the view cannot see the
future; attempting to read an offset beyond the current index raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl
from numpy.typing import NDArray

from btc_swing.core.config import IndicatorCfg
from btc_swing.core.enums import Timeframe
from btc_swing.features.indicators import (
    atr,
    ema,
    rolling_max_prev,
    rolling_min_prev,
    slope,
    swing_points,
)
from btc_swing.features.resample import resample_completed

F = NDArray[np.float64]
ALL_TFS = [Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1]


@dataclass
class TfSeries:
    tf: Timeframe
    open_ms: NDArray[np.int64]
    close_ms: NDArray[np.int64]
    open: F
    high: F
    low: F
    close: F
    volume: F
    ind: dict[str, F] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.open_ms)


def build_tf_series(
    bars_5m: pl.DataFrame, tf: Timeframe, icfg: IndicatorCfg, slope_bars: int
) -> TfSeries:
    last_close = int(bars_5m["open_time_ms"][-1]) + 5 * 60_000
    df = resample_completed(bars_5m, tf, last_close)
    s = TfSeries(
        tf=tf,
        open_ms=df["open_time_ms"].to_numpy().astype(np.int64),
        close_ms=df["close_time_ms"].to_numpy().astype(np.int64),
        open=df["open"].to_numpy().astype(np.float64),
        high=df["high"].to_numpy().astype(np.float64),
        low=df["low"].to_numpy().astype(np.float64),
        close=df["close"].to_numpy().astype(np.float64),
        volume=df["volume"].to_numpy().astype(np.float64),
    )
    if "taker_buy_volume" in df.columns:
        s.ind["taker_buy_volume"] = (
            df["taker_buy_volume"].fill_null(0.0).to_numpy().astype(np.float64)
        )
    s.ind["ema_fast"] = ema(s.close, icfg.ema_fast)
    s.ind["ema_slow"] = ema(s.close, icfg.ema_slow)
    s.ind["ema_trend"] = ema(s.close, icfg.ema_trend)
    s.ind["atr"] = atr(s.high, s.low, s.close, icfg.atr_period)
    s.ind["atr_pct"] = s.ind["atr"] / s.close
    s.ind["atr_prev10"] = np.concatenate([np.full(10, np.nan), s.ind["atr"][:-10]])
    s.ind["donchian_high"] = rolling_max_prev(s.high, icfg.donchian_period)
    s.ind["donchian_low"] = rolling_min_prev(s.low, icfg.donchian_period)
    s.ind["ema_slow_slope"] = slope(s.ind["ema_slow"], slope_bars)
    sh, shi, sl, sli = swing_points(s.high, s.low, icfg.swing_k)
    s.ind["swing_high"] = sh
    s.ind["swing_high_idx"] = shi
    s.ind["swing_low"] = sl
    s.ind["swing_low_idx"] = sli
    return s


class MultiTfSeries:
    def __init__(self, bars_5m: pl.DataFrame, icfg: IndicatorCfg, slope_bars: int) -> None:
        if bars_5m.is_empty():
            raise ValueError("no 5m bars")
        self.series: dict[Timeframe, TfSeries] = {
            tf: build_tf_series(bars_5m, tf, icfg, slope_bars) for tf in ALL_TFS
        }
        self.min_bars = icfg.min_bars

    @property
    def base(self) -> TfSeries:
        return self.series[Timeframe.M5]

    def view_at(self, t_ms: int) -> MarketView:
        idx = {
            tf: int(np.searchsorted(s.close_ms, t_ms, side="right")) - 1
            for tf, s in self.series.items()
        }
        return MarketView(self, t_ms, idx)


class LookAheadError(RuntimeError):
    pass


@dataclass
class MarketView:
    series: MultiTfSeries
    t_ms: int
    idx: dict[Timeframe, int]

    def n(self, tf: Timeframe) -> int:
        """Number of completed bars of `tf` visible at t."""
        return self.idx[tf] + 1

    def warm(self, tf: Timeframe) -> bool:
        return self.n(tf) >= self.series.min_bars.get(tf, 1)

    def _i(self, tf: Timeframe, offset: int) -> int:
        if offset < 0:
            raise LookAheadError("offset must be >= 0 (0 = latest completed bar)")
        i = self.idx[tf] - offset
        if i < 0:
            raise IndexError(f"not enough {tf.value} history (offset {offset})")
        return i

    def bar(self, tf: Timeframe, offset: int = 0) -> tuple[int, int, float, float, float, float]:
        """(open_ms, close_ms, open, high, low, close) of the completed bar `offset` back."""
        s = self.series.series[tf]
        i = self._i(tf, offset)
        return (
            int(s.open_ms[i]),
            int(s.close_ms[i]),
            float(s.open[i]),
            float(s.high[i]),
            float(s.low[i]),
            float(s.close[i]),
        )

    def close(self, tf: Timeframe, offset: int = 0) -> float:
        return float(self.series.series[tf].close[self._i(tf, offset)])

    def high(self, tf: Timeframe, offset: int = 0) -> float:
        return float(self.series.series[tf].high[self._i(tf, offset)])

    def low(self, tf: Timeframe, offset: int = 0) -> float:
        return float(self.series.series[tf].low[self._i(tf, offset)])

    def ind(self, tf: Timeframe, name: str, offset: int = 0) -> float:
        return float(self.series.series[tf].ind[name][self._i(tf, offset)])

    def close_ms(self, tf: Timeframe, offset: int = 0) -> int:
        return int(self.series.series[tf].close_ms[self._i(tf, offset)])

    def assert_pit(self) -> None:
        for tf, i in self.idx.items():
            if i >= 0 and int(self.series.series[tf].close_ms[i]) > self.t_ms:
                raise LookAheadError(f"{tf.value} bar closing after t is visible")
