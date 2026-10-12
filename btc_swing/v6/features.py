"""V6 PIT feature frame at 5-minute granularity (`v6-feat-1`, docs/BTC_V6_ACTIVE_INTRADAY_DESIGN.md §3).

Row k describes the completed 5m bar closing at T_k and uses only information known at T_k:
5m rows with close <= T_k, COMPLETED 15m/1h/4h bars (the last bar whose close <= T_k), aggTrades
5m aggregates of the same bar, metrics/funding/premium rows with time <= T_k, and same-time-of-day
baselines computed from PREVIOUS days only. Data-quality rules (V5.1 lineage): a 5m row is a valid
observation iff it exists, has trades > 0, volume > 0 and a finite close; every rolling feature is
NaN unless its whole lookback window is valid; baselines ignore invalid observations; ATR is NaN
when any true range in its window touches an invalid row. Nothing is interpolated or backfilled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray

from btc_swing.v6.config import V6Config

F = NDArray[np.float64]
I = NDArray[np.int64]  # noqa: E741
MS_5M = 300_000
DAY_MS = 86_400_000
SLOTS = 288


# ----------------------------------------------------------------------------- inputs
@dataclass
class V6Inputs:
    bars: (
        pl.DataFrame
    )  # 5m perp klines (open_time_ms, open, high, low, close, volume, trades, taker_buy_volume)
    flow: pl.DataFrame  # aggTrades 5m aggregates (open_time_ms, buy_qty, sell_qty, big_*_qty)
    metrics: pl.DataFrame  # (time_ms, open_interest)
    funding: pl.DataFrame  # (time_ms, funding_rate)
    premium: pl.DataFrame  # premium index 5m klines (open_time_ms, close)
    hashes: dict[str, str] = field(default_factory=dict)


# ----------------------------------------------------------------------------- helpers
def _series(x: F) -> pl.Series:
    return pl.Series(x.astype(np.float64)).fill_nan(None)


def rolling_sum(x: F, window: int) -> F:
    """Sum over the last `window` rows INCLUDING the current; NaN until full or if any NaN."""
    return (
        pl.Series(x.astype(np.float64))
        .rolling_sum(window, min_samples=window)
        .to_numpy()
        .astype(np.float64)
    )


def rolling_max_prev(x: F, window: int) -> F:
    s = _series(x).shift(1)
    return s.rolling_max(window, min_samples=window).to_numpy().astype(np.float64)


def rolling_min_prev(x: F, window: int) -> F:
    s = _series(x).shift(1)
    return s.rolling_min(window, min_samples=window).to_numpy().astype(np.float64)


def rolling_z(x: F, window: int, min_periods: int) -> F:
    """z[k] = (x[k] - mean(x[k-window:k])) / std(...); NaNs ignored; past rows only."""
    s = _series(x).shift(1)
    mean = s.rolling_mean(window, min_samples=min_periods).to_numpy().astype(np.float64)
    std = s.rolling_std(window, min_samples=min_periods).to_numpy().astype(np.float64)
    out = np.full(len(x), np.nan)
    ok = ~np.isnan(mean) & ~np.isnan(std) & (std > 0) & ~np.isnan(x)
    out[ok] = (x[ok] - mean[ok]) / std[ok]
    return out


def lagged(x: F, k: int) -> F:
    out = np.full(len(x), np.nan)
    if k < len(x):
        out[k:] = x[:-k]
    return out


def window_valid(valid: NDArray[np.bool_], window: int) -> NDArray[np.bool_]:
    """True at k iff rows k-window+1..k are all valid."""
    s = pl.Series(valid.astype(np.float64)).rolling_min(window, min_samples=window).to_numpy()
    return np.asarray(s == 1.0)


def mask(x: F, ok: NDArray[np.bool_]) -> F:
    return np.where(ok, x, np.nan)


def ema(x: F, period: int) -> F:
    """EMA over a series that may contain NaN: NaN inputs leave the state unchanged and give NaN."""
    out = np.full(len(x), np.nan)
    a = 2.0 / (period + 1.0)
    st = math.nan
    cnt = 0
    for i in range(len(x)):
        v = x[i]
        if math.isnan(v):
            continue
        st = v if math.isnan(st) else st + a * (v - st)
        cnt += 1
        if cnt >= period:
            out[i] = st
    return out


def wilder_atr(high: F, low: F, close: F, valid: NDArray[np.bool_], period: int) -> F:
    """Wilder ATR over clean bars only: TR needs the bar and its predecessor valid; the recursion
    skips contaminated bars; the output is NaN whenever any of the last `period` TRs is missing."""
    n = len(close)
    pc = lagged(close, 1)
    tr = np.maximum.reduce([high - low, np.abs(high - pc), np.abs(low - pc)])
    tr_ok = valid & np.concatenate([[False], valid[:-1]]) & ~np.isnan(tr)
    out = np.full(n, np.nan)
    st = math.nan
    cnt = 0
    seed: list[float] = []
    for i in range(n):
        if not tr_ok[i]:
            continue
        if cnt < period:
            seed.append(float(tr[i]))
            cnt += 1
            if cnt == period:
                st = float(np.mean(seed))
        else:
            st = (st * (period - 1) + float(tr[i])) / period
        if cnt >= period:
            out[i] = st
    return mask(out, window_valid(tr_ok, period))


def swing_points(high: F, low: F, k: int) -> tuple[F, F]:
    """Last confirmed swing high / swing low (pivot confirmed k bars later; NaN-safe)."""
    n = len(high)
    sh = np.full(n, np.nan)
    sl = np.full(n, np.nan)
    ch = cl = math.nan
    for j in range(n):
        p = j - k
        if p - k >= 0:
            hp, lp = high[p], low[p]
            wh = np.concatenate([high[p - k : p], high[p + 1 : p + k + 1]])
            wl = np.concatenate([low[p - k : p], low[p + 1 : p + k + 1]])
            if not np.isnan(hp) and not np.isnan(wh).any() and np.all(wh < hp):
                ch = hp
            if not np.isnan(lp) and not np.isnan(wl).any() and np.all(wl > lp):
                cl = lp
        sh[j], sl[j] = ch, cl
    return sh, sl


def _last_at_many(times: I, values: F, t: I) -> F:
    out = np.full(len(t), np.nan)
    if len(times) == 0:
        return out
    pos = np.searchsorted(times, t, side="right") - 1
    ok = pos >= 0
    out[ok] = values[pos[ok]]
    return out


def _exact_at(times: I, values: F, t: I) -> F:
    out = np.full(len(t), np.nan)
    if len(times) == 0:
        return out
    pos = np.searchsorted(times, t)
    posc = np.minimum(pos, len(times) - 1)
    ok = (pos < len(times)) & (times[posc] == t)
    out[ok] = values[posc[ok]]
    return out


def slot_median_prev_days(x: F, n_days: int, window_days: int, min_days: int) -> F:
    """Same-time-of-day median over the previous `window_days` days (NaN ignored, at least
    `min_days` observations); x has length n_days * SLOTS and starts at a UTC day boundary."""
    m = x.reshape(n_days, SLOTS)
    out = np.full_like(m, np.nan)
    if n_days > window_days:
        w = np.lib.stride_tricks.sliding_window_view(m, window_days, axis=0)  # (D-w+1, S, w)
        cnt = (~np.isnan(w)).sum(axis=2)
        med = np.nanmedian(np.where(np.isnan(w), np.nan, w), axis=2)
        med = np.where(cnt >= min_days, med, np.nan)
        out[window_days:] = med[:-1]
    return out.reshape(-1)


def _agg(x: F, m: int, how: str) -> F:
    g = x.reshape(-1, m)
    if how == "first":
        return g[:, 0].copy()
    if how == "last":
        return g[:, -1].copy()
    if how == "max":
        return np.max(g, axis=1)
    if how == "min":
        return np.min(g, axis=1)
    return np.sum(g, axis=1)


# ----------------------------------------------------------------------------- frame
@dataclass
class V6Frame:
    open_ms: I
    close_ms: I
    valid: NDArray[np.bool_]
    cols: dict[str, F] = field(default_factory=dict)
    regime_code: I = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    meta: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.close_ms)

    def idx_at(self, t_ms: int) -> int:
        return int(np.searchsorted(self.close_ms, t_ms, side="right")) - 1

    def v(self, name: str, k: int) -> float:
        return float(self.cols[name][k]) if 0 <= k < len(self.close_ms) else math.nan

    def row(self, k: int, names: list[str] | None = None) -> dict[str, float]:
        cs = names or list(self.cols)
        return {c: float(self.cols[c][k]) for c in cs}

    def to_frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "open_ms": self.open_ms,
                "close_ms": self.close_ms,
                "valid": self.valid,
                **self.cols,
                "regime_code": self.regime_code,
            }
        )


REGIMES = [
    "DOWN_LOWVOL", "DOWN_MIDVOL", "DOWN_HIGHVOL",
    "FLAT_LOWVOL", "FLAT_MIDVOL", "FLAT_HIGHVOL",
    "UP_LOWVOL", "UP_MIDVOL", "UP_HIGHVOL",
    "UNCLEAR",
]  # fmt: skip
UNCLEAR = len(REGIMES) - 1


def regime_name(code: int) -> str:
    return REGIMES[code] if 0 <= code < len(REGIMES) else "UNCLEAR"


def _grid(bars: pl.DataFrame) -> tuple[I, pl.DataFrame]:
    b = bars.sort("open_time_ms")
    t0 = (int(b["open_time_ms"][0]) // DAY_MS) * DAY_MS
    t1 = ((int(b["open_time_ms"][-1]) // DAY_MS) + 1) * DAY_MS
    grid = np.arange(t0, t1, MS_5M, dtype=np.int64)
    g = pl.DataFrame({"open_time_ms": grid}).join(
        b.select(
            "open_time_ms", "open", "high", "low", "close", "volume", "trades", "taker_buy_volume"
        ),
        on="open_time_ms",
        how="left",
    )
    return grid, g


def build_frame(inp: V6Inputs, cfg: V6Config) -> V6Frame:
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
    # ---- returns (window must be valid)
    for kk, name in ((1, "ret_5m"), (3, "ret_15m"), (12, "ret_1h"), (48, "ret_4h")):
        cols[name] = mask(c / lagged(c, kk) - 1.0, window_valid(valid, kk + 1))
    # ---- ATR(5m) and 5m structure
    cols["atr5"] = wilder_atr(h, lo, c, valid, fc.atr_period)
    for w, name in ((12, "1h"), (48, "4h"), (288, "24h")):
        okw = window_valid(valid, w + 1)
        cols[f"hi_{name}"] = mask(rolling_max_prev(h, w), okw)
        cols[f"lo_{name}"] = mask(rolling_min_prev(lo, w), okw)
    sh5, sl5 = swing_points(h, lo, fc.swing_k)
    cols["swing_high_5m"], cols["swing_low_5m"] = sh5, sl5
    # ---- higher timeframes from completed bars (grid is day-aligned, so 3/12/48 rows per bar)
    for m, tag in ((3, "15m"), (12, "1h"), (48, "4h")):
        nb = n // m
        hh, ll, cc = (
            _agg(h[: nb * m], m, "max"),
            _agg(lo[: nb * m], m, "min"),
            _agg(c[: nb * m], m, "last"),
        )
        oo = _agg(o[: nb * m], m, "first")
        vb = np.asarray(_agg(valid[: nb * m].astype(np.float64), m, "min") == 1.0)
        atr_b = wilder_atr(hh, ll, cc, vb, fc.atr_period)
        ef = ema(np.where(vb, cc, np.nan), fc.ema_fast)
        es = ema(np.where(vb, cc, np.nan), fc.ema_slow)
        j = (np.arange(n) + 1) // m - 1  # last completed bar index
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
        cols[f"open_{tag}"] = at(oo)
        tr = np.zeros(nb)
        tr[(ef > es) & (cc > es)] = 1.0
        tr[(ef < es) & (cc < es)] = -1.0
        tr[np.isnan(ef) | np.isnan(es) | np.isnan(cc)] = np.nan
        cols[f"trend_{tag}"] = at(tr)
        if tag == "15m":
            shb, slb = swing_points(hh, ll, fc.swing_k)
            cols["swing_high_15m"], cols["swing_low_15m"] = at(shb), at(slb)
            cols["bar15_complete"] = ((np.arange(n) + 1) % m == 0).astype(np.float64)
    # ---- realised volatility and its 30-day terciles (1h cadence samples of past rows)
    lr = np.concatenate([[np.nan], np.diff(np.log(c))])
    lr2 = lr * lr
    for w, name in ((12, "rv_1h"), (288, "rv_24h")):
        m1, m2 = rolling_sum(lr, w) / w, rolling_sum(lr2, w) / w
        cols[name] = np.sqrt(np.maximum(m2 - m1 * m1, 0.0)) * math.sqrt(288.0)
    rv = _series(cols["rv_24h"]).shift(1)
    wq = fc.rv_rank_days * SLOTS
    q33 = rv.rolling_quantile(0.3333, window_size=wq, min_samples=wq // 3).to_numpy()
    q67 = rv.rolling_quantile(0.6667, window_size=wq, min_samples=wq // 3).to_numpy()
    cols["rv_24h_q33"], cols["rv_24h_q67"] = (
        np.asarray(q33, dtype=np.float64),
        np.asarray(q67, dtype=np.float64),
    )
    vt = np.full(n, np.nan)
    ok = ~np.isnan(cols["rv_24h"]) & ~np.isnan(cols["rv_24h_q33"]) & ~np.isnan(cols["rv_24h_q67"])
    vt[ok] = 1.0
    vt[ok & (cols["rv_24h"] <= cols["rv_24h_q33"])] = 0.0
    vt[ok & (cols["rv_24h"] >= cols["rv_24h_q67"])] = 2.0
    cols["vol_tercile"] = vt
    # ---- session VWAP (UTC day, cumulative over valid rows)
    tp = (h + lo + c) / 3.0
    pv = np.where(valid, tp * vol, 0.0).reshape(n_days, SLOTS)
    vv = np.where(valid, vol, 0.0).reshape(n_days, SLOTS)
    cpv, cvv = np.cumsum(pv, axis=1), np.cumsum(vv, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        cols["vwap_day"] = np.where(cvv > 0, cpv / cvv, np.nan).reshape(-1)
    # ---- same-time-of-day baselines (previous days only)
    base_vol = slot_median_prev_days(vol, n_days, fc.rel_vol_days, fc.min_baseline_days)
    cols["base_vol_5m"] = base_vol
    with np.errstate(invalid="ignore", divide="ignore"):
        cols["rel_vol_5m"] = np.where(base_vol > 0, vol / base_vol, np.nan)
        base15 = rolling_sum(base_vol, 3)
        cols["vol_15m"] = rolling_sum(vol, 3)
        cols["rel_vol_15m"] = np.where(base15 > 0, cols["vol_15m"] / base15, np.nan)
        base_ret = slot_median_prev_days(
            np.abs(cols["ret_15m"]), n_days, fc.rel_vol_days, fc.min_baseline_days
        )
        cols["rel_absret_15m"] = np.where(base_ret > 0, np.abs(cols["ret_15m"]) / base_ret, np.nan)
    # ---- trade flow (aggTrades 5m aggregates; kline taker-buy volume as fallback)
    k_buy = col("taker_buy_volume") * nanv
    k_sell = vol - k_buy
    flow = inp.flow
    if not flow.is_empty():
        f = flow.sort("open_time_ms")
        ft = f["open_time_ms"].to_numpy().astype(np.int64)

        def fcol(name: str) -> F:
            return _exact_at(ft, f[name].cast(pl.Float64).to_numpy().astype(np.float64), t_open)

        a_buy, a_sell = fcol("buy_qty"), fcol("sell_qty")
        src = ~np.isnan(a_buy) & valid
        buy, sell = np.where(src, a_buy, k_buy), np.where(src, a_sell, k_sell)
        big_buy, big_sell = fcol("big_buy_qty"), fcol("big_sell_qty")
    else:
        src = np.zeros(n, dtype=bool)
        buy, sell = k_buy, k_sell
        big_buy = big_sell = np.full(n, np.nan)
    cols["flow_source"] = src.astype(np.float64)
    buy, sell = buy * nanv, sell * nanv
    cols["buy_qty"], cols["sell_qty"] = buy, sell
    delta = buy - sell
    tot = buy + sell
    with np.errstate(invalid="ignore", divide="ignore"):
        cols["delta_5m"] = delta
        cols["imbalance_5m"] = np.where(tot > 0, delta / tot, np.nan)
        for w, tag in ((3, "15m"), (12, "1h")):
            d, s_ = rolling_sum(delta, w), rolling_sum(tot, w)
            cols[f"delta_{tag}"] = d
            cols[f"imbalance_{tag}"] = np.where(s_ > 0, d / s_, np.nan)
            cols[f"cvd_slope_{tag}"] = d / w
        bb, bs = rolling_sum(big_buy * nanv, 3), rolling_sum(big_sell * nanv, 3)
        cols["big_imb_15m"] = np.where(bb + bs > 0, (bb - bs) / (bb + bs), np.nan)
    # ---- open interest (metrics rows with time <= T_k)
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
    for kk, name in ((1, "oi_chg_5m"), (3, "oi_chg_15m"), (12, "oi_chg_1h")):
        cols[name] = oi / lagged(oi, kk) - 1.0
    base_oi = slot_median_prev_days(
        np.abs(cols["oi_chg_15m"]), n_days, fc.rel_vol_days, fc.min_baseline_days
    )
    with np.errstate(invalid="ignore", divide="ignore"):
        cols["rel_absoi_15m"] = np.where(base_oi > 0, np.abs(cols["oi_chg_15m"]) / base_oi, np.nan)
    # ---- funding / premium
    fu = inp.funding
    if not fu.is_empty():
        f_ = fu.sort("time_ms")
        cols["fund"] = _last_at_many(
            f_["time_ms"].to_numpy().astype(np.int64),
            f_["funding_rate"].to_numpy().astype(np.float64),
            t,
        )
    else:
        cols["fund"] = np.full(n, np.nan)
    pr = inp.premium
    if not pr.is_empty():
        p_ = pr.sort("open_time_ms")
        prem = _exact_at(
            p_["open_time_ms"].to_numpy().astype(np.int64),
            p_["close"].to_numpy().astype(np.float64),
            t_open,
        )
    else:
        prem = np.full(n, np.nan)
    cols["prem"] = prem
    cols["prem_1h"] = rolling_sum(prem, 12) / 12.0
    cols["prem_z"] = rolling_z(cols["prem_1h"], fc.prem_z_window, fc.prem_z_min_periods)
    # ---- calendar
    cols["day"] = (t_open // DAY_MS).astype(np.float64)
    cols["slot"] = ((t_open % DAY_MS) // MS_5M).astype(np.float64)
    cols["utc_hour"] = ((t_open % DAY_MS) // 3_600_000).astype(np.float64)
    cols["weekday"] = (((t_open // DAY_MS) + 3) % 7).astype(np.float64)  # 1970-01-01 = Thursday(3)
    cols["session"] = np.floor(cols["utc_hour"] / 8.0)  # 0 Asia, 1 Europe, 2 US
    # ---- active-day classification (reporting only; uses the day's full range)
    dh, dl = (
        np.nanmax(h.reshape(n_days, SLOTS), axis=1),
        np.nanmin(lo.reshape(n_days, SLOTS), axis=1),
    )
    dc = c.reshape(n_days, SLOTS)
    last_c = np.array([_last_finite(r) for r in dc])
    with np.errstate(invalid="ignore", divide="ignore"):
        drange = np.where(last_c > 0, (dh - dl) / last_c, np.nan)
    rm = cfg.research.active_day.range_median_days
    med = (
        _series(drange)
        .shift(1)
        .rolling_median(rm, min_samples=max(10, rm // 2))
        .to_numpy()
        .astype(np.float64)
    )
    active = np.where(~np.isnan(med) & ~np.isnan(drange), (drange >= med).astype(float), np.nan)
    cols["day_range"] = np.repeat(drange, SLOTS)
    cols["active_day"] = np.repeat(active, SLOTS)
    # ---- regime code (1h trend x 30-day realised-vol tercile)
    tr1 = cols["trend_1h"]
    code = np.full(n, UNCLEAR, dtype=np.int64)
    okr = ~np.isnan(tr1) & ~np.isnan(vt)
    code[okr] = ((tr1[okr] + 1).astype(np.int64) * 3 + vt[okr].astype(np.int64)).astype(np.int64)
    fr = V6Frame(t_open, t, valid, cols, code)
    fr.meta = {
        "rows": n,
        "days": n_days,
        "valid_rows": int(valid.sum()),
        "flow_rows_from_aggtrades": int(src.sum()),
        "first_open_ms": int(t_open[0]),
        "last_close_ms": int(t[-1]),
        "feature_version": "v6-feat-1",
    }
    return fr


def _last_finite(r: F) -> float:
    f = r[~np.isnan(r)]
    return float(f[-1]) if len(f) else math.nan


DETECTOR_INPUTS: list[str] = [
    "atr5", "atr15m", "atr1h", "atr4h", "hi_4h", "lo_4h", "hi_24h", "lo_24h", "hi_1h", "lo_1h",
    "swing_high_5m", "swing_low_5m", "swing_high_15m", "swing_low_15m", "ema_fast_15m",
    "trend_1h", "trend_4h", "rel_vol_5m", "rel_vol_15m", "rel_absret_15m", "imbalance_5m",
    "imbalance_15m", "imbalance_1h", "cvd_slope_15m", "cvd_slope_1h", "oi_chg_15m", "oi_chg_1h",
    "rel_absoi_15m", "prem_z", "vwap_day", "vol_tercile",
]  # fmt: skip
