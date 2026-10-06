"""V4 event families (frozen, docs/BTC_SWING_V4_DESIGN.md section 4).

Each family exposes `event_at(j, side)` (the pure event condition on feature-frame row j, used by
Stage A for every 1H bar) and implements the generic `SetupDetector` interface so the episode
lifecycle can be reused: `detect` fires when the event holds on the last completed 1H bar,
`confirmed` is the family's 15m condition, `zone_reached` / `triggered` are 5m closes inside the
zone, `pre_entry_invalidated` is the structural cancel or the thesis-window expiry. SHORT is the
mirror of LONG through the side sign `s`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import cast

from btc_swing.core.enums import InvalidationReason, Regime, SetupFamily, Side, Timeframe
from btc_swing.features.view import MarketView
from btc_swing.setups.base import SetupDetector, SetupPlan
from btc_swing.v4.config import V4Config, V4Family
from btc_swing.v4.features import FeatureFrame

M5, M15, H1 = Timeframe.M5, Timeframe.M15, Timeframe.H1
MS_1H = 3_600_000


@dataclass(frozen=True)
class Event:
    family: V4Family
    side: Side
    j: int  # feature-frame row (1H bar) of the event
    strength: float
    anchor: float  # structural anchor (flush extreme / 24-bar low / level)
    objective: float | None


class V4Detector(SetupDetector):
    v4_family: V4Family

    def __init__(self, cfg: V4Config, side: Side, ff: FeatureFrame) -> None:
        super().__init__([])
        self.cfg = cfg
        self.side = side
        self.s = float(side.sign)
        self.ff = ff
        self.family = cast(SetupFamily, self.v4_family)
        self._last_j = -1

    def eligible(self, regime: Regime) -> bool:
        return True

    # ---- event condition (pure function of the frame row) --------------------------------
    def event_at(self, j: int) -> Event | None:
        raise NotImplementedError

    def _ok(self, *xs: float) -> bool:
        return all(not math.isnan(x) for x in xs)

    # ---- lifecycle ---------------------------------------------------------------------
    def detect(self, view: MarketView, regime: Regime) -> SetupPlan | None:
        j = self.ff.idx_at(view.t_ms)
        if j < 0 or j == self._last_j or int(self.ff.close_ms[j]) != view.close_ms(H1):
            return None
        self._last_j = j
        ev = self.event_at(j)
        if ev is None:
            return None
        return self._plan(ev, view)

    def _plan(self, ev: Event, view: MarketView) -> SetupPlan | None:
        raise NotImplementedError

    def _zone(self, lo: float, hi: float) -> tuple[float, float]:
        return (min(lo, hi), max(lo, hi))

    def _in_zone(self, p: float, plan: SetupPlan) -> bool:
        return plan.entry_zone_low <= p <= plan.entry_zone_high

    def zone_reached(self, view: MarketView, plan: SetupPlan) -> bool:
        return self._in_zone(view.close(M5), plan)

    def triggered(self, view: MarketView, plan: SetupPlan) -> bool:
        return self._in_zone(view.close(M5), plan)

    def _expired(self, view: MarketView, plan: SetupPlan) -> bool:
        return view.t_ms > int(plan.notes["expiry_ms"])

    def _ema_reclaim_15m(self, view: MarketView) -> bool:
        s = self.s
        _o_ms, _c_ms, o, _h, _l, c = view.bar(M15)
        e, prev_c, prev_e = (
            view.ind(M15, "ema_fast"),
            view.close(M15, 1),
            view.ind(M15, "ema_fast", 1),
        )
        if not self._ok(e, prev_e):
            return False
        return s * (c - e) > 0 and s * (prev_c - prev_e) <= 0 and s * (c - o) > 0

    def make_plan(
        self,
        ev: Event,
        view: MarketView,
        zone: tuple[float, float],
        invalidation: float,
        structural_stop: float,
        watch_hours: int,
        notes: dict[str, float | int],
        stop_reason: str,
    ) -> SetupPlan:
        """The plan carries the STRUCTURAL stop; the engine finalises the stop at the trigger bar
        as the farther of the structural stop and the volatility floor (capped), per the design."""
        atr = self.ff.v("atr", ev.j)
        return SetupPlan(
            family=self.family,
            side=self.side,
            anchor=ev.anchor,
            entry_zone_low=zone[0],
            entry_zone_high=zone[1],
            trigger="5m close inside the entry zone after the 15m confirmation",
            invalidation_level=invalidation,
            invalidation_rule=self.v4_family.value,
            stop_price=structural_stop,
            stop_distance_ref=abs(self.ff.v("close", ev.j) - structural_stop),
            stop_reason=stop_reason,
            structural_target=ev.objective,
            atr_setup_tf=atr,
            detected_at_ms=view.t_ms,
            notes={
                **notes,
                "event_j": ev.j,
                "strength": ev.strength,
                "expiry_ms": view.t_ms + watch_hours * MS_1H,
                "structural_stop": structural_stop,
            },
            run_away_level=None,
        )


# ----------------------------------------------------------------------------- A
class DeleveragingReversal(V4Detector):
    v4_family = V4Family.DELEVERAGING_REVERSAL

    def event_at(self, j: int) -> Event | None:
        p = self.cfg.events.deleveraging
        s = self.s
        f = self.ff
        rz, oz, vz, tz = (
            f.v("ret_4h_z", j),
            f.v("oi_chg_4h_z", j),
            f.v("vol4_z", j),
            f.v("taker_4h_z", j),
        )
        if not self._ok(rz, oz, vz, tz, f.v("atr", j)):
            return None
        if not (
            -s * rz >= p.impulse_ret_z
            and oz <= p.oi_change_4h_z
            and vz >= p.volume_4h_z
            and -s * tz >= p.taker_4h_z
        ):
            return None
        ext = f.v("lo4", j) if s > 0 else f.v("hi4", j)
        obj = f.v("swing_high_1h", j) if s > 0 else f.v("swing_low_1h", j)
        return Event(self.v4_family, self.side, j, -oz, ext, obj if not math.isnan(obj) else None)

    def _plan(self, ev: Event, view: MarketView) -> SetupPlan | None:
        p = self.cfg.events.deleveraging
        s = self.s
        atr = self.ff.v("atr", ev.j)
        ext = ev.anchor
        zone = self._zone(ext, ext + s * self.cfg.geometry.zone_width_atr * atr)
        return self.make_plan(
            ev,
            view,
            zone,
            invalidation=ext,
            structural_stop=ext - s * p.struct_buffer_atr * atr,
            watch_hours=p.watch_hours,
            notes={"flush_extreme": ext},
            stop_reason="FLUSH_EXTREME_MINUS_BUFFER_OR_VOL_FLOOR",
        )

    def confirmed(self, view: MarketView, plan: SetupPlan) -> bool:
        return (
            self._ema_reclaim_15m(view)
            and self.s * (view.close(M5) - float(plan.notes["flush_extreme"])) >= 0
            and not self._expired(view, plan)
        )

    def pre_entry_invalidated(self, view: MarketView, plan: SetupPlan) -> InvalidationReason | None:
        if self.s * (view.close(M15) - plan.invalidation_level) < 0:
            return InvalidationReason.LEVEL_BREACHED
        if self._expired(view, plan):
            return InvalidationReason.WATCH_TIMEOUT
        return None


# ----------------------------------------------------------------------------- B
class PositioningResetContinuation(V4Detector):
    v4_family = V4Family.POSITIONING_RESET_CONTINUATION

    def event_at(self, j: int) -> Event | None:
        p = self.cfg.events.positioning_reset
        s = self.s
        f = self.ff
        tr, c, e, rz = f.v("trend_4h", j), f.v("close", j), f.v("ema21", j), f.v("ret_4h_z", j)
        sw = f.v("swing_low_4h", j) if s > 0 else f.v("swing_high_4h", j)
        oz, fz, fzx, pz, pzx, tz = (
            f.v("oi_chg_24h_z", j),
            f.v("fund_z", j),
            (f.v("fund_z_max48", j) if s > 0 else f.v("fund_z_min48", j)),
            f.v("prem_z", j),
            (f.v("prem_z_max48", j) if s > 0 else f.v("prem_z_min48", j)),
            f.v("taker_4h_z", j),
        )
        if not self._ok(tr, c, e, rz, sw, oz, f.v("atr", j)) or tr != s:
            return None
        if not (s * (c - e) < 0 and -s * rz >= p.pullback_ret_z and s * (c - sw) > 0):
            return None
        if oz > p.oi_change_24h_z:
            return None
        comps = 0
        if self._ok(fz, fzx) and s * (fzx - fz) >= p.cooling_z_drop:
            comps += 1
        if self._ok(pz, pzx) and s * (pzx - pz) >= p.cooling_z_drop:
            comps += 1
        if self._ok(tz) and -s * tz >= p.taker_4h_z:
            comps += 1
        if comps == 0:
            return None
        anchor = f.v("lo24", j) if s > 0 else f.v("hi24", j)
        obj = f.v("swing_high_1h", j) if s > 0 else f.v("swing_low_1h", j)
        return Event(
            self.v4_family,
            self.side,
            j,
            comps + (-oz) / 2.0,
            anchor,
            obj if not math.isnan(obj) else None,
        )

    def _plan(self, ev: Event, view: MarketView) -> SetupPlan | None:
        p = self.cfg.events.positioning_reset
        s = self.s
        atr = self.ff.v("atr", ev.j)
        sw = self.ff.v("swing_low_4h", ev.j) if s > 0 else self.ff.v("swing_high_4h", ev.j)
        zone = self._zone(ev.anchor, ev.anchor + s * self.cfg.geometry.zone_width_atr * atr)
        return self.make_plan(
            ev,
            view,
            zone,
            invalidation=sw,
            structural_stop=sw - s * p.struct_buffer_atr * atr,
            watch_hours=p.watch_hours,
            notes={"swing_4h": sw},
            stop_reason="SWING_4H_MINUS_BUFFER_OR_VOL_FLOOR",
        )

    def confirmed(self, view: MarketView, plan: SetupPlan) -> bool:
        j = self.ff.idx_at(view.t_ms)
        taker = self.ff.v("taker_1h", j)
        flow_ok = (taker > 0.5) if self.s > 0 else (taker < 0.5)
        return (
            self._ema_reclaim_15m(view)
            and not math.isnan(taker)
            and flow_ok
            and not self._expired(view, plan)
        )

    def pre_entry_invalidated(self, view: MarketView, plan: SetupPlan) -> InvalidationReason | None:
        if self.s * (view.close(H1) - plan.invalidation_level) < 0:
            return InvalidationReason.LEVEL_BREACHED
        if self._expired(view, plan):
            return InvalidationReason.WATCH_TIMEOUT
        return None


# ----------------------------------------------------------------------------- C
class ParticipationBreakout(V4Detector):
    v4_family = V4Family.PARTICIPATION_BREAKOUT

    def event_at(self, j: int) -> Event | None:
        p = self.cfg.events.participation_breakout
        s = self.s
        f = self.ff
        c, lvl = f.v("close", j), (f.v("hi48", j) if s > 0 else f.v("lo48", j))
        vz, oz, tz, fz, pz = (
            f.v("vol_z", j),
            f.v("oi_chg_4h_z", j),
            f.v("taker_1h_z", j),
            f.v("fund_z", j),
            f.v("prem_z", j),
        )
        if not self._ok(c, lvl, vz, oz, tz, f.v("atr", j)):
            return None
        if not (
            s * (c - lvl) > 0
            and vz >= p.volume_1h_z
            and oz >= p.oi_change_4h_z
            and s * tz >= p.taker_1h_z
        ):
            return None
        if (self._ok(fz) and s * fz > p.max_crowding_z) or (
            self._ok(pz) and s * pz > p.max_crowding_z
        ):
            return None
        height = f.v("hi48", j) - f.v("lo48", j)
        obj = lvl + s * height if not math.isnan(height) else None
        return Event(self.v4_family, self.side, j, (vz + oz + s * tz) / 3.0, lvl, obj)

    def _plan(self, ev: Event, view: MarketView) -> SetupPlan | None:
        p = self.cfg.events.participation_breakout
        g = self.cfg.geometry
        s = self.s
        atr = self.ff.v("atr", ev.j)
        lvl = ev.anchor
        zone = self._zone(lvl - s * g.zone_inner_atr * atr, lvl + s * g.zone_width_atr * atr)
        return self.make_plan(
            ev,
            view,
            zone,
            invalidation=lvl - s * p.struct_buffer_atr * atr * 0.5,
            structural_stop=lvl - s * p.struct_buffer_atr * atr,
            watch_hours=p.watch_hours,
            notes={"level": lvl, "event_close_ms": int(self.ff.close_ms[ev.j])},
            stop_reason="LEVEL_MINUS_BUFFER_OR_VOL_FLOOR",
        )

    def confirmed(self, view: MarketView, plan: SetupPlan) -> bool:
        p = self.cfg.events.participation_breakout
        s = self.s
        lvl, atr = float(plan.notes["level"]), plan.atr_setup_tf
        if view.close_ms(M15) <= int(plan.notes["event_close_ms"]) or self._expired(view, plan):
            return False
        try:
            closes = [view.close(M15, k) for k in range(p.accept_bars_15m)]
            times = [view.close_ms(M15, k) for k in range(p.accept_bars_15m)]
        except IndexError:
            return False
        if min(times) <= int(plan.notes["event_close_ms"]):
            return False
        return (
            all(s * (x - lvl) > 0 for x in closes)
            and s * (closes[0] - lvl) <= p.max_chase_atr * atr
        )

    def pre_entry_invalidated(self, view: MarketView, plan: SetupPlan) -> InvalidationReason | None:
        if self.s * (view.close(H1) - plan.invalidation_level) < 0:
            return InvalidationReason.LEVEL_BREACHED
        if self._expired(view, plan):
            return InvalidationReason.WATCH_TIMEOUT
        return None


DETECTORS: dict[V4Family, type[V4Detector]] = {
    V4Family.DELEVERAGING_REVERSAL: DeleveragingReversal,
    V4Family.POSITIONING_RESET_CONTINUATION: PositioningResetContinuation,
    V4Family.PARTICIPATION_BREAKOUT: ParticipationBreakout,
}


def build_v4_detectors(cfg: V4Config, ff: FeatureFrame) -> list[V4Detector]:
    out: list[V4Detector] = []
    for fam in cfg.episode.family_priority:
        for side in (Side.LONG, Side.SHORT):
            out.append(DETECTORS[fam](cfg, side, ff))
    return out
