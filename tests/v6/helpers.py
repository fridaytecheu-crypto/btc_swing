"""Synthetic inputs for the V6 tests: a 5m random walk with aggTrades-style flow, 5-min OI
metrics, 8h funding and premium klines, plus deterministic pattern injection helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

from btc_swing.v6.config import V6Config, load_v6_config
from btc_swing.v6.features import V6Inputs

ROOT = Path(__file__).resolve().parents[2]
START = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
MS_5M = 300_000


def cfg() -> V6Config:
    return load_v6_config(ROOT / "config" / "btc_swing_v6.yaml")


def synthetic_inputs(
    n_days: int = 40,
    seed: int = 3,
    start: int = START,
    gaps: list[tuple[int, int]] | None = None,
    price: np.ndarray | None = None,
    volume: np.ndarray | None = None,
    buy_share: np.ndarray | None = None,
    oi: np.ndarray | None = None,
) -> V6Inputs:
    n = n_days * 288
    rng = np.random.RandomState(seed)
    t = start + np.arange(n) * MS_5M
    close = price if price is not None else 60_000 + np.cumsum(rng.normal(0, 60, n))
    o = np.concatenate([[close[0]], close[:-1]])
    spread = np.abs(rng.normal(35, 10, n))
    high = np.maximum(o, close) + spread
    low = np.minimum(o, close) - spread
    vol = (
        volume
        if volume is not None
        else rng.uniform(80, 300, n) * (1 + 0.4 * np.sin(2 * np.pi * np.arange(n) / 288))
    )
    bs = buy_share if buy_share is not None else rng.uniform(0.4, 0.6, n)
    buy = vol * bs
    trades = np.full(n, 400, dtype=np.int64)
    bars = pl.DataFrame(
        {
            "open_time_ms": t,
            "open": o,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
            "trades": trades,
            "taker_buy_volume": buy,
        }
    )
    if gaps:
        keep = np.ones(n, dtype=bool)
        for a, b in gaps:
            keep[a:b] = False
        bars = bars.filter(pl.Series(keep))
    flow = pl.DataFrame(
        {
            "open_time_ms": t,
            "buy_qty": buy,
            "sell_qty": vol - buy,
            "big_buy_qty": buy * 0.2,
            "big_sell_qty": (vol - buy) * 0.2,
        }
    )
    oi_arr = oi if oi is not None else 50_000 + np.cumsum(rng.normal(0, 4, n))
    metrics = pl.DataFrame({"time_ms": t, "open_interest": oi_arr})
    ft = np.arange(start, start + n * MS_5M, 8 * 3_600_000)
    funding = pl.DataFrame({"time_ms": ft, "funding_rate": np.full(len(ft), 1e-4)})
    premium = pl.DataFrame({"open_time_ms": t, "close": rng.normal(1e-4, 5e-5, n)})
    return V6Inputs(bars, flow, metrics, funding, premium, {"synthetic": "1"})
