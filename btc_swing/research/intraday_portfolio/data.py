"""Read-only loaders for the existing local datasets and raw stores. Nothing here writes.

- Bybit trade-built bars (`data/btc/datasets/bybit_trades_{1m,5m}/BTCUSDT/<tf>/<day>.parquet`): exchange
  trade times (open / close / first / last trade ms), aggressor-side BTC size (buy_qty / sell_qty),
  trade-weighted VWAP. No receipt time, no BBO, no liquidations (archive).
- Bybit public WebSocket raw stores (V5 collector and V10-format): one JSON row per message with
  ts_received_ms (local receipt), ts_exchange_ms, channel (publicTrade / orderbook.1 / orderbook.50 /
  allLiquidation / tickers / kline) and the untouched raw payload.
- Binance USDT-perp funding (8 h) as a PROXY for Bybit funding (labelled, never silently substituted)."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from btc_swing.research.intraday_portfolio.contract import MS_1M, MS_5M, Availability, Provenance
from btc_swing.research.intraday_portfolio.primitives import Bars, completed_bars

HOLDOUT_START = date(
    2025, 7, 1
)  # locked once-only window (repo policy): refused unless explicitly allowed


def _day_files(root: Path, start: date | None, end: date | None, allow_holdout: bool) -> list[Path]:
    out = []
    for p in sorted(root.glob("*.parquet")):
        try:
            d = date.fromisoformat(p.stem)
        except ValueError:
            continue
        if (start and d < start) or (end and d > end):
            continue
        if d >= HOLDOUT_START and not allow_holdout:
            continue
        out.append(p)
    return out


def load_bybit_bars(
    data_dir: Path,
    tf: str,
    start: date | None = None,
    end: date | None = None,
    allow_holdout: bool = False,
) -> tuple[Bars, Provenance, list[str]]:
    """Completed Bybit bars for `tf` in {"1m", "5m"} with provenance and the list of day files used."""
    root = data_dir / "btc" / "datasets" / f"bybit_trades_{tf}" / "BTCUSDT" / tf
    files = _day_files(root, start, end, allow_holdout)
    step = MS_1M if tf == "1m" else MS_5M
    if not files:
        empty = np.zeros(0)
        return (
            completed_bars(
                np.zeros(0, dtype=np.int64),
                empty,
                empty,
                empty,
                empty,
                empty,
                empty,
                empty,
                empty,
                step,
            ),
            Provenance(f"bybit_trades_{tf}", Availability.MISSING, provenance=str(root)),
            [],
        )
    cols = ["open_time_ms", "open", "high", "low", "close", "volume", "vwap", "buy_qty", "sell_qty"]
    df = pl.concat([pl.read_parquet(f, columns=cols) for f in files], how="vertical")
    bars = completed_bars(
        df["open_time_ms"].to_numpy().astype(np.int64),
        df["open"].to_numpy().astype(np.float64),
        df["high"].to_numpy().astype(np.float64),
        df["low"].to_numpy().astype(np.float64),
        df["close"].to_numpy().astype(np.float64),
        df["volume"].to_numpy().astype(np.float64),
        df["vwap"].to_numpy().astype(np.float64),
        df["buy_qty"].to_numpy().astype(np.float64),
        df["sell_qty"].to_numpy().astype(np.float64),
        step,
    )
    prov = Provenance(
        f"bybit_trades_{tf} parquet (Bybit public trade archive, trade-built bars)",
        Availability.AVAILABLE,
        provenance=f"{files[0].stem}..{files[-1].stem} ({len(files)} days)",
    )
    return bars, prov, [f.stem for f in files]


def load_funding_proxy(data_dir: Path) -> tuple[np.ndarray, np.ndarray, Provenance]:
    """Binance USDT-perp funding (time_ms, rate) as a labelled PROXY for Bybit funding."""
    root = data_dir / "btc" / "datasets" / "funding" / "BTCUSDT" / "na"
    files = sorted(p for p in root.glob("*.parquet"))
    if not files:
        return np.zeros(0, dtype=np.int64), np.zeros(0), Provenance("funding", Availability.MISSING)
    df = pl.concat([pl.read_parquet(f, columns=["time_ms", "funding_rate"]) for f in files]).sort(
        "time_ms"
    )
    return (
        df["time_ms"].to_numpy().astype(np.int64),
        df["funding_rate"].to_numpy().astype(np.float64),
        Provenance(
            "binance_vision fundingRate (USDT-perp) — PROXY for Bybit funding",
            Availability.PARTIAL,
            provenance="venue mismatch: Binance rate applied to a Bybit position; labelled proxy",
        ),
    )


# ----------------------------------------------------------------------------- raw Bybit public stores
@dataclass
class RawRow:
    rx_ms: int  # local receipt
    ex_ms: int | None  # exchange timestamp of the message
    channel: str
    data: Any
    typ: str | None


def iter_raw_rows(hour_file: Path) -> list[RawRow]:
    """Rows of one hourly raw file (V5 collector or V10-format; identical schema `bybit-public-raw-1`)."""
    out: list[RawRow] = []
    if hour_file.suffix == ".gz":
        with gzip.open(hour_file, "rt", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    else:
        text = hour_file.read_text(encoding="utf-8", errors="replace")
    if True:
        for line in text.splitlines():
            try:
                r = json.loads(line)
                d = json.loads(r["raw"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            if "topic" not in d:
                continue
            ex = d.get("ts")
            out.append(
                RawRow(
                    int(r["ts_received_ms"]),
                    int(ex) if isinstance(ex, int | float) else None,
                    str(d["topic"]),
                    d.get("data"),
                    d.get("type"),
                )
            )
    return out


def raw_hour_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        files += [p for p in root.glob("*/*.jsonl*") if p.suffix in (".jsonl", ".gz")]
    return sorted(set(files), key=lambda p: (p.parent.name, p.name))


def raw_coverage(roots: list[Path]) -> dict[str, Any]:
    """Receipt-time coverage per channel: minutes with at least one message, per UTC day; the
    number of complete UTC days (>= 1380 covered minutes for trades AND liquidation channel present)."""
    per_day: dict[str, dict[str, set[int]]] = {}
    for f in raw_hour_files(roots):
        for r in iter_raw_rows(f):
            day = datetime.fromtimestamp(r.rx_ms / 1000, tz=UTC).date().isoformat()
            ch = r.channel.split(".")[0] + (
                "." + r.channel.split(".")[1] if r.channel.startswith("orderbook") else ""
            )
            per_day.setdefault(day, {}).setdefault(ch, set()).add(r.rx_ms // MS_1M)
    days = {d: {ch: len(m) for ch, m in chs.items()} for d, chs in sorted(per_day.items())}
    trade_minutes = sum(v.get("publicTrade", 0) for v in days.values())
    complete_days = [
        d for d, v in days.items() if v.get("publicTrade", 0) >= 1380 and "allLiquidation" in v
    ]
    bbo_minutes = sum(max(v.get("orderbook.1", 0), v.get("orderbook.50", 0)) for v in days.values())
    return {
        "roots": [str(r) for r in roots],
        "days": days,
        "n_days_touched": len(days),
        "complete_days": complete_days,
        "n_complete_days": len(complete_days),
        "trade_minutes_total": trade_minutes,
        "bbo_minutes_total": bbo_minutes,
        "liquidation_channel_days": sum(1 for v in days.values() if "allLiquidation" in v),
        "required_complete_days_for_e4_threshold": 30,
    }


def utc_day(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date()


def day_range(d0: date, d1: date) -> list[date]:
    return [d0 + timedelta(days=k) for k in range((d1 - d0).days + 1)]
