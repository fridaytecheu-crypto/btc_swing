from __future__ import annotations

from dataclasses import dataclass, field

from btc_swing.core.config import load_btc_config
from btc_swing.core.enums import EpisodeState, InvalidationReason, Regime, SetupFamily, Side
from btc_swing.episodes.state_machine import EpisodeManager
from btc_swing.setups.base import SetupDetector, SetupPlan
from tests.conftest import ROOT


@dataclass
class FakeView:
    t_ms: int = 0


@dataclass
class ScriptedDetector(SetupDetector):
    """Answers are scripted per call so the state machine can be driven deterministically."""

    family: SetupFamily = SetupFamily.TREND_PULLBACK_LONG
    detect_now: bool = False
    zone: bool = False
    confirm: bool = False
    trigger: bool = False
    invalid: InvalidationReason | None = None
    anchor: float = 100.0
    plans_made: list[SetupPlan] = field(default_factory=list)

    def __init__(self) -> None:
        super().__init__([Regime.TREND_UP])
        self.detect_now = self.zone = self.confirm = self.trigger = False
        self.invalid = None
        self.anchor = 100.0
        self.plans_made = []

    def detect(self, view, regime):  # type: ignore[no-untyped-def]
        if not self.detect_now or not self.eligible(regime):
            return None
        p = SetupPlan(
            self.family,
            Side.LONG,
            self.anchor,
            95.0,
            97.0,
            "t",
            93.0,
            "r",
            92.0,
            4.0,
            "s",
            100.0,
            1.0,
            view.t_ms,
        )
        self.plans_made.append(p)
        return p

    def zone_reached(self, view, plan):  # type: ignore[no-untyped-def]
        return self.zone

    def confirmed(self, view, plan):  # type: ignore[no-untyped-def]
        return self.confirm

    def triggered(self, view, plan):  # type: ignore[no-untyped-def]
        return self.trigger

    def pre_entry_invalidated(self, view, plan):  # type: ignore[no-untyped-def]
        return self.invalid


def _mgr() -> tuple[EpisodeManager, ScriptedDetector]:
    cfg = load_btc_config(ROOT / "config" / "btc_swing.default.yaml")
    det = ScriptedDetector()
    return EpisodeManager(cfg.episode, [det]), det


def test_full_lifecycle_is_one_episode_one_trade() -> None:
    m, det = _mgr()
    det.detect_now = True
    bar = 0
    assert m.step(FakeView(), Regime.TREND_UP, bar).kind == "NONE"
    ep = m.current
    assert ep is not None and ep.state is EpisodeState.WATCH
    # repeated 5m signals while WATCH do not create new episodes
    for bar in range(1, 10):
        m.step(FakeView(bar), Regime.TREND_UP, bar)
    assert len(det.plans_made) == 1 and m.current is ep
    det.zone = det.confirm = True
    m.step(FakeView(10), Regime.TREND_UP, 10)
    assert ep.state is EpisodeState.ENTRY_READY
    det.trigger = True
    a = m.step(FakeView(11), Regime.TREND_UP, 11)
    assert a.kind == "ENTER" and ep.state is EpisodeState.TRIGGERED
    m.mark_active(ep, 12, 12, trade_id=1)
    assert ep.state is EpisodeState.ACTIVE
    assert m.step(FakeView(13), Regime.TREND_UP, 13).kind == "NONE"  # engine owns ACTIVE
    m.mark_closed(ep, 50, 50, "STOP")
    assert ep.state is EpisodeState.CLOSED and m.current is None and ep.trade_id == 1
    # cooldown + same-anchor dedupe block an immediate re-detection
    assert m.step(FakeView(51), Regime.TREND_UP, 51).episode is None
    det.anchor = 200.0  # different structure -> allowed once cooldown passed
    assert m.step(FakeView(51 + 12), Regime.TREND_UP, 51 + 12).episode is not None


def test_entry_ready_timeout_reverts_to_watch_then_watch_timeout_invalidates() -> None:
    m, det = _mgr()
    det.detect_now = det.zone = det.confirm = True
    m.step(FakeView(), Regime.TREND_UP, 0)
    m.step(FakeView(), Regime.TREND_UP, 1)
    ep = m.current
    assert ep is not None and ep.state is EpisodeState.ENTRY_READY
    det.zone = det.confirm = False
    m.step(FakeView(), Regime.TREND_UP, 1 + 25)
    assert ep.state is EpisodeState.WATCH
    a = m.step(FakeView(), Regime.TREND_UP, 97)
    assert a.kind == "INVALIDATED" and ep.end_reason == InvalidationReason.WATCH_TIMEOUT.value


def test_regime_change_and_level_breach_invalidate() -> None:
    m, det = _mgr()
    det.detect_now = True
    m.step(FakeView(), Regime.TREND_UP, 0)
    assert m.step(FakeView(), Regime.RANGE, 1).kind == "INVALIDATED"
    assert m.closed[-1].end_reason == InvalidationReason.REGIME_INELIGIBLE.value
    det.anchor = 300.0
    m.step(FakeView(), Regime.TREND_UP, 20)
    det.invalid = InvalidationReason.LEVEL_BREACHED
    assert m.step(FakeView(), Regime.TREND_UP, 21).kind == "INVALIDATED"
    assert m.closed[-1].end_reason == InvalidationReason.LEVEL_BREACHED.value
