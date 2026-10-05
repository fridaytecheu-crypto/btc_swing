"""TREND_PULLBACK_LONG / TREND_PULLBACK_SHORT (one implementation, mirrored by side).

Long version (short is the mirror image):
  trend     4h: EMA_fast > EMA_slow and close > EMA_slow
  structure 1h: a confirmed swing high (the impulse) formed AFTER the last confirmed swing low
  pullback  1h: swing_high - close >= min_pullback_atr * ATR(1h), close still above the zone
  zone      1h: [min(EMA_fast, EMA_slow) - pad*ATR, max(EMA_fast, EMA_slow) + pad*ATR]
  invalidation: the 1h swing low (a 5m close below it ends the setup)
  stop      swing low - stop_atr_buffer * ATR(1h), bounded to [min_stop_atr, max_stop_atr] * ATR;
            fallback: zone edge - buffer (STOP_REASON says which)
  entry mechanics: ZoneSetupDetector (15m confirmation, 5m trigger)
  target    structural: the swing high; R targets are set by the exit config
"""

from __future__ import annotations

from btc_swing.core.config import TrendPullbackCfg
from btc_swing.core.enums import Regime, SetupFamily, Side
from btc_swing.features.view import MarketView
from btc_swing.setups.base import SetupPlan, ZoneSetupDetector


class TrendPullbackDetector(ZoneSetupDetector):
    def __init__(self, side: Side, cfg: TrendPullbackCfg, eligible: list[Regime]) -> None:
        super().__init__(
            side, eligible, cfg.setup_tf, cfg.confirm_tf, cfg.entry_tf, cfg.zone_pad_atr
        )
        self.family = (
            SetupFamily.TREND_PULLBACK_LONG
            if side is Side.LONG
            else SetupFamily.TREND_PULLBACK_SHORT
        )
        self.cfg = cfg

    def detect(self, view: MarketView, regime: Regime) -> SetupPlan | None:
        c = self.cfg
        s = self.sgn
        if not self.eligible(regime):
            return None
        if not self.warm(view, c.trend_tf, c.setup_tf, c.confirm_tf, c.entry_tf):
            return None
        ef_t, es_t = view.ind(c.trend_tf, "ema_fast"), view.ind(c.trend_tf, "ema_slow")
        close_t = view.close(c.trend_tf)
        if not self.ok(ef_t, es_t):
            return None
        if not (s * (ef_t - es_t) > 0 and s * (close_t - es_t) > 0):
            return None
        atr_s = view.ind(c.setup_tf, "atr")
        ef_s, es_s = view.ind(c.setup_tf, "ema_fast"), view.ind(c.setup_tf, "ema_slow")
        close_s = view.close(c.setup_tf)
        sh, shi = view.ind(c.setup_tf, "swing_high"), view.ind(c.setup_tf, "swing_high_idx")
        sl, sli = view.ind(c.setup_tf, "swing_low"), view.ind(c.setup_tf, "swing_low_idx")
        if not self.ok(atr_s, ef_s, es_s, sh, shi, sl, sli) or atr_s <= 0:
            return None
        anchor, anchor_idx = (sh, shi) if s > 0 else (sl, sli)
        inval, inval_idx = (sl, sli) if s > 0 else (sh, shi)
        if anchor_idx <= inval_idx:  # impulse must come after the structural invalidation point
            return None
        depth = s * (anchor - close_s)
        if depth < c.min_pullback_atr * atr_s:
            return None
        lo = min(ef_s, es_s) - c.zone_pad_atr * atr_s
        hi = max(ef_s, es_s) + c.zone_pad_atr * atr_s
        edge = lo if s > 0 else hi  # the far side of the zone
        if s * (close_s - edge) <= 0:  # already through the zone: no pullback setup
            return None
        if s * (inval - edge) >= 0:  # invalidation must lie beyond the zone
            return None
        mid = 0.5 * (lo + hi)
        side_word = "LOW" if s > 0 else "HIGH"
        bs = self.bounded_stop(
            inval - s * c.stop_atr_buffer * atr_s,
            f"{c.setup_tf.value}_SWING_{side_word}_MINUS_{c.stop_atr_buffer}xATR",
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
            anchor=anchor,
            entry_zone_low=lo,
            entry_zone_high=hi,
            trigger=(
                f"{c.confirm_tf.value} close {'>' if s > 0 else '<'} {c.confirm_tf.value} EMA_fast, "
                f"then {c.entry_tf.value} close {'above' if s > 0 else 'below'} previous "
                f"{c.entry_tf.value} {'high' if s > 0 else 'low'}"
            ),
            invalidation_level=inval,
            invalidation_rule=f"{c.entry_tf.value} close {'below' if s > 0 else 'above'} {c.setup_tf.value} swing",
            stop_price=stop,
            stop_distance_ref=dist,
            stop_reason=reason,
            structural_target=anchor,
            atr_setup_tf=atr_s,
            detected_at_ms=view.t_ms,
            notes={
                "pullback_depth_atr": depth / atr_s,
                "trend_tf_ema_fast": ef_t,
                "trend_tf_ema_slow": es_t,
                "setup_tf_ema_fast": ef_s,
                "setup_tf_ema_slow": es_s,
                "stop_distance_atr": dist / atr_s,
            },
            run_away_level=anchor,
        )
