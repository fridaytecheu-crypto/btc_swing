"""V10 engine: events in, features at a 1-second cadence, the six family machines, the immediate
decision on TRIGGERED (safety checks, stop, sizing), shadow or Demo execution, hash-chained journals,
near-misses, daily safety, forward outcomes appended later without touching the original records."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from btc_swing.core.enums import Side
from btc_swing.risk.sizing import size_position
from btc_swing.v5.demo.journal import HashChainJournal
from btc_swing.v10.config import (
    PREFIX_EPISODE,
    PREFIX_SIGNAL,
    STRATEGY,
    V10_FAMILY_VERSION,
    V10_FEATURE_VERSION,
    V10_RISK_VERSION,
    V10Config,
)
from btc_swing.v10.families import Family, Transition, all_families
from btc_swing.v10.features import Bar, FeatureEngine
from btc_swing.v10.paths import V10Paths
from btc_swing.v10.shadow import ShadowBook

log = logging.getLogger(__name__)
DAY_MS = 86_400_000


class ExecutorLike(Protocol):
    def enter(self, sig: dict[str, Any], f: dict[str, float]) -> dict[str, Any]: ...
    def manage(self, now_ms: int) -> dict[str, Any]: ...
    def has_position(self) -> bool: ...
    def closed_trades_since(self, t_ms: int) -> list[dict[str, Any]]: ...


def _iso(ms: int | float | None) -> str | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _clean(v: Any) -> Any:
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_clean(x) for x in v]
    return v


def _hid(prefix: str, *parts: Any) -> str:
    return prefix + hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _day(ms: int) -> int:
    return ms // DAY_MS


class V10Engine:
    def __init__(
        self,
        cfg: V10Config,
        paths: V10Paths,
        observation_start_ms: int,
        mode: str = "SHADOW",
        executor: ExecutorLike | None = None,
    ) -> None:
        self.cfg, self.paths = cfg, paths
        self.mode = mode  # SHADOW | DEMO
        self.executor = executor
        self.observation_start_ms = observation_start_ms
        paths.ensure()
        self.features = FeatureEngine(cfg, on_bar=self._on_bar)
        self.families: list[Family] = all_families(cfg)
        self.shadow = ShadowBook(cfg)
        self.j_ep = HashChainJournal(paths.episodes, "v10_episodes")
        self.j_sig = HashChainJournal(paths.signals, "v10_signals")
        self.j_nm = HashChainJournal(paths.nearmiss, "v10_nearmiss")
        self.j_sh = HashChainJournal(paths.shadow_trades, "v10_shadow_trades")
        self.j_out = HashChainJournal(paths.outcomes, "v10_outcomes")
        self.state: dict[str, Any] = self._default_state()
        self.last_features: dict[str, float] = {}
        self.pending_outcomes: list[dict[str, Any]] = []  # {id, kind, t_ms, p0, side, done:[h]}
        self._load_state()
        self.started_ms = int(time.time() * 1000)

    # ------------------------------------------------------------------ state
    def _default_state(self) -> dict[str, Any]:
        return {
            "strategy": STRATEGY,
            "mode": self.mode,
            "observation_start_ms": self.observation_start_ms,
            "families": {},
            "shadow_position": None,
            "executed_ms": [],  # rolling 24h executed decisions
            "day": None,
            "day_realised_usd": 0.0,
            "consecutive_full_losses": 0,
            "blocked_until_ms": None,
            "blocked_reason": None,
            "nearmiss_last": {},
            "nearmiss_hour": [0, 0],  # [hour index, count]
            "counters": {
                "armed": 0,
                "triggered": 0,
                "executed": 0,
                "blocked": 0,
                "nearmiss": 0,
                "invalidated": 0,
                "expired": 0,
            },
            "last_tick_ms": None,
            "last_event_ms": None,
            "last_signal": None,
            "history": {},
            "pending_outcomes": [],
            "cum_shadow_net_usd": 0.0,
            "shadow_wins": 0,
            "shadow_losses": 0,
        }

    def _load_state(self) -> None:
        p = self.paths.state
        if not p.exists():
            return
        try:
            st = json.loads(p.read_text())
        except json.JSONDecodeError:
            return
        if st.get("observation_start_ms") != self.observation_start_ms:
            log.warning("state.json belongs to another observation start; starting fresh")
            return
        self.state = {**self._default_state(), **st, "mode": self.mode}
        for fam in self.families:
            d = self.state["families"].get(f"{fam.family}{fam.st.side}")
            if d:
                fam.st.__dict__.update({k: v for k, v in d.items() if k in fam.st.__dict__})
                if fam.st.state == "TRIGGERED":  # a decision was never recorded: drop it
                    fam.reset_after_decision("RESTART_DROP")
        self.shadow.restore(self.state.get("shadow_position"))
        for k, vals in (self.state.get("history") or {}).items():
            if k in self.features.hist:
                for v in vals:
                    self.features.hist[k].add(float(v))
        self.pending_outcomes = list(self.state.get("pending_outcomes") or [])
        self._reload_bars()

    def _reload_bars(self) -> None:
        """Completed 1m bars from disk (last 24 h) so a restart keeps ATR / structure warm."""
        p = self.paths.bars_1m
        if not p.exists():
            return
        rows = read_jsonl(p)[-1440:]
        for r in rows:
            self.features.bars.done.append(
                Bar(
                    int(r["open_ms"]),
                    float(r["o"]),
                    float(r["h"]),
                    float(r["l"]),
                    float(r["c"]),
                    float(r["vol"]),
                    float(r["buy"]),
                    float(r["sell"]),
                    int(r["n"]),
                )
            )

    def save(self) -> None:
        self.state["families"] = {f"{f.family}{f.st.side}": f.st.as_dict() for f in self.families}
        self.state["shadow_position"] = self.shadow.state()
        self.state["history"] = {k: list(h.vals) for k, h in self.features.hist.items()}
        self.state["pending_outcomes"] = self.pending_outcomes[-5000:]
        tmp = self.paths.state.with_suffix(".tmp")
        tmp.write_text(json.dumps(_clean(self.state), sort_keys=True))
        tmp.replace(self.paths.state)

    # ------------------------------------------------------------------ ingestion
    def _on_bar(self, b: Bar) -> None:
        with self.paths.bars_1m.open("a") as fh:
            fh.write(json.dumps(b.as_dict(), sort_keys=True) + "\n")

    def on_event(self, ev: dict[str, Any]) -> None:
        n0 = self.features.n_trades
        self.features.on_event(ev)
        self.state["last_event_ms"] = ev.get("rx_ms")
        if self.features.n_trades > n0 and self.shadow.pos is not None:
            for t in list(self.features.trades)[-(self.features.n_trades - n0) :]:
                closed = self.shadow.on_trade(t)
                if closed is not None:
                    self._shadow_closed(closed)
                    break

    # ------------------------------------------------------------------ tick
    def tick(self, now_ms: int | None = None) -> dict[str, Any]:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        f = self.features.compute(now)
        self.last_features = f
        self.state["last_tick_ms"] = now
        self._roll_day(now)
        out: dict[str, Any] = {"now": now, "transitions": [], "decisions": []}
        closed = self.shadow.on_tick(now, f.get("last", math.nan))
        if closed is not None:
            self._shadow_closed(closed)
        if self.executor is not None:
            try:
                m = self.executor.manage(now)
                if m.get("action") == "closed":
                    self._demo_closed(m.get("trade") or {}, now)
            except Exception as e:  # the observation continues; the error is journaled
                log.exception("demo manage failed: %s", e)
                self.j_sig.append("DEMO_MANAGE_ERROR", {"error": str(e)[:300], "at": _iso(now)})
        if now < self.observation_start_ms:
            return out  # warm-up before the prospective start: features only, no transition
        for fam in self.families:
            ep_id = _hid(PREFIX_EPISODE, fam.family, fam.st.side, now)
            for tr in fam.step(f, now, ep_id):
                out["transitions"].append(tr.kind)
                self._journal_transition(fam, tr, f)
                if tr.kind == "TRIGGERED":
                    out["decisions"].append(self._decide(fam, tr, f, now))
                    fam.reset_after_decision("DECIDED")
            nm = fam.near_miss(f)
            if nm is not None:
                self._near_miss(fam, nm, f, now)
        self._outcomes(now)
        return out

    def _roll_day(self, now: int) -> None:
        d = _day(now)
        if self.state["day"] != d:
            self.state["day"] = d
            self.state["day_realised_usd"] = 0.0
            self.state["consecutive_full_losses"] = 0
            if self.state.get("blocked_until_ms") and now >= int(self.state["blocked_until_ms"]):
                self.state["blocked_until_ms"] = None
                self.state["blocked_reason"] = None
        self.state["executed_ms"] = [t for t in self.state["executed_ms"] if now - t < DAY_MS]

    # ------------------------------------------------------------------ journals
    def _base(self, fam: Family, now: int) -> dict[str, Any]:
        return {
            "strategy": STRATEGY,
            "family": fam.family,
            "side": "LONG" if fam.st.side > 0 else "SHORT",
            "side_sign": fam.st.side,
            "episode_id": fam.st.episode_id,
            "ts": _iso(now),
            "ts_ms": now,
            "feature_version": V10_FEATURE_VERSION,
            "family_version": V10_FAMILY_VERSION,
            "execution_mode": self.mode,
        }

    def _journal_transition(self, fam: Family, tr: Transition, f: dict[str, float]) -> None:
        rec = self._base(fam, tr.now_ms) | {
            "conditions": tr.conditions,
            "detail": tr.detail,
            "armed_ts": _iso(fam.st.armed_ms),
            "event_ts": _iso(
                int(fam.st.arm_features.get("now_ms", tr.now_ms))
                if fam.st.arm_features
                else tr.now_ms
            ),
        }
        if tr.kind == "ARMED":
            rec["features"] = _clean(f)
            self.state["counters"]["armed"] += 1
            self._queue_outcome(
                fam.st.episode_id or "", "ARMED", tr.now_ms, f.get("last", math.nan), fam.st.side
            )
        elif tr.kind == "INVALIDATED":
            self.state["counters"]["invalidated"] += 1
        elif tr.kind == "EXPIRED":
            self.state["counters"]["expired"] += 1
        elif tr.kind == "TRIGGERED":
            self.state["counters"]["triggered"] += 1
        self.j_ep.append(f"EPISODE_{tr.kind}", rec)

    # ------------------------------------------------------------------ decision
    def _stop_for(self, fam: Family, f: dict[str, float]) -> tuple[float, float, str]:
        s = float(fam.st.side)
        last, a5 = f["last"], f.get("atr5", math.nan)
        sc = self.cfg.stop
        struct = f.get("swing_lo_5m", math.nan) if s > 0 else f.get("swing_hi_5m", math.nan)
        cands = [x for x in (fam.st.extreme, struct) if math.isfinite(x)]
        level = (min(cands) if s > 0 else max(cands)) - s * sc.buffer_atr5m * a5
        dist = s * (last - level)
        note = "STRUCTURAL"
        if dist < last * sc.floor_pct / 100.0:
            dist = last * sc.floor_pct / 100.0
            level = last - s * dist
            note = "FLOORED"
        return level, 100.0 * dist / last, note

    def _safety_blockers(self, fam: Family, f: dict[str, float], now: int) -> list[str]:
        sc, fc = self.cfg.safety, self.cfg.features
        b: list[str] = []
        if f.get("warm", 0.0) != 1.0:
            b.append("WARMUP")
        if not (f.get("trade_age_s", math.inf) <= fc.trade_feed_max_age_s):
            b.append("TRADE_FEED_STALE")
        if self.has_open_position():
            b.append("POSITION_OPEN")
        if self.state.get("blocked_until_ms") and now < int(self.state["blocked_until_ms"]):
            b.append(f"DAILY_SAFETY:{self.state.get('blocked_reason')}")
        if len(self.state["executed_ms"]) >= sc.max_trades_rolling_24h:
            b.append("MAX_TRADES_24H")
        if fam.st.armed_ms is not None and now - fam.st.armed_ms < 0:
            b.append("CLOCK")
        return b

    def has_open_position(self) -> bool:
        if self.executor is not None:
            return self.executor.has_position()
        return self.shadow.pos is not None

    def _decide(self, fam: Family, tr: Transition, f: dict[str, float], now: int) -> dict[str, Any]:
        wall = int(time.time() * 1000)
        t_decide = now  # the tick clock (== wall clock live; simulated in replay / tests)
        latency = max(0, wall - tr.now_ms) if wall >= now else 0
        s = fam.st.side
        last = f["last"]
        sig_id = _hid(PREFIX_SIGNAL, fam.st.episode_id, tr.now_ms)
        rec = self._base(fam, now) | {
            "signal_id": sig_id,
            "event_ts": _iso(int(fam.st.arm_features.get("now_ms", now))),
            "armed_ts": _iso(fam.st.armed_ms),
            "trigger_ts": _iso(tr.now_ms),
            "decision_ts": _iso(t_decide),
            "decision_wall_ts": _iso(wall),
            "decision_latency_ms": latency,
            "trigger_conditions": tr.conditions,
            "features": _clean(f),
            "arm_features": _clean(fam.st.arm_features),
            "risk_version": V10_RISK_VERSION,
        }
        blockers = self._safety_blockers(fam, f, now)
        stop, stop_pct, stop_note = self._stop_for(fam, f)
        if stop_pct > self.cfg.stop.cap_pct:
            blockers.append("STOP_TOO_WIDE")
        if latency > self.cfg.safety.decision_max_delay_s * 1000:
            blockers.append("ENTRY_TOO_LATE")
        dist = abs(last - stop)
        ex = self.cfg.exits
        rec.update(
            {
                "entry_ref": last,
                "stop": stop,
                "stop_pct": stop_pct,
                "stop_note": stop_note,
                "tp1": last + s * ex.tp1_r * dist,
                "tp2": last + s * ex.tp2_r * dist,
            }
        )
        sz = size_position(
            self.cfg.risk.initial_equity,
            last,
            stop,
            Side.LONG if s > 0 else Side.SHORT,
            f.get("atr1h", math.nan),
            self.cfg.risk,
        )
        rec.update(
            {
                "qty": float(sz.qty) if sz.accepted else 0.0,
                "leverage": float(sz.leverage) if sz.accepted else 0.0,
                "risk_usdt": float(sz.risk_amount) if sz.accepted else 0.0,
                "sizing_reason": sz.reason,
            }
        )
        if not sz.accepted:
            blockers.append(f"SIZING:{sz.reason}")
        if blockers:
            rec["action"], rec["blockers"] = "BLOCKED", blockers
            self.state["counters"]["blocked"] += 1
            self.j_sig.append("SIGNAL_BLOCKED", rec)
            self._queue_outcome(sig_id, "BLOCKED", now, last, s)
            self.state["last_signal"] = {
                "signal_id": sig_id,
                "action": "BLOCKED",
                "at": _iso(now),
                "family": fam.family,
                "side": rec["side"],
            }
            self.save()
            return rec
        rec["action"] = "EXECUTED"
        rec["decision_ms"] = t_decide
        self.state["counters"]["executed"] += 1
        self.state["executed_ms"].append(now)
        self.j_sig.append("SIGNAL_EXECUTED", rec)
        self._queue_outcome(sig_id, "EXECUTED", now, last, s)
        if self.executor is not None:
            try:
                rec["demo"] = self.executor.enter(rec, f)
            except Exception as e:
                log.exception("demo enter failed: %s", e)
                rec["demo"] = {"action": "ERROR", "error": str(e)[:300]}
                self.j_sig.append("DEMO_ENTER_ERROR", {"signal_id": sig_id, "error": str(e)[:300]})
        else:
            pos = self.shadow.open(rec, f)
            self.j_sh.append("SHADOW_OPEN", {"signal_id": sig_id, **pos.as_dict()})
        self.state["last_signal"] = {
            "signal_id": sig_id,
            "action": "EXECUTED",
            "at": _iso(now),
            "family": fam.family,
            "side": rec["side"],
        }
        self.save()
        return rec

    # ------------------------------------------------------------------ closes and safety
    def _register_close(self, net_usd: float, net_r: float, now: int) -> None:
        self.state["day_realised_usd"] = float(self.state["day_realised_usd"]) + net_usd
        if net_r <= -0.9:
            self.state["consecutive_full_losses"] = int(self.state["consecutive_full_losses"]) + 1
        elif net_usd > 0:
            self.state["consecutive_full_losses"] = 0
        sc = self.cfg.safety
        limit = -sc.daily_stop_frac * self.cfg.risk.initial_equity
        reason = None
        if self.state["day_realised_usd"] <= limit:
            reason = "DAILY_STOP"
        elif self.state["consecutive_full_losses"] >= sc.max_consecutive_losses:
            reason = "CONSECUTIVE_LOSSES"
        if reason and not self.state.get("blocked_until_ms"):
            self.state["blocked_until_ms"] = (_day(now) + 1) * DAY_MS
            self.state["blocked_reason"] = reason
            self.j_sig.append(
                "DAILY_SAFETY_BLOCK",
                {
                    "reason": reason,
                    "until": _iso(self.state["blocked_until_ms"]),
                    "day_realised_usd": self.state["day_realised_usd"],
                },
            )

    def _shadow_closed(self, trade: dict[str, Any], now: int | None = None) -> None:
        t = now if now is not None else int(trade["exit_ms"])
        self.j_sh.append("SHADOW_CLOSED", trade)
        self.state["cum_shadow_net_usd"] = float(self.state["cum_shadow_net_usd"]) + float(
            trade["net_usd"]
        )
        if float(trade["net_usd"]) > 0:
            self.state["shadow_wins"] = int(self.state["shadow_wins"]) + 1
        else:
            self.state["shadow_losses"] = int(self.state["shadow_losses"]) + 1
        if self.executor is None:
            self._register_close(float(trade["net_usd"]), float(trade["net_r"]), t)
        self.save()

    def _demo_closed(self, trade: dict[str, Any], now: int) -> None:
        net = float(trade.get("net_pnl") or 0.0)
        r = float(trade.get("R_net_vs_planned") or 0.0)
        self._register_close(net, r, now)
        self.save()

    # ------------------------------------------------------------------ near-misses
    def _near_miss(
        self, fam: Family, nm: tuple[str, str, dict[str, bool]], f: dict[str, float], now: int
    ) -> None:
        stage, failing, conds = nm
        key = f"{fam.family}{fam.st.side}:{stage}"
        last = self.state["nearmiss_last"].get(key)
        if last is not None and now - int(last) < self.cfg.outcomes.nearmiss_dedupe_s * 1000:
            return
        hour = now // 3_600_000
        h_idx, h_cnt = self.state["nearmiss_hour"]
        if h_idx != hour:
            h_idx, h_cnt = hour, 0
        if h_cnt >= 120:
            return
        self.state["nearmiss_hour"] = [h_idx, h_cnt + 1]
        self.state["nearmiss_last"][key] = now
        nm_id = _hid("V10N-", key, now)
        self.state["counters"]["nearmiss"] += 1
        self.j_nm.append(
            "NEAR_MISS",
            self._base(fam, now)
            | {
                "nearmiss_id": nm_id,
                "stage": stage,
                "failing": failing,
                "conditions": conds,
                "features": _clean(f),
            },
        )
        self._queue_outcome(nm_id, f"NEARMISS_{stage}", now, f.get("last", math.nan), fam.st.side)

    # ------------------------------------------------------------------ outcomes
    def _queue_outcome(self, rid: str, kind: str, t_ms: int, p0: float, side: int) -> None:
        if not math.isfinite(p0):
            return
        self.pending_outcomes.append(
            {"id": rid, "kind": kind, "t_ms": t_ms, "p0": p0, "side": side, "done": []}
        )

    def _outcomes(self, now: int) -> None:
        if not self.pending_outcomes:
            return
        bars = list(self.features.bars.done)
        if not bars:
            return
        keep: list[dict[str, Any]] = []
        for po in self.pending_outcomes:
            for h in self.cfg.outcomes.horizons_min:
                if h in po["done"]:
                    continue
                end = po["t_ms"] + h * 60_000
                if now < end + 65_000:
                    continue
                win = [b for b in bars if po["t_ms"] <= b.open_ms + 60_000 and b.open_ms < end]
                if not win:
                    po["done"].append(h)
                    continue
                s = float(po["side"])
                p0 = float(po["p0"])
                hi, lo, close = max(b.h for b in win), min(b.lo for b in win), win[-1].c
                self.j_out.append(
                    "OUTCOME",
                    {
                        "id": po["id"],
                        "kind": po["kind"],
                        "horizon_min": h,
                        "t_ms": po["t_ms"],
                        "p0": p0,
                        "side": "LONG" if s > 0 else "SHORT",
                        "return_bps": s * (close / p0 - 1) * 1e4,
                        "mfe_bps": (hi / p0 - 1) * 1e4 if s > 0 else (1 - lo / p0) * 1e4,
                        "mae_bps": (lo / p0 - 1) * 1e4 if s > 0 else (1 - hi / p0) * 1e4,
                        "max_up_bps": (hi / p0 - 1) * 1e4,
                        "max_down_bps": (lo / p0 - 1) * 1e4,
                        "bars": len(win),
                    },
                )
                po["done"].append(h)
            if len(po["done"]) < len(self.cfg.outcomes.horizons_min):
                keep.append(po)
        self.pending_outcomes = keep

    # ------------------------------------------------------------------ diagnostics
    def diagnostic(self) -> dict[str, Any]:
        f = self.last_features
        fams = []
        for fam in self.families:
            if fam.st.state == "IDLE":
                conds = fam.arm_conditions(f) if f else {}
            else:
                conds = fam.trigger_conditions(f) if f else {}
            fams.append(
                {
                    "family": fam.family,
                    "side": "LONG" if fam.st.side > 0 else "SHORT",
                    "state": fam.st.state,
                    "reason": fam.st.last_reason,
                    "conditions": conds,
                    "armed_ts": _iso(fam.st.armed_ms),
                    "episode_id": fam.st.episode_id,
                }
            )
        return {
            "features": _clean(f),
            "families": fams,
            "history": self.features.history_state(),
            "blocked_until": _iso(self.state.get("blocked_until_ms")),
            "blocked_reason": self.state.get("blocked_reason"),
            "open_position": self.has_open_position(),
        }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with path.open() as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out
