"""V6 setup families (`v6-event-1`): Stage A event detection (vectorised, PIT at the 5m close) and
the Stage B anti-chase episode state machine per family. Every threshold comes from the config;
nothing here looks at outcomes. States: WATCH (event seen, waiting for an executable trigger),
ACTIONABLE (trigger condition met -> the engine enters at the next 5m open), EXTENDED (price too
far from the reference: wait, never chase), INVALIDATED (thesis failed), EXPIRED (max age)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np
from numpy.typing import NDArray

from btc_swing.core.enums import Side
from btc_swing.v6.config import V6Config, V6Family
from btc_swing.v6.features import V6Frame

F = NDArray[np.float64]
B = NDArray[np.bool_]


class EpState(StrEnum):
    WATCH = "WATCH"
    ACTIONABLE = "ACTIONABLE"
    EXTENDED = "EXTENDED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    TRIGGERED = "TRIGGERED"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


@dataclass
class Trigger:
    ref_close: float
    structural_stop: float
    reason: str


@dataclass
class Episode:
    episode_id: int
    family: V6Family
    side: Side
    event_k: int
    event_ms: int
    strength: float
    refs: dict[str, float]
    state: EpState = EpState.WATCH
    state_path: list[str] = field(default_factory=list)
    first_actionable_k: int = -1
    end_k: int = -1
    end_reason: str = ""
    trade_id: int = -1
    extended_bars: int = 0
    scratch: dict[str, float] = field(default_factory=dict)

    def set_state(self, st: EpState, k: int, reason: str = "") -> None:
        if st != self.state:
            self.state_path.append(f"{st.value}@{k}" + (f":{reason}" if reason else ""))
        self.state = st
        if st in (EpState.INVALIDATED, EpState.EXPIRED, EpState.CLOSED):
            self.end_k, self.end_reason = k, reason

    def as_row(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "family": self.family.value,
            "side": self.side.value,
            "event_k": self.event_k,
            "event_ms": self.event_ms,
            "strength": self.strength,
            "state": self.state.value,
            "first_actionable_k": self.first_actionable_k,
            "end_k": self.end_k,
            "end_reason": self.end_reason,
            "trade_id": self.trade_id,
            "extended_bars": self.extended_bars,
            "path": " > ".join(self.state_path),
            **{f"ref_{k}": v for k, v in self.refs.items()},
        }


def _nz(x: F) -> F:
    return np.where(np.isnan(x), 0.0, x)


def first_in_cluster(ev: B, cluster_bars: int) -> B:
    """True where an event has no same-family/side event in the previous `cluster_bars` rows."""
    n = len(ev)
    out = np.zeros(n, dtype=bool)
    last = -(10**9)
    for i in np.flatnonzero(ev):
        out[i] = i - last > cluster_bars
        last = int(i)
    return out


# ----------------------------------------------------------------------------- detectors
class Detector:
    """One family/side. `events` (bool per bar) and `strength` are vectorised over the frame; the
    per-episode Stage B logic runs bar by bar through `step`."""

    family: V6Family

    def __init__(self, cfg: V6Config, fr: V6Frame, side: Side) -> None:
        self.cfg, self.fr, self.side = cfg, fr, side
        self.s = side.sign
        self.c, self.h, self.lo = fr.cols["close"], fr.cols["high"], fr.cols["low"]
        self.atr5, self.atr15 = fr.cols["atr5"], fr.cols["atr15m"]
        self.events, self.strength, self.refs = self.detect()

    # vectorised Stage A ------------------------------------------------------------
    def detect(self) -> tuple[B, F, dict[str, F]]:
        raise NotImplementedError

    def open_episode(self, k: int, episode_id: int) -> Episode:
        refs = {name: float(arr[k]) for name, arr in self.refs.items()}
        return Episode(
            episode_id,
            self.family,
            self.side,
            k,
            int(self.fr.close_ms[k]),
            float(self.strength[k]),
            refs,
        )

    # Stage B -------------------------------------------------------------------------
    def step(self, ep: Episode, k: int) -> Trigger | None:
        age = k - ep.event_k
        if age > self.cfg.episode.max_age_bars:
            ep.set_state(EpState.EXPIRED, k, "MAX_AGE")
            return None
        if (
            ep.first_actionable_k >= 0
            and k - ep.first_actionable_k > self.cfg.episode.entry_window_bars
        ):
            ep.set_state(EpState.EXPIRED, k, "ENTRY_WINDOW")
            return None
        if not self.fr.valid[k] or math.isnan(self.atr5[k]) or math.isnan(self.atr15[k]):
            ep.set_state(EpState.INVALIDATED, k, "DATA_GAP")
            return None
        return self._step(ep, k)

    def _step(self, ep: Episode, k: int) -> Trigger | None:
        raise NotImplementedError

    def _trigger(self, ep: Episode, k: int, stop: float, reason: str) -> Trigger:
        if ep.first_actionable_k < 0:
            ep.first_actionable_k = k
        ep.set_state(EpState.ACTIONABLE, k, reason)
        return Trigger(float(self.c[k]), stop, reason)


class MomentumContinuation(Detector):
    family = V6Family.MOMENTUM_CONTINUATION

    def detect(self) -> tuple[B, F, dict[str, F]]:
        fc = self.cfg.families.momentum_continuation
        fr, s = self.fr, self.s
        c, atr5 = self.c, self.atr5
        # level = extreme of the 48 bars before the 15m bar started (15m bar = rows k-2..k)
        lvl_src = fr.cols["hi_4h"] if s > 0 else fr.cols["lo_4h"]
        level = np.full(len(c), np.nan)
        level[2:] = lvl_src[:-2]
        ext = s * (c - level) / atr5
        ev = (
            (fr.cols["bar15_complete"] == 1.0)
            & (ext > 0)
            & (ext <= fc.max_extension_atr5)
            & (fr.cols["rel_vol_15m"] >= fc.rel_vol_15m_min)
            & (s * fr.cols["imbalance_15m"] > 0)
            & (s * fr.cols["cvd_slope_1h"] > 0)
            & (fr.cols["oi_chg_1h"] > 0)
            & (s * fr.cols["trend_1h"] >= 0)
        )
        ev = np.asarray(ev & fr.valid, dtype=bool)
        return ev, _nz(fr.cols["rel_vol_15m"]), {"level": level, "extension_atr5": ext}

    def _step(self, ep: Episode, k: int) -> Trigger | None:
        fc = self.cfg.families.momentum_continuation
        s, c, lo, h = self.s, self.c, self.lo, self.h
        level = ep.refs["level"]
        a5 = self.atr5[k]
        d = s * (c[k] - level) / a5
        if d < -fc.invalidation_atr5:
            ep.set_state(EpState.INVALIDATED, k, "FAILED_BREAK")
            return None
        if s * (c[k] - c[k - 1]) < 0:
            ep.scratch["down_closes"] = ep.scratch.get("down_closes", 0.0) + 1.0
        struct = (
            float(np.nanmin(lo[ep.event_k - 2 : k + 1]))
            if s > 0
            else float(np.nanmax(h[ep.event_k - 2 : k + 1]))
        )
        stop = struct - s * self.cfg.stops.buffer_atr5 * a5
        if 0 <= d <= fc.zone_atr5:
            return self._trigger(ep, k, stop, "PULLBACK_TO_LEVEL")
        hl = (
            ep.scratch.get("down_closes", 0.0) > 0
            and s * (lo[k] - lo[k - 1] if s > 0 else h[k - 1] - h[k]) > 0
            and s * (c[k] - c[k - 1]) > 0
            and d <= 2.0 * fc.zone_atr5
        )
        if hl:
            return self._trigger(ep, k, stop, "HIGHER_LOW" if s > 0 else "LOWER_HIGH")
        if d > fc.max_extension_atr5:
            ep.extended_bars += 1
            ep.set_state(EpState.EXTENDED, k, "")
        elif ep.state is EpState.EXTENDED:
            ep.set_state(EpState.WATCH, k, "")
        return None


class PullbackContinuation(Detector):
    family = V6Family.PULLBACK_CONTINUATION

    def detect(self) -> tuple[B, F, dict[str, F]]:
        fc = self.cfg.families.pullback_continuation
        fr, s, c = self.fr, self.s, self.c
        nb = fc.impulse_bars
        move = s * (c - np.concatenate([np.full(nb, np.nan), c[:-nb]]))
        imp = move / fr.cols["atr1h"]
        ok = np.asarray(
            (imp >= fc.impulse_min_atr1h)
            & (s * fr.cols["imbalance_1h"] > 0)
            & (fr.cols["oi_chg_1h"] > 0)
            & fr.valid,
            dtype=bool,
        )
        hi = self.h
        lo = self.lo
        top = np.full(len(c), np.nan)
        origin = np.full(len(c), np.nan)
        for k in np.flatnonzero(ok):
            w0 = max(0, k - nb)
            if s > 0:
                top[k], origin[k] = np.nanmax(hi[w0 : k + 1]), np.nanmin(lo[w0 : k + 1])
            else:
                top[k], origin[k] = np.nanmin(lo[w0 : k + 1]), np.nanmax(hi[w0 : k + 1])
        return ok, _nz(imp), {"top": top, "origin": origin}

    def _step(self, ep: Episode, k: int) -> Trigger | None:
        fc = self.cfg.families.pullback_continuation
        fr, s, c, h, lo = self.fr, self.s, self.c, self.h, self.lo
        a5 = self.atr5[k]
        top, origin = ep.refs["top"], ep.refs["origin"]
        in_zone = ep.scratch.get("in_zone", 0.0) > 0
        if not in_zone and s * ((h[k] if s > 0 else lo[k]) - top) > 0:
            top = float(h[k] if s > 0 else lo[k])  # impulse still extending
            ep.refs["top"] = top
            ep.scratch["pb_ext"] = top
            ep.scratch["pb_bars"] = 0.0
            ep.scratch["pb_relvol"] = 0.0
            return None
        pb_ext = ep.scratch.get("pb_ext", top)
        pb_ext = min(pb_ext, lo[k]) if s > 0 else max(pb_ext, h[k])
        ep.scratch["pb_ext"] = pb_ext
        ep.scratch["pb_bars"] = ep.scratch.get("pb_bars", 0.0) + 1.0
        ep.scratch["pb_relvol"] = ep.scratch.get("pb_relvol", 0.0) + float(
            _nz(np.array([fr.cols["rel_vol_5m"][k]]))[0]
        )
        rng = s * (top - origin)
        if rng <= 0:
            ep.set_state(EpState.INVALIDATED, k, "NO_RANGE")
            return None
        retr = s * (top - pb_ext) / rng
        if retr >= fc.retrace_invalid:
            ep.set_state(EpState.INVALIDATED, k, "RETRACE_TOO_DEEP")
            return None
        if retr >= fc.retrace_min:
            ep.scratch["in_zone"] = 1.0
            in_zone = True
        if not in_zone:
            return None
        mean_rv = ep.scratch["pb_relvol"] / max(ep.scratch["pb_bars"], 1.0)
        reclaim = s * (c[k] - fr.cols["ema_fast_15m"][k]) > 0 or (
            s * (c[k] - (fr.cols["swing_high_5m"][k] if s > 0 else fr.cols["swing_low_5m"][k])) > 0
        )
        if (
            retr <= fc.retrace_max
            and s * fr.cols["imbalance_5m"][k] > 0
            and reclaim
            and mean_rv <= fc.pullback_max_rel_vol
        ):
            stop = pb_ext - s * self.cfg.stops.buffer_atr5 * a5
            return self._trigger(ep, k, stop, "PULLBACK_RECLAIM")
        return None


class FailedBreakoutReversal(Detector):
    family = V6Family.FAILED_BREAKOUT_REVERSAL

    def detect(self) -> tuple[B, F, dict[str, F]]:
        fc = self.cfg.families.failed_breakout_reversal
        fr, s, c, h, lo = self.fr, self.s, self.c, self.h, self.lo
        n = len(c)
        # a SHORT reversal follows a failed UPSIDE break (s < 0): breakout extreme = high
        if s < 0:
            level = fr.cols["hi_24h"]
            cand = (h > level) & (s * fr.cols["imbalance_5m"] <= -fc.imbalance_5m_min)
        else:
            level = fr.cols["lo_24h"]
            cand = (lo < level) & (fr.cols["imbalance_5m"] <= -fc.imbalance_5m_min)
        cand = np.asarray(
            cand & (fr.cols["rel_vol_5m"] >= fc.rel_vol_5m_min) & fr.valid, dtype=bool
        )
        nonconf = (s * fr.cols["cvd_slope_15m"] <= 0) | (fr.cols["oi_chg_15m"] <= 0)
        ev = np.zeros(n, dtype=bool)
        lvl = np.full(n, np.nan)
        sweep = np.full(n, np.nan)
        brk = np.full(n, np.nan)
        strength = np.zeros(n)
        for k in np.flatnonzero(cand):
            for d in range(1, fc.reentry_bars + 1):
                j = k + d
                if j >= n or not fr.valid[j]:
                    break
                back_inside = s * (c[j] - level[k]) > 0  # short: close below the old high
                if back_inside and nonconf[j]:
                    ev[j] = True
                    lvl[j] = level[k]
                    sweep[j] = np.nanmax(h[k : j + 1]) if s < 0 else np.nanmin(lo[k : j + 1])
                    brk[j] = lo[k] if s < 0 else h[k]
                    strength[j] = float(fr.cols["rel_vol_5m"][k])
                    break
        return ev, strength, {"level": lvl, "sweep_extreme": sweep, "break_bar_extreme": brk}

    def _step(self, ep: Episode, k: int) -> Trigger | None:
        s, c = self.s, self.c
        a5 = self.atr5[k]
        sweep, brk = ep.refs["sweep_extreme"], ep.refs["break_bar_extreme"]
        if s * (c[k] - sweep) < 0:  # short: close above the sweep high -> breakout succeeded
            ep.set_state(EpState.INVALIDATED, k, "BREAKOUT_SUCCEEDED")
            return None
        if s * (c[k] - brk) > 0:  # short: close below the breakout bar's low
            stop = sweep - s * self.cfg.stops.buffer_atr5 * a5
            return self._trigger(ep, k, stop, "REVERSAL_CONFIRMED")
        return None


class FlowOiImpulse(Detector):
    family = V6Family.FLOW_OI_IMPULSE

    def detect(self) -> tuple[B, F, dict[str, F]]:
        fc = self.cfg.families.flow_oi_impulse
        fr, s, c = self.fr, self.s, self.c
        ret15_atr = s * (c - lagged3(c)) / fr.cols["atr15m"]
        ev = (
            (fr.cols["bar15_complete"] == 1.0)
            & (ret15_atr >= fc.ret_15m_min_atr15)
            & (fr.cols["rel_vol_15m"] >= fc.rel_vol_15m_min)
            & (s * fr.cols["imbalance_15m"] >= fc.imbalance_15m_min)
            & (s * fr.cols["cvd_slope_15m"] > 0)
            & (fr.cols["rel_absoi_15m"] >= fc.oi_rel_min)
            & (fr.cols["oi_chg_15m"] > 0)
            & ~(s * fr.cols["prem_z"] > fc.max_crowding_prem_z)
        )
        ev = np.asarray(ev & fr.valid, dtype=bool)
        origin = np.full(len(c), np.nan)
        lo, h = self.lo, self.h
        for k in np.flatnonzero(ev):
            origin[k] = np.nanmin(lo[k - 2 : k + 1]) if s > 0 else np.nanmax(h[k - 2 : k + 1])
        return ev, _nz(ret15_atr), {"origin": origin, "ret15_atr": ret15_atr}

    def _step(self, ep: Episode, k: int) -> Trigger | None:
        fc = self.cfg.families.flow_oi_impulse
        s, c, h, lo = self.s, self.c, self.h, self.lo
        a5 = self.atr5[k]
        origin = ep.refs["origin"]
        if s * (c[k] - origin) < 0:
            ep.set_state(EpState.INVALIDATED, k, "BACK_TO_ORIGIN")
            return None
        ext = h[k] if s > 0 else lo[k]
        prev_ext = h[k - 1] if s > 0 else lo[k - 1]
        pause_bars = ep.scratch.get("pause_bars", 0.0)
        pause_high = ep.scratch.get("pause_high", math.nan)
        if pause_bars > 0 and not math.isnan(pause_high) and s * (c[k] - pause_high) > 0:
            if pause_bars <= fc.pause_max_bars:
                stop = origin - s * self.cfg.stops.buffer_atr5 * a5
                return self._trigger(ep, k, stop, "CONTINUATION_AFTER_PAUSE")
            ep.set_state(EpState.INVALIDATED, k, "PAUSE_TOO_LONG")
            return None
        if s * (ext - prev_ext) <= 0:  # no new extreme: a pause bar
            ep.scratch["pause_bars"] = pause_bars + 1.0
            ep.scratch["pause_high"] = (
                (
                    float(max(pause_high, prev_ext))
                    if not math.isnan(pause_high)
                    else float(prev_ext)
                )
                if s > 0
                else (
                    float(min(pause_high, prev_ext))
                    if not math.isnan(pause_high)
                    else float(prev_ext)
                )
            )
            if ep.scratch["pause_bars"] > fc.pause_max_bars:
                ep.set_state(EpState.INVALIDATED, k, "PAUSE_TOO_LONG")
            elif ep.state is EpState.EXTENDED:
                ep.set_state(EpState.WATCH, k, "")
            return None
        if pause_bars == 0 and k - ep.event_k > fc.pause_wait_bars:
            ep.extended_bars += 1
            ep.set_state(EpState.EXTENDED, k, "")
        return None


def lagged3(c: F) -> F:
    out = np.full(len(c), np.nan)
    out[3:] = c[:-3]
    return out


DETECTORS: dict[V6Family, type[Detector]] = {
    V6Family.MOMENTUM_CONTINUATION: MomentumContinuation,
    V6Family.PULLBACK_CONTINUATION: PullbackContinuation,
    V6Family.FAILED_BREAKOUT_REVERSAL: FailedBreakoutReversal,
    V6Family.FLOW_OI_IMPULSE: FlowOiImpulse,
}


def build_detectors(
    cfg: V6Config, fr: V6Frame, families: list[V6Family] | None = None
) -> list[Detector]:
    fams = families if families is not None else cfg.enabled_families()
    out: list[Detector] = []
    for fam in fams:
        for side in (Side.LONG, Side.SHORT):
            out.append(DETECTORS[fam](cfg, fr, side))
    return out
