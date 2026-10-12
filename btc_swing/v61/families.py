"""V6.1 setup families (`v6.1-event-1`): STRUCTURE -> LOCATION (Stage A, vectorised) -> FLOW
CONFIRMATION -> ENTRY (Stage B, per episode). Every threshold comes from the config; the flow
predicate can be switched off (structure-only control) without touching anything else. Longs are
written; shorts are the exact mirror through the side sign. States reuse the frozen V6 episode."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from btc_swing.core.enums import Side
from btc_swing.v6.families import EpState, first_in_cluster
from btc_swing.v6.features import V6Frame, lagged, rolling_min_prev
from btc_swing.v61.config import V61Config, V61Family

F = NDArray[np.float64]
B = NDArray[np.bool_]


@dataclass
class Episode:
    """V6.1 episode (same lifecycle as the frozen V6 episode, typed for V6.1 families)."""

    episode_id: int
    family: V61Family
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
            "flow_checks": self.scratch.get("flow_checks", 0.0),
            "flow_rejects": self.scratch.get("flow_rejects", 0.0),
            "path": " > ".join(self.state_path),
            **{f"ref_{k}": v for k, v in self.refs.items()},
        }


@dataclass
class Trigger61:
    ref_close: float
    structural_stop: float
    target: float
    reason: str
    flow_ok: bool


def _nz(x: F) -> F:
    return np.where(np.isnan(x), 0.0, x)


def _rolling_max_incl(x: F, w: int) -> F:
    import polars as pl

    return pl.Series(x).fill_nan(None).rolling_max(w, min_samples=w).to_numpy().astype(np.float64)


def _rolling_min_incl(x: F, w: int) -> F:
    import polars as pl

    return pl.Series(x).fill_nan(None).rolling_min(w, min_samples=w).to_numpy().astype(np.float64)


def _rolling_sum_incl(x: F, w: int) -> F:
    import polars as pl

    return pl.Series(x).rolling_sum(w, min_samples=w).to_numpy().astype(np.float64)


class Detector61:
    family: V61Family

    def __init__(self, cfg: V61Config, fr: V6Frame, side: Side, flow_on: bool = True) -> None:
        self.cfg, self.fr, self.side, self.flow_on = cfg, fr, side, flow_on
        self.s = side.sign
        c = fr.cols
        self.c, self.h, self.lo = c["close"], c["high"], c["low"]
        self.atr5, self.atr15 = c["atr5"], c["atr15m"]
        self.imb5 = c["imbalance_5m"]
        self.relvol = c["rel_vol_5m"]
        self.events, self.strength, self.refs = self.detect()

    # mirrored price accessors: "up" extremes for the trade direction
    def ext_fav(self, k: int) -> float:  # favourable extreme of bar k
        return float(self.h[k] if self.s > 0 else self.lo[k])

    def ext_adv(self, k: int) -> float:  # adverse extreme of bar k
        return float(self.lo[k] if self.s > 0 else self.h[k])

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

    def step(self, ep: Episode, k: int) -> Trigger61 | None:
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

    def _step(self, ep: Episode, k: int) -> Trigger61 | None:
        raise NotImplementedError

    def flow(self, k: int, ep: Episode) -> bool:
        raise NotImplementedError

    def _trigger(
        self, ep: Episode, k: int, stop: float, target: float, reason: str
    ) -> Trigger61 | None:
        """Structure says enter; the flow predicate (if on) must agree on this bar, else wait."""
        if ep.first_actionable_k < 0:
            ep.first_actionable_k = k
        ok = (not self.flow_on) or self.flow(k, ep)
        ep.scratch["flow_checks"] = ep.scratch.get("flow_checks", 0.0) + 1.0
        if not ok:
            ep.scratch["flow_rejects"] = ep.scratch.get("flow_rejects", 0.0) + 1.0
            ep.set_state(EpState.WATCH, k, "FLOW_NOT_CONFIRMED")
            return None
        ep.set_state(EpState.ACTIONABLE, k, reason)
        return Trigger61(float(self.c[k]), stop, target, reason, True)


# ----------------------------------------------------------------------------- A
class TrendPullbackReclaim(Detector61):
    family = V61Family.TREND_PULLBACK_RECLAIM

    def detect(self) -> tuple[B, F, dict[str, F]]:
        fc = self.cfg.families.trend_pullback_reclaim
        fr, s, c = self.fr, self.s, self.c
        n = len(c)
        lb = fc.impulse_lookback_bars
        fav = self.h if s > 0 else self.lo
        adv = self.lo if s > 0 else self.h
        # impulse extreme over the last L bars (including current) and its index
        if n < lb + 1:
            z = np.zeros(n, dtype=bool)
            return z, np.zeros(n), {}
        wv = np.lib.stride_tricks.sliding_window_view(np.where(np.isnan(fav), -np.inf * s, fav), lb)
        arg = (np.argmax(wv, axis=1) if s > 0 else np.argmin(wv, axis=1)).astype(np.int64)
        imp_idx = np.full(n, -1, dtype=np.int64)
        imp_idx[lb - 1 :] = np.arange(n - lb + 1) + arg
        imp_ext = np.full(n, np.nan)
        okk = imp_idx >= 0
        imp_ext[okk] = fav[imp_idx[okk]]
        # origin: adverse extreme of the L bars BEFORE the impulse extreme
        origin_src = rolling_min_prev(adv, lb) if s > 0 else -rolling_min_prev(-adv, lb)
        origin = np.full(n, np.nan)
        origin[okk] = origin_src[imp_idx[okk]]
        size = s * (imp_ext - origin)
        atr15 = self.atr15
        with np.errstate(invalid="ignore", divide="ignore"):
            retr = np.where(size > 0, s * (imp_ext - c) / size, np.nan)
        # level proximity: EMA21(15m), session VWAP, broken prior-1h level at the impulse time
        p1 = fr.cols["high_1h"] if s > 0 else fr.cols["low_1h"]
        lvl3 = np.full(n, np.nan)
        lvl3[okk] = p1[imp_idx[okk]]
        lvl3 = np.where(s * (imp_ext - lvl3) > 0, lvl3, np.nan)  # only if the impulse broke it
        prox = fc.level_proximity_atr15 * atr15
        near = (
            (np.abs(c - fr.cols["ema_fast_15m"]) <= prox)
            | (np.abs(c - fr.cols["vwap_day"]) <= prox)
            | (np.abs(c - lvl3) <= prox)
        )
        rng3 = _rolling_max_incl(self.h, 3) - _rolling_min_incl(self.lo, 3)
        rv3 = _rolling_sum_incl(_nz(self.relvol), 3) / 3.0
        lost = (rng3 <= fc.pullback_range_max_atr5 * self.atr5) | (rv3 <= fc.pullback_max_rel_vol)
        bars_after = np.arange(n) - imp_idx
        ev = (
            (fr.cols["regime_1h"] == s)
            & (size >= fc.impulse_min_atr15 * atr15)
            & (retr >= fc.retrace_min)
            & (retr <= fc.retrace_max)
            & near
            & lost
            & (bars_after >= fc.min_bars_after_high)
            & fr.valid
        )
        ev = np.asarray(ev, dtype=bool)
        return (
            ev,
            _nz(size / atr15),
            {
                "impulse_ext": imp_ext,
                "origin": origin,
                "impulse_idx": imp_idx.astype(np.float64),
                "retrace": retr,
            },
        )

    def _step(self, ep: Episode, k: int) -> Trigger61 | None:
        fc = self.cfg.families.trend_pullback_reclaim
        fr, s, c = self.fr, self.s, self.c
        i0 = int(ep.refs["impulse_idx"])
        adv = self.lo if s > 0 else self.h
        pb = float(np.nanmin(adv[i0 : k + 1])) if s > 0 else float(np.nanmax(adv[i0 : k + 1]))
        ep.scratch["pullback_ext"] = pb
        size = s * (ep.refs["impulse_ext"] - ep.refs["origin"])
        retr = s * (ep.refs["impulse_ext"] - pb) / size if size > 0 else math.nan
        a15 = self.atr15[k]
        if retr > fc.retrace_invalid or s * (c[k] - pb) < -0.25 * a15:
            ep.set_state(EpState.INVALIDATED, k, "RETRACE_TOO_DEEP")
            return None
        reclaim = (
            s * (c[k] - (self.h[k - 1] if s > 0 else self.lo[k - 1])) > 0
            and s * (c[k] - fr.cols["ema_fast_15m"][k]) > 0
        )
        if reclaim:
            stop = pb - s * self.cfg.stops.buffer_atr5 * self.atr5[k]
            return self._trigger(ep, k, stop, float(ep.refs["impulse_ext"]), "PULLBACK_RECLAIM")
        return None

    def flow(self, k: int, ep: Episode) -> bool:
        fc = self.cfg.families.trend_pullback_reclaim
        d15 = self.fr.cols["delta_15m"]
        return bool(
            self.s * self.imb5[k] >= fc.flow_imbalance_5m and self.s * (d15[k] - d15[k - 1]) > 0
        )


# ----------------------------------------------------------------------------- B
class BreakoutRetestContinuation(Detector61):
    family = V61Family.BREAKOUT_RETEST_CONTINUATION

    def detect(self) -> tuple[B, F, dict[str, F]]:
        fc = self.cfg.families.breakout_retest_continuation
        fr, s, c = self.fr, self.s, self.c
        hi, lo = fr.cols["hi_4h"], fr.cols["lo_4h"]
        width = fr.cols["range_4h_atr"]
        level = hi if s > 0 else lo
        ev = (
            (s * (c - level) > fc.break_min_atr15 * self.atr15)
            & (width >= fc.range_min_atr15)
            & (width <= fc.range_max_atr15)
            & fr.valid
        )
        ev = np.asarray(ev, dtype=bool)
        return ev, _nz(s * (c - level) / self.atr15), {"level": level, "width": hi - lo}

    def _step(self, ep: Episode, k: int) -> Trigger61 | None:
        fc = self.cfg.families.breakout_retest_continuation
        s, c = self.s, self.c
        level, a15 = ep.refs["level"], self.atr15[k]
        d = s * (c[k] - level) / a15
        if d < -fc.invalidation_atr15:
            ep.set_state(EpState.INVALIDATED, k, "FAILED_BREAK")
            return None
        retested = ep.scratch.get("retested", 0.0) > 0
        if not retested:
            if k - ep.event_k > fc.retest_window_bars:
                ep.set_state(EpState.EXPIRED, k, "NO_RETEST")
                return None
            if ep.scratch.get("extended", 0.0) > 0:
                return None  # ran away before any retest: never actionable (anti-chase)
            if d > fc.extended_atr15:
                ep.scratch["extended"] = 1.0
                ep.extended_bars += 1
                ep.set_state(EpState.EXTENDED, k, "RAN_AWAY")
                return None
            touch = s * (self.ext_adv(k) - level) / a15
            if (
                -fc.retest_below_atr15 <= touch <= fc.retest_above_atr15
                and d >= -fc.hold_below_atr15
            ):
                ep.scratch["retested"] = 1.0
                ep.scratch["retest_high"] = self.ext_fav(k)
                ep.scratch["retest_low"] = self.ext_adv(k)
                ep.set_state(EpState.WATCH, k, "RETEST_HELD")
            return None
        ep.scratch["retest_low"] = (
            min(ep.scratch["retest_low"], self.ext_adv(k))
            if s > 0
            else max(ep.scratch["retest_low"], self.ext_adv(k))
        )
        if s * (c[k] - ep.scratch["retest_high"]) > 0 and d <= fc.max_trigger_ext_atr15:
            stop = ep.scratch["retest_low"] - s * self.cfg.stops.buffer_atr5 * self.atr5[k]
            return self._trigger(ep, k, stop, level + s * ep.refs["width"], "RETEST_CONTINUATION")
        return None

    def flow(self, k: int, ep: Episode) -> bool:
        fc = self.cfg.families.breakout_retest_continuation
        return bool(
            self.relvol[k] >= fc.flow_rel_vol
            and self.s * self.imb5[k] >= fc.flow_imbalance_5m
            and self.s * self.fr.cols["cvd_slope_15m"][k] > 0
        )


# ----------------------------------------------------------------------------- C
class LiquiditySweepReversal(Detector61):
    family = V61Family.LIQUIDITY_SWEEP_REVERSAL

    def detect(self) -> tuple[B, F, dict[str, F]]:
        fc = self.cfg.families.liquidity_sweep_reversal
        fr, s, c = self.fr, self.s, self.c
        adv = self.lo if s > 0 else self.h
        lb, xb = fc.reference_lookback_bars, fc.reference_exclude_bars
        # reference = adverse extreme over bars k-lb .. k-xb-1
        win = _rolling_min_incl(adv, lb - xb) if s > 0 else _rolling_max_incl(adv, lb - xb)
        ref_struct = lagged(win, xb + 1)
        prev_day = fr.cols["prev_day_lo"] if s > 0 else fr.cols["prev_day_hi"]
        ref = np.where(
            np.isnan(prev_day),
            ref_struct,
            (np.minimum if s > 0 else np.maximum)(ref_struct, prev_day),
        )
        recent = lagged(_rolling_min_incl(adv, xb) if s > 0 else _rolling_max_incl(adv, xb), 1)
        in_place = s * (recent - ref) >= 0  # no deeper extreme in the last X bars
        depth = s * (ref - adv) / self.atr15  # how far the bar swept beyond the reference
        ev = (depth > fc.sweep_min_atr15) & (s * (c - ref) > 0) & in_place & fr.valid
        ev = np.asarray(ev, dtype=bool)
        return (
            ev,
            _nz(depth),
            {
                "reference": ref,
                "sweep_ext": adv,
                "sweep_bar_fav": self.h if s > 0 else self.lo,
                "sweep_imbalance": fr.cols["imbalance_5m"],
                "depth_atr15": depth,
            },
        )

    def _step(self, ep: Episode, k: int) -> Trigger61 | None:
        s, c = self.s, self.c
        ref = ep.refs["reference"]
        sweep_ext = ep.scratch.get("sweep_ext", ep.refs["sweep_ext"])
        if s * (c[k] - sweep_ext) < 0:
            ep.set_state(EpState.INVALIDATED, k, "SWEEP_FAILED")
            return None
        sweep_ext = min(sweep_ext, self.ext_adv(k)) if s > 0 else max(sweep_ext, self.ext_adv(k))
        ep.scratch["sweep_ext"] = sweep_ext
        if s * (c[k] - ep.refs["sweep_bar_fav"]) > 0 and s * (c[k] - ref) > 0:
            stop = sweep_ext - s * self.cfg.stops.buffer_atr5 * self.atr5[k]
            # reference + 2 x sweep depth, but at least 1 ATR15 beyond the trigger close
            target = ref + s * 2.0 * abs(ref - sweep_ext)
            target = (
                max(target, c[k] + s * self.atr15[k])
                if s > 0
                else min(target, c[k] + s * self.atr15[k])
            )
            return self._trigger(ep, k, stop, target, "SWEEP_RECLAIM")
        return None

    def flow(self, k: int, ep: Episode) -> bool:
        fc = self.cfg.families.liquidity_sweep_reversal
        sw = self.s * ep.refs["sweep_imbalance"]
        return bool(
            sw <= fc.flow_sweep_imbalance and self.s * self.imb5[k] >= fc.flow_trigger_imbalance
        )


# ----------------------------------------------------------------------------- D
class RangeEdgeMeanReversion(Detector61):
    family = V61Family.RANGE_EDGE_MEAN_REVERSION

    def detect(self) -> tuple[B, F, dict[str, F]]:
        fc = self.cfg.families.range_edge_mean_reversion
        fr, s, c = self.fr, self.s, self.c
        hi, lo = fr.cols["hi_4h"], fr.cols["lo_4h"]
        width = fr.cols["range_4h_atr"]
        pos = fr.cols["range_pos_4h"]
        pos_dir = pos if s > 0 else 1.0 - pos  # distance from the entry edge, 0 = at the edge
        edge = lo if s > 0 else hi
        opposite = hi if s > 0 else lo
        adv = self.lo if s > 0 else self.h
        a15 = self.atr15
        ev = (
            (fr.cols["regime_1h"] == 0.0)
            & (width >= fc.range_min_atr15)
            & (width <= fc.range_max_atr15)
            & (fr.cols["edge_touches_hi"] >= fc.min_edge_touches)
            & (fr.cols["edge_touches_lo"] >= fc.min_edge_touches)
            & (s * (opposite - c) >= fc.min_opposite_distance_atr15 * a15)
            & (pos_dir <= fc.edge_zone)
            & (s * (adv - edge) <= fc.touch_atr15 * a15)
            & (s * (c - edge) >= fc.touch_atr15 * a15)
            & fr.valid
        )
        ev = np.asarray(ev, dtype=bool)
        return ev, _nz(width), {"edge": edge, "opposite": opposite, "touch_ext": adv}

    def _step(self, ep: Episode, k: int) -> Trigger61 | None:
        fc = self.cfg.families.range_edge_mean_reversion
        fr, s, c = self.fr, self.s, self.c
        edge, opp = ep.refs["edge"], ep.refs["opposite"]
        a15 = self.atr15[k]
        if s * (c[k] - edge) < -fc.invalidation_atr15 * a15:
            ep.set_state(EpState.INVALIDATED, k, "RANGE_BROKEN")
            return None
        if fr.cols["regime_1h"][k] != 0.0:
            ep.set_state(EpState.INVALIDATED, k, "REGIME_LEFT_RANGE")
            return None
        te = ep.scratch.get("touch_ext", ep.refs["touch_ext"])
        te = min(te, self.ext_adv(k)) if s > 0 else max(te, self.ext_adv(k))
        ep.scratch["touch_ext"] = te
        width = abs(opp - edge)
        pos_dir = s * (c[k] - edge) / width if width > 0 else math.nan
        if (
            s * (c[k] - (self.h[k - 1] if s > 0 else self.lo[k - 1])) > 0
            and pos_dir <= fc.trigger_zone
        ):
            stop = te - s * self.cfg.stops.buffer_atr5 * self.atr5[k]
            return self._trigger(ep, k, stop, (edge + opp) / 2.0, "EDGE_REJECTION")
        return None

    def flow(self, k: int, ep: Episode) -> bool:
        fc = self.cfg.families.range_edge_mean_reversion
        return bool(
            self.s * self.imb5[k] >= fc.flow_imbalance_5m
            and self.s * self.fr.cols["delta_accel"][k] > 0
        )


# ----------------------------------------------------------------------------- E
class ImpulseConsolidationBreak(Detector61):
    family = V61Family.IMPULSE_CONSOLIDATION_BREAK

    def detect(self) -> tuple[B, F, dict[str, F]]:
        fc = self.cfg.families.impulse_consolidation_break
        fr, s, c = self.fr, self.s, self.c
        nb = fc.impulse_bars
        net = s * (c - lagged(c, nb))
        path = _rolling_sum_incl(np.abs(np.concatenate([[np.nan], np.diff(c)])), nb)
        eff = np.where(path > 0, net / path, np.nan)
        adv = self.lo if s > 0 else self.h
        fav = self.h if s > 0 else self.lo
        origin = _rolling_min_incl(adv, nb) if s > 0 else _rolling_max_incl(adv, nb)
        imp_ext = _rolling_max_incl(fav, nb) if s > 0 else _rolling_min_incl(fav, nb)
        a15 = self.atr15
        ev = (
            (fr.cols["regime_1h"] == s)
            & (net >= fc.impulse_min_atr15 * a15)
            & (eff >= fc.min_efficiency)
            & fr.valid
        )
        ev = np.asarray(ev, dtype=bool)
        return ev, _nz(net / a15), {"origin": origin, "impulse_ext": imp_ext, "efficiency": eff}

    def _step(self, ep: Episode, k: int) -> Trigger61 | None:
        fc = self.cfg.families.impulse_consolidation_break
        s, c = self.s, self.c
        a15 = self.atr15[k]
        origin = ep.refs["origin"]
        imp_ext = ep.scratch.get("impulse_ext", ep.refs["impulse_ext"])
        fav_k, adv_k = self.ext_fav(k), self.ext_adv(k)
        cons_bars = int(ep.scratch.get("cons_bars", 0.0))
        if cons_bars >= 1:
            # a completed consolidation (>= min bars, compressed, quiet, holding the upper half)
            ch, cl = ep.scratch["cons_high"], ep.scratch["cons_low"]
            prev_ok = (
                cons_bars >= fc.consolidation_min_bars
                and abs(ch - cl) <= fc.consolidation_max_range_atr15 * a15
                and ep.scratch.get("rv_sum", 0.0) / cons_bars <= fc.consolidation_max_rel_vol
                and s * (cl - (origin + s * fc.hold_share * abs(imp_ext - origin))) >= 0
            )
            if (
                prev_ok
                and s * (c[k] - ch) > 0
                and s * (c[k] - origin) <= fc.max_extension_atr15 * a15
            ):
                stop = cl - s * self.cfg.stops.buffer_atr5 * self.atr5[k]
                target = imp_ext + s * 0.5 * abs(imp_ext - origin)
                return self._trigger(ep, k, stop, target, "CONSOLIDATION_BREAK")
            if (
                cons_bars >= fc.consolidation_min_bars
                and s * (c[k] - cl) < -fc.invalidation_atr15 * a15
            ):
                ep.set_state(EpState.INVALIDATED, k, "CONSOLIDATION_FAILED")
                return None
            if cons_bars >= fc.consolidation_max_bars:
                ep.set_state(EpState.INVALIDATED, k, "CONSOLIDATION_TOO_LONG")
                return None
        if s * (fav_k - imp_ext) > 0:
            # the impulse is still extending: it absorbs this bar, the consolidation restarts
            ep.scratch["impulse_ext"] = fav_k
            ep.scratch["cons_bars"] = 0.0
            ep.scratch["rv_sum"] = 0.0
            ep.scratch.pop("cons_high", None)
            ep.scratch.pop("cons_low", None)
            if s * (c[k] - origin) > fc.max_extension_atr15 * a15:
                ep.extended_bars += 1
                ep.set_state(EpState.EXTENDED, k, "")
            return None
        ch = ep.scratch.get("cons_high", math.nan)
        cl = ep.scratch.get("cons_low", math.nan)
        ep.scratch["cons_high"] = (
            fav_k if math.isnan(ch) else (max(ch, fav_k) if s > 0 else min(ch, fav_k))
        )
        ep.scratch["cons_low"] = (
            adv_k if math.isnan(cl) else (min(cl, adv_k) if s > 0 else max(cl, adv_k))
        )
        ep.scratch["cons_bars"] = cons_bars + 1.0
        ep.scratch["rv_sum"] = ep.scratch.get("rv_sum", 0.0) + float(
            _nz(np.array([self.relvol[k]]))[0]
        )
        if ep.state is EpState.EXTENDED:
            ep.set_state(EpState.WATCH, k, "")
        return None

    def flow(self, k: int, ep: Episode) -> bool:
        fc = self.cfg.families.impulse_consolidation_break
        return bool(
            self.s * self.imb5[k] >= fc.flow_imbalance_5m and self.relvol[k] >= fc.flow_rel_vol
        )


DETECTORS61: dict[V61Family, type[Detector61]] = {
    V61Family.TREND_PULLBACK_RECLAIM: TrendPullbackReclaim,
    V61Family.BREAKOUT_RETEST_CONTINUATION: BreakoutRetestContinuation,
    V61Family.LIQUIDITY_SWEEP_REVERSAL: LiquiditySweepReversal,
    V61Family.RANGE_EDGE_MEAN_REVERSION: RangeEdgeMeanReversion,
    V61Family.IMPULSE_CONSOLIDATION_BREAK: ImpulseConsolidationBreak,
}


def build_detectors61(
    cfg: V61Config,
    fr: V6Frame,
    families: list[V61Family] | None = None,
    flow: dict[V61Family, bool] | bool = True,
) -> list[Detector61]:
    fams = families if families is not None else cfg.enabled_families()
    out: list[Detector61] = []
    for fam in fams:
        fo = flow if isinstance(flow, bool) else flow.get(fam, True)
        for side in (Side.LONG, Side.SHORT):
            out.append(DETECTORS61[fam](cfg, fr, side, fo))
    return out


__all__: list[Any] = [
    "Detector61",
    "EpState",
    "Episode",
    "Trigger61",
    "build_detectors61",
    "first_in_cluster",
]
