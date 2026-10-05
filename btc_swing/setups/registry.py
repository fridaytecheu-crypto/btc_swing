"""Enabled setup detectors from configuration (the eight pre-registered families, no more)."""

from __future__ import annotations

from btc_swing.core.config import BtcStrategyConfig
from btc_swing.core.enums import SetupFamily
from btc_swing.setups.base import SetupDetector
from btc_swing.setups.breakout import BreakoutDetector
from btc_swing.setups.momentum import MomentumContinuationDetector
from btc_swing.setups.support_reclaim import SupportReclaimDetector
from btc_swing.setups.trend_pullback import TrendPullbackDetector


def build_detectors(cfg: BtcStrategyConfig) -> list[SetupDetector]:
    out: list[SetupDetector] = []
    sc = cfg.setups
    for fam in sc.enabled:
        eligible = sc.eligible_regimes[fam]
        side = fam.side
        if fam in (SetupFamily.TREND_PULLBACK_LONG, SetupFamily.TREND_PULLBACK_SHORT):
            out.append(TrendPullbackDetector(side, sc.trend_pullback, eligible))
        elif fam in (SetupFamily.BREAKOUT_LONG, SetupFamily.BREAKDOWN_SHORT):
            out.append(BreakoutDetector(side, sc.breakout, eligible))
        elif fam in (SetupFamily.SUPPORT_RECLAIM_LONG, SetupFamily.RESISTANCE_REJECTION_SHORT):
            out.append(SupportReclaimDetector(side, sc.support_reclaim, eligible))
        elif fam in (
            SetupFamily.MOMENTUM_CONTINUATION_LONG,
            SetupFamily.MOMENTUM_CONTINUATION_SHORT,
        ):
            out.append(MomentumContinuationDetector(side, sc.momentum_continuation, eligible))
        else:  # pragma: no cover - enum is closed
            raise NotImplementedError(fam)
    assert len({d.family for d in out}) == len(out)
    return out
