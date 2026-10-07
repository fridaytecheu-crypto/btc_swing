"""Raw Bybit events (immutable hourly JSONL written by the collector) -> completed 5-minute rows.

Pure function of the raw files: every row of `forward_5m` is derived from messages whose exchange
timestamp falls inside the bar (trades, liquidations, ticker samples, confirmed klines) and from
the order-book state at the bar close (maintained from orderbook.50 snapshots/deltas). Bars are
finalised only when a later message proves the bar is complete (or when the processor is told the
wall clock is past the bar close plus a grace period). The processor persists file/line offsets of
the last finalised bar and replays from there after a restart, so no bar is ever produced twice
with different content. Nothing is fabricated: bars without trades are flagged, never invented.
"""

from __future__ import annotations

import gzip
import json
import math
import statistics
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

MS_5M = 300_000
BIG_TRADE_QTY = 1.0  # same threshold as the frozen V5 feature frame (features.big_trade_qty_btc)
BIG_NOTIONAL_100K = 100_000.0


@dataclass
class _Book:
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    valid: bool = False
    last_u: int | None = None

    def apply(self, msg: dict[str, Any]) -> None:
        data = msg.get("data") or {}
        typ = msg.get("type")
        if typ == "snapshot":
            self.bids = {float(p): float(q) for p, q in data.get("b", [])}
            self.asks = {float(p): float(q) for p, q in data.get("a", [])}
            self.valid = True
        elif self.valid:
            for p, q in data.get("b", []):
                fp, fq = float(p), float(q)
                if fq == 0:
                    self.bids.pop(fp, None)
                else:
                    self.bids[fp] = fq
            for p, q in data.get("a", []):
                fp, fq = float(p), float(q)
                if fq == 0:
                    self.asks.pop(fp, None)
                else:
                    self.asks[fp] = fq
        u = data.get("u")
        self.last_u = int(u) if u is not None else self.last_u

    def summary(self, depth_pct: float) -> dict[str, float]:
        out = {
            "book_valid": 1.0 if self.valid and self.bids and self.asks else 0.0,
            "book_bid1": math.nan,
            "book_ask1": math.nan,
            "book_bid1_size": math.nan,
            "book_ask1_size": math.nan,
            "book_spread_bps": math.nan,
            "book_bid_depth_all": math.nan,
            "book_ask_depth_all": math.nan,
            "book_bid_depth_pct": math.nan,
            "book_ask_depth_pct": math.nan,
            "book_bid_range_pct": math.nan,
            "book_ask_range_pct": math.nan,
            "book_levels_bid": float(len(self.bids)),
            "book_levels_ask": float(len(self.asks)),
        }
        if not out["book_valid"]:
            return out
        bb, ba = max(self.bids), min(self.asks)
        mid = 0.5 * (bb + ba)
        lo, hi = mid * (1 - depth_pct / 100.0), mid * (1 + depth_pct / 100.0)
        out.update(
            {
                "book_bid1": bb,
                "book_ask1": ba,
                "book_bid1_size": self.bids[bb],
                "book_ask1_size": self.asks[ba],
                "book_spread_bps": (ba - bb) / mid * 1e4,
                "book_bid_depth_all": sum(self.bids.values()),
                "book_ask_depth_all": sum(self.asks.values()),
                "book_bid_depth_pct": sum(q for p, q in self.bids.items() if p >= lo),
                "book_ask_depth_pct": sum(q for p, q in self.asks.items() if p <= hi),
                "book_bid_range_pct": (mid - min(self.bids)) / mid * 100.0,
                "book_ask_range_pct": (max(self.asks) - mid) / mid * 100.0,
            }
        )
        return out


@dataclass
class _Ticker:
    """Last-known ticker fields (Bybit sends deltas with only the changed keys)."""

    state: dict[str, str] = field(default_factory=dict)

    def apply(self, msg: dict[str, Any]) -> None:
        data = msg.get("data") or {}
        if isinstance(data, dict):
            self.state.update({k: str(v) for k, v in data.items() if v not in (None, "")})

    def f(self, key: str) -> float:
        try:
            return float(self.state[key])
        except (KeyError, ValueError):
            return math.nan


@dataclass
class _Bar:
    open_ms: int
    open: float = math.nan
    high: float = -math.inf
    low: float = math.inf
    close: float = math.nan
    volume: float = 0.0
    quote_volume: float = 0.0
    trades: int = 0
    n_buy: int = 0
    n_sell: int = 0
    buy_qty: float = 0.0
    sell_qty: float = 0.0
    buy_notional: float = 0.0
    sell_notional: float = 0.0
    big_buy_qty: float = 0.0
    big_sell_qty: float = 0.0
    big_buy_notional_100k: float = 0.0
    big_sell_notional_100k: float = 0.0
    max_trade_qty: float = 0.0
    liq_n_long: int = 0
    liq_n_short: int = 0
    liq_long_qty: float = 0.0
    liq_short_qty: float = 0.0
    liq_long_notional: float = 0.0
    liq_short_notional: float = 0.0
    liq_max_single_qty: float = 0.0
    kline_close: float = math.nan
    kline_volume: float = math.nan
    kline_confirmed: float = 0.0
    mark_open: float = math.nan
    mark_high: float = -math.inf
    mark_low: float = math.inf
    mark_close: float = math.nan
    index_open: float = math.nan
    index_high: float = -math.inf
    index_low: float = math.inf
    index_close: float = math.nan
    n_msgs: int = 0
    n_ticker_msgs: int = 0
    latencies: list[float] = field(default_factory=list)

    def add_trade(self, price: float, qty: float, is_buy: bool) -> None:
        if math.isnan(self.open):
            self.open = price
        self.high, self.low, self.close = max(self.high, price), min(self.low, price), price
        notional = price * qty
        self.volume += qty
        self.quote_volume += notional
        self.trades += 1
        self.max_trade_qty = max(self.max_trade_qty, qty)
        if is_buy:
            self.n_buy += 1
            self.buy_qty += qty
            self.buy_notional += notional
            if qty >= BIG_TRADE_QTY:
                self.big_buy_qty += qty
            if notional >= BIG_NOTIONAL_100K:
                self.big_buy_notional_100k += notional
        else:
            self.n_sell += 1
            self.sell_qty += qty
            self.sell_notional += notional
            if qty >= BIG_TRADE_QTY:
                self.big_sell_qty += qty
            if notional >= BIG_NOTIONAL_100K:
                self.big_sell_notional_100k += notional

    def add_mark_index(self, mark: float, index: float) -> None:
        if not math.isnan(mark):
            if math.isnan(self.mark_open):
                self.mark_open = mark
            self.mark_high, self.mark_low, self.mark_close = (
                max(self.mark_high, mark),
                min(self.mark_low, mark),
                mark,
            )
        if not math.isnan(index):
            if math.isnan(self.index_open):
                self.index_open = index
            self.index_high, self.index_low, self.index_close = (
                max(self.index_high, index),
                min(self.index_low, index),
                index,
            )

    def row(self, book: dict[str, float], tk: _Ticker, depth_pct: float) -> dict[str, Any]:
        has_trades = self.trades > 0
        vwap = self.quote_volume / self.volume if self.volume > 0 else math.nan
        r: dict[str, Any] = {
            "open_time_ms": self.open_ms,
            "close_time_ms": self.open_ms + MS_5M,
            "open": self.open if has_trades else math.nan,
            "high": self.high if has_trades else math.nan,
            "low": self.low if has_trades else math.nan,
            "close": self.close if has_trades else math.nan,
            "volume": self.volume,
            "quote_volume": self.quote_volume,
            "trades": self.trades,
            "taker_buy_volume": self.buy_qty,
            "taker_buy_quote_volume": self.buy_notional,
            "n_buy": self.n_buy,
            "n_sell": self.n_sell,
            "buy_qty": self.buy_qty,
            "sell_qty": self.sell_qty,
            "buy_notional": self.buy_notional,
            "sell_notional": self.sell_notional,
            "big_buy_qty": self.big_buy_qty,
            "big_sell_qty": self.big_sell_qty,
            "big_buy_notional_100k": self.big_buy_notional_100k,
            "big_sell_notional_100k": self.big_sell_notional_100k,
            "max_trade_qty": self.max_trade_qty,
            "vwap": vwap,
            "liq_n_long": self.liq_n_long,
            "liq_n_short": self.liq_n_short,
            "liq_long_qty": self.liq_long_qty,
            "liq_short_qty": self.liq_short_qty,
            "liq_long_notional": self.liq_long_notional,
            "liq_short_notional": self.liq_short_notional,
            "liq_max_single_qty": self.liq_max_single_qty,
            "kline_close": self.kline_close,
            "kline_volume": self.kline_volume,
            "kline_confirmed": self.kline_confirmed,
            "mark_open": self.mark_open,
            "mark_high": self.mark_high if not math.isinf(self.mark_high) else math.nan,
            "mark_low": self.mark_low if not math.isinf(self.mark_low) else math.nan,
            "mark_close": self.mark_close,
            "index_open": self.index_open,
            "index_high": self.index_high if not math.isinf(self.index_high) else math.nan,
            "index_low": self.index_low if not math.isinf(self.index_low) else math.nan,
            "index_close": self.index_close,
            "oi_last": tk.f("openInterest"),
            "oi_value_last": tk.f("openInterestValue"),
            "funding_rate_last": tk.f("fundingRate"),
            "next_funding_ms": tk.f("nextFundingTime"),
            "ticker_bid1": tk.f("bid1Price"),
            "ticker_ask1": tk.f("ask1Price"),
            "ticker_bid1_size": tk.f("bid1Size"),
            "ticker_ask1_size": tk.f("ask1Size"),
            "volume24h": tk.f("volume24h"),
            "turnover24h": tk.f("turnover24h"),
            "n_msgs": self.n_msgs,
            "n_ticker_msgs": self.n_ticker_msgs,
            "latency_p50_ms": float(statistics.median(self.latencies))
            if self.latencies
            else math.nan,
            "gap_filled": 0.0,
            "source": "bybit_ws",
        }
        r.update(book)
        return r


class RawProcessor:
    def __init__(
        self,
        raw_dir: Path,
        out_dir: Path,
        state_path: Path,
        depth_pct: float = 1.0,
        min_ts_ms: int = 0,
    ) -> None:
        self.raw_dir = raw_dir
        self.min_ts_ms = min_ts_ms  # messages before the observation start are ignored
        self.out_dir = out_dir
        self.state_path = state_path
        self.depth_pct = depth_pct
        self.book = _Book()
        self.ticker = _Ticker()
        self.state: dict[str, Any] = {
            "file": "",
            "line": 0,
            "last_bar_close_ms": 0,
            "bars": 0,
            "ticker": {},
        }
        if state_path.exists():
            self.state = json.loads(state_path.read_text())
            self.ticker.state = dict(self.state.get("ticker", {}))
        self._cur: _Bar | None = None
        self._pending_rows: list[dict[str, Any]] = []
        self._seen: OrderedDict[str, None] = OrderedDict()  # payload hashes (exact duplicates)
        self.duplicates = 0
        self._cur_sha = ""
        self._pos_file = ""
        self._pos_line = 0

    # ------------------------------------------------------------------ files
    def _files(self) -> list[Path]:
        return sorted(p for p in self.raw_dir.glob("*/*.jsonl*") if p.suffix in (".jsonl", ".gz"))

    @staticmethod
    def _key(p: Path) -> str:
        return str(p).removesuffix(".gz")

    @staticmethod
    def _iter_lines(p: Path) -> Any:
        if p.suffix == ".gz":
            with gzip.open(p, "rt", encoding="utf-8") as f:
                yield from f
        else:
            with p.open("r", encoding="utf-8") as f:
                yield from f

    # ------------------------------------------------------------------ messages
    def _finalise(self, bar: _Bar) -> None:
        row = bar.row(self.book.summary(self.depth_pct), self.ticker, self.depth_pct)
        self._pending_rows.append(row)
        self.state["last_bar_close_ms"] = row["close_time_ms"]
        self.state["bars"] = int(self.state.get("bars", 0)) + 1
        self.state["file"], self.state["line"] = self._pos_file, self._pos_line
        self.state["replay_sha"] = self._cur_sha  # replayed next time: not a duplicate
        self.state["ticker"] = dict(self.ticker.state)

    def _bar_for(self, ts: int) -> _Bar | None:
        """Return the accumulator for ts, finalising earlier bars. Messages older than the last
        finalised bar are ignored (late or replayed)."""
        open_ms = ts // MS_5M * MS_5M
        if open_ms + MS_5M <= int(self.state.get("last_bar_close_ms", 0)):
            return None
        if self._cur is None:
            self._cur = _Bar(open_ms)
        while self._cur.open_ms < open_ms:
            self._finalise(self._cur)
            self._cur = _Bar(self._cur.open_ms + MS_5M)
        return self._cur

    def _on_row(self, r: dict[str, Any]) -> None:
        ch = str(r.get("channel", ""))
        try:
            msg = json.loads(r["raw"])
        except (KeyError, json.JSONDecodeError):
            return
        ts_ex = r.get("ts_exchange_ms")
        ts_rx = r.get("ts_received_ms")
        if not isinstance(ts_ex, int) or ts_ex < self.min_ts_ms:
            return
        h = str(r.get("sha256", ""))
        if h:
            if h in self._seen:
                self.duplicates += 1
                return
            self._seen[h] = None
            if len(self._seen) > 200_000:
                self._seen.popitem(last=False)
        self._cur_sha = h
        if ch.startswith("orderbook.50"):
            self.book.apply(msg)
            bar = self._bar_for(ts_ex)
            if bar is not None:
                bar.n_msgs += 1
            return
        if ch.startswith("kline."):
            # a confirmed kline arrives AFTER its bar closed: attach it before any later message
            # can finalise that bar (the kline's own exchange ts belongs to the next bar)
            for k in msg.get("data") or []:
                if k.get("confirm") and int(k["start"]) >= self.min_ts_ms:
                    b = self._bar_for(int(k["start"]))
                    if b is not None:
                        b.kline_close = float(k["close"])
                        b.kline_volume = float(k["volume"])
                        b.kline_confirmed = 1.0
            return
        bar = self._bar_for(ts_ex)
        if ch.startswith("tickers"):
            self.ticker.apply(msg)
            if bar is not None:
                bar.n_msgs += 1
                bar.n_ticker_msgs += 1
                bar.add_mark_index(self.ticker.f("markPrice"), self.ticker.f("indexPrice"))
            return
        if bar is None:
            return
        bar.n_msgs += 1
        if isinstance(ts_rx, int):
            bar.latencies.append(float(ts_rx - ts_ex))
        if ch.startswith("publicTrade"):
            for t in msg.get("data") or []:
                tt = int(t.get("T", ts_ex))
                b = self._bar_for(tt)
                if b is None:
                    continue
                b.add_trade(float(t["p"]), float(t["v"]), t.get("S") == "Buy")
        elif ch.startswith("allLiquidation"):
            for liq in msg.get("data") or []:
                qty, price = float(liq.get("v", 0)), float(liq.get("p", 0))
                tt = int(liq.get("T", ts_ex))
                b = self._bar_for(tt)
                if b is None:
                    continue
                b.liq_max_single_qty = max(b.liq_max_single_qty, qty)
                if liq.get("S") == "Buy":  # a Buy liquidation order closes a LONG position
                    b.liq_n_long += 1
                    b.liq_long_qty += qty
                    b.liq_long_notional += qty * price
                else:
                    b.liq_n_short += 1
                    b.liq_short_qty += qty
                    b.liq_short_notional += qty * price

    # ------------------------------------------------------------------ run
    def process(self, now_ms: int, grace_ms: int = 20_000) -> int:
        """Consume new raw lines; finalise bars proven complete by later messages or by the wall
        clock (now - grace past the bar close). Returns the number of bars written."""
        start_file, start_line = str(self.state.get("file", "")), int(self.state.get("line", 0))
        self._cur = None
        self._pending_rows = []
        self._seen.pop(str(self.state.get("replay_sha", "")), None)
        started = start_file == ""
        for p in self._files():
            key = self._key(p)
            if not started:
                if key < start_file:
                    continue
                started = True
            skip = start_line if key == start_file else 0
            for i, line in enumerate(self._iter_lines(p)):
                if i < skip or not line.strip():
                    continue
                self._pos_file, self._pos_line = key, i  # replayed after a restart: it may
                # belong to the bar after the one it finalises
                try:
                    self._on_row(json.loads(line))
                except (ValueError, KeyError, TypeError):
                    continue
        if self._cur is not None and self._cur.open_ms + MS_5M + grace_ms <= now_ms:
            self._finalise(self._cur)
            self._cur = None
        n = len(self._pending_rows)
        if n:
            self._write(self._pending_rows)
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self.state, indent=1, sort_keys=True))
        return n

    def _write(self, rows: list[dict[str, Any]]) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        df = pl.DataFrame(rows)
        for day, part in df.with_columns(
            (pl.col("open_time_ms") // 86_400_000).alias("_d")
        ).group_by("_d"):
            d = datetime.fromtimestamp(int(day[0]) * 86400, tz=UTC).strftime("%Y-%m-%d")
            path = self.out_dir / f"{d}.parquet"
            new = part.drop("_d")
            if path.exists():
                old = pl.read_parquet(path)
                new = (
                    pl.concat([old, new], how="vertical_relaxed")
                    .unique(subset=["open_time_ms"], keep="first", maintain_order=True)
                    .sort("open_time_ms")
                )
            new.write_parquet(path)


def load_forward_bars(out_dir: Path) -> pl.DataFrame:
    files = sorted(out_dir.glob("*.parquet"))
    if not files:
        return pl.DataFrame()
    return pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed").sort(
        "open_time_ms"
    )


def rebuild_forward_bars(raw_dir: Path, out_dir: Path, now_ms: int, depth_pct: float = 1.0) -> int:
    """Audit helper: rebuild every bar from the raw files into a fresh directory (bars are a pure
    function of the raw data; comparing with the incrementally built partitions proves it)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = RawProcessor(raw_dir, out_dir, out_dir / "state.json", depth_pct)
    return proc.process(now_ms)
