"""V11 decision + exit engine (`v11-exit-1`). One `process_row(i)` per completed 1m bar over
precomputed frame / score arrays; state is keyed by timestamps so the same code runs the historical
experiment and the live shadow (rolling window). One position; immediate decisions; no ranking."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from btc_swing.core.enums import Side
from btc_swing.risk.sizing import size_position
from btc_swing.v11.config import GROUPS, V11Config
from btc_swing.v11.features import V11Frame

F = NDArray[np.float64]
MS_1M = 60_000
FAMILIES = ("A", "B", "C")
SNAPSHOT_KEYS = (
    "close",
    "vwap",
    "dist_vwap",
    "vwap_slope",
    "ema9",
    "ema21",
    "ema50",
    "ema21_slope",
    "struct15",
    "rsi7",
    "rsi14",
    "adx14",
    "roc3",
    "accel5",
    "accel1",
    "atr1",
    "atr5",
    "bbw",
    "bbw_rising",
    "squeeze_on",
    "squeeze_release",
    "atr_band",
    "atr_low",
    "vol_tercile",
    "bb_dev",
    "rvol5",
    "vol_spike",
    "delta1",
    "delta5",
    "delta15",
    "imb5",
    "imb15",
    "cum_delta",
    "intensity1",
    "swing_hi5",
    "swing_lo5",
    "swing_hi15",
    "swing_lo15",
    "hi60",
    "lo60",
    "rng_hi15",
    "rng_lo15",
    "a_retrace_long",
    "a_retrace_short",
    "oi",
    "oi_chg_1h",
    "oi_chg_15m",
    "funding",
    "ret_1h",
    "hour",
)


@dataclass
class Position:
    signal_id: str
    family: str
    side: int
    decision_ms: int
    entry_ms: int
    entry_ref: float
    entry_fill: float
    stop0: float
    stop: float
    stop_pct: float
    tp1: float
    tp2: float
    qty: float
    leverage: float
    notional: float
    margin: float
    risk_usdt: float
    score_entry: float
    trigger_low: float  # invalidation level (trigger bar low for longs / high for shorts)
    frac_open: float = 1.0
    tp1_done: bool = False
    tp2_done: bool = False
    stall_checked: bool = False
    mfe_r: float = 0.0
    mae_r: float = 0.0
    legs: list[dict[str, Any]] = field(default_factory=list)
    funding_usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Trade:
    pos: dict[str, Any]
    exit_ms: int
    exit_reason: str
    hold_min: float
    gross_usd: float
    fee_usd: float
    slip_usd: float
    funding_usd: float
    net_usd: float
    net_r: float
    gross_r: float
    cost_r: float
    decision: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.pos,
            "exit_ms": self.exit_ms,
            "exit_reason": self.exit_reason,
            "hold_min": self.hold_min,
            "gross_usd": self.gross_usd,
            "fee_usd": self.fee_usd,
            "slip_usd": self.slip_usd,
            "funding_usd": self.funding_usd,
            "net_usd": self.net_usd,
            "net_r": self.net_r,
            "gross_r": self.gross_r,
            "cost_r": self.cost_r,
            "decision": self.decision,
        }


class V11Engine:
    def __init__(
        self,
        cfg: V11Config,
        fr: V11Frame,
        scores: dict[str, F],
        cost_mult: float = 1.0,
        funding_t: NDArray[np.int64] | None = None,
        funding_r: F | None = None,
    ) -> None:
        self.cfg, self.fr, self.sc = cfg, fr, scores
        self.cost_mult = cost_mult
        self.fund_t = funding_t if funding_t is not None else np.zeros(0, dtype=np.int64)
        self.fund_r = funding_r if funding_r is not None else np.zeros(0)
        self.X = fr.c
        self.t_open = fr.t_open
        self.pos: Position | None = None
        self.armed: dict[str, dict[str, Any]] = {}  # key "A+1" -> {"until_ms", "score", "armed_ms"}
        self.trades: list[Trade] = []
        self.decisions: list[dict[str, Any]] = []  # executed + blocked
        self.counters: dict[str, int] = {
            "candidates": 0,
            "armed": 0,
            "triggers": 0,
            "executed": 0,
            "blocked": 0,
            "skipped_while_open": 0,
        }
        self.blockers: dict[str, int] = {}
        self.by_family: dict[str, dict[str, int]] = {
            f: {"armed": 0, "triggers": 0, "executed": 0} for f in FAMILIES
        }
        self._cand_last: dict[str, int] = {}
        self._n_sig = 0
        self.last_close_ms = 0
        self.pending_open: tuple[dict[str, Any], int] | None = None
        self.pos_decision: dict[str, Any] = {}
        self._dist = math.nan

    # ------------------------------------------------------------------ helpers
    def _f(self, k: str, i: int) -> float:
        return float(self.X[k][i])

    def _score(self, s: int, i: int) -> float:
        return float(self.sc["score_long" if s > 0 else "score_short"][i])

    def _contrib(self, s: int, i: int) -> dict[str, float | None]:
        tag = "long" if s > 0 else "short"
        out: dict[str, float | None] = {}
        for g in GROUPS:
            v = float(self.sc[f"{tag}_{g}"][i])
            out[g] = None if math.isnan(v) else v
        return out

    def snapshot(self, i: int) -> dict[str, float | None]:
        return {
            k: (None if math.isnan(float(self.X[k][i])) else float(self.X[k][i]))
            for k in SNAPSHOT_KEYS
            if k in self.X
        }

    # ------------------------------------------------------------------ structure / trigger / stop
    def structure(self, fam: str, s: int, i: int) -> bool:
        X = self.X  # noqa: N806
        c, a5 = X["close"][i], X["atr5"][i]
        if not (np.isfinite(c) and np.isfinite(a5) and a5 > 0):
            return False
        if fam == "A":
            ok = X["a_struct_long"][i] == 1.0 if s > 0 else X["a_struct_short"][i] == 1.0
            turn = (s * X["accel5"][i] > 0) or (s * (X["rsi7"][i] - X["rsi7_prev"][i]) > 0)
            return bool(ok and X["struct15"][i] == s and turn)
        fb = self.cfg.families.b
        if fam == "B":
            comp = X["squeeze_release_recent"][i] == 1.0 or X["atr_low_recent"][i] == 1.0
            lvl = X["hi60"][i] if s > 0 else X["lo60"][i]
            near = np.isfinite(lvl) and 0 <= s * (lvl - c) <= fb.near_boundary_atr * a5
            return bool(comp and near)
        fcc = self.cfg.families.c
        recent = X["sweep_long_recent"][i] == 1.0 if s > 0 else X["sweep_short_recent"][i] == 1.0
        r7 = X["rsi7"][i]
        exhausted = r7 <= fcc.rsi_exhaust if s > 0 else r7 >= 100.0 - fcc.rsi_exhaust
        return bool(recent and np.isfinite(r7) and exhausted)

    def trigger(self, fam: str, s: int, i: int) -> bool:
        X = self.X  # noqa: N806
        c, a5 = X["close"][i], X["atr5"][i]
        if fam == "A":
            lvl = X["hi3"][i] if s > 0 else X["lo3"][i]
            return bool(np.isfinite(lvl) and s * (c - lvl) > 0)
        if fam == "B":
            fb = self.cfg.families.b
            lvl = X["hi60"][i] if s > 0 else X["lo60"][i]
            return bool(
                np.isfinite(lvl)
                and s * (c - lvl) > 0
                and X["rvol5"][i] >= fb.rvol_min
                and s * (c - lvl) <= fb.max_extension_atr * a5
            )
        lvl = X["lo60"][i] if s > 0 else X["hi60"][i]
        ext = X["sweep_low"][i] if s > 0 else X["sweep_high"][i]
        return bool(
            np.isfinite(lvl) and np.isfinite(ext) and s * (c - lvl) > 0 and s * (c - ext) > 0
        )

    def stop_level(self, fam: str, s: int, i: int) -> tuple[float, str]:
        X = self.X  # noqa: N806
        a5 = X["atr5"][i]
        fams = self.cfg.families
        if fam == "A":
            pb = X["a_pb_low"][i] if s > 0 else X["a_pb_high"][i]
            sw = X["swing_lo5"][i] if s > 0 else X["swing_hi5"][i]
            cands = [x for x in (pb, sw) if np.isfinite(x)]
            base = (min(cands) if s > 0 else max(cands)) if cands else math.nan
            return base - s * fams.a.stop_buffer_atr * a5, "A:min(pullback,swing)-buffer"
        if fam == "B":
            lvl = X["hi60"][i] if s > 0 else X["lo60"][i]
            return lvl - s * fams.b.stop_buffer_atr * a5, "B:breakout level-buffer"
        ext = X["sweep_low"][i] if s > 0 else X["sweep_high"][i]
        return ext - s * fams.c.stop_buffer_atr * a5, "C:sweep extreme-buffer"

    # ------------------------------------------------------------------ per-row processing
    def process_row(self, i: int, in_window: bool = True) -> dict[str, Any] | None:
        """Called at the close of 1m bar i (PIT). Manages an open position on this bar, then, when
        flat, evaluates arming / triggers. Returns the decision record when one was made."""
        t_close = int(self.t_open[i]) + MS_1M
        self.last_close_ms = t_close
        if self.pos is not None:
            self._manage(i)
        if not in_window or not self.fr.valid[i]:
            return None
        th = self.cfg.thresholds
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
                    if key not in self.armed:
                        self.counters["armed"] += 1
                        self.by_family[fam]["armed"] += 1
                    self.armed[key] = {
                        "until_ms": t_close + valid_min * MS_1M,
                        "score": sc,
                        "armed_ms": t_close,
                    }
                arm = self.armed.get(key)
                if arm is not None and t_close > int(arm["until_ms"]):
                    self.armed.pop(key, None)
                    arm = None
                if arm is None or decision is not None:
                    continue
                if not self.trigger(fam, s, i):
                    continue
                self.counters["triggers"] += 1
                self.by_family[fam]["triggers"] += 1
                if not (math.isfinite(sc) and sc >= th.entry):
                    continue
                decision = self._decide(fam, s, i, arm)
                self.armed.pop(key, None)
        return decision

    def _decide(self, fam: str, s: int, i: int, arm: dict[str, Any]) -> dict[str, Any]:
        X = self.X  # noqa: N806
        t_close = int(self.t_open[i]) + MS_1M
        c = float(X["close"][i])
        sc = self._score(s, i)
        self._n_sig += 1
        sig_id = f"V11S-{t_close}-{fam}{'L' if s > 0 else 'S'}"
        stop_raw, note = self.stop_level(fam, s, i)
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
                blockers.append("STOP_TOO_WIDE_FOR_V11")
            stop, stop_pct = c - s * dist, pct
        rec.update(
            {
                "stop": None if math.isnan(stop) else stop,
                "stop_pct": None if math.isnan(stop_pct) else stop_pct,
                "stop_note": note,
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

    def open_from_pending(self, i_entry: int, fill_ref: float | None = None) -> Position | None:
        """Historical: fill at the open of the next bar; live: at the decision close + costs."""
        pend = self.pending_open
        if pend is None:
            return None
        rec, i_dec = pend
        self.pending_open = None
        s = int(rec["side_sign"])
        co = self.cfg.costs
        ref = float(self.X["open"][i_entry]) if fill_ref is None else fill_ref
        if not np.isfinite(ref):
            rec["action"], rec["blockers"] = "BLOCKED", ["NO_ENTRY_BAR"]
            self.counters["executed"] -= 1
            self.counters["blocked"] += 1
            return None
        fill = ref * (1 + s * (co.half_spread_bps + co.entry_slippage_bps) * self.cost_mult / 1e4)
        dist = s * (float(rec["entry_ref"]) - float(rec["stop"]))
        # the plan keeps the decision-close geometry: stop / targets measured from the decision close
        trig_low = float(self.X["low"][i_dec]) if s > 0 else float(self.X["high"][i_dec])
        self.pos = Position(
            signal_id=rec["signal_id"],
            family=rec["family"],
            side=s,
            decision_ms=int(rec["decision_ms"]),
            entry_ms=int(self.t_open[i_entry]),
            entry_ref=ref,
            entry_fill=fill,
            stop0=float(rec["stop"]),
            stop=float(rec["stop"]),
            stop_pct=float(rec["stop_pct"]),
            tp1=float(rec["tp1"]),
            tp2=float(rec["tp2"]),
            qty=float(rec["qty"]),
            leverage=float(rec["leverage"]),
            notional=float(rec["notional"]),
            margin=float(rec["margin"]),
            risk_usdt=float(rec["risk_usdt"]),
            score_entry=float(rec["score"]),
            trigger_low=trig_low,
        )
        self.pos_decision = rec
        self._dist = dist
        return self.pos

    # ------------------------------------------------------------------ management
    def _leg(self, frac: float, ref: float, kind: str, t_ms: int) -> None:
        p = self.pos
        assert p is not None
        co = self.cfg.costs
        s = float(p.side)
        slip = co.stop_slippage_bps if kind in ("STOP", "BE", "TRAIL") else co.exit_slippage_bps
        fill = ref * (1 - s * (slip + co.half_spread_bps) * self.cost_mult / 1e4)
        p.legs.append({"frac": frac, "ref": ref, "fill": fill, "kind": kind, "t_ms": t_ms})
        p.frac_open = max(0.0, p.frac_open - frac)

    def _manage(self, i: int) -> None:
        p = self.pos
        if p is None:
            return
        X = self.X  # noqa: N806
        t_open = int(self.t_open[i])
        t_close = t_open + MS_1M
        if t_open < p.entry_ms or not self.fr.valid[i]:
            return
        s = float(p.side)
        ex = self.cfg.exits
        h, lo, c = float(X["high"][i]), float(X["low"][i]), float(X["close"][i])
        d = self._dist
        fav, adv = (h, lo) if s > 0 else (lo, h)
        p.mfe_r = max(p.mfe_r, s * (fav - p.entry_ref) / d)
        p.mae_r = min(p.mae_r, s * (adv - p.entry_ref) / d)
        self._funding_step(p, t_open, t_close, c)
        # stop first (conservative)
        if s * (adv - p.stop) <= 0:
            if not p.tp1_done:
                kind = "STOP"
            elif p.tp2_done:
                kind = "TRAIL"
            else:
                kind = "BE" if abs(p.stop - p.entry_ref) < d * 0.5 else "STOP"
            self._leg(p.frac_open, p.stop, kind, t_close)
            self._close(kind, t_close)
            return
        if not p.tp1_done and s * (fav - p.tp1) >= 0:
            p.tp1_done = True
            self._leg(ex.tp1_frac, p.tp1, "TP1", t_close)
            be = p.entry_ref * (1 + s * ex.breakeven_after_tp1_bps / 1e4)
            if s * (be - p.stop) > 0:
                p.stop = be
        if p.tp1_done and not p.tp2_done and s * (fav - p.tp2) >= 0:
            p.tp2_done = True
            self._leg(ex.tp2_frac, p.tp2, "TP2", t_close)
        if p.tp2_done and (i + 1) % 5 == 0:  # completed 5m bar: trail the runner
            nb = ex.runner_trail_bars_5m
            lo5 = [
                float(np.nanmin(X["low"][j - 4 : j + 1]))
                for j in range(i - 5 * (nb - 1), i + 1, 5)
                if j - 4 >= 0
            ]
            hi5 = [
                float(np.nanmax(X["high"][j - 4 : j + 1]))
                for j in range(i - 5 * (nb - 1), i + 1, 5)
                if j - 4 >= 0
            ]
            a5 = float(X["atr5"][i])
            if lo5 and hi5 and np.isfinite(a5):
                trail = (
                    (min(lo5) - ex.runner_trail_buffer_atr * a5)
                    if s > 0
                    else (max(hi5) + ex.runner_trail_buffer_atr * a5)
                )
                if s * (trail - p.stop) > 0:
                    p.stop = trail
        held_min = (t_close - p.entry_ms) / MS_1M
        if not p.stall_checked and held_min >= ex.stall_review_min:
            p.stall_checked = True
            sc_now = self._score(p.side, i)
            invalid = s * (c - p.trigger_low) < 0
            deteriorated = math.isfinite(sc_now) and sc_now <= p.score_entry - ex.stall_score_drop
            if p.mfe_r < ex.stall_min_mfe_r and (deteriorated or invalid):
                self._leg(p.frac_open, c, "STALL", t_close)
                self._close("STALL", t_close)
                return
        if held_min >= ex.hard_max_min and p.frac_open > 1e-12:
            self._leg(p.frac_open, c, "TIME", t_close)
            self._close("TIME", t_close)

    def _funding_step(self, p: Position, t_open: int, t_close: int, px: float) -> None:
        if not self.cfg.costs.apply_funding or len(self.fund_t) == 0:
            return
        lo = int(np.searchsorted(self.fund_t, t_open, side="right"))
        hi = int(np.searchsorted(self.fund_t, t_close, side="right"))
        for j in range(lo, hi):
            p.funding_usd += -p.side * float(self.fund_r[j]) * p.qty * p.frac_open * px

    def _close(self, reason: str, t_ms: int) -> None:
        p = self.pos
        assert p is not None
        co = self.cfg.costs
        s = float(p.side)
        fee_rate = co.taker_fee_bps * self.cost_mult / 1e4
        gross = 0.0
        slip = p.qty * s * (p.entry_fill - p.entry_ref)
        fees = fee_rate * p.qty * p.entry_fill
        for lg in p.legs:
            q = p.qty * float(lg["frac"])
            gross += q * s * (float(lg["ref"]) - p.entry_ref)
            slip += q * s * (float(lg["ref"]) - float(lg["fill"]))
            fees += fee_rate * q * float(lg["fill"])
        net = gross - fees - slip + p.funding_usd
        risk = p.qty * abs(p.entry_ref - p.stop0)
        tr = Trade(
            pos=p.as_dict(),
            exit_ms=t_ms,
            exit_reason=reason,
            hold_min=(t_ms - p.entry_ms) / MS_1M,
            gross_usd=gross,
            fee_usd=fees,
            slip_usd=slip,
            funding_usd=p.funding_usd,
            net_usd=net,
            net_r=net / risk if risk > 0 else math.nan,
            gross_r=gross / risk if risk > 0 else math.nan,
            cost_r=(fees + slip - p.funding_usd) / risk if risk > 0 else math.nan,
            decision=self.pos_decision,
        )
        self.trades.append(tr)
        self.pos = None

    # ------------------------------------------------------------------ historical driver
    def run(self, start_ms: int, end_ms: int) -> None:
        i0 = int(np.searchsorted(self.t_open, start_ms, side="left"))
        i1 = int(np.searchsorted(self.t_open, end_ms, side="left"))
        n = len(self.t_open)
        for i in range(0, min(i1, n)):
            in_window = i >= i0
            self.process_row(i, in_window)
            pend = self.pending_open
            if pend is not None and in_window:
                if i + 1 < n and i + 1 < i1:
                    self.open_from_pending(i + 1)
                else:
                    self.pending_open = None
        if self.pos is not None:  # window end: close at the last close (reported as TIME)
            i_last = min(i1, n) - 1
            self._leg(
                self.pos.frac_open,
                float(self.X["close"][i_last]),
                "TIME",
                int(self.t_open[i_last]) + MS_1M,
            )
            self._close("WINDOW_END", int(self.t_open[i_last]) + MS_1M)
