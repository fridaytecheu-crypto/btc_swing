from __future__ import annotations

import numpy as np
import polars as pl

from btc_swing.v61.features import build_frame_v61
from tests.v61.helpers import cfg, synthetic_inputs

REPORTING_ONLY = {"day_range", "active_day"}


def test_pit_truncate_and_recompute() -> None:
    c = cfg()
    full = build_frame_v61(synthetic_inputs(n_days=50), c)
    k_cut = 44 * 288
    t_cut = int(full.open_ms[k_cut])
    inp2 = synthetic_inputs(n_days=50)
    inp2.bars = inp2.bars.filter(pl.col("open_time_ms") < t_cut)
    inp2.metrics = inp2.metrics.filter(pl.col("time_ms") <= t_cut)
    inp2.premium = inp2.premium.filter(pl.col("open_time_ms") < t_cut)
    part = build_frame_v61(inp2, c)
    assert len(part) == k_cut
    for name, arr in full.cols.items():
        if name in REPORTING_ONLY:
            continue
        a, b = arr[:k_cut], part.cols[name]
        both = ~(np.isnan(a) & np.isnan(b))
        assert np.allclose(a[both], b[both], equal_nan=True, atol=1e-9), name


def test_location_features_definitions() -> None:
    c = cfg()
    fr = build_frame_v61(synthetic_inputs(n_days=40), c)
    k = 30 * 288 + 100
    close, high, low = fr.cols["close"], fr.cols["high"], fr.cols["low"]
    a15 = fr.cols["atr15m"][k]
    assert np.isclose(
        fr.cols["dist_ema21_15m_atr"][k], (close[k] - fr.cols["ema_fast_15m"][k]) / a15
    )
    hi4 = np.max(high[k - 48 : k])
    lo4 = np.min(low[k - 48 : k])
    assert fr.cols["hi_4h"][k] == hi4 and np.isclose(
        fr.cols["range_pos_4h"][k], (close[k] - lo4) / (hi4 - lo4)
    )
    # session high/low include the current bar and reset at the UTC day boundary
    d0 = 30 * 288
    assert fr.cols["day_hi"][d0] == high[d0] and fr.cols["day_lo"][d0] == low[d0]
    assert fr.cols["day_hi"][k] == np.max(high[d0 : k + 1])
    assert fr.cols["prev_day_lo"][k] == np.min(low[d0 - 288 : d0])
    # previous completed 1h bar
    j = (k + 1) // 12 * 12  # start of the current (incomplete) 1h block
    assert fr.cols["prev1h_high"][k] == np.max(high[j - 24 : j - 12])
    # edge touches count previous bars near the 4h extreme
    n_touch = int(np.sum(high[k - 48 : k] >= hi4 - c.features.edge_touch_atr15 * a15))
    assert fr.cols["edge_touches_hi"][k] == n_touch
    assert np.isclose(
        fr.cols["compression_6"][k],
        (np.max(high[k - 5 : k + 1]) - np.min(low[k - 5 : k + 1])) / a15,
    )
    assert set(np.unique(fr.cols["regime_1h"][~np.isnan(fr.cols["regime_1h"])])) <= {-1.0, 0.0, 1.0}


def test_regime_definition_pit_and_three_states() -> None:
    c = cfg()
    fr = build_frame_v61(synthetic_inputs(n_days=40, seed=5), c)
    reg = fr.cols["regime_1h"]
    ef, es, cl = fr.cols["ema_fast_1h"], fr.cols["ema_slow_1h"], fr.cols["close_1h"]
    k = 35 * 288 + 7
    if reg[k] == 1.0:
        assert ef[k] > es[k] and cl[k] > es[k]
    if reg[k] == -1.0:
        assert ef[k] < es[k] and cl[k] < es[k]
    # values are constant inside a 1h block (completed-bar semantics)
    j = (k + 1) // 12 * 12
    assert len(set(reg[j : j + 12].tolist())) == 1


def test_gap_rows_invalid() -> None:
    c = cfg()
    a, b = 30 * 288 + 50, 30 * 288 + 56
    fr = build_frame_v61(synthetic_inputs(n_days=36, gaps=[(a, b)]), c)
    assert not fr.valid[a:b].any()
    assert np.isnan(fr.cols["ret_1h"][a : b + 12]).all()
    assert np.isnan(fr.cols["atr5"][a : b + 14]).all()
