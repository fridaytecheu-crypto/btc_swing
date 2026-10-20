#!/usr/bin/env python
# ruff: noqa: E501
"""Binance USD-M BTCUSDT inputs for the frozen carry screen, exactly where `btc-swing ipr research-carry`
expects them (run on the Mac; data.binance.vision is a public archive, no keys, no orders).

It REUSES the repository's existing Binance Vision ingestor (`btc_swing.ingest.pipeline.BtcIngestor`, the
same code path as `btc-swing data ingest`): monthly archive zips with the published SHA256 verified, raw
files kept in the immutable archive store, datasets written as
  <BTC_DATA_DIR>/btc/datasets/funding/BTCUSDT/na/<YYYY-MM>.parquet      (time_ms, funding_interval_hours, funding_rate, ...)
  <BTC_DATA_DIR>/btc/datasets/mark_price/BTCUSDT/5m/<YYYY-MM>.parquet   (open_time_ms = interval START, open, high, low, close, ...)
  <BTC_DATA_DIR>/btc/datasets/perp_klines/BTCUSDT/5m/<YYYY-MM>.parquet  (traded 5m, the carry screen's secondary valuation)
plus the per-series JSON manifests the store maintains. The frozen V1 config file is NOT modified: the heavy
datasets it would also ingest (1h/4h/1d klines, metrics, premium index) are switched off through in-memory
overrides only. The Binance archive carries no settlement mark price (reported as absent, never invented) and
no index klines in this pipeline (optional, diagnostic only; not fetched here).

Usage (Mac):
  uv run python scripts/binance_carry_inputs.py ingest     # 2021-11 .. 2025-06 (holdout months refused)
  uv run python scripts/binance_carry_inputs.py validate   # checks + the carry loader's own inventory
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

HOLDOUT_FIRST_MONTH = "2025-07"
DEFAULT_START = "2021-11"
DEFAULT_END = "2025-06"
MS_5M = 300_000
MS_1H = 3_600_000
SYMBOL = "BTCUSDT"
OVERRIDES: dict[str, Any] = {
    "data": {
        "provider": "binance_vision",
        "ingest_native_timeframes": [],
        "ingest_funding": True,
        "ingest_metrics": False,
        "ingest_premium_index": False,
        "ingest_mark_price": True,
        "ingest_spot": False,
    }
}


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def ingest(data_dir: Path, start: str, end: str, allow_holdout: bool) -> dict[str, Any]:
    from btc_swing.core.config import load_btc_config
    from btc_swing.ingest.pipeline import BtcIngestor
    from btc_swing.providers.binance_vision import BinanceVisionProvider, default_entitlement
    from btc_swing.storage.archive_store import ArchiveStore
    from btc_swing.storage.bar_store import BarStore

    if not allow_holdout and end >= HOLDOUT_FIRST_MONTH:
        end = DEFAULT_END
        print(f"end capped at {end} (locked holdout never downloaded for this experiment)")
    cfg = load_btc_config(
        None, OVERRIDES
    )  # frozen V1 file untouched; overrides live in memory only
    plan = [
        (d.value, s, tf.value if tf else "na")
        for d, s, tf in BtcIngestor(
            BinanceVisionProvider(default_entitlement(0)),
            ArchiveStore(data_dir),
            BarStore(data_dir),
            cfg,
        ).plan()
    ]
    print("ingest plan:", plan)
    ing = BtcIngestor(
        BinanceVisionProvider(default_entitlement(cfg.data.latency_minutes)),
        ArchiveStore(data_dir),
        BarStore(data_dir),
        cfg,
    )
    stats = ing.ingest(start, end)
    out = stats.as_dict()
    print(json.dumps(out, indent=2))
    return out


def _files(root: Path, cap_month: str) -> list[Path]:
    return sorted(p for p in root.glob("*.parquet") if p.stem < cap_month)


def validate(data_dir: Path) -> dict[str, Any]:
    ds = data_dir / "btc" / "datasets"
    rep: dict[str, Any] = {"ok": True}
    ff = _files(ds / "funding" / SYMBOL / "na", HOLDOUT_FIRST_MONTH)
    if not ff:
        rep["funding"] = {"status": "MISSING", "path": str(ds / "funding" / SYMBOL / "na")}
        rep["ok"] = False
    else:
        f = pl.concat(
            [
                pl.read_parquet(p, columns=["time_ms", "funding_rate", "funding_interval_hours"])
                for p in ff
            ]
        )
        t = f["time_ms"].to_numpy()
        d = np.diff(np.sort(t))
        intervals = Counter(round(int(x) / MS_1H) for x in d.tolist())
        jitter = (
            int(np.max(np.abs((t % (8 * MS_1H) + 4 * MS_1H) % (8 * MS_1H) - 4 * MS_1H)))
            if len(t)
            else 0
        )
        rep["funding"] = {
            "status": "AVAILABLE",
            "files": len(ff),
            "events": int(f.height),
            "first_utc": _utc(int(t.min())),
            "last_utc": _utc(int(t.max())),
            "duplicate_timestamps": int(f.height - f["time_ms"].n_unique()),
            "null_rates": int(f["funding_rate"].null_count()),
            "null_timestamps": int(f["time_ms"].null_count()),
            "settlement_interval_hours_distribution": dict(sorted(intervals.items())),
            "max_abs_jitter_ms_from_8h_grid": jitter,
            "note": "Binance settlement stamps carry ms jitter; the carry loader looks the settlement mark up in the 5m bar containing the stamp",
            "declared_interval_hours": sorted(
                {int(x) for x in f["funding_interval_hours"].drop_nulls().unique().to_list()}
            ),
            "timestamps_strictly_increasing_in_file_order": bool(np.all(np.diff(t) > 0)),
            "settlement_mark_price": "NOT PROVIDED by the Binance Vision fundingRate archive (carry uses the mark-bar open, flagged)",
        }
        rep["ok"] &= (
            rep["funding"]["duplicate_timestamps"] == 0
            and rep["funding"]["null_rates"] == 0
            and rep["funding"]["null_timestamps"] == 0
        )
    for name, tf in (("mark_price", "5m"), ("perp_klines", "5m")):
        mf = _files(ds / name / SYMBOL / tf, HOLDOUT_FIRST_MONTH)
        if not mf:
            rep[name] = {"status": "MISSING", "path": str(ds / name / SYMBOL / tf)}
            if name == "mark_price":
                rep["ok"] = False
            continue
        m = pl.concat([pl.read_parquet(p, columns=["open_time_ms", "open", "close"]) for p in mf])
        t = np.sort(m["open_time_ms"].to_numpy())
        d = np.diff(t)
        gaps = d[d != MS_5M]
        rep[name] = {
            "status": "AVAILABLE",
            "files": len(mf),
            "rows": int(m.height),
            "first_utc": _utc(int(t[0])),
            "last_utc": _utc(int(t[-1])),
            "all_on_5m_utc_grid": bool(np.all(t % MS_5M == 0)),
            "gaps_gt_5m": len(gaps),
            "max_gap_minutes": float(d.max() / 60_000) if len(d) else None,
            "missing_bars_implied": int(sum(int(g // MS_5M) - 1 for g in gaps.tolist())),
            "nulls": int(m.null_count().sum_horizontal()[0]),
            "duplicate_timestamps": int(m.height - m["open_time_ms"].n_unique()),
            "bar_timestamp_semantics": "open_time_ms = interval START (Binance kline open time); no forward fill applied",
        }
        if name == "mark_price":
            rep["ok"] &= (
                rep[name]["all_on_5m_utc_grid"]
                and rep[name]["nulls"] == 0
                and rep[name]["duplicate_timestamps"] == 0
            )
    # the carry loader's own view (both venues)
    try:
        from btc_swing.research.intraday_portfolio.carry import load_venues

        _venues, inv = load_venues(data_dir)
        rep["carry_loader"] = {
            "missing": inv.get("missing", []),
            "binance": {
                k: v
                for k, v in inv.get("BINANCE", {}).items()
                if k
                in (
                    "funding_settlements",
                    "funding_interval_hours_observed",
                    "funding_non_grid_gaps",
                    "mark_rows",
                    "mark_gaps_gt_5m",
                    "traded_5m_available",
                )
            },
            "bybit": {
                k: v
                for k, v in inv.get("BYBIT", {}).items()
                if k
                in ("funding_settlements", "mark_rows", "mark_gaps_gt_5m", "traded_5m_available")
            },
        }
        rep["ok"] &= not inv.get("missing")
    except Exception as e:
        rep["carry_loader"] = {"error": f"{type(e).__name__}: {e}"}
        rep["ok"] = False
    rep["verdict"] = "VALID" if rep["ok"] else "INVALID"
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("ingest")
    i.add_argument("--data-dir", default="data")
    i.add_argument("--from", dest="start", default=DEFAULT_START)
    i.add_argument("--to", dest="end", default=DEFAULT_END)
    i.add_argument("--allow-holdout", action="store_true")
    v = sub.add_parser("validate")
    v.add_argument("--data-dir", default="data")
    a = ap.parse_args()
    if a.cmd == "ingest":
        ingest(Path(a.data_dir), a.start, a.end, a.allow_holdout)
        return 0
    rep = validate(Path(a.data_dir))
    print(json.dumps(rep, indent=2, sort_keys=True, default=str))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
