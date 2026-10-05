"""Deterministic synthetic BTC perpetual data for tests and plumbing validation.

The generator emits Binance-format CSV bytes (so the real parsing path is exercised) for a
seeded regime-switching price path: trending up, ranging, trending down, high-volatility bursts.
It proves the pipeline, never the strategy.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

from btc_swing.core.clock import ProviderEntitlement
from btc_swing.core.enums import Timeframe
from btc_swing.core.timeframes import tf_ms
from btc_swing.ingest.pipeline import months_between
from btc_swing.providers.base import (
    KLINE_RAW_COLUMNS,
    ArchiveFetch,
    ArchiveMeta,
    CryptoMarketDataProvider,
    Dataset,
)
from btc_swing.providers.binance_vision import parse_archive_zip

PROVIDER_NAME = "synthetic_btc"
_SEGMENT_BARS = 288 * 12  # 12 days per regime segment
_REGIMES = [  # (drift per bar in log terms, sigma per bar)
    ("trend_up", 0.00012, 0.0022),
    ("range", 0.0, 0.0016),
    ("trend_down", -0.00012, 0.0024),
    ("high_vol", 0.0, 0.0055),
    ("trend_up", 0.00010, 0.0020),
    ("range", 0.0, 0.0014),
]


def _month_bounds_ms(month: str) -> tuple[int, int]:
    y, m = (int(x) for x in month.split("-"))
    start = datetime(y, m, 1, tzinfo=UTC)
    end = datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=UTC)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


class SyntheticBtcProvider(CryptoMarketDataProvider):
    name = PROVIDER_NAME

    def __init__(
        self, start_month: str, end_month: str, seed: int = 7, start_price: float = 30_000.0
    ) -> None:
        self.entitlement = ProviderEntitlement(
            PROVIDER_NAME, "synthetic", timedelta(minutes=0), None
        )
        self.months = months_between(start_month, end_month)
        self._bars5 = self._generate(seed, start_price)
        self._funding = self._generate_funding(seed)

    # ----------------------------------------------------------------- generation
    def _generate(self, seed: int, p0: float) -> pl.DataFrame:
        t0, _ = _month_bounds_ms(self.months[0])
        _, t1 = _month_bounds_ms(self.months[-1])
        step = tf_ms(Timeframe.M5)
        n = (t1 - t0) // step
        rng = np.random.RandomState(seed)
        drift = np.zeros(n)
        sigma = np.zeros(n)
        for i in range(n):
            _name, mu, sg = _REGIMES[(i // _SEGMENT_BARS) % len(_REGIMES)]
            drift[i], sigma[i] = mu, sg
        z = rng.standard_normal(n)
        logret = drift + sigma * z
        close = p0 * np.exp(np.cumsum(logret))
        open_ = np.concatenate([[p0], close[:-1]])
        wick = np.abs(rng.standard_normal(n)) * sigma * close * 0.6
        high = np.maximum(open_, close) + wick
        low = np.minimum(open_, close) - np.abs(rng.standard_normal(n)) * sigma * close * 0.6
        vol = np.exp(rng.normal(6.0, 0.5, n)) * (1 + 40 * np.abs(logret))
        taker = vol * np.clip(0.5 + 8 * logret + rng.normal(0, 0.05, n), 0.05, 0.95)
        open_ms = t0 + np.arange(n) * step
        return pl.DataFrame(
            {
                "open_time_ms": open_ms,
                "open": np.round(open_, 1),
                "high": np.round(high, 1),
                "low": np.round(low, 1),
                "close": np.round(close, 1),
                "volume": np.round(vol, 3),
                "close_time_ms": open_ms + step - 1,
                "quote_volume": np.round(vol * close, 2),
                "trades": (vol * 10).astype(np.int64),
                "taker_buy_volume": np.round(taker, 3),
                "taker_buy_quote_volume": np.round(taker * close, 2),
            }
        )

    def _generate_funding(self, seed: int) -> pl.DataFrame:
        rng = np.random.RandomState(seed + 1)
        t0, _ = _month_bounds_ms(self.months[0])
        _, t1 = _month_bounds_ms(self.months[-1])
        step = 8 * 3_600_000
        times = np.arange(t0, t1, step)
        rates = np.round(0.0001 + rng.normal(0, 0.00015, len(times)), 8)
        return pl.DataFrame(
            {
                "time_ms": times,
                "funding_interval_hours": np.full(len(times), 8, dtype=np.int64),
                "funding_rate": rates,
            }
        )

    def resampled(self, tf: Timeframe) -> pl.DataFrame:
        """Plain OHLCV aggregation of the generated 5m bars (used as native-bar stand-in)."""
        step = tf_ms(tf)
        df = self._bars5.with_columns((pl.col("open_time_ms") // step * step).alias("bucket"))
        return (
            df.group_by("bucket", maintain_order=True)
            .agg(
                pl.col("open").first(),
                pl.col("high").max(),
                pl.col("low").min(),
                pl.col("close").last(),
                pl.col("volume").sum(),
                pl.col("quote_volume").sum(),
                pl.col("trades").sum(),
                pl.col("taker_buy_volume").sum(),
                pl.col("taker_buy_quote_volume").sum(),
            )
            .rename({"bucket": "open_time_ms"})
            .with_columns((pl.col("open_time_ms") + step - 1).alias("close_time_ms"))
            .select(KLINE_RAW_COLUMNS)
        )

    # ----------------------------------------------------------------- provider API
    def list_periods(self, dataset: Dataset, symbol: str, timeframe: Timeframe | None) -> list[str]:
        if dataset in (Dataset.PERP_KLINES, Dataset.FUNDING):
            return list(self.months)
        return []

    def published_checksum(
        self, dataset: Dataset, symbol: str, timeframe: Timeframe | None, period: str
    ) -> str | None:
        return None  # synthetic archives publish no checksum; the pipeline verifies by content

    def _csv(self, dataset: Dataset, tf: Timeframe | None, period: str) -> bytes:
        lo, hi = _month_bounds_ms(period)
        if dataset is Dataset.PERP_KLINES:
            assert tf is not None
            src = self._bars5 if tf is Timeframe.M5 else self.resampled(tf)
            df = src.filter((pl.col("open_time_ms") >= lo) & (pl.col("open_time_ms") < hi))
            df = df.with_columns(pl.lit(0).alias("ignore")).rename(
                {"open_time_ms": "open_time", "close_time_ms": "close_time", "trades": "count"}
            )
        elif dataset is Dataset.FUNDING:
            df = self._funding.filter((pl.col("time_ms") >= lo) & (pl.col("time_ms") < hi)).rename(
                {"time_ms": "calc_time", "funding_rate": "last_funding_rate"}
            )
        else:
            raise ValueError(dataset)
        buf = io.BytesIO()
        df.write_csv(buf)
        return buf.getvalue()

    def fetch(
        self, dataset: Dataset, symbol: str, timeframe: Timeframe | None, period: str
    ) -> ArchiveFetch:
        csv = self._csv(dataset, timeframe, period)
        stem = f"{symbol}-{timeframe.value if timeframe else 'fundingRate'}-{period}"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            info = zipfile.ZipInfo(f"{stem}.csv", date_time=(2020, 1, 1, 0, 0, 0))
            zf.writestr(info, csv)
        raw = buf.getvalue()
        key = f"synthetic/{dataset.value}/{symbol}/{timeframe.value if timeframe else 'na'}/{stem}.zip"
        meta = ArchiveMeta(
            provider=self.name,
            dataset=dataset,
            symbol=symbol,
            timeframe=timeframe,
            period=period,
            key=key,
            filename=f"{stem}.zip",
            sha256=hashlib.sha256(raw).hexdigest(),
            byte_size=len(raw),
            fetched_at=datetime.now(UTC),
            published_sha256=None,
            checksum_verified=False,
        )
        return ArchiveFetch(meta=meta, raw_bytes=raw, frame=parse_archive_zip(dataset, raw))
