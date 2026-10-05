"""Provider abstraction for crypto market data.

A provider delivers *archive files* (one file = one dataset / symbol / timeframe / period) as raw
bytes plus a parsed frame with canonical column names. The raw bytes are persisted immutably
BEFORE normalisation. No provider in this package
implements order placement; the execution venue is modelled, not connected.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import polars as pl

from btc_swing.core.clock import ProviderEntitlement
from btc_swing.core.enums import Timeframe


class Dataset(StrEnum):
    PERP_KLINES = "perp_klines"
    SPOT_KLINES = "spot_klines"
    FUNDING = "funding"
    METRICS = "metrics"  # open interest, long/short ratios, taker buy/sell ratio
    PREMIUM_INDEX = "premium_index"  # perpetual premium (basis proxy) klines
    MARK_PRICE = "mark_price"


@dataclass(frozen=True)
class ArchiveMeta:
    provider: str
    dataset: Dataset
    symbol: str
    timeframe: Timeframe | None
    period: str  # YYYY-MM for monthly files, YYYY-MM-DD for daily files
    key: str  # provider-relative path of the file
    filename: str
    sha256: str
    byte_size: int
    fetched_at: datetime
    published_sha256: str | None  # provider-published checksum when one exists
    checksum_verified: bool


@dataclass(frozen=True)
class ArchiveFetch:
    meta: ArchiveMeta
    raw_bytes: bytes
    frame: pl.DataFrame  # canonical columns (see btc_swing.ingest.normalise)


# Canonical raw frame columns per dataset. Timestamps are epoch milliseconds (Int64).
KLINE_RAW_COLUMNS = [
    "open_time_ms",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time_ms",
    "quote_volume",
    "trades",
    "taker_buy_volume",
    "taker_buy_quote_volume",
]
FUNDING_RAW_COLUMNS = ["time_ms", "funding_interval_hours", "funding_rate"]
METRICS_RAW_COLUMNS = [
    "time_ms",
    "open_interest",
    "open_interest_value",
    "top_trader_long_short_ratio_accounts",
    "top_trader_long_short_ratio_positions",
    "long_short_ratio_accounts",
    "taker_long_short_volume_ratio",
]


class CryptoMarketDataProvider(ABC):
    """Historical archive access. Period granularity is monthly except METRICS (daily files)."""

    name: str
    entitlement: ProviderEntitlement

    @abstractmethod
    def list_periods(self, dataset: Dataset, symbol: str, timeframe: Timeframe | None) -> list[str]:
        """Available periods (sorted ascending) for a dataset."""

    @abstractmethod
    def published_checksum(
        self, dataset: Dataset, symbol: str, timeframe: Timeframe | None, period: str
    ) -> str | None:
        """Provider-published sha256 of the archive file, or None if the provider has none."""

    @abstractmethod
    def fetch(
        self, dataset: Dataset, symbol: str, timeframe: Timeframe | None, period: str
    ) -> ArchiveFetch:
        """Download one archive file, verify it, parse it."""
