from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest

from btc_swing.core.enums import Timeframe
from btc_swing.ingest.normalise import parse_funding_csv, parse_kline_csv
from btc_swing.ingest.pipeline import BtcIngestor, IngestStats, count_missing_bars, normalise_klines
from btc_swing.providers.base import ArchiveFetch, ArchiveMeta, Dataset
from btc_swing.providers.synthetic import SyntheticBtcProvider
from btc_swing.storage.archive_store import ArchiveStore, ImmutabilityError
from btc_swing.storage.bar_store import BarStore


def test_kline_csv_header_and_no_header_and_microseconds() -> None:
    with_header = (
        b"open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"
        b"1704067200000,42314.00,42437.20,42289.60,42437.10,1724.210,1704067499999,73068834.13,15368,1274.871,54029686.66,0\n"
    )
    no_header_us = b"1748736000000000,104591.88,104647.11,104530.42,104530.43,44.40977,1748736299999999,4644728.54,8151,14.88668,1557076.55,0\n"
    a = parse_kline_csv(with_header)
    b = parse_kline_csv(no_header_us)
    assert a["open_time_ms"][0] == 1704067200000 and a["trades"][0] == 15368
    assert b["open_time_ms"][0] == 1748736000000  # microseconds detected and converted
    assert b["close_time_ms"][0] == 1748736299999
    assert a.columns == b.columns


def test_funding_csv() -> None:
    f = parse_funding_csv(
        b"calc_time,funding_interval_hours,last_funding_rate\n1704067200000,8,0.00037409\n"
    )
    assert f["funding_rate"][0] == pytest.approx(0.00037409)
    assert f["funding_interval_hours"][0] == 8


def test_normalise_klines_pit_columns_and_alignment() -> None:
    raw = parse_kline_csv(
        b"1704067200000,1,2,0.5,1.5,10,1704067499999,15,3,4,6,0\n"
        b"1704067500000,1.5,2,1,1.2,10,1704067799999,15,3,4,6,0\n"
    )
    df, anomalies = normalise_klines(raw, "BTCUSDT", "perp", Timeframe.M5, 2, datetime.now(UTC))
    assert anomalies == []
    assert df["close_time_ms"][0] == 1704067200000 + 300_000
    assert df["provider_available_time"][0] == df["close_time"][0].replace(
        microsecond=0
    ) + __import__("datetime").timedelta(minutes=2)
    bad = raw.with_columns(pl.col("open_time_ms") + 1)
    with pytest.raises(ValueError):
        normalise_klines(bad, "BTCUSDT", "perp", Timeframe.M5, 0, datetime.now(UTC))


def test_count_missing_bars() -> None:
    df = pl.DataFrame({"open_time_ms": [0, 300_000, 900_000]})
    assert count_missing_bars(df, Timeframe.M5) == 1


def test_ingest_is_resumable_and_raw_is_immutable(synthetic_data: dict[str, object]) -> None:
    stats = synthetic_data["stats"]
    assert isinstance(stats, IngestStats)
    assert stats.fetched > 0 and stats.skipped == 0 and stats.anomalies == []
    d = synthetic_data["dir"]
    prov = synthetic_data["provider"]
    cfg = synthetic_data["cfg"]
    assert isinstance(prov, SyntheticBtcProvider)
    again = BtcIngestor(prov, ArchiveStore(d), BarStore(d), cfg).ingest("2023-01", "2023-08")  # type: ignore[arg-type]
    assert again.fetched == 0 and again.skipped == stats.fetched
    # a different payload under an existing key must be refused
    fetch = prov.fetch(Dataset.PERP_KLINES, "BTCUSDT", Timeframe.M5, "2023-01")
    tampered = ArchiveFetch(
        meta=ArchiveMeta(**{**fetch.meta.__dict__, "sha256": "0" * 64}),
        raw_bytes=fetch.raw_bytes + b"x",
        frame=fetch.frame,
    )
    with pytest.raises(ImmutabilityError):
        ArchiveStore(d).put(tampered)  # type: ignore[arg-type]
    store = synthetic_data["store"]
    assert isinstance(store, BarStore)
    assert len(store.periods(Dataset.PERP_KLINES, "BTCUSDT", Timeframe.M5)) == 8
    assert len(store.series_hash(Dataset.PERP_KLINES, "BTCUSDT", Timeframe.M5)) == 64
