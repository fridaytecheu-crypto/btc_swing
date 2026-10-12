"""V9 setup detection on completed 5m bars (design section 3). One code path for both directions:
SHORT uses the mirrored price series (high <-> -low, close -> -close, trend -> -trend).

Leg table (per peak bar p): is p the peak of an impulse leg of at most `max_leg_bars` bars, with
origin o (lowest low in p-L..p-1), size, size in ATR, impulse RVOL and impulse volume.
Arm table (per arming bar i): the earliest valid leg peak p = i - n (n in [min_bars, max_bars]) such
that no bar after p exceeded the peak, with the pullback low, retrace share and pullback volume ratio.
Thresholds are NOT applied here (the widest studied retrace bounds only); variants filter the tables."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from btc_swing.v9.config import V9Config
from btc_swing.v9.features import V9Frame

F = NDArray[np.float64]
I = NDArray[np.int64]  # noqa: E741
B = NDArray[np.bool_]


@dataclass
class LegTable:
    ok: B  # leg exists at peak p (structure + clean bars + finite ATR/trend)
    origin: I  # bar index o
    size: F  # price units (mirrored space)
    size_atr: F
    imp_rvol: F
    imp_vol: F
    trend_ok: B  # 15m trend at p aligned with the direction
    peak: F  # mirrored high at p
    origin_px: F  # mirrored low at o


@dataclass
class ArmTable:
    p: I  # peak bar of the chosen leg, -1 if none
    n_pb: I
    retrace: F
    pb_ratio: F
    pb_low: F  # mirrored
    origin_intact: B


@dataclass
class DirectionTables:
    direction: int
    legs: LegTable
    arms: ArmTable


def _mirror(fr: V9Frame, direction: int) -> tuple[F, F, F, F]:
    if direction > 0:
        return fr.b5["high"], fr.b5["low"], fr.b5["close"], fr.b5["trend"]
    return -fr.b5["low"], -fr.b5["high"], -fr.b5["close"], -fr.b5["trend"]


def _sliding(x: F, w: int, fill: float) -> F:
    """Windows of length w ending at each index (rows < w-1 are filled)."""
    n = len(x)
    out = np.full((n, w), fill)
    if n >= w:
        out[w - 1 :] = np.lib.stride_tricks.sliding_window_view(x, w)
    return out


def build_leg_table(fr: V9Frame, direction: int, cfg: V9Config) -> LegTable:
    h, lo, _c, trend = _mirror(fr, direction)
    v = fr.b5["valid"] == 1.0
    atr, rvol, vol = fr.b5["atr5"], fr.b5["rvol5"], fr.b5["volume"]
    n = len(h)
    lmax = cfg.impulse.max_leg_bars
    # peak: h[p] >= max h[p-lmax..p-1]
    hw = _sliding(np.where(np.isnan(h), -np.inf, h), lmax + 1, -np.inf)
    prev_max = hw[:, :-1].max(axis=1)
    peak_ok = h >= prev_max
    # origin: lowest low in p-lmax..p (must precede p)
    lw = _sliding(np.where(np.isnan(lo), np.inf, lo), lmax + 1, np.inf)
    rel = lw.argmin(axis=1)
    origin = np.arange(n) - lmax + rel
    origin_ok = rel < lmax
    origin_px = np.where(origin_ok, lw[np.arange(n), rel], np.nan)
    size = h - origin_px
    # clean window p-lmax..p
    vw = _sliding(v.astype(np.float64), lmax + 1, 0.0).min(axis=1) == 1.0
    ok = (
        peak_ok
        & origin_ok
        & vw
        & (size > 0)
        & ~np.isnan(atr)
        & (atr > 0)
        & ~np.isnan(trend)
        & ~np.isnan(rvol)
    )
    size_atr = np.where(ok, size / atr, np.nan)
    # leg means over o+1..p via cumulative sums
    cs_v = np.concatenate([[0.0], np.cumsum(np.nan_to_num(vol))])
    cs_r = np.concatenate([[0.0], np.cumsum(np.nan_to_num(rvol))])
    p = np.arange(n)
    o = np.clip(origin, 0, n - 1)
    leg_len = np.maximum(p - o, 1)
    imp_vol = (cs_v[p + 1] - cs_v[o + 1]) / leg_len
    imp_rvol = (cs_r[p + 1] - cs_r[o + 1]) / leg_len
    # a NaN rvol inside the leg invalidates the leg's RVOL
    cs_nan = np.concatenate([[0.0], np.cumsum(np.isnan(rvol).astype(np.float64))])
    rvol_clean = (cs_nan[p + 1] - cs_nan[o + 1]) == 0
    imp_rvol = np.where(rvol_clean, imp_rvol, np.nan)
    ok &= rvol_clean
    return LegTable(
        ok=ok,
        origin=np.where(ok, o, -1).astype(np.int64),
        size=np.where(ok, size, np.nan),
        size_atr=size_atr,
        imp_rvol=np.where(ok, imp_rvol, np.nan),
        imp_vol=np.where(ok, imp_vol, np.nan),
        trend_ok=ok & (trend == 1.0),
        peak=np.where(ok, h, np.nan),
        origin_px=np.where(ok, origin_px, np.nan),
    )


def build_arm_table(fr: V9Frame, direction: int, legs: LegTable, cfg: V9Config) -> ArmTable:
    h, lo, _c, _t = _mirror(fr, direction)
    vol = fr.b5["volume"]
    n = len(h)
    pb = cfg.pullback
    r_lo = min(min(g) for g in [*pb.retrace_grid, [pb.retrace_min, pb.retrace_max]])
    r_hi = max(max(g) for g in [*pb.retrace_grid, [pb.retrace_min, pb.retrace_max]])
    cs_v = np.concatenate([[0.0], np.cumsum(np.nan_to_num(vol))])
    best_p = np.full(n, -1, dtype=np.int64)
    best_n = np.zeros(n, dtype=np.int64)
    best_ret = np.full(n, np.nan)
    best_ratio = np.full(n, np.nan)
    best_low = np.full(n, np.nan)
    best_intact = np.zeros(n, dtype=bool)
    i_all = np.arange(n)
    hn = np.where(np.isnan(h), np.inf, h)  # a NaN bar after the peak voids the setup
    ln = np.where(np.isnan(lo), np.nan, lo)
    for k in range(pb.max_bars, pb.min_bars - 1, -1):  # earliest valid peak wins
        p = i_all - k
        okp = p >= 0
        pc = np.clip(p, 0, n - 1)
        leg_ok = legs.ok[pc] & okp
        # no bar in p+1..i above the peak; pullback low over p+1..i
        hw = _sliding(hn, k, np.inf)  # windows p+1..i (length k)
        lw = _sliding(ln, k, np.nan)
        above = hw.max(axis=1) > legs.peak[pc]
        pb_low = lw.min(axis=1)  # NaN if any NaN (np.min propagates)
        size = legs.size[pc]
        with np.errstate(invalid="ignore", divide="ignore"):
            ret = (legs.peak[pc] - pb_low) / size
            ratio = ((cs_v[i_all + 1] - cs_v[pc + 1]) / k) / legs.imp_vol[pc]
        intact = pb_low > legs.origin_px[pc]
        good = leg_ok & ~above & ~np.isnan(pb_low) & (ret >= r_lo) & (ret <= r_hi) & intact
        take = good & (best_p < 0)
        best_p[take] = pc[take]
        best_n[take] = k
        best_ret[take] = ret[take]
        best_ratio[take] = ratio[take]
        best_low[take] = pb_low[take]
        best_intact[take] = intact[take]
    return ArmTable(best_p, best_n, best_ret, best_ratio, best_low, best_intact)


def build_tables(fr: V9Frame, cfg: V9Config) -> dict[int, DirectionTables]:
    out: dict[int, DirectionTables] = {}
    for d in (1, -1):
        legs = build_leg_table(fr, d, cfg)
        out[d] = DirectionTables(d, legs, build_arm_table(fr, d, legs, cfg))
    return out
