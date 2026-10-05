"""V3 structural context (PIT): 4H trend state, 1H alignment, 1H ATR percentile, V3 regime label.
Everything reads the `MarketView` at t (completed bars only)."""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

from btc_swing.core.enums import Side, Timeframe
from btc_swing.features.view import MarketView, TfSeries
from btc_swing.v3.config import V3Config, V3Regime

F = NDArray[np.float64]
_PCT_CACHE: dict[tuple[int, int, int], F] = {}


def atr_percentile_series(s: TfSeries, window: int) -> F:
    """percentile[j] = share of the previous `window` ATR values below ATR[j] (NaN while not warm).
    Uses bars < j only, so it is causal; cached per series object."""
    key = (id(s), len(s), window)
    hit = _PCT_CACHE.get(key)
    if hit is not None:
        return hit
    a = s.ind["atr"]
    out = np.full(len(a), np.nan)
    for j in range(window, len(a)):
        prev = a[j - window : j]
        if math.isnan(a[j]) or np.isnan(prev).any():
            continue
        out[j] = float(np.mean(prev < a[j]))
    _PCT_CACHE[key] = out
    return out


def atr_percentile(view: MarketView, tf: Timeframe, window: int) -> float:
    s = view.series.series[tf]
    i = view.idx[tf]
    if i < 0:
        return math.nan
    return float(atr_percentile_series(s, window)[i])


def trend_state(view: MarketView, tf: Timeframe) -> int:
    """+1 UP (EMA21 > EMA50 and close > EMA50), -1 DOWN (mirror), 0 NEUTRAL / not warm."""
    if not view.warm(tf):
        return 0
    c, ef, es = view.close(tf), view.ind(tf, "ema_fast"), view.ind(tf, "ema_slow")
    if any(math.isnan(x) for x in (c, ef, es)):
        return 0
    if ef > es and c > es:
        return 1
    if ef < es and c < es:
        return -1
    return 0


def aligned(view: MarketView, tf: Timeframe, side: Side) -> bool:
    if not view.warm(tf):
        return False
    ef, es = view.ind(tf, "ema_fast"), view.ind(tf, "ema_slow")
    if math.isnan(ef) or math.isnan(es):
        return False
    return ef > es if side is Side.LONG else ef < es


def classify_v3_regime(view: MarketView, cfg: V3Config) -> V3Regime:
    d1, h4, h1 = Timeframe.D1, Timeframe.H4, Timeframe.H1
    if not (view.warm(d1) and view.warm(h4) and view.warm(h1)):
        return V3Regime.UNCLEAR
    pct = atr_percentile(view, h1, cfg.context.vol_percentile_window)
    a4, a4p = view.ind(h4, "atr"), view.ind(h4, "atr_prev10")
    ratio = a4 / a4p if not math.isnan(a4p) and a4p > 0 else math.nan
    if math.isnan(pct):
        return V3Regime.UNCLEAR
    r = cfg.regime
    if pct >= r.vol_expansion_percentile or (
        not math.isnan(ratio) and ratio >= r.vol_expansion_4h_atr_ratio
    ):
        return V3Regime.VOLATILITY_EXPANSION
    if pct <= r.vol_compression_percentile:
        return V3Regime.VOLATILITY_COMPRESSION
    t4 = trend_state(view, cfg.context.trend_tf)
    try:
        slope = view.ind(d1, "ema_slow") - view.ind(d1, "ema_slow", r.d1_slope_bars)
    except IndexError:
        slope = math.nan
    s1 = 0 if math.isnan(slope) or slope == 0 else (1 if slope > 0 else -1)
    if t4 == 0 and s1 == 0:
        return V3Regime.RANGE
    if t4 == 0:
        return V3Regime.RANGE
    if t4 == s1:
        return V3Regime.TREND_UP if t4 > 0 else V3Regime.TREND_DOWN
    return V3Regime.TRANSITION
