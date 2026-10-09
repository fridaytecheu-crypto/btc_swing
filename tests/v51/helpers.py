"""Synthetic forward bars for V5.1 tests: a realistic 5m stream written exactly like the raw
processor writes it (zero-trade rows during a collector outage: trades 0, NaN OHLC, volume 0,
ticker fields carried forward), assembled through the real forward pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

from btc_swing.v5.forward.config import ForwardPaths, load_forward_config, load_frozen_v5
from btc_swing.v5.forward.pipeline import ForwardContext
from btc_swing.v51.config import load_v51_config

ROOT = Path(__file__).resolve().parents[2]
START = int(datetime(2026, 9, 1, 0, 0, tzinfo=UTC).timestamp() * 1000)
MS_5M = 300_000


def synthetic_bars(
    n: int, start: int = START, seed: int = 11, outages: list[tuple[int, int]] | None = None
) -> pl.DataFrame:
    rng = np.random.RandomState(seed)
    close = 80_000 + np.cumsum(rng.normal(0, 25, n))
    t = start + np.arange(n) * MS_5M
    vol = rng.uniform(100, 400, n) * (1 + 0.5 * np.sin(np.arange(n) / 100))
    buy = vol * rng.uniform(0.3, 0.7, n)
    oi = 58_000 + np.cumsum(rng.normal(0, 5, n))
    mark = close * (1 + rng.normal(0, 2e-5, n))
    index = close * (1 + rng.normal(0, 2e-5, n))
    df = pl.DataFrame(
        {
            "open_time_ms": t,
            "close_time_ms": t + MS_5M,
            "open": close + rng.normal(0, 3, n),
            "high": close + np.abs(rng.normal(15, 5, n)),
            "low": close - np.abs(rng.normal(15, 5, n)),
            "close": close,
            "volume": vol,
            "quote_volume": vol * close,
            "trades": np.full(n, 500, dtype=np.int64),
            "taker_buy_volume": buy,
            "taker_buy_quote_volume": buy * close,
            "buy_qty": buy,
            "sell_qty": vol - buy,
            "n_buy": np.full(n, 250, dtype=np.int64),
            "n_sell": np.full(n, 250, dtype=np.int64),
            "buy_notional": buy * close,
            "sell_notional": (vol - buy) * close,
            "big_buy_qty": buy * 0.2,
            "big_sell_qty": (vol - buy) * 0.2,
            "big_buy_notional_100k": buy * close * 0.1,
            "big_sell_notional_100k": (vol - buy) * close * 0.1,
            "max_trade_qty": np.full(n, 3.0),
            "vwap": close,
            "gap_filled": np.zeros(n),
            "oi_last": oi,
            "oi_value_last": oi * close,
            "mark_open": mark,
            "mark_high": mark + 10,
            "mark_low": mark - 10,
            "mark_close": mark,
            "index_open": index,
            "index_high": index + 10,
            "index_low": index - 10,
            "index_close": index,
            "next_funding_ms": ((t // 28_800_000 + 1) * 28_800_000).astype(float),
            "funding_rate_last": np.full(n, 1e-4),
        }
    )
    if outages:
        # exactly what RawProcessor writes while the collector is down: no trades, NaN prices,
        # zero volume, ticker fields carried forward (stale OI), no mark/index
        idx = np.zeros(n, dtype=bool)
        for a, b in outages:
            idx[a:b] = True
        last_oi = None
        oi_col = df["oi_last"].to_numpy().copy()
        for i in range(n):
            if idx[i] and last_oi is not None:
                oi_col[i] = last_oi
            elif not idx[i]:
                last_oi = oi_col[i]
        nan = pl.when(pl.Series(idx)).then(float("nan"))
        df = df.with_columns(
            pl.Series("oi_last", oi_col),
            *[nan.otherwise(pl.col(c)).alias(c) for c in ("open", "high", "low", "close", "vwap")],
            *[
                pl.when(pl.Series(idx)).then(0.0).otherwise(pl.col(c)).alias(c)
                for c in (
                    "volume",
                    "quote_volume",
                    "taker_buy_volume",
                    "taker_buy_quote_volume",
                    "buy_qty",
                    "sell_qty",
                    "buy_notional",
                    "sell_notional",
                    "big_buy_qty",
                    "big_sell_qty",
                    "big_buy_notional_100k",
                    "big_sell_notional_100k",
                    "max_trade_qty",
                )
            ],
            pl.when(pl.Series(idx)).then(0).otherwise(pl.col("trades")).alias("trades"),
            pl.when(pl.Series(idx)).then(0).otherwise(pl.col("n_buy")).alias("n_buy"),
            pl.when(pl.Series(idx)).then(0).otherwise(pl.col("n_sell")).alias("n_sell"),
            *[
                nan.otherwise(pl.col(c)).alias(c)
                for c in (
                    "mark_open",
                    "mark_high",
                    "mark_low",
                    "mark_close",
                    "index_open",
                    "index_high",
                    "index_low",
                    "index_close",
                )
            ],
        )
    return df


def v5_ctx(tmp: Path, bars: pl.DataFrame, start_ms: int | None = None) -> ForwardContext:
    fcfg = load_forward_config(ROOT / "config" / "btc_swing_v5_forward.yaml")
    cfg = load_frozen_v5(fcfg, ROOT)
    paths = ForwardPaths(tmp, "BTCUSDT")
    bars.write_parquet(paths.bars_dir / "all.parquet")
    s = start_ms if start_ms is not None else int(bars["close_time_ms"][0])
    return ForwardContext(fcfg, cfg, paths, s)


def v51_cfg() -> object:
    return load_v51_config(ROOT / "config" / "btc_swing_v5_1.yaml")
