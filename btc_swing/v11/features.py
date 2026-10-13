"""V11 feature frame (`v11-feat-1`, design section 3): every value at 1m row i uses rows <= i, completed
5m / 15m bars (aligned to the day grid) and Binance proxy rows available at or before the 1m close.
Vectorised; the same function serves the historical run and the live rolling window."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray

from btc_swing.v6.features import (
    _agg,
    _last_at_many,
    _series,
    ema,
    lagged,
    mask,
    rolling_max_prev,
    rolling_min_prev,
    rolling_sum,
    wilder_atr,
    window_valid,
)
from btc_swing.v11.config import V11Config

F = NDArray[np.float64]
I = NDArray[np.int64]  # noqa: E741
B = NDArray[np.bool_]
MS_1M = 60_000
DAY_MS = 86_400_000
SLOTS = 1440


@dataclass
class V11Frame:
    t_open: I
    valid: B
    c: dict[str, F] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.t_open)

    def row(self, i: int) -> dict[str, float]:
        return {k: float(v[i]) for k, v in self.c.items()}


# ----------------------------------------------------------------------------- helpers
def _grid(rows: pl.DataFrame) -> tuple[I, pl.DataFrame]:
    b = rows.sort("open_time_ms")
    t0 = (int(b["open_time_ms"][0]) // DAY_MS) * DAY_MS
    t1 = ((int(b["open_time_ms"][-1]) // DAY_MS) + 1) * DAY_MS
    grid = np.arange(t0, t1, MS_1M, dtype=np.int64)
    return grid, pl.DataFrame({"open_time_ms": grid}).join(b, on="open_time_ms", how="left")


def _roll_mean_prev(x: F, w: int, mp: int) -> F:
    return np.asarray(
        _series(x).shift(1).rolling_mean(w, min_samples=mp).to_numpy(), dtype=np.float64
    )


def _roll_q_prev(x: F, q: float, w: int, mp: int) -> F:
    return np.asarray(
        _series(x).shift(1).rolling_quantile(q, window_size=w, min_samples=mp).to_numpy(),
        dtype=np.float64,
    )


def _roll_mean(x: F, w: int) -> F:
    return np.asarray(_series(x).rolling_mean(w, min_samples=w).to_numpy(), dtype=np.float64)


def _roll_std(x: F, w: int) -> F:
    return np.asarray(_series(x).rolling_std(w, min_samples=w, ddof=0).to_numpy(), dtype=np.float64)


def _roll_max(x: F, w: int) -> F:
    return np.asarray(_series(x).rolling_max(w, min_samples=w).to_numpy(), dtype=np.float64)


def _roll_min(x: F, w: int) -> F:
    return np.asarray(_series(x).rolling_min(w, min_samples=w).to_numpy(), dtype=np.float64)


def _bars(x: F, m: int, how: str) -> F:
    nb = len(x) // m
    return _agg(x[: nb * m], m, how)


def _at(arr: F, n: int, m: int) -> F:
    """Value of the last COMPLETED m-minute bar at each 1m row (NaN before the first)."""
    j = (np.arange(n) + 1) // m - 1
    out = np.full(n, np.nan)
    ok = j >= 0
    out[ok] = arr[j[ok]]
    return out


def rsi(close: F, period: int) -> F:
    d = np.diff(close, prepend=np.nan)
    up = np.where(d > 0, d, 0.0)
    dn = np.where(d < 0, -d, 0.0)
    out = np.full(len(close), np.nan)
    au = ad = math.nan
    cnt = 0
    for i in range(1, len(close)):
        if math.isnan(d[i]):
            continue
        if cnt < period:
            au = up[i] if math.isnan(au) else au + up[i]
            ad = dn[i] if math.isnan(ad) else ad + dn[i]
            cnt += 1
            if cnt == period:
                au, ad = au / period, ad / period
        else:
            au = (au * (period - 1) + up[i]) / period
            ad = (ad * (period - 1) + dn[i]) / period
        if cnt >= period:
            out[i] = 100.0 if ad == 0 else 100.0 - 100.0 / (1.0 + au / ad)
    return out


def adx(high: F, low: F, close: F, period: int) -> F:
    n = len(close)
    out = np.full(n, np.nan)
    pc = lagged(close, 1)
    tr = np.maximum.reduce([high - low, np.abs(high - pc), np.abs(low - pc)])
    up_m = high - lagged(high, 1)
    dn_m = lagged(low, 1) - low
    pdm = np.where((up_m > dn_m) & (up_m > 0), up_m, 0.0)
    ndm = np.where((dn_m > up_m) & (dn_m > 0), dn_m, 0.0)
    s_tr = s_p = s_n = math.nan
    adx_v = math.nan
    cnt = 0
    dxs: list[float] = []
    for i in range(1, n):
        if math.isnan(tr[i]):
            continue
        if cnt < period:
            s_tr = tr[i] if math.isnan(s_tr) else s_tr + tr[i]
            s_p = pdm[i] if math.isnan(s_p) else s_p + pdm[i]
            s_n = ndm[i] if math.isnan(s_n) else s_n + ndm[i]
            cnt += 1
            if cnt < period:
                continue
        else:
            s_tr = s_tr - s_tr / period + tr[i]
            s_p = s_p - s_p / period + pdm[i]
            s_n = s_n - s_n / period + ndm[i]
        if s_tr <= 0:
            continue
        pdi, ndi = 100.0 * s_p / s_tr, 100.0 * s_n / s_tr
        dx = 100.0 * abs(pdi - ndi) / (pdi + ndi) if pdi + ndi > 0 else 0.0
        if math.isnan(adx_v):
            dxs.append(dx)
            if len(dxs) == period:
                adx_v = float(np.mean(dxs))
        else:
            adx_v = (adx_v * (period - 1) + dx) / period
        out[i] = adx_v
    return out


def swing_sequence(high: F, low: F, k: int) -> tuple[F, F, F, F]:
    """Last two confirmed swing highs / lows (prev_sh, last_sh, prev_sl, last_sl) per bar."""
    n = len(high)
    psh = np.full(n, np.nan)
    lsh = np.full(n, np.nan)
    psl = np.full(n, np.nan)
    lsl = np.full(n, np.nan)
    a_sh = b_sh = a_sl = b_sl = math.nan
    for j in range(n):
        p = j - k
        if p - k >= 0:
            hp, lp = high[p], low[p]
            wh = np.concatenate([high[p - k : p], high[p + 1 : p + k + 1]])
            wl = np.concatenate([low[p - k : p], low[p + 1 : p + k + 1]])
            if not np.isnan(hp) and not np.isnan(wh).any() and np.all(wh < hp):
                a_sh, b_sh = b_sh, hp
            if not np.isnan(lp) and not np.isnan(wl).any() and np.all(wl > lp):
                a_sl, b_sl = b_sl, lp
        psh[j], lsh[j], psl[j], lsl[j] = a_sh, b_sh, a_sl, b_sl
    return psh, lsh, psl, lsl


def slot_median_prev_days(x: F, slots: int, window_days: int, min_days: int) -> F:
    n_days = len(x) // slots
    m = x.reshape(n_days, slots)
    out = np.full_like(m, np.nan)
    if n_days > window_days:
        w = np.lib.stride_tricks.sliding_window_view(m, window_days, axis=0)
        cnt = (~np.isnan(w)).sum(axis=2)
        with np.errstate(all="ignore"):
            med = np.nanmedian(w, axis=2)
        out[window_days:] = np.where(cnt >= min_days, med, np.nan)[:-1]
    return out.reshape(-1)


def _leg_pullback(h: F, lo: F, vol: F, atr: F, valid: B, cfg: V11Config) -> tuple[B, F, F]:
    """Family A structure on 5m bars (mirrored series for shorts): at bar i, is there a leg of at most
    L bars (origin = lowest low, peak = highest high) with size >= X ATR, followed by a 2-6 bar
    pullback (no bar above the peak) retracing 20-60% with volume contraction? Returns (ok, pullback
    low, retrace)."""
    fa = cfg.families.a
    n = len(h)
    L = fa.impulse_max_leg_bars  # noqa: N806
    hn = np.where(np.isnan(h), -np.inf, h)
    ln = np.where(np.isnan(lo), np.inf, lo)
    vn = np.nan_to_num(vol)
    cs_v = np.concatenate([[0.0], np.cumsum(vn)])
    ok = np.zeros(n, dtype=bool)
    pb_low = np.full(n, np.nan)
    retrace = np.full(n, np.nan)
    # leg per peak bar p: h[p] >= max h[p-L..p-1]; origin = argmin l[p-L..p]
    hw = np.full((n, L + 1), -np.inf)
    lw = np.full((n, L + 1), np.inf)
    if n >= L + 1:
        hw[L:] = np.lib.stride_tricks.sliding_window_view(hn, L + 1)
        lw[L:] = np.lib.stride_tricks.sliding_window_view(ln, L + 1)
    peak_ok = h >= hw[:, :-1].max(axis=1)
    rel = lw.argmin(axis=1)
    origin = np.arange(n) - L + rel
    origin_px = np.where(rel < L, lw[np.arange(n), rel], np.nan)
    size = h - origin_px
    vw = np.zeros(n, dtype=bool)
    if n >= L + 1:
        vw[L:] = (
            np.lib.stride_tricks.sliding_window_view(valid.astype(np.int8), L + 1).min(axis=1) == 1
        )
    leg_ok = (
        peak_ok
        & (rel < L)
        & vw
        & (size > 0)
        & np.isfinite(atr)
        & (atr > 0)
        & (size >= fa.impulse_size_atr * atr)
    )
    o = np.clip(origin, 0, n - 1)
    p_idx = np.arange(n)
    leg_len = np.maximum(p_idx - o, 1)
    leg_vol = (cs_v[p_idx + 1] - cs_v[o + 1]) / leg_len
    for k in range(fa.pullback_max_bars, fa.pullback_min_bars - 1, -1):  # earliest valid peak wins
        p = p_idx - k
        okp = p >= 0
        pc = np.clip(p, 0, n - 1)
        hw_k = np.full((n, k), np.inf)
        lw_k = np.full((n, k), np.nan)
        if n >= k:
            hw_k[k - 1 :] = np.lib.stride_tricks.sliding_window_view(hn, k)
            lw_k[k - 1 :] = np.lib.stride_tricks.sliding_window_view(ln, k)
        above = hw_k.max(axis=1) > h[pc]
        low_k = lw_k.min(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            ret = (h[pc] - low_k) / size[pc]
            ratio = ((cs_v[p_idx + 1] - cs_v[pc + 1]) / k) / leg_vol[pc]
        good = (
            okp
            & leg_ok[pc]
            & ~above
            & np.isfinite(low_k)
            & (low_k > origin_px[pc])
            & (ret >= fa.retrace_min)
            & (ret <= fa.retrace_max)
            & (ratio <= fa.pullback_volume_ratio_max)
        )
        take = good & ~ok
        ok[take] = True
        pb_low[take] = low_k[take]
        retrace[take] = ret[take]
    return ok, pb_low, retrace


# ----------------------------------------------------------------------------- frame
def build_frame_v11(
    rows: pl.DataFrame,
    cfg: V11Config,
    metrics: pl.DataFrame | None = None,
    funding: pl.DataFrame | None = None,
) -> V11Frame:
    fc = cfg.features
    grid, g = _grid(rows)
    n = len(grid)
    n_days = n // SLOTS
    t_close = grid + MS_1M

    def col(name: str) -> F:
        return g[name].cast(pl.Float64).to_numpy().astype(np.float64)

    o, h, lo, c, v = col("open"), col("high"), col("low"), col("close"), col("volume")
    tc = col("n_trades")
    buy, sell = col("buy_qty"), col("sell_qty")
    valid = np.asarray(np.isfinite(c) & np.isfinite(h) & np.isfinite(lo) & (tc > 0), dtype=bool)
    nanv = np.where(valid, 1.0, np.nan)
    o, h, lo, c, v, tc, buy, sell = (x * nanv for x in (o, h, lo, c, v, tc, buy, sell))
    X: dict[str, F] = {  # noqa: N806
        "open": o,
        "high": h,
        "low": lo,
        "close": c,
        "volume": v,
        "buy": buy,
        "sell": sell,
        "tc": tc,
    }
    # ---- 5m / 15m / 1h bars
    v5 = np.asarray(_bars(valid.astype(np.float64), 5, "min") == 1.0)
    nan5 = np.where(v5, 1.0, np.nan)
    h5, l5, c5, vol5 = (
        _bars(h, 5, "max") * nan5,
        _bars(lo, 5, "min") * nan5,
        _bars(c, 5, "last") * nan5,
        _bars(v, 5, "sum") * nan5,
    )
    v15 = np.asarray(_bars(v5.astype(np.float64), 3, "min") == 1.0)
    nan15 = np.where(v15, 1.0, np.nan)
    h15, l15 = _bars(h5, 3, "max") * nan15, _bars(l5, 3, "min") * nan15
    v60 = np.asarray(_bars(v5.astype(np.float64), 12, "min") == 1.0)
    h60b, l60b, c60b = _bars(h5, 12, "max"), _bars(l5, 12, "min"), _bars(c5, 12, "last")
    atr5 = wilder_atr(h5, l5, c5, v5, fc.atr_period)
    X["atr5"] = _at(atr5, n, 5)
    X["atr1"] = wilder_atr(h, lo, c, valid, fc.atr_period)
    X["atr1h"] = _at(wilder_atr(h60b, l60b, c60b, v60, fc.atr_period), n, 60)
    a5 = X["atr5"]
    with np.errstate(invalid="ignore", divide="ignore"):
        # ---- A trend / regime
        pv = np.nan_to_num(c * v).reshape(n_days, SLOTS)
        vv = np.nan_to_num(v).reshape(n_days, SLOTS)
        cs_pv, cs_v = np.cumsum(pv, axis=1), np.cumsum(vv, axis=1)
        vwap = np.where(cs_v > 0, cs_pv / cs_v, np.nan).reshape(-1)
        minute = (grid % DAY_MS) // MS_1M
        vwap[minute < fc.vwap_min_minutes] = np.nan
        X["vwap"] = vwap
        X["dist_vwap"] = (c - vwap) / a5
        X["vwap_slope"] = (vwap - lagged(vwap, 15)) / a5
        c5n = np.where(v5, c5, np.nan)
        e9, e21, e50 = ema(c5n, fc.ema_fast), ema(c5n, fc.ema_mid), ema(c5n, fc.ema_slow)
        X["ema9"], X["ema21"], X["ema50"] = _at(e9, n, 5), _at(e21, n, 5), _at(e50, n, 5)
        X["ema21_slope"] = _at((e21 - lagged(e21, 3)), n, 5) / a5
        psh, lsh, psl, lsl = swing_sequence(h15, l15, fc.swing_k)
        st = np.where(
            (lsh > psh) & (lsl > psl), 1.0, np.where((lsh < psh) & (lsl < psl), -1.0, 0.0)
        )
        st[np.isnan(psh) | np.isnan(psl) | np.isnan(lsh) | np.isnan(lsl)] = np.nan
        X["struct15"] = _at(st, n, 15)
        X["swing_hi15"], X["swing_lo15"] = _at(lsh, n, 15), _at(lsl, n, 15)
        # ---- B momentum
        r7, r14 = rsi(c5n, fc.rsi_fast), rsi(c5n, fc.rsi_slow)
        X["rsi7"], X["rsi14"] = _at(r7, n, 5), _at(r14, n, 5)
        X["rsi7_prev"] = _at(lagged(r7, 1), n, 5)
        X["adx14"] = _at(
            adx(np.where(v5, h5, np.nan), np.where(v5, l5, np.nan), c5n, fc.adx_period), n, 5
        )
        X["roc3"] = _at((c5n - lagged(c5n, 3)), n, 5) / a5
        ret5 = c5n / lagged(c5n, 1) - 1.0
        X["accel5"] = _at(ret5 - lagged(ret5, 1), n, 5)
        ret1 = c / lagged(c, 1) - 1.0
        X["accel1"] = ret1 - lagged(ret1, 1)
        # ---- C volatility
        mid, sd = _roll_mean(c5n, fc.bb_period), _roll_std(c5n, fc.bb_period)
        up_bb, lo_bb = mid + fc.bb_k * sd, mid - fc.bb_k * sd
        up_kc, lo_kc = mid + fc.kc_k * atr5, mid - fc.kc_k * atr5
        sq = (up_bb < up_kc) & (lo_bb > lo_kc)
        sq_f = sq.astype(np.float64)
        sq_recent = np.asarray(
            _series(lagged(sq_f, 1))
            .rolling_max(fc.squeeze_lookback_bars, min_samples=1)
            .to_numpy(),
            dtype=np.float64,
        )
        release = (sq_recent == 1.0) & ~sq
        bbw = (up_bb - lo_bb) / mid
        X["bb_mid"], X["bb_sd"] = _at(mid, n, 5), _at(sd, n, 5)
        X["bbw"] = _at(bbw, n, 5)
        X["bbw_rising"] = _at((bbw > lagged(bbw, 1)).astype(np.float64), n, 5)
        X["squeeze_on"] = _at(sq_f, n, 5)
        X["squeeze_release"] = _at(release.astype(np.float64), n, 5)
        hist5 = fc.atr_pct_history_days * 288
        qs = {q: _roll_q_prev(atr5, q, hist5, hist5 // 3) for q in (0.2, 0.3, 1 / 3, 2 / 3, 0.8)}
        X["atr_band"] = _at(((atr5 >= qs[0.2]) & (atr5 <= qs[0.8])).astype(np.float64), n, 5)
        atr_low = (atr5 <= qs[0.3]).astype(np.float64)
        X["atr_low"] = _at(atr_low, n, 5)
        X["atr_low_recent"] = _at(
            np.asarray(
                _series(atr_low).rolling_max(fc.squeeze_lookback_bars, min_samples=1).to_numpy(),
                dtype=np.float64,
            ),
            n,
            5,
        )
        X["squeeze_release_recent"] = _at(
            np.asarray(
                _series(release.astype(np.float64))
                .rolling_max(fc.squeeze_lookback_bars, min_samples=1)
                .to_numpy(),
                dtype=np.float64,
            ),
            n,
            5,
        )
        vt = np.where(atr5 <= qs[1 / 3], 0.0, np.where(atr5 >= qs[2 / 3], 2.0, 1.0))
        vt[np.isnan(qs[1 / 3]) | np.isnan(atr5)] = np.nan
        X["vol_tercile"] = _at(vt, n, 5)
        X["bb_dev"] = (c - X["bb_mid"]) / X["bb_sd"]
        # ---- D volume / flow
        base5 = slot_median_prev_days(
            np.nan_to_num(vol5, nan=np.nan), 288, fc.rvol_baseline_days, fc.rvol_min_days
        )
        rvol5 = np.where(base5 > 0, vol5 / base5, np.nan)
        X["rvol5"] = _at(rvol5, n, 5)
        q90 = _roll_q_prev(
            vol5, 0.9, fc.vol_spike_history_days * 288, fc.vol_spike_history_days * 96
        )
        X["vol_spike"] = _at((vol5 >= q90).astype(np.float64), n, 5)
        delta = buy - sell
        tot = buy + sell
        X["delta1"] = delta
        for w in (5, 15):
            d, s_ = rolling_sum(delta, w), rolling_sum(tot, w)
            X[f"delta{w}"] = d
            X[f"imb{w}"] = np.where(s_ > 0, d / s_, np.nan)
        cum = np.cumsum(np.nan_to_num(delta).reshape(n_days, SLOTS), axis=1).reshape(-1)
        X["cum_delta"] = cum
        X["cum_delta_15ago"] = lagged(cum, 15)
        X["intensity1"] = tc / _roll_mean_prev(
            tc, fc.intensity_baseline_min, fc.intensity_baseline_min
        )
        # ---- E location / structure
        _, sh5, _, sl5 = swing_sequence(
            np.where(v5, h5, np.nan), np.where(v5, l5, np.nan), fc.swing_k
        )
        X["swing_hi5"], X["swing_lo5"] = _at(sh5, n, 5), _at(sl5, n, 5)
        w60 = fc.range_hi_lo_min
        okw = window_valid(valid, w60 + 1)
        X["hi60"] = mask(rolling_max_prev(h, w60), okw)
        X["lo60"] = mask(rolling_min_prev(lo, w60), okw)
        w15 = fc.struct_range_min
        ok15 = window_valid(valid, w15 + 1)
        X["rng_hi15"] = mask(rolling_max_prev(h, w15), ok15)
        X["rng_lo15"] = mask(rolling_min_prev(lo, w15), ok15)
        k3 = cfg.families.a.trigger_lookback_1m
        X["hi3"] = mask(rolling_max_prev(h, k3), window_valid(valid, k3 + 1))
        X["lo3"] = mask(rolling_min_prev(lo, k3), window_valid(valid, k3 + 1))
        # family A structure (both sides) on 5m bars
        ok_a, pbl, retr = _leg_pullback(
            np.where(v5, h5, np.nan), np.where(v5, l5, np.nan), vol5, atr5, v5, cfg
        )
        ok_as, pbh_m, retr_s = _leg_pullback(
            np.where(v5, -l5, np.nan), np.where(v5, -h5, np.nan), vol5, atr5, v5, cfg
        )
        X["a_struct_long"], X["a_pb_low"], X["a_retrace_long"] = (
            _at(ok_a.astype(np.float64), n, 5),
            _at(pbl, n, 5),
            _at(retr, n, 5),
        )
        X["a_struct_short"], X["a_pb_high"], X["a_retrace_short"] = (
            _at(ok_as.astype(np.float64), n, 5),
            -_at(pbh_m, n, 5),
            _at(retr_s, n, 5),
        )
        # family C sweep memory (1m): sweep within the last `sweep_lookback_min` completed minutes
        fcc = cfg.families.c
        sweep_l = (lo < X["lo60"] - fcc.sweep_depth_atr * a5).astype(np.float64)
        sweep_s = (h > X["hi60"] + fcc.sweep_depth_atr * a5).astype(np.float64)
        X["sweep_long_recent"] = np.asarray(
            _series(lagged(sweep_l, 1))
            .rolling_max(fcc.sweep_lookback_min, min_samples=1)
            .to_numpy(),
            dtype=np.float64,
        )
        X["sweep_short_recent"] = np.asarray(
            _series(lagged(sweep_s, 1))
            .rolling_max(fcc.sweep_lookback_min, min_samples=1)
            .to_numpy(),
            dtype=np.float64,
        )
        X["sweep_low"] = mask(
            rolling_min_prev(lo, fcc.sweep_lookback_min),
            window_valid(valid, fcc.sweep_lookback_min + 1),
        )
        X["sweep_high"] = mask(
            rolling_max_prev(h, fcc.sweep_lookback_min),
            window_valid(valid, fcc.sweep_lookback_min + 1),
        )
        # ---- F derivatives (Binance PIT proxy; availability = time + latency)
        lat = fc.binance_latency_s * 1000
        if metrics is not None and not metrics.is_empty():
            m_ = metrics.sort("time_ms")
            oi = _last_at_many(
                m_["time_ms"].to_numpy().astype(np.int64) + lat,
                m_["open_interest"].to_numpy().astype(np.float64),
                t_close,
            )
            oi[oi <= 0] = np.nan
        else:
            oi = np.full(n, np.nan)
        X["oi"] = oi
        X["oi_chg_1h"] = oi / lagged(oi, 60) - 1.0
        X["oi_chg_15m"] = oi / lagged(oi, 15) - 1.0
        if funding is not None and not funding.is_empty():
            f_ = funding.sort("time_ms")
            X["funding"] = _last_at_many(
                f_["time_ms"].to_numpy().astype(np.int64) + lat,
                f_["funding_rate"].to_numpy().astype(np.float64),
                t_close,
            )
        else:
            X["funding"] = np.full(n, np.nan)
        X["ret_1h"] = c / lagged(c, 60) - 1.0
        # ---- calendar
        X["hour"] = ((grid % DAY_MS) // 3_600_000).astype(np.float64)
        X["day"] = (grid // DAY_MS).astype(np.float64)
    fr = V11Frame(grid, valid, X)
    fr.meta = {
        "rows": n,
        "days": n_days,
        "valid_rows": int(valid.sum()),
        "feature_version": "v11-feat-1",
        "first_open_ms": int(grid[0]),
        "last_close_ms": int(t_close[-1]),
    }
    return fr
