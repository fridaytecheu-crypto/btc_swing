"""V11-LITE engine (bookkeeping model `v11lw-book-2`): the frozen V11 engine with ONE economic change and
ONE arm state per side.

Economic change (unchanged since f885f85): the A/B/C family structure never gates arming; arming needs
score >= armed threshold and data validity only (close and ATR5 finite, ATR5 > 0, V11's own first guard).

Bookkeeping: a side is ARMED while at least one frozen trigger/stop TEMPLATE (A, B, C) is available to it.
Every refresh (valid data and score >= armed) re-arms all three templates at once; a template stays
available until its frozen `armed_valid_min` window lapses (A / B 30 min, C 10 min) or until it is
consumed by a decision. A new arm START (arm episode) is counted only on the NOT_ARMED -> ARMED transition
of the side. This is the f885f85 per-(family, side) key dictionary written as one state per side: every
key of a side was always refreshed together (same validity and score test), so `available_templates`
reproduces exactly the set of keys the old dictionary held, and the decision loop below evaluates the
templates in the identical order (A LONG, A SHORT, B LONG, B SHORT, C LONG, C SHORT). Decisions, entries,
stops, targets, exits and costs are inherited untouched from `V11Engine` (proved by replay against the
f885f85 engine in tests/v11_lite_warm)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from btc_swing.v11.engine import FAMILIES, MS_1M, V11Engine
from btc_swing.v11_lite_warm.config import PREFIX_SIGNAL

TEMPLATES = FAMILIES  # frozen trigger / stop templates, frozen evaluation order
BOOKKEEPING_VERSION = "v11lw-book-2"
SIDE_NAME = {1: "LONG", -1: "SHORT"}


class V11LiteEngine(V11Engine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # side (+1 / -1) -> {"episode_id", "since_ms", "armed_ms", "score", "consumed": [templates]}
        self.side_arm: dict[int, dict[str, Any]] = {}
        self.arm_starts_now: list[
            int
        ] = []  # sides whose arm episode started on the last processed row
        self.triggered_now: dict[
            int, list[str]
        ] = {}  # side -> templates whose trigger fired (pre-decision)
        self.counters["arm_episodes"] = 0
        self.counters["trigger_side_minutes"] = 0
        self.by_template: dict[str, int] = {
            t: 0 for t in TEMPLATES
        }  # decisions per selected template

    # ------------------------------------------------------------------ the economic delta
    def structure(self, fam: str, s: int, i: int) -> bool:
        """Data validity only (close and ATR5 finite, ATR5 > 0); the family structure never gates."""
        c, a5 = self.X["close"][i], self.X["atr5"][i]
        return bool(np.isfinite(c) and np.isfinite(a5) and a5 > 0)

    def frozen_structure(self, fam: str, s: int, i: int) -> bool:
        """The frozen V11 structure condition, as a LABEL only (never consulted by arming)."""
        return V11Engine.structure(self, fam, s, i)

    def structure_labels(self, i: int) -> dict[str, dict[str, bool]]:
        return {
            t: {"LONG": self.frozen_structure(t, 1, i), "SHORT": self.frozen_structure(t, -1, i)}
            for t in TEMPLATES
        }

    # ------------------------------------------------------------------ side-level arm state
    def _valid_min(self, t: str) -> int:
        f = self.cfg.families
        return {"A": f.a.armed_valid_min, "B": f.b.armed_valid_min, "C": f.c.armed_valid_min}[t]

    def available_templates(self, s: int, t_close: int) -> list[str]:
        st = self.side_arm.get(s)
        if st is None:
            return []
        consumed = set(st.get("consumed") or [])
        armed_ms = int(st["armed_ms"])
        return [
            t
            for t in TEMPLATES
            if t not in consumed and t_close <= armed_ms + self._valid_min(t) * MS_1M
        ]

    def side_armed(self, s: int, t_close: int) -> bool:
        return bool(self.available_templates(s, t_close))

    # ------------------------------------------------------------------ per-row processing
    def process_row(self, i: int, in_window: bool = True) -> dict[str, Any] | None:
        t_close = int(self.t_open[i]) + MS_1M
        self.last_close_ms = t_close
        self.arm_starts_now = []
        self.triggered_now = {}
        if self.pos is not None:
            self._manage(i)
        if not in_window or not self.fr.valid[i]:
            return None
        th = self.cfg.thresholds
        for s in (1, -1):  # candidate counter: verbatim V11Engine
            sc = self._score(s, i)
            if math.isfinite(sc) and sc >= th.candidate:
                key = f"cand{s}"
                last = self._cand_last.get(key, -(10**18))
                if t_close - last >= self.cfg.outcomes.candidate_dedupe_min * MS_1M:
                    self._cand_last[key] = t_close
                    self.counters["candidates"] += 1
        data_ok = self.structure("A", 1, i)  # side / template independent validity
        for s in (1, -1):
            sc = self._score(s, i)
            was_armed = self.side_armed(s, t_close)
            if data_ok and math.isfinite(sc) and sc >= th.armed:
                if was_armed:
                    st = self.side_arm[s]
                    st.update({"armed_ms": t_close, "score": sc, "consumed": []})
                else:
                    self.side_arm[s] = {
                        "episode_id": f"{SIDE_NAME[s][0]}-{t_close}",
                        "since_ms": t_close,
                        "armed_ms": t_close,
                        "score": sc,
                        "consumed": [],
                    }
                    self.arm_starts_now.append(s)
                    self.counters["armed"] += 1
                    self.counters["arm_episodes"] += 1
            elif not was_armed:
                self.side_arm.pop(s, None)
        decision: dict[str, Any] | None = None
        for t in TEMPLATES:  # frozen order: A LONG, A SHORT, B LONG, B SHORT, C LONG, C SHORT
            for s in (1, -1):
                if decision is not None:
                    break
                if t not in self.available_templates(s, t_close):
                    continue
                if not self.trigger(t, s, i):
                    continue
                if s not in self.triggered_now:
                    self.counters["trigger_side_minutes"] += 1
                    self.counters["triggers"] += 1
                self.triggered_now.setdefault(s, []).append(t)
                sc = self._score(s, i)
                if not (math.isfinite(sc) and sc >= th.entry):
                    continue
                st = self.side_arm[s]
                arm = {
                    "armed_ms": int(st["armed_ms"]),
                    "score": float(st["score"]),
                    "until_ms": int(st["armed_ms"]) + self._valid_min(t) * MS_1M,
                    "episode_id": st["episode_id"],
                }
                decision = self._decide(t, s, i, arm)
                st["consumed"] = [*st.get("consumed", []), t]
        return decision

    def _decide(self, fam: str, s: int, i: int, arm: dict[str, Any]) -> dict[str, Any]:
        rec = super()._decide(fam, s, i, arm)
        rec["signal_id"] = PREFIX_SIGNAL + str(rec["signal_id"]).removeprefix("V11S-")
        rec["selected_template"] = fam
        rec["structure_labels"] = {t: self.frozen_structure(t, s, i) for t in TEMPLATES}
        rec["arm_episode_id"] = arm.get("episode_id")
        rec["arm_since_ms"] = int(self.side_arm[s]["since_ms"]) if s in self.side_arm else None
        self.by_template[fam] += 1
        return rec

    # ------------------------------------------------------------------ persistence helpers
    def side_arm_state(self) -> dict[str, Any]:
        return {str(s): dict(v) for s, v in self.side_arm.items()}

    def restore_side_arm(self, raw: dict[str, Any] | None) -> None:
        self.side_arm = {}
        for k, v in (raw or {}).items():
            if isinstance(v, dict) and {"episode_id", "since_ms", "armed_ms", "score"} <= set(v):
                self.side_arm[int(k)] = {**v, "consumed": list(v.get("consumed") or [])}
