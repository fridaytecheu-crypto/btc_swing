"""Binance SPOT 5m klines (data.binance.vision monthly archive, published SHA256 verified) for the N2
spot-flow screen. Only taker-buy quote / total quote volume and OHLC are kept. Timestamp units are
normalised to UTC milliseconds (the archive switched to microseconds on 2025-01-01). Months at or after
the locked holdout start are never downloaded. Raw zips are not retained; the sha256 of every archive
file is recorded in `manifests/binance_spot_klines_5m_manifest.jsonl`."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from btc_swing.research.intraday_portfolio.data import HOLDOUT_START

BASE_URL = "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/5m/"
DATASET = "binance_spot_klines"
COLUMNS = [
    "open_time_ms",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trades",
    "taker_buy_volume",
    "taker_buy_quote_volume",
]


def dataset_dir(data_dir: Path) -> Path:
    return data_dir / "btc" / "datasets" / DATASET / "BTCUSDT" / "5m"


def months(start: date, end: date) -> list[str]:
    out: list[str] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _to_ms(v: int) -> int:
    """Archive timestamps are ms before 2025-01-01 and us afterwards; normalise by magnitude."""
    return v // 1000 if v > 10**14 else v


def parse_zip(raw: bytes) -> pl.DataFrame:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".csv"))
        text = zf.read(name).decode("utf-8")
    rows: list[dict[str, Any]] = []
    for rec in csv.reader(io.StringIO(text)):
        if not rec or not rec[0].isdigit():
            continue  # header line (newer files) or blank
        rows.append(
            {
                "open_time_ms": _to_ms(int(rec[0])),
                "open": float(rec[1]),
                "high": float(rec[2]),
                "low": float(rec[3]),
                "close": float(rec[4]),
                "volume": float(rec[5]),
                "quote_volume": float(rec[7]),
                "trades": int(rec[8]),
                "taker_buy_volume": float(rec[9]),
                "taker_buy_quote_volume": float(rec[10]),
            }
        )
    df = pl.DataFrame(rows, schema_overrides={"open_time_ms": pl.Int64, "trades": pl.Int64})
    return df.sort("open_time_ms").unique(
        subset=["open_time_ms"], keep="first", maintain_order=True
    )


def _get(client: httpx.Client, url: str) -> bytes:
    r = client.get(url)
    r.raise_for_status()
    return r.content


def ingest(
    data_dir: Path,
    manifest_path: Path,
    start: date = date(2021, 11, 1),
    end: date | None = None,
    allow_holdout: bool = False,
) -> list[dict[str, Any]]:
    """Download + verify + convert every month in [start, end]; months already on disk are skipped
    (their manifest line stands). Returns the manifest rows written in this call."""
    cap = date(HOLDOUT_START.year, HOLDOUT_START.month, 1)
    last = end or date(cap.year, cap.month - 1 if cap.month > 1 else 12, 1)
    if not allow_holdout and (last.year, last.month) >= (cap.year, cap.month):
        last = date(cap.year, cap.month - 1, 1) if cap.month > 1 else date(cap.year - 1, 12, 1)
    out_dir = dataset_dir(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, Any]] = []
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        for ym in months(start, last):
            target = out_dir / f"{ym}.parquet"
            if target.exists():
                continue
            fn = f"BTCUSDT-5m-{ym}.zip"
            published = _get(client, BASE_URL + fn + ".CHECKSUM").decode().split()[0].lower()
            raw = _get(client, BASE_URL + fn)
            sha = hashlib.sha256(raw).hexdigest()
            if sha != published:
                raise RuntimeError(f"checksum mismatch {fn}: published {published} got {sha}")
            df = parse_zip(raw)
            df.write_parquet(target)
            row = {
                "dataset": DATASET,
                "month": ym,
                "key": f"data/spot/monthly/klines/BTCUSDT/5m/{fn}",
                "sha256": sha,
                "published_sha256": published,
                "bytes": len(raw),
                "rows": df.height,
                "first_open_time_ms": int(df["open_time_ms"][0]),
                "last_open_time_ms": int(df["open_time_ms"][-1]),
                "raw_retained": False,
                "fetched_at": datetime.now(UTC).isoformat(),
            }
            with manifest_path.open("a") as fh:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
            written.append(row)
    return written


def load_spot_5m(data_dir: Path, allow_holdout: bool = False) -> pl.DataFrame | None:
    files = sorted(dataset_dir(data_dir).glob("*.parquet"))
    cap = HOLDOUT_START.isoformat()[:7]
    files = [f for f in files if allow_holdout or f.stem < cap]
    if not files:
        return None
    df = pl.concat([pl.read_parquet(f, columns=COLUMNS) for f in files]).sort("open_time_ms")
    return df.unique(subset=["open_time_ms"], keep="first", maintain_order=True)
