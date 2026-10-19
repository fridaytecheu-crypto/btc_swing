"""Synthetic 1m price paths for the E1 / E2 scenario tests (no market data touched)."""

from __future__ import annotations

import math

import numpy as np

from btc_swing.research.intraday_portfolio.contract import MS_1H, MS_1M
from btc_swing.research.intraday_portfolio.primitives import (
    Bars,
    bars_1m_to_5m,
    completed_bars,
    efficiency_ratio,
    hour_boxes,
    wilder_atr,
)

T0 = 1_700_000_000_000 - (1_700_000_000_000 % MS_1H)
M = 10_000.0  # mirror level for long/short symmetry


def bars_from_ohlc(rows: list[tuple[float, float, float, float]], t0: int = T0) -> Bars:
    n = len(rows)
    a = np.array(rows, dtype=np.float64)
    open_ms = (t0 + np.arange(n, dtype=np.int64) * MS_1M).astype(np.int64)
    vol = np.ones(n)
    return completed_bars(
        open_ms, a[:, 0], a[:, 1], a[:, 2], a[:, 3], vol, a[:, 3], vol * 0.5, vol * 0.5, MS_1M
    )


def mirror(
    rows: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    """Reflect every price around M: longs become shorts with identical geometry."""
    return [(2 * M - o, 2 * M - lo, 2 * M - h, 2 * M - c) for o, h, lo, c in rows]


def drop_minutes(rows: list[tuple[float, float, float, float]], t0: int, drop: set[int]) -> Bars:
    """Bars with the given minute indices removed (a missing interval)."""
    keep = [k for k in range(len(rows)) if k not in drop]
    a = np.array([rows[k] for k in keep], dtype=np.float64)
    open_ms = np.array([t0 + k * MS_1M for k in keep], dtype=np.int64)
    vol = np.ones(len(keep))
    return completed_bars(
        open_ms, a[:, 0], a[:, 1], a[:, 2], a[:, 3], vol, a[:, 3], vol * 0.5, vol * 0.5, MS_1M
    )


def features(b1: Bars) -> tuple[Bars, np.ndarray, np.ndarray, dict]:  # type: ignore[type-arg]
    b5 = bars_1m_to_5m(b1)
    return b5, wilder_atr(b5), efficiency_ratio(b5), hour_boxes(b5)


def _bar(
    o: float, c: float, h: float | None = None, lo: float | None = None
) -> tuple[float, float, float, float]:
    hh = max(o, c) + 1 if h is None else h
    ll = min(o, c) - 1 if lo is None else lo
    return (o, hh, ll, c)


def e1_long_path(
    entry_open: float = 9950.0, with_target: bool = True
) -> list[tuple[float, float, float, float]]:
    """Hours 0-5 uptrend (ER ~ 0.6, no breakout pattern), hour 6 flat box [9880, 9920], hour 7: two closes above, a retest, entry,
    then either the 2R target (default) or a drift sideways (TIME exit)."""
    rows: list[tuple[float, float, float, float]] = []
    # trend hours: closes climb 40 inside each hour and step back 9.3 at the hour boundary (net +30/h),
    # wicks +30 above the close so no two consecutive closes ever exceed the previous hour's high
    for hour in range(6):
        prev = 9700.0 + 30 * hour
        for m in range(60):
            c = 9700.0 + 30 * hour + 40.0 * m / 60
            rows.append((prev, c + 30.0, min(prev, c) - 1.0, c))
            prev = c
    # box hour: alternate 9895 / 9905 with highs 9920 / lows 9880 at fixed minutes
    for m in range(60):
        o = 9895.0 if m % 2 == 0 else 9905.0
        c = 9905.0 if m % 2 == 0 else 9895.0
        h = 9920.0 if m == 10 else max(o, c) + 1
        lo = 9880.0 if m == 20 else min(o, c) - 1
        rows.append((o, h, lo, c))
    # hour 7: bar0 (min 420-424) closes 9940, bar1 closes 9950, bar2 retest low 9918 close 9935
    for m in range(5):
        rows.append(_bar(9925.0 + m * 3, 9928.0 + m * 3))  # closes 9928..9940
    for m in range(5):
        rows.append(_bar(9942.0 + m * 2, 9944.0 + m * 2))  # closes 9944..9952 (>9920)
    rows[-1] = _bar(9950.0, 9950.0)
    rows.append(_bar(9950.0, 9940.0))
    rows.append((9940.0, 9941.0, 9918.0, 9930.0))  # retest: low crosses 9920
    rows.append(_bar(9930.0, 9932.0))
    rows.append(_bar(9932.0, 9934.0))
    rows.append(_bar(9934.0, 9935.0))  # retest 5m bar closes 9935 > 9920
    # entry minute opens at entry_open
    p = entry_open
    rows.append(_bar(p, p + 2))
    p += 2
    for _ in range(40):
        step = 4.0 if with_target else 0.0
        rows.append(_bar(p, p + step))
        p += step
    for _ in range(300):
        rows.append(_bar(p, p))
    return rows


def e2_long_path(entry_open: float = 9990.0) -> list[tuple[float, float, float, float]]:
    """Hours 0-5 sine range (ER << 0.25); hour 6 box mostly at 10040 with a 5-minute dip to 9980 (VWAP
    ~10035); hour 7: sweep to 9970, close inside, confirm bar inside with higher low, entry, VWAP exit."""
    rows: list[tuple[float, float, float, float]] = []
    prev = 10000.0
    for m in range(360):
        c = 10000.0 + 20.0 * math.sin(2 * math.pi * m / 60)
        rows.append(_bar(prev, c))
        prev = c
    for m in range(60):
        if 30 <= m < 35:
            o, c = 9985.0, 9985.0
            rows.append((o, 9990.0, 9980.0, c))
        else:
            o, c = 10040.0, 10040.0
            rows.append((o, 10044.0, 10037.0, c))
    # sweep bar (min 420-424): low 9970, close 9985 inside
    rows.append((10038.0, 10039.0, 10010.0, 10010.0))
    rows.append((10010.0, 10011.0, 9990.0, 9992.0))
    rows.append((9992.0, 9993.0, 9970.0, 9975.0))
    rows.append((9975.0, 9982.0, 9974.0, 9981.0))
    rows.append((9981.0, 9986.0, 9980.0, 9985.0))
    # confirmation bar (min 425-429): inside, low 9975 >= 9970, close 9990
    rows.append((9985.0, 9987.0, 9975.0, 9980.0))
    rows.append((9980.0, 9984.0, 9979.0, 9983.0))
    rows.append((9983.0, 9988.0, 9982.0, 9987.0))
    rows.append((9987.0, 9991.0, 9986.0, 9989.0))
    rows.append((9989.0, 9991.0, 9988.0, 9990.0))
    # entry minute
    p = entry_open
    rows.append(_bar(p, p + 3))
    p += 3
    for _ in range(25):
        rows.append(_bar(p, p + 3))
        p += 3
    for _ in range(300):
        rows.append(_bar(p, p))
    return rows
