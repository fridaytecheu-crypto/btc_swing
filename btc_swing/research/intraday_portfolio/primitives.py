"""Validated primitives (each independently tested):

1. completed UTC 5-minute bars (incomplete bars never used; duplicates never duplicate; gaps detected)
2. 20-period Wilder ATR on completed 5m bars (a gap invalidates the chain until re-seeded)
3. trade-weighted VWAP = sum(price * BTC size) / sum(BTC size)
4. frozen 1h range box (previous COMPLETED UTC hour: high / low / VWAP), frozen at each hour
5. 4h efficiency ratio ER = |c_now - c_4h_ago| / sum |successive 5m close changes over 4h|
   (denominator 0 or any missing bar -> regime unavailable); ER <= 0.25 range, ER > 0.25 directional
   (frozen research convention, NOT an optimised parameter)
6. a bounded buffer that keeps updating after reaching its maximum length

All computations are point-in-time: a value at bar i depends only on bars with close_ms <= close_ms[i]."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from btc_swing.research.intraday_portfolio.contract import MS_1H, MS_5M

F = NDArray[np.float64]
I64 = NDArray[np.int64]
B = NDArray[np.bool_]

ER_RANGE_MAX = 0.25  # frozen convention
ER_BARS = 48  # 4 h of 5m bars
ATR_PERIOD = 20


@dataclass(frozen=True)
class Bars:
    """Completed bars on a fixed grid (open_ms strictly increasing, one row per bar actually present)."""

    open_ms: I64
    close_ms: I64
    open: F
    high: F
    low: F
    close: F
    volume: F  # BTC size
    vwap: F  # trade-weighted price of the bar (NaN when the bar had no size)
    buy: F  # taker-buy BTC size (NaN when unknown: never 0)
    sell: F
    step_ms: int

    def __len__(self) -> int:
        return len(self.open_ms)

    def contiguous(self, i0: int, i1: int) -> bool:
        """Bars i0..i1 inclusive cover every grid step (no missing interval)."""
        if i0 < 0 or i1 >= len(self) or i1 < i0:
            return False
        return bool(self.open_ms[i1] - self.open_ms[i0] == (i1 - i0) * self.step_ms)


def completed_bars(
    open_ms: I64,
    o: F,
    h: F,
    lo: F,
    c: F,
    vol: F,
    vwap: F,
    buy: F,
    sell: F,
    step_ms: int,
    now_ms: int | None = None,
) -> Bars:
    """Order, de-duplicate (first occurrence wins) and keep only bars COMPLETED at `now_ms`
    (close_ms = open_ms + step <= now_ms). Bars whose open is not aligned to the grid are refused."""
    if np.any(open_ms % step_ms != 0):
        raise ValueError("bar open not aligned to the grid")
    order = np.argsort(open_ms, kind="stable")
    om = open_ms[order]
    keep = np.ones(len(om), dtype=bool)
    keep[1:] = om[1:] != om[:-1]
    sel = order[keep]
    om = open_ms[sel]
    cm = om + step_ms
    if now_ms is not None:
        done = cm <= now_ms
        sel, om, cm = sel[done], om[done], cm[done]
    return Bars(
        om.astype(np.int64),
        cm.astype(np.int64),
        o[sel].astype(np.float64),
        h[sel].astype(np.float64),
        lo[sel].astype(np.float64),
        c[sel].astype(np.float64),
        vol[sel].astype(np.float64),
        vwap[sel].astype(np.float64),
        buy[sel].astype(np.float64),
        sell[sel].astype(np.float64),
        step_ms,
    )


def wilder_atr(bars: Bars, period: int = ATR_PERIOD) -> F:
    """ATR on completed bars; seeded with the mean of the first `period` true ranges of a CONTIGUOUS
    run, then Wilder-smoothed. A missing interval breaks the chain: NaN until `period` contiguous bars
    re-seed it. Value at i uses bars <= i only."""
    n = len(bars)
    out = np.full(n, np.nan)
    if n == 0:
        return out
    tr = np.full(n, np.nan)
    gap = np.zeros(n, dtype=bool)
    tr[0] = bars.high[0] - bars.low[0]
    gap[0] = True
    for i in range(1, n):
        if bars.open_ms[i] - bars.open_ms[i - 1] != bars.step_ms:
            # gap: the previous close is unknown; this bar starts a new chain with its own range
            tr[i], gap[i] = bars.high[i] - bars.low[i], True
            continue
        pc = bars.close[i - 1]
        tr[i] = max(bars.high[i] - bars.low[i], abs(bars.high[i] - pc), abs(bars.low[i] - pc))
    run = 0
    acc = 0.0
    atr = math.nan
    for i in range(n):
        if gap[i]:
            run, acc, atr = 0, 0.0, math.nan
        if not math.isfinite(tr[i]):
            run, acc, atr = 0, 0.0, math.nan
            continue
        run += 1
        if run <= period:
            acc += tr[i]
            if run == period:
                atr = acc / period
                out[i] = atr
            continue
        atr = (atr * (period - 1) + tr[i]) / period
        out[i] = atr
    return out


def vwap(prices: F, sizes: F) -> float:
    """sum(price * size) / sum(size); NaN when no size (never a filled-in mid)."""
    s = float(np.nansum(sizes))
    if s <= 0 or not np.all(np.isfinite(prices[np.isfinite(sizes) & (sizes > 0)])):
        return math.nan
    return float(np.nansum(prices * sizes) / s)


@dataclass(frozen=True)
class HourBox:
    hour_open_ms: (
        int  # the UTC hour the box is frozen FOR (it describes the previous completed hour)
    )
    high: float
    low: float
    vwap: float
    bars: int

    @property
    def available(self) -> bool:
        return self.bars == 12 and math.isfinite(self.high) and math.isfinite(self.vwap)


def hour_boxes(bars5: Bars) -> dict[int, HourBox]:
    """For every UTC hour H that has at least one 5m bar before it: the box of [H-1h, H) frozen at H.
    Needs all 12 completed 5m bars of that hour, else the box is recorded as unavailable."""
    out: dict[int, HourBox] = {}
    by_hour: dict[int, list[int]] = {}
    for i in range(len(bars5)):
        by_hour.setdefault(int(bars5.open_ms[i] // MS_1H * MS_1H), []).append(i)
    for h0, idx in by_hour.items():
        h_for = h0 + MS_1H
        if len(idx) != 12:
            out[h_for] = HourBox(h_for, math.nan, math.nan, math.nan, len(idx))
            continue
        ii = np.array(idx)
        out[h_for] = HourBox(
            h_for,
            float(np.max(bars5.high[ii])),
            float(np.min(bars5.low[ii])),
            vwap(bars5.vwap[ii], bars5.volume[ii]),
            12,
        )
    return out


def efficiency_ratio(bars5: Bars, n: int = ER_BARS) -> F:
    """ER at each completed 5m bar i over the previous `n` bars (contiguous required; denominator 0 or
    any NaN -> NaN = regime unavailable)."""
    m = len(bars5)
    out = np.full(m, np.nan)
    c = bars5.close
    for i in range(n, m):
        if not bars5.contiguous(i - n, i):
            continue
        seg = c[i - n : i + 1]
        if not np.all(np.isfinite(seg)):
            continue
        den = float(np.sum(np.abs(np.diff(seg))))
        if den <= 0:
            continue
        out[i] = abs(float(seg[-1] - seg[0])) / den
    return out


def regime(er: float) -> str:
    if not math.isfinite(er):
        return "UNAVAILABLE"
    return "RANGE" if er <= ER_RANGE_MAX else "DIRECTIONAL"


class BoundedBuffer[T]:
    """A deque with a maximum length that keeps accepting (and dropping the oldest) after it is full."""

    def __init__(self, maxlen: int) -> None:
        self._d: deque[T] = deque(maxlen=maxlen)
        self.appended = 0

    def push(self, x: T) -> None:
        self._d.append(x)
        self.appended += 1

    def items(self) -> list[T]:
        return list(self._d)

    def __len__(self) -> int:
        return len(self._d)


def bars_1m_to_5m(b1: Bars) -> Bars:
    """Aggregate completed 1m bars into completed 5m bars; a 5m bar is emitted ONLY when all five 1m
    bars are present (no partial aggregation). Taker split: NaN if any constituent is NaN."""
    groups: dict[int, list[int]] = {}
    for i in range(len(b1)):
        groups.setdefault(int(b1.open_ms[i] // MS_5M * MS_5M), []).append(i)
    rows = [(g, idx) for g, idx in sorted(groups.items()) if len(idx) == 5]
    n = len(rows)
    om = np.zeros(n, dtype=np.int64)
    o, h, lo, c, v, vw, bu, se = (np.full(n, np.nan) for _ in range(8))
    for k, (g, idx) in enumerate(rows):
        ii = np.array(idx)
        om[k] = g
        o[k], c[k] = b1.open[ii[0]], b1.close[ii[-1]]
        h[k], lo[k] = float(np.max(b1.high[ii])), float(np.min(b1.low[ii]))
        v[k] = float(np.sum(b1.volume[ii]))
        vw[k] = vwap(b1.vwap[ii], b1.volume[ii])
        bu[k] = float(np.sum(b1.buy[ii])) if np.all(np.isfinite(b1.buy[ii])) else np.nan
        se[k] = float(np.sum(b1.sell[ii])) if np.all(np.isfinite(b1.sell[ii])) else np.nan
    return Bars(om, om + MS_5M, o, h, lo, c, v, vw, bu, se, MS_5M)
