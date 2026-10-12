from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from btc_swing.v61.features import normalise_binance, normalise_bybit
from btc_swing.v61.ingest import COLUMNS, BybitTradeIngestor, aggregate_trades, days_between


def _ticks() -> pl.DataFrame:
    # 2024-03-05 00:00:00 UTC = 1709596800; trades across the first two 5m buckets + one outside the day
    ts = [1709596800.1, 1709596801.5, 1709596999.9, 1709597100.0, 1709597150.0, 1709683200.5]
    side = ["Buy", "Sell", "Buy", "Sell", "Buy", "Buy"]
    size = [0.5, 1.2, 0.001, 2.0, 0.3, 9.0]
    price = [68000.0, 68010.0, 67990.0, 68100.0, 68050.0, 70000.0]
    return pl.DataFrame({"timestamp": ts, "side": side, "size": size, "price": price})


def test_aggregate_is_bucket_exact_and_taker_sided() -> None:
    a = aggregate_trades(_ticks(), "2024-03-05")
    assert a.columns == COLUMNS
    assert a.height == 2  # the trade on the next day is excluded
    r0 = a.row(0, named=True)
    assert r0["open_time_ms"] == 1709596800000 and r0["close_time_ms"] == 1709597100000
    assert (
        r0["open"] == 68000.0
        and r0["close"] == 67990.0
        and r0["high"] == 68010.0
        and r0["low"] == 67990.0
    )
    assert np.isclose(r0["volume"], 1.701) and r0["n_trades"] == 3
    assert np.isclose(r0["buy_qty"], 0.501) and np.isclose(r0["sell_qty"], 1.2)
    assert r0["n_buy"] == 2 and r0["n_sell"] == 1
    assert np.isclose(r0["big_sell_qty"], 1.2) and r0["big_buy_qty"] == 0.0  # >= 1 BTC prints only
    assert np.isclose(r0["vwap"], (0.5 * 68000 + 1.2 * 68010 + 0.001 * 67990) / 1.701)
    assert r0["first_trade_ms"] == 1709596800100 and r0["last_trade_ms"] == 1709596999900
    assert np.isclose(r0["avg_trade_qty"], 1.701 / 3)
    r1 = a.row(1, named=True)
    assert r1["open_time_ms"] == 1709597100000 and r1["n_trades"] == 2


def test_holdout_days_refused(tmp_path) -> None:
    ing = BybitTradeIngestor.__new__(BybitTradeIngestor)
    ing.data_dir = tmp_path
    ing.symbol = "BTCUSDT"
    ing.out_dir = tmp_path / "x"
    ing.manifest = tmp_path / "m.jsonl"
    ing.prefetch = 1
    ing._done = {}
    from btc_swing.v61.ingest import BybitIngestStats

    with pytest.raises(RuntimeError, match="locked historical holdout"):
        ing.ingest("2025-06-30", "2025-07-01", BybitIngestStats())
    assert days_between("2024-01-30", "2024-02-02") == [
        "2024-01-30",
        "2024-01-31",
        "2024-02-01",
        "2024-02-02",
    ]


def test_normalisers_share_one_schema() -> None:
    a = aggregate_trades(_ticks(), "2024-03-05")
    u = normalise_bybit(a)
    k = pl.DataFrame(
        {
            "open_time_ms": [1709596800000],
            "open": [1.0],
            "high": [2.0],
            "low": [0.5],
            "close": [1.5],
            "volume": [10.0],
            "trades": [4],
            "taker_buy_volume": [6.0],
        }
    )
    b = normalise_binance(k, pl.DataFrame())
    assert u.columns == b.columns
    assert b["buy_qty"][0] == 6.0 and b["sell_qty"][0] == 4.0 and b["avg_trade_qty"][0] == 2.5
