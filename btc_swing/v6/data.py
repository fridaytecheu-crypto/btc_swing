"""V6 input loading from the immutable Binance Vision datasets already on disk (no network)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from btc_swing.core.enums import Timeframe
from btc_swing.providers.base import Dataset
from btc_swing.storage.bar_store import BarStore
from btc_swing.v5.ingest import load_v5_dataset
from btc_swing.v6.config import V6Config
from btc_swing.v6.features import DAY_MS, V6Inputs


def ms(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp() * 1000)


def iso(t_ms: float) -> str:
    return datetime.fromtimestamp(t_ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


def load_v6_inputs(cfg: V6Config, data_dir: Path, start_ms: int, end_ms: int) -> V6Inputs:
    """Rows from `start - warmup_days` (feature warm-up) to `end_exclusive`; nothing later is read."""
    bs = BarStore(data_dir)
    sym = cfg.instrument.symbol
    lo = start_ms - cfg.research.warmup_days * DAY_MS

    def _win(df: pl.DataFrame, col: str) -> pl.DataFrame:
        if df.is_empty():
            return df
        return df.filter((pl.col(col) >= lo) & (pl.col(col) < end_ms))

    bars = _win(
        bs.scan(Dataset.PERP_KLINES, sym, Timeframe.M5, time_col="open_time_ms"), "open_time_ms"
    )
    funding = _win(bs.scan(Dataset.FUNDING, sym, None, time_col="time_ms"), "time_ms")
    metrics = _win(bs.scan(Dataset.METRICS, sym, None, time_col="time_ms"), "time_ms")
    premium = _win(
        bs.scan(Dataset.PREMIUM_INDEX, sym, Timeframe.M5, time_col="open_time_ms"), "open_time_ms"
    )
    flow = _win(load_v5_dataset(data_dir, "aggtrades_flow", sym), "open_time_ms")
    hashes = {
        "perp_klines_5m": bs.series_hash(Dataset.PERP_KLINES, sym, Timeframe.M5),
        "funding": bs.series_hash(Dataset.FUNDING, sym, None),
        "metrics": bs.series_hash(Dataset.METRICS, sym, None),
        "premium_index_5m": bs.series_hash(Dataset.PREMIUM_INDEX, sym, Timeframe.M5),
    }
    return V6Inputs(bars, flow, metrics, funding, premium, hashes)
