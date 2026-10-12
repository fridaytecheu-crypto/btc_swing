"""V9 inputs: the existing Bybit-native 1m dataset (never re-ingested; holdout days never read) and
the Binance funding series as a cost proxy."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from btc_swing.providers.base import Dataset
from btc_swing.storage.bar_store import BarStore
from btc_swing.v7.ingest import load_bybit_1m

DAY_MS = 86_400_000


def ms(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp() * 1000)


def iso(t_ms: float) -> str:
    return datetime.fromtimestamp(t_ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


def load_rows(data_dir: Path, start_ms: int, end_ms: int) -> pl.DataFrame:
    """1m Bybit rows in [start_ms, end_ms); `end_ms` must not exceed the recent-validation end."""
    rows = load_bybit_1m(data_dir, start_ms=start_ms, end_ms=end_ms)
    if rows.is_empty():
        return rows
    return rows.filter((pl.col("open_time_ms") >= start_ms) & (pl.col("open_time_ms") < end_ms))


def load_funding(data_dir: Path, symbol: str, start_ms: int, end_ms: int) -> pl.DataFrame:
    bs = BarStore(data_dir)
    df = bs.scan(Dataset.FUNDING, symbol, None, time_col="time_ms")
    if df.is_empty():
        return df
    return df.filter((pl.col("time_ms") >= start_ms) & (pl.col("time_ms") < end_ms)).sort("time_ms")
