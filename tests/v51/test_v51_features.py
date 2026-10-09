"""V5.1 gap-aware features: clean-data equivalence with frozen V5, zero-volume synthetic rows,
1h windows touching gaps, rolling z ignoring invalid observations, clean-bar ATR, no leakage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from btc_swing.features.indicators import true_range
from btc_swing.v5.events import build_v5_detectors
from btc_swing.v5.features import rolling_z
from btc_swing.v5.forward.pipeline import assemble, build
from btc_swing.v51 import features as f51
from tests.v51.helpers import synthetic_bars, v5_ctx, v51_cfg

N = 4300  # ~15 days of 5m rows (the 2880-observation warm-up is reached)
OUT = (3200, 3260)  # a 5-hour collector outage (zero-trade rows)


def _both(
    tmp: Path, outages: list[tuple[int, int]] | None, n: int = N
) -> tuple[Any, Any, Any, Any]:
    ctx = v5_ctx(tmp, synthetic_bars(n, outages=outages))
    a = assemble(ctx)
    b = build(ctx, a)
    ff51, val = f51.build_feature_frame_v51(
        b.series, b.aux, v51_cfg(), a.extra, f51.bar_validity(a.rows)
    )
    return a, b, ff51, val


def _same(x: np.ndarray, y: np.ndarray) -> bool:
    nx, ny = np.isnan(x), np.isnan(y)
    return bool(np.array_equal(nx, ny) and np.allclose(x[~nx], y[~ny], rtol=0, atol=1e-12))


def test_clean_data_v51_equals_frozen_v5_features_and_stage_a_masks(tmp_path: Path) -> None:
    # a frame longer than the frozen z window: every row after window + longest lookback has a
    # z baseline free of frame-start rows (on the real stream the 45-day seed guarantees this
    # for every evaluated row)
    cfg = v51_cfg()
    n = cfg.features.z_window_bars + 1200
    k0 = cfg.features.z_window_bars + 577
    _, b, ff51, val = _both(tmp_path, None, n)
    ff5 = b.ff
    assert val.bar.all()
    diff = [
        c for c in ff5.cols if c in ff51.cols and not _same(ff5.cols[c][k0:], ff51.cols[c][k0:])
    ]
    assert diff == [], diff
    assert ff51.regime[k0:] == ff5.regime[k0:]
    d5 = build_v5_detectors(cfg, ff5)
    d51 = build_v5_detectors(cfg, ff51)
    for x, y in zip(d5, d51, strict=True):
        assert np.array_equal(x.mask[k0:], y.mask[k0:]) and _same(x.strength[k0:], y.strength[k0:])
    assert sum(int(d.mask[k0:].sum()) for d in d51) > 0  # the synthetic stream does produce events
    # before k0 the only differences are frame-start rows that V5 defines before their lookback
    # exists (rv over a NaN log-return treated as 0, break flags with a NaN reference, premium
    # means over a partial first hour) and the z baselines that still contain them
    early = [
        c for c in ff5.cols if c in ff51.cols and not _same(ff5.cols[c][:k0], ff51.cols[c][:k0])
    ]
    assert set(early) <= {
        "rv_1h",
        "rv_24h",
        "rv_24h_z",
        "break_up",
        "break_dn",
        "prem_1h",
        "prem_z",
    }


def test_zero_volume_synthetic_rows_are_not_observations(tmp_path: Path) -> None:
    a, b, ff51, val = _both(tmp_path, [OUT])
    gap = a.rows["gap_filled"].to_numpy() > 0
    assert gap[OUT[0] : OUT[1]].all() and gap.sum() == OUT[1] - OUT[0]
    # frozen V5: rows whose 1h window is partly outage carry a tiny "real" volume -> absurd z
    assert np.nanmin(b.ff.cols["vol_1h_z"][OUT[0] : OUT[1] + 11]) < -2
    # V5.1: never an observation; NaN while the 1h window touches the outage, valid afterwards
    v51_vol = ff51.cols["vol_1h_z"]
    assert np.isnan(v51_vol[OUT[0] : OUT[1] + 11]).all()
    assert not np.isnan(v51_vol[OUT[1] + 11 : OUT[1] + 40]).any()
    assert not val.bar[OUT[0] : OUT[1]].any() and val.bar[OUT[1]]
    # the raw masked columns are NaN too (no "tiny real volume" interpretation anywhere)
    for c in ("ret_1h", "imbalance_1h", "cvd_slope_1h", "oi_chg_1h", "lo_struct", "break_up"):
        assert np.isnan(ff51.cols[c][OUT[0] : OUT[1] + 11]).all(), c
    # the gap rows themselves are retained in the frame (coverage/audit), not dropped
    assert len(ff51.close_ms) == N


def test_log_of_a_residue_volume_is_never_treated_as_a_real_observation(tmp_path: Path) -> None:
    """The live contamination: the sliding 1h volume sum of outage rows left a float residue
    (~1e-12 BTC), whose log (~ -27) entered the frozen 30-day baseline (std inflated ~10x)."""
    bars = synthetic_bars(N, outages=[OUT])
    vol = bars["volume"].to_numpy().copy()
    vol[OUT[0] : OUT[1]] = 1e-12
    bars = bars.with_columns(pl.Series("volume", vol))
    ctx = v5_ctx(tmp_path, bars)
    a = assemble(ctx)
    b = build(ctx, a)
    ff51, _ = f51.build_feature_frame_v51(
        b.series, b.aux, v51_cfg(), a.extra, f51.bar_validity(a.rows)
    )
    k = OUT[1] + 40
    assert np.nanmin(b.ff.cols["vol_1h_z"][OUT[0] : OUT[1]]) < -5  # frozen V5: absurd observations
    assert np.isnan(ff51.cols["vol_1h_z"][OUT[0] : OUT[1] + 11]).all()  # V5.1: not observations
    # the V5.1 baseline after the outage is unaffected by the residue rows
    ok = f51.window_valid(f51.bar_validity(a.rows), 11)
    v1h = np.convolve(b.series.base.volume, np.ones(12), "full")[:N]
    src = np.log(np.where(v1h > 0, v1h, np.nan))
    w = src[:k][ok[:k] & ~np.isnan(src[:k])]
    assert ff51.cols["vol_1h_z"][k] == pytest.approx((src[k] - w.mean()) / w.std(ddof=1), rel=1e-9)
    assert abs(b.ff.cols["vol_1h_z"][k] - ff51.cols["vol_1h_z"][k]) > 0.5


def test_rolling_z_excludes_invalid_observations_from_mean_and_std(tmp_path: Path) -> None:
    _, b, ff51, val = _both(tmp_path, [OUT])
    cfg = v51_cfg()
    win, mp = cfg.features.z_window_bars, cfg.features.z_min_periods
    vol = b.series.base.volume
    v1h = np.convolve(vol, np.ones(12), "full")[: len(vol)]
    v1h[:11] = np.nan
    src = np.log(np.where(v1h > 0, v1h, np.nan))
    ok = val.obs["vol_1h"]
    k = OUT[1] + 30  # a clean row after the outage; its z window contains the outage rows
    w = src[max(0, k - win) : k]
    wok = ok[max(0, k - win) : k]
    clean = w[wok & ~np.isnan(w)]
    assert len(clean) >= mp and (~wok).sum() >= OUT[1] - OUT[0]
    expect = (src[k] - clean.mean()) / clean.std(ddof=1)
    assert ff51.cols["vol_1h_z"][k] == pytest.approx(expect, rel=1e-9)
    # frozen V5 counts the partial-outage rows as observations of its baseline; V5.1 does not
    v5_src = np.log(np.where(v1h > 0, v1h, np.nan))
    n_v5 = int((~np.isnan(v5_src[max(0, k - win) : k])).sum())
    assert n_v5 - len(clean) >= 11
    # generic property of the frozen helper: NaN sources are excluded, not treated as zero
    x = np.array([1.0, 2.0, np.nan, 3.0, 4.0, 100.0, 5.0], dtype=float)
    z = rolling_z(x, 4, 2)
    assert z[6] == pytest.approx((5 - np.mean([3, 4, 100])) / np.std([3, 4, 100], ddof=1))


def test_one_hour_windows_touching_a_gap_are_invalid_exactly(tmp_path: Path) -> None:
    _, _, ff51, _ = _both(tmp_path, [OUT])
    end = OUT[1]
    for col, lb in (
        ("ret_1h_z", 12),
        ("imbalance_1h_z", 11),
        ("cvd_slope_1h_z", 11),
        ("oi_chg_1h_z", 12),
    ):
        x = ff51.cols[col]
        assert np.isnan(x[OUT[0] : end + lb]).all(), col
        assert not np.isnan(x[end + lb]), col  # first row whose whole lookback is clean
    # the 24h break reference needs 288 clean rows before it is an observation again
    assert np.isnan(ff51.cols["break_up"][end : end + 288]).all()
    assert not np.isnan(ff51.cols["break_up"][end + 288])


def test_atr_from_clean_1h_bars_only(tmp_path: Path) -> None:
    _, b, ff51, val = _both(tmp_path, [OUT])
    cfg = v51_cfg()
    series = b.series
    atr51, clean, _ = f51.atr_clean_1h(series, val.bar, cfg.indicators.atr_period)
    h1 = series.series[f51.Timeframe.H1]
    # contaminated: the 1h bars overlapping the outage and the one after (true range uses prev close)
    t0, t1 = int(series.base.close_ms[OUT[0]]), int(series.base.close_ms[OUT[1] - 1])
    touched = (h1.close_ms >= t0) & (h1.open_ms < t1)
    first_after = int(np.flatnonzero(touched).max()) + 1
    assert not clean[touched].any() and not clean[first_after] and clean[first_after + 1]
    assert np.isnan(atr51[touched]).all() and np.isnan(atr51[first_after])
    # the recursion never consumes a contaminated true range: poisoning them changes nothing
    poisoned = series.series[f51.Timeframe.H1]
    hi, lo = poisoned.high.copy(), poisoned.low.copy()
    hi[~clean] += 1e6
    lo[~clean] -= 1e6
    tr_p = true_range(hi, lo, poisoned.close)
    idx = np.flatnonzero(clean)
    a = np.full(len(idx), np.nan)
    a[13] = float(np.mean(tr_p[idx][:14]))
    for i in range(14, len(idx)):
        a[i] = (a[i - 1] * 13 + tr_p[idx][i]) / 14
    assert np.allclose(atr51[idx][13:], a[13:], rtol=0, atol=1e-9)
    assert np.nanmax(atr51) < 1e4  # no poisoned value leaked
    # per 5m row: NaN while the geometry is invalid, then the clean ATR
    ff_atr = ff51.cols["atr"]
    assert np.isnan(ff_atr[OUT[0] : OUT[1] + 12]).all()
    assert not np.isnan(ff_atr[OUT[1] + 36 :]).any()


def test_clean_atr_equals_frozen_atr_on_continuous_data(tmp_path: Path) -> None:
    _, b, _, val = _both(tmp_path, None)
    cfg = v51_cfg()
    h1 = b.series.series[f51.Timeframe.H1]
    atr51, clean, _ = f51.atr_clean_1h(b.series, val.bar, cfg.indicators.atr_period)
    assert clean.all()
    assert _same(atr51, h1.ind["atr"])


def test_detectors_are_off_while_any_input_is_invalid_and_equal_v5_elsewhere(
    tmp_path: Path,
) -> None:
    _, b, ff51, _ = _both(tmp_path, [OUT])
    cfg = v51_cfg()
    d5 = {(d.v5_family, d.side): d for d in build_v5_detectors(cfg, b.ff)}
    d51 = {(d.v5_family, d.side): d for d in build_v5_detectors(cfg, ff51)}
    for key, det in d51.items():
        assert not det.mask[OUT[0] : OUT[1] + 12].any(), key
        # before the outage the two frames share an identical history: identical masks
        before = np.arange(N) < OUT[0]
        assert np.array_equal(det.mask[before], d5[key].mask[before]), key


def test_no_future_leakage_in_v51_features(tmp_path: Path) -> None:
    bars = synthetic_bars(N, outages=[OUT])
    ctx_full = v5_ctx(tmp_path / "full", bars)
    ctx_cut = v5_ctx(tmp_path / "cut", bars.head(N - 400))
    cfg = v51_cfg()
    out = []
    for ctx in (ctx_full, ctx_cut):
        a = assemble(ctx)
        b = build(ctx, a)
        out.append(
            f51.build_feature_frame_v51(b.series, b.aux, cfg, a.extra, f51.bar_validity(a.rows))
        )
    (ff_f, val_f), (ff_c, val_c) = out
    m = len(ff_c.close_ms)
    assert np.array_equal(ff_f.close_ms[:m], ff_c.close_ms)
    for c in ("ret_1h_z", "vol_1h_z", "imbalance_1h_z", "cvd_slope_1h_z", "oi_chg_1h_z", "atr"):
        assert _same(ff_f.cols[c][:m], ff_c.cols[c]), c
    assert np.array_equal(val_f.geometry[:m], val_c.geometry)
