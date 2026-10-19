"""Primitives: completed bars, Wilder ATR, VWAP, hour boxes, efficiency ratio, bounded buffer. Proves:
future rows cannot change prior features; incomplete bars are never used; duplicate timestamps cannot
duplicate bars; missing intervals invalidate required calculations; bounded buffers keep updating."""

from __future__ import annotations

import math

import numpy as np
import pytest

from btc_swing.research.intraday_portfolio.contract import (
    MS_1H,
    MS_5M,
    Availability,
    Input,
    MissingInputError,
    Provenance,
)
from btc_swing.research.intraday_portfolio.primitives import (
    Bars,
    BoundedBuffer,
    bars_1m_to_5m,
    completed_bars,
    efficiency_ratio,
    hour_boxes,
    regime,
    vwap,
    wilder_atr,
)

T0 = 1_700_000_000_000 - (1_700_000_000_000 % MS_1H)


def _mk(
    n: int,
    step: int = MS_5M,
    seed: int = 1,
    drop: set[int] | None = None,
    now_ms: int | None = None,
) -> Bars:
    rng = np.random.default_rng(seed)
    om = np.array([T0 + k * step for k in range(n) if not (drop and k in drop)], dtype=np.int64)
    m = len(om)
    c = 60000 + np.cumsum(rng.normal(0, 20, m))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) + rng.uniform(1, 15, m)
    lo = np.minimum(o, c) - rng.uniform(1, 15, m)
    v = rng.uniform(1, 10, m)
    return completed_bars(om, o, h, lo, c, v, (h + lo + c) / 3, v * 0.5, v * 0.5, step, now_ms)


def _slice(b: Bars, n: int) -> Bars:
    return Bars(
        b.open_ms[:n],
        b.close_ms[:n],
        b.open[:n],
        b.high[:n],
        b.low[:n],
        b.close[:n],
        b.volume[:n],
        b.vwap[:n],
        b.buy[:n],
        b.sell[:n],
        b.step_ms,
    )


def test_completed_bars_dedupe_order_and_incomplete() -> None:
    om = np.array([T0 + MS_5M, T0, T0 + MS_5M, T0 + 2 * MS_5M], dtype=np.int64)
    x = np.array([1.0, 2.0, 3.0, 4.0])
    b = completed_bars(om, x, x, x, x, x, x, x, x, MS_5M, now_ms=T0 + 2 * MS_5M + 1)
    assert list(b.open_ms) == [
        T0,
        T0 + MS_5M,
    ]  # ordered, duplicate dropped, incomplete last bar excluded
    assert b.close[1] == 1.0  # first occurrence of the duplicate wins
    b2 = completed_bars(om, x, x, x, x, x, x, x, x, MS_5M, now_ms=T0 + 3 * MS_5M)
    assert len(b2) == 3
    with pytest.raises(ValueError):
        completed_bars(
            np.array([T0 + 1]), x[:1], x[:1], x[:1], x[:1], x[:1], x[:1], x[:1], x[:1], MS_5M
        )


def test_atr_is_pit_and_gap_invalidates() -> None:
    b = _mk(120)
    a = wilder_atr(b, 20)
    assert np.all(np.isnan(a[:19])) and np.all(np.isfinite(a[19:]))
    # future rows cannot change prior values
    a_short = wilder_atr(_slice(b, 60), 20)
    assert np.allclose(a_short, a[:60], equal_nan=True)
    # a missing interval breaks the chain until re-seeded
    g = _mk(160, drop={50})
    ag = wilder_atr(g, 20)
    i_after = int(np.searchsorted(g.open_ms, T0 + 51 * MS_5M))
    assert np.all(np.isnan(ag[i_after : i_after + 19])) and math.isfinite(ag[i_after + 19])
    # hand check of the seed: mean of first 20 true ranges
    tr = [b.high[0] - b.low[0]] + [
        max(b.high[i] - b.low[i], abs(b.high[i] - b.close[i - 1]), abs(b.low[i] - b.close[i - 1]))
        for i in range(1, 20)
    ]
    assert math.isclose(a[19], sum(tr) / 20)


def test_vwap_definition_and_missing_size() -> None:
    assert math.isclose(vwap(np.array([100.0, 200.0]), np.array([1.0, 3.0])), 175.0)
    assert math.isnan(vwap(np.array([100.0]), np.array([0.0])))
    assert math.isnan(vwap(np.array([100.0, np.nan]), np.array([1.0, 1.0])))


def test_hour_boxes_need_twelve_bars_and_are_frozen_for_the_next_hour() -> None:
    b = _mk(36)  # three full hours
    boxes = hour_boxes(b)
    assert sorted(boxes) == [T0 + MS_1H, T0 + 2 * MS_1H, T0 + 3 * MS_1H]
    bx = boxes[T0 + MS_1H]
    assert (
        bx.available
        and bx.high == float(np.max(b.high[:12]))
        and bx.low == float(np.min(b.low[:12]))
    )
    assert math.isclose(bx.vwap, float(np.sum(b.vwap[:12] * b.volume[:12]) / np.sum(b.volume[:12])))
    g = _mk(36, drop={14})
    assert not hour_boxes(g)[T0 + 2 * MS_1H].available and hour_boxes(g)[T0 + 2 * MS_1H].bars == 11


def test_efficiency_ratio_contiguity_zero_denominator_and_regime() -> None:
    b = _mk(100)
    er = efficiency_ratio(b)
    assert (
        np.all(np.isnan(er[:48]))
        and np.all(np.isfinite(er[48:]))
        and np.all((er[48:] >= 0) & (er[48:] <= 1))
    )
    assert np.allclose(efficiency_ratio(_slice(b, 70)), er[:70], equal_nan=True)
    g = _mk(160, drop={60})
    erg = efficiency_ratio(g)
    j = int(np.searchsorted(g.open_ms, T0 + 61 * MS_5M))
    assert np.all(np.isnan(erg[j : j + 48])) and math.isfinite(erg[j + 48])
    flat = Bars(
        b.open_ms[:60],
        b.close_ms[:60],
        np.full(60, 1.0),
        np.full(60, 1.0),
        np.full(60, 1.0),
        np.full(60, 1.0),
        np.ones(60),
        np.ones(60),
        np.ones(60),
        np.ones(60),
        MS_5M,
    )
    assert np.all(np.isnan(efficiency_ratio(flat)))  # denominator 0 -> unavailable
    assert (
        regime(0.25) == "RANGE"
        and regime(0.2501) == "DIRECTIONAL"
        and regime(math.nan) == "UNAVAILABLE"
    )


def test_bounded_buffer_keeps_updating_after_maxlen() -> None:
    buf: BoundedBuffer[int] = BoundedBuffer(3)
    for k in range(10):
        buf.push(k)
    assert buf.items() == [7, 8, 9] and len(buf) == 3 and buf.appended == 10


def test_1m_to_5m_needs_all_five_and_propagates_unknown_flow() -> None:
    b1 = _mk(25, step=60_000, drop={7})
    b5 = bars_1m_to_5m(b1)
    assert list(b5.open_ms) == [
        T0,
        T0 + 2 * MS_5M,
        T0 + 3 * MS_5M,
        T0 + 4 * MS_5M,
    ]  # the 5m with a missing minute is absent
    b1n = _mk(10, step=60_000)
    b1n.buy[3] = np.nan
    b5n = bars_1m_to_5m(b1n)
    assert math.isnan(b5n.buy[0]) and math.isfinite(
        b5n.buy[1]
    )  # unknown flow stays unknown, never 0


def test_input_fails_closed() -> None:
    ok: Input[float] = Input(1.0, Provenance("x", Availability.AVAILABLE))
    assert ok.require("v") == 1.0
    with pytest.raises(MissingInputError):
        Input(None, Provenance("x", Availability.MISSING)).require("v")
    with pytest.raises(MissingInputError):
        Input(1.0, Provenance("x", Availability.PARTIAL, synthetic=True)).require("v")
    with pytest.raises(MissingInputError):
        Input(math.nan, Provenance("x", Availability.AVAILABLE)).require("v")
    assert (
        Input(1.0, Provenance("x", Availability.PARTIAL, synthetic=True)).require(
            "v", allow_synthetic=True
        )
        == 1.0
    )
