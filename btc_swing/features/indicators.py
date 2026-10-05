"""Causal indicators on numpy arrays. Every value at index i uses bars <= i only.

Swing points use the fractal definition with half-width k: a swing high at i needs high[i] strictly
above the k highs on each side, so it is CONFIRMED only at bar i + k. `last_swing_*` arrays hold,
at every index j, the most recent swing confirmed at or before j (NaN until one exists).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

F = NDArray[np.float64]


def ema(x: F, period: int) -> F:
    out = np.full_like(x, np.nan)
    if len(x) == 0:
        return out
    alpha = 2.0 / (period + 1.0)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1.0 - alpha) * out[i - 1]
    out[: period - 1] = np.nan  # not warm
    return out


def true_range(high: F, low: F, close: F) -> F:
    prev_close = np.concatenate([[np.nan], close[:-1]])
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    tr[0] = high[0] - low[0] if len(high) else np.nan
    return tr


def atr(high: F, low: F, close: F, period: int) -> F:
    """Wilder ATR: SMA seed over the first `period` bars, then recursive smoothing."""
    tr = true_range(high, low, close)
    out = np.full_like(tr, np.nan)
    n = len(tr)
    if n < period:
        return out
    out[period - 1] = float(np.mean(tr[:period]))
    for i in range(period, n):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def rolling_max_prev(x: F, window: int) -> F:
    """Max of the previous `window` values, excluding the current bar (Donchian upper)."""
    out = np.full_like(x, np.nan)
    for i in range(window, len(x)):
        out[i] = np.max(x[i - window : i])
    return out


def rolling_min_prev(x: F, window: int) -> F:
    out = np.full_like(x, np.nan)
    for i in range(window, len(x)):
        out[i] = np.min(x[i - window : i])
    return out


def swing_points(high: F, low: F, k: int) -> tuple[F, F, F, F]:
    """Returns (last_swing_high, last_swing_high_idx, last_swing_low, last_swing_low_idx).

    Values at j are the most recent swing confirmed at or before j (confirmation = pivot + k).
    *_idx arrays hold the pivot bar index as float (NaN when none).
    """
    n = len(high)
    lsh = np.full(n, np.nan)
    lsh_i = np.full(n, np.nan)
    lsl = np.full(n, np.nan)
    lsl_i = np.full(n, np.nan)
    cur_h = cur_hi = cur_l = cur_li = np.nan
    for j in range(n):
        p = j - k  # pivot candidate confirmed at j
        if p - k >= 0:
            hp = high[p]
            if np.all(high[p - k : p] < hp) and np.all(high[p + 1 : p + k + 1] < hp):
                cur_h, cur_hi = hp, float(p)
            lp = low[p]
            if np.all(low[p - k : p] > lp) and np.all(low[p + 1 : p + k + 1] > lp):
                cur_l, cur_li = lp, float(p)
        lsh[j], lsh_i[j], lsl[j], lsl_i[j] = cur_h, cur_hi, cur_l, cur_li
    return lsh, lsh_i, lsl, lsl_i


def slope(x: F, bars: int) -> F:
    out = np.full_like(x, np.nan)
    if len(x) > bars:
        out[bars:] = x[bars:] - x[:-bars]
    return out
