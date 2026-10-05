"""CSV parsing for Binance-format archive files into canonical raw frames.

Binance quirks handled explicitly:
  * futures files carry a header row, spot files do not;
  * spot timestamps switched from milliseconds to MICROSECONDS on 2025-01-01 — detected per file
    by magnitude and converted to milliseconds;
  * kline close_time is the last millisecond of the bar (open + tf - 1 ms); it is kept raw here and
    the canonical `close_time` (= instant the bar is complete) is derived in `pipeline`.
"""

from __future__ import annotations

import io

import polars as pl

from btc_swing.providers.base import FUNDING_RAW_COLUMNS, KLINE_RAW_COLUMNS, METRICS_RAW_COLUMNS

_US_THRESHOLD = 10**14  # epoch ms values are ~1.7e12; epoch us values are ~1.7e15


def _has_header(csv_bytes: bytes) -> bool:
    first = csv_bytes.split(b"\n", 1)[0].split(b",", 1)[0].strip()
    try:
        float(first)
    except ValueError:
        return True
    return False


def _to_ms(col: str) -> pl.Expr:
    c = pl.col(col).cast(pl.Int64)
    return pl.when(c > _US_THRESHOLD).then(c // 1000).otherwise(c).alias(col)


def parse_kline_csv(csv_bytes: bytes) -> pl.DataFrame:
    df = pl.read_csv(
        io.BytesIO(csv_bytes),
        has_header=_has_header(csv_bytes),
        new_columns=[*KLINE_RAW_COLUMNS, "ignore"],
        infer_schema_length=0,
    )
    floats = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "taker_buy_volume",
        "taker_buy_quote_volume",
    ]
    df = df.with_columns(
        pl.col("open_time_ms").cast(pl.Int64),
        pl.col("close_time_ms").cast(pl.Int64),
        pl.col("trades").cast(pl.Float64).cast(pl.Int64),
        *[pl.col(c).cast(pl.Float64) for c in floats],
    ).with_columns(_to_ms("open_time_ms"), _to_ms("close_time_ms"))
    return df.select(KLINE_RAW_COLUMNS).sort("open_time_ms")


def parse_funding_csv(csv_bytes: bytes) -> pl.DataFrame:
    df = pl.read_csv(
        io.BytesIO(csv_bytes),
        has_header=_has_header(csv_bytes),
        new_columns=FUNDING_RAW_COLUMNS,
        infer_schema_length=0,
    )
    return (
        df.with_columns(
            pl.col("time_ms").cast(pl.Int64),
            pl.col("funding_interval_hours").cast(pl.Float64).cast(pl.Int64),
            pl.col("funding_rate").cast(pl.Float64),
        )
        .with_columns(
            _to_ms("time_ms"),
        )
        .select(FUNDING_RAW_COLUMNS)
        .sort("time_ms")
    )


def parse_metrics_csv(csv_bytes: bytes) -> pl.DataFrame:
    df = pl.read_csv(
        io.BytesIO(csv_bytes),
        has_header=_has_header(csv_bytes),
        new_columns=["create_time", "symbol", *METRICS_RAW_COLUMNS[1:]],
        infer_schema_length=0,
        null_values=[""],
    )
    return (
        df.with_columns(
            pl.col("create_time")
            .str.strptime(pl.Datetime("ms"), "%Y-%m-%d %H:%M:%S")
            .dt.replace_time_zone("UTC")
            .dt.epoch("ms")
            .alias("time_ms"),
            *[pl.col(c).cast(pl.Float64, strict=False) for c in METRICS_RAW_COLUMNS[1:]],
        )
        .select(METRICS_RAW_COLUMNS)
        .sort("time_ms")
    )
