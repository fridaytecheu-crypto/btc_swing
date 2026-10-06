"""V4 PIT feature frame at 1H granularity (`v4-feat-1`).

Row j describes the completed 1H bar with close time T_j and uses only observations with time
<= T_j (metrics, funding, premium rows: observation time + latency <= T_j; 5m/1H/4H bars: close
time <= T_j). Rolling z-scores use the previous `z_window_bars` rows, EXCLUDING row j, with at
least `z_min_periods` rows; the funding z uses the previous `funding_z_window` funding
observations. A row is therefore fully known at T_j and never changes later.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
import polars as pl
from numpy.typing import NDArray

from btc_swing.core.enums import Timeframe
from btc_swing.features.context import AuxSeries
from btc_swing.features.view import MultiTfSeries, TfSeries
from btc_swing.v4.config import V4Config

F = NDArray[np.float64]
I = NDArray[np.int64]  # noqa: E741
H1, H4, M5 = Timeframe.H1, Timeframe.H4, Timeframe.M5
MS_1H = 3_600_000


def rolling_z(x: F, window: int, min_periods: int) -> F:
    """z[i] = (x[i] - mean(x[i-window:i])) / std(x[i-window:i]); NaNs ignored; past only."""
    s = pl.Series(x.astype(np.float64)).fill_nan(None).shift(1)
    mean = s.rolling_mean(window, min_samples=min_periods).to_numpy().astype(np.float64)
    std = s.rolling_std(window, min_samples=min_periods).to_numpy().astype(np.float64)
    out = np.full(len(x), np.nan)
    ok = ~np.isnan(mean) & ~np.isnan(std) & (std > 0) & ~np.isnan(x)
    out[ok] = (x[ok] - mean[ok]) / std[ok]
    return out


def rolling_max_prev(x: F, window: int) -> F:
    s = pl.Series(x.astype(np.float64)).fill_nan(None).shift(1)
    return s.rolling_max(window, min_samples=1).to_numpy().astype(np.float64)


def rolling_min_prev(x: F, window: int) -> F:
    s = pl.Series(x.astype(np.float64)).fill_nan(None).shift(1)
    return s.rolling_min(window, min_samples=1).to_numpy().astype(np.float64)


def _last_at_many(times: I, values: F, t: I, lag: int = 0) -> F:
    out = np.full(len(t), np.nan)
    if len(times) == 0:
        return out
    pos = np.searchsorted(times, t - lag, side="right") - 1
    ok = pos >= 0
    out[ok] = values[pos[ok]]
    return out


def _pct_rank_prev(x: F, window: int) -> F:
    out = np.full(len(x), np.nan)
    for j in range(window, len(x)):
        prev = x[j - window : j]
        prev = prev[~np.isnan(prev)]
        if len(prev) >= window // 2 and not math.isnan(x[j]):
            out[j] = float(np.mean(prev < x[j]))
    return out


class V4Regime(StrEnum):
    UP_EXPANSION = "UP_EXPANSION"
    UP_COMPRESSION = "UP_COMPRESSION"
    UP_NORMAL = "UP_NORMAL"
    DOWN_EXPANSION = "DOWN_EXPANSION"
    DOWN_COMPRESSION = "DOWN_COMPRESSION"
    DOWN_NORMAL = "DOWN_NORMAL"
    NEUTRAL_EXPANSION = "NEUTRAL_EXPANSION"
    NEUTRAL_COMPRESSION = "NEUTRAL_COMPRESSION"
    NEUTRAL_NORMAL = "NEUTRAL_NORMAL"
    UNCLEAR = "UNCLEAR"


@dataclass
class FeatureFrame:
    close_ms: I
    cols: dict[str, F] = field(default_factory=dict)
    regime: list[V4Regime] = field(default_factory=list)

    def idx_at(self, t_ms: int) -> int:
        return int(np.searchsorted(self.close_ms, t_ms, side="right")) - 1

    def v(self, name: str, j: int) -> float:
        return float(self.cols[name][j]) if 0 <= j < len(self.close_ms) else math.nan

    def row(self, j: int) -> dict[str, float]:
        return {k: float(a[j]) for k, a in self.cols.items()}

    def to_frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "close_ms": self.close_ms,
                **{k: a for k, a in self.cols.items()},
                "regime": self.regime,
            }
        )


def _trend_state_arr(h4: TfSeries) -> F:
    c, ef, es = h4.close, h4.ind["ema_fast"], h4.ind["ema_slow"]
    out = np.zeros(len(c))
    out[(ef > es) & (c > es)] = 1.0
    out[(ef < es) & (c < es)] = -1.0
    out[np.isnan(ef) | np.isnan(es)] = np.nan
    return out


def build_feature_frame(series: MultiTfSeries, aux: AuxSeries, cfg: V4Config) -> FeatureFrame:
    fc = cfg.features
    h1, h4, base = series.series[H1], series.series[H4], series.base
    t = h1.close_ms.astype(np.int64)
    n = len(t)
    win, mp = fc.z_window_bars, fc.z_min_periods
    c = h1.close.astype(np.float64)
    cols: dict[str, F] = {
        "close": c,
        "high": h1.high,
        "low": h1.low,
        "atr": h1.ind["atr"],
        "ema21": h1.ind["ema_fast"],
    }

    def lagged_ret(k: int) -> F:
        r = np.full(n, np.nan)
        r[k:] = c[k:] / c[:-k] - 1.0
        return r

    cols["ret_1h"], cols["ret_4h"], cols["ret_24h"] = lagged_ret(1), lagged_ret(4), lagged_ret(24)
    cols["ret_1h_z"] = rolling_z(cols["ret_1h"], win, mp)
    cols["ret_4h_z"] = rolling_z(cols["ret_4h"], win, mp)
    # open interest
    oi = _last_at_many(aux.metrics_t, aux.oi, t, aux.latency_ms)
    oi[oi <= 0] = np.nan
    cols["oi"] = oi
    for k, name in ((1, "oi_chg_1h"), (4, "oi_chg_4h"), (24, "oi_chg_24h")):
        r = np.full(n, np.nan)
        r[k:] = oi[k:] / oi[:-k] - 1.0
        cols[name] = r
    cols["oi_chg_4h_z"] = rolling_z(cols["oi_chg_4h"], win, mp)
    cols["oi_chg_24h_z"] = rolling_z(cols["oi_chg_24h"], win, mp)
    cols["price_oi_div_4h"] = cols["ret_4h"] * 100.0 - cols["oi_chg_4h"] * 100.0
    # funding (z per funding observation, mapped to bars)
    if len(aux.funding_t):
        fr = aux.funding_rate.astype(np.float64)
        fz = rolling_z(fr, fc.funding_z_window, max(10, fc.funding_z_window // 2))
        fchg = np.concatenate([[np.nan], np.diff(fr)])
        cols["fund"] = _last_at_many(aux.funding_t, fr, t, aux.latency_ms)
        cols["fund_chg"] = _last_at_many(aux.funding_t, fchg, t, aux.latency_ms)
        cols["fund_z"] = _last_at_many(aux.funding_t, fz, t, aux.latency_ms)
    else:
        cols["fund"] = cols["fund_chg"] = cols["fund_z"] = np.full(n, np.nan)
    cols["fund_z_max48"] = rolling_max_prev(cols["fund_z"], fc.cooling_lookback_hours)
    cols["fund_z_min48"] = rolling_min_prev(cols["fund_z"], fc.cooling_lookback_hours)
    # taker flow from kline flow columns (5m), summed over 1h / 4h ending at T_j
    kpos = np.searchsorted(base.close_ms, t, side="right") - 1
    tb = base.ind.get("taker_buy_volume", np.zeros(len(base)))
    cs_tb = np.concatenate([[0.0], np.cumsum(tb)])
    cs_v = np.concatenate([[0.0], np.cumsum(base.volume)])

    def taker_ratio(bars: int) -> F:
        out = np.full(n, np.nan)
        lo = kpos - bars + 1
        ok = (lo >= 0) & (kpos >= 0)
        vol = cs_v[kpos[ok] + 1] - cs_v[lo[ok]]
        buy = cs_tb[kpos[ok] + 1] - cs_tb[lo[ok]]
        res = np.full(int(ok.sum()), np.nan)
        pos_v = vol > 0
        res[pos_v] = buy[pos_v] / vol[pos_v]
        out[ok] = res
        return out

    cols["taker_1h"], cols["taker_4h"] = taker_ratio(12), taker_ratio(48)
    cols["taker_1h_z"] = rolling_z(cols["taker_1h"], win, mp)
    cols["taker_4h_z"] = rolling_z(cols["taker_4h"], win, mp)
    cols["taker_accel"] = cols["taker_1h"] - cols["taker_4h"]
    # premium / basis
    if len(aux.premium_t):
        cols["prem"] = _last_at_many(aux.premium_t, aux.premium_close, t, aux.latency_ms)
        cs_p = np.concatenate([[0.0], np.cumsum(aux.premium_close)])
        ppos = np.searchsorted(aux.premium_t, t - aux.latency_ms, side="right") - 1
        plo = np.searchsorted(aux.premium_t, t - aux.latency_ms - MS_1H, side="right")
        pm = np.full(n, np.nan)
        ok = (ppos >= plo) & (ppos >= 0)
        pm[ok] = (cs_p[ppos[ok] + 1] - cs_p[plo[ok]]) / (ppos[ok] - plo[ok] + 1)
        cols["prem_1h"] = pm
    else:
        cols["prem"] = cols["prem_1h"] = np.full(n, np.nan)
    cols["prem_z"] = rolling_z(cols["prem_1h"], win, mp)
    cols["prem_z_max48"] = rolling_max_prev(cols["prem_z"], fc.cooling_lookback_hours)
    cols["prem_z_min48"] = rolling_min_prev(cols["prem_z"], fc.cooling_lookback_hours)
    # volume
    lv = np.log(np.where(h1.volume > 0, h1.volume, np.nan))
    cols["vol_z"] = rolling_z(lv, win, mp)
    v4 = (
        pl.Series(h1.volume.astype(np.float64))
        .rolling_sum(4, min_samples=4)
        .to_numpy()
        .astype(np.float64)
    )
    cols["vol4_z"] = rolling_z(np.log(np.where(v4 > 0, v4, np.nan)), win, mp)
    v20 = (
        pl.Series(h1.volume.astype(np.float64))
        .shift(1)
        .rolling_mean(20, min_samples=20)
        .to_numpy()
        .astype(np.float64)
    )
    cols["vol_accel"] = np.where(v20 > 0, h1.volume / v20, np.nan)
    # realised volatility (24h of 5m log returns, daily units)
    lr = np.concatenate([[0.0], np.diff(np.log(base.close))])
    cs1, cs2 = np.concatenate([[0.0], np.cumsum(lr)]), np.concatenate([[0.0], np.cumsum(lr * lr)])
    rv = np.full(n, np.nan)
    w = fc.rv_window_bars
    ok = kpos - w >= 0
    m1 = (cs1[kpos[ok] + 1] - cs1[kpos[ok] + 1 - w]) / w
    m2 = (cs2[kpos[ok] + 1] - cs2[kpos[ok] + 1 - w]) / w
    rv[ok] = np.sqrt(np.maximum(m2 - m1 * m1, 0.0)) * math.sqrt(288.0)
    cols["rv_24h"] = rv
    # 4H context at T_j (last completed 4H bar)
    j4 = np.searchsorted(h4.close_ms, t, side="right") - 1
    ts4 = _trend_state_arr(h4)
    cols["trend_4h"] = np.where(j4 >= 0, ts4[np.maximum(j4, 0)], np.nan)
    cols["swing_low_4h"] = np.where(j4 >= 0, h4.ind["swing_low"][np.maximum(j4, 0)], np.nan)
    cols["swing_high_4h"] = np.where(j4 >= 0, h4.ind["swing_high"][np.maximum(j4, 0)], np.nan)
    cols["swing_low_1h"], cols["swing_high_1h"] = h1.ind["swing_low"], h1.ind["swing_high"]
    # structure: previous-48-bar extremes (excluding the current bar)
    lvl_n = fc.level_lookback_bars
    cols["hi48"] = rolling_max_prev(h1.high.astype(np.float64), lvl_n)
    cols["lo48"] = rolling_min_prev(h1.low.astype(np.float64), lvl_n)
    cols["hi48"][:lvl_n] = np.nan
    cols["lo48"][:lvl_n] = np.nan
    cols["lo4"] = rolling_min_prev(
        np.concatenate([h1.low[1:], [np.nan]]).astype(np.float64), 4
    )  # placeholder, replaced below
    # lowest/highest of the LAST k bars INCLUDING the current bar
    lows = pl.Series(h1.low.astype(np.float64))
    highs = pl.Series(h1.high.astype(np.float64))
    cols["lo4"] = lows.rolling_min(4, min_samples=1).to_numpy().astype(np.float64)
    cols["hi4"] = highs.rolling_max(4, min_samples=1).to_numpy().astype(np.float64)
    cols["lo24"] = lows.rolling_min(24, min_samples=1).to_numpy().astype(np.float64)
    cols["hi24"] = highs.rolling_max(24, min_samples=1).to_numpy().astype(np.float64)
    cols["dist_ema21_atr"] = (c - cols["ema21"]) / cols["atr"]
    rng = cols["hi48"] - cols["lo48"]
    cols["range_pos_48"] = np.where(rng > 0, (c - cols["lo48"]) / rng, np.nan)
    cols["atr_pct_rank"] = _pct_rank_prev(cols["atr"], 100)
    ff = FeatureFrame(t, cols)
    ff.regime = _regimes(ff)
    return ff


def _regimes(ff: FeatureFrame) -> list[V4Regime]:
    out: list[V4Regime] = []
    tr, pr = ff.cols["trend_4h"], ff.cols["atr_pct_rank"]
    for j in range(len(ff.close_ms)):
        if math.isnan(tr[j]) or math.isnan(pr[j]):
            out.append(V4Regime.UNCLEAR)
            continue
        a = "UP" if tr[j] > 0 else ("DOWN" if tr[j] < 0 else "NEUTRAL")
        b = "EXPANSION" if pr[j] >= 0.8 else ("COMPRESSION" if pr[j] <= 0.2 else "NORMAL")
        out.append(V4Regime(f"{a}_{b}"))
    return out


FEATURE_COLUMNS: list[str] = [
    "ret_1h",
    "ret_4h",
    "ret_24h",
    "ret_1h_z",
    "ret_4h_z",
    "oi",
    "oi_chg_1h",
    "oi_chg_4h",
    "oi_chg_24h",
    "oi_chg_4h_z",
    "oi_chg_24h_z",
    "price_oi_div_4h",
    "fund",
    "fund_chg",
    "fund_z",
    "fund_z_max48",
    "fund_z_min48",
    "taker_1h",
    "taker_4h",
    "taker_1h_z",
    "taker_4h_z",
    "taker_accel",
    "prem",
    "prem_1h",
    "prem_z",
    "prem_z_max48",
    "prem_z_min48",
    "vol_z",
    "vol4_z",
    "vol_accel",
    "rv_24h",
    "trend_4h",
    "dist_ema21_atr",
    "range_pos_48",
    "atr_pct_rank",
]
