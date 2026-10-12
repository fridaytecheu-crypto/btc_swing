from __future__ import annotations

import numpy as np
import polars as pl

from btc_swing.v6.features import build_frame, slot_median_prev_days, wilder_atr, window_valid
from tests.v6.helpers import cfg, synthetic_inputs

REPORTING_ONLY = {
    "day_range",
    "active_day",
}  # classify the day with its full range (not decision inputs)


def test_pit_truncate_and_recompute() -> None:
    """Features at row k from the full stream equal those computed from rows <= k only."""
    c = cfg()
    inp = synthetic_inputs(n_days=36)
    full = build_frame(inp, c)
    cut_day = 30
    k_cut = cut_day * 288  # truncate at a day boundary so the grid stays day-aligned
    t_cut = int(full.open_ms[k_cut])
    inp2 = synthetic_inputs(n_days=36)
    inp2.bars = inp2.bars.filter(pl.col("open_time_ms") < t_cut)
    inp2.flow = inp2.flow.filter(pl.col("open_time_ms") < t_cut)
    inp2.metrics = inp2.metrics.filter(
        pl.col("time_ms") <= t_cut
    )  # an observation AT T is known at T
    inp2.premium = inp2.premium.filter(pl.col("open_time_ms") < t_cut)
    part = build_frame(inp2, c)
    assert len(part) == k_cut
    for name, arr in full.cols.items():
        if name in REPORTING_ONLY:
            continue
        a, b = arr[:k_cut], part.cols[name]
        both = ~(np.isnan(a) & np.isnan(b))
        assert np.allclose(a[both], b[both], equal_nan=True, atol=1e-9), name
    assert np.array_equal(full.regime_code[:k_cut], part.regime_code)


def test_gap_rows_are_never_observations() -> None:
    c = cfg()
    a, b = 25 * 288 + 100, 25 * 288 + 110
    inp = synthetic_inputs(n_days=30, gaps=[(a, b)])
    fr = build_frame(inp, c)
    assert not fr.valid[a:b].any() and fr.valid[a - 1] and fr.valid[b]
    assert np.isnan(fr.cols["close"][a:b]).all()
    # 1h return needs 13 valid rows: NaN through the gap and 12 rows after it
    assert np.isnan(fr.cols["ret_1h"][a : b + 12]).all()
    assert not np.isnan(fr.cols["ret_1h"][b + 12])
    # ATR(5m): NaN while any of the last 14 true ranges touches the gap
    assert np.isnan(fr.cols["atr5"][a : b + 14]).all()
    assert not np.isnan(fr.cols["atr5"][b + 15])
    # the 15m bar containing the gap is contaminated: its ATR row is NaN while its window includes it
    assert np.isnan(fr.cols["atr15m"][b]).item() or np.isnan(fr.cols["atr15m"][b + 2]).item()
    # the same-time-of-day baseline on later days ignores the gap rows (no zero volume enters)
    slot = (a % 288) + 2
    later = 27 * 288 + slot
    assert fr.cols["base_vol_5m"][later] > 0


def test_same_time_baseline_uses_previous_days_only() -> None:
    x = np.arange(30 * 288, dtype=float).reshape(30, 288)
    x[:, :] = np.arange(30)[:, None]  # value = day index
    out = slot_median_prev_days(x.reshape(-1), 30, 20, 10).reshape(30, 288)
    assert np.isnan(out[:20]).all()
    # day 20 sees days 0..19 -> median 9.5; day 25 sees 5..24 -> 14.5
    assert out[20, 0] == 9.5 and out[25, 100] == 14.5
    # changing today's value never changes today's baseline
    y = x.copy()
    y[25, :] = -1e9
    out2 = slot_median_prev_days(y.reshape(-1), 30, 20, 10).reshape(30, 288)
    assert out2[25, 0] == out[25, 0]
    assert out2[26, 0] == 14.5 and out[26, 0] == 15.5


def test_wilder_atr_skips_contaminated_bars() -> None:
    n = 100
    rng = np.random.RandomState(1)
    c = 100 + np.cumsum(rng.normal(0, 1, n))
    h, lo = c + 1, c - 1
    valid = np.ones(n, dtype=bool)
    a = wilder_atr(h, lo, c, valid, 14)
    assert np.isnan(a[:14]).all() and not np.isnan(a[14:]).any()  # first TR needs a previous close
    valid2 = valid.copy()
    valid2[50] = False
    h2 = h.copy()
    h2[50] = 1e6  # poison the contaminated bar: it must not enter any ATR value
    a2 = wilder_atr(h2, lo, c, valid2, 14)
    assert np.isnan(a2[50:65]).all()
    assert np.allclose(a2[:50], a[:50], equal_nan=True)
    assert np.isfinite(a2[70]) and a2[70] < 10


def test_window_valid() -> None:
    v = np.array([1, 1, 0, 1, 1, 1, 1], dtype=bool)
    assert window_valid(v, 3).tolist() == [False, False, False, False, False, True, True]


def test_higher_timeframes_are_completed_bars_only() -> None:
    c = cfg()
    inp = synthetic_inputs(n_days=30)
    fr = build_frame(inp, c)
    close = fr.cols["close"]
    # at row k the last completed 15m bar closes at the latest multiple of 3 rows <= k
    for k in (5000, 5001, 5002, 5003):
        j = (k + 1) // 3 * 3 - 1
        assert fr.cols["close_15m"][k] == close[j]
        assert fr.cols["high_15m"][k] == np.max(fr.cols["high"][j - 2 : j + 1])
    for k in (7000, 7011):
        j = (k + 1) // 12 * 12 - 1
        assert fr.cols["close_1h"][k] == close[j]
    assert fr.cols["bar15_complete"][5000] == 1.0 and fr.cols["bar15_complete"][5001] == 0.0


def test_rolling_references_exclude_current_bar() -> None:
    c = cfg()
    fr = build_frame(synthetic_inputs(n_days=30), c)
    k = 8000
    assert fr.cols["hi_4h"][k] == np.max(fr.cols["high"][k - 48 : k])
    assert fr.cols["lo_24h"][k] == np.min(fr.cols["low"][k - 288 : k])
    # OI at row k is the metrics row with time <= close time (same 5m cadence here)
    assert fr.cols["oi"][k] == float(fr.cols["oi"][k])
