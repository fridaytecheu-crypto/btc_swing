"""Bybit-native 5m dataset from the Bybit public trade archive (`public.bybit.com/trading/<symbol>/`).

One gzip CSV per UTC day (tick trades: timestamp s, symbol, side = taker side, size BTC, price,
tickDirection, trdMatchID, grossValue, homeNotional, foreignNotional). The file is streamed, its
sha256 recorded, aggregated to immutable 5-minute rows and discarded (the raw archive is not
retained; the manifest keeps key, sha256, byte size and row counts so any day can be re-derived
byte for byte). Every 5m row contains only trades with timestamp inside its bucket, so a row is
complete at the bucket close and never changes later (PIT by construction). Days inside the locked
historical holdout (2025-07-01 onward) are refused by default."""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl

log = logging.getLogger(__name__)
BASE = "https://public.bybit.com/trading/"
DATASET = "bybit_trades_5m"
MS_5M = 300_000
BIG_QTY_BTC = 1.0  # single print >= 1 BTC (same pre-declared definition as the V5 Binance flow)
BIG_NOTIONAL_USD = 100_000.0
HOLDOUT_START = date(2025, 7, 1)  # locked once-only window: never downloaded without the flag
COLUMNS = [
    "open_time_ms", "close_time_ms", "open", "high", "low", "close", "volume", "quote_volume",
    "n_trades", "n_buy", "n_sell", "buy_qty", "sell_qty", "buy_notional", "sell_notional",
    "big_buy_qty", "big_sell_qty", "big_buy_notional_100k", "big_sell_notional_100k",
    "max_trade_qty", "avg_trade_qty", "vwap", "first_trade_ms", "last_trade_ms",
]  # fmt: skip


@dataclass
class BybitIngestStats:
    fetched: int = 0
    skipped: int = 0
    rows: int = 0
    trades: int = 0
    bytes: int = 0
    unavailable: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "fetched": self.fetched,
            "skipped": self.skipped,
            "rows": self.rows,
            "trades": self.trades,
            "bytes": self.bytes,
            "unavailable": self.unavailable[:50],
            "n_unavailable": len(self.unavailable),
            "errors": self.errors[:50],
        }


def days_between(start: str, end_inclusive: str) -> list[str]:
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end_inclusive)
    out = []
    d = d0
    while d <= d1:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def aggregate_trades(df: pl.DataFrame, day: str) -> pl.DataFrame:
    """Tick trades of one UTC day -> 288 (at most) immutable 5m rows. Side = taker side."""
    d0 = int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)
    d = (
        df.with_columns((pl.col("timestamp") * 1000.0).round(0).cast(pl.Int64).alias("ms"))
        .filter((pl.col("ms") >= d0) & (pl.col("ms") < d0 + 86_400_000))
        .with_columns(
            ((pl.col("ms") // MS_5M) * MS_5M).alias("open_time_ms"),
            (pl.col("side") == "Buy").alias("is_buy"),
            (pl.col("size") * pl.col("price")).alias("notional"),
        )
        .sort("ms", maintain_order=True)
    )
    big = pl.col("size") >= BIG_QTY_BTC
    big_n = pl.col("notional") >= BIG_NOTIONAL_USD
    buy, sell = pl.col("is_buy"), ~pl.col("is_buy")
    agg = d.group_by("open_time_ms", maintain_order=True).agg(
        pl.col("price").first().alias("open"),
        pl.col("price").max().alias("high"),
        pl.col("price").min().alias("low"),
        pl.col("price").last().alias("close"),
        pl.col("size").sum().alias("volume"),
        pl.col("notional").sum().alias("quote_volume"),
        pl.len().cast(pl.Int64).alias("n_trades"),
        buy.sum().cast(pl.Int64).alias("n_buy"),
        sell.sum().cast(pl.Int64).alias("n_sell"),
        pl.col("size").filter(buy).sum().alias("buy_qty"),
        pl.col("size").filter(sell).sum().alias("sell_qty"),
        pl.col("notional").filter(buy).sum().alias("buy_notional"),
        pl.col("notional").filter(sell).sum().alias("sell_notional"),
        pl.col("size").filter(buy & big).sum().alias("big_buy_qty"),
        pl.col("size").filter(sell & big).sum().alias("big_sell_qty"),
        pl.col("notional").filter(buy & big_n).sum().alias("big_buy_notional_100k"),
        pl.col("notional").filter(sell & big_n).sum().alias("big_sell_notional_100k"),
        pl.col("size").max().alias("max_trade_qty"),
        pl.col("size").mean().alias("avg_trade_qty"),
        (pl.col("notional").sum() / pl.col("size").sum()).alias("vwap"),
        pl.col("ms").min().alias("first_trade_ms"),
        pl.col("ms").max().alias("last_trade_ms"),
    )
    agg = agg.with_columns((pl.col("open_time_ms") + MS_5M).alias("close_time_ms"))
    return agg.select(COLUMNS).sort("open_time_ms")


class BybitTradeIngestor:
    def __init__(self, data_dir: Path, symbol: str = "BTCUSDT", prefetch: int = 3) -> None:
        self.data_dir = data_dir
        self.symbol = symbol
        self.out_dir = data_dir / "btc" / "datasets" / DATASET / symbol / "5m"
        self.manifest = data_dir / "btc" / "raw" / f"manifest_{DATASET}.jsonl"
        self.client = httpx.Client(
            timeout=httpx.Timeout(600.0, connect=60.0), follow_redirects=True
        )
        self.prefetch = max(1, prefetch)
        self._done: dict[str, dict[str, Any]] = {}
        if self.manifest.exists():
            for line in self.manifest.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._done[str(row["day"])] = row

    def key(self, day: str) -> str:
        return f"{self.symbol}/{self.symbol}{day}.csv.gz"

    def _download(self, day: str) -> tuple[Path | None, str, int]:
        url = BASE + self.key(day)
        h = hashlib.sha256()
        n = 0
        tmp = Path(tempfile.mkstemp(prefix=f"bybit_{day}_", suffix=".csv.gz")[1])
        with self.client.stream("GET", url) as r:
            if r.status_code != 200:
                tmp.unlink(missing_ok=True)
                return None, "", 0
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    h.update(chunk)
                    n += len(chunk)
                    f.write(chunk)
        return tmp, h.hexdigest(), n

    def _record(self, row: dict[str, Any]) -> None:
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
        self._done[str(row["day"])] = row

    def ingest(
        self, start: str, end_inclusive: str, stats: BybitIngestStats, allow_holdout: bool = False
    ) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        days = []
        for day in days_between(start, end_inclusive):
            if date.fromisoformat(day) >= HOLDOUT_START and not allow_holdout:
                raise RuntimeError(
                    f"{day} lies in the locked historical holdout (>= {HOLDOUT_START}); refused"
                )
            out = self.out_dir / f"{day}.parquet"
            if day in self._done and out.exists():
                stats.skipped += 1
                continue
            days.append(day)
        with ThreadPoolExecutor(max_workers=self.prefetch) as pool:
            futures = {d: pool.submit(self._download, d) for d in days[: self.prefetch]}
            nxt = self.prefetch
            for day in days:
                tmp, sha, nbytes = futures.pop(day).result()
                if nxt < len(days):
                    futures[days[nxt]] = pool.submit(self._download, days[nxt])
                    nxt += 1
                if tmp is None:
                    stats.unavailable.append(day)
                    continue
                try:
                    df = pl.read_csv(
                        tmp,
                        columns=["timestamp", "side", "size", "price"],
                        schema_overrides={
                            "timestamp": pl.Float64,
                            "size": pl.Float64,
                            "price": pl.Float64,
                        },
                    )
                    agg = aggregate_trades(df, day)
                    out = self.out_dir / f"{day}.parquet"
                    tmp_out = out.with_suffix(".parquet.tmp")
                    agg.write_parquet(tmp_out, compression="zstd")
                    tmp_out.replace(out)
                    self._record(
                        {
                            "dataset": DATASET,
                            "day": day,
                            "key": self.key(day),
                            "sha256": sha,
                            "bytes": nbytes,
                            "trades": df.height,
                            "rows": agg.height,
                            "raw_retained": False,
                            "fetched_at": datetime.now(UTC).isoformat(),
                        }
                    )
                    stats.fetched += 1
                    stats.rows += agg.height
                    stats.trades += df.height
                    stats.bytes += nbytes
                    log.info(
                        "%s: %d trades -> %d rows (%.1f MB)",
                        day,
                        df.height,
                        agg.height,
                        nbytes / 1e6,
                    )
                except Exception as e:
                    stats.errors.append(f"{day}: {e!r}")
                    log.exception("failed %s", day)
                finally:
                    tmp.unlink(missing_ok=True)


def load_bybit_5m(data_dir: Path, symbol: str = "BTCUSDT") -> pl.DataFrame:
    d = data_dir / "btc" / "datasets" / DATASET / symbol / "5m"
    files = sorted(d.glob("*.parquet")) if d.exists() else []
    if not files:
        return pl.DataFrame()
    df = pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")
    return df.sort("open_time_ms").unique(
        subset=["open_time_ms"], keep="first", maintain_order=True
    )


def dataset_hash(data_dir: Path, symbol: str = "BTCUSDT") -> str:
    """Stable hash of the manifest (day -> archive sha256) for provenance."""
    m = data_dir / "btc" / "raw" / f"manifest_{DATASET}.jsonl"
    h = hashlib.sha256()
    if m.exists():
        rows = sorted(json.loads(x) for x in m.read_text().splitlines() if x.strip())
        for r in rows:
            h.update(f"{r['day']}:{r['sha256']}\n".encode())
    return h.hexdigest()
