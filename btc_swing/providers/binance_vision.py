"""Binance public historical archive (https://data.binance.vision).

Why the archive and not the REST API: the REST endpoints (api/fapi.binance.com) are geo-blocked
from several jurisdictions (HTTP 451), while the archive is a plain static file store that
publishes a SHA256 CHECKSUM beside every file. Files are immutable once published, which suits
a point-in-time research store. Monthly zips exist for klines, fundingRate, premiumIndexKlines
and markPriceKlines; `metrics` (open interest, long/short ratios, taker ratios) exist as daily
zips only. No authenticated endpoint is ever touched by this module.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import polars as pl

from btc_swing.core.clock import ProviderEntitlement
from btc_swing.core.enums import Timeframe
from btc_swing.ingest.normalise import parse_funding_csv, parse_kline_csv, parse_metrics_csv
from btc_swing.providers.base import (
    ArchiveFetch,
    ArchiveMeta,
    CryptoMarketDataProvider,
    Dataset,
)

log = logging.getLogger(__name__)

BASE_URL = "https://data.binance.vision/"
LIST_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
PROVIDER_NAME = "binance_vision"

_KEY_RE = re.compile(r"<Key>(.*?)</Key>")
_TRUNC_RE = re.compile(r"<IsTruncated>(true|false)</IsTruncated>")
_MARKER_RE = re.compile(r"<NextMarker>(.*?)</NextMarker>")


def default_entitlement(latency_minutes: int = 0) -> ProviderEntitlement:
    return ProviderEntitlement(
        provider=PROVIDER_NAME,
        plan="public_archive",
        delay=timedelta(minutes=latency_minutes),
        history_years=None,
    )


def _prefix(dataset: Dataset, symbol: str, tf: Timeframe | None) -> tuple[str, str, bool]:
    """(directory prefix, filename stem prefix, is_daily)."""
    if dataset is Dataset.PERP_KLINES:
        assert tf is not None
        return (
            f"data/futures/um/monthly/klines/{symbol}/{tf.value}/",
            f"{symbol}-{tf.value}-",
            False,
        )
    if dataset is Dataset.SPOT_KLINES:
        assert tf is not None
        return f"data/spot/monthly/klines/{symbol}/{tf.value}/", f"{symbol}-{tf.value}-", False
    if dataset is Dataset.FUNDING:
        return f"data/futures/um/monthly/fundingRate/{symbol}/", f"{symbol}-fundingRate-", False
    if dataset is Dataset.METRICS:
        return f"data/futures/um/daily/metrics/{symbol}/", f"{symbol}-metrics-", True
    if dataset is Dataset.PREMIUM_INDEX:
        assert tf is not None
        return (
            f"data/futures/um/monthly/premiumIndexKlines/{symbol}/{tf.value}/",
            f"{symbol}-{tf.value}-",
            False,
        )
    if dataset is Dataset.MARK_PRICE:
        assert tf is not None
        return (
            f"data/futures/um/monthly/markPriceKlines/{symbol}/{tf.value}/",
            f"{symbol}-{tf.value}-",
            False,
        )
    raise ValueError(dataset)


class BinanceVisionProvider(CryptoMarketDataProvider):
    name = PROVIDER_NAME

    def __init__(
        self,
        entitlement: ProviderEntitlement | None = None,
        transport: Callable[[str], bytes] | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.entitlement = entitlement or default_entitlement()
        self._timeout = timeout
        self._get = transport or self._http_get

    # ----------------------------------------------------------------- transport
    def _http_get(self, url: str) -> bytes:
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.content

    # ----------------------------------------------------------------- listing
    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        marker: str | None = None
        while True:
            url = f"{LIST_URL}?delimiter=/&prefix={prefix}"
            if marker:
                url += f"&marker={marker}"
            xml = self._get(url).decode("utf-8")
            page = _KEY_RE.findall(xml)
            keys.extend(page)
            trunc = _TRUNC_RE.search(xml)
            if not trunc or trunc.group(1) != "true" or not page:
                break
            nm = _MARKER_RE.search(xml)
            marker = nm.group(1) if nm else page[-1]
        return sorted(keys)

    def list_periods(self, dataset: Dataset, symbol: str, timeframe: Timeframe | None) -> list[str]:
        prefix, stem, _daily = _prefix(dataset, symbol, timeframe)
        out: list[str] = []
        for k in self.list_keys(prefix):
            fn = k.rsplit("/", 1)[-1]
            if fn.endswith(".zip") and fn.startswith(stem):
                out.append(fn[len(stem) : -4])
        return sorted(out)

    # ----------------------------------------------------------------- fetch
    def _key(self, dataset: Dataset, symbol: str, tf: Timeframe | None, period: str) -> str:
        prefix, stem, _ = _prefix(dataset, symbol, tf)
        return f"{prefix}{stem}{period}.zip"

    def published_checksum(
        self, dataset: Dataset, symbol: str, timeframe: Timeframe | None, period: str
    ) -> str | None:
        key = self._key(dataset, symbol, timeframe, period)
        try:
            text = self._get(BASE_URL + key + ".CHECKSUM").decode("utf-8")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        return text.split()[0].lower() if text.strip() else None

    def fetch(
        self, dataset: Dataset, symbol: str, timeframe: Timeframe | None, period: str
    ) -> ArchiveFetch:
        key = self._key(dataset, symbol, timeframe, period)
        published = self.published_checksum(dataset, symbol, timeframe, period)
        raw = self._get(BASE_URL + key)
        sha = hashlib.sha256(raw).hexdigest()
        if published is not None and published != sha:
            raise RuntimeError(f"checksum mismatch for {key}: published {published} got {sha}")
        frame = parse_archive_zip(dataset, raw)
        meta = ArchiveMeta(
            provider=self.name,
            dataset=dataset,
            symbol=symbol,
            timeframe=timeframe,
            period=period,
            key=key,
            filename=key.rsplit("/", 1)[-1],
            sha256=sha,
            byte_size=len(raw),
            fetched_at=datetime.now(UTC),
            published_sha256=published,
            checksum_verified=published is not None,
        )
        return ArchiveFetch(meta=meta, raw_bytes=raw, frame=frame)


def parse_archive_zip(dataset: Dataset, raw: bytes) -> pl.DataFrame:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"expected exactly one csv in archive, got {names}")
        csv_bytes = zf.read(names[0])
    if dataset in (
        Dataset.PERP_KLINES,
        Dataset.SPOT_KLINES,
        Dataset.PREMIUM_INDEX,
        Dataset.MARK_PRICE,
    ):
        return parse_kline_csv(csv_bytes)
    if dataset is Dataset.FUNDING:
        return parse_funding_csv(csv_bytes)
    if dataset is Dataset.METRICS:
        return parse_metrics_csv(csv_bytes)
    raise ValueError(dataset)
