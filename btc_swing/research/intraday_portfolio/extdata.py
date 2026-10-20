"""Two small PUBLIC reference datasets for the cash-funded spot/perp carry screen (no keys, no orders):

- SOFR daily rates from the New York Fed markets API (opportunity-cost benchmark only),
  stored as <BTC_DATA_DIR>/btc/datasets/sofr/na/sofr_daily.parquet (effective_date, percent_rate).
- USDT/USD daily candles from Coinbase Exchange's public product history (independent USD price of
  USDT), stored as <BTC_DATA_DIR>/btc/datasets/usdtusd_coinbase/na/daily.parquet
  (day_open_ms = UTC day start, open, high, low, close, volume).

Every fetch appends a manifest line (source URL, rows, span, sha256 of the parquet) to
manifests/cash_carry_reference_manifest.jsonl. Nothing is interpolated or forward-filled here; the
consumer decides what a missing day means."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl

SOFR_URL = "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json"
COINBASE_URL = "https://api.exchange.coinbase.com/products/USDT-USD/candles"
MANIFEST = Path("manifests/cash_carry_reference_manifest.jsonl")


def sofr_path(data_dir: Path) -> Path:
    return data_dir / "btc" / "datasets" / "sofr" / "na" / "sofr_daily.parquet"


def usdtusd_path(data_dir: Path) -> Path:
    return data_dir / "btc" / "datasets" / "usdtusd_coinbase" / "na" / "daily.parquet"


def _record(manifest: Path, row: dict[str, Any]) -> None:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def fetch_sofr(data_dir: Path, start: date, end: date, manifest: Path = MANIFEST) -> pl.DataFrame:
    with httpx.Client(timeout=60.0) as c:
        r = c.get(SOFR_URL, params={"startDate": start.isoformat(), "endDate": end.isoformat()})
        r.raise_for_status()
        rows = r.json().get("refRates", [])
    df = (
        pl.DataFrame(
            {
                "effective_date": [str(x["effectiveDate"]) for x in rows],
                "percent_rate": [float(x["percentRate"]) for x in rows],
                "source": ["nyfed_markets_api_sofr"] * len(rows),
            }
        )
        .unique(subset=["effective_date"], keep="first")
        .sort("effective_date")
    )
    p = sofr_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(p)
    _record(
        manifest,
        {
            "dataset": "sofr",
            "source": SOFR_URL,
            "rows": df.height,
            "first": df["effective_date"][0] if df.height else None,
            "last": df["effective_date"][-1] if df.height else None,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "fetched_at": datetime.now(tz=UTC).isoformat(),
        },
    )
    return df


def fetch_usdtusd(
    data_dir: Path, start: date, end: date, manifest: Path = MANIFEST
) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    cur = start
    with httpx.Client(timeout=60.0) as c:
        while cur <= end:
            stop = min(end, cur + timedelta(days=299))
            r = c.get(
                COINBASE_URL,
                params={
                    "granularity": 86400,
                    "start": f"{cur.isoformat()}T00:00:00Z",
                    "end": f"{stop.isoformat()}T00:00:00Z",
                },
            )
            r.raise_for_status()
            for t, lo, hi, op, cl, vol in r.json():  # [time, low, high, open, close, volume]
                rows.append(
                    {
                        "day_open_ms": int(t) * 1000,
                        "open": float(op),
                        "high": float(hi),
                        "low": float(lo),
                        "close": float(cl),
                        "volume": float(vol),
                        "source": "coinbase_exchange_public_candles_USDT-USD",
                    }
                )
            cur = stop + timedelta(days=1)
    df = pl.DataFrame(rows).unique(subset=["day_open_ms"], keep="first").sort("day_open_ms")
    p = usdtusd_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(p)
    _record(
        manifest,
        {
            "dataset": "usdtusd_coinbase",
            "source": COINBASE_URL,
            "rows": df.height,
            "first_ms": int(df["day_open_ms"][0]) if df.height else None,
            "last_ms": int(df["day_open_ms"][-1]) if df.height else None,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "fetched_at": datetime.now(tz=UTC).isoformat(),
        },
    )
    return df


def load_sofr(data_dir: Path) -> dict[str, float] | None:
    p = sofr_path(data_dir)
    if not p.exists():
        return None
    df = pl.read_parquet(p)
    return {
        str(d): float(r)
        for d, r in zip(df["effective_date"].to_list(), df["percent_rate"].to_list(), strict=True)
    }


def load_usdtusd(data_dir: Path) -> dict[int, float] | None:
    """UTC day start (ms) -> daily OPEN (the first USD print of the day, before a 00:10 UTC entry)."""
    p = usdtusd_path(data_dir)
    if not p.exists():
        return None
    df = pl.read_parquet(p)
    return {
        int(t): float(o)
        for t, o in zip(df["day_open_ms"].to_list(), df["open"].to_list(), strict=True)
    }


def fingerprint(p: Path) -> dict[str, Any]:
    if not p.exists():
        return {"status": "MISSING", "path": str(p)}
    df = pl.read_parquet(p)
    return {
        "status": "AVAILABLE",
        "path": str(p),
        "rows": df.height,
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
    }
