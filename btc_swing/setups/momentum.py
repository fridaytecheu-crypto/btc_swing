"""MOMENTUM_CONTINUATION_LONG / MOMENTUM_CONTINUATION_SHORT.

Long version (short mirrored):
  impulse   4h: within the last flag_max_age_bars completed 4h bars, a bar with range >=
            impulse_min_atr * ATR(4h) closing in the top (1 - impulse_close_location) of its range,
            with 4h EMA_fast > EMA_slow
  flag      1h: price has retraced from the impulse high by between flag_min and flag_max ATR(4h)
            and has not closed below the impulse bar's midpoint
  zone      [impulse_high - flag_max*ATR4h, impulse_high - flag_min*ATR4h]
  invalidation: impulse bar midpoint (a 5m close below = momentum failed)
  stop      flag low (lowest 1h low since the impulse) - stop_atr_buffer * ATR(1h), bounded;
            fallback zone edge
  run-away  impulse high: a 1h close above it before entry = RAN_WITHOUT_US
  target    structural: impulse high + impulse range (measured move)
"""

from __future__ import annotations

from btc_swing.core.config import MomentumContinuationCfg
from btc_swing.core.enums import Regime, SetupFamily, Side
from btc_swing.features.view import MarketView
from btc_swing.setups.base import SetupPlan, ZoneSetupDetector


class MomentumContinuationDetector(ZoneSetupDetector):
    def __init__(self, side: Side, cfg: MomentumContinuationCfg, eligible: list[Regime]) -> None:
        super().__init__(side, eligible, cfg.setup_tf, cfg.confirm_tf, cfg.entry_tf, 0.25)
        self.family = (
            SetupFamily.MOMENTUM_CONTINUATION_LONG
            if side is Side.LONG
            else SetupFamily.MOMENTUM_CONTINUATION_SHORT
        )
        self.cfg = cfg

    def detect(self, view: MarketView, regime: Regime) -> SetupPlan | None:
        c = self.cfg
        s = self.sgn
        if not self.eligible(regime):
            return None
        if not self.warm(view, c.impulse_tf, c.setup_tf, c.confirm_tf, c.entry_tf):
            return None
        ef_i, es_i = view.ind(c.impulse_tf, "ema_fast"), view.ind(c.impulse_tf, "ema_slow")
        atr_i = view.ind(c.impulse_tf, "atr")
        atr_s = view.ind(c.setup_tf, "atr")
        if not self.ok(ef_i, es_i, atr_i, atr_s) or atr_i <= 0 or atr_s <= 0:
            return None
        if s * (ef_i - es_i) <= 0:
            return None
        impulse: tuple[int, float, float, float] | None = None  # (offset, high, low, close)
        for off in range(c.flag_max_age_bars):
            try:
                _o_ms, _c_ms, o, h, lo_, cl = view.bar(c.impulse_tf, off)
            except IndexError:
                break
            rng = h - lo_
            if rng < c.impulse_min_atr * atr_i or rng <= 0:
                continue
            loc = (cl - lo_) / rng
            if s > 0 and loc >= c.impulse_close_location and cl > o:
                impulse = (off, h, lo_, cl)
                break
            if s < 0 and loc <= 1 - c.impulse_close_location and cl < o:
                impulse = (off, h, lo_, cl)
                break
        if impulse is None:
            return None
        off, ih, il, _ic = impulse
        extreme = ih if s > 0 else il
        mid_impulse = 0.5 * (ih + il)
        close_s = view.close(c.setup_tf)
        retrace = s * (extreme - close_s)
        if not (c.flag_min_atr * atr_i <= retrace <= c.flag_max_atr * atr_i):
            return None
        if s * (close_s - mid_impulse) <= 0:
            return None
        # flag extreme: lowest 1h low (long) / highest 1h high (short) since the impulse bar opened
        impulse_open_ms = view.bar(c.impulse_tf, off)[0]
        flag_ext = close_s
        k = 0
        while k < view.n(c.setup_tf) and view.bar(c.setup_tf, k)[0] >= impulse_open_ms:
            v = view.low(c.setup_tf, k) if s > 0 else view.high(c.setup_tf, k)
            flag_ext = min(flag_ext, v) if s > 0 else max(flag_ext, v)
            k += 1
        lo = extreme - (c.flag_max_atr if s > 0 else c.flag_min_atr) * atr_i
        hi = extreme - (c.flag_min_atr if s > 0 else c.flag_max_atr) * atr_i
        mid = 0.5 * (lo + hi)
        edge = lo if s > 0 else hi
        bs = self.bounded_stop(
            flag_ext - s * c.stop_atr_buffer * atr_s,
            f"FLAG_{'LOW' if s > 0 else 'HIGH'}_MINUS_{c.stop_atr_buffer}xATR_{c.setup_tf.value}",
            edge - s * c.stop_atr_buffer * atr_s,
            f"ENTRY_ZONE_EDGE_MINUS_{c.stop_atr_buffer}xATR_{c.setup_tf.value}",
            mid,
            atr_s,
            c.min_stop_atr,
            c.max_stop_atr,
        )
        if bs is None:
            return None
        stop, dist, reason = bs
        return SetupPlan(
            family=self.family,
            side=self.side,
            anchor=extreme,
            entry_zone_low=lo,
            entry_zone_high=hi,
            trigger=(
                f"{c.confirm_tf.value} close {'>' if s > 0 else '<'} {c.confirm_tf.value} EMA_fast inside the flag, "
                f"then {c.entry_tf.value} close {'above' if s > 0 else 'below'} previous {c.entry_tf.value} {'high' if s > 0 else 'low'}"
            ),
            invalidation_level=mid_impulse,
            invalidation_rule=f"{c.entry_tf.value} close beyond the impulse bar midpoint",
            stop_price=stop,
            stop_distance_ref=dist,
            stop_reason=reason,
            structural_target=extreme + s * (ih - il),
            atr_setup_tf=atr_s,
            detected_at_ms=view.t_ms,
            notes={
                "impulse_offset": off,
                "impulse_range_atr": (ih - il) / atr_i,
                "retrace_atr4h": retrace / atr_i,
                "flag_extreme": flag_ext,
                "stop_distance_atr": dist / atr_s,
            },
            run_away_level=extreme,
        )
