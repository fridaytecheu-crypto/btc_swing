"""Warm-up seed from the Bybit PUBLIC trading archive (https://public.bybit.com/trading/<SYMBOL>/).

Daily CSV.gz files of every trade (timestamp, side = taker side, size, price) are aggregated into
the same 5-minute bar + taker-flow rows the forward processor produces, for the N days before the
observation start. They warm up price indicators (ATR, EMA, 4H trend) and flow z-scores from the
SAME venue; open interest, funding and basis have no public history and warm up forward only.
Every archive file's sha256 is recorded; the raw files are kept."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from btc_swing.v5.forward.raw import BIG_NOTIONAL_100K, BIG_TRADE_QTY, MS_5M

log = logging.getLogger(__name__)
BASE = "https://public.bybit.com/trading/"


def aggregate_bybit_trades(csv_gz: bytes) -> pl.DataFrame:
    with gzip.open(io.BytesIO(csv_gz), "rb") as f:
        df = pl.read_csv(f.read(), columns=["timestamp", "side", "size", "price"])
    df = df.with_columns(
        (pl.col("timestamp").cast(pl.Float64) * 1000).cast(pl.Int64).alias("ts_ms"),
        pl.col("size").cast(pl.Float64),
        pl.col("price").cast(pl.Float64),
    ).sort("ts_ms")
    df = df.with_columns(
        (pl.col("ts_ms") // MS_5M * MS_5M).alias("open_time_ms"),
        (pl.col("side") == "Buy").alias("is_buy"),
        (pl.col("size") * pl.col("price")).alias("notional"),
    )
    big = pl.col("size") >= BIG_TRADE_QTY
    big_n = pl.col("notional") >= BIG_NOTIONAL_100K
    buy, sell = pl.col("is_buy"), ~pl.col("is_buy")
    out = (
        df.group_by("open_time_ms")
        .agg(
            pl.col("price").first().alias("open"),
            pl.col("price").max().alias("high"),
            pl.col("price").min().alias("low"),
            pl.col("price").last().alias("close"),
            pl.col("size").sum().alias("volume"),
            pl.col("notional").sum().alias("quote_volume"),
            pl.len().cast(pl.Int64).alias("trades"),
            pl.col("size").filter(buy).sum().alias("taker_buy_volume"),
            pl.col("notional").filter(buy).sum().alias("taker_buy_quote_volume"),
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
        )
        .with_columns(
            (pl.col("open_time_ms") + MS_5M).alias("close_time_ms"),
            (pl.col("quote_volume") / pl.col("volume")).alias("vwap"),
            pl.lit(0.0).alias("gap_filled"),
            pl.lit("bybit_archive").alias("source"),
        )
        .sort("open_time_ms")
    )
    return out.fill_null(0.0)


class BybitSeedIngestor:
    def __init__(self, seed_dir: Path, symbol: str) -> None:
        self.seed_dir = seed_dir
        self.symbol = symbol
        self.raw_dir = seed_dir / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.manifest = seed_dir / "seed_manifest.jsonl"
        self.client = httpx.Client(
            timeout=httpx.Timeout(300.0, connect=30.0), follow_redirects=True
        )
        self._done: dict[str, dict[str, Any]] = {}
        if self.manifest.exists():
            for line in self.manifest.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    self._done[r["day"]] = r

    def ingest_days(self, days: list[str]) -> dict[str, Any]:
        stats: dict[str, Any] = {"fetched": 0, "skipped": 0, "unavailable": [], "rows": 0}
        for day in days:
            out = self.seed_dir / "days" / f"{day}.parquet"
            if day in self._done and out.exists():
                stats["skipped"] += 1
                continue
            url = f"{BASE}{self.symbol}/{self.symbol}{day}.csv.gz"
            r = self.client.get(url)
            if r.status_code != 200:
                stats["unavailable"].append(day)
                continue
            raw = self.raw_dir / f"{self.symbol}{day}.csv.gz"
            raw.write_bytes(r.content)
            sha = hashlib.sha256(r.content).hexdigest()
            df = aggregate_bybit_trades(r.content)
            out.parent.mkdir(parents=True, exist_ok=True)
            df.write_parquet(out)
            rec = {
                "day": day,
                "url": url,
                "sha256": sha,
                "bytes": len(r.content),
                "rows": df.height,
                "fetched_at": datetime.now(UTC).isoformat(),
            }
            with self.manifest.open("a") as f:
                f.write(json.dumps(rec, sort_keys=True) + "\n")
            self._done[day] = rec
            stats["fetched"] += 1
            stats["rows"] += df.height
            log.info("seed %s: %d bars", day, df.height)
        return stats


def seed_days(start_ms: int, days_before: int) -> list[str]:
    start = datetime.fromtimestamp(start_ms / 1000, tz=UTC).date()
    return [(start - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(days_before, 0, -1)]


def load_seed_bars(seed_dir: Path, before_ms: int | None = None) -> pl.DataFrame:
    d = seed_dir / "days"
    files = sorted(d.glob("*.parquet")) if d.exists() else []
    if not files:
        return pl.DataFrame()
    df = pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed").sort("open_time_ms")
    if before_ms is not None:
        df = df.filter(pl.col("close_time_ms") <= before_ms)
    return df.unique(subset=["open_time_ms"], keep="first", maintain_order=True)


def seed_summary(seed_dir: Path) -> dict[str, Any]:
    df = load_seed_bars(seed_dir)
    if df.is_empty():
        return {"bars": 0}
    return {
        "bars": df.height,
        "first": datetime.fromtimestamp(int(df["open_time_ms"].min()) / 1000, tz=UTC).isoformat(),  # type: ignore[arg-type]
        "last": datetime.fromtimestamp(int(df["close_time_ms"].max()) / 1000, tz=UTC).isoformat(),  # type: ignore[arg-type]
        "days": int(df.select((pl.col("open_time_ms") // 86_400_000).n_unique()).item()),
        "mean_volume_per_bar": float(df["volume"].mean() or math.nan),  # type: ignore[arg-type]
    }
