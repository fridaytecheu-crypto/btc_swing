"""V11-LITE-WARM live runtime: the V11-WARM live scorer (same warm window, seam healer, feed handling,
journals, outcomes) driving the V11-LITE engine, plus (a) read-only context telemetry on every journaled
event and (b) an in-process STRICT V11 CONTROL TWIN: an unchanged `V11Engine` fed the identical frame,
scores and microstructure, whose decisions say exactly whether / why the frozen V11 rules would have
rejected each LITE event. The twin never influences a LITE decision. No exchange client exists."""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl

from btc_swing.v5.demo.journal import HashChainJournal
from btc_swing.v11.config import V11Config
from btc_swing.v11.engine import FAMILIES, MS_1M, Position, V11Engine
from btc_swing.v11.features import V11Frame, build_frame_v11
from btc_swing.v11.live import MIN_BARS
from btc_swing.v11.scoring import score_frame
from btc_swing.v11_lite_warm.config import STRATEGY, STRATEGY_DELTA, LiteCfg
from btc_swing.v11_lite_warm.context import context_at
from btc_swing.v11_lite_warm.engine import V11LiteEngine
from btc_swing.v11_lite_warm.paths import V11LiteWarmPaths
from btc_swing.v11_warm.config import WarmCfg
from btc_swing.v11_warm.live import V11WarmLive

log = logging.getLogger(__name__)


def _iso(ms_: int | float | None) -> str | None:
    return None if ms_ is None else datetime.fromtimestamp(ms_ / 1000, tz=UTC).isoformat()


class V11LiteWarmLive(V11WarmLive):
    def __init__(
        self,
        cfg: V11Config,
        paths: V11LiteWarmPaths,
        observation_start_ms: int,
        warm: WarmCfg,
        lite: LiteCfg,
    ) -> None:
        self.lite = lite
        self.twin: V11Engine | None = None
        self.last_context: dict[str, Any] = {}
        self.last_labels: dict[str, dict[str, bool]] = {}
        super().__init__(cfg, paths, observation_start_ms, warm)
        self.state["strategy"] = STRATEGY
        self.state["strategy_delta"] = STRATEGY_DELTA
        self.state.setdefault(
            "twin",
            {
                "position": None,
                "armed": {},
                "counters": {},
                "trades": 0,
                "cum_net_usd": 0.0,
                "wins": 0,
                "losses": 0,
            },
        )
        self.j_twin = HashChainJournal(paths.control_twin, "v11lw_control_twin")

    # ------------------------------------------------------------------ persistence
    def save(self) -> None:
        tw = self.twin
        st = self.state.setdefault("twin", {})
        if tw is not None:
            st["position"] = tw.pos.as_dict() if tw.pos else None
            st["armed"] = tw.armed
            st["counters"] = tw.counters
        super().save()

    def _restore_twin(self) -> None:
        tw = self.twin
        assert tw is not None
        st = self.state.get("twin") or {}
        tw.counters.update(st.get("counters") or {})
        tw.armed = dict(st.get("armed") or {})
        p = st.get("position")
        if p:
            try:
                tw.pos = Position(
                    **{k: v for k, v in p.items() if k in Position.__dataclass_fields__}
                )
                tw._dist = abs(tw.pos.entry_ref - tw.pos.stop0)
                tw.pos_decision = {}
            except TypeError:
                tw.pos = None

    # ------------------------------------------------------------------ tick
    def tick(self, now_ms: int) -> dict[str, Any] | None:
        self.micro.compute(now_ms)
        n = len(self.micro.bars.done)
        if n < MIN_BARS:
            return None
        last_open = int(self.micro.bars.done[-1].open_ms)
        if last_open == self._last_tick_open_ms:
            return None
        if self._last_tick_open_ms > 0 and last_open - self._last_tick_open_ms > MS_1M:
            self.heal_holes("live")
            n = len(self.micro.bars.done)
        self._last_tick_open_ms = last_open
        self._n_bars_seen = n
        bars = list(self.micro.bars.done)[-self.window_bars :]
        rows = pl.DataFrame(
            {
                "open_time_ms": [b.open_ms for b in bars],
                "open": [b.o for b in bars],
                "high": [b.h for b in bars],
                "low": [b.lo for b in bars],
                "close": [b.c for b in bars],
                "volume": [b.vol for b in bars],
                "n_trades": [float(b.n) for b in bars],
                "buy_qty": [b.buy for b in bars],
                "sell_qty": [b.sell for b in bars],
            }
        )
        met = (
            pl.DataFrame(
                {"time_ms": [t for t, _ in self.oi], "open_interest": [v for _, v in self.oi]}
            )
            if self.oi
            else None
        )
        fnd = (
            pl.DataFrame(
                {
                    "time_ms": [t for t, _ in self.funding],
                    "funding_rate": [v for _, v in self.funding],
                }
            )
            if self.funding
            else None
        )
        cfg_live = self.cfg.model_copy(
            update={"features": self.cfg.features.model_copy(update={"binance_latency_s": 0})}
        )
        fr = build_frame_v11(rows, cfg_live, met, fnd)
        i = int(np.searchsorted(fr.t_open, bars[-1].open_ms))
        if i >= len(fr) or fr.t_open[i] != bars[-1].open_ms:
            return None
        micro_arrays = self._micro_arrays(len(fr), i, bars[-1].open_ms + MS_1M)
        sc = score_frame(fr, self.cfg, micro_arrays)
        rec = self._step(fr, sc, i)
        self._outcomes(int(fr.t_open[i]) + MS_1M)
        self.save()
        return rec

    def _step(self, fr: V11Frame, sc: dict[str, Any], i: int) -> dict[str, Any] | None:
        """Everything after the frame / scores exist for row i: twin + LITE engines, journals. Split
        from `tick` so tests can replay the identical code path on a precomputed frame."""
        if self.engine is None:
            self.engine = V11LiteEngine(self.cfg, fr, sc)
            self._restore_engine()
        else:
            self.engine.fr, self.engine.X, self.engine.sc, self.engine.t_open = (
                fr,
                fr.c,
                sc,
                fr.t_open,
            )
        if self.twin is None:
            self.twin = V11Engine(self.cfg, fr, sc)  # the unchanged frozen V11 rules
            self._restore_twin()
        else:
            self.twin.fr, self.twin.X, self.twin.sc, self.twin.t_open = fr, fr.c, sc, fr.t_open
        eng = self.engine
        assert isinstance(eng, V11LiteEngine)
        tw = self.twin
        t_close = int(fr.t_open[i]) + MS_1M
        in_window = t_close > self.observation_start_ms
        self.state["last_bar_ms"] = t_close
        self.last_features = eng.snapshot(i)
        a1h = float(fr.c["atr1h"][i])
        self.last_features["atr1h"] = None if np.isnan(a1h) else a1h
        self.last_scores = {
            "long": eng._score(1, i),
            "short": eng._score(-1, i),
            "contrib_long": eng._contrib(1, i),
            "contrib_short": eng._contrib(-1, i),
        }
        self.last_labels = eng.family_labels(i)
        self.last_context = context_at(
            self.cfg, self.lite, fr.c, i, getattr(self, "last_micro", {})
        )
        # ---- strict V11 control twin on the identical inputs (telemetry only)
        twin_pos_before = tw.pos
        n_tw = len(tw.trades)
        twin_rec = tw.process_row(i, in_window=in_window)
        if tw.pending_open is not None:
            tpos = tw.open_from_pending(i, fill_ref=float(fr.c["close"][i]))
            if tpos is not None:
                tpos.entry_ms = t_close
                self.j_twin.append("TWIN_OPEN", tpos.as_dict())
        if len(tw.trades) > n_tw:
            tst = self.state["twin"]
            for t in tw.trades[n_tw:]:
                self.j_twin.append("TWIN_CLOSED", t.as_dict())
                tst["trades"] = int(tst.get("trades", 0)) + 1
                tst["cum_net_usd"] = float(tst.get("cum_net_usd", 0.0)) + float(t.net_usd)
                tst["wins" if t.net_usd > 0 else "losses"] = (
                    int(tst.get("wins" if t.net_usd > 0 else "losses", 0)) + 1
                )
            tw.trades = tw.trades[-50:]
        if twin_rec is not None:
            self.j_twin.append(
                "TWIN_" + str(twin_rec["action"]),
                {**twin_rec, "ts": _iso(twin_rec["decision_ms"]), "execution_mode": "NONE"},
            )
        # ---- LITE engine (the shadow strategy under observation)
        pos_before = eng.pos
        n_tr = len(eng.trades)
        rec = eng.process_row(i, in_window=in_window)
        if eng.pending_open is not None:
            fill_ref = float(fr.c["close"][i])
            pos = eng.open_from_pending(i, fill_ref=fill_ref)
            if pos is not None:
                pos.entry_ms = t_close
                self.j_sh.append("SHADOW_OPEN", pos.as_dict())
        if len(eng.trades) > n_tr:
            for t in eng.trades[n_tr:]:
                d = t.as_dict()
                self.j_sh.append("SHADOW_CLOSED", d)
                self.state["trades"] += 1
                self.state["cum_net_usd"] = float(self.state["cum_net_usd"]) + float(t.net_usd)
                self.state["wins" if t.net_usd > 0 else "losses"] += 1
            eng.trades = eng.trades[-50:]
        self._journal_row_lite(eng, tw, i, t_close, rec, pos_before, twin_rec, twin_pos_before)
        return rec

    # ------------------------------------------------------------------ control-twin attribution
    def control_assessment(
        self,
        eng: V11LiteEngine,
        tw: V11Engine,
        fam: str,
        s: int,
        i: int,
        twin_rec: dict[str, Any] | None,
        twin_pos_before: Position | None,
    ) -> dict[str, Any]:
        """Would the frozen V11 rules (the twin) have made this decision? If not, the exact reason."""
        side = "LONG" if s > 0 else "SHORT"
        label = eng.family_structure(fam, s, i)
        detail = self.last_context.get("structure_detail", {}).get(fam, {}).get(side, {})
        failed = [k for k, ok in detail.items() if ok is False]
        out: dict[str, Any] = {
            "family_structure_label": label,
            "structure_detail": detail,
            "failed_conditions": failed,
            "twin_decision": None
            if twin_rec is None
            else {
                "signal_id": twin_rec.get("signal_id"),
                "family": twin_rec.get("family"),
                "side": twin_rec.get("side"),
                "action": twin_rec.get("action"),
                "blockers": twin_rec.get("blockers"),
            },
            "twin_position_open_before": twin_pos_before is not None,
        }
        if twin_rec is not None and int(twin_rec["side_sign"]) == s:
            blockers = list(twin_rec.get("blockers") or [])
            if twin_rec["family"] == fam:
                out["would_take"] = True
                out["reason"] = "CONTROL_SAME_DECISION"
            else:
                out["would_take"] = True
                out["reason"] = f"CONTROL_SAME_SIDE_OTHER_FAMILY:{twin_rec['family']}"
            if blockers == ["POSITION_OPEN"]:
                out["reason"] += "|CONTROL_BLOCKED_POSITION_OPEN"
            return out
        out["would_take"] = False
        if not label:
            out["reason"] = f"STRUCTURE_{fam}_FALSE:" + ",".join(failed)
        elif f"{fam}{s:+d}" in tw.armed:
            out["reason"] = f"CONTROL_ARMED_{fam}_NO_DECISION"
        else:
            out["reason"] = f"STRUCTURE_{fam}_TRUE_NOW_BUT_CONTROL_NOT_ARMED"
        if twin_rec is not None:
            out["reason"] += f"|CONTROL_DECIDED_OTHER_SIDE:{twin_rec['family']}{twin_rec['side']}"
        return out

    # ------------------------------------------------------------------ journals (V11 rows + labels + context)
    def _journal_row_lite(
        self,
        eng: V11LiteEngine,
        tw: V11Engine,
        i: int,
        t_close: int,
        rec: dict[str, Any] | None,
        pos_before: Position | None,
        twin_rec: dict[str, Any] | None,
        twin_pos_before: Position | None,
    ) -> None:
        th = self.cfg.thresholds
        micro = getattr(self, "last_micro", {})
        ctx = self.last_context
        labels = self.last_labels
        for s, tag in ((1, "long"), (-1, "short")):
            sc = eng._score(s, i)
            if math.isfinite(sc) and sc >= th.candidate and t_close > self.observation_start_ms:
                last = self._cand_last.get(tag, -(10**18))
                if t_close - last >= self.cfg.outcomes.candidate_dedupe_min * MS_1M:
                    self._cand_last[tag] = t_close
                    self.state["counters"]["candidates"] += 1
                    self.j_cand.append(
                        "CANDIDATE",
                        {
                            "ts": _iso(t_close),
                            "ts_ms": t_close,
                            "side": tag.upper(),
                            "score": sc,
                            "contrib": eng._contrib(s, i),
                            "micro": micro,
                            "indicators": eng.snapshot(i),
                            "family_labels": {f: labels[f][tag.upper()] for f in FAMILIES},
                            "any_family_structure": any(labels[f][tag.upper()] for f in FAMILIES),
                            "control_twin_armed": sorted(tw.armed),
                            "context": ctx,
                        },
                    )
                    p0 = float(eng.X["close"][i])
                    if math.isfinite(p0):
                        self._queue_outcome(
                            f"V11LWC-{t_close}-{tag[0].upper()}", "CANDIDATE", t_close, p0, s
                        )
        for key, arm in eng.armed.items():
            if int(arm["armed_ms"]) == t_close:
                fam, side = key[0], "LONG" if key.endswith("+1") else "SHORT"
                self.j_ep.append(
                    "ARMED",
                    {
                        "ts": _iso(t_close),
                        "ts_ms": t_close,
                        "family": fam,
                        "side": side,
                        "score": arm["score"],
                        "valid_until": _iso(arm["until_ms"]),
                        "family_structure_label": labels[fam][side],
                        "control_twin_armed": key in tw.armed,
                        "fib_bucket_atr": ctx.get("fib", {}).get("bucket_atr"),
                    },
                )
                self.state["counters"]["armed"] += 1
        for fam in FAMILIES:
            for s in (1, -1):
                key = f"{fam}{s:+d}"
                arm_nm = eng.armed.get(key)
                sc = eng._score(s, i)
                if (
                    arm_nm is not None
                    and eng.trigger(fam, s, i)
                    and math.isfinite(sc)
                    and th.entry - 10 <= sc < th.entry
                    and (rec is None or rec.get("signal_id") is None)
                ):
                    self.state["counters"]["nearmiss"] += 1
                    self.j_nm.append(
                        "NEAR_MISS",
                        {
                            "ts": _iso(t_close),
                            "ts_ms": t_close,
                            "family": fam,
                            "side": "LONG" if s > 0 else "SHORT",
                            "score": sc,
                            "entry_threshold": th.entry,
                            "contrib": eng._contrib(s, i),
                            "micro": micro,
                            "family_structure_label": labels[fam]["LONG" if s > 0 else "SHORT"],
                            "context": ctx,
                        },
                    )
                    self._queue_outcome(
                        f"V11LWN-{t_close}-{fam}{'L' if s > 0 else 'S'}",
                        "NEAR_MISS",
                        t_close,
                        float(eng.X["close"][i]),
                        s,
                    )
        if rec is not None:
            self.state["counters"]["executed" if rec["action"] == "EXECUTED" else "blocked"] += 1
            if "POSITION_OPEN" in (rec.get("blockers") or []):
                self.state["counters"]["skipped_while_open"] += 1
            ctl = self.control_assessment(
                eng, tw, rec["family"], int(rec["side_sign"]), i, twin_rec, twin_pos_before
            )
            rec["control_twin"] = ctl
            rec["incremental"] = not bool(ctl["would_take"])
            rec["context"] = ctx
            self.j_sig.append(
                "SIGNAL_" + rec["action"],
                {**rec, "ts": _iso(rec["decision_ms"]), "execution_mode": "SHADOW", "micro": micro},
            )
            self.state["last_signal"] = {
                "signal_id": rec["signal_id"],
                "action": rec["action"],
                "family": rec["family"],
                "side": rec["side"],
                "at": _iso(rec["decision_ms"]),
                "incremental": rec["incremental"],
                "control_reason": ctl["reason"],
            }
            self._queue_outcome(
                rec["signal_id"],
                rec["action"],
                int(rec["decision_ms"]),
                float(rec["entry_ref"]),
                int(rec["side_sign"]),
            )

    # ------------------------------------------------------------------ diagnostics
    def diagnostic(self) -> dict[str, Any]:
        d = super().diagnostic()
        tw = self.twin
        d["strategy"] = STRATEGY
        d["mode"] = "SHADOW_ONLY (warm-started V11, family structure as labels)"
        d["strategy_delta"] = STRATEGY_DELTA
        d["family_labels"] = self.last_labels
        d["context"] = self.last_context
        d["control_twin"] = {
            "armed": tw.armed if tw else {},
            "position": tw.pos.as_dict() if tw and tw.pos else None,
            "counters": tw.counters if tw else {},
            "trades": (self.state.get("twin") or {}).get("trades"),
            "cum_net_usd": (self.state.get("twin") or {}).get("cum_net_usd"),
        }
        d["written_ms"] = int(time.time() * 1000)
        return d


def read_state(paths: V11LiteWarmPaths) -> dict[str, Any]:
    return json.loads(paths.state.read_text()) if paths.state.exists() else {}
