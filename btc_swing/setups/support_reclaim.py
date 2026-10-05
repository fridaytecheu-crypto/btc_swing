"""SUPPORT_RECLAIM_LONG / RESISTANCE_REJECTION_SHORT.

Long version (short mirrored):
  level     4h: last confirmed 4h swing low, at most level_max_age_bars old
  sweep     1h: within the last sweep_window_bars completed 1h bars a low pierced the level by
            >= sweep_min_atr * ATR(1h) and the latest 1h close is back above the level (reclaim)
  zone      [level - zone_inner*ATR, level + zone_outer*ATR]
  invalidation: the sweep extreme (a 5m close below it = the reclaim failed)
  stop      sweep extreme - stop_atr_buffer * ATR(1h), bounded; fallback zone edge
  run-away  structural target (prior 4h swing high); reached before entry = RAN_WITHOUT_US
"""

from __future__ import annotations

from btc_swing.core.config import SupportReclaimCfg
from btc_swing.core.enums import Regime, SetupFamily, Side
from btc_swing.features.view import MarketView
from btc_swing.setups.base import SetupPlan, ZoneSetupDetector


class SupportReclaimDetector(ZoneSetupDetector):
    def __init__(self, side: Side, cfg: SupportReclaimCfg, eligible: list[Regime]) -> None:
        super().__init__(
            side, eligible, cfg.setup_tf, cfg.confirm_tf, cfg.entry_tf, cfg.zone_inner_atr
        )
        self.family = (
            SetupFamily.SUPPORT_RECLAIM_LONG
            if side is Side.LONG
            else SetupFamily.RESISTANCE_REJECTION_SHORT
        )
        self.cfg = cfg

    def detect(self, view: MarketView, regime: Regime) -> SetupPlan | None:
        c = self.cfg
        s = self.sgn
        if not self.eligible(regime):
            return None
        if not self.warm(view, c.level_tf, c.setup_tf, c.confirm_tf, c.entry_tf):
            return None
        lvl_name, idx_name, other_name = (
            ("swing_low", "swing_low_idx", "swing_high")
            if s > 0
            else ("swing_high", "swing_high_idx", "swing_low")
        )
        level, level_idx = view.ind(c.level_tf, lvl_name), view.ind(c.level_tf, idx_name)
        other = view.ind(c.level_tf, other_name)
        atr_s = view.ind(c.setup_tf, "atr")
        if not self.ok(level, level_idx, atr_s) or atr_s <= 0:
            return None
        if view.idx[c.level_tf] - level_idx > c.level_max_age_bars:
            return None
        close_s = view.close(c.setup_tf)
        if s * (close_s - level) <= 0:  # not reclaimed
            return None
        # find the sweep within the window
        sweep: float | None = None
        for off in range(c.sweep_window_bars):
            try:
                ext = view.low(c.setup_tf, off) if s > 0 else view.high(c.setup_tf, off)
            except IndexError:
                break
            if s * (level - ext) >= c.sweep_min_atr * atr_s:
                sweep = ext if sweep is None else (min(sweep, ext) if s > 0 else max(sweep, ext))
        if sweep is None:
            return None
        lo = level - (c.zone_inner_atr if s > 0 else c.zone_outer_atr) * atr_s
        hi = level + (c.zone_outer_atr if s > 0 else c.zone_inner_atr) * atr_s
        mid = 0.5 * (lo + hi)
        edge = lo if s > 0 else hi
        bs = self.bounded_stop(
            sweep - s * c.stop_atr_buffer * atr_s,
            f"SWEEP_EXTREME_MINUS_{c.stop_atr_buffer}xATR_{c.setup_tf.value}",
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
        target = other if self.ok(other) and s * (other - level) > 0 else None
        return SetupPlan(
            family=self.family,
            side=self.side,
            anchor=level,
            entry_zone_low=lo,
            entry_zone_high=hi,
            trigger=(
                f"{c.confirm_tf.value} close {'>' if s > 0 else '<'} {c.confirm_tf.value} EMA_fast after the reclaim, "
                f"then {c.entry_tf.value} close {'above' if s > 0 else 'below'} previous {c.entry_tf.value} {'high' if s > 0 else 'low'}"
            ),
            invalidation_level=sweep,
            invalidation_rule=f"{c.entry_tf.value} close beyond the sweep extreme (reclaim failed)",
            stop_price=stop,
            stop_distance_ref=dist,
            stop_reason=reason,
            structural_target=target,
            atr_setup_tf=atr_s,
            detected_at_ms=view.t_ms,
            notes={
                "level": level,
                "level_age_bars": view.idx[c.level_tf] - level_idx,
                "sweep_extreme": sweep,
                "sweep_depth_atr": s * (level - sweep) / atr_s,
                "stop_distance_atr": dist / atr_s,
            },
            run_away_level=target if target is not None else level + s * 3.0 * atr_s,
        )
