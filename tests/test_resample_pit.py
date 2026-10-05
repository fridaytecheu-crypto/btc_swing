from __future__ import annotations

import numpy as np
import polars as pl

from btc_swing.core.config import load_btc_config
from btc_swing.core.enums import Timeframe
from btc_swing.core.timeframes import tf_ms
from btc_swing.features.indicators import atr, ema, swing_points
from btc_swing.features.resample import resample_completed
from btc_swing.features.view import MultiTfSeries
from btc_swing.providers.synthetic import SyntheticBtcProvider
from tests.conftest import ROOT


def test_partial_bucket_is_dropped(bars_5m: pl.DataFrame) -> None:
    # cut the series 2 five-minute bars into a 4h bucket: that bucket must not appear
    step = tf_ms(Timeframe.H4)
    t0 = int(bars_5m["open_time_ms"][0])
    cut_open = t0 + 10 * step + 2 * tf_ms(Timeframe.M5)
    part = bars_5m.filter(pl.col("open_time_ms") <= cut_open)
    last_close = cut_open + tf_ms(Timeframe.M5)
    h4 = resample_completed(part, Timeframe.H4, last_close)
    assert int(h4["close_time_ms"].max()) <= last_close
    assert int(h4["open_time_ms"].max()) == t0 + 9 * step
    assert (h4["n_bars"] == 48).all()


def test_resample_matches_native_aggregation(
    synthetic_data: dict[str, object], bars_5m: pl.DataFrame
) -> None:
    prov = synthetic_data["provider"]
    assert isinstance(prov, SyntheticBtcProvider)
    last_close = int(bars_5m["open_time_ms"][-1]) + tf_ms(Timeframe.M5)
    for tf in (Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1):
        ours = resample_completed(bars_5m, tf, last_close)
        native = prov.resampled(tf).filter(pl.col("open_time_ms") + tf_ms(tf) <= last_close)
        assert ours.height == native.height
        for c in ("open", "high", "low", "close", "volume"):
            assert np.allclose(ours[c].to_numpy(), native[c].to_numpy())


def test_view_never_exposes_a_bar_closing_after_t(bars_5m: pl.DataFrame) -> None:
    cfg = load_btc_config(ROOT / "config" / "btc_swing.default.yaml")
    ms = MultiTfSeries(bars_5m.head(288 * 40), cfg.indicators, cfg.regime.trend_slope_bars)
    h4 = ms.series[Timeframe.H4]
    k = 30
    close_k = int(h4.close_ms[k])
    v_before = ms.view_at(close_k - 1)
    v_at = ms.view_at(close_k)
    assert v_before.idx[Timeframe.H4] == k - 1
    assert v_at.idx[Timeframe.H4] == k
    v_at.assert_pit()
    v_before.assert_pit()
    assert v_at.close_ms(Timeframe.H4) == close_k
    # daily bar for the day containing t is not visible until midnight
    d1 = ms.series[Timeframe.D1]
    j = 10
    v = ms.view_at(int(d1.close_ms[j]) - 5 * 60_000)
    assert v.idx[Timeframe.D1] == j - 1


def test_indicators_are_causal() -> None:
    rng = np.random.RandomState(0)
    n = 600
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + np.abs(rng.normal(0, 0.5, n))
    low = close - np.abs(rng.normal(0, 0.5, n))
    e1, a1 = ema(close, 20), atr(high, low, close, 14)
    sh1, _, sl1, _ = swing_points(high, low, 3)
    close2, high2, low2 = close.copy(), high.copy(), low.copy()
    close2[500:] += 50
    high2[500:] += 50
    low2[500:] += 50
    e2, a2 = ema(close2, 20), atr(high2, low2, close2, 14)
    sh2, _, sl2, _ = swing_points(high2, low2, 3)
    assert np.allclose(e1[:500], e2[:500], equal_nan=True)
    assert np.allclose(a1[:500], a2[:500], equal_nan=True)
    # swing confirmed at j only uses bars <= j, so values before the change are identical
    assert np.allclose(sh1[:500], sh2[:500], equal_nan=True)
    assert np.allclose(sl1[:500], sl2[:500], equal_nan=True)


def test_swing_confirmation_delay() -> None:
    high = np.array([1, 2, 3, 10, 3, 2, 1, 1, 1, 1], dtype=float)
    low = high - 0.5
    sh, shi, _sl, _ = swing_points(high, low, 3)
    # pivot at index 3 is confirmed at index 6, not before
    assert np.isnan(sh[5])
    assert sh[6] == 10 and shi[6] == 3
