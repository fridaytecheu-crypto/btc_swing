"""Resumable ingestion of archive files into immutable raw storage and Parquet bar datasets.

For each (dataset, period): read the provider-published checksum; if the stored partition was
built from a source file with the same sha256, skip. Otherwise download, verify, persist the raw
file immutably, normalise and write the partition. Gaps in the bar series are counted and
reported, never filled.

Canonical bar columns (PIT):
  open_time_ms / open_time       bar open (UTC)
  close_time_ms / close_time     instant the bar is COMPLETE (= open + timeframe); Binance's raw
                                 `close_time` (open + tf - 1 ms) is validated against it
  provider_available_time        close_time + provider latency (DEPLOYMENT_AS_OF visibility)
A decision at T may use a bar only if close_time <= T (MARKET_AS_OF) or
provider_available_time <= T (DEPLOYMENT_AS_OF).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl

from btc_swing.core.config import BtcStrategyConfig
from btc_swing.core.enums import Timeframe
from btc_swing.core.timeframes import tf_ms
from btc_swing.providers.base import ArchiveFetch, CryptoMarketDataProvider, Dataset
from btc_swing.storage.archive_store import ArchiveStore
from btc_swing.storage.bar_store import BarStore

log = logging.getLogger(__name__)

BAR_COLUMNS = [
    "symbol",
    "market",
    "timeframe",
    "open_time_ms",
    "close_time_ms",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trades",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "open_time",
    "close_time",
    "provider_available_time",
    "ingested_at",
]


@dataclass
class IngestStats:
    fetched: int = 0
    skipped: int = 0
    rows: int = 0
    partitions: int = 0
    missing_bars: dict[str, int] = field(default_factory=dict)
    anomalies: list[str] = field(default_factory=list)
    checksum_unverified: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "fetched": self.fetched,
            "skipped": self.skipped,
            "rows": self.rows,
            "partitions": self.partitions,
            "missing_bars": dict(self.missing_bars),
            "anomalies": list(self.anomalies),
            "checksum_unverified": self.checksum_unverified,
        }


def months_between(start: str, end: str) -> list[str]:
    """Inclusive YYYY-MM list."""
    y0, m0 = (int(x) for x in start.split("-"))
    y1, m1 = (int(x) for x in end.split("-"))
    out: list[str] = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def days_in_month(month: str) -> list[str]:
    y, m = (int(x) for x in month.split("-"))
    d = date(y, m, 1)
    out: list[str] = []
    while d.month == m:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def normalise_klines(
    raw: pl.DataFrame,
    symbol: str,
    market: str,
    tf: Timeframe,
    latency_minutes: int,
    ingested_at: datetime,
) -> tuple[pl.DataFrame, list[str]]:
    """Raw kline frame -> canonical bar frame. Returns (frame, anomalies)."""
    step = tf_ms(tf)
    anomalies: list[str] = []
    df = raw.unique(subset=["open_time_ms"], keep="last", maintain_order=True).sort("open_time_ms")
    if df.height != raw.height:
        anomalies.append(f"{raw.height - df.height} duplicate open_time rows dropped")
    misaligned = df.filter(pl.col("open_time_ms") % step != 0).height
    if misaligned:
        raise ValueError(f"{misaligned} bars not aligned to {tf.value} boundaries")
    bad_close = df.filter(
        (pl.col("close_time_ms") - pl.col("open_time_ms") - (step - 1)).abs() > 1
    ).height
    if bad_close:
        anomalies.append(f"{bad_close} bars whose raw close_time != open_time + tf - 1ms")
    bad_ohlc = df.filter(
        (pl.col("high") < pl.col("low"))
        | (pl.col("high") < pl.col("open"))
        | (pl.col("high") < pl.col("close"))
        | (pl.col("low") > pl.col("open"))
        | (pl.col("low") > pl.col("close"))
    ).height
    if bad_ohlc:
        anomalies.append(f"{bad_ohlc} bars with inconsistent OHLC")
    lat_ms = latency_minutes * 60_000
    out = df.with_columns(
        pl.lit(symbol).alias("symbol"),
        pl.lit(market).alias("market"),
        pl.lit(tf.value).alias("timeframe"),
        (pl.col("open_time_ms") + step).alias("close_time_ms"),
    ).with_columns(
        pl.from_epoch("open_time_ms", time_unit="ms")
        .dt.replace_time_zone("UTC")
        .alias("open_time"),
        pl.from_epoch("close_time_ms", time_unit="ms")
        .dt.replace_time_zone("UTC")
        .alias("close_time"),
        pl.from_epoch(pl.col("close_time_ms") + lat_ms, time_unit="ms")
        .dt.replace_time_zone("UTC")
        .alias("provider_available_time"),
        pl.lit(ingested_at).dt.replace_time_zone("UTC").alias("ingested_at"),
    )
    return out.select(BAR_COLUMNS), anomalies


def count_missing_bars(df: pl.DataFrame, tf: Timeframe) -> int:
    if df.height < 2:
        return 0
    t = df["open_time_ms"]
    span = int(t[-1]) - int(t[0])
    expected = span // tf_ms(tf) + 1
    return max(0, expected - df.height)


def normalise_funding(
    raw: pl.DataFrame, symbol: str, latency_minutes: int, ingested_at: datetime
) -> pl.DataFrame:
    lat_ms = latency_minutes * 60_000
    return (
        raw.unique(subset=["time_ms"], keep="last", maintain_order=True)
        .sort("time_ms")
        .with_columns(
            pl.lit(symbol).alias("symbol"),
            pl.from_epoch("time_ms", time_unit="ms").dt.replace_time_zone("UTC").alias("time"),
            pl.from_epoch(pl.col("time_ms") + lat_ms, time_unit="ms")
            .dt.replace_time_zone("UTC")
            .alias("provider_available_time"),
            pl.lit(ingested_at).dt.replace_time_zone("UTC").alias("ingested_at"),
        )
    )


def normalise_metrics(
    raw: pl.DataFrame, symbol: str, latency_minutes: int, ingested_at: datetime
) -> pl.DataFrame:
    return normalise_funding(raw, symbol, latency_minutes, ingested_at)


class BtcIngestor:
    def __init__(
        self,
        provider: CryptoMarketDataProvider,
        archive: ArchiveStore,
        bars: BarStore,
        cfg: BtcStrategyConfig,
    ) -> None:
        self.provider = provider
        self.archive = archive
        self.bars = bars
        self.cfg = cfg
        self.latency = cfg.data.latency_minutes

    def plan(self) -> list[tuple[Dataset, str, Timeframe | None]]:
        c = self.cfg
        sym = c.instrument.symbol
        items: list[tuple[Dataset, str, Timeframe | None]] = [
            (Dataset.PERP_KLINES, sym, c.data.base_timeframe)
        ]
        items += [(Dataset.PERP_KLINES, sym, tf) for tf in c.data.ingest_native_timeframes]
        if c.data.ingest_funding:
            items.append((Dataset.FUNDING, sym, None))
        if c.data.ingest_metrics:
            items.append((Dataset.METRICS, sym, None))
        if c.data.ingest_premium_index:
            items.append((Dataset.PREMIUM_INDEX, sym, c.data.base_timeframe))
        if c.data.ingest_mark_price:
            items.append((Dataset.MARK_PRICE, sym, c.data.base_timeframe))
        if c.data.ingest_spot and c.instrument.spot_symbol:
            items.append((Dataset.SPOT_KLINES, c.instrument.spot_symbol, c.data.base_timeframe))
        return items

    def ingest(self, start_month: str, end_month: str) -> IngestStats:
        stats = IngestStats()
        for dataset, symbol, tf in self.plan():
            months = months_between(start_month, end_month)
            periods = (
                [d for m in months for d in days_in_month(m)]
                if dataset is Dataset.METRICS
                else months
            )
            available = set(self.provider.list_periods(dataset, symbol, tf))
            for period in periods:
                if period not in available:
                    stats.anomalies.append(
                        f"{dataset.value}/{tf.value if tf else 'na'}/{period}: not published"
                    )
                    continue
                self._ingest_one(dataset, symbol, tf, period, stats)
        return stats

    def _ingest_one(
        self, dataset: Dataset, symbol: str, tf: Timeframe | None, period: str, stats: IngestStats
    ) -> None:
        published = self.provider.published_checksum(dataset, symbol, tf, period)
        have = self.bars.partition_source_sha(dataset, symbol, tf, period)
        if published is not None and have == published:
            stats.skipped += 1
            return
        fetch = self.provider.fetch(dataset, symbol, tf, period)
        if have == fetch.meta.sha256:
            stats.skipped += 1
            return
        if not fetch.meta.checksum_verified:
            stats.checksum_unverified += 1
        self.archive.put(fetch)
        stats.fetched += 1
        self._write(fetch, dataset, symbol, tf, period, stats)

    def _write(
        self,
        fetch: ArchiveFetch,
        dataset: Dataset,
        symbol: str,
        tf: Timeframe | None,
        period: str,
        stats: IngestStats,
    ) -> None:
        now = datetime.now(UTC)
        label = f"{dataset.value}/{tf.value if tf else 'na'}/{period}"
        if dataset in (
            Dataset.PERP_KLINES,
            Dataset.SPOT_KLINES,
            Dataset.PREMIUM_INDEX,
            Dataset.MARK_PRICE,
        ):
            assert tf is not None
            market = "spot" if dataset is Dataset.SPOT_KLINES else "perp"
            df, anomalies = normalise_klines(fetch.frame, symbol, market, tf, self.latency, now)
            stats.anomalies.extend(f"{label}: {a}" for a in anomalies)
            miss = count_missing_bars(df, tf)
            if miss:
                stats.missing_bars[label] = miss
        elif dataset is Dataset.FUNDING:
            df = normalise_funding(fetch.frame, symbol, self.latency, now)
        else:
            df = normalise_metrics(fetch.frame, symbol, self.latency, now)
        self.bars.write_partition(
            dataset, symbol, tf, period, df, fetch.meta.sha256, self.provider.name, self.latency
        )
        stats.rows += df.height
        stats.partitions += 1
