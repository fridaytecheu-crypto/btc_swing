"""1-minute microstructure bars from the Bybit public trade archive (second streaming pass).

Same provenance discipline as `btc_swing.v61.ingest` (streamed, sha256, raw not retained, holdout days
refused). Adds per-day trade-size quantiles and LARGE-TRADE aggressor volumes at three point-in-time
thresholds: the threshold applied to day D is the median of the previous 7 days' quantile, so no trade
of day D influences its own threshold (days without 7 prior days carry NaN large-trade columns)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import polars as pl

from btc_swing.v61.ingest import BASE, days_between

log = logging.getLogger(__name__)
DATASET = "bybit_trades_1m"
MS_1M = 60_000
HOLDOUT_START = date(2025, 7, 1)
QUANTILES = (0.5, 0.9, 0.95, 0.975, 0.99)
LARGE_KEYS = ("q90", "q95", "q975")
LARGE_Q = {"q90": 0.9, "q95": 0.95, "q975": 0.975}
THRESHOLD_DAYS = 7
BASE_COLUMNS = [
    "open_time_ms", "close_time_ms", "open", "high", "low", "close", "volume", "quote_volume",
    "n_trades", "n_buy", "n_sell", "buy_qty", "sell_qty", "buy_notional", "sell_notional",
    "max_trade_qty", "vwap",
]  # fmt: skip
COLUMNS = (
    BASE_COLUMNS
    + [f"large_{side}_{k}" for k in LARGE_KEYS for side in ("buy", "sell")]
    + [f"n_large_{k}" for k in LARGE_KEYS]
)


@dataclass
class V7IngestStats:
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


def day_quantiles(sizes: np.ndarray) -> dict[str, float]:
    if len(sizes) == 0:
        return {f"p{q}": float("nan") for q in QUANTILES}
    qs = np.quantile(sizes, QUANTILES)
    return {f"p{q}": float(v) for q, v in zip(QUANTILES, qs, strict=True)}


def pit_thresholds(history: list[dict[str, float]]) -> dict[str, float]:
    """Thresholds for the next day from the previous days' quantiles (median over THRESHOLD_DAYS)."""
    out: dict[str, float] = {}
    recent = history[-THRESHOLD_DAYS:]
    for key, q in LARGE_Q.items():
        vals = [h[f"p{q}"] for h in recent if not np.isnan(h[f"p{q}"])]
        out[key] = float(np.median(vals)) if len(vals) == THRESHOLD_DAYS else float("nan")
    return out


def aggregate_1m(df: pl.DataFrame, day: str, thresholds: dict[str, float]) -> pl.DataFrame:
    d0 = int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)
    d = (
        df.with_columns((pl.col("timestamp") * 1000.0).round(0).cast(pl.Int64).alias("ms"))
        .filter((pl.col("ms") >= d0) & (pl.col("ms") < d0 + 86_400_000))
        .with_columns(
            ((pl.col("ms") // MS_1M) * MS_1M).alias("open_time_ms"),
            (pl.col("side") == "Buy").alias("is_buy"),
            (pl.col("size") * pl.col("price")).alias("notional"),
        )
        .sort("ms", maintain_order=True)
    )
    buy, sell = pl.col("is_buy"), ~pl.col("is_buy")
    aggs = [
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
        pl.col("size").max().alias("max_trade_qty"),
        (pl.col("notional").sum() / pl.col("size").sum()).alias("vwap"),
    ]
    for k in LARGE_KEYS:
        thr = thresholds.get(k, float("nan"))
        if np.isnan(thr):
            aggs += [
                pl.lit(float("nan")).alias(f"large_buy_{k}"),
                pl.lit(float("nan")).alias(f"large_sell_{k}"),
                pl.lit(float("nan")).alias(f"n_large_{k}"),
            ]
        else:
            big = pl.col("size") >= thr
            aggs += [
                pl.col("size").filter(buy & big).sum().alias(f"large_buy_{k}"),
                pl.col("size").filter(sell & big).sum().alias(f"large_sell_{k}"),
                big.sum().cast(pl.Float64).alias(f"n_large_{k}"),
            ]
    agg = d.group_by("open_time_ms", maintain_order=True).agg(aggs)
    agg = agg.with_columns((pl.col("open_time_ms") + MS_1M).alias("close_time_ms"))
    return agg.select(COLUMNS).sort("open_time_ms")


class BybitTrade1mIngestor:
    def __init__(self, data_dir: Path, symbol: str = "BTCUSDT", prefetch: int = 2) -> None:
        self.data_dir = data_dir
        self.symbol = symbol
        self.out_dir = data_dir / "btc" / "datasets" / DATASET / symbol / "1m"
        self.quant_path = (
            data_dir / "btc" / "datasets" / DATASET / symbol / "daily_trade_size_quantiles.jsonl"
        )
        self.manifest = data_dir / "btc" / "raw" / f"manifest_{DATASET}.jsonl"
        self.client = httpx.Client(
            timeout=httpx.Timeout(600.0, connect=60.0), follow_redirects=True
        )
        self.prefetch = max(1, prefetch)
        self._done: dict[str, dict[str, Any]] = {}
        self._quant: dict[str, dict[str, float]] = {}
        if self.manifest.exists():
            for line in self.manifest.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._done[str(row["day"])] = row
        if self.quant_path.exists():
            for line in self.quant_path.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._quant[str(row["day"])] = {
                        k: float(v) for k, v in row.items() if k.startswith("p")
                    }

    def key(self, day: str) -> str:
        return f"{self.symbol}/{self.symbol}{day}.csv.gz"

    def _download(self, day: str) -> tuple[Path | None, str, int]:
        url = BASE + self.key(day)
        h = hashlib.sha256()
        n = 0
        fd, name = tempfile.mkstemp(prefix=f"bybit1m_{day}_", suffix=".csv.gz")
        os.close(fd)
        tmp = Path(name)
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

    def _record_quant(self, day: str, q: dict[str, float]) -> None:
        with self.quant_path.open("a") as f:
            f.write(json.dumps({"day": day, **q}, sort_keys=True) + "\n")
        self._quant[day] = q

    def _history_before(self, day: str) -> list[dict[str, float]]:
        return [self._quant[d] for d in sorted(self._quant) if d < day]

    def ingest(
        self, start: str, end_inclusive: str, stats: V7IngestStats, allow_holdout: bool = False
    ) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        days = []
        for day in days_between(start, end_inclusive):
            if date.fromisoformat(day) >= HOLDOUT_START and not allow_holdout:
                raise RuntimeError(
                    f"{day} lies in the locked historical holdout (>= {HOLDOUT_START}); refused"
                )
            if (
                day in self._done
                and (self.out_dir / f"{day}.parquet").exists()
                and day in self._quant
            ):
                stats.skipped += 1
                continue
            days.append(day)
        with ThreadPoolExecutor(max_workers=self.prefetch) as pool:
            futures = {d: pool.submit(self._download, d) for d in days[: self.prefetch]}
            nxt = self.prefetch
            for day in days:  # strictly in date order: thresholds depend on previous days
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
                    thr = pit_thresholds(self._history_before(day))
                    agg = aggregate_1m(df, day, thr)
                    out = self.out_dir / f"{day}.parquet"
                    tmp_out = out.with_suffix(".parquet.tmp")
                    agg.write_parquet(tmp_out, compression="zstd")
                    tmp_out.replace(out)
                    q = day_quantiles(df["size"].to_numpy())
                    if day not in self._quant:
                        self._record_quant(day, q)
                    self._record(
                        {
                            "dataset": DATASET,
                            "day": day,
                            "key": self.key(day),
                            "sha256": sha,
                            "bytes": nbytes,
                            "trades": df.height,
                            "rows": agg.height,
                            "thresholds": thr,
                            "raw_retained": False,
                            "fetched_at": datetime.now(UTC).isoformat(),
                        }
                    )
                    stats.fetched += 1
                    stats.rows += agg.height
                    stats.trades += df.height
                    stats.bytes += nbytes
                    log.info(
                        "%s: %d trades -> %d rows; thresholds %s",
                        day,
                        df.height,
                        agg.height,
                        {k: round(v, 3) for k, v in thr.items()},
                    )
                except Exception as e:
                    stats.errors.append(f"{day}: {e!r}")
                    log.exception("failed %s", day)
                finally:
                    tmp.unlink(missing_ok=True)


def load_bybit_1m(
    data_dir: Path, symbol: str = "BTCUSDT", start_ms: int | None = None, end_ms: int | None = None
) -> pl.DataFrame:
    d = data_dir / "btc" / "datasets" / DATASET / symbol / "1m"
    files = sorted(d.glob("*.parquet")) if d.exists() else []
    if start_ms is not None or end_ms is not None:
        keep = []
        for f in files:
            t = int(datetime.fromisoformat(f.stem).replace(tzinfo=UTC).timestamp() * 1000)
            if (start_ms is None or t + 86_400_000 > start_ms) and (end_ms is None or t < end_ms):
                keep.append(f)
        files = keep
    if not files:
        return pl.DataFrame()
    df = pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")
    return df.sort("open_time_ms").unique(
        subset=["open_time_ms"], keep="first", maintain_order=True
    )


def dataset_hash_1m(data_dir: Path) -> str:
    m = data_dir / "btc" / "raw" / f"manifest_{DATASET}.jsonl"
    h = hashlib.sha256()
    if m.exists():
        rows = sorted(
            (json.loads(x) for x in m.read_text().splitlines() if x.strip()),
            key=lambda r: str(r["day"]),
        )
        for r in rows:
            h.update(f"{r['day']}:{r['sha256']}\n".encode())
    return h.hexdigest()
