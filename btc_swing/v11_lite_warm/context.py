"""Read-only context telemetry for V11-LITE-WARM: Fibonacci retracement levels of the last confirmed
swing, VWAP distance, 1h / 4h range position, swing distances, ATR-normalised location, and the
strict-V11 structure sub-conditions (why V11 / V11-WARM would reject). Every value at row i is a pure
function of completed bars <= i (PIT); nothing here is read by any decision. Unconfirmable -> None (N/A)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.typing import NDArray

from btc_swing.v11.config import V11Config
from btc_swing.v11_lite_warm.config import LiteCfg

F = NDArray[np.float64]


def _fin(x: float) -> float | None:
    return None if (x is None or not math.isfinite(x)) else float(x)


def confirmed_swings(
    high: F, low: F, k: int, tf: int, i: int, lookback: int
) -> dict[str, Any] | None:
    """Last confirmed swing leg on completed `tf`-minute bars ending at 1m row i (inclusive). A pivot
    at tf-bar p (high above / low below the k bars on each side) is confirmed only once k later
    tf-bars closed at or before i, so nothing after i is used. The leg ENDS at the newest confirmed
    pivot and STARTS at the opposite extreme since the previous pivot of the same type (the move that
    led into the new pivot), which guarantees swing_high > swing_low. None if no pivot exists."""
    n_end = i + 1
    nb = n_end // tf
    if nb < 2 * k + 1:
        return None
    start_bar = max(0, nb - lookback // tf)
    hs: list[float] = []
    ls: list[float] = []
    for b in range(start_bar, nb):
        seg_h = high[b * tf : (b + 1) * tf]
        seg_l = low[b * tf : (b + 1) * tf]
        if np.isnan(seg_h).any() or np.isnan(seg_l).any():
            hs.append(math.nan)
            ls.append(math.nan)
        else:
            hs.append(float(np.max(seg_h)))
            ls.append(float(np.min(seg_l)))
    m = len(hs)
    highs: list[int] = []
    lows: list[int] = []
    for p in range(k, m - k):
        hp, lp = hs[p], ls[p]
        wh = hs[p - k : p] + hs[p + 1 : p + k + 1]
        wl = ls[p - k : p] + ls[p + 1 : p + k + 1]
        if not math.isnan(hp) and all(not math.isnan(x) and x < hp for x in wh):
            highs.append(p)
        if not math.isnan(lp) and all(not math.isnan(x) and x > lp for x in wl):
            lows.append(p)
    if not highs and not lows:
        return None
    newest_is_high = bool(highs) and (not lows or highs[-1] >= lows[-1])
    if newest_is_high:
        p_end = highs[-1]
        q = highs[-2] if len(highs) > 1 else -1
        seg = ls[q + 1 : p_end]
        if not seg or all(math.isnan(x) for x in seg):
            return None
        p_start = q + 1 + int(np.nanargmin(np.array(seg)))
        hi_v, lo_v, hi_bar, lo_bar = hs[p_end], ls[p_start], p_end, p_start
    else:
        p_end = lows[-1]
        q = lows[-2] if len(lows) > 1 else -1
        seg = hs[q + 1 : p_end]
        if not seg or all(math.isnan(x) for x in seg):
            return None
        p_start = q + 1 + int(np.nanargmax(np.array(seg)))
        hi_v, lo_v, hi_bar, lo_bar = hs[p_start], ls[p_end], p_start, p_end
    if not (hi_v > lo_v):
        return None
    rel_h = (start_bar + hi_bar + 1) * tf - 1  # last 1m row of the swing-high tf-bar
    rel_l = (start_bar + lo_bar + 1) * tf - 1
    return {
        "swing_high": hi_v,
        "swing_low": lo_v,
        "swing_high_row": rel_h,
        "swing_low_row": rel_l,
        "direction": "UP" if newest_is_high else "DOWN",  # the leg into the newest pivot
        "confirmed_at_row": (start_bar + p_end + 1 + k) * tf - 1,
    }


def fibonacci(
    close: float, atr5: float, sw: dict[str, Any] | None, ratios: list[float]
) -> dict[str, Any]:
    """Retracement levels of the last leg and the distances close -> level (bps and ATR5)."""
    if sw is None or not math.isfinite(close) or close <= 0:
        return {"available": False}
    hi, lo = float(sw["swing_high"]), float(sw["swing_low"])
    rng = hi - lo
    if rng <= 0:
        return {"available": False}
    up = sw["direction"] == "UP"
    levels = {f"{r:.3f}": (hi - r * rng if up else lo + r * rng) for r in ratios}
    a5 = atr5 if (atr5 is not None and math.isfinite(atr5) and atr5 > 0) else math.nan
    dist_bps = {k: (close - v) / close * 1e4 for k, v in levels.items()}
    dist_atr = {k: ((close - v) / a5 if math.isfinite(a5) else None) for k, v in levels.items()}
    nearest = min(levels, key=lambda k: abs(close - levels[k]))
    near_atr = dist_atr[nearest]
    return {
        "available": True,
        "direction": sw["direction"],
        "swing_high": hi,
        "swing_low": lo,
        "swing_high_row": sw["swing_high_row"],
        "swing_low_row": sw["swing_low_row"],
        "leg_bps": rng / close * 1e4,
        "retrace_frac": ((hi - close) / rng) if up else ((close - lo) / rng),
        "levels": levels,
        "dist_bps": dist_bps,
        "dist_atr": dist_atr,
        "nearest_ratio": nearest,
        "nearest_dist_bps": dist_bps[nearest],
        "nearest_abs_dist_atr": None if near_atr is None else abs(near_atr),
    }


def fib_bucket(abs_dist_atr: float | None, edges: list[float]) -> str:
    if abs_dist_atr is None or not math.isfinite(abs_dist_atr):
        return "N/A"
    prev = 0.0
    for e in edges:
        if abs_dist_atr <= e:
            return f"<= {e:.2f}" if prev == 0.0 else f"{prev:.2f}-{e:.2f}"
        prev = e
    return f"> {prev:.2f}"


def range_position(high: F, low: F, close: float, i: int, minutes: int) -> dict[str, Any]:
    """Position of the close inside the high/low of the last `minutes` completed bars (incl. i)."""
    j0 = i + 1 - minutes
    if j0 < 0:
        return {"available": False}
    h, lo = high[j0 : i + 1], low[j0 : i + 1]
    if np.isnan(h).sum() > minutes * 0.05 or np.isnan(lo).sum() > minutes * 0.05:
        return {"available": False}
    hi_v, lo_v = float(np.nanmax(h)), float(np.nanmin(lo))
    if not (math.isfinite(close) and hi_v > lo_v):
        return {"available": False}
    return {
        "available": True,
        "high": hi_v,
        "low": lo_v,
        "position": (close - lo_v) / (hi_v - lo_v),
        "width_bps": (hi_v - lo_v) / close * 1e4,
    }


def _dist(close: float, lvl: float, a5: float) -> dict[str, float | None]:
    if not (math.isfinite(close) and math.isfinite(lvl) and close > 0):
        return {"bps": None, "atr": None}
    return {
        "bps": (close - lvl) / close * 1e4,
        "atr": ((close - lvl) / a5) if (math.isfinite(a5) and a5 > 0) else None,
    }


def structure_detail(cfg: V11Config, x: dict[str, F], fam: str, s: int, i: int) -> dict[str, Any]:
    """The frozen V11 structure sub-conditions (mirrors V11Engine.structure, read-only), so a
    rejection can be attributed to the exact failed condition."""
    c, a5 = float(x["close"][i]), float(x["atr5"][i])
    valid = bool(np.isfinite(c) and np.isfinite(a5) and a5 > 0)
    if fam == "A":
        ok = bool(x["a_struct_long"][i] == 1.0 if s > 0 else x["a_struct_short"][i] == 1.0)
        turn = bool((s * x["accel5"][i] > 0) or (s * (x["rsi7"][i] - x["rsi7_prev"][i]) > 0))
        return {
            "data_valid": valid,
            "impulse_pullback": ok,
            "struct15_aligned": bool(x["struct15"][i] == s),
            "turn": turn,
        }
    if fam == "B":
        fb = cfg.families.b
        comp = bool(x["squeeze_release_recent"][i] == 1.0 or x["atr_low_recent"][i] == 1.0)
        lvl = x["hi60"][i] if s > 0 else x["lo60"][i]
        near = bool(np.isfinite(lvl) and 0 <= s * (lvl - c) <= fb.near_boundary_atr * a5)
        return {"data_valid": valid, "compression": comp, "near_boundary": near}
    fcc = cfg.families.c
    recent = bool(x["sweep_long_recent"][i] == 1.0 if s > 0 else x["sweep_short_recent"][i] == 1.0)
    r7 = x["rsi7"][i]
    exhausted = bool(
        np.isfinite(r7) and (r7 <= fcc.rsi_exhaust if s > 0 else r7 >= 100.0 - fcc.rsi_exhaust)
    )
    return {"data_valid": valid, "sweep_recent": recent, "rsi_exhausted": exhausted}


def context_at(
    cfg: V11Config, lite: LiteCfg, x: dict[str, F], i: int, micro: dict[str, Any] | None
) -> dict[str, Any]:
    """All read-only context for row i (side-independent; the report side-adjusts)."""
    c, a5 = float(x["close"][i]), float(x["atr5"][i])
    sw = confirmed_swings(
        x["high"],
        x["low"],
        cfg.features.swing_k,
        lite.fib_swing_tf_min,
        i,
        lite.fib_swing_lookback_min,
    )
    fib = fibonacci(c, a5, sw, list(lite.fib_ratios))
    fib["bucket_atr"] = fib_bucket(fib.get("nearest_abs_dist_atr"), list(lite.fib_bucket_edges_atr))
    vwap = float(x["vwap"][i])
    return {
        "fib": fib,
        "vwap": {
            "vwap": _fin(vwap),
            "dist_bps": ((c - vwap) / vwap * 1e4) if (math.isfinite(vwap) and vwap > 0) else None,
            "dist_atr": _fin(float(x["dist_vwap"][i])),
            "slope_atr": _fin(float(x["vwap_slope"][i])),
        },
        "range": {
            f"{m}m": range_position(x["high"], x["low"], c, i, m) for m in lite.range_minutes
        },
        "swings": {
            k: _dist(c, float(x[k][i]), a5)
            for k in ("swing_hi5", "swing_lo5", "swing_hi15", "swing_lo15", "hi60", "lo60")
            if k in x
        },
        "location_atr": {
            "close_minus_ema21": _fin((c - float(x["ema21"][i])) / a5) if a5 > 0 else None,
            "close_minus_ema50": _fin((c - float(x["ema50"][i])) / a5) if a5 > 0 else None,
            "close_minus_vwap": _fin(float(x["dist_vwap"][i])),
            "to_hi60": _fin((float(x["hi60"][i]) - c) / a5) if a5 > 0 else None,
            "to_lo60": _fin((c - float(x["lo60"][i])) / a5) if a5 > 0 else None,
        },
        "flow": {
            k: _fin(float(x[k][i]))
            for k in ("delta1", "delta5", "delta15", "imb5", "imb15", "intensity1", "rvol5")
            if k in x
        },
        "microstructure": {
            k: (None if v is None or (isinstance(v, float) and not math.isfinite(v)) else v)
            for k, v in (micro or {}).items()
        },
        "structure_detail": {
            fam: {
                "LONG": structure_detail(cfg, x, fam, 1, i),
                "SHORT": structure_detail(cfg, x, fam, -1, i),
            }
            for fam in ("A", "B", "C")
        },
    }
