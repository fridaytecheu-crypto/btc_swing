"""V5 microstructure ingestion from the Binance Vision archive (public, checksum-verified).

Datasets (all USDT-M perpetual BTCUSDT):
  * bookDepth (daily, from 2023-01-01): order-book depth at +-1..5% of mid, ~30 s snapshots.
    Raw zip retained; parquet per day.
  * indexPriceKlines 5m (monthly): index price for basis = perp close / index close - 1.
    Raw zip retained; parquet per month.
  * aggTrades (daily, full history): per-trade aggressor flag. Files are large (15-30 MB/day, ~35 GB
    for the window), so the raw zip is NOT retained: it is downloaded, sha256-verified against the
    published checksum, aggregated to immutable 5-minute flow rows and discarded. The manifest keeps
    the key, the verified sha256 and the row counts so any day can be re-derived byte for byte.

Every output row carries only information with transact/snapshot time inside its bucket, so a
5m row is complete at the bucket's close and never changes later (PIT by construction).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from btc_swing.ingest.normalise import parse_kline_csv

log = logging.getLogger(__name__)
BASE = "https://data.binance.vision/"
LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
BIG_QTY_BTC = 1.0
BIG_NOTIONAL_USD = 100_000.0
FLOW_COLUMNS = [
    "open_time_ms",
    "n_trades",
    "n_buy",
    "n_sell",
    "buy_qty",
    "sell_qty",
    "buy_notional",
    "sell_notional",
    "big_buy_qty",
    "big_sell_qty",
    "big_buy_notional_100k",
    "big_sell_notional_100k",
    "max_trade_qty",
    "vwap",
]


@dataclass
class V5IngestStats:
    fetched: int = 0
    skipped: int = 0
    rows: int = 0
    unavailable: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "fetched": self.fetched,
            "skipped": self.skipped,
            "rows": self.rows,
            "unavailable": self.unavailable[:50],
            "n_unavailable": len(self.unavailable),
            "errors": self.errors[:50],
        }


def _days(start: str, end: str) -> list[str]:
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    d = d0
    while d <= d1:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _months(start: str, end: str) -> list[str]:
    y0, m0 = int(start[:4]), int(start[5:7])
    y1, m1 = int(end[:4]), int(end[5:7])
    out = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


class V5Ingestor:
    def __init__(self, data_dir: Path, symbol: str = "BTCUSDT") -> None:
        self.data_dir = data_dir
        self.symbol = symbol
        self.raw_dir = data_dir / "btc" / "raw" / "binance_vision"
        self.ds_dir = data_dir / "btc" / "datasets"
        self.manifest = data_dir / "btc" / "raw" / "manifest_v5.jsonl"
        self.client = httpx.Client(
            timeout=httpx.Timeout(120.0, connect=30.0), follow_redirects=True
        )
        self._done: set[str] = set()
        if self.manifest.exists():
            for line in self.manifest.read_text().splitlines():
                if line.strip():
                    self._done.add(json.loads(line)["key"])

    # ------------------------------------------------------------------ download
    def _published_sha(self, key: str) -> str | None:
        r = self.client.get(BASE + key + ".CHECKSUM")
        if r.status_code != 200:
            return None
        return r.text.split()[0].strip().lower()

    def _download(self, key: str, dest: Path) -> tuple[bool, str]:
        h = hashlib.sha256()
        with self.client.stream("GET", BASE + key) as r:
            if r.status_code != 200:
                return False, ""
            with dest.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    h.update(chunk)
                    f.write(chunk)
        return True, h.hexdigest()

    def _record(self, row: dict[str, Any]) -> None:
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
        self._done.add(str(row["key"]))

    # ------------------------------------------------------------------ bookDepth
    def ingest_book_depth(self, start: str, end: str, stats: V5IngestStats) -> None:
        out_dir = self.ds_dir / "book_depth" / self.symbol / "na"
        out_dir.mkdir(parents=True, exist_ok=True)
        for day in _days(start, end):
            key = f"data/futures/um/daily/bookDepth/{self.symbol}/{self.symbol}-bookDepth-{day}.zip"
            out = out_dir / f"{day}.parquet"
            if key in self._done and out.exists():
                stats.skipped += 1
                continue
            raw = self.raw_dir / key
            raw.parent.mkdir(parents=True, exist_ok=True)
            ok, sha = self._download(key, raw)
            if not ok:
                stats.unavailable.append(key)
                raw.unlink(missing_ok=True)
                continue
            pub = self._published_sha(key)
            if pub is not None and pub != sha:
                stats.errors.append(f"checksum mismatch {key}")
                raw.unlink(missing_ok=True)
                continue
            with zipfile.ZipFile(raw) as z:
                csv_bytes = z.read(z.namelist()[0])
            df = pl.read_csv(
                io.BytesIO(csv_bytes),
                has_header=True,
                new_columns=["timestamp", "percentage", "depth", "notional"],
                infer_schema_length=0,
            )
            df = (
                df.with_columns(
                    pl.col("timestamp")
                    .str.strptime(pl.Datetime("ms"), "%Y-%m-%d %H:%M:%S")
                    .dt.replace_time_zone("UTC")
                    .dt.epoch("ms")
                    .alias("time_ms"),
                    pl.col("percentage").cast(pl.Float64).cast(pl.Int64),
                    pl.col("depth").cast(pl.Float64),
                    pl.col("notional").cast(pl.Float64),
                )
                .select("time_ms", "percentage", "depth", "notional")
                .sort("time_ms", "percentage")
            )
            df.write_parquet(out)
            self._record(
                {
                    "dataset": "book_depth",
                    "key": key,
                    "sha256": sha,
                    "published_sha256": pub,
                    "rows": df.height,
                    "raw_retained": True,
                    "fetched_at": datetime.now(UTC).isoformat(),
                }
            )
            stats.fetched += 1
            stats.rows += df.height

    # ------------------------------------------------------------------ index klines
    def ingest_index_klines(self, start: str, end: str, stats: V5IngestStats) -> None:
        out_dir = self.ds_dir / "index_klines" / self.symbol / "5m"
        out_dir.mkdir(parents=True, exist_ok=True)
        for month in _months(start, end):
            key = f"data/futures/um/monthly/indexPriceKlines/{self.symbol}/5m/{self.symbol}-5m-{month}.zip"
            out = out_dir / f"{month}.parquet"
            if key in self._done and out.exists():
                stats.skipped += 1
                continue
            raw = self.raw_dir / key
            raw.parent.mkdir(parents=True, exist_ok=True)
            ok, sha = self._download(key, raw)
            if not ok:
                stats.unavailable.append(key)
                raw.unlink(missing_ok=True)
                continue
            pub = self._published_sha(key)
            if pub is not None and pub != sha:
                stats.errors.append(f"checksum mismatch {key}")
                raw.unlink(missing_ok=True)
                continue
            with zipfile.ZipFile(raw) as z:
                csv_bytes = z.read(z.namelist()[0])
            df = parse_kline_csv(csv_bytes).select("open_time_ms", "open", "high", "low", "close")
            df.write_parquet(out)
            self._record(
                {
                    "dataset": "index_klines",
                    "key": key,
                    "sha256": sha,
                    "published_sha256": pub,
                    "rows": df.height,
                    "raw_retained": True,
                    "fetched_at": datetime.now(UTC).isoformat(),
                }
            )
            stats.fetched += 1
            stats.rows += df.height

    # ------------------------------------------------------------------ aggTrades -> 5m flow
    def ingest_aggtrades_flow(self, start: str, end: str, stats: V5IngestStats) -> None:
        out_dir = self.ds_dir / "aggtrades_flow" / self.symbol / "5m"
        out_dir.mkdir(parents=True, exist_ok=True)
        for day in _days(start, end):
            key = f"data/futures/um/daily/aggTrades/{self.symbol}/{self.symbol}-aggTrades-{day}.zip"
            out = out_dir / f"{day}.parquet"
            if key in self._done and out.exists():
                stats.skipped += 1
                continue
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td) / "a.zip"
                ok, sha = self._download(key, tmp)
                if not ok:
                    stats.unavailable.append(key)
                    continue
                pub = self._published_sha(key)
                if pub is not None and pub != sha:
                    stats.errors.append(f"checksum mismatch {key}")
                    continue
                with zipfile.ZipFile(tmp) as z:
                    csv_bytes = z.read(z.namelist()[0])
            try:
                df = aggregate_aggtrades(csv_bytes)
            except Exception as e:  # noqa: BLE001 - recorded, never silently skipped
                stats.errors.append(f"{key}: {type(e).__name__}: {e}")
                continue
            df.write_parquet(out)
            self._record(
                {
                    "dataset": "aggtrades_flow",
                    "key": key,
                    "sha256": sha,
                    "published_sha256": pub,
                    "rows_raw": int(df["n_trades"].sum()),
                    "rows": df.height,
                    "raw_retained": False,
                    "fetched_at": datetime.now(UTC).isoformat(),
                }
            )
            stats.fetched += 1
            stats.rows += df.height
            log.info(
                "aggTrades %s: %d trades -> %d 5m rows", day, int(df["n_trades"].sum()), df.height
            )


def _has_header(csv_bytes: bytes) -> bool:
    first = csv_bytes.split(b"\n", 1)[0].split(b",", 1)[0].strip()
    try:
        float(first)
    except ValueError:
        return True
    return False


def aggregate_aggtrades(csv_bytes: bytes) -> pl.DataFrame:
    """Per-trade aggTrades CSV -> 5m aggressor-flow rows (buyer is the taker when is_buyer_maker is false)."""
    cols = [
        "agg_trade_id",
        "price",
        "quantity",
        "first_trade_id",
        "last_trade_id",
        "transact_time",
        "is_buyer_maker",
    ]
    df = pl.read_csv(
        io.BytesIO(csv_bytes),
        has_header=_has_header(csv_bytes),
        new_columns=cols,
        infer_schema_length=0,
    )
    df = df.with_columns(
        pl.col("price").cast(pl.Float64),
        pl.col("quantity").cast(pl.Float64),
        pl.col("transact_time").cast(pl.Int64),
        pl.col("is_buyer_maker").str.to_lowercase().is_in(["true", "1"]).alias("maker_buy"),
    ).with_columns(
        (pl.col("transact_time") // 300_000 * 300_000).alias("open_time_ms"),
        (~pl.col("maker_buy")).alias("taker_buy"),
        (pl.col("price") * pl.col("quantity")).alias("notional"),
    )
    g = df.group_by("open_time_ms").agg(
        pl.len().alias("n_trades"),
        pl.col("taker_buy").sum().alias("n_buy"),
        (~pl.col("taker_buy")).sum().alias("n_sell"),
        pl.when(pl.col("taker_buy")).then(pl.col("quantity")).otherwise(0.0).sum().alias("buy_qty"),
        pl.when(~pl.col("taker_buy"))
        .then(pl.col("quantity"))
        .otherwise(0.0)
        .sum()
        .alias("sell_qty"),
        pl.when(pl.col("taker_buy"))
        .then(pl.col("notional"))
        .otherwise(0.0)
        .sum()
        .alias("buy_notional"),
        pl.when(~pl.col("taker_buy"))
        .then(pl.col("notional"))
        .otherwise(0.0)
        .sum()
        .alias("sell_notional"),
        pl.when(pl.col("taker_buy") & (pl.col("quantity") >= BIG_QTY_BTC))
        .then(pl.col("quantity"))
        .otherwise(0.0)
        .sum()
        .alias("big_buy_qty"),
        pl.when((~pl.col("taker_buy")) & (pl.col("quantity") >= BIG_QTY_BTC))
        .then(pl.col("quantity"))
        .otherwise(0.0)
        .sum()
        .alias("big_sell_qty"),
        pl.when(pl.col("taker_buy") & (pl.col("notional") >= BIG_NOTIONAL_USD))
        .then(pl.col("notional"))
        .otherwise(0.0)
        .sum()
        .alias("big_buy_notional_100k"),
        pl.when((~pl.col("taker_buy")) & (pl.col("notional") >= BIG_NOTIONAL_USD))
        .then(pl.col("notional"))
        .otherwise(0.0)
        .sum()
        .alias("big_sell_notional_100k"),
        pl.col("quantity").max().alias("max_trade_qty"),
        (pl.col("notional").sum() / pl.col("quantity").sum()).alias("vwap"),
    )
    return g.sort("open_time_ms").select(FLOW_COLUMNS)


def run_v5_ingest(data_dir: Path, start: str, end: str, datasets: list[str]) -> dict[str, Any]:
    ing = V5Ingestor(data_dir)
    stats = V5IngestStats()
    if "book_depth" in datasets:
        ing.ingest_book_depth(max(start, "2023-01-01"), end, stats)
    if "index_klines" in datasets:
        ing.ingest_index_klines(start, end, stats)
    if "aggtrades_flow" in datasets:
        ing.ingest_aggtrades_flow(start, end, stats)
    return stats.as_dict()


def load_v5_dataset(data_dir: Path, name: str, symbol: str = "BTCUSDT") -> pl.DataFrame:
    tf = {"book_depth": "na", "index_klines": "5m", "aggtrades_flow": "5m"}[name]
    d = data_dir / "btc" / "datasets" / name / symbol / tf
    files = sorted(d.glob("*.parquet")) if d.exists() else []
    if not files:
        return pl.DataFrame()
    return pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")
