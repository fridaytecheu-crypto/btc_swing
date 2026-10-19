"""E4 event-study components: book integrity, receipt-time grid, stale BBO, fail-closed thresholds,
shock dedup, mutually exclusive arms with long/short mirror, reordered raw input invariance."""

from __future__ import annotations

import gzip
import json
import math
import random
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from btc_swing.research.intraday_portfolio import e4
from btc_swing.research.intraday_portfolio.contract import MS_1M
from btc_swing.research.intraday_portfolio.data import raw_coverage
from btc_swing.research.intraday_portfolio.primitives import Bars, completed_bars

DAY0 = int(datetime(2026, 1, 5, tzinfo=UTC).timestamp() * 1000)


def _grid(
    n: int, px: float = 10_000.0, bid: float | None = 9_999.0, ask: float | None = 10_001.0
) -> e4.Grid:
    t = (DAY0 + np.arange(n, dtype=np.int64) * 1000).astype(np.int64)
    nan = np.full(n, np.nan)
    return e4.Grid(
        t,
        np.full(n, px),
        np.full(n, px),
        np.zeros(n),
        np.zeros(n),
        np.zeros(n),
        np.zeros(n),
        np.full(n, bid if bid is not None else np.nan),
        np.full(n, ask if ask is not None else np.nan),
        nan,
        n,
    )


def test_book_sequence_gap_and_crossed_book_invalidate() -> None:
    b = e4._Book()
    assert b.apply("delta", {"u": 1, "b": [["100", "1"]], "a": [["101", "1"]]}) is None
    assert b.apply("snapshot", {"u": 2, "b": [["100", "1"]], "a": [["101", "1"]]}) == (100.0, 101.0)
    assert b.apply("delta", {"u": 3, "b": [["100.5", "1"]], "a": []}) == (100.5, 101.0)
    assert b.apply("delta", {"u": 5, "b": [["100.7", "1"]], "a": []}) is None  # gap: u 4 missing
    assert b.apply("delta", {"u": 6, "b": [], "a": []}) is None  # stays invalid until a snapshot
    assert (
        b.apply("snapshot", {"u": 7, "b": [["102", "1"]], "a": [["101", "1"]]}) is None
    )  # crossed


def test_lagged_thresholds_fail_closed_without_complete_history() -> None:
    n = 3 * 86400
    g = _grid(n)
    col = np.random.default_rng(0).random(n)
    # all three days complete -> with min_days=2 only day 3 has thresholds, days 1-2 none
    thr = e4.lagged_thresholds(g, col, min_days=2)
    days = sorted({d for d, _ in thr})
    assert days == [e4.day_of(int(g.t_s[-1]))] and len(thr) == 24
    # an incomplete day (no trades for 2 hours) breaks completeness -> nothing for day 3 either
    g2 = _grid(n)
    g2.last_px[0:7200] = np.nan
    assert e4.lagged_thresholds(g2, col, min_days=2) == {}
    # the full requirement (30 days) is never met by 3 days
    assert e4.lagged_thresholds(g, col) == {}


def test_detect_dedupes_within_10_minutes_and_keeps_first_crossing() -> None:
    g = _grid(3600)
    g.atr[:] = 20.0
    g.px_60s_ago[:] = 10_000.0
    for t_s in (100, 400, 900):  # 100 and 400 are < 600 s apart; 900 is a new parent
        g.liq_sell_60[t_s] = 1e6
        g.last_px[t_s] = 10_000.0 - 15.0  # 60 s move -15 >= 0.5 ATR down
    key = (e4.day_of(DAY0), e4.hour_of(DAY0))
    shocks = e4.detect(g, {key: 5e5}, "LIQUIDATION")
    assert [s.t_rx_ms for s in shocks] == [DAY0 + 100_000, DAY0 + 900_000]
    assert shocks[0].repeated == 1 and shocks[1].repeated == 0
    assert shocks[0].direction == "FORCED_SELL"
    # a crossing without the price move is not a shock; FORCED_BUY needs an up move
    g.liq_buy_60[2000] = 1e6
    assert len(e4.detect(g, {key: 5e5}, "LIQUIDATION")) == 2
    assert e4.detect(g, {}, "LIQUIDATION") == []  # no threshold -> nothing (fail closed)


def _bars(rows: list[tuple[float, float, float, float]], t0: int) -> Bars:
    a = np.array(rows)
    n = len(rows)
    one = np.ones(n)
    return completed_bars(
        (t0 + np.arange(n, dtype=np.int64) * MS_1M).astype(np.int64),
        a[:, 0], a[:, 1], a[:, 2], a[:, 3], one, a[:, 3], one / 2, one / 2, MS_1M,
    )  # fmt: skip


def _shock(direction: str, t_rx: int) -> e4.Shock:
    return e4.Shock("X", "LIQUIDATION", direction, t_rx, None, 1e6, 5e5, -15.0, 20.0)


def test_arms_reversal_continuation_abstain_and_mirror() -> None:
    t_ev = DAY0 + 60_000  # event in minute 1
    rev_rows = [
        (10_000.0, 10_000.0, 9_990.0, 9_995.0),
        (9_995.0, 10_000.0, 9_950.0, 9_960.0),  # event minute: low 9950
        (9_960.0, 9_970.0, 9_955.0, 9_965.0),  # response 1
        (9_965.0, 9_980.0, 9_960.0, 9_975.0),  # response 2: no lower low, close > r1 high
    ] + [(9_975.0, 9_976.0, 9_974.0, 9_975.0)] * 70
    g = _grid(80 * 60, px=9_975.0, bid=9_979.0, ask=9_981.0)
    # after entry (known at minute 4) the price runs to the 2R target
    known = (t_ev // MS_1M) * MS_1M + 4 * MS_1M
    i_k = int((known - DAY0) // 1000)
    g.last_px[i_k + 300 :] = 10_200.0
    g.bid[i_k + 300 :], g.ask[i_k + 300 :] = 10_199.0, 10_201.0
    sh = _shock("FORCED_SELL", t_ev)
    e4.arms(g, _bars(rev_rows, DAY0), sh)
    assert sh.arm == "REVERSAL" and sh.arm_side == "LONG" and sh.entry_exec == 9_981.0
    assert sh.stop is not None and math.isclose(sh.stop, 9_950.0 - 0.25 * (20.0 / 1e4 * 9_975.0))
    assert sh.exit_reason == "TARGET" and sh.gross_bps is not None and sh.gross_bps > 0
    assert sh.exit_price == 10_199.0  # exit at the bid (never a mid)
    # mirror: FORCED_BUY with reflected bars -> REVERSAL SHORT at the bid
    mir = [(2e4 - o, 2e4 - lo, 2e4 - h, 2e4 - c) for o, h, lo, c in rev_rows]
    g2 = _grid(80 * 60, px=2e4 - 9_975.0, bid=2e4 - 9_981.0, ask=2e4 - 9_979.0)
    g2.last_px[i_k + 300 :] = 2e4 - 10_200.0
    g2.bid[i_k + 300 :], g2.ask[i_k + 300 :] = 2e4 - 10_201.0, 2e4 - 10_199.0
    sh2 = _shock("FORCED_BUY", t_ev)
    e4.arms(g2, _bars(mir, DAY0), sh2)
    assert sh2.arm == "REVERSAL" and sh2.arm_side == "SHORT" and sh2.exit_reason == "TARGET"
    assert math.isclose(sh2.stop_bps or 0, sh.stop_bps or 1, rel_tol=0.01)
    # continuation: both responses close below the event low, second below first
    cont_rows = (
        rev_rows[:2]
        + [
            (9_960.0, 9_962.0, 9_935.0, 9_940.0),
            (9_940.0, 9_942.0, 9_925.0, 9_930.0),
        ]
        + [(9_930.0, 9_931.0, 9_929.0, 9_930.0)] * 70
    )
    g3 = _grid(80 * 60, px=9_930.0, bid=9_929.0, ask=9_931.0)
    g3.last_px[i_k + 300 :] = 9_700.0
    g3.bid[i_k + 300 :], g3.ask[i_k + 300 :] = 9_699.0, 9_701.0
    sh3 = _shock("FORCED_SELL", t_ev)
    e4.arms(g3, _bars(cont_rows, DAY0), sh3)
    assert sh3.arm == "CONTINUATION" and sh3.arm_side == "SHORT" and sh3.entry_exec == 9_929.0
    assert sh3.exit_reason == "TARGET" and sh3.exit_price == 9_701.0
    # neither pattern -> ABSTAIN; no quote -> no entry
    abst = [
        *rev_rows[:2],
        (9_960.0, 9_965.0, 9_945.0, 9_950.0),
        (9_950.0, 9_958.0, 9_948.0, 9_955.0),
    ]
    sh4 = _shock("FORCED_SELL", t_ev)
    e4.arms(g, _bars(abst + [(9_955.0, 9_956.0, 9_954.0, 9_955.0)] * 5, DAY0), sh4)
    assert sh4.arm == "ABSTAIN" and sh4.entry_exec is None
    g5 = _grid(80 * 60, px=9_975.0, bid=None, ask=None)
    sh5 = _shock("FORCED_SELL", t_ev)
    e4.arms(g5, _bars(rev_rows, DAY0), sh5)
    assert sh5.arm == "REVERSAL" and sh5.exit_reason == "NO_EXECUTABLE_QUOTE"
    # markouts: every horizon x latency x arm key, NaN where no quote
    mk = e4.markouts(g5, sh5)
    assert len(mk) == 2 * len(e4.LATENCIES_S) * len(e4.HORIZONS_S) and all(
        math.isnan(v) for v in mk.values()
    )
    # finite wherever the horizon is on the 80-minute grid (the 2 h horizon falls off it -> NaN)
    assert all(math.isfinite(v) for k, v in e4.markouts(g, sh).items() if not k.endswith("_7200s"))


def _raw_record(rx_ms: int, topic: str, typ: str, ts: int, data: object) -> str:
    raw = json.dumps({"topic": topic, "type": typ, "ts": ts, "data": data})
    return json.dumps(
        {
            "channel": topic,
            "raw": raw,
            "schema_version": "bybit-public-raw-1",
            "ts_received_ms": rx_ms,
            "type": typ,
        }
    )


def _write_store(root: Path, lines: list[tuple[int, str]], gz: bool) -> None:
    by_hour: dict[tuple[str, int], list[str]] = {}
    for rx, line in lines:
        d = datetime.fromtimestamp(rx / 1000, tz=UTC)
        by_hour.setdefault((d.date().isoformat(), d.hour), []).append(line)
    for (day, hour), ls in by_hour.items():
        p = root / day
        p.mkdir(parents=True, exist_ok=True)
        f = p / (f"{hour:02d}.jsonl.gz" if gz else f"{hour:02d}.jsonl")
        text = "\n".join(ls) + "\n"
        if gz:
            with gzip.open(f, "wt") as fh:
                fh.write(text)
        else:
            f.write_text(text)


def _synthetic_lines() -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    t = DAY0
    for k in range(7200):  # two hours of one trade per second, received 40 ms late
        rx = t + k * 1000 + 40
        px = 10_000.0 + (k % 50)
        lines.append(
            (
                rx,
                _raw_record(
                    rx,
                    "publicTrade.BTCUSDT",
                    "snapshot",
                    t + k * 1000,
                    [
                        {
                            "T": t + k * 1000,
                            "p": str(px),
                            "v": "0.01",
                            "S": "Buy" if k % 2 else "Sell",
                        }
                    ],
                ),
            )
        )
        if k % 10 == 0:
            lines.append(
                (
                    rx + 1,
                    _raw_record(
                        rx + 1,
                        "orderbook.1.BTCUSDT",
                        "snapshot",
                        t + k * 1000,
                        {"b": [[str(px - 0.5), "1"]], "a": [[str(px + 0.5), "1"]], "u": k},
                    ),
                )
            )
    lines.append(
        (
            t + 3_000_040,
            _raw_record(
                t + 3_000_040,
                "allLiquidation.BTCUSDT",
                "snapshot",
                t + 3_000_000,
                [{"T": t + 3_000_000, "p": "10000", "v": "50", "S": "Buy"}],
            ),
        )
    )
    return lines


def test_raw_store_ingest_reorder_invariance_and_fail_closed(tmp_path: Path) -> None:
    lines = _synthetic_lines()
    ordered = tmp_path / "ordered"
    shuffled = tmp_path / "shuffled"
    _write_store(ordered, lines, gz=False)
    rng = random.Random(7)
    sh = lines[:]
    rng.shuffle(sh)
    _write_store(shuffled, sh, gz=True)
    ev_a = e4.events_from_roots([ordered])
    ev_b = e4.events_from_roots([shuffled])
    assert len(ev_a.tr_rx) == 7200 and len(ev_a.lq_rx) == 1 and len(ev_a.bbo_rx) == 720
    for k in ("tr_rx", "tr_px", "lq_rx", "bbo_rx", "bid", "ask"):
        assert np.array_equal(getattr(ev_a, k), getattr(ev_b, k)), k
    ga, gb = e4.build_grid(ev_a), e4.build_grid(ev_b)
    assert ga is not None and gb is not None
    assert np.array_equal(ga.last_px, gb.last_px, equal_nan=True)
    # receipt-time availability: the trade of second k is known at second k (received 40 ms later)
    assert ga.last_px[0] == 10_000.0 and ga.last_px[1] == 10_001.0
    # stale BBO: quotes arrive every 10 s (+1 ms), max age 2 s -> 2 of each 10 seconds carry a quote
    assert np.isfinite(ga.bid).sum() == 720 * 2
    assert ga.liq_sell_60[3000] == 50 * 10_000.0 and ga.liq_buy_60[3000] == 0.0
    # thresholds need 30 complete days: the study refuses to detect anything on two hours
    res = e4.run_e4([ordered])
    assert res["status"] == "THRESHOLD_UNAVAILABLE_INSUFFICIENT_HISTORY"
    assert res["shocks"] == [] and res["flow_parents"] == [] and res["n_trades"] == 7200
    cov = raw_coverage([ordered])
    assert cov["n_complete_days"] == 0 and cov["trade_minutes_total"] == 120
    assert cov["liquidation_channel_days"] == 1 and cov["bbo_minutes_total"] == 120
