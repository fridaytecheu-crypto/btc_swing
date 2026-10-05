"""Setup definitions: a setup is a *plan written before entry*, not a trade.

Every plan fixes, before any order exists: entry zone, trigger, technical invalidation, stop
(price, distance, reason) and initial targets (R multiples plus a structural level). Detectors
also answer the state-machine questions (zone reached? confirmed? triggered? invalidated?) so
discovery stays separate from entry timing. All eight V1 families share the same zone-based
entry mechanics (`ZoneSetupDetector`); they differ only in how the plan is discovered.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

from btc_swing.core.enums import InvalidationReason, Regime, SetupFamily, Side, Timeframe
from btc_swing.features.view import MarketView


@dataclass(frozen=True)
class SetupPlan:
    family: SetupFamily
    side: Side
    anchor: float  # structural anchor used for de-duplication (e.g. pullback origin)
    entry_zone_low: float
    entry_zone_high: float
    trigger: str
    invalidation_level: float
    invalidation_rule: str
    stop_price: float
    stop_distance_ref: float  # planned |zone mid - stop|
    stop_reason: str
    structural_target: float | None
    atr_setup_tf: float
    detected_at_ms: int
    notes: dict[str, Any] = field(default_factory=dict)
    run_away_level: float | None = None  # setup-TF close beyond this before entry = RAN_WITHOUT_US

    @property
    def zone_mid(self) -> float:
        return 0.5 * (self.entry_zone_low + self.entry_zone_high)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["family"] = self.family.value
        d["side"] = self.side.value
        return d


class SetupDetector(ABC):
    family: SetupFamily

    def __init__(self, eligible_regimes: list[Regime]) -> None:
        self.eligible_regimes = frozenset(eligible_regimes)

    def eligible(self, regime: Regime) -> bool:
        return regime in self.eligible_regimes

    @abstractmethod
    def detect(self, view: MarketView, regime: Regime) -> SetupPlan | None:
        """A fresh plan if the setup is present at t (-> WATCH), else None."""

    @abstractmethod
    def zone_reached(self, view: MarketView, plan: SetupPlan) -> bool: ...

    @abstractmethod
    def confirmed(self, view: MarketView, plan: SetupPlan) -> bool:
        """Confirmation-timeframe condition (WATCH -> ENTRY_READY)."""

    @abstractmethod
    def triggered(self, view: MarketView, plan: SetupPlan) -> bool:
        """Entry-timeframe condition (ENTRY_READY -> TRIGGERED)."""

    @abstractmethod
    def pre_entry_invalidated(
        self, view: MarketView, plan: SetupPlan
    ) -> InvalidationReason | None: ...


class ZoneSetupDetector(SetupDetector):
    """Shared entry mechanics for zone-based plans.

    zone reached   entry-TF bar trades into the zone and closes on the right side of its far edge
    confirmed      confirm-TF close back through its EMA_fast with a bar in the trade direction,
                   while price is not yet further than one pad beyond the zone
    triggered      entry-TF close beyond the previous entry-TF extreme, on the right side of the zone
    invalidated    entry-TF close beyond the invalidation level, or setup-TF close beyond the
                   run-away level (the move happened without us)
    """

    def __init__(
        self,
        side: Side,
        eligible: list[Regime],
        setup_tf: Timeframe,
        confirm_tf: Timeframe,
        entry_tf: Timeframe,
        zone_pad_atr: float,
    ) -> None:
        super().__init__(eligible)
        self.side = side
        self.sgn = side.sign
        self.setup_tf = setup_tf
        self.confirm_tf = confirm_tf
        self.entry_tf = entry_tf
        self.zone_pad_atr = zone_pad_atr

    def zone_reached(self, view: MarketView, plan: SetupPlan) -> bool:
        tf = self.entry_tf
        close, high, low = view.close(tf), view.high(tf), view.low(tf)
        if self.sgn > 0:
            return low <= plan.entry_zone_high and close >= plan.entry_zone_low
        return high >= plan.entry_zone_low and close <= plan.entry_zone_high

    def confirmed(self, view: MarketView, plan: SetupPlan) -> bool:
        tf = self.confirm_tf
        _o_ms, _c_ms, o, _h, _l, c = view.bar(tf)
        ef = view.ind(tf, "ema_fast")
        if math.isnan(ef):
            return False
        pad = self.zone_pad_atr * plan.atr_setup_tf
        if self.sgn > 0:
            return c > ef and c > o and c <= plan.entry_zone_high + pad
        return c < ef and c < o and c >= plan.entry_zone_low - pad

    def triggered(self, view: MarketView, plan: SetupPlan) -> bool:
        tf = self.entry_tf
        close = view.close(tf)
        if self.sgn > 0:
            return close > view.high(tf, 1) and close > plan.entry_zone_low
        return close < view.low(tf, 1) and close < plan.entry_zone_high

    def pre_entry_invalidated(self, view: MarketView, plan: SetupPlan) -> InvalidationReason | None:
        c_entry = view.close(self.entry_tf)
        if self.sgn * (c_entry - plan.invalidation_level) < 0:
            return InvalidationReason.LEVEL_BREACHED
        cs = view.close(self.setup_tf)
        run_away = plan.run_away_level if plan.run_away_level is not None else plan.anchor
        if self.sgn * (cs - run_away) > 0:
            return InvalidationReason.RAN_WITHOUT_US
        return None

    # ----------------------------------------------------------------- helpers for subclasses
    def warm(self, view: MarketView, *tfs: Timeframe) -> bool:
        return all(view.warm(tf) for tf in tfs)

    @staticmethod
    def ok(*xs: float) -> bool:
        return all(not math.isnan(x) for x in xs)

    def bounded_stop(
        self,
        structural_stop: float,
        structural_reason: str,
        fallback_stop: float,
        fallback_reason: str,
        ref: float,
        atr: float,
        min_atr: float,
        max_atr: float,
    ) -> tuple[float, float, str] | None:
        """(stop, distance from ref, reason) using the structural stop when its distance lies in
        [min_atr, max_atr] x ATR, else the fallback, else None."""
        for stop, reason in (
            (structural_stop, structural_reason),
            (fallback_stop, fallback_reason),
        ):
            dist = self.sgn * (ref - stop)
            if min_atr * atr <= dist <= max_atr * atr:
                return stop, dist, reason
        return None
