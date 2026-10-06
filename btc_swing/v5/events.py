"""V5 event families (frozen, docs/BTC_SWING_V5_DESIGN.md §5) and the generic Stage B lifecycle.

Each family computes, ONCE and vectorised, the event mask and strength over the whole feature
frame (row k uses only row k, which is PIT by construction); `event_at(k)` reads that mask, so
Stage A (every bar) and Stage B (the lifecycle) cannot disagree. The lifecycle (`SetupDetector`
interface reused from V1): `detect` fires on the bar where the event holds; `confirmed` is the one
15m condition (first completed 15m bar after the event whose close is beyond the previous 15m
close in the trade direction, within the 2 h thesis window); `zone_reached`/`triggered` are a 5m
close inside the entry zone within 6 bars of the confirmation; `pre_entry_invalidated` is the
thesis-window or entry-window expiry. SHORT mirrors LONG through the side sign `s`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import NDArray

from btc_swing.core.enums import InvalidationReason, Regime, SetupFamily, Side, Timeframe
from btc_swing.features.view import MarketView
from btc_swing.setups.base import SetupDetector, SetupPlan
from btc_swing.v5.config import V5Config, V5Family
from btc_swing.v5.features import FeatureFrame

M5, M15, H1 = Timeframe.M5, Timeframe.M15, Timeframe.H1
MS_5M = 300_000
F = NDArray[np.float64]


@dataclass(frozen=True)
class Event:
    family: V5Family
    side: Side
    k: int  # feature-frame row (5m bar) of the event
    strength: float
    close: float
    atr: float


class V5Detector(SetupDetector):
    v5_family: V5Family

    def __init__(self, cfg: V5Config, side: Side, ff: FeatureFrame) -> None:
        super().__init__([])
        self.cfg = cfg
        self.side = side
        self.s = float(side.sign)
        self.ff = ff
        self.family = cast(SetupFamily, self.v5_family)
        self._last_k = -1
        self.mask, self.strength = self.compute()
        base_ok = ~np.isnan(ff.cols["atr"]) & (ff.cols["atr"] > 0) & ~np.isnan(ff.cols["close"])
        self.mask &= base_ok

    # ---- vectorised event condition (pure function of the frame rows) -------------------
    def compute(self) -> tuple[NDArray[np.bool_], F]:
        raise NotImplementedError

    def _c(self, name: str) -> F:
        return self.ff.cols[name]

    def event_at(self, k: int) -> Event | None:
        if k < 0 or k >= len(self.mask) or not self.mask[k]:
            return None
        return Event(
            self.v5_family,
            self.side,
            k,
            float(self.strength[k]),
            self.ff.v("close", k),
            self.ff.v("atr", k),
        )

    def eligible(self, regime: Regime) -> bool:
        return True

    # ---- lifecycle ---------------------------------------------------------------------
    def detect(self, view: MarketView, regime: Regime) -> SetupPlan | None:
        k = self.ff.idx_at(view.t_ms)
        if k < 0 or k == self._last_k or int(self.ff.close_ms[k]) != view.close_ms(M5):
            return None
        self._last_k = k
        ev = self.event_at(k)
        if ev is None:
            return None
        return self.make_plan(ev, view)

    def make_plan(self, ev: Event, view: MarketView) -> SetupPlan:
        """Zone [close - 0.5 ATR, close + 1.0 ATR] (LONG; mirrored); the plan carries the
        STRUCTURAL stop (lowest 5m low of the 12 bars before the event bar - 0.25 ATR); the
        engine finalises the stop at the trigger bar as the farther of the structural stop and
        the 1.25 ATR volatility floor from the trigger close, capped at 3 ATR."""
        x, s, atr = self.cfg.execution, self.s, ev.atr
        c = ev.close
        z1, z2 = c - s * x.zone_below_atr * atr, c + s * x.zone_above_atr * atr
        struct = self.ff.v("lo_struct", ev.k) if s > 0 else self.ff.v("hi_struct", ev.k)
        if math.isnan(struct):
            struct = c
        structural_stop = struct - s * x.struct_buffer_atr * atr
        event_ms = int(self.ff.close_ms[ev.k])
        return SetupPlan(
            family=self.family,
            side=self.side,
            anchor=c,
            entry_zone_low=min(z1, z2),
            entry_zone_high=max(z1, z2),
            trigger="5m close inside the entry zone within 6 bars of the 15m confirmation",
            invalidation_level=structural_stop,
            invalidation_rule="THESIS_WINDOW_2H",
            stop_price=structural_stop,
            stop_distance_ref=abs(c - structural_stop),
            stop_reason="STRUCT_12BAR_EXTREME_MINUS_0_25ATR_OR_VOL_FLOOR_1_25ATR",
            structural_target=None,
            atr_setup_tf=atr,
            detected_at_ms=view.t_ms,
            notes={
                "event_k": ev.k,
                "event_ms": event_ms,
                "event_close": c,
                "strength": ev.strength,
                "expiry_ms": event_ms + x.confirm_window_bars * MS_5M,
                "structural_stop": structural_stop,
                "confirm_ms": -1,
                "regime_code": int(self.ff.regime_code[ev.k]),
            },
            run_away_level=None,
        )

    def _in_zone(self, p: float, plan: SetupPlan) -> bool:
        return plan.entry_zone_low <= p <= plan.entry_zone_high

    def _entry_window_open(self, view: MarketView, plan: SetupPlan) -> bool:
        cm = int(plan.notes["confirm_ms"])
        return cm > 0 and view.t_ms <= cm + self.cfg.episode.entry_ready_timeout_bars * MS_5M

    def confirmed(self, view: MarketView, plan: SetupPlan) -> bool:
        """One condition: the first completed 15m bar closing after the event bar whose close is
        beyond the previous 15m close in the trade direction. Once found (inside the thesis
        window) the confirmation time is recorded on the plan and stays confirmed."""
        if int(plan.notes["confirm_ms"]) > 0:
            return True
        if view.t_ms > int(plan.notes["expiry_ms"]):
            return False
        event_ms = int(plan.notes["event_ms"])
        n15 = view.n(M15)
        for off in range(0, min(n15 - 1, 12)):
            cm = view.close_ms(M15, off)
            if cm <= event_ms:
                break
            if self.s * (view.close(M15, off) - view.close(M15, off + 1)) > 0:
                # the FIRST such bar after the event is the confirmation; earlier bars with
                # the condition would have been found at their own close time
                plan.notes["confirm_ms"] = cm
                return True
        return False

    def zone_reached(self, view: MarketView, plan: SetupPlan) -> bool:
        return self._in_zone(view.close(M5), plan)

    def triggered(self, view: MarketView, plan: SetupPlan) -> bool:
        return self._entry_window_open(view, plan) and self._in_zone(view.close(M5), plan)

    def pre_entry_invalidated(self, view: MarketView, plan: SetupPlan) -> InvalidationReason | None:
        cm = int(plan.notes["confirm_ms"])
        if cm > 0 and view.t_ms > cm + self.cfg.episode.entry_ready_timeout_bars * MS_5M:
            return InvalidationReason.WATCH_TIMEOUT  # entry window after the confirmation expired
        if cm <= 0 and view.t_ms > int(plan.notes["expiry_ms"]):
            return InvalidationReason.WATCH_TIMEOUT  # thesis window expired without confirmation
        return None


def _nz(*xs: F) -> NDArray[np.bool_]:
    ok = np.ones(len(xs[0]), dtype=bool)
    for x in xs:
        ok &= ~np.isnan(x)
    return ok


# ----------------------------------------------------------------------------- A
class LiquidationContinuation(V5Detector):
    """Historical OI-flush PROXY for a liquidation cascade continuing in the impulse direction."""

    v5_family = V5Family.LIQUIDATION_CONTINUATION

    def compute(self) -> tuple[NDArray[np.bool_], F]:
        p, s = self.cfg.events.liquidation_continuation, self.s
        rz, oz, vz, iz = (
            self._c("ret_1h_z"),
            self._c("oi_chg_1h_z"),
            self._c("vol_1h_z"),
            self._c("imbalance_1h_z"),
        )
        m = (
            _nz(rz, oz, vz, iz)
            & (s * rz >= p.impulse_ret_1h_z)
            & (oz <= p.oi_chg_1h_z)
            & (vz >= p.vol_1h_z)
            & (s * iz >= p.imbalance_1h_z)
        )
        return m, vz - oz


# ----------------------------------------------------------------------------- B
class AbsorptionReversal(V5Detector):
    v5_family = V5Family.ABSORPTION_REVERSAL

    def compute(self) -> tuple[NDArray[np.bool_], F]:
        p, s = self.cfg.events.absorption_reversal, self.s
        rz, vz, iz = self._c("ret_1h_z"), self._c("vol_1h_z"), self._c("imbalance_1h_z")
        m = (
            _nz(rz, vz, iz)
            & (s * iz <= p.imbalance_1h_z)
            & (vz >= p.vol_1h_z)
            & (s * rz >= p.max_adverse_ret_1h_z)
        )
        return m, (-s * iz) - np.abs(rz)


# ----------------------------------------------------------------------------- C
class FlowOiContinuation(V5Detector):
    v5_family = V5Family.FLOW_OI_CONTINUATION

    def compute(self) -> tuple[NDArray[np.bool_], F]:
        p, s = self.cfg.events.flow_oi_continuation, self.s
        rz, iz, cz, oz = (
            self._c("ret_1h_z"),
            self._c("imbalance_1h_z"),
            self._c("cvd_slope_1h_z"),
            self._c("oi_chg_1h_z"),
        )
        fz, pz = self._c("fund_z"), self._c("prem_z")
        crowded = (~np.isnan(fz) & (s * fz > p.max_crowding_z)) | (
            ~np.isnan(pz) & (s * pz > p.max_crowding_z)
        )
        m = (
            _nz(rz, iz, cz, oz)
            & (s * rz >= p.ret_1h_z)
            & (s * iz >= p.imbalance_1h_z)
            & (s * cz >= p.cvd_slope_1h_z)
            & (oz >= p.oi_chg_1h_z)
            & ~crowded
        )
        return m, (s * iz + s * cz + oz) / 3.0


# ----------------------------------------------------------------------------- D
class FlowDivergenceReversal(V5Detector):
    """SHORT at a new 24h high that flow (CVD slope z <= 0) and OI (chg z <= 0) do not confirm;
    LONG mirror at a new 24h low (CVD slope z >= 0, i.e. selling not confirming). The OI term is
    unsigned in both directions ("no new positioning": oi_chg_1h_z <= max_oi_chg_z)."""

    v5_family = V5Family.FLOW_DIVERGENCE_REVERSAL

    def compute(self) -> tuple[NDArray[np.bool_], F]:
        p, s = self.cfg.events.flow_divergence_reversal, self.s
        cz, oz = self._c("cvd_slope_1h_z"), self._c("oi_chg_1h_z")
        brk = self._c("break_dn") if s > 0 else self._c("break_up")
        m = _nz(cz, oz) & (brk > 0) & (-s * cz <= p.max_cvd_slope_z) & (oz <= p.max_oi_chg_z)
        return m, (s * cz - oz) / 2.0


DETECTORS: dict[V5Family, type[V5Detector]] = {
    V5Family.LIQUIDATION_CONTINUATION: LiquidationContinuation,
    V5Family.ABSORPTION_REVERSAL: AbsorptionReversal,
    V5Family.FLOW_OI_CONTINUATION: FlowOiContinuation,
    V5Family.FLOW_DIVERGENCE_REVERSAL: FlowDivergenceReversal,
}


def build_v5_detectors(cfg: V5Config, ff: FeatureFrame) -> list[V5Detector]:
    out: list[V5Detector] = []
    for fam in cfg.episode.family_priority:
        for side in (Side.LONG, Side.SHORT):
            out.append(DETECTORS[fam](cfg, side, ff))
    return out
