"""Parquet datasets for normalised bars/funding/metrics with a JSON manifest per series.

Layout: `<data_dir>/btc/datasets/<dataset>/<symbol>/<timeframe>/<period>.parquet` plus
`manifest.json` recording row count, content hash, the sha256 of the SOURCE archive file (for
resumable ingestion: a period whose source hash is unchanged is skipped), provider and latency.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from btc_swing.core.enums import Timeframe
from btc_swing.providers.base import Dataset


class BarStore:
    def __init__(self, data_dir: Path) -> None:
        self.root = Path(data_dir) / "btc" / "datasets"

    # ----------------------------------------------------------------- paths
    def series_dir(self, dataset: Dataset, symbol: str, tf: Timeframe | None) -> Path:
        return self.root / dataset.value / symbol / (tf.value if tf else "na")

    def _manifest_path(self, dataset: Dataset, symbol: str, tf: Timeframe | None) -> Path:
        return self.series_dir(dataset, symbol, tf) / "manifest.json"

    def manifest(self, dataset: Dataset, symbol: str, tf: Timeframe | None) -> dict[str, Any]:
        p = self._manifest_path(dataset, symbol, tf)
        if not p.exists():
            return {}
        return dict(json.loads(p.read_text()))

    def _write_manifest(
        self, dataset: Dataset, symbol: str, tf: Timeframe | None, m: dict[str, Any]
    ) -> None:
        p = self._manifest_path(dataset, symbol, tf)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(m, sort_keys=True, indent=1))
        tmp.replace(p)

    # ----------------------------------------------------------------- write
    def partition_source_sha(
        self, dataset: Dataset, symbol: str, tf: Timeframe | None, period: str
    ) -> str | None:
        e = self.manifest(dataset, symbol, tf).get(period)
        return str(e["source_sha256"]) if e else None

    def write_partition(
        self,
        dataset: Dataset,
        symbol: str,
        tf: Timeframe | None,
        period: str,
        df: pl.DataFrame,
        source_sha256: str,
        provider: str,
        latency_minutes: int,
    ) -> dict[str, Any]:
        d = self.series_dir(dataset, symbol, tf)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{period}.parquet"
        tmp = d / f"{period}.parquet.tmp"
        df.write_parquet(tmp, compression="zstd")
        tmp.replace(path)
        entry = {
            "rows": df.height,
            "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source_sha256": source_sha256,
            "provider": provider,
            "latency_minutes": latency_minutes,
            "ingested_at": datetime.now(UTC).isoformat(),
        }
        m = self.manifest(dataset, symbol, tf)
        m[period] = entry
        self._write_manifest(dataset, symbol, tf, m)
        return entry

    # ----------------------------------------------------------------- read
    def periods(self, dataset: Dataset, symbol: str, tf: Timeframe | None) -> list[str]:
        return sorted(self.manifest(dataset, symbol, tf))

    def scan(
        self,
        dataset: Dataset,
        symbol: str,
        tf: Timeframe | None,
        periods: list[str] | None = None,
        time_col: str = "open_time_ms",
    ) -> pl.DataFrame:
        d = self.series_dir(dataset, symbol, tf)
        want = periods if periods is not None else self.periods(dataset, symbol, tf)
        paths = [d / f"{p}.parquet" for p in want if (d / f"{p}.parquet").exists()]
        if not paths:
            return pl.DataFrame()
        df = pl.concat([pl.read_parquet(p) for p in paths], how="vertical")
        return df.unique(subset=[time_col], keep="last", maintain_order=True).sort(time_col)

    def series_hash(self, dataset: Dataset, symbol: str, tf: Timeframe | None) -> str:
        """Stable hash of the stored series (content hashes of all partitions in order)."""
        m = self.manifest(dataset, symbol, tf)
        h = hashlib.sha256()
        for period in sorted(m):
            h.update(f"{period}:{m[period]['content_sha256']}\n".encode())
        return h.hexdigest()
