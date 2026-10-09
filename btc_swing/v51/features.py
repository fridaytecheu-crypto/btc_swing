"""V5.1 feature frame: the frozen V5 feature definitions (`v5-feat-1`) computed on VALID market
observations only.

A 5m row is a valid observation iff it is not a synthetic row (`gap_filled == 0`: neither a
collector outage carried forward as a zero-trade bar nor a seed->forward gap fill) and it has
trades. Every rolling feature has a frozen lookback (the number of earlier rows its value depends
on); the feature observation at row k is valid only if row k and every row in its lookback are
valid. Invalid observations are NaN: they are the current value of nothing, and the frozen rolling
z-score (`rolling_z`, which ignores NaNs) therefore excludes them from its mean/std. The rows
themselves stay in the frame (coverage/audit); nothing is interpolated or backfilled.

ATR(1h) is recomputed with the frozen Wilder recursion over CLEAN 1h bars only (a 1h bar is clean if
its 12 rows and the 12 rows of the previous 1h bar are valid, so its true range never touches a
carried-forward price); the ATR of a contaminated 1h bar is NaN. Event geometry (zone, structural
stop, volatility floor) additionally needs a clean 12-bar structural window; `atr` is NaN where the
geometry is invalid, which switches every frozen detector off at that row (their base mask requires
a positive ATR). On continuous clean data every column equals the V5 frame exactly."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray

from btc_swing.core.enums import Timeframe
from btc_swing.features.context import AuxSeries
from btc_swing.features.indicators import true_range
from btc_swing.features.view import MultiTfSeries
from btc_swing.v5.features import FeatureFrame, V5Inputs, _regimes, build_feature_frame, rolling_z
from btc_swing.v51.config import V51Config

F = NDArray[np.float64]
B = NDArray[np.bool_]
MS_5M = 300_000

# Frozen lookbacks (rows before k that the V5 feature at row k depends on). Derived from
# btc_swing/v5/features.py, not configurable.
LOOKBACK: dict[str, int] = {
    "ret_5m": 1,
    "ret_15m": 3,
    "ret_1h": 12,
    "ret_4h": 48,
    "rv_1h": 12,
    "rv_24h": 288,
    "vol_5m": 0,
    "vol_1h": 11,
    "range_pos_48h": 576,
    "break": 288,
    "struct": 12,
    "delta_5m": 0,
    "delta_15m": 2,
    "delta_1h": 11,
    "flow_accel": 5,
    "cvd_slope_4h": 47,
    "big_imb_1h": 11,
    "oi": 0,
    "oi_chg_5m": 1,
    "oi_chg_15m": 3,
    "oi_chg_1h": 12,
    "oi_accel": 6,
    "prem": 0,
    "prem_1h": 11,
    "basis": 0,
    "basis_chg_1h": 12,
}
# raw column -> lookback key, for the columns that are masked (value NaN when invalid)
MASKED_RAW: dict[str, str] = {
    "ret_5m": "ret_5m",
    "ret_15m": "ret_15m",
    "ret_1h": "ret_1h",
    "ret_4h": "ret_4h",
    "rv_1h": "rv_1h",
    "rv_24h": "rv_24h",
    "range_pos_48h": "range_pos_48h",
    "hi_break_ref": "break",
    "lo_break_ref": "break",
    "break_up": "break",
    "break_dn": "break",
    "lo_struct": "struct",
    "hi_struct": "struct",
    "delta_5m": "delta_5m",
    "delta_15m": "delta_15m",
    "delta_1h": "delta_1h",
    "imbalance_1h": "delta_1h",
    "cvd_slope_1h": "delta_1h",
    "cvd_slope_4h": "cvd_slope_4h",
    "flow_accel": "flow_accel",
    "big_imb_1h": "big_imb_1h",
    "oi": "oi",
    "oi_chg_5m": "oi_chg_5m",
    "oi_chg_15m": "oi_chg_15m",
    "oi_chg_1h": "oi_chg_1h",
    "oi_accel": "oi_accel",
    "prem": "prem",
    "prem_1h": "prem_1h",
    "basis": "basis",
    "basis_chg_1h": "basis_chg_1h",
}
# z column -> (raw source column or None for a derived source, lookback key)
Z_COLUMNS: dict[str, tuple[str | None, str]] = {
    "ret_15m_z": ("ret_15m", "ret_15m"),
    "ret_1h_z": ("ret_1h", "ret_1h"),
    "rv_24h_z": ("rv_24h", "rv_24h"),
    "vol_5m_z": (None, "vol_5m"),  # log(volume)
    "vol_1h_z": (None, "vol_1h"),  # log(sum volume 12)
    "imbalance_1h_z": ("imbalance_1h", "delta_1h"),
    "cvd_slope_1h_z": ("cvd_slope_1h", "delta_1h"),
    "cvd_slope_4h_z": ("cvd_slope_4h", "cvd_slope_4h"),
    "flow_accel_z": ("flow_accel", "flow_accel"),
    "big_imb_1h_z": ("big_imb_1h", "big_imb_1h"),
    "oi_chg_1h_z": ("oi_chg_1h", "oi_chg_1h"),
    "oi_chg_15m_z": ("oi_chg_15m", "oi_chg_15m"),
    "prem_z": ("prem_1h", "prem_1h"),
    "basis_z": ("basis", "basis"),
}
DETECTOR_Z = ("ret_1h_z", "vol_1h_z", "imbalance_1h_z", "cvd_slope_1h_z", "oi_chg_1h_z")


@dataclass
class Validity:
    """Per-row validity of the bars and of every masked feature observation."""

    bar: B
    obs: dict[str, B] = field(default_factory=dict)  # lookback key -> valid observation at k
    atr_1h_clean: B | None = None  # per 5m row: the ATR used at that row comes from clean bars
    geometry: B | None = None  # atr valid and struct window clean
    n_1h_bars_clean: int = 0
    n_1h_bars: int = 0

    def obs_cols(self) -> set[str]:
        return set(MASKED_RAW) | set(Z_COLUMNS) | {"atr"}

    def valid_for(self, col: str) -> B:
        if col in MASKED_RAW:
            return self.obs[MASKED_RAW[col]]
        if col in Z_COLUMNS:
            return self.obs[Z_COLUMNS[col][1]]
        if col == "atr":
            assert self.geometry is not None
            return self.geometry
        return self.bar


def bar_validity(rows: pl.DataFrame) -> B:
    """Valid market observation: not gap-filled/synthetic and with at least one trade."""
    n = rows.height
    gap = (
        rows["gap_filled"].fill_null(0.0).to_numpy().astype(float) > 0
        if "gap_filled" in rows.columns
        else np.zeros(n, dtype=bool)
    )
    trades = (
        rows["trades"].fill_null(0).to_numpy().astype(float) > 0
        if "trades" in rows.columns
        else np.ones(n, dtype=bool)
    )
    close = rows["close"].to_numpy().astype(float)
    return (~gap) & trades & ~np.isnan(close)


def window_valid(valid: B, lookback: int) -> B:
    """True at k iff rows k-lookback..k are all valid (False for k < lookback)."""
    v = valid.astype(np.float64)
    if lookback == 0:
        return valid.copy()
    s = pl.Series(v).rolling_sum(lookback + 1, min_samples=lookback + 1).to_numpy()
    out = np.where(np.isnan(s), 0.0, s) >= lookback + 1
    return out.astype(bool)


def _clean_1h_bars(series: MultiTfSeries, valid: B) -> tuple[B, B, NDArray[np.int64]]:
    """Per 1h bar: fully valid (all 12 rows present and valid); clean (fully valid and the
    previous 1h bar fully valid, so its true range never uses a carried-forward close)."""
    base, h1 = series.base, series.series[Timeframe.H1]
    t5 = base.close_ms.astype(np.int64)
    open1, close1 = h1.open_ms.astype(np.int64), h1.close_ms.astype(np.int64)
    cnt_valid = np.zeros(len(open1))
    cnt_rows = np.zeros(len(open1))
    j = np.searchsorted(open1, t5 - 1, side="right") - 1  # 1h bar containing the 5m row
    ok = (j >= 0) & (j < len(open1))
    ok &= np.where(ok, t5 <= close1[np.minimum(np.maximum(j, 0), len(close1) - 1)], False)
    np.add.at(cnt_rows, j[ok], 1.0)
    np.add.at(cnt_valid, j[ok], valid[ok].astype(float))
    full = (cnt_rows >= 12) & (cnt_valid == cnt_rows)
    clean = full.copy()
    clean[1:] &= full[:-1]
    return full, clean, j


def atr_clean_1h(series: MultiTfSeries, valid: B, period: int) -> tuple[F, B, B]:
    """Frozen Wilder ATR over the clean 1h bars only; NaN at contaminated bars. On all-clean data
    identical to `indicators.atr`. Returns (atr per 1h bar, clean per 1h bar, full per 1h bar)."""
    h1 = series.series[Timeframe.H1]
    full, clean, _ = _clean_1h_bars(series, valid)
    tr = true_range(
        h1.high.astype(np.float64), h1.low.astype(np.float64), h1.close.astype(np.float64)
    )
    out = np.full(len(tr), np.nan)
    idx = np.flatnonzero(clean)
    if len(idx) >= period:
        tr_c = tr[idx]
        a = np.full(len(idx), np.nan)
        a[period - 1] = float(np.mean(tr_c[:period]))
        for i in range(period, len(idx)):
            a[i] = (a[i - 1] * (period - 1) + tr_c[i]) / period
        out[idx] = a
    return out, clean, full


def build_feature_frame_v51(
    series: MultiTfSeries,
    aux: AuxSeries,
    cfg: V51Config,
    extra: V5Inputs | None,
    bar_valid: B,
) -> tuple[FeatureFrame, Validity]:
    """The frozen V5 frame, then every masked column recomputed on valid observations only."""
    ff = build_feature_frame(series, aux, cfg, extra)  # frozen definitions, unchanged
    n = len(ff.close_ms)
    if len(bar_valid) != n:
        raise ValueError("bar validity length does not match the feature frame")
    val = Validity(bar=bar_valid.astype(bool))
    for key, lb in LOOKBACK.items():
        val.obs[key] = window_valid(val.bar, lb)
    cols = ff.cols
    # ---- raw columns: NaN where the observation is invalid
    for col, key in MASKED_RAW.items():
        if col in cols:
            m = val.obs[key]
            x = cols[col].copy()
            x[~m] = np.nan
            cols[col] = x
    # ---- z-scores: the frozen rolling_z on the masked source (NaN sources are excluded from the
    # previous-window mean/std and give a NaN z at their own row)
    win, mp = cfg.features.z_window_bars, cfg.features.z_min_periods
    vol = series.base.volume.astype(np.float64)
    src_vol5 = np.log(np.where(vol > 0, vol, np.nan))
    v1h = pl.Series(vol).rolling_sum(12, min_samples=12).to_numpy().astype(np.float64)
    src_vol1h = np.log(np.where(v1h > 0, v1h, np.nan))
    for zc, (raw, key) in Z_COLUMNS.items():
        if raw is None:
            src = src_vol5 if zc == "vol_5m_z" else src_vol1h
            src = src.copy()
        else:
            src = cols[raw].copy()  # already masked
        src[~val.obs[key]] = np.nan
        cols[zc] = rolling_z(src, win, mp)
    cols["divergence"] = cols["ret_1h_z"] - cols["cvd_slope_1h_z"]
    # ---- ATR(1h) from clean 1h bars; event geometry needs a clean structural window
    atr1, clean1, full1 = atr_clean_1h(series, val.bar, cfg.indicators.atr_period)
    h1 = series.series[Timeframe.H1]
    t = ff.close_ms
    j1 = np.searchsorted(h1.close_ms, t, side="right") - 1
    ok1 = j1 >= 0
    jj1 = np.maximum(j1, 0)
    atr_rows = np.where(ok1, atr1[jj1], np.nan)
    atr_clean_rows = ok1 & clean1[jj1]
    val.atr_1h_clean = atr_clean_rows
    val.geometry = atr_clean_rows & ~np.isnan(atr_rows) & val.obs["struct"] & val.bar
    atr_rows = atr_rows.copy()
    atr_rows[~val.geometry] = np.nan
    cols["atr"] = atr_rows
    val.n_1h_bars_clean, val.n_1h_bars = int(clean1.sum()), int(full1.sum())
    ff.regime, ff.regime_code = _regimes(ff)
    return ff, val


def validity_summary(val: Validity, k: int, win: int) -> dict[str, Any]:
    """Counts of valid observations inside the frozen z window ending before row k."""
    lo = max(0, k - win)
    out: dict[str, Any] = {
        "bars_valid_in_window": int(val.bar[lo:k].sum()),
        "bars_in_window": int(k - lo),
        "current_bar_valid": bool(val.bar[k]) if k >= 0 else False,
        "current_1h_window_clean": bool(val.obs["ret_1h"][k]) if k >= 0 else False,
        "current_geometry_valid": bool(val.geometry[k])
        if val.geometry is not None and k >= 0
        else False,
        "clean_1h_bars": val.n_1h_bars_clean,
        "full_1h_bars": val.n_1h_bars,
    }
    return out
