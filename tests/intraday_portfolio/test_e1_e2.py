"""E1 / E2 scenario tests: PIT safety, future-data invariance, no same-close fills, missing data fail
closed, episode dedup, long/short symmetry, no retroactive fill."""

from __future__ import annotations

import math

import numpy as np

from btc_swing.research.intraday_portfolio import e1 as e1mod
from btc_swing.research.intraday_portfolio import e2 as e2mod
from btc_swing.research.intraday_portfolio.contract import MS_1M
from btc_swing.research.intraday_portfolio.primitives import Bars
from tests.intraday_portfolio.synth import (
    T0,
    bars_from_ohlc,
    drop_minutes,
    e1_long_path,
    e2_long_path,
    features,
    mirror,
)

NOFUND = (np.zeros(0, dtype=np.int64), np.zeros(0))


def _run_e1(b1: Bars) -> list:  # type: ignore[type-arg]
    b5, atr, er, boxes = features(b1)
    return e1mod.run_e1(e1mod.E1Inputs(b5, b1, atr, er, boxes, *NOFUND))


def _run_e2(b1: Bars) -> list:  # type: ignore[type-arg]
    b5, atr, er, boxes = features(b1)
    return e2mod.run_e2(e2mod.E2Inputs(b5, b1, atr, er, boxes, *NOFUND))


def _slice(b: Bars, n: int) -> Bars:
    return Bars(
        b.open_ms[:n], b.close_ms[:n], b.open[:n], b.high[:n], b.low[:n], b.close[:n],
        b.volume[:n], b.vwap[:n], b.buy[:n], b.sell[:n], b.step_ms,
    )  # fmt: skip


def test_e1_long_scenario_trades_to_target() -> None:
    rows = e1_long_path()
    parents = _run_e1(bars_from_ohlc(rows))
    traded = [p for p in parents if p.traded]
    assert len(traded) == 1
    p = traded[0]
    assert p.side == "LONG" and p.regime == "DIRECTIONAL" and p.er > 0.25
    assert p.frozen_levels["box_high"] == 9920.0 and p.frozen_levels["box_low"] == 9880.0
    # entry = open of the first minute AFTER the retest bar close: never the decision bar's own close
    assert p.times.benchmark_entry_time == p.times.decision_time
    assert p.entry_benchmark == 9950.0
    k = int(np.searchsorted(bars_from_ohlc(rows).open_ms, p.times.benchmark_entry_time))
    assert int(bars_from_ohlc(rows).open_ms[k]) == p.times.benchmark_entry_time
    assert p.stop < 9918.0 and 20 <= p.stop_bps <= 150
    assert p.exit is not None and p.exit.reason == "TARGET"
    assert math.isclose(p.target, 9950.0 + 2 * (9950.0 - p.stop))
    assert p.gross_bps > 0 and math.isclose(p.net_bps, p.gross_bps - 16.0)
    assert p.net_r > 0 and math.isnan(p.notes["funding_bps_proxy"])  # no funding data -> NaN not 0
    # every rejected / repeated breakout stays recorded on the parent list
    assert all(p_.engine == "E1" for p_ in parents)


def test_e1_time_exit_and_future_data_invariance() -> None:
    rows = e1_long_path(with_target=False)
    b1 = bars_from_ohlc(rows)
    full = _run_e1(b1)
    traded = [p for p in full if p.traded]
    assert len(traded) == 1 and traded[0].exit is not None
    assert traded[0].exit.reason == "TIME" and traded[0].hold_min == 120
    # truncate right after the exit: the parent's recorded fields are unchanged by later rows
    n_cut = int(np.searchsorted(b1.open_ms, traded[0].exit.time_ms)) + 1
    part = _run_e1(_slice(b1, n_cut))
    a = next(p.as_row() for p in part if p.traded)
    b = traded[0].as_row()
    for k in (
        "entry_benchmark",
        "stop",
        "target",
        "exit_price",
        "exit_time_ms",
        "gross_bps",
        "net_bps",
    ):
        assert a[k] == b[k], k


def test_e1_entry_print_missing_fails_closed() -> None:
    rows = e1_long_path()
    b1_full = bars_from_ohlc(rows)
    traded = next(p for p in _run_e1(b1_full) if p.traded)
    k = int((traded.times.benchmark_entry_time - T0) // MS_1M)
    b1 = drop_minutes(rows, T0, {k})
    parents = _run_e1(b1)
    assert not any(p.traded for p in parents)
    assert any("ENTRY_PRINT_UNAVAILABLE" in p.rejection_reasons for p in parents)


def test_e1_5m_gap_cancels_the_pattern() -> None:
    rows = e1_long_path()
    # remove the whole retest 5m bar (minutes 430-434): the pattern must cancel on the data gap
    b1 = drop_minutes(rows, T0, set(range(430, 435)))
    parents = _run_e1(b1)
    assert not any(p.traded for p in parents)
    assert any("CANCEL_DATA_GAP" in p.rejection_reasons for p in parents)


def test_e1_repeated_breakouts_are_one_parent() -> None:
    rows = e1_long_path(with_target=False)
    parents = [p for p in _run_e1(bars_from_ohlc(rows)) if p.side == "LONG"]
    # the uptrend hours and the breakout produce parents; later bars above the box are repeats
    br = [p for p in parents if p.frozen_levels["box_high"] == 9920.0]
    assert len(br) == 1
    assert br[0].repeated_triggers > 0
    assert len(br[0].repeated_trigger_times_ms) == br[0].repeated_triggers


def test_e1_long_short_symmetry() -> None:
    rows = e1_long_path()
    longs = [p for p in _run_e1(bars_from_ohlc(rows)) if p.traded]
    shorts = [p for p in _run_e1(bars_from_ohlc(mirror(rows))) if p.traded]
    assert len(longs) == len(shorts) == 1
    a, b = longs[0], shorts[0]
    assert a.side == "LONG" and b.side == "SHORT"
    assert a.exit is not None and b.exit is not None and a.exit.reason == b.exit.reason
    assert math.isclose(a.stop_bps, b.stop_bps, rel_tol=0.02)
    assert math.isclose(a.gross_bps, b.gross_bps, rel_tol=0.02)
    assert a.times.benchmark_entry_time == b.times.benchmark_entry_time


def test_e2_long_scenario_exits_at_vwap() -> None:
    rows = e2_long_path()
    parents = _run_e2(bars_from_ohlc(rows))
    traded = [p for p in parents if p.traded]
    assert len(traded) == 1
    p = traded[0]
    assert p.side == "LONG" and p.regime == "RANGE" and p.er <= 0.25
    assert p.frozen_levels["box_low"] == 9980.0 and p.frozen_levels["sweep_extreme"] == 9970.0
    assert p.entry_benchmark == 9990.0
    assert p.notes["vwap_distance_bps"] >= 40 and p.notes["vwap_distance_bps"] >= 1.5 * p.stop_bps
    assert p.exit is not None and p.exit.reason == "VWAP" and p.exit.price == p.target
    assert math.isclose(p.target, p.frozen_levels["box_vwap"])
    assert p.hold_min <= 60


def test_e2_no_retroactive_fill_when_target_already_reached() -> None:
    rows = e2_long_path(entry_open=10036.0)
    parents = _run_e2(bars_from_ohlc(rows))
    assert not any(p.traded for p in parents)
    assert any("TARGET_ALREADY_REACHED_NO_RETROACTIVE_FILL" in p.rejection_reasons for p in parents)


def test_e2_long_short_symmetry() -> None:
    rows = e2_long_path()
    longs = [p for p in _run_e2(bars_from_ohlc(rows)) if p.traded]
    shorts = [p for p in _run_e2(bars_from_ohlc(mirror(rows))) if p.traded]
    assert len(longs) == len(shorts) == 1
    a, b = longs[0], shorts[0]
    assert b.side == "SHORT" and a.exit is not None and b.exit is not None
    assert a.exit.reason == b.exit.reason == "VWAP"
    assert math.isclose(a.stop_bps, b.stop_bps, rel_tol=0.02)
    assert math.isclose(a.gross_bps, b.gross_bps, rel_tol=0.02)


def test_e2_confirmation_bar_missing_fails_closed() -> None:
    rows = e2_long_path()
    b1 = drop_minutes(rows, T0, set(range(425, 430)))
    parents = _run_e2(b1)
    assert not any(p.traded for p in parents)
    assert any("CANCEL_DATA_GAP" in p.rejection_reasons for p in parents)
