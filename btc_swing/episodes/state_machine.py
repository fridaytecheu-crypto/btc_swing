"""Persistent TRADE_SETUP episodes and their state machine.

NO_SETUP -> WATCH -> ENTRY_READY -> TRIGGERED -> ACTIVE -> CLOSED
                 \\-> INVALIDATED (level breached, timeout, regime, ran without us, risk rejected)
ENTRY_READY falls back to WATCH when the trigger does not come in time. One episode = at most one
trade. Repeated 5m signals never create new episodes while one is open; after an episode ends, a
per-family cooldown and a structural-anchor de-duplication window apply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from btc_swing.core.config import EpisodeCfg
from btc_swing.core.enums import EpisodeState, InvalidationReason, Regime, SetupFamily
from btc_swing.features.view import MarketView
from btc_swing.setups.base import SetupDetector, SetupPlan


@dataclass
class Episode:
    episode_id: int
    plan: SetupPlan
    regime_at_detection: Regime
    opened_bar: int
    state: EpisodeState = EpisodeState.WATCH
    state_since_bar: int = 0
    transitions: list[dict[str, Any]] = field(default_factory=list)
    trade_id: int | None = None
    end_reason: str | None = None
    closed_bar: int | None = None
    features: dict[str, float] = field(default_factory=dict)

    @property
    def outcome_class(self) -> str:
        """TRADED | INVALIDATED | RISK_REJECTED | NEVER_TRIGGERED (incl. EXPIRED watch)."""
        if self.trade_id is not None:
            return "TRADED"
        r = self.end_reason or ""
        if r.startswith(InvalidationReason.RISK_REJECTED.value):
            return "RISK_REJECTED"
        if r == InvalidationReason.LEVEL_BREACHED.value:
            return "INVALIDATED"
        return "NEVER_TRIGGERED"

    @property
    def reached_entry_ready(self) -> bool:
        return any(t["to"] == EpisodeState.ENTRY_READY.value for t in self.transitions)

    def set_state(self, state: EpisodeState, bar: int, t_ms: int, reason: str) -> None:
        self.transitions.append(
            {
                "t_ms": t_ms,
                "bar": bar,
                "from": self.state.value,
                "to": state.value,
                "reason": reason,
            }
        )
        self.state = state
        self.state_since_bar = bar
        if state in (EpisodeState.INVALIDATED, EpisodeState.CLOSED):
            self.end_reason = reason
            self.closed_bar = bar

    def as_row(self) -> dict[str, Any]:
        p = self.plan
        return {
            "episode_id": self.episode_id,
            "family": p.family.value,
            "side": p.side.value,
            "regime_at_detection": self.regime_at_detection.value,
            "detected_at_ms": p.detected_at_ms,
            "opened_bar": self.opened_bar,
            "closed_bar": self.closed_bar,
            "final_state": self.state.value,
            "end_reason": self.end_reason,
            "trade_id": self.trade_id,
            "anchor": p.anchor,
            "entry_zone_low": p.entry_zone_low,
            "entry_zone_high": p.entry_zone_high,
            "invalidation_level": p.invalidation_level,
            "stop_price": p.stop_price,
            "stop_distance_ref": p.stop_distance_ref,
            "stop_reason": p.stop_reason,
            "structural_target": p.structural_target,
            "n_transitions": len(self.transitions),
            "episode_bars": (self.closed_bar - self.opened_bar)
            if self.closed_bar is not None
            else None,
            "outcome_class": self.outcome_class,
            "reached_entry_ready": self.reached_entry_ready,
            "expired": self.end_reason == InvalidationReason.WATCH_TIMEOUT.value,
            "run_away_level": p.run_away_level,
            **{f"f_{k}": v for k, v in self.features.items()},
        }


@dataclass
class Action:
    kind: str  # NONE | ENTER | INVALIDATED
    episode: Episode | None = None


class EpisodeManager:
    def __init__(
        self,
        cfg: EpisodeCfg,
        detectors: list[SetupDetector],
        entry_mode: str = "CONFIRMED_TRIGGER",
    ) -> None:
        self.cfg = cfg
        self.entry_mode = entry_mode
        self.detectors = {d.family: d for d in detectors}
        self.current: Episode | None = None
        self.closed: list[Episode] = []
        self._next_id = 1
        self.cooldown_until: dict[SetupFamily, int] = {}
        self.recent_anchors: dict[SetupFamily, list[tuple[float, int]]] = {}
        # setups detected while another episode/position occupied the single slot (missed opportunities)
        self.shadow: list[dict[str, Any]] = []
        self._last_shadow_anchor: dict[SetupFamily, float] = {}

    # ----------------------------------------------------------------- helpers
    def _blocked(self, fam: SetupFamily, plan: SetupPlan, bar: int) -> bool:
        if bar < self.cooldown_until.get(fam, -1):
            return True
        if self.cfg.dedupe_same_anchor:
            tol = self.cfg.anchor_tolerance_atr * plan.atr_setup_tf
            for anchor, until in self.recent_anchors.get(fam, []):
                if bar < until and abs(anchor - plan.anchor) <= tol:
                    return True
        return False

    def _end(self, ep: Episode, bar: int, cooldown: int) -> None:
        fam = ep.plan.family
        self.cooldown_until[fam] = bar + cooldown
        anchors = [(a, u) for a, u in self.recent_anchors.get(fam, []) if u > bar]
        anchors.append((ep.plan.anchor, bar + max(cooldown, self.cfg.watch_timeout_bars)))
        self.recent_anchors[fam] = anchors
        self.closed.append(ep)
        self.current = None

    # ----------------------------------------------------------------- public
    def step(self, view: MarketView, regime: Regime, bar: int) -> Action:
        """Advance the current episode (pre-entry states only) or open a new one."""
        ep = self.current
        t = view.t_ms
        if ep is None:
            for fam, det in self.detectors.items():
                plan = det.detect(view, regime)
                if plan is None or self._blocked(fam, plan, bar):
                    continue
                ep = Episode(self._next_id, plan, regime, bar, EpisodeState.WATCH, bar)
                ep.transitions.append(
                    {"t_ms": t, "bar": bar, "from": "NO_SETUP", "to": "WATCH", "reason": "detected"}
                )
                self._next_id += 1
                self.current = ep
                return Action("NONE", ep)
            return Action("NONE", None)
        self._record_shadow(view, regime, bar, ep)
        if ep.state in (EpisodeState.TRIGGERED, EpisodeState.ACTIVE):
            return Action("NONE", ep)  # the engine owns these states
        det = self.detectors[ep.plan.family]
        inv = det.pre_entry_invalidated(view, ep.plan)
        if inv is not None:
            return self._invalidate(ep, bar, t, inv.value)
        if not det.eligible(regime):
            return self._invalidate(ep, bar, t, InvalidationReason.REGIME_INELIGIBLE.value)
        if bar - ep.opened_bar > self.cfg.watch_timeout_bars:
            return self._invalidate(ep, bar, t, InvalidationReason.WATCH_TIMEOUT.value)
        if ep.state is EpisodeState.WATCH:
            if self.entry_mode == "ZONE_ENTRY":
                # Phase 2.1 variant: the pre-defined zone is reached while the plan is valid -> enter
                if det.zone_reached(view, ep.plan):
                    ep.set_state(EpisodeState.TRIGGERED, bar, t, "zone_reached")
                    return Action("ENTER", ep)
                return Action("NONE", ep)
            if det.zone_reached(view, ep.plan) and det.confirmed(view, ep.plan):
                ep.set_state(EpisodeState.ENTRY_READY, bar, t, "zone_reached_and_confirmed")
            return Action("NONE", ep)
        if ep.state is EpisodeState.ENTRY_READY:
            if det.triggered(view, ep.plan):
                ep.set_state(EpisodeState.TRIGGERED, bar, t, "entry_trigger")
                return Action("ENTER", ep)
            if bar - ep.state_since_bar > self.cfg.entry_ready_timeout_bars:
                ep.set_state(EpisodeState.WATCH, bar, t, "entry_ready_timeout")
            return Action("NONE", ep)
        return Action("NONE", ep)

    def _record_shadow(self, view: MarketView, regime: Regime, bar: int, current: Episode) -> None:
        for fam, det in self.detectors.items():
            if fam is current.plan.family:
                continue
            plan = det.detect(view, regime)
            if plan is None:
                continue
            tol = self.cfg.anchor_tolerance_atr * plan.atr_setup_tf
            last = self._last_shadow_anchor.get(fam)
            if last is not None and abs(last - plan.anchor) <= tol:
                continue
            self._last_shadow_anchor[fam] = plan.anchor
            self.shadow.append(
                {
                    "t_ms": view.t_ms,
                    "bar": bar,
                    "family": fam.value,
                    "side": plan.side.value,
                    "regime": regime.value,
                    "anchor": plan.anchor,
                    "entry_zone_low": plan.entry_zone_low,
                    "entry_zone_high": plan.entry_zone_high,
                    "stop_price": plan.stop_price,
                    "invalidation_level": plan.invalidation_level,
                    "structural_target": plan.structural_target,
                    "blocked_by_episode": current.episode_id,
                    "blocked_by_family": current.plan.family.value,
                    "blocked_by_state": current.state.value,
                }
            )

    def _invalidate(self, ep: Episode, bar: int, t: int, reason: str) -> Action:
        ep.set_state(EpisodeState.INVALIDATED, bar, t, reason)
        self._end(ep, bar, self.cfg.cooldown_bars_after_invalidation)
        return Action("INVALIDATED", ep)

    def mark_active(self, ep: Episode, bar: int, t: int, trade_id: int) -> None:
        ep.trade_id = trade_id
        ep.set_state(EpisodeState.ACTIVE, bar, t, "filled")

    def mark_risk_rejected(self, ep: Episode, bar: int, t: int, detail: str) -> None:
        ep.set_state(
            EpisodeState.INVALIDATED, bar, t, f"{InvalidationReason.RISK_REJECTED.value}:{detail}"
        )
        self._end(ep, bar, self.cfg.cooldown_bars_after_invalidation)

    def mark_closed(self, ep: Episode, bar: int, t: int, reason: str) -> None:
        ep.set_state(EpisodeState.CLOSED, bar, t, reason)
        self._end(ep, bar, self.cfg.cooldown_bars_after_close)
