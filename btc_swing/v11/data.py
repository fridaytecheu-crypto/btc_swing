"""V11 inputs (read-only, holdout-capped): the Bybit-native 1m dataset (V7 ingest) and the Binance
5m open-interest / funding series as a PIT proxy (availability = time + latency). No row at or after
`windows.data_cap` is ever loaded, although the Binance series on disk extend further."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from btc_swing.providers.base import Dataset
from btc_swing.storage.bar_store import BarStore
from btc_swing.v7.ingest import load_bybit_1m
from btc_swing.v11.config import V11Config

DAY_MS = 86_400_000


def ms(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp() * 1000)


def iso(t_ms: float | int | None) -> str | None:
    return None if t_ms is None else datetime.fromtimestamp(t_ms / 1000, tz=UTC).isoformat()


@dataclass
class V11Inputs:
    rows: pl.DataFrame  # 1m Bybit rows
    metrics: pl.DataFrame  # Binance 5m OI (time_ms, open_interest), may be empty
    funding: pl.DataFrame  # Binance funding (time_ms, funding_rate), may be empty
    start_ms: int
    end_ms: int


def load_inputs(cfg: V11Config, data_dir: Path, start_ms: int, end_ms: int) -> V11Inputs:
    cap = ms(cfg.windows.data_cap)
    if end_ms > cap:
        raise RuntimeError(f"V11 never loads rows at or after the data cap {cfg.windows.data_cap}")
    lo = start_ms - cfg.windows.warmup_days * DAY_MS
    rows = load_bybit_1m(data_dir, cfg.symbol, start_ms=lo, end_ms=end_ms)
    if not rows.is_empty():
        rows = rows.filter((pl.col("open_time_ms") >= lo) & (pl.col("open_time_ms") < end_ms))
    bs = BarStore(data_dir)
    met = bs.scan(Dataset.METRICS, cfg.symbol, None, time_col="time_ms")
    fnd = bs.scan(Dataset.FUNDING, cfg.symbol, None, time_col="time_ms")
    if not met.is_empty():
        met = met.filter((pl.col("time_ms") >= lo) & (pl.col("time_ms") < end_ms)).select(
            ["time_ms", "open_interest"]
        )
    if not fnd.is_empty():
        fnd = fnd.filter((pl.col("time_ms") >= lo) & (pl.col("time_ms") < end_ms)).select(
            ["time_ms", "funding_rate"]
        )
    return V11Inputs(rows, met, fnd, start_ms, end_ms)
