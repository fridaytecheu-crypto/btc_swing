"""PIT-safe feature warm-start for V11-WARM: ~30 calendar days of RECENT BTCUSDT 1m bars built from
the Bybit PUBLIC trade archive (public.bybit.com; the same source and aggregation as the Bybit-native
research dataset), ending strictly BEFORE the prospective observation_start. Every bar carries the
real open/high/low/close/volume, taker buy/sell quantity and trade count. Raw gz files are streamed,
hashed and discarded. Nothing from the order book, liquidations or live microstructure is backfilled.

The bootstrap bars are written ONLY under the V11-WARM root (never into a research dataset), as the
V11 live bar format, flagged `src: bootstrap`."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from btc_swing.v7.ingest import aggregate_1m
from btc_swing.v61.ingest import BASE

log = logging.getLogger(__name__)
MS_1M = 60_000
BAR_KEYS = ("open_ms", "o", "h", "l", "c", "vol", "buy", "sell", "n")


@dataclass
class BootstrapStats:
    days_requested: int = 0
    days_fetched: int = 0
    days_unavailable: list[str] = field(default_factory=list)
    bars: int = 0
    bytes: int = 0
    first_open_ms: int | None = None
    last_close_ms: int | None = None
    sha256_bars: str | None = None
    manifest: list[dict[str, Any]] = field(default_factory=list)


def bootstrap_days(end_exclusive: date, days: int) -> list[str]:
    return [(end_exclusive - timedelta(days=k)).isoformat() for k in range(days, 0, -1)]


def _download(client: httpx.Client, symbol: str, day: str) -> tuple[Path | None, str, int]:
    url = f"{BASE}{symbol}/{symbol}{day}.csv.gz"
    h = hashlib.sha256()
    n = 0
    fd, name = tempfile.mkstemp(prefix=f"v11warm_{day}_", suffix=".csv.gz")
    os.close(fd)
    tmp = Path(name)
    with client.stream("GET", url) as r:
        if r.status_code != 200:
            tmp.unlink(missing_ok=True)
            return None, "", 0
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                h.update(chunk)
                n += len(chunk)
                f.write(chunk)
    return tmp, h.hexdigest(), n


def day_bars(
    symbol: str, day: str, client: httpx.Client
) -> tuple[pl.DataFrame | None, dict[str, Any]]:
    tmp, sha, nbytes = _download(client, symbol, day)
    if tmp is None:
        return None, {"day": day, "available": False}
    try:
        df = pl.read_csv(
            tmp,
            columns=["timestamp", "side", "size", "price"],
            schema_overrides={"timestamp": pl.Float64, "size": pl.Float64, "price": pl.Float64},
        )
        agg = aggregate_1m(df, day, {})
        bars = agg.select(
            pl.col("open_time_ms").alias("open_ms"),
            pl.col("open").alias("o"),
            pl.col("high").alias("h"),
            pl.col("low").alias("l"),
            pl.col("close").alias("c"),
            pl.col("volume").alias("vol"),
            pl.col("buy_qty").alias("buy"),
            pl.col("sell_qty").alias("sell"),
            pl.col("n_trades").alias("n"),
        )
        meta = {
            "day": day,
            "available": True,
            "key": f"{symbol}/{symbol}{day}.csv.gz",
            "sha256": sha,
            "bytes": nbytes,
            "trades": df.height,
            "bars": bars.height,
            "raw_retained": False,
            "fetched_at": datetime.now(UTC).isoformat(),
        }
        return bars, meta
    finally:
        tmp.unlink(missing_ok=True)


def fetch_bootstrap(
    symbol: str, end_exclusive: date, days: int, out_parquet: Path, manifest_path: Path
) -> BootstrapStats:
    """Fetch `days` archive days ending the day before `end_exclusive` into one parquet of V11 live
    bars (sorted, unique, every bar closing <= end_exclusive 00:00 UTC)."""
    st = BootstrapStats(days_requested=days)
    frames: list[pl.DataFrame] = []
    with httpx.Client(timeout=httpx.Timeout(600.0, connect=60.0), follow_redirects=True) as client:
        for day in bootstrap_days(end_exclusive, days):
            bars, meta = day_bars(symbol, day, client)
            st.manifest.append(meta)
            if bars is None:
                st.days_unavailable.append(day)
                log.warning("%s: archive day unavailable", day)
                continue
            frames.append(bars)
            st.days_fetched += 1
            st.bytes += int(meta["bytes"])
            log.info("%s: %d trades -> %d bars", day, meta["trades"], meta["bars"])
    if not frames:
        raise RuntimeError("no bootstrap day could be fetched")
    df = (
        pl.concat(frames)
        .sort("open_ms")
        .unique(subset=["open_ms"], keep="first", maintain_order=True)
    )
    end_ms = int(
        datetime.combine(end_exclusive, datetime.min.time(), tzinfo=UTC).timestamp() * 1000
    )
    df = df.filter(pl.col("open_ms") + MS_1M <= end_ms)
    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_parquet.with_suffix(".parquet.tmp")
    df.write_parquet(tmp, compression="zstd")
    tmp.replace(out_parquet)
    st.bars = df.height
    st.first_open_ms = int(df["open_ms"][0])
    st.last_close_ms = int(df["open_ms"][-1]) + MS_1M
    st.sha256_bars = bars_sha256(df)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "symbol": symbol,
                "source": BASE,
                "days_requested": days,
                "end_exclusive": end_exclusive.isoformat(),
                "days_fetched": st.days_fetched,
                "days_unavailable": st.days_unavailable,
                "bars": st.bars,
                "first_open_ms": st.first_open_ms,
                "last_close_ms": st.last_close_ms,
                "sha256_bars": st.sha256_bars,
                "files": st.manifest,
            },
            indent=1,
            sort_keys=True,
        )
    )
    return st


def bars_sha256(df: pl.DataFrame) -> str:
    """Content hash of the bootstrap bars (row order and values), independent of file encoding."""
    h = hashlib.sha256()
    for row in df.select(list(BAR_KEYS)).iter_rows():
        h.update(json.dumps(row, separators=(",", ":")).encode())
    return h.hexdigest()


def load_bootstrap(parquet: Path) -> pl.DataFrame:
    return pl.read_parquet(parquet).select(list(BAR_KEYS)).sort("open_ms")


def write_bootstrap_bars(
    df: pl.DataFrame, bars_1m: Path, observation_start_ms: int | None = None
) -> int:
    """Seed the V11-WARM `bars_1m.jsonl` with the bootstrap bars (refused if the file already holds
    bars, or if any bar would close after a known observation_start)."""
    if bars_1m.exists() and bars_1m.stat().st_size > 0:
        raise RuntimeError(f"{bars_1m} already holds bars; refusing to re-seed")
    if observation_start_ms is not None:
        last_close = int(df["open_ms"][-1]) + MS_1M
        if last_close > observation_start_ms:
            raise RuntimeError("bootstrap bars would close after observation_start: PIT violation")
    bars_1m.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with bars_1m.open("w") as fh:
        for row in df.iter_rows(named=True):
            rec = {k: row[k] for k in BAR_KEYS}
            rec["n"] = int(rec["n"])
            rec["src"] = "bootstrap"
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            n += 1
    return n


def _kline_row(k: list[Any]) -> dict[str, Any]:
    return {
        "open_ms": int(k[0]),
        "o": float(k[1]),
        "h": float(k[2]),
        "l": float(k[3]),
        "c": float(k[4]),
        "vol": float(k[5]),
        "buy": None,
        "sell": None,
        "n": 1,
        "src": "kline_gapfill",
    }


def gapfill_klines(
    symbol: str,
    from_ms: int,
    bars_1m: Path,
    max_hours: int,
    observation_start_ms: int | None = None,
    client: httpx.Client | None = None,
    base_url: str = "https://api.bybit.com",
) -> tuple[int, dict[str, Any]]:
    """Append Bybit PUBLIC 1m klines (v5/market/kline, linear) from `from_ms` up to the last COMPLETED
    minute, as `src: kline_gapfill` bars: open/high/low/close/volume exact; the taker split is UNKNOWN
    (buy/sell NaN) and the trade count is a validity placeholder (1). Bounded by `max_hours`; never
    reaches a known observation_start. REST is geo-blocked in the cloud container, where the caller
    lets the gap warm live instead."""
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    end_ms = (now_ms // MS_1M) * MS_1M  # open time of the current, incomplete minute
    if observation_start_ms is not None:
        end_ms = min(end_ms, observation_start_ms)
    if end_ms - from_ms > max_hours * 3_600_000:
        raise RuntimeError(f"gap {((end_ms - from_ms) / 3.6e6):.1f} h exceeds gapfill_max_hours")
    if end_ms <= from_ms:
        return 0, {"bars": 0, "from_ms": from_ms, "end_ms": end_ms}
    own = client is None
    cl = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=20.0))
    rows: dict[int, dict[str, Any]] = {}
    try:
        cursor_end = end_ms - 1
        while cursor_end >= from_ms:  # Bybit returns the newest <= 1000 candles in [start, end]
            r = cl.get(
                f"{base_url}/v5/market/kline",
                params={
                    "category": "linear",
                    "symbol": symbol,
                    "interval": "1",
                    "start": from_ms,
                    "end": cursor_end,
                    "limit": 1000,
                },
            )
            r.raise_for_status()
            body = r.json()
            if body.get("retCode") != 0:
                raise RuntimeError(f"kline retCode {body.get('retCode')}: {body.get('retMsg')}")
            lst = body["result"]["list"]
            if not lst:
                break
            for k in lst:
                t = int(k[0])
                if from_ms <= t < end_ms:
                    rows[t] = _kline_row(k)
            oldest = min(int(k[0]) for k in lst)
            if oldest <= from_ms or len(lst) < 1000:
                break
            cursor_end = oldest - 1
    finally:
        if own:
            cl.close()
    if not rows:
        return 0, {"bars": 0, "from_ms": from_ms, "end_ms": end_ms}
    bars_1m.parent.mkdir(parents=True, exist_ok=True)
    with bars_1m.open("a") as fh:
        for t in sorted(rows):
            fh.write(json.dumps(rows[t], sort_keys=True) + "\n")
    return len(rows), {
        "bars": len(rows),
        "from_ms": from_ms,
        "end_ms": end_ms,
        "first_open": datetime.fromtimestamp(min(rows) / 1000, tz=UTC).isoformat(),
        "last_close": datetime.fromtimestamp((max(rows) + MS_1M) / 1000, tz=UTC).isoformat(),
        "fields": "price/volume exact; taker split unknown (NaN); trade count placeholder 1",
    }
