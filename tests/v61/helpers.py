"""Synthetic V6.1 inputs: unified 5m rows (Bybit-shaped) plus Binance-shaped auxiliary series."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

from btc_swing.v61.config import V61Config, load_v61_config
from btc_swing.v61.features import V61Inputs

ROOT = Path(__file__).resolve().parents[2]
START = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
MS_5M = 300_000


def cfg() -> V61Config:
    return load_v61_config(ROOT / "config" / "btc_swing_v6_1.yaml")


def synthetic_inputs(
    n_days: int = 45,
    seed: int = 3,
    start: int = START,
    price: np.ndarray | None = None,
    volume: np.ndarray | None = None,
    buy_share: np.ndarray | None = None,
    gaps: list[tuple[int, int]] | None = None,
) -> V61Inputs:
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
    trades = np.full(n, 400.0)
    bars = pl.DataFrame(
        {
            "open_time_ms": t,
            "open": o,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
            "trades": trades,
            "buy_qty": buy,
            "sell_qty": vol - buy,
            "big_buy_qty": buy * 0.2,
            "big_sell_qty": (vol - buy) * 0.2,
            "avg_trade_qty": vol / trades,
        }
    )
    if gaps:
        keep = np.ones(n, dtype=bool)
        for a, b in gaps:
            keep[a:b] = False
        bars = bars.filter(pl.Series(keep))
    metrics = pl.DataFrame({"time_ms": t, "open_interest": 50_000 + np.cumsum(rng.normal(0, 4, n))})
    ft = np.arange(start, start + n * MS_5M, 8 * 3_600_000)
    funding = pl.DataFrame({"time_ms": ft, "funding_rate": np.full(len(ft), 1e-4)})
    premium = pl.DataFrame({"open_time_ms": t, "close": rng.normal(1e-4, 5e-5, n)})
    return V61Inputs(bars, metrics, funding, premium, "synthetic", {"synthetic": "1"})
