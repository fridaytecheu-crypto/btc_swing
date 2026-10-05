"""Auxiliary PIT features: funding, open interest, taker flow, premium (basis), volume
acceleration and mark price. Everything is looked up with `time <= t` (plus provider latency),
so a snapshot at decision time t never contains a later observation. These are FEATURES recorded
on episodes and trades for research; none of them gates a trade in V1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import polars as pl
from numpy.typing import NDArray

from btc_swing.core.enums import Timeframe
from btc_swing.features.view import MarketView, MultiTfSeries

F = NDArray[np.float64]
I = NDArray[np.int64]  # noqa: E741


def _arr(df: pl.DataFrame | None, col: str) -> F:
    if df is None or df.is_empty() or col not in df.columns:
        return np.zeros(0, dtype=np.float64)
    return df[col].to_numpy().astype(np.float64)


def _ms(df: pl.DataFrame | None, col: str) -> I:
    if df is None or df.is_empty() or col not in df.columns:
        return np.zeros(0, dtype=np.int64)
    return df[col].to_numpy().astype(np.int64)


def _last_at(times: I, values: F, t: int, lag: int = 0) -> tuple[float, int]:
    """(value, index) of the last observation with time <= t, or (nan, -1)."""
    if len(times) == 0:
        return math.nan, -1
    i = int(np.searchsorted(times, t - lag, side="right")) - 1
    if i < 0:
        return math.nan, -1
    return float(values[i]), i


@dataclass
class AuxSeries:
    """Time-indexed auxiliary datasets aligned to epoch milliseconds."""

    funding_t: I = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    funding_rate: F = field(default_factory=lambda: np.zeros(0))
    metrics_t: I = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    oi: F = field(default_factory=lambda: np.zeros(0))
    oi_value: F = field(default_factory=lambda: np.zeros(0))
    ls_accounts: F = field(default_factory=lambda: np.zeros(0))
    top_ls_positions: F = field(default_factory=lambda: np.zeros(0))
    taker_ls_vol: F = field(default_factory=lambda: np.zeros(0))
    premium_t: I = field(default_factory=lambda: np.zeros(0, dtype=np.int64))  # close_time_ms
    premium_close: F = field(default_factory=lambda: np.zeros(0))
    mark_t: I = field(default_factory=lambda: np.zeros(0, dtype=np.int64))  # close_time_ms
    mark_open: F = field(default_factory=lambda: np.zeros(0))
    mark_high: F = field(default_factory=lambda: np.zeros(0))
    mark_low: F = field(default_factory=lambda: np.zeros(0))
    mark_close: F = field(default_factory=lambda: np.zeros(0))
    latency_ms: int = 0

    @classmethod
    def build(
        cls,
        funding: pl.DataFrame | None,
        metrics: pl.DataFrame | None,
        premium: pl.DataFrame | None,
        mark: pl.DataFrame | None,
        latency_minutes: int = 0,
    ) -> AuxSeries:
        a = cls(latency_ms=latency_minutes * 60_000)
        if funding is not None and not funding.is_empty():
            f = funding.sort("time_ms")
            a.funding_t, a.funding_rate = _ms(f, "time_ms"), _arr(f, "funding_rate")
        if metrics is not None and not metrics.is_empty():
            m = metrics.sort("time_ms")
            a.metrics_t = _ms(m, "time_ms")
            a.oi = _arr(m, "open_interest")
            a.oi_value = _arr(m, "open_interest_value")
            a.ls_accounts = _arr(m, "long_short_ratio_accounts")
            a.top_ls_positions = _arr(m, "top_trader_long_short_ratio_positions")
            a.taker_ls_vol = _arr(m, "taker_long_short_volume_ratio")
        if premium is not None and not premium.is_empty():
            p = premium.sort("open_time_ms")
            a.premium_t, a.premium_close = _ms(p, "close_time_ms"), _arr(p, "close")
        if mark is not None and not mark.is_empty():
            k = mark.sort("open_time_ms")
            a.mark_t = _ms(k, "close_time_ms")
            a.mark_open, a.mark_high = _arr(k, "open"), _arr(k, "high")
            a.mark_low, a.mark_close = _arr(k, "low"), _arr(k, "close")
        return a

    # ----------------------------------------------------------------- mark price alignment
    def mark_aligned(self, base_close_ms: I) -> tuple[F, F, F, F]:
        """Mark (open, high, low, close) per base 5m bar, NaN where the mark bar is missing."""
        n = len(base_close_ms)
        out = [np.full(n, np.nan) for _ in range(4)]
        if len(self.mark_t) == 0:
            return out[0], out[1], out[2], out[3]
        pos = np.searchsorted(self.mark_t, base_close_ms)
        ok = (pos < len(self.mark_t)) & (
            self.mark_t[np.minimum(pos, len(self.mark_t) - 1)] == base_close_ms
        )
        idx = pos[ok]
        for o, src in zip(
            out, (self.mark_open, self.mark_high, self.mark_low, self.mark_close), strict=True
        ):
            o[ok] = src[idx]
        return out[0], out[1], out[2], out[3]

    # ----------------------------------------------------------------- snapshot
    def snapshot(self, t: int, view: MarketView, series: MultiTfSeries) -> dict[str, float]:
        lag = self.latency_ms
        s: dict[str, float] = {}
        fr, fi = _last_at(self.funding_t, self.funding_rate, t, lag)
        s["funding_rate_last"] = fr
        s["funding_rate_mean_3"] = (
            float(np.mean(self.funding_rate[max(0, fi - 2) : fi + 1])) if fi >= 0 else math.nan
        )
        s["funding_annualised_pct"] = fr * 3 * 365 * 100 if not math.isnan(fr) else math.nan
        oi, oi_i = _last_at(self.metrics_t, self.oi, t, lag)
        s["open_interest"] = oi
        s["open_interest_value"] = _last_at(self.metrics_t, self.oi_value, t, lag)[0]
        for name, back in (("1h", 12), ("4h", 48), ("24h", 288)):
            j = oi_i - back
            s[f"oi_change_{name}_pct"] = (
                (oi / float(self.oi[j]) - 1.0) * 100
                if oi_i >= 0 and j >= 0 and self.oi[j] > 0
                else math.nan
            )
        s["long_short_ratio_accounts"] = _last_at(self.metrics_t, self.ls_accounts, t, lag)[0]
        s["top_trader_ls_positions"] = _last_at(self.metrics_t, self.top_ls_positions, t, lag)[0]
        s["taker_long_short_vol_ratio"] = _last_at(self.metrics_t, self.taker_ls_vol, t, lag)[0]
        pr, pi = _last_at(self.premium_t, self.premium_close, t, lag)
        s["premium_index"] = pr
        s["premium_mean_1h"] = (
            float(np.mean(self.premium_close[max(0, pi - 11) : pi + 1])) if pi >= 0 else math.nan
        )
        mk, _mi = _last_at(self.mark_t, self.mark_close, t, lag)
        s["mark_price"] = mk
        last = view.close(Timeframe.M5)
        s["last_minus_mark_pct"] = (
            (last / mk - 1.0) * 100 if not math.isnan(mk) and mk > 0 else math.nan
        )
        # taker buy ratio and volume acceleration from the (PIT) kline flow columns
        s["taker_buy_ratio_1h"] = _taker_ratio(series, view, 12)
        s["taker_buy_ratio_4h"] = _taker_ratio(series, view, 48)
        s["volume_accel_1h"] = _volume_accel(view, Timeframe.H1, 20)
        s["volume_accel_5m"] = _volume_accel(view, Timeframe.M5, 36)
        return s


def _taker_ratio(series: MultiTfSeries, view: MarketView, bars: int) -> float:
    base = series.base
    i = view.idx[Timeframe.M5]
    if i < bars - 1 or "taker_buy_volume" not in base.ind:
        return math.nan
    tb = base.ind["taker_buy_volume"][i - bars + 1 : i + 1]
    v = base.volume[i - bars + 1 : i + 1]
    tot = float(np.sum(v))
    return float(np.sum(tb)) / tot if tot > 0 else math.nan


def _volume_accel(view: MarketView, tf: Timeframe, window: int) -> float:
    s = view.series.series[tf]
    i = view.idx[tf]
    if i < window:
        return math.nan
    prev = s.volume[i - window : i]
    m = float(np.mean(prev))
    return float(s.volume[i]) / m if m > 0 else math.nan
