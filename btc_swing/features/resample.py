"""Point-in-time resampling of 5m bars into higher timeframes.

A higher-timeframe bar is formed from the 5m bars whose open falls in [bucket_open, bucket_close).
It is COMPLETE at `bucket_close` and never earlier: the engine exposes a 4h bar to a decision at
T only if bucket_close <= T. A bucket whose nominal close lies beyond the last available 5m bar
is a partial bar and is dropped here (it would otherwise leak future information into
indicators). Missing 5m bars inside a bucket (exchange downtime) do not delay the bar: the venue
still closes the candle at its nominal time.
"""

from __future__ import annotations

import polars as pl

from btc_swing.core.enums import Timeframe
from btc_swing.core.timeframes import tf_ms

OHLCV_COLS = ["open_time_ms", "close_time_ms", "open", "high", "low", "close", "volume"]


def resample_completed(bars_5m: pl.DataFrame, tf: Timeframe, last_close_ms: int) -> pl.DataFrame:
    """Aggregate 5m bars into `tf` bars, keeping only bars complete at `last_close_ms`.

    `bars_5m` must contain open_time_ms, open, high, low, close, volume (sorted ascending).
    Extra flow columns (quote_volume, trades, taker_buy_volume) are summed when present.
    """
    if tf is Timeframe.M5:
        out = bars_5m.filter(pl.col("open_time_ms") + tf_ms(tf) <= last_close_ms)
        return out.select([c for c in [*OHLCV_COLS, *_flow_cols(bars_5m)] if c in out.columns])
    step = tf_ms(tf)
    aggs = [
        pl.col("open").first(),
        pl.col("high").max(),
        pl.col("low").min(),
        pl.col("close").last(),
        pl.col("volume").sum(),
        pl.len().alias("n_bars"),
    ]
    aggs += [pl.col(c).sum() for c in _flow_cols(bars_5m)]
    df = (
        bars_5m.sort("open_time_ms")
        .with_columns((pl.col("open_time_ms") // step * step).alias("bucket"))
        .group_by("bucket", maintain_order=True)
        .agg(aggs)
        .rename({"bucket": "open_time_ms"})
        .with_columns((pl.col("open_time_ms") + step).alias("close_time_ms"))
        .filter(pl.col("close_time_ms") <= last_close_ms)
    )
    cols = [*OHLCV_COLS, "n_bars", *_flow_cols(bars_5m)]
    return df.select(cols)


def _flow_cols(df: pl.DataFrame) -> list[str]:
    return [
        c
        for c in ("quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume")
        if c in df.columns
    ]
