"""BREAKOUT_LONG / BREAKDOWN_SHORT.

Long version (short mirrored):
  level     4h: prior 20-bar Donchian high of completed 4h bars (a range/resistance boundary)
  break     1h: latest completed 1h close above the level, with ATR(1h) expansion
            (ATR / ATR ten bars earlier >= min_expansion) and not further than
            max_extension_atr * ATR(1h) above the level (no chasing)
  zone      retest zone [level - zone_inner*ATR, level + zone_outer*ATR]
  invalidation: level - zone_inner*ATR (a 5m close back below = failed breakout)
  stop      level - stop_atr_buffer * ATR(1h) (failed breakout), bounded; fallback zone edge
  run-away  level + max_extension_atr * ATR(1h): a 1h close beyond it before entry = RAN_WITHOUT_US
  target    structural: level + (level - Donchian low), i.e. the range height projected
"""

from __future__ import annotations

from btc_swing.core.config import BreakoutCfg
from btc_swing.core.enums import Regime, SetupFamily, Side
from btc_swing.features.view import MarketView
from btc_swing.setups.base import SetupPlan, ZoneSetupDetector


class BreakoutDetector(ZoneSetupDetector):
    def __init__(self, side: Side, cfg: BreakoutCfg, eligible: list[Regime]) -> None:
        super().__init__(
            side, eligible, cfg.setup_tf, cfg.confirm_tf, cfg.entry_tf, cfg.zone_inner_atr
        )
        self.family = (
            SetupFamily.BREAKOUT_LONG if side is Side.LONG else SetupFamily.BREAKDOWN_SHORT
        )
        self.cfg = cfg

    def detect(self, view: MarketView, regime: Regime) -> SetupPlan | None:
        c = self.cfg
        s = self.sgn
        if not self.eligible(regime):
            return None
        if not self.warm(view, c.level_tf, c.setup_tf, c.confirm_tf, c.entry_tf):
            return None
        dh, dl = view.ind(c.level_tf, "donchian_high"), view.ind(c.level_tf, "donchian_low")
        atr_s, atr_prev = view.ind(c.setup_tf, "atr"), view.ind(c.setup_tf, "atr_prev10")
        close_s = view.close(c.setup_tf)
        if not self.ok(dh, dl, atr_s, atr_prev) or atr_s <= 0 or atr_prev <= 0:
            return None
        level = dh if s > 0 else dl
        other = dl if s > 0 else dh
        if s * (close_s - level) <= 0:  # no break yet
            return None
        if atr_s / atr_prev < c.min_expansion:
            return None
        ext = s * (close_s - level)
        if ext > c.max_extension_atr * atr_s:
            return None
        lo = level - (c.zone_inner_atr if s > 0 else c.zone_outer_atr) * atr_s
        hi = level + (c.zone_outer_atr if s > 0 else c.zone_inner_atr) * atr_s
        mid = 0.5 * (lo + hi)
        inval = level - s * c.zone_inner_atr * atr_s
        edge = lo if s > 0 else hi
        bs = self.bounded_stop(
            level - s * c.stop_atr_buffer * atr_s,
            f"BREAK_LEVEL_{c.level_tf.value}_MINUS_{c.stop_atr_buffer}xATR_{c.setup_tf.value}",
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
        height = abs(dh - dl)
        return SetupPlan(
            family=self.family,
            side=self.side,
            anchor=level,
            entry_zone_low=lo,
            entry_zone_high=hi,
            trigger=(
                f"retest: {c.confirm_tf.value} close back {'above' if s > 0 else 'below'} {c.confirm_tf.value} EMA_fast, "
                f"then {c.entry_tf.value} close {'above' if s > 0 else 'below'} previous {c.entry_tf.value} {'high' if s > 0 else 'low'}"
            ),
            invalidation_level=inval,
            invalidation_rule=f"{c.entry_tf.value} close back through the level by {c.zone_inner_atr} ATR (failed breakout)",
            stop_price=stop,
            stop_distance_ref=dist,
            stop_reason=reason,
            structural_target=level + s * height,
            atr_setup_tf=atr_s,
            detected_at_ms=view.t_ms,
            notes={
                "level": level,
                "range_other_side": other,
                "extension_atr": ext / atr_s,
                "atr_expansion": atr_s / atr_prev,
                "stop_distance_atr": dist / atr_s,
            },
            run_away_level=level + s * c.max_extension_atr * atr_s,
        )
