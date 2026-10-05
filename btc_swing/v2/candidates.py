"""V2 candidate generator: V1 setups as candidate generators, not trade decisions.

Each of the eight V1 families runs its own V1 episode lifecycle (one `EpisodeManager` with one
detector): WATCH -> zone reached and confirmed -> ENTRY_READY -> 5m trigger -> TRIGGERED. A
TRIGGERED episode is recorded as a *candidate opportunity* at the close of the trigger bar; the
family then observes the V1 post-close cooldown and anchor de-duplication, exactly as after a V1
trade. No slot is occupied, so candidates are not censored by other families or by open trades.
The frozen V1 risk rule is applied at the decision bar; rejected candidates are kept (flagged) but
carry no label. Features (`v2-fs-1`) are computed at the same decision bar from the same PIT view.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl

from btc_swing.core.config import BtcStrategyConfig
from btc_swing.core.enums import Regime, Side
from btc_swing.episodes.state_machine import Episode, EpisodeManager
from btc_swing.features.context import AuxSeries
from btc_swing.features.view import MultiTfSeries
from btc_swing.regime.classifier import classify_regime
from btc_swing.risk.sizing import Sizing, size_position
from btc_swing.setups.base import SetupPlan
from btc_swing.setups.registry import build_detectors
from btc_swing.v2.features import compute_features

log = logging.getLogger(__name__)
WEEK_BARS = 7 * 288


@dataclass
class Candidate:
    candidate_id: int
    family: str
    side: Side
    plan: SetupPlan
    episode_id: int
    detected_bar: int
    detected_ms: int
    confirmed_bar: int | None
    trigger_bar: int
    trigger_ms: int
    close_at_trigger: float
    regime_at_detection: Regime
    regime_at_trigger: Regime
    regime_age_bars: int
    regime_transitions_7d: int
    sizing: Sizing
    aux: dict[str, float] = field(default_factory=dict)
    features: dict[str, float] = field(default_factory=dict)

    @property
    def risk_accepted(self) -> bool:
        return self.sizing.accepted

    def as_row(self) -> dict[str, Any]:
        p = self.plan
        return {
            "candidate_id": self.candidate_id,
            "family": self.family,
            "side": self.side.value,
            "episode_id": self.episode_id,
            "detected_bar": self.detected_bar,
            "detected_ms": self.detected_ms,
            "confirmed_bar": self.confirmed_bar,
            "trigger_bar": self.trigger_bar,
            "trigger_ms": self.trigger_ms,
            "close_at_trigger": self.close_at_trigger,
            "regime_at_detection": self.regime_at_detection.value,
            "regime_at_trigger": self.regime_at_trigger.value,
            "regime_age_bars": self.regime_age_bars,
            "regime_transitions_7d": self.regime_transitions_7d,
            "anchor": p.anchor,
            "entry_zone_low": p.entry_zone_low,
            "entry_zone_high": p.entry_zone_high,
            "invalidation_level": p.invalidation_level,
            "stop_price": p.stop_price,
            "stop_reason": p.stop_reason,
            "structural_target": p.structural_target,
            "atr_setup_tf": p.atr_setup_tf,
            "risk_accepted": self.sizing.accepted,
            "risk_reason": self.sizing.reason,
            "leverage": self.sizing.leverage,
            "short_in_trend_down": self.side is Side.SHORT
            and self.regime_at_trigger is Regime.TREND_DOWN,
            **{f"aux_{k}": v for k, v in self.aux.items()},
            **{f"x_{k}": v for k, v in self.features.items()},
        }


@dataclass
class CandidateRun:
    candidates: list[Candidate]
    decisions: pl.DataFrame  # t_ms, regime (the PIT regime journal; used by the null benchmark)
    n_bars: int
    episode_outcomes: dict[str, int]


def _confirmed_bar(ep: Episode) -> int | None:
    for tr in ep.transitions:
        if tr["to"] == "ENTRY_READY":
            return int(tr["bar"])
    return None


def generate_candidates(
    cfg: BtcStrategyConfig,
    series: MultiTfSeries,
    aux: AuxSeries,
    start_ms: int,
    end_ms: int,
    cooldown_after_candidate: int,
) -> CandidateRun:
    base = series.base
    n = len(base)
    i0 = int(np.searchsorted(base.close_ms, start_ms, side="left"))
    i1 = int(np.searchsorted(base.close_ms, end_ms, side="right"))
    managers = {
        d.family: EpisodeManager(cfg.episode, [d], "CONFIRMED_TRIGGER")
        for d in build_detectors(cfg)
    }
    cands: list[Candidate] = []
    t_journal: list[int] = []
    r_journal: list[str] = []
    outcomes: dict[str, int] = {}
    cur_regime: Regime | None = None
    regime_since = i0
    changes: deque[int] = deque()
    next_id = 1
    for i in range(i0, min(i1, n)):
        t = int(base.close_ms[i])
        c = float(base.close[i])
        view = series.view_at(t)
        reg = classify_regime(view, cfg.regime).regime
        if reg is not cur_regime:
            if cur_regime is not None:
                changes.append(i)
            cur_regime, regime_since = reg, i
        while changes and changes[0] < i - WEEK_BARS:
            changes.popleft()
        t_journal.append(t)
        r_journal.append(reg.value)
        for fam, mgr in managers.items():
            action = mgr.step(view, reg, i)
            ep = action.episode
            if action.kind == "INVALIDATED" and ep is not None:
                outcomes[ep.end_reason or "INVALIDATED"] = (
                    outcomes.get(ep.end_reason or "INVALIDATED", 0) + 1
                )
            if action.kind != "ENTER" or ep is None:
                continue
            plan = ep.plan
            atr_liq = (
                view.ind(cfg.risk.liquidation_atr_tf, "atr")
                if view.warm(cfg.risk.liquidation_atr_tf)
                else math.nan
            )
            sizing = size_position(
                cfg.risk.initial_equity, c, plan.stop_price, plan.side, atr_liq, cfg.risk
            )
            snap = aux.snapshot(t, view, series)
            cand = Candidate(
                candidate_id=next_id,
                family=fam.value,
                side=plan.side,
                plan=plan,
                episode_id=ep.episode_id,
                detected_bar=ep.opened_bar,
                detected_ms=plan.detected_at_ms,
                confirmed_bar=_confirmed_bar(ep),
                trigger_bar=i,
                trigger_ms=t,
                close_at_trigger=c,
                regime_at_detection=ep.regime_at_detection,
                regime_at_trigger=reg,
                regime_age_bars=i - regime_since,
                regime_transitions_7d=len(changes),
                sizing=sizing,
                aux=snap,
            )
            cand.features = compute_features(cand, view, series, aux, snap)
            cands.append(cand)
            next_id += 1
            outcomes["CANDIDATE"] = outcomes.get("CANDIDATE", 0) + 1
            # the family observes the V1 post-close cooldown; no slot is occupied
            mgr.mark_closed(ep, i, t, "CANDIDATE_RECORDED")
            mgr.cooldown_until[fam] = i + cooldown_after_candidate
    decisions = pl.DataFrame({"t_ms": t_journal, "regime": r_journal})
    log.info("candidates: %d over %d bars", len(cands), min(i1, n) - i0)
    return CandidateRun(cands, decisions, min(i1, n) - i0, outcomes)


def candidates_frame(run: CandidateRun) -> pl.DataFrame:
    return pl.DataFrame([c.as_row() for c in run.candidates]) if run.candidates else pl.DataFrame()
