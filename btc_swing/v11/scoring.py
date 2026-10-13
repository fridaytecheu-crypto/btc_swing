"""V11 group sub-scores and the 0-100 LONG / SHORT scores (`v11-score-1`, design section 3-4).
Vectorised over a frame; the live path scores the last row the same way. Groups without data are
renormalised away (never filled with neutral values); every row keeps its group contributions."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from btc_swing.v11.config import GROUPS, V11Config
from btc_swing.v11.features import V11Frame

F = NDArray[np.float64]


def _v(cond: NDArray[np.bool_] | F, w: float) -> F:
    return w * np.asarray(cond, dtype=np.float64)


def _fin(*xs: F) -> NDArray[np.bool_]:
    out = np.ones(len(xs[0]), dtype=bool)
    for x in xs:
        out &= np.isfinite(x)
    return out


def group_scores(
    fr: V11Frame, cfg: V11Config, s: float, micro: dict[str, F] | None = None
) -> dict[str, F]:
    """Sub-scores in [0, 1] per group for side s (+1 LONG, -1 SHORT); NaN = group unavailable."""
    X = fr.c  # noqa: N806
    th = cfg.thresholds
    n = len(fr)
    nan = np.full(n, np.nan)
    with np.errstate(invalid="ignore"):
        # A trend
        a = (
            _v(s * (X["close"] - X["vwap"]) > 0, 0.20)
            + _v(s * (X["ema9"] - X["ema21"]) > 0, 0.20)
            + _v(s * (X["ema21"] - X["ema50"]) > 0, 0.20)
            + _v(X["struct15"] == s, 0.25)
            + _v(s * X["ema21_slope"] > 0, 0.15)
        )
        a = np.where(s * X["dist_vwap"] > th.overextension_vwap_atr, 0.5 * a, a)
        a = np.where(_fin(X["vwap"], X["ema50"], X["struct15"], X["atr5"]), a, nan)
        # B momentum
        rsi_exh = X["rsi7"] > th.rsi_exhaust_high if s > 0 else X["rsi7"] < th.rsi_exhaust_low
        b = (
            _v(s * (X["rsi14"] - 50.0) > 0, 0.20)
            + _v(s * (X["rsi7"] - X["rsi7_prev"]) > 0, 0.20)
            + _v(X["adx14"] > 20.0, 0.20)
            + _v(s * X["roc3"] > 0, 0.20)
            + _v(s * X["accel5"] > 0, 0.20)
        )
        b = np.where(rsi_exh, 0.5 * b, b)
        b = np.where(_fin(X["rsi14"], X["rsi7_prev"], X["adx14"], X["roc3"], X["accel5"]), b, nan)
        # C volatility (neutral)
        cvol = (
            _v(X["atr_band"] == 1.0, 0.40)
            + _v(X["bbw_rising"] == 1.0, 0.30)
            + _v(np.abs(X["bb_dev"]) <= 2.2, 0.30)
        )
        cvol = np.where(_fin(X["atr_band"], X["bbw_rising"], X["bb_dev"]), cvol, nan)
        # D volume / flow
        d = (
            _v(s * X["imb5"] > 0.10, 0.25)
            + _v(s * X["delta15"] > 0, 0.20)
            + _v(X["rvol5"] >= 1.2, 0.25)
            + _v(X["intensity1"] >= 1.2, 0.15)
            + _v(s * (X["cum_delta"] - X["cum_delta_15ago"]) > 0, 0.15)
        )
        d = np.where(
            _fin(X["imb5"], X["delta15"], X["rvol5"], X["intensity1"], X["cum_delta_15ago"]), d, nan
        )
        # E location
        dv = s * X["dist_vwap"]
        if s > 0:
            room = ((X["hi60"] - X["close"]) >= 0.5 * X["atr5"]) | (X["close"] > X["hi60"])
            above_swing = X["close"] > X["swing_lo5"]
        else:
            room = ((X["close"] - X["lo60"]) >= 0.5 * X["atr5"]) | (X["close"] < X["lo60"])
            above_swing = X["close"] < X["swing_hi5"]
        e = _v((dv >= -0.5) & (dv <= 2.0), 0.30) + _v(above_swing, 0.30) + _v(room, 0.40)
        e = np.where(
            _fin(X["dist_vwap"], X["swing_lo5"], X["swing_hi5"], X["hi60"], X["lo60"]), e, nan
        )
        # F derivatives
        if s > 0:
            div = (X["ret_1h"] > 0) & (X["oi_chg_1h"] < 0)
            fund_ok = X["funding"] <= 0.0003
            oi_ok = (X["oi_chg_1h"] > 0) & (X["ret_1h"] > 0)
        else:
            div = (X["ret_1h"] < 0) & (X["oi_chg_1h"] < 0)
            fund_ok = X["funding"] >= -0.0003
            oi_ok = (X["oi_chg_1h"] > 0) & (X["ret_1h"] < 0)
        f = _v(oi_ok, 0.40) + _v(fund_ok, 0.30) + _v(~div, 0.30)
        f = np.where(_fin(X["oi_chg_1h"], X["funding"], X["ret_1h"]), f, nan)
        # G microstructure (forward-only; dict of arrays aligned with the frame, or None)
        if micro is None:
            g = nan
        else:
            m = micro
            liq_burst = (m["liq_burst_pct_120"] >= 0.98) & (s * m["liq_imb_120"] >= 0.5)
            repl = m["bid_repl"] if s > 0 else m["ask_repl"]
            g = (
                _v(s * m["imb_30"] > 0.10, 0.25)
                + _v(s * m["imb_10"] > 0, 0.15)
                + _v((s * m["depth_imb"] > 0) | (repl >= 1.0), 0.20)
                + _v((m["efficient_30"] == 1.0) & (s * m["delta_30"] > 0), 0.20)
                + _v(liq_burst, 0.20)
            )
            g = np.where(_fin(m["imb_30"], m["imb_10"], m["delta_30"]), g, nan)
    return {
        "trend": a,
        "momentum": b,
        "volatility": cvol,
        "volume": d,
        "location": e,
        "derivatives": f,
        "microstructure": g,
    }


def total_score(sub: dict[str, F], cfg: V11Config) -> tuple[F, dict[str, F], F]:
    """0-100 score renormalised over available groups; returns (score, contributions, available weight)."""
    w = cfg.weights.as_dict()
    n = len(next(iter(sub.values())))
    num = np.zeros(n)
    den = np.zeros(n)
    contrib: dict[str, F] = {}
    for g_name in GROUPS:
        x = sub[g_name]
        ok = np.isfinite(x)
        contrib[g_name] = np.where(ok, w[g_name] * x, np.nan)
        num += np.where(ok, w[g_name] * x, 0.0)
        den += np.where(ok, w[g_name], 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        score = np.where(den > 0, 100.0 * num / den, np.nan)
    return score, contrib, den


def score_frame(fr: V11Frame, cfg: V11Config, micro: dict[str, F] | None = None) -> dict[str, F]:
    out: dict[str, F] = {}
    for s, tag in ((1.0, "long"), (-1.0, "short")):
        sub = group_scores(fr, cfg, s, micro)
        score, contrib, den = total_score(sub, cfg)
        out[f"score_{tag}"] = score
        out[f"avail_weight_{tag}"] = den
        for g_name in GROUPS:
            out[f"{tag}_{g_name}"] = contrib[g_name]
    return out
