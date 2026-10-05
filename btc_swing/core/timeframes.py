"""Timeframe arithmetic on UTC epoch milliseconds.

Conventions (identical to the exchange's): a bar is identified by its OPEN time; it is complete
at `close_time = open_time + tf_ms`. Higher-timeframe bars are aligned to UTC midnight (daily)
and to multiples of their length since the epoch (15m, 1h, 4h), which matches Binance.
A bar is visible to a decision at time T only if close_time <= T.
"""

from __future__ import annotations

from datetime import UTC, datetime

from btc_swing.core.enums import TF_MINUTES, Timeframe

MS_PER_MIN = 60_000


def tf_ms(tf: Timeframe) -> int:
    return TF_MINUTES[tf] * MS_PER_MIN


def floor_open(ts_ms: int, tf: Timeframe) -> int:
    """Open time of the bar of timeframe `tf` that contains instant ts_ms."""
    step = tf_ms(tf)
    return (ts_ms // step) * step


def close_of(open_ms: int, tf: Timeframe) -> int:
    return open_ms + tf_ms(tf)


def ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


def dt_to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        raise ValueError("naive datetime not allowed")
    return int(dt.timestamp() * 1000)


def bars_per_day(tf: Timeframe) -> int:
    return 1440 // TF_MINUTES[tf]
