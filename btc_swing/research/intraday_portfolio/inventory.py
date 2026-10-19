"""Phase 0 — machine-readable data inventory (AVAILABLE / PARTIAL / MISSING per required input).

Nothing here is inferred or fabricated: every status is derived from files that exist on disk. Candles
cannot reconstruct liquidations; klines cannot reconstruct aggressor flow; a missing flow field is MISSING
(never zero); a missing BBO can never become a perfect-mid fill. The consequences for each engine are
recorded explicitly (`consequences`)."""

from __future__ import annotations

import glob
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from btc_swing.research.intraday_portfolio.contract import Availability
from btc_swing.research.intraday_portfolio.data import HOLDOUT_START, raw_coverage

# Local raw Bybit public WebSocket stores (schema bybit-public-raw-1) relative to BTC_DATA_DIR/btc.
RAW_STORE_RELPATHS: tuple[str, ...] = (
    "forward/bybit/BTCUSDT",
    "forward_v10_verify/raw/BTCUSDT",
    "forward_v11_verify/raw/BTCUSDT",
    "forward_v11_warm_verify/raw/BTCUSDT",
    "forward_v11_lite_warm_verify/raw/BTCUSDT",
    "forward_v11_lite_warm_verify_f885f85/raw/BTCUSDT",
)


def _dataset_span(root: Path, pattern: str = "**/*.parquet") -> dict[str, Any]:
    files = sorted(glob.glob(str(root / pattern), recursive=True))
    if not files:
        return {"status": Availability.MISSING, "files": 0, "path": str(root)}
    names = sorted(Path(f).stem for f in files)
    return {
        "status": Availability.AVAILABLE,
        "files": len(files),
        "first": names[0],
        "last": names[-1],
        "path": str(root),
    }


def _bybit_bar_days(root: Path, cap: date) -> dict[str, Any]:
    """Day files of a Bybit trade-built bar dataset, split at the locked holdout start (never read)."""
    files = sorted(root.glob("*.parquet"))
    dev = [f.stem for f in files if date.fromisoformat(f.stem) < cap]
    held = [f.stem for f in files if date.fromisoformat(f.stem) >= cap]
    missing_days: list[str] = []
    if dev:
        d0, d1 = date.fromisoformat(dev[0]), date.fromisoformat(dev[-1])
        have = set(dev)
        for k in range((d1 - d0).days + 1):
            d = date.fromordinal(d0.toordinal() + k).isoformat()
            if d not in have:
                missing_days.append(d)
    return {
        "status": Availability.AVAILABLE if dev else Availability.MISSING,
        "development_days": len(dev),
        "development_first": dev[0] if dev else None,
        "development_last": dev[-1] if dev else None,
        "missing_days_inside_span": missing_days,
        "holdout_day_files_present_not_read": len(held),
        "holdout_start_never_read": cap.isoformat(),
        "path": str(root),
    }


def raw_roots(data_dir: Path) -> list[Path]:
    return [
        data_dir / "btc" / rel for rel in RAW_STORE_RELPATHS if (data_dir / "btc" / rel).is_dir()
    ]


def build_inventory(data_dir: Path) -> dict[str, Any]:
    ds = data_dir / "btc" / "datasets"
    b1 = _bybit_bar_days(ds / "bybit_trades_1m" / "BTCUSDT" / "1m", HOLDOUT_START)
    b5 = _bybit_bar_days(ds / "bybit_trades_5m" / "BTCUSDT" / "5m", HOLDOUT_START)
    roots = raw_roots(data_dir)
    raw: dict[str, Any] = (
        raw_coverage(roots) if roots else {"n_complete_days": 0, "days": {}, "roots": []}
    )
    bars_ok = b1["status"] == Availability.AVAILABLE and b5["status"] == Availability.AVAILABLE
    liq_days = int(raw.get("liquidation_channel_days", 0))
    n_complete = int(raw.get("n_complete_days", 0))
    bbo_minutes = int(raw.get("bbo_minutes_total", 0))

    def st(ok: bool, partial: bool = False) -> str:
        return (
            str(Availability.AVAILABLE)
            if ok
            else (str(Availability.PARTIAL) if partial else str(Availability.MISSING))
        )

    inputs: dict[str, dict[str, Any]] = {
        "bybit_5m_completed_bars": {
            "status": b5["status"],
            "role": "E1/E2 structure: boxes, ATR20, ER, confirmation closes",
            "source": "Bybit public trade archive -> trade-built 5m bars (bybit_trades_5m)",
            "detail": b5,
        },
        "bybit_1m_completed_bars": {
            "status": b1["status"],
            "role": "E1/E2 benchmark entry (open of the first minute after the decision close) and exit path",
            "source": "Bybit public trade archive -> trade-built 1m bars (bybit_trades_1m)",
            "detail": b1,
        },
        "trade_weighted_vwap": {
            "status": st(bars_ok),
            "role": "frozen 1h box VWAP = sum(p*size)/sum(size), exact from per-bar notional/size",
            "source": "bybit_trades_5m.vwap (notional / size) and volume",
        },
        "aggressor_flow_1m": {
            "status": st(bars_ok),
            "role": "taker buy/sell quantity per completed bar (historical, bar granularity only)",
            "source": "bybit_trades_1m.buy_qty / sell_qty (aggressor side from the trade archive)",
            "limit": "bar-level only; no per-second receipt-time flow before the live raw stores",
        },
        "historical_bbo_bid_ask": {
            "status": st(False, partial=bbo_minutes > 0),
            "role": "E1/E2 executable entry (ask / bid), passive-entry study, spread quintiles",
            "source": "none historical; live raw stores carry orderbook.1 / orderbook.50 for ~hours only",
            "detail": {"raw_bbo_minutes_total": bbo_minutes},
            "note": "candles cannot reconstruct the BBO; a missing BBO is never a perfect-mid fill",
        },
        "receipt_time_local": {
            "status": st(False, partial=bool(raw.get("n_days_touched"))),
            "role": "receipt-time discipline (local_receive_time) for every observation",
            "source": "only the live raw stores (rx_ms); the historical archive has exchange time only",
        },
        "liquidations": {
            "status": st(False, partial=liq_days > 0),
            "role": "E4 forced-flow shock detection (allLiquidation; S=Buy means a LONG was liquidated)",
            "source": "live raw stores only; Bybit publishes no historical liquidation archive",
            "detail": {"raw_days_with_liquidation_channel": liq_days},
            "note": "candles cannot reconstruct liquidations; Binance liquidationSnapshot not on disk",
        },
        "e4_lagged_threshold_history_30_complete_days": {
            "status": st(n_complete >= 30, partial=0 < n_complete < 30),
            "role": "same-UTC-hour lagged 99th percentile over the previous 30 complete days",
            "source": "raw stores receipt-time coverage",
            "detail": {
                "complete_days": n_complete,
                "required": 30,
                "gap_days": max(0, 30 - n_complete),
            },
        },
        "funding": {
            "status": str(Availability.PARTIAL),
            "role": "funding cost in the benchmark ledger",
            "source": "Binance Vision fundingRate — PROXY for Bybit funding (venue mismatch, labelled)",
            "detail": _dataset_span(ds / "funding"),
        },
        "binance_auxiliary_not_used_by_engines": {
            "status": str(Availability.AVAILABLE),
            "role": "context only (never substitutes a MISSING Bybit field)",
            "detail": {
                k: _dataset_span(ds / k)
                for k in (
                    "aggtrades_flow",
                    "book_depth",
                    "metrics",
                    "perp_klines",
                    "premium_index",
                    "mark_price",
                    "index_klines",
                )
            },
            "note": "book_depth is +-1..5% depth, not the BBO; aggtrades_flow is Binance, not Bybit",
        },
    }
    consequences = {
        "E1": {
            "historical_replay": "BENCHMARK LEDGER ONLY" if bars_ok else "UNAVAILABLE",
            "executable_replay": "UNAVAILABLE (no historical BBO / receipt time)",
            "analysis_label": "feasibility / cost-envelope analysis on completed Bybit bars",
        },
        "E2": {
            "historical_replay": "BENCHMARK LEDGER ONLY" if bars_ok else "UNAVAILABLE",
            "executable_replay": "UNAVAILABLE (no historical BBO / receipt time)",
            "analysis_label": "feasibility / cost-envelope analysis on completed Bybit bars",
        },
        "E4": {
            "event_study": (
                "RUNNABLE"
                if n_complete >= 30
                else "THRESHOLD UNAVAILABLE — insufficient raw history"
            ),
            "raw_complete_days": n_complete,
            "raw_days_touched": raw.get("n_days_touched", 0),
            "exact_gap": f"{max(0, 30 - n_complete)} complete UTC days of trades + allLiquidation missing",
        },
    }
    return {
        "schema": "intraday_portfolio_inventory-1",
        "generated_at_utc": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "data_dir": str(data_dir),
        "holdout_locked_never_read_from": HOLDOUT_START.isoformat(),
        "inputs": inputs,
        "raw_store_coverage": raw,
        "consequences": consequences,
    }


def write_inventory(inv: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(inv, indent=2, sort_keys=True, default=str) + "\n")
