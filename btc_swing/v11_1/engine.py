"""V11.1 engine: the V11 engine (read-only library, subclassed) with the pre-registered entry-quality
gates, failed-breakout rejection, 0.55% / 1.00% stop bounds, the cost-in-R filter and evidence logging.
Exits, management, costs and sizing are inherited unchanged (design sections 3-9)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from btc_swing.core.enums import Side
from btc_swing.risk.sizing import size_position
from btc_swing.v11.engine import FAMILIES, MS_1M, V11Engine
from btc_swing.v11_1.config import PREFIX_SIGNAL, V111Config

BLOCK_STOP_WIDE = "STOP_TOO_WIDE_V11_1"
BLOCK_COST = "COST_TOO_HIGH"
BLOCK_EVIDENCE = "INSUFFICIENT_EVIDENCE"
BLOCK_CONTRA = "TREND_CONTRADICTION"
BLOCK_FAILED_BREAKOUT = "FAILED_BREAKOUT"


class V111Engine(V11Engine):
    def __init__(self, cfg111: V111Config, fr: Any, scores: dict[str, Any], *args: Any) -> None:
        super().__init__(cfg111.base, fr, scores, *args)
        self.q = cfg111.quality
        self.cf = cfg111.cost_filter
        self.quality_rejections: dict[str, int] = {}

    # ------------------------------------------------------------------ evidence helpers
    def evidence(self, s: int, i: int) -> tuple[int, list[str]]:
        tag = "long" if s > 0 else "short"
        groups = [g for g in self.q.directional_groups if self.sc[f"evid_{tag}_{g}"][i] == 1.0]
        n_ev = self.sc[f"evid_n_{tag}"][i]
        return (int(n_ev) if np.isfinite(n_ev) else 0), groups

    def contradiction(self, s: int, i: int) -> bool:
        return bool(self.sc[f"contra_{'long' if s > 0 else 'short'}"][i] == 1.0)

    # ------------------------------------------------------------------ family quality (section 4-6)
    def quality_reason(self, fam: str, s: int, i: int, arm: dict[str, Any]) -> str | None:
        X = self.X  # noqa: N806
        q = self.q
        c, a5 = X["close"][i], X["atr5"][i]
        if not (np.isfinite(c) and np.isfinite(a5) and a5 > 0):
            return "NO_ATR"
        if fam == "A":
            if not (X["struct15"][i] == s and s * X["ema21_slope"][i] > 0):
                return "A_NO_CLEAR_REGIME"
            dv = s * X["dist_vwap"][i]
            if not (s * (c - X["vwap"][i]) > 0 and dv <= q.a_max_dist_vwap_atr):
                return "A_VWAP_POSITION"
            sw = X["swing_lo5"][i] if s > 0 else X["swing_hi5"][i]
            if not (np.isfinite(sw) and s * (c - sw) > 0):
                return "A_STRUCTURE_INVALIDATED"
            r7 = X["rsi7"][i]
            if not np.isfinite(r7) or (r7 >= 85.0 if s > 0 else r7 <= 15.0):
                return "A_MOMENTUM_EXHAUSTED"
            if not s * X["accel1"][i] > 0:
                return "A_NO_REACCELERATION"
            if not (s * X["delta1"][i] > 0 and s * X["imb5"][i] > 0):
                return "A_NO_DIRECTIONAL_FLOW"
            if s * X["imb15"][i] < q.a_opposing_imb15:
                return "A_OPPOSING_FLOW"
            stop, _ = self.stop_level(fam, s, i)
            lvl = X["hi60"][i] if s > 0 else X["lo60"][i]
            if np.isfinite(stop) and np.isfinite(lvl):
                dist = s * (c - stop)
                room = s * (lvl - c)
                if room > 0 and dist > 0 and room < q.a_min_room_r * dist:
                    return "A_POOR_REWARD_ROOM"
            return None
        if fam == "B":
            if arm.get("failed"):
                return BLOCK_FAILED_BREAKOUT
            rv, rv_prev = X["rvol5"][i], X["rvol5"][i - 5] if i >= 5 else math.nan
            rising = (
                np.isfinite(rv) and rv >= q.b_rvol_min and np.isfinite(rv_prev) and rv > rv_prev
            )
            if not (rising or X["vol_spike"][i] == 1.0):
                return "B_RVOL_NOT_RISING"
            if not (s * X["roc3"][i] > 0 and s * X["accel1"][i] > 0):
                return "B_NO_MOMENTUM_EXPANSION"
            lvl = X["hi60"][i] if s > 0 else X["lo60"][i]
            brk = s * (c - lvl)
            if not (
                np.isfinite(lvl) and q.b_min_break_atr * a5 <= brk <= q.b_max_extension_atr * a5
            ):
                return "B_NOT_DECISIVE"
            if not (s * X["delta1"][i] > 0 and s * X["imb5"][i] > q.b_min_imb5):
                return "B_NO_DIRECTIONAL_FLOW"
            h, lo = X["high"][i], X["low"][i]
            rng = h - lo
            loc = ((c - lo) / rng) if s > 0 else ((h - c) / rng)
            if not (rng > 0 and loc >= q.b_min_close_location):
                return "B_ABSORPTION"
            return None
        # family C
        lvl = X["lo60"][i] if s > 0 else X["hi60"][i]
        ext = X["sweep_low"][i] if s > 0 else X["sweep_high"][i]
        if not (np.isfinite(lvl) and np.isfinite(ext)):
            return "C_NO_LEVEL"
        depth = s * (lvl - ext)
        if depth < q.c_min_depth_atr * a5:
            return "C_NO_EXTENSION"
        if not (s * (c - lvl) > 0 and s * (c - (ext + s * q.c_min_reclaim_frac * depth)) > 0):
            return "C_NO_RECLAIM"
        if not s * (X["rsi7"][i] - X["rsi7_prev"][i]) > 0:
            return "C_NO_TURN"
        if not (s * X["delta1"][i] > 0 and s * X["imb5"][i] > 0):
            return "C_NO_REVERSAL_FLOW"
        return None

    # ------------------------------------------------------------------ per-row processing
    def process_row(self, i: int, in_window: bool = True) -> dict[str, Any] | None:
        t_close = int(self.t_open[i]) + MS_1M
        self.last_close_ms = t_close
        if self.pos is not None:
            self._manage(i)
        if not in_window or not self.fr.valid[i]:
            return None
        th = self.cfg.thresholds
        X = self.X  # noqa: N806
        decision: dict[str, Any] | None = None
        for s in (1, -1):
            sc = self._score(s, i)
            if math.isfinite(sc) and sc >= th.candidate:
                key = f"cand{s}"
                last = self._cand_last.get(key, -(10**18))
                if t_close - last >= self.cfg.outcomes.candidate_dedupe_min * MS_1M:
                    self._cand_last[key] = t_close
                    self.counters["candidates"] += 1
        for fam in FAMILIES:
            for s in (1, -1):
                key = f"{fam}{s:+d}"
                sc = self._score(s, i)
                st = self.structure(fam, s, i)
                valid_min = {
                    "A": self.cfg.families.a.armed_valid_min,
                    "B": self.cfg.families.b.armed_valid_min,
                    "C": self.cfg.families.c.armed_valid_min,
                }[fam]
                if st and math.isfinite(sc) and sc >= th.armed:
                    prev = self.armed.get(key)
                    if prev is None:
                        self.counters["armed"] += 1
                        self.by_family[fam]["armed"] += 1
                    rec_arm: dict[str, Any] = {
                        "until_ms": t_close + valid_min * MS_1M,
                        "score": sc,
                        "armed_ms": t_close,
                    }
                    if fam == "B":
                        lvl = float(X["hi60"][i] if s > 0 else X["lo60"][i])
                        if prev is None:
                            rec_arm.update({"level": lvl, "broke": False, "failed": False})
                        else:
                            for k in ("level", "broke", "failed"):
                                if k in prev:
                                    rec_arm[k] = prev[k]
                    self.armed[key] = rec_arm
                arm = self.armed.get(key)
                if arm is not None and t_close > int(arm["until_ms"]):
                    self.armed.pop(key, None)
                    arm = None
                if arm is None:
                    continue
                if fam == "B" and "level" in arm:  # failed-breakout memory (design section 5)
                    c = float(X["close"][i])
                    lvl = float(arm["level"])
                    if np.isfinite(c):
                        if s * (c - lvl) > 0:
                            arm["broke"] = True
                        elif arm.get("broke") and not arm.get("failed"):
                            arm["failed"] = True
                            self._reject(BLOCK_FAILED_BREAKOUT + "_DETECTED")
                if decision is not None:
                    continue
                if not self.trigger(fam, s, i):
                    continue
                self.counters["triggers"] += 1
                self.by_family[fam]["triggers"] += 1
                if not (math.isfinite(sc) and sc >= th.entry):
                    continue
                if self.pos is not None:  # one position: counted in _decide for the funnel
                    decision = self._decide(fam, s, i, arm)
                    self.armed.pop(key, None)
                    continue
                if self.contradiction(s, i):
                    self._reject(BLOCK_CONTRA)
                    continue
                n_ev, _ = self.evidence(s, i)
                if n_ev < self.q.min_evidence_groups:
                    self._reject(BLOCK_EVIDENCE)
                    continue
                reason = self.quality_reason(fam, s, i, arm)
                if reason is not None:
                    self._reject(reason)
                    continue
                decision = self._decide(fam, s, i, arm)
                self.armed.pop(key, None)
        return decision

    def _reject(self, reason: str) -> None:
        self.quality_rejections[reason] = self.quality_rejections.get(reason, 0) + 1

    # ------------------------------------------------------------------ decision (section 7-8)
    def _decide(self, fam: str, s: int, i: int, arm: dict[str, Any]) -> dict[str, Any]:
        X = self.X  # noqa: N806
        t_close = int(self.t_open[i]) + MS_1M
        c = float(X["close"][i])
        sc = self._score(s, i)
        self._n_sig += 1
        sig_id = f"{PREFIX_SIGNAL}{t_close}-{fam}{'L' if s > 0 else 'S'}"
        stop_raw, note = self.stop_level(fam, s, i)
        n_ev, groups = self.evidence(s, i)
        rec: dict[str, Any] = {
            "signal_id": sig_id,
            "family": fam,
            "side": "LONG" if s > 0 else "SHORT",
            "side_sign": s,
            "decision_ms": t_close,
            "armed_ms": int(arm["armed_ms"]),
            "score_armed": float(arm["score"]),
            "score_long": self._score(1, i),
            "score_short": self._score(-1, i),
            "score": sc,
            "contrib": self._contrib(s, i),
            "contrib_other_side": self._contrib(-s, i),
            "evidence_n": n_ev,
            "evidence_groups": groups,
            "indicators": self.snapshot(i),
            "entry_ref": c,
            "structural_stop": None if math.isnan(stop_raw) else stop_raw,
            "stop_note": note,
        }
        blockers: list[str] = []
        if self.pos is not None:
            blockers.append("POSITION_OPEN")
            self.counters["skipped_while_open"] += 1
        sc_cfg = self.cfg.stop
        exp_cost_r = math.nan
        if not np.isfinite(stop_raw) or s * (c - stop_raw) <= 0:
            blockers.append("NO_VALID_STRUCTURAL_STOP")
            stop, stop_pct = math.nan, math.nan
        else:
            dist = s * (c - stop_raw)
            pct = 100.0 * dist / c
            if pct < sc_cfg.floor_pct:
                dist = c * sc_cfg.floor_pct / 100.0
                pct = sc_cfg.floor_pct
                note += "|FLOORED"
            if pct > sc_cfg.cap_pct:
                blockers.append(BLOCK_STOP_WIDE)
            stop, stop_pct = c - s * dist, pct
            exp_cost_r = self.cf.expected_cost_bps / (pct * 100.0)
            if BLOCK_STOP_WIDE not in blockers and exp_cost_r > self.cf.max_cost_r:
                blockers.append(BLOCK_COST)
        rec.update(
            {
                "stop": None if math.isnan(stop) else stop,
                "stop_pct": None if math.isnan(stop_pct) else stop_pct,
                "stop_note": note,
                "expected_cost_r": None if math.isnan(exp_cost_r) else exp_cost_r,
            }
        )
        qty = lev = notional = margin = risk = 0.0
        if not blockers:
            sz = size_position(
                self.cfg.risk.initial_equity,
                c,
                stop,
                Side.LONG if s > 0 else Side.SHORT,
                float(X["atr1h"][i]),
                self.cfg.risk,
            )
            if not sz.accepted:
                blockers.append(f"SIZING:{sz.reason}")
            else:
                qty, lev, notional, risk = (
                    float(sz.qty),
                    float(sz.leverage),
                    float(sz.notional),
                    float(sz.risk_amount),
                )
                margin = notional / lev if lev > 0 else math.nan
        rec.update(
            {"qty": qty, "leverage": lev, "notional": notional, "margin": margin, "risk_usdt": risk}
        )
        if blockers:
            rec["action"], rec["blockers"] = "BLOCKED", blockers
            self.counters["blocked"] += 1
            for b in blockers:
                self.blockers[b] = self.blockers.get(b, 0) + 1
            self.decisions.append(rec)
            return rec
        ex = self.cfg.exits
        dist = s * (c - stop)
        rec["action"] = "EXECUTED"
        rec.update(
            {
                "tp1": c + s * ex.tp1_r * dist,
                "tp2": c + s * ex.tp2_r * dist,
                "runner_rule": f"trail lowest/highest of last {ex.runner_trail_bars_5m} completed 5m bars - {ex.runner_trail_buffer_atr} ATR5 after TP2; BE +{ex.breakeven_after_tp1_bps} bps after TP1",
            }
        )
        self.counters["executed"] += 1
        self.by_family[fam]["executed"] += 1
        self.decisions.append(rec)
        self.pending_open = (rec, i)
        return rec
