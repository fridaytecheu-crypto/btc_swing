from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from btc_swing.v5.forward.config import ForwardPaths, load_forward_config, load_frozen_v5
from btc_swing.v5.forward.freeze import check_freeze, write_freeze
from btc_swing.v5.forward.pipeline import _gap_fill
from btc_swing.v5.forward.raw import MS_5M, RawProcessor, load_forward_bars
from btc_swing.v5.forward.report import render_day
from btc_swing.v5.forward.seed import aggregate_bybit_trades

ROOT = Path(__file__).resolve().parents[2]
T0 = int(datetime(2026, 10, 7, 12, 0, tzinfo=UTC).timestamp() * 1000)


def _row(channel: str, ts: int, payload: dict[str, object]) -> str:
    raw = json.dumps({"topic": channel, **payload})
    return json.dumps(
        {
            "ts_received_ms": ts + 40,
            "ts_exchange_ms": ts,
            "symbol": "BTCUSDT",
            "channel": channel,
            "type": payload.get("type"),
            "schema_version": "t",
            "sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "raw": raw,
        }
    )


def _write_raw(raw_dir: Path, lines: list[str]) -> None:
    d = raw_dir / "2026-10-07"
    d.mkdir(parents=True, exist_ok=True)
    (d / "12.jsonl").write_text("\n".join(lines) + "\n")


def _messages() -> list[str]:
    msgs = [
        _row(
            "orderbook.50.BTCUSDT",
            T0 + 1000,
            {
                "type": "snapshot",
                "ts": T0 + 1000,
                "data": {
                    "s": "BTCUSDT",
                    "b": [["100.0", "2.0"], ["99.5", "1.0"]],
                    "a": [["100.5", "1.5"], ["101.0", "3.0"]],
                    "u": 1,
                    "seq": 1,
                },
            },
        ),
        _row(
            "tickers.BTCUSDT",
            T0 + 1500,
            {
                "type": "snapshot",
                "ts": T0 + 1500,
                "data": {
                    "symbol": "BTCUSDT",
                    "markPrice": "100.2",
                    "indexPrice": "100.1",
                    "openInterest": "5000",
                    "openInterestValue": "500000",
                    "fundingRate": "0.0001",
                    "nextFundingTime": str(T0 + 3_600_000),
                    "bid1Price": "100.0",
                    "ask1Price": "100.5",
                },
            },
        ),
        _row(
            "publicTrade.BTCUSDT",
            T0 + 2000,
            {
                "type": "snapshot",
                "ts": T0 + 2000,
                "data": [
                    {"T": T0 + 2000, "S": "Buy", "v": "1.5", "p": "100.5", "i": "a"},
                    {"T": T0 + 2500, "S": "Sell", "v": "0.5", "p": "100.0", "i": "b"},
                ],
            },
        ),
        _row(
            "allLiquidation.BTCUSDT",
            T0 + 2600,
            {
                "type": "snapshot",
                "ts": T0 + 2600,
                "data": [{"T": T0 + 2600, "S": "Buy", "v": "0.3", "p": "100.0"}],
            },
        ),
        _row(
            "tickers.BTCUSDT",
            T0 + 60_000,
            {
                "type": "delta",
                "ts": T0 + 60_000,
                "data": {"symbol": "BTCUSDT", "openInterest": "5010", "markPrice": "100.4"},
            },
        ),
        _row(
            "orderbook.50.BTCUSDT",
            T0 + 61_000,
            {
                "type": "delta",
                "ts": T0 + 61_000,
                "data": {
                    "s": "BTCUSDT",
                    "b": [["99.5", "0"]],
                    "a": [["100.5", "2.0"]],
                    "u": 2,
                    "seq": 2,
                },
            },
        ),
        _row(
            "orderbook.50.BTCUSDT",
            T0 + MS_5M + 50,
            {
                "type": "delta",
                "ts": T0 + MS_5M + 50,
                "data": {"s": "BTCUSDT", "b": [], "a": [], "u": 3, "seq": 3},
            },
        ),
        _row(
            "kline.5.BTCUSDT",
            T0 + MS_5M + 100,
            {
                "type": "snapshot",
                "ts": T0 + MS_5M + 100,
                "data": [
                    {
                        "start": T0,
                        "end": T0 + MS_5M - 1,
                        "close": "100.0",
                        "volume": "2.0",
                        "confirm": True,
                    }
                ],
            },
        ),
        # next bar: one trade proves the first bar complete
        _row(
            "publicTrade.BTCUSDT",
            T0 + MS_5M + 5000,
            {
                "type": "snapshot",
                "ts": T0 + MS_5M + 5000,
                "data": [{"T": T0 + MS_5M + 5000, "S": "Sell", "v": "0.2", "p": "99.0", "i": "c"}],
            },
        ),
    ]
    return msgs


def test_raw_processor_builds_bars_and_resumes(tmp_path: Path) -> None:
    raw, out = tmp_path / "raw", tmp_path / "bars"
    msgs = _messages()
    _write_raw(raw, msgs[:6])  # first bar still open: nothing proves it complete
    p = RawProcessor(raw, out, tmp_path / "state.json")
    assert p.process(T0 + 120_000) == 0
    _write_raw(raw, msgs)
    p = RawProcessor(raw, out, tmp_path / "state.json")  # restart: replays from the stored offset
    assert p.process(T0 + MS_5M + 10_000) == 1
    df = load_forward_bars(out)
    r = df.row(0, named=True)
    assert (
        r["open_time_ms"] == T0
        and r["trades"] == 2
        and r["buy_qty"] == 1.5
        and r["sell_qty"] == 0.5
    )
    assert r["high"] == 100.5 and r["low"] == 100.0 and r["volume"] == 2.0
    assert r["liq_n_long"] == 1 and r["liq_long_qty"] == 0.3 and r["liq_n_short"] == 0
    assert (
        r["oi_last"] == 5010.0 and r["mark_close"] == 100.4 and r["index_close"] == 100.1
    )  # ticker deltas merge
    assert (
        r["book_valid"] == 1.0 and r["book_ask1_size"] == 2.0 and r["book_levels_bid"] == 1
    )  # delta applied
    assert r["kline_confirmed"] == 1.0 and r["kline_close"] == 100.0
    # the open second bar is finalised only by the wall clock past its close + grace
    assert p.process(T0 + 2 * MS_5M + 30_000) == 1
    assert load_forward_bars(out).height == 2
    # observation-start filter: a processor starting later ignores earlier messages
    p3 = RawProcessor(raw, tmp_path / "bars3", tmp_path / "state3.json", min_ts_ms=T0 + MS_5M)
    assert p3.process(T0 + 2 * MS_5M + 30_000) == 1


def test_gap_fill_flags_and_limits() -> None:
    base = {
        "open_time_ms": T0,
        "close_time_ms": T0 + MS_5M,
        "open": 1.0,
        "high": 1.0,
        "low": 1.0,
        "close": 2.0,
        "volume": 1.0,
        "quote_volume": 2.0,
        "trades": 1,
        "taker_buy_volume": 1.0,
        "taker_buy_quote_volume": 2.0,
        "gap_filled": 0.0,
        "source": "x",
    }
    df = pl.DataFrame(
        [base, {**base, "open_time_ms": T0 + 3 * MS_5M, "close_time_ms": T0 + 4 * MS_5M}]
    )
    filled, gaps = _gap_fill(df, 10)
    assert filled.height == 4 and gaps[0]["bars"] == 2 and gaps[0]["filled"]
    assert filled.filter(pl.col("gap_filled") == 1.0)["close"].to_list() == [2.0, 2.0]
    assert filled.filter(pl.col("gap_filled") == 1.0)["volume"].to_list() == [0.0, 0.0]
    not_filled, gaps2 = _gap_fill(df, 1)
    assert not_filled.height == 2 and not gaps2[0]["filled"]


def test_seed_aggregation_matches_processor_conventions() -> None:
    import gzip

    csv = (
        "timestamp,symbol,side,size,price,tickDirection,trdMatchID,grossValue,homeNotional,foreignNotional,RPI\n"
        + f"{T0 / 1000 + 1},BTCUSDT,Buy,1.5,100.5,x,id1,0,0,0,0\n"
        + f"{T0 / 1000 + 2},BTCUSDT,Sell,0.5,100.0,x,id2,0,0,0,0\n"
    )
    df = aggregate_bybit_trades(gzip.compress(csv.encode()))
    r = df.row(0, named=True)
    assert (
        r["open_time_ms"] == T0
        and r["buy_qty"] == 1.5
        and r["sell_qty"] == 0.5
        and r["big_buy_qty"] == 1.5
        and r["trades"] == 2
    )


def test_freeze_is_idempotent_and_guards_the_hash(tmp_path: Path) -> None:
    fcfg = load_forward_config(ROOT / "config" / "btc_swing_v5_forward.yaml")
    cfg = load_frozen_v5(fcfg, ROOT)
    p = tmp_path / "freeze.json"
    rec = write_freeze(cfg, fcfg, p)
    assert rec["v5_config_hash"] == cfg.config_hash and write_freeze(cfg, fcfg, p) == rec
    assert check_freeze(cfg, p)["observation_start_ms"] == rec["observation_start_ms"]
    other = cfg.model_copy(update={"risk": cfg.risk.model_copy(update={"risk_per_trade": 0.002})})
    try:
        check_freeze(other, p)
        raise AssertionError("changed strategy must be refused")
    except RuntimeError:
        pass
    paths = ForwardPaths(tmp_path, "BTCUSDT")
    assert paths.signals_file.parent.exists() and paths.bars_dir.exists()


def test_daily_report_renders_without_data() -> None:
    snap = {
        "day": "2026-10-07",
        "partial": True,
        "generated_at": "2026-10-07T15:00:00+00:00",
        "observation_start": "2026-10-07T14:59:00+00:00",
        "frozen_v5_config_hash": "abc" * 22,
        "collector": {
            "process_alive": False,
            "state_saved_at": None,
            "stats_current_process": {},
            "by_topic_current_process": {},
            "latency_ms_current_process": {},
            "raw_files_today": 0,
            "raw_bytes_today": 0,
            "newest_raw_file_mtime": None,
            "last_orderbook_u": None,
        },
        "bars": {
            "forward_bars_today": 0,
            "expected_if_full_day": 288,
            "gap_filled_today": 0,
            "bars_without_trades": 0,
            "messages_today": 0,
            "latency_p50_ms_median": float("nan"),
            "book_valid_share": float("nan"),
            "kline_confirmed_share": float("nan"),
            "first_bar": None,
            "last_bar_close": None,
        },
        "warmup": None,
        "cycles_today": 0,
        "signals_today": 0,
        "signals_today_by_family_side": [],
        "signals_today_list": [],
        "signals_cumulative": 0,
        "paper_trades_opened_today": 0,
        "paper_trades_closed_today": 0,
        "paper_closed_today_list": [],
        "open_paper_position": None,
        "daily_pnl_usdt": 0.0,
        "cumulative": {},
        "family_breakdown_cumulative": [],
        "matured_outcomes_today": 0,
        "outcomes_cumulative": {"n": 0},
        "liquidations_today": {},
    }
    md = render_day(snap)
    assert "(PARTIAL)" in md and "## Paper ledger" in md and "no liquidation data yet" in md
