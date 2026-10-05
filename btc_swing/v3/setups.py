"""V3 setup families (frozen definitions, docs/BTC_SWING_V3_DESIGN.md section 4).

Each detector implements the generic `SetupDetector` interface so the V1 episode state machine
can be reused unchanged as a lifecycle: `detect` (1H/4H structure -> plan, WATCH), `zone_reached`
(5m close inside the entry zone), `confirmed` (the family's 15m condition), `triggered` (5m close
still inside the zone -> fill at the next 5m open), `pre_entry_invalidated` (structural cancel).
SHORT is the exact mirror of LONG through the side sign `s`: a price `p` is "beyond" a level `L`
in the trade direction when s * (p - L) > 0.
"""

from __future__ import annotations

import math
from typing import cast

import numpy as np

from btc_swing.core.enums import InvalidationReason, Regime, SetupFamily, Side, Timeframe
from btc_swing.features.view import MarketView
from btc_swing.setups.base import SetupDetector, SetupPlan
from btc_swing.v3.config import V3Config, V3Family
from btc_swing.v3.context import aligned, atr_percentile_series, trend_state

M5, M15, H1, H4 = Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4
MS_15M = 15 * 60_000
MS_1H = 60 * 60_000


def _fam(f: V3Family) -> SetupFamily:
    # the generic plan/episode records carry the family as a StrEnum value; V3 names are used
    return cast(SetupFamily, f)


class V3Detector(SetupDetector):
    """Shared mechanics: zone test on 5m closes, stop-distance bounds, min reward-to-risk."""

    v3_family: V3Family

    def __init__(self, cfg: V3Config, side: Side) -> None:
        super().__init__([])
        self.cfg = cfg
        self.side = side
        self.s = float(side.sign)
        self.family = _fam(self.v3_family)

    def eligible(self, regime: Regime) -> bool:  # V3 has no regime eligibility table
        return True

    # ---- helpers
    def _warm(self, view: MarketView) -> bool:
        return all(view.warm(tf) for tf in (M5, M15, H1, H4))

    def _bounds_ok(self, dist: float, atr: float) -> bool:
        st = self.cfg.setups
        return st.min_stop_atr * atr <= dist <= st.max_stop_atr * atr

    def _rr_ok(self, ref: float, stop: float, target: float) -> bool:
        dist = self.s * (ref - stop)
        return dist > 0 and self.s * (target - ref) / dist >= self.cfg.setups.min_rr

    def _zone(self, lo: float, hi: float) -> tuple[float, float]:
        return (min(lo, hi), max(lo, hi))

    def _in_zone(self, p: float, plan: SetupPlan) -> bool:
        return plan.entry_zone_low <= p <= plan.entry_zone_high

    def zone_reached(self, view: MarketView, plan: SetupPlan) -> bool:
        return self._in_zone(view.close(M5), plan)

    def triggered(self, view: MarketView, plan: SetupPlan) -> bool:
        return self._in_zone(view.close(M5), plan)

    def _stop_breached_5m(self, view: MarketView, plan: SetupPlan) -> bool:
        return self.s * (view.close(M5) - plan.stop_price) < 0

    def _expired(self, view: MarketView, plan: SetupPlan) -> bool:
        exp = plan.notes.get("expiry_ms")
        return exp is not None and view.t_ms > int(exp)

    def _plan(
        self,
        anchor: float,
        zone: tuple[float, float],
        invalidation: float,
        stop: float,
        ref: float,
        target: float,
        atr: float,
        view: MarketView,
        notes: dict[str, float | int],
        run_away: float | None = None,
        stop_reason: str = "",
    ) -> SetupPlan:
        return SetupPlan(
            family=self.family,
            side=self.side,
            anchor=anchor,
            entry_zone_low=zone[0],
            entry_zone_high=zone[1],
            trigger="5m close inside the entry zone after the 15m confirmation",
            invalidation_level=invalidation,
            invalidation_rule=self.v3_family.value,
            stop_price=stop,
            stop_distance_ref=abs(ref - stop),
            stop_reason=stop_reason,
            structural_target=target,
            atr_setup_tf=atr,
            detected_at_ms=view.t_ms,
            notes=dict(notes),
            run_away_level=run_away,
        )


# ----------------------------------------------------------------------------- A
class TrendPullbackContinuation(V3Detector):
    v3_family = V3Family.TREND_PULLBACK_CONTINUATION

    def detect(self, view: MarketView, regime: Regime) -> SetupPlan | None:
        if not self._warm(view):
            return None
        c = self.cfg
        s = self.s
        if trend_state(view, c.context.trend_tf) != int(s) or not aligned(
            view, c.context.align_tf, self.side
        ):
            return None
        p = c.setups.trend_pullback
        atr = view.ind(H1, "atr")
        end_name, start_name = ("swing_high", "swing_low") if s > 0 else ("swing_low", "swing_high")
        sw_end, sw_end_i = view.ind(H1, end_name), view.ind(H1, f"{end_name}_idx")
        sw_start, sw_start_i = view.ind(H1, start_name), view.ind(H1, f"{start_name}_idx")
        if any(math.isnan(x) for x in (atr, sw_end, sw_end_i, sw_start, sw_start_i)) or atr <= 0:
            return None
        if sw_end_i <= sw_start_i:
            return None
        impulse = s * (sw_end - sw_start)
        if impulse < p.impulse_min_atr * atr:
            return None
        z_far, z_near = sw_end - s * p.retrace_max * impulse, sw_end - s * p.retrace_min * impulse
        zlo, zhi = self._zone(z_far, z_near)
        c1 = view.close(H1)
        if not zlo <= c1 <= zhi:
            return None
        h1 = view.series.series[H1]
        i = view.idx[H1]
        j0 = int(sw_end_i) + 1
        if j0 > i:
            return None
        pb_ext = float(np.min(h1.low[j0 : i + 1])) if s > 0 else float(np.max(h1.high[j0 : i + 1]))
        if s * (pb_ext - sw_start) <= 0:
            return None  # structure broken
        stop = pb_ext - s * p.stop_buffer_atr * atr
        dist = s * (c1 - stop)
        if not self._bounds_ok(dist, atr) or not self._rr_ok(c1, stop, sw_end):
            return None
        pad = p.zone_pad_atr * atr
        return self._plan(
            anchor=sw_end,
            zone=(zlo - pad, zhi + pad),
            invalidation=sw_start,
            stop=stop,
            ref=c1,
            target=sw_end,
            atr=atr,
            view=view,
            notes={"zone_core_lo": zlo, "zone_core_hi": zhi, "pullback_extreme": pb_ext},
            run_away=sw_end,
            stop_reason="PULLBACK_EXTREME_MINUS_ATR_BUFFER",
        )

    def confirmed(self, view: MarketView, plan: SetupPlan) -> bool:
        s = self.s
        _o_ms, _c_ms, o, _h, _l, c = view.bar(M15)
        e, prev_c, prev_e = (
            view.ind(M15, "ema_fast"),
            view.close(M15, 1),
            view.ind(M15, "ema_fast", 1),
        )
        if any(math.isnan(x) for x in (e, prev_e)):
            return False
        return (
            s * (c - e) > 0
            and s * (prev_c - prev_e) <= 0
            and s * (c - o) > 0
            and self._in_zone(c, plan)
        )

    def pre_entry_invalidated(self, view: MarketView, plan: SetupPlan) -> InvalidationReason | None:
        s = self.s
        if s * (view.close(H1) - plan.invalidation_level) < 0 or self._stop_breached_5m(view, plan):
            return InvalidationReason.LEVEL_BREACHED
        if plan.run_away_level is not None and s * (view.close(H1) - plan.run_away_level) > 0:
            return InvalidationReason.RAN_WITHOUT_US
        return None


# ----------------------------------------------------------------------------- B
class BreakoutRetest(V3Detector):
    v3_family = V3Family.BREAKOUT_RETEST

    def detect(self, view: MarketView, regime: Regime) -> SetupPlan | None:
        if not self._warm(view):
            return None
        s = self.s
        p = self.cfg.setups.breakout_retest
        h1 = view.series.series[H1]
        i = view.idx[H1]
        n = p.level_lookback_bars
        if i - 2 - n < 0:
            return None
        hi_s, lo_s = h1.high[i - 1 - n : i - 1], h1.low[i - 1 - n : i - 1]
        atr = view.ind(H1, "atr")
        if math.isnan(atr) or atr <= 0:
            return None
        level = float(np.max(hi_s)) if s > 0 else float(np.min(lo_s))
        height = (level - float(np.min(lo_s))) if s > 0 else (float(np.max(hi_s)) - level)
        c1 = view.close(H1)
        if s * (c1 - level) <= p.break_buffer_atr * atr:
            return None
        stop = level - s * p.stop_buffer_atr * atr
        ref = level + s * 0.5 * (p.zone_outer_atr - p.zone_inner_atr) * atr
        target = level + s * height
        dist = s * (ref - stop)
        if not self._bounds_ok(dist, atr) or not self._rr_ok(ref, stop, target):
            return None
        zone = self._zone(level - s * p.zone_inner_atr * atr, level + s * p.zone_outer_atr * atr)
        return self._plan(
            anchor=level,
            zone=zone,
            invalidation=level - s * p.invalidation_atr * atr,
            stop=stop,
            ref=ref,
            target=target,
            atr=atr,
            view=view,
            notes={
                "level": level,
                "height": height,
                "expiry_ms": view.t_ms + p.retest_window_bars * MS_1H,
            },
            stop_reason="FAILED_BREAKOUT_LEVEL_MINUS_ATR_BUFFER",
        )

    def confirmed(self, view: MarketView, plan: SetupPlan) -> bool:
        s = self.s
        p = self.cfg.setups.breakout_retest
        level, atr = float(plan.notes["level"]), plan.atr_setup_tf
        _o_ms, _c_ms, o, h, lo, c = view.bar(M15)
        adverse = lo if s > 0 else h
        touched = s * (level + s * p.retest_touch_atr * atr - adverse) >= 0
        return touched and s * (c - level) >= 0 and s * (c - o) > 0

    def pre_entry_invalidated(self, view: MarketView, plan: SetupPlan) -> InvalidationReason | None:
        if self.s * (view.close(H1) - plan.invalidation_level) < 0 or self._stop_breached_5m(
            view, plan
        ):
            return InvalidationReason.LEVEL_BREACHED
        if self._expired(view, plan):
            return InvalidationReason.WATCH_TIMEOUT
        return None


# ----------------------------------------------------------------------------- C
class LiquiditySweepReversal(V3Detector):
    v3_family = V3Family.LIQUIDITY_SWEEP_REVERSAL

    def detect(self, view: MarketView, regime: Regime) -> SetupPlan | None:
        if not self._warm(view):
            return None
        s = self.s
        p = self.cfg.setups.liquidity_sweep
        atr = view.ind(H1, "atr")
        if math.isnan(atr) or atr <= 0:
            return None
        lvl_name = "swing_low" if s > 0 else "swing_high"
        levels = [view.ind(H4, lvl_name), view.ind(H1, lvl_name)]
        _o_ms, c_ms, _o, h, lo, _c = view.bar(M15)
        adverse = lo if s > 0 else h
        level = math.nan
        for lv in levels:  # the 4H level first (more significant), then the 1H level
            if not math.isnan(lv) and s * (lv - adverse) >= p.sweep_min_atr * atr:
                level = lv
                break
        if math.isnan(level):
            return None
        target = view.ind(H1, "swing_high" if s > 0 else "swing_low")
        if math.isnan(target):
            return None
        stop = adverse - s * p.stop_buffer_atr * atr
        dist = s * (level - stop)
        if not self._bounds_ok(dist, atr) or not self._rr_ok(level, stop, target):
            return None
        zone = self._zone(level - s * p.zone_inner_atr * atr, level + s * p.zone_outer_atr * atr)
        return self._plan(
            anchor=level,
            zone=zone,
            invalidation=adverse,
            stop=stop,
            ref=level,
            target=target,
            atr=atr,
            view=view,
            notes={
                "level": level,
                "sweep_extreme": adverse,
                "sweep_close_ms": c_ms,
                "expiry_ms": c_ms + p.reclaim_window_bars * MS_15M,
            },
            stop_reason="SWEEP_EXTREME_MINUS_ATR_BUFFER",
        )

    def confirmed(self, view: MarketView, plan: SetupPlan) -> bool:
        s = self.s
        level = float(plan.notes["level"])
        _o_ms, _c_ms, _o, h, lo, c = view.bar(M15)
        mid = 0.5 * (h + lo)
        return s * (c - level) > 0 and s * (c - mid) >= 0 and not self._expired(view, plan)

    def pre_entry_invalidated(self, view: MarketView, plan: SetupPlan) -> InvalidationReason | None:
        if self.s * (view.close(M15) - plan.invalidation_level) < 0 or self._stop_breached_5m(
            view, plan
        ):
            return InvalidationReason.LEVEL_BREACHED
        if self._expired(view, plan):
            return InvalidationReason.WATCH_TIMEOUT
        return None


# ----------------------------------------------------------------------------- D
class VolatilityExpansionContinuation(V3Detector):
    v3_family = V3Family.VOLATILITY_EXPANSION_CONTINUATION

    def detect(self, view: MarketView, regime: Regime) -> SetupPlan | None:
        if not self._warm(view):
            return None
        s = self.s
        p = self.cfg.setups.volatility_expansion
        h1 = view.series.series[H1]
        i = view.idx[H1]
        w = self.cfg.context.vol_percentile_window
        need = (
            max(w + p.compression_lookback_bars, p.breakout_lookback_bars, p.volume_lookback_bars)
            + 2
        )
        if i < need:
            return None
        pct = atr_percentile_series(h1, w)
        recent = pct[i - p.compression_lookback_bars : i]
        if np.isnan(recent).all() or not bool(np.nanmin(recent) <= p.compression_percentile):
            return None
        atr_prev = float(h1.ind["atr"][i - 1])
        atr = view.ind(H1, "atr")
        if math.isnan(atr_prev) or atr_prev <= 0 or math.isnan(atr):
            return None
        hi, lo, c = float(h1.high[i]), float(h1.low[i]), float(h1.close[i])
        rng = hi - lo
        if rng < p.expansion_range_atr * atr_prev or rng <= 0:
            return None
        loc = (c - lo) / rng if s > 0 else (hi - c) / rng
        if loc < p.close_location:
            return None
        prev_ext = (
            float(np.max(h1.high[i - p.breakout_lookback_bars : i]))
            if s > 0
            else float(np.min(h1.low[i - p.breakout_lookback_bars : i]))
        )
        if s * (c - prev_ext) <= 0 or s * (c - prev_ext) > p.max_chase_atr * atr:
            return None
        vol_prev = float(np.mean(h1.volume[i - p.volume_lookback_bars : i]))
        if vol_prev <= 0 or float(h1.volume[i]) < p.volume_mult * vol_prev:
            return None
        if s * (c - view.ind(H1, "ema_fast")) <= 0:
            return None
        mid = 0.5 * (hi + lo)
        fav = hi if s > 0 else lo
        stop = mid - s * p.stop_buffer_atr * atr
        target = fav + s * rng
        dist = s * (c - stop)
        if not self._bounds_ok(dist, atr) or not self._rr_ok(c, stop, target):
            return None
        zone = self._zone(mid, fav + s * p.confirm_max_above_atr * atr)
        exp_close_ms = int(h1.close_ms[i])
        return self._plan(
            anchor=fav,
            zone=zone,
            invalidation=mid,
            stop=stop,
            ref=c,
            target=target,
            atr=atr,
            view=view,
            notes={
                "expansion_extreme": fav,
                "expansion_mid": mid,
                "expansion_close_ms": exp_close_ms,
                "expiry_ms": exp_close_ms + p.confirm_window_bars * MS_15M,
            },
            stop_reason="EXPANSION_MIDPOINT_MINUS_ATR_BUFFER",
        )

    def confirmed(self, view: MarketView, plan: SetupPlan) -> bool:
        s = self.s
        p = self.cfg.setups.volatility_expansion
        if view.close_ms(M15) <= int(plan.notes["expansion_close_ms"]) or self._expired(view, plan):
            return False
        c = view.close(M15)
        prev_ext = view.high(M15, 1) if s > 0 else view.low(M15, 1)
        cap = (
            float(plan.notes["expansion_extreme"]) + s * p.confirm_max_above_atr * plan.atr_setup_tf
        )
        return s * (c - prev_ext) > 0 and s * (c - cap) <= 0

    def pre_entry_invalidated(self, view: MarketView, plan: SetupPlan) -> InvalidationReason | None:
        if self.s * (view.close(M15) - plan.invalidation_level) < 0 or self._stop_breached_5m(
            view, plan
        ):
            return InvalidationReason.LEVEL_BREACHED
        if self._expired(view, plan):
            return InvalidationReason.WATCH_TIMEOUT
        return None


DETECTORS: dict[V3Family, type[V3Detector]] = {
    V3Family.TREND_PULLBACK_CONTINUATION: TrendPullbackContinuation,
    V3Family.BREAKOUT_RETEST: BreakoutRetest,
    V3Family.LIQUIDITY_SWEEP_REVERSAL: LiquiditySweepReversal,
    V3Family.VOLATILITY_EXPANSION_CONTINUATION: VolatilityExpansionContinuation,
}


def build_v3_detectors(cfg: V3Config) -> list[V3Detector]:
    """Priority order: families as configured, LONG before SHORT."""
    out: list[V3Detector] = []
    for fam in cfg.episode.family_priority:
        for side in (Side.LONG, Side.SHORT):
            out.append(DETECTORS[fam](cfg, side))
    return out
