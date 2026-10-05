"""BTC regime classification from completed 1d and 4h bars (rule based, PIT).

Priority order (first match wins): UNCLEAR (insufficient history) > HIGH_VOLATILITY >
BREAKOUT_REGIME > TREND_UP / TREND_DOWN > LOW_VOLATILITY > RANGE > UNCLEAR.
The regime decides which setup families are eligible; it never sizes or triggers a trade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from btc_swing.core.config import RegimeCfg
from btc_swing.core.enums import Regime, Timeframe
from btc_swing.core.versions import REGIME_RULE_VERSION
from btc_swing.features.view import MarketView


@dataclass
class RegimeResult:
    regime: Regime
    inputs: dict[str, Any] = field(default_factory=dict)
    rule_version: str = REGIME_RULE_VERSION


def _ok(*xs: float) -> bool:
    return all(not math.isnan(x) for x in xs)


def classify_regime(view: MarketView, cfg: RegimeCfg) -> RegimeResult:
    d1, h4 = Timeframe.D1, Timeframe.H4
    if not (view.warm(d1) and view.warm(h4)):
        return RegimeResult(Regime.UNCLEAR, {"reason": "insufficient_history"})
    close = view.close(d1)
    ef, es = view.ind(d1, "ema_fast"), view.ind(d1, "ema_slow")
    a, a_prev = view.ind(d1, "atr"), view.ind(d1, "atr_prev10")
    atr_pct = view.ind(d1, "atr_pct")
    sl = view.ind(d1, "ema_slow_slope")
    h4_ef, h4_es = view.ind(h4, "ema_fast"), view.ind(h4, "ema_slow")
    inputs: dict[str, Any] = {
        "d1_close": close,
        "d1_ema_fast": ef,
        "d1_ema_slow": es,
        "d1_atr_pct": atr_pct,
        "d1_ema_slow_slope": sl,
        "h4_ema_fast": h4_ef,
        "h4_ema_slow": h4_es,
    }
    if not _ok(close, ef, es, a, atr_pct, sl, h4_ef, h4_es):
        return RegimeResult(Regime.UNCLEAR, {**inputs, "reason": "indicator_not_warm"})
    if atr_pct >= cfg.high_vol_atr_pct:
        return RegimeResult(Regime.HIGH_VOLATILITY, inputs)
    # breakout: a recent 1d close beyond the prior Donchian channel with ATR expansion
    expansion = (a / a_prev) if _ok(a_prev) and a_prev > 0 else math.nan
    inputs["d1_atr_expansion"] = expansion
    if _ok(expansion) and expansion >= cfg.breakout_atr_expansion:
        for off in range(cfg.breakout_lookback_bars):
            try:
                c = view.close(d1, off)
                dh, dl = view.ind(d1, "donchian_high", off), view.ind(d1, "donchian_low", off)
            except IndexError:
                break
            if _ok(dh, dl) and (c > dh or c < dl):
                inputs["breakout_offset"] = off
                return RegimeResult(Regime.BREAKOUT_REGIME, inputs)
    if close > es and ef > es and sl > 0 and h4_ef > h4_es:
        return RegimeResult(Regime.TREND_UP, inputs)
    if close < es and ef < es and sl < 0 and h4_ef < h4_es:
        return RegimeResult(Regime.TREND_DOWN, inputs)
    if atr_pct <= cfg.low_vol_atr_pct:
        return RegimeResult(Regime.LOW_VOLATILITY, inputs)
    if abs(ef - es) <= cfg.range_ema_band_atr * a:
        return RegimeResult(Regime.RANGE, inputs)
    return RegimeResult(Regime.UNCLEAR, {**inputs, "reason": "no_rule_matched"})
