#!/usr/bin/env python
# ruff: noqa: E501
"""Bybit PUBLIC history export for the carry screen (run on the Mac: api.bybit.com is blocked here).

READ-ONLY against the exchange (public GET, no keys, no orders). Writes immutable parquet tables under
<BTC_DATA_DIR>/btc/datasets/ plus a sha256 manifest, never touching any runner, journal or freeze:

  bybit_funding/BTCUSDT/na/<YYYY-MM>.parquet        settled funding events (time_ms, funding_rate)
  bybit_mark_5m/BTCUSDT/5m/<YYYY-MM>.parquet         mark-price 5m klines (open_time_ms, open, high, low, close)
  bybit_index_5m/BTCUSDT/5m/<YYYY-MM>.parquet        index-price 5m klines
  bybit_instrument_info/BTCUSDT/instruments_info_<retrieved>.json   CURRENT contract metadata only
  manifests/bybit_history_export_manifest.jsonl      one line per table file with sha256, rows, span

What Bybit does NOT publish historically (recorded, not invented): the funding interval / cap history
(instruments-info returns the CURRENT fundingInterval, upperFundingRate, lowerFundingRate only).

Usage (Mac):
  uv run python scripts/bybit_history_export.py export --start 2021-11-15 --end 2025-06-30
  uv run python scripts/bybit_history_export.py validate
Holdout months (>= 2025-07) are refused unless --allow-holdout is given.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import polars as pl

BASE = "https://api.bybit.com"
SYMBOL = "BTCUSDT"
HOLDOUT_START = date(2025, 7, 1)
MS_5M = 300_000
MS_8H = 8 * 3_600_000


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


def _get(client: httpx.Client, path: str, params: dict[str, Any]) -> dict[str, Any]:
    for attempt in range(6):
        r = client.get(path, params=params)
        r.raise_for_status()
        body = r.json()
        code = int(body.get("retCode", -1))
        if code == 0:
            return body["result"]
        if code == 10006:  # rate limited
            time.sleep(2.0 * (attempt + 1))
            continue
        raise RuntimeError(f"{path} retCode {code}: {body.get('retMsg')}")
    raise RuntimeError(f"{path}: rate limited repeatedly")


def fetch_funding(client: httpx.Client, start_ms: int, end_ms: int) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    end = end_ms
    while end > start_ms:
        res = _get(
            client,
            "/v5/market/funding/history",
            {
                "category": "linear",
                "symbol": SYMBOL,
                "startTime": start_ms,
                "endTime": end,
                "limit": 200,
            },
        )
        lst = res.get("list") or []
        if not lst:
            break
        for r in lst:
            rows.append(
                {
                    "time_ms": int(r["fundingRateTimestamp"]),
                    "funding_rate": float(r["fundingRate"]),
                    "symbol": SYMBOL,
                    "source": "bybit_rest_funding_history",
                }
            )
        oldest = min(int(r["fundingRateTimestamp"]) for r in lst)
        if oldest <= start_ms:
            break
        end = oldest - 1
        time.sleep(0.15)
    df = pl.DataFrame(
        rows,
        schema={
            "time_ms": pl.Int64,
            "funding_rate": pl.Float64,
            "symbol": pl.Utf8,
            "source": pl.Utf8,
        },
    )
    return (
        df.unique(subset=["time_ms"], keep="first")
        .sort("time_ms")
        .filter((pl.col("time_ms") >= start_ms) & (pl.col("time_ms") <= end_ms))
    )


def fetch_kline(
    client: httpx.Client, path: str, start_ms: int, end_ms: int, source: str
) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    end = end_ms
    while end > start_ms:
        res = _get(
            client,
            path,
            {
                "category": "linear",
                "symbol": SYMBOL,
                "interval": "5",
                "start": start_ms,
                "end": end,
                "limit": 1000,
            },
        )
        lst = res.get("list") or []
        if not lst:
            break
        for r in lst:  # [startTime, open, high, low, close]
            rows.append(
                {
                    "open_time_ms": int(r[0]),
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                    "source": source,
                }
            )
        oldest = min(int(r[0]) for r in lst)
        if oldest <= start_ms:
            break
        end = oldest - 1
        time.sleep(0.15)
    df = pl.DataFrame(
        rows,
        schema={
            "open_time_ms": pl.Int64,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "source": pl.Utf8,
        },
    )
    return (
        df.unique(subset=["open_time_ms"], keep="first")
        .sort("open_time_ms")
        .filter((pl.col("open_time_ms") >= start_ms) & (pl.col("open_time_ms") <= end_ms))
    )


def _write_monthly(df: pl.DataFrame, tcol: str, root: Path, manifest: Path, dataset: str) -> int:
    root.mkdir(parents=True, exist_ok=True)
    if df.height == 0:
        return 0
    df = df.with_columns(
        pl.from_epoch(pl.col(tcol), time_unit="ms").dt.strftime("%Y-%m").alias("_ym")
    )
    n = 0
    for ym, part in sorted(df.group_by("_ym"), key=lambda kv: str(kv[0][0])):
        ym_s = str(ym[0])
        target = root / f"{ym_s}.parquet"
        body = part.drop("_ym").sort(tcol)
        if target.exists():
            old = pl.read_parquet(target)
            body = pl.concat([old, body]).unique(subset=[tcol], keep="first").sort(tcol)
        body.write_parquet(target)
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        with manifest.open("a") as fh:
            fh.write(
                json.dumps(
                    {
                        "dataset": dataset,
                        "month": ym_s,
                        "file": str(target),
                        "sha256": sha,
                        "rows": body.height,
                        "first_ms": int(body[tcol][0]),
                        "last_ms": int(body[tcol][-1]),
                        "written_at": datetime.now(tz=UTC).isoformat(),
                    },
                    sort_keys=True,
                )
                + "\n"
            )
        n += 1
    return n


def export(data_dir: Path, start: date, end: date, allow_holdout: bool) -> None:
    if not allow_holdout and end >= HOLDOUT_START:
        end = date(2025, 6, 30)
        print(f"end capped at {end} (locked holdout)")
    s_ms, e_ms = _ms(start), _ms(end) + 86_400_000 - 1
    ds = data_dir / "btc" / "datasets"
    manifest = Path("manifests/bybit_history_export_manifest.jsonl")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=BASE, timeout=30.0) as client:
        fund = fetch_funding(client, s_ms, e_ms)
        print(f"funding events {fund.height}")
        print(
            "monthly funding files",
            _write_monthly(
                fund, "time_ms", ds / "bybit_funding" / SYMBOL / "na", manifest, "bybit_funding"
            ),
        )
        mark = fetch_kline(
            client, "/v5/market/mark-price-kline", s_ms, e_ms, "bybit_rest_mark_price_kline_5"
        )
        print(f"mark 5m rows {mark.height}")
        print(
            "monthly mark files",
            _write_monthly(
                mark,
                "open_time_ms",
                ds / "bybit_mark_5m" / SYMBOL / "5m",
                manifest,
                "bybit_mark_5m",
            ),
        )
        index = fetch_kline(
            client, "/v5/market/index-price-kline", s_ms, e_ms, "bybit_rest_index_price_kline_5"
        )
        print(f"index 5m rows {index.height}")
        print(
            "monthly index files",
            _write_monthly(
                index,
                "open_time_ms",
                ds / "bybit_index_5m" / SYMBOL / "5m",
                manifest,
                "bybit_index_5m",
            ),
        )
        info = _get(client, "/v5/market/instruments-info", {"category": "linear", "symbol": SYMBOL})
        info_dir = ds / "bybit_instrument_info" / SYMBOL
        info_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        p = info_dir / f"instruments_info_{stamp}.json"
        p.write_text(
            json.dumps(
                {
                    "retrieved_at_utc": stamp,
                    "note": "CURRENT metadata only; Bybit publishes no funding interval / cap history",
                    "result": info,
                },
                indent=2,
                sort_keys=True,
            )
        )
        with manifest.open("a") as fh:
            fh.write(
                json.dumps(
                    {
                        "dataset": "bybit_instrument_info",
                        "file": str(p),
                        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                        "written_at": stamp,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
        print("instrument info", p)


def validate(data_dir: Path) -> int:
    ds = data_dir / "btc" / "datasets"
    ok = True
    fund_files = sorted((ds / "bybit_funding" / SYMBOL / "na").glob("*.parquet"))
    if fund_files:
        f = pl.concat([pl.read_parquet(x) for x in fund_files]).sort("time_ms")
        t = f["time_ms"].to_numpy()
        gaps = int(((t[1:] - t[:-1]) != MS_8H).sum())
        on_grid = bool(((t % MS_8H) == 0).all())
        print(
            f"bybit_funding: {f.height} events {datetime.fromtimestamp(int(t[0]) / 1000, tz=UTC)} .. {datetime.fromtimestamp(int(t[-1]) / 1000, tz=UTC)}; non-8h spacings {gaps}; all on 8h UTC grid {on_grid}; |rate| max {f['funding_rate'].abs().max()!r}; nulls {f['funding_rate'].null_count()}"
        )
        ok &= f["funding_rate"].null_count() == 0
    else:
        print("bybit_funding: MISSING")
        ok = False
    for name in ("bybit_mark_5m", "bybit_index_5m"):
        files = sorted((ds / name / SYMBOL / "5m").glob("*.parquet"))
        if not files:
            print(f"{name}: MISSING")
            ok = False
            continue
        k = pl.concat([pl.read_parquet(x) for x in files]).sort("open_time_ms")
        t = k["open_time_ms"].to_numpy()
        d = t[1:] - t[:-1]
        print(
            f"{name}: {k.height} rows {datetime.fromtimestamp(int(t[0]) / 1000, tz=UTC)} .. {datetime.fromtimestamp(int(t[-1]) / 1000, tz=UTC)}; gaps>5m {int((d != MS_5M).sum())}; max gap min {float(d.max() / 60000):.1f}; on 5m grid {bool(((t % MS_5M) == 0).all())}; nulls {k.null_count().sum_horizontal()[0]}"
        )
    infos = sorted((ds / "bybit_instrument_info" / SYMBOL).glob("*.json"))
    print("instrument info files", len(infos), "(CURRENT metadata only)")
    print("VALID" if ok else "INVALID")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--data-dir", default="data")
    e.add_argument("--start", default="2021-11-15")
    e.add_argument("--end", default="2025-06-30")
    e.add_argument("--allow-holdout", action="store_true")
    v = sub.add_parser("validate")
    v.add_argument("--data-dir", default="data")
    a = ap.parse_args()
    if a.cmd == "export":
        export(
            Path(a.data_dir),
            date.fromisoformat(a.start),
            date.fromisoformat(a.end),
            a.allow_holdout,
        )
        return 0
    return validate(Path(a.data_dir))


if __name__ == "__main__":
    sys.exit(main())
