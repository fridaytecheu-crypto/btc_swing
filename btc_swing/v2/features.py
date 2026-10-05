"""Feature set `v2-fs-1`: fixed, documented, strictly PIT.

Every feature is computed at the candidate decision time t from (a) the `MarketView` at t
(completed bars only; reading a later bar raises), (b) the plan frozen at detection, (c) the regime
journal up to t, (d) the `AuxSeries` snapshot at t (observation time + latency <= t). Momentum and
distance features are signed in the trade direction (positive = in favour of the candidate's side)
unless the name says `raw`. NaN = not available at t; the model pipeline imputes with the
training-fold median and `n_missing` counts them. The list `FEATURE_NAMES` is the whole space:
nothing is added or removed after results are seen.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np

from btc_swing.core.enums import Regime, SetupFamily, Side, Timeframe
from btc_swing.features.context import AuxSeries
from btc_swing.features.view import MarketView, MultiTfSeries

if TYPE_CHECKING:
    from btc_swing.v2.candidates import Candidate

FAMILIES = [f.value for f in SetupFamily]
REGIMES = [r.value for r in Regime]
DERIVATIVES_FEATURES = [
    "funding_rate_last",
    "funding_rate_mean_3",
    "funding_minus_mean3",
    "funding_z_90",
    "log_open_interest",
    "oi_change_1h_pct",
    "oi_change_4h_pct",
    "oi_change_24h_pct",
    "price_oi_interaction_24h",
    "long_short_ratio_accounts",
    "top_trader_ls_positions",
    "taker_long_short_vol_ratio",
    "taker_buy_ratio_1h",
    "taker_buy_ratio_4h",
    "taker_buy_ratio_diff",
    "premium_index",
    "premium_mean_1h",
    "premium_minus_mean_1h",
    "last_minus_mark_pct",
]
FEATURE_NAMES: list[str] = [
    # setup
    *[f"fam_{f}" for f in FAMILIES],
    "side",
    "setup_age_bars",
    "bars_since_confirm",
    "dist_zone_mid_atr",
    "dist_anchor_atr",
    "stop_dist_atr",
    "stop_dist_pct",
    "zone_width_atr",
    "structural_target_r",
    "has_structural_target",
    # price / momentum
    "mom_15m",
    "mom_1h",
    "mom_4h",
    "mom_24h",
    "mom_7d",
    "price_change_24h_pct_raw",
    "dist_ema_fast_1h_atr",
    "dist_ema_slow_1h_atr",
    "ema_spread_1h_atr",
    "dist_ema_fast_4h_atr",
    "dist_ema_slow_4h_atr",
    "ema_spread_4h_atr",
    "dist_ema_trend_1d_atr",
    "ema_spread_1d_atr",
    "ema_slow_slope_1d_atr",
    "range_pos_4h_raw",
    "range_pos_4h_dir",
    "range_pos_1d_raw",
    "range_pos_1d_dir",
    "retrace_1h_atr",
    "volume_accel_5m",
    "volume_accel_1h",
    "atr_pct_1h",
    "atr_pct_4h",
    "atr_pct_1d",
    "atr_expansion_1h",
    "atr_expansion_1d",
    "atr_pct_1d_percentile_90",
    "rv_24h",
    "rv_7d",
    # regime
    *[f"reg_{r}" for r in REGIMES],
    "regime_age_bars",
    "regime_transitions_7d",
    "short_in_trend_down",
    # derivatives
    *DERIVATIVES_FEATURES,
    # context
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "weekend",
    "dist_30d_high_pct",
    "dist_30d_low_pct",
    "n_missing",
]


def _ret(view: MarketView, tf: Timeframe, k: int) -> float:
    try:
        c0, ck = view.close(tf, 0), view.close(tf, k)
    except IndexError:
        return math.nan
    return c0 / ck - 1.0 if ck > 0 else math.nan


def _ind(view: MarketView, tf: Timeframe, name: str, offset: int = 0) -> float:
    try:
        return view.ind(tf, name, offset)
    except (IndexError, KeyError):
        return math.nan


def _div(a: float, b: float) -> float:
    return a / b if (not math.isnan(a) and not math.isnan(b) and b != 0.0) else math.nan


def _realised_vol(series: MultiTfSeries, view: MarketView, bars: int) -> float:
    base = series.base
    i = view.idx[Timeframe.M5]
    if i < bars:
        return math.nan
    c = base.close[i - bars : i + 1]
    lr = np.diff(np.log(c))
    return float(np.std(lr, ddof=1) * math.sqrt(288.0)) if len(lr) > 2 else math.nan


def _percentile_prev(arr: np.ndarray, i: int, window: int) -> float:
    if i < window:
        return math.nan
    prev = arr[i - window : i]
    cur = arr[i]
    prev = prev[~np.isnan(prev)]
    if len(prev) < window // 2 or math.isnan(cur):
        return math.nan
    return float(np.mean(prev < cur))


def _extreme_prev(view: MarketView, tf: Timeframe, bars: int, high: bool) -> float:
    s = view.series.series[tf]
    i = view.idx[tf]
    if i < 0:
        return math.nan
    lo = max(0, i - bars + 1)
    seg = s.high[lo : i + 1] if high else s.low[lo : i + 1]
    return float(np.max(seg)) if high else float(np.min(seg))


def compute_features(
    cand: Candidate,
    view: MarketView,
    series: MultiTfSeries,
    aux: AuxSeries,
    snap: dict[str, float],
) -> dict[str, float]:
    f: dict[str, float] = {}
    plan = cand.plan
    s = float(cand.side.sign)
    c = cand.close_at_trigger
    atr_s = plan.atr_setup_tf if plan.atr_setup_tf > 0 else math.nan
    # setup
    for fam in FAMILIES:
        f[f"fam_{fam}"] = 1.0 if cand.family == fam else 0.0
    f["side"] = s
    f["setup_age_bars"] = float(cand.trigger_bar - cand.detected_bar)
    f["bars_since_confirm"] = (
        float(cand.trigger_bar - cand.confirmed_bar) if cand.confirmed_bar is not None else math.nan
    )
    f["dist_zone_mid_atr"] = _div(s * (c - plan.zone_mid), atr_s)
    f["dist_anchor_atr"] = _div(s * (c - plan.anchor), atr_s)
    stop_dist = s * (c - plan.stop_price)
    f["stop_dist_atr"] = _div(stop_dist, atr_s)
    f["stop_dist_pct"] = _div(stop_dist, c) * 100.0
    f["zone_width_atr"] = _div(plan.entry_zone_high - plan.entry_zone_low, atr_s)
    f["structural_target_r"] = (
        _div(s * (plan.structural_target - c), stop_dist)
        if plan.structural_target is not None
        else math.nan
    )
    f["has_structural_target"] = 1.0 if plan.structural_target is not None else 0.0
    # momentum (signed)
    f["mom_15m"] = s * _ret(view, Timeframe.M5, 3)
    f["mom_1h"] = s * _ret(view, Timeframe.M15, 4)
    f["mom_4h"] = s * _ret(view, Timeframe.H1, 4)
    f["mom_24h"] = s * _ret(view, Timeframe.H4, 6)
    f["mom_7d"] = s * _ret(view, Timeframe.D1, 7)
    p24 = _ret(view, Timeframe.H4, 6) * 100.0
    f["price_change_24h_pct_raw"] = p24
    for tf, tag in ((Timeframe.H1, "1h"), (Timeframe.H4, "4h")):
        a = _ind(view, tf, "atr")
        ef, es = _ind(view, tf, "ema_fast"), _ind(view, tf, "ema_slow")
        ctf = view.close(tf) if view.n(tf) > 0 else math.nan
        f[f"dist_ema_fast_{tag}_atr"] = _div(s * (ctf - ef), a)
        f[f"dist_ema_slow_{tag}_atr"] = _div(s * (ctf - es), a)
        f[f"ema_spread_{tag}_atr"] = _div(s * (ef - es), a)
    a1 = _ind(view, Timeframe.D1, "atr")
    c1 = view.close(Timeframe.D1) if view.n(Timeframe.D1) > 0 else math.nan
    f["dist_ema_trend_1d_atr"] = _div(s * (c1 - _ind(view, Timeframe.D1, "ema_trend")), a1)
    f["ema_spread_1d_atr"] = _div(
        s * (_ind(view, Timeframe.D1, "ema_fast") - _ind(view, Timeframe.D1, "ema_slow")), a1
    )
    f["ema_slow_slope_1d_atr"] = _div(s * _ind(view, Timeframe.D1, "ema_slow_slope"), a1)
    for tf, tag in ((Timeframe.H4, "4h"), (Timeframe.D1, "1d")):
        dh, dl = _ind(view, tf, "donchian_high"), _ind(view, tf, "donchian_low")
        ctf = view.close(tf) if view.n(tf) > 0 else math.nan
        pos = _div(ctf - dl, dh - dl)
        f[f"range_pos_{tag}_raw"] = pos
        f[f"range_pos_{tag}_dir"] = pos if s > 0 else (1.0 - pos if not math.isnan(pos) else pos)
    ah = _ind(view, Timeframe.H1, "atr")
    if s > 0:
        f["retrace_1h_atr"] = _div(_ind(view, Timeframe.H1, "swing_high") - c, ah)
    else:
        f["retrace_1h_atr"] = _div(c - _ind(view, Timeframe.H1, "swing_low"), ah)
    f["volume_accel_5m"] = snap.get("volume_accel_5m", math.nan)
    f["volume_accel_1h"] = snap.get("volume_accel_1h", math.nan)
    f["atr_pct_1h"] = _ind(view, Timeframe.H1, "atr_pct")
    f["atr_pct_4h"] = _ind(view, Timeframe.H4, "atr_pct")
    f["atr_pct_1d"] = _ind(view, Timeframe.D1, "atr_pct")
    f["atr_expansion_1h"] = _div(ah, _ind(view, Timeframe.H1, "atr_prev10"))
    f["atr_expansion_1d"] = _div(a1, _ind(view, Timeframe.D1, "atr_prev10"))
    d1 = series.series[Timeframe.D1]
    f["atr_pct_1d_percentile_90"] = _percentile_prev(d1.ind["atr_pct"], view.idx[Timeframe.D1], 90)
    f["rv_24h"] = _realised_vol(series, view, 288)
    f["rv_7d"] = _realised_vol(series, view, 2016)
    # regime
    for r in REGIMES:
        f[f"reg_{r}"] = 1.0 if cand.regime_at_trigger.value == r else 0.0
    f["regime_age_bars"] = float(cand.regime_age_bars)
    f["regime_transitions_7d"] = float(cand.regime_transitions_7d)
    f["short_in_trend_down"] = (
        1.0 if (cand.side is Side.SHORT and cand.regime_at_trigger is Regime.TREND_DOWN) else 0.0
    )
    # derivatives
    fr, fm3 = snap.get("funding_rate_last", math.nan), snap.get("funding_rate_mean_3", math.nan)
    f["funding_rate_last"] = fr
    f["funding_rate_mean_3"] = fm3
    f["funding_minus_mean3"] = fr - fm3 if not (math.isnan(fr) or math.isnan(fm3)) else math.nan
    f["funding_z_90"] = _funding_z(aux, cand.trigger_ms, 90)
    oi = snap.get("open_interest", math.nan)
    f["log_open_interest"] = math.log(oi) if not math.isnan(oi) and oi > 0 else math.nan
    for tag in ("1h", "4h", "24h"):
        f[f"oi_change_{tag}_pct"] = snap.get(f"oi_change_{tag}_pct", math.nan)
    oi24 = f["oi_change_24h_pct"]
    f["price_oi_interaction_24h"] = (
        p24 * oi24 if not (math.isnan(p24) or math.isnan(oi24)) else math.nan
    )
    f["long_short_ratio_accounts"] = snap.get("long_short_ratio_accounts", math.nan)
    f["top_trader_ls_positions"] = snap.get("top_trader_ls_positions", math.nan)
    f["taker_long_short_vol_ratio"] = snap.get("taker_long_short_vol_ratio", math.nan)
    tb1, tb4 = snap.get("taker_buy_ratio_1h", math.nan), snap.get("taker_buy_ratio_4h", math.nan)
    f["taker_buy_ratio_1h"] = tb1
    f["taker_buy_ratio_4h"] = tb4
    f["taker_buy_ratio_diff"] = tb1 - tb4 if not (math.isnan(tb1) or math.isnan(tb4)) else math.nan
    pr, pm = snap.get("premium_index", math.nan), snap.get("premium_mean_1h", math.nan)
    f["premium_index"] = pr
    f["premium_mean_1h"] = pm
    f["premium_minus_mean_1h"] = pr - pm if not (math.isnan(pr) or math.isnan(pm)) else math.nan
    f["last_minus_mark_pct"] = snap.get("last_minus_mark_pct", math.nan)
    # context
    dt = datetime.fromtimestamp(cand.trigger_ms / 1000, tz=UTC)
    hour = dt.hour + dt.minute / 60.0
    f["hour_sin"] = math.sin(2 * math.pi * hour / 24.0)
    f["hour_cos"] = math.cos(2 * math.pi * hour / 24.0)
    f["dow_sin"] = math.sin(2 * math.pi * dt.weekday() / 7.0)
    f["dow_cos"] = math.cos(2 * math.pi * dt.weekday() / 7.0)
    f["weekend"] = 1.0 if dt.weekday() >= 5 else 0.0
    hi30 = _extreme_prev(view, Timeframe.D1, 30, True)
    lo30 = _extreme_prev(view, Timeframe.D1, 30, False)
    f["dist_30d_high_pct"] = (_div(c, hi30) - 1.0) * 100.0 if not math.isnan(hi30) else math.nan
    f["dist_30d_low_pct"] = (_div(c, lo30) - 1.0) * 100.0 if not math.isnan(lo30) else math.nan
    f["n_missing"] = float(sum(1 for k in FEATURE_NAMES if k != "n_missing" and _isnan(f.get(k))))
    return {k: f.get(k, math.nan) for k in FEATURE_NAMES}


def _isnan(x: float | None) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def _funding_z(aux: AuxSeries, t: int, window: int) -> float:
    if len(aux.funding_t) == 0:
        return math.nan
    i = int(np.searchsorted(aux.funding_t, t - aux.latency_ms, side="right")) - 1
    if i < window:
        return math.nan
    prev = aux.funding_rate[i - window : i]
    sd = float(np.std(prev, ddof=1))
    return float((aux.funding_rate[i] - np.mean(prev)) / sd) if sd > 0 else math.nan
