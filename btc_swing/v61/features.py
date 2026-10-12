"""V6.1 PIT feature frame (`v6.1-feat-1`): Bybit-native 5m rows (primary) or Binance 5m rows
(robustness) through one normaliser, V5.1 data-quality rules, completed higher-timeframe bars,
same-time-of-day baselines from previous days only, LOCATION features (section 6 of the design)
and FLOW confirmation features (section 7). Helpers are reused read-only from the frozen V6 module."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import polars as pl
from numpy.typing import NDArray

from btc_swing.v6.features import (
    DAY_MS,
    MS_5M,
    SLOTS,
    UNCLEAR,
    V6Frame,
    _agg,
    _exact_at,
    _last_at_many,
    _series,
    ema,
    lagged,
    mask,
    rolling_max_prev,
    rolling_min_prev,
    rolling_sum,
    rolling_z,
    slot_median_prev_days,
    swing_points,
    wilder_atr,
    window_valid,
)
from btc_swing.v61.config import V61Config

F = NDArray[np.float64]
I = NDArray[np.int64]  # noqa: E741
UNIFIED = [
    "open_time_ms", "open", "high", "low", "close", "volume", "trades", "buy_qty", "sell_qty",
    "big_buy_qty", "big_sell_qty", "avg_trade_qty",
]  # fmt: skip


@dataclass
class V61Inputs:
    bars: pl.DataFrame  # unified 5m schema (see `normalise_bybit` / `normalise_binance`)
    metrics: pl.DataFrame  # Binance 5-min metrics (time_ms, open_interest)
    funding: pl.DataFrame  # (time_ms, funding_rate)
    premium: pl.DataFrame  # premium index 5m klines (open_time_ms, close)
    source: str = "bybit"
    hashes: dict[str, str] = field(default_factory=dict)


def normalise_bybit(rows: pl.DataFrame) -> pl.DataFrame:
    """Rows of `btc_swing.v61.ingest` -> unified schema."""
    if rows.is_empty():
        return pl.DataFrame(schema={c: pl.Float64 for c in UNIFIED})
    return rows.select(
        pl.col("open_time_ms"),
        pl.col("open"),
        pl.col("high"),
        pl.col("low"),
        pl.col("close"),
        pl.col("volume"),
        pl.col("n_trades").cast(pl.Float64).alias("trades"),
        pl.col("buy_qty"),
        pl.col("sell_qty"),
        pl.col("big_buy_qty"),
        pl.col("big_sell_qty"),
        pl.col("avg_trade_qty"),
    ).sort("open_time_ms")


def normalise_binance(klines: pl.DataFrame, flow: pl.DataFrame) -> pl.DataFrame:
    """Binance 5m klines + aggTrades flow aggregates -> unified schema (robustness source)."""
    k = klines.select(
        "open_time_ms", "open", "high", "low", "close", "volume",
        pl.col("trades").cast(pl.Float64), "taker_buy_volume",
    ).sort("open_time_ms")  # fmt: skip
    if flow.is_empty():
        return k.with_columns(
            pl.col("taker_buy_volume").alias("buy_qty"),
            (pl.col("volume") - pl.col("taker_buy_volume")).alias("sell_qty"),
            pl.lit(math.nan).alias("big_buy_qty"),
            pl.lit(math.nan).alias("big_sell_qty"),
            (pl.col("volume") / pl.col("trades")).alias("avg_trade_qty"),
        ).select(UNIFIED)
    f = flow.select(
        "open_time_ms", "buy_qty", "sell_qty", "big_buy_qty", "big_sell_qty", "n_trades"
    )
    j = k.join(f, on="open_time_ms", how="left")
    return j.with_columns(
        pl.coalesce(pl.col("buy_qty"), pl.col("taker_buy_volume")).alias("buy_qty"),
        pl.coalesce(pl.col("sell_qty"), pl.col("volume") - pl.col("taker_buy_volume")).alias(
            "sell_qty"
        ),
        (pl.col("volume") / pl.col("trades")).alias("avg_trade_qty"),
    ).select(UNIFIED)


def _grid(bars: pl.DataFrame) -> tuple[I, pl.DataFrame]:
    b = bars.sort("open_time_ms")
    t0 = (int(b["open_time_ms"][0]) // DAY_MS) * DAY_MS
    t1 = ((int(b["open_time_ms"][-1]) // DAY_MS) + 1) * DAY_MS
    grid = np.arange(t0, t1, MS_5M, dtype=np.int64)
    g = pl.DataFrame({"open_time_ms": grid}).join(b, on="open_time_ms", how="left")
    return grid, g


def _touches(ext: F, ref: F, atr: F, tol: float, window: int, above: bool) -> F:
    """Number of the previous `window` bars whose extreme lies within tol*ATR of the reference."""
    n = len(ext)
    out = np.full(n, np.nan)
    if n <= window:
        return out
    w = np.lib.stride_tricks.sliding_window_view(ext, window)  # row i -> ext[i : i + window]
    # for bar k the previous window is ext[k-window : k] -> w[k-window]
    r = ref[window:]
    a = atr[window:]
    ww = w[:-1]
    cmp = (ww >= (r - tol * a)[:, None]) if above else (ww <= (r + tol * a)[:, None])
    cnt = np.where(np.isnan(r) | np.isnan(a), np.nan, cmp.sum(axis=1).astype(np.float64))
    out[window:] = cnt
    return out


def build_frame_v61(inp: V61Inputs, cfg: V61Config) -> V6Frame:
    fc = cfg.features
    grid, g = _grid(inp.bars)
    n = len(grid)
    n_days = n // SLOTS
    t_open = grid
    t = grid + MS_5M

    def col(name: str) -> F:
        return g[name].cast(pl.Float64).to_numpy().astype(np.float64)

    o, h, lo, c, vol = col("open"), col("high"), col("low"), col("close"), col("volume")
    trades = col("trades")
    valid = np.asarray(
        ~np.isnan(c) & ~np.isnan(h) & ~np.isnan(lo) & (vol > 0) & (trades > 0), dtype=bool
    )
    nanv = np.where(valid, 1.0, np.nan)
    o, h, lo, c, vol = o * nanv, h * nanv, lo * nanv, c * nanv, vol * nanv
    cols: dict[str, F] = {"open": o, "high": h, "low": lo, "close": c, "volume": vol}
    cols["trades"] = trades * nanv
    for kk, name in ((1, "ret_5m"), (3, "ret_15m"), (12, "ret_1h"), (48, "ret_4h")):
        cols[name] = mask(c / lagged(c, kk) - 1.0, window_valid(valid, kk + 1))
    cols["atr5"] = wilder_atr(h, lo, c, valid, fc.atr_period)
    for w, name in ((12, "1h"), (48, "4h"), (288, "24h")):
        okw = window_valid(valid, w + 1)
        cols[f"hi_{name}"] = mask(rolling_max_prev(h, w), okw)
        cols[f"lo_{name}"] = mask(rolling_min_prev(lo, w), okw)
    sh5, sl5 = swing_points(h, lo, fc.swing_k)
    cols["swing_high_5m"], cols["swing_low_5m"] = sh5, sl5
    # ---- completed higher-timeframe bars (grid is day-aligned)
    for m, tag in ((3, "15m"), (12, "1h"), (48, "4h")):
        nb = n // m
        hh = _agg(h[: nb * m], m, "max")
        ll = _agg(lo[: nb * m], m, "min")
        cc = _agg(c[: nb * m], m, "last")
        vb = np.asarray(_agg(valid[: nb * m].astype(np.float64), m, "min") == 1.0)
        atr_b = wilder_atr(hh, ll, cc, vb, fc.atr_period)
        ef = ema(np.where(vb, cc, np.nan), fc.ema_fast)
        es = ema(np.where(vb, cc, np.nan), fc.ema_slow)
        j = (np.arange(n) + 1) // m - 1
        okj = j >= 0
        jj = np.maximum(j, 0)

        def at(arr: F, okj: NDArray[np.bool_] = okj, jj: I = jj) -> F:
            return np.where(okj, arr[jj], np.nan)

        cols[f"atr{tag}"] = at(atr_b)
        cols[f"ema_fast_{tag}"] = at(ef)
        cols[f"ema_slow_{tag}"] = at(es)
        cols[f"close_{tag}"] = at(cc)
        cols[f"high_{tag}"] = at(hh)
        cols[f"low_{tag}"] = at(ll)
        tr = np.zeros(nb)
        tr[(ef > es) & (cc > es)] = 1.0
        tr[(ef < es) & (cc < es)] = -1.0
        tr[np.isnan(ef) | np.isnan(es) | np.isnan(cc)] = np.nan
        cols[f"trend_{tag}"] = at(tr)
        if tag == "15m":
            shb, slb = swing_points(hh, ll, fc.swing_k)
            cols["swing_high_15m"], cols["swing_low_15m"] = at(shb), at(slb)
            cols["bar15_complete"] = ((np.arange(n) + 1) % m == 0).astype(np.float64)
        if tag == "1h":
            slope_ok = ef - lagged(ef, fc.trend_slope_bars_1h)
            reg = np.zeros(nb)
            reg[(tr == 1.0) & (slope_ok > 0)] = 1.0
            reg[(tr == -1.0) & (slope_ok < 0)] = -1.0
            reg[np.isnan(tr) | np.isnan(slope_ok)] = np.nan
            cols["regime_1h"] = at(reg)
            # previous COMPLETED 1h bar (the one before the last completed)
            jprev = np.maximum(j - 1, 0)
            okp = j >= 1
            cols["prev1h_high"] = np.where(okp, hh[jprev], np.nan)
            cols["prev1h_low"] = np.where(okp, ll[jprev], np.nan)
    atr15 = cols["atr15m"]
    # ---- realised volatility and terciles (reporting regime)
    lr = np.concatenate([[np.nan], np.diff(np.log(c))])
    lr2 = lr * lr
    for w, name in ((12, "rv_1h"), (288, "rv_24h")):
        m1, m2 = rolling_sum(lr, w) / w, rolling_sum(lr2, w) / w
        cols[name] = np.sqrt(np.maximum(m2 - m1 * m1, 0.0)) * math.sqrt(288.0)
    rv = _series(cols["rv_24h"]).shift(1)
    wq = fc.rv_rank_days * SLOTS
    q33 = np.asarray(
        rv.rolling_quantile(0.3333, window_size=wq, min_samples=wq // 3).to_numpy(),
        dtype=np.float64,
    )
    q67 = np.asarray(
        rv.rolling_quantile(0.6667, window_size=wq, min_samples=wq // 3).to_numpy(),
        dtype=np.float64,
    )
    vt = np.full(n, np.nan)
    ok = ~np.isnan(cols["rv_24h"]) & ~np.isnan(q33) & ~np.isnan(q67)
    vt[ok] = 1.0
    vt[ok & (cols["rv_24h"] <= q33)] = 0.0
    vt[ok & (cols["rv_24h"] >= q67)] = 2.0
    cols["vol_tercile"] = vt
    # ---- session VWAP and session range (UTC day, including the current bar)
    tp = (h + lo + c) / 3.0
    pv = np.where(valid, tp * vol, 0.0).reshape(n_days, SLOTS)
    vv = np.where(valid, vol, 0.0).reshape(n_days, SLOTS)
    cpv, cvv = np.cumsum(pv, axis=1), np.cumsum(vv, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        cols["vwap_day"] = np.where(cvv > 0, cpv / cvv, np.nan).reshape(-1)
    hd = np.where(valid, h, -np.inf).reshape(n_days, SLOTS)
    ld = np.where(valid, lo, np.inf).reshape(n_days, SLOTS)
    day_hi = np.maximum.accumulate(hd, axis=1)
    day_lo = np.minimum.accumulate(ld, axis=1)
    day_hi = np.where(np.isfinite(day_hi), day_hi, np.nan).reshape(-1)
    day_lo = np.where(np.isfinite(day_lo), day_lo, np.nan).reshape(-1)
    cols["day_hi"], cols["day_lo"] = day_hi, day_lo
    full_hi = np.nanmax(np.where(valid, h, np.nan).reshape(n_days, SLOTS), axis=1)
    full_lo = np.nanmin(np.where(valid, lo, np.nan).reshape(n_days, SLOTS), axis=1)
    prev_hi = np.concatenate([[np.nan], full_hi[:-1]])
    prev_lo = np.concatenate([[np.nan], full_lo[:-1]])
    cols["prev_day_hi"] = np.repeat(prev_hi, SLOTS)
    cols["prev_day_lo"] = np.repeat(prev_lo, SLOTS)
    # ---- same-time-of-day baselines
    base_vol = slot_median_prev_days(vol, n_days, fc.rel_vol_days, fc.min_baseline_days)
    cols["base_vol_5m"] = base_vol
    avg_tr = col("avg_trade_qty") * nanv
    base_avg = slot_median_prev_days(avg_tr, n_days, fc.rel_vol_days, fc.min_baseline_days)
    with np.errstate(invalid="ignore", divide="ignore"):
        cols["rel_vol_5m"] = np.where(base_vol > 0, vol / base_vol, np.nan)
        base15 = rolling_sum(base_vol, 3)
        cols["vol_15m"] = rolling_sum(vol, 3)
        cols["rel_vol_15m"] = np.where(base15 > 0, cols["vol_15m"] / base15, np.nan)
        cols["avg_trade_qty"] = avg_tr
        cols["avg_trade_rel"] = np.where(base_avg > 0, avg_tr / base_avg, np.nan)
    # ---- flow (aggressor side)
    buy, sell = col("buy_qty") * nanv, col("sell_qty") * nanv
    cols["buy_qty"], cols["sell_qty"] = buy, sell
    delta, tot = buy - sell, buy + sell
    with np.errstate(invalid="ignore", divide="ignore"):
        cols["delta_5m"] = delta
        cols["imbalance_5m"] = np.where(tot > 0, delta / tot, np.nan)
        for w, tag in ((3, "15m"), (12, "1h")):
            d, s_ = rolling_sum(delta, w), rolling_sum(tot, w)
            cols[f"delta_{tag}"] = d
            cols[f"imbalance_{tag}"] = np.where(s_ > 0, d / s_, np.nan)
            cols[f"cvd_slope_{tag}"] = d / w
        cols["delta_accel"] = delta - lagged(delta, 1)
        cols["flow_flip"] = (np.sign(delta) != np.sign(lagged(cols["delta_15m"], 1))).astype(float)
        bb = rolling_sum(col("big_buy_qty") * nanv, 3)
        bs = rolling_sum(col("big_sell_qty") * nanv, 3)
        cols["big_imb_15m"] = np.where(bb + bs > 0, (bb - bs) / (bb + bs), np.nan)
    # ---- Binance open interest / funding / premium (confirmation only; venue mismatch documented)
    met = inp.metrics
    if not met.is_empty():
        m_ = met.sort("time_ms")
        oi = _last_at_many(
            m_["time_ms"].to_numpy().astype(np.int64),
            m_["open_interest"].to_numpy().astype(np.float64),
            t,
        )
        oi[oi <= 0] = np.nan
    else:
        oi = np.full(n, np.nan)
    cols["oi"] = oi
    for kk, name in ((3, "oi_chg_15m"), (12, "oi_chg_1h")):
        cols[name] = oi / lagged(oi, kk) - 1.0
    fu = inp.funding
    cols["fund"] = (
        _last_at_many(
            fu.sort("time_ms")["time_ms"].to_numpy().astype(np.int64),
            fu.sort("time_ms")["funding_rate"].to_numpy().astype(np.float64),
            t,
        )
        if not fu.is_empty()
        else np.full(n, np.nan)
    )
    pr = inp.premium
    prem = (
        _exact_at(
            pr.sort("open_time_ms")["open_time_ms"].to_numpy().astype(np.int64),
            pr.sort("open_time_ms")["close"].to_numpy().astype(np.float64),
            t_open,
        )
        if not pr.is_empty()
        else np.full(n, np.nan)
    )
    cols["prem"] = prem
    cols["prem_1h"] = rolling_sum(prem, 12) / 12.0
    cols["prem_z"] = rolling_z(cols["prem_1h"], fc.prem_z_window, fc.prem_z_min_periods)
    # ---- LOCATION features (ATR15 units)
    with np.errstate(invalid="ignore", divide="ignore"):
        cols["dist_vwap_atr"] = (c - cols["vwap_day"]) / atr15
        cols["dist_ema21_15m_atr"] = (c - cols["ema_fast_15m"]) / atr15
        cols["dist_ema21_1h_atr"] = (c - cols["ema_fast_1h"]) / atr15
        cols["dist_prev1h_hi_atr"] = (cols["prev1h_high"] - c) / atr15
        cols["dist_prev1h_lo_atr"] = (c - cols["prev1h_low"]) / atr15
        rng4 = cols["hi_4h"] - cols["lo_4h"]
        cols["range_4h_atr"] = rng4 / atr15
        cols["range_pos_4h"] = np.where(rng4 > 0, (c - cols["lo_4h"]) / rng4, np.nan)
        rng24 = cols["hi_24h"] - cols["lo_24h"]
        cols["range_pos_24h"] = np.where(rng24 > 0, (c - cols["lo_24h"]) / rng24, np.nan)
        hi6 = pl.Series(h).rolling_max(6, min_samples=6).to_numpy().astype(np.float64)
        lo6 = pl.Series(lo).rolling_min(6, min_samples=6).to_numpy().astype(np.float64)
        cols["compression_6"] = (hi6 - lo6) / atr15
    cols["edge_touches_hi"] = _touches(h, cols["hi_4h"], atr15, fc.edge_touch_atr15, 48, True)
    cols["edge_touches_lo"] = _touches(lo, cols["lo_4h"], atr15, fc.edge_touch_atr15, 48, False)
    # ---- calendar / reporting
    cols["day"] = (t_open // DAY_MS).astype(np.float64)
    cols["slot"] = ((t_open % DAY_MS) // MS_5M).astype(np.float64)
    cols["utc_hour"] = ((t_open % DAY_MS) // 3_600_000).astype(np.float64)
    cols["weekday"] = (((t_open // DAY_MS) + 3) % 7).astype(np.float64)
    cols["session"] = np.floor(cols["utc_hour"] / 8.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        last_c = np.array([_last_finite(r) for r in c.reshape(n_days, SLOTS)])
        drange = np.where(last_c > 0, (full_hi - full_lo) / last_c, np.nan)
    med = _series(drange).shift(1).rolling_median(60, min_samples=30).to_numpy().astype(np.float64)
    active = np.where(~np.isnan(med) & ~np.isnan(drange), (drange >= med).astype(float), np.nan)
    cols["day_range"] = np.repeat(drange, SLOTS)
    cols["active_day"] = np.repeat(active, SLOTS)
    # regime code for reporting/nulls: 1h trend x vol tercile (same coding as V6)
    tr1 = cols["trend_1h"]
    code = np.full(n, UNCLEAR, dtype=np.int64)
    okr = ~np.isnan(tr1) & ~np.isnan(vt)
    code[okr] = ((tr1[okr] + 1).astype(np.int64) * 3 + vt[okr].astype(np.int64)).astype(np.int64)
    fr = V6Frame(t_open, t, valid, cols, code)
    fr.meta = {
        "rows": n,
        "days": n_days,
        "valid_rows": int(valid.sum()),
        "source": inp.source,
        "first_open_ms": int(t_open[0]),
        "last_close_ms": int(t[-1]),
        "feature_version": "v6.1-feat-1",
        "oi_rows_available": int((~np.isnan(oi)).sum()),
        "regime_share": {
            "TREND_UP": float(np.nanmean(cols["regime_1h"] == 1.0)),
            "TREND_DOWN": float(np.nanmean(cols["regime_1h"] == -1.0)),
            "RANGE": float(np.nanmean(cols["regime_1h"] == 0.0)),
        },
    }
    return fr


def _last_finite(r: F) -> float:
    f = r[~np.isnan(r)]
    return float(f[-1]) if len(f) else math.nan
