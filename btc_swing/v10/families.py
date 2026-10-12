"""The three V10 families (`v10-fam-1`, design section 3) as explicit state machines:
IDLE -> ARMED -> TRIGGERED, or ARMED -> INVALIDATED / EXPIRED. LONG and SHORT share one code path
through the sign `s`. Near-misses are condition vectors with exactly one failing condition."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from btc_swing.v10.config import V10Config

FAMILIES = ("A", "B", "C")
Conds = dict[str, bool]


def _ok(x: float) -> bool:
    return math.isfinite(x)


@dataclass
class FamState:
    family: str
    side: int
    state: str = "IDLE"
    episode_id: str | None = None
    armed_ms: int | None = None
    arm_price: float = math.nan
    extreme: float = math.nan  # adverse extreme since ARM (low for LONG, high for SHORT)
    favourable: float = math.nan
    absorption_seen: bool = False
    arm_features: dict[str, float] = field(default_factory=dict)
    last_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Transition:
    kind: str  # ARMED | TRIGGERED | INVALIDATED | EXPIRED
    family: str
    side: int
    now_ms: int
    conditions: Conds
    detail: dict[str, Any]


def absorption(f: dict[str, float], flow_pct: float, impact_pct: float) -> bool:
    return (
        _ok(f.get("abs_delta_pct_30", math.nan))
        and f["abs_delta_pct_30"] >= flow_pct
        and _ok(f.get("abs_impact_pct_30", math.nan))
        and f["abs_impact_pct_30"] <= impact_pct
    )


class Family:
    def __init__(self, family: str, side: int, cfg: V10Config) -> None:
        if family not in FAMILIES or side not in (1, -1):
            raise ValueError((family, side))
        self.family, self.s, self.cfg = family, float(side), cfg
        self.st = FamState(family, side)

    # ------------------------------------------------------------------ conditions
    def arm_conditions(self, f: dict[str, float]) -> Conds:
        s = self.s

        def g(k: str) -> float:
            return f.get(k, math.nan)

        if self.family == "A":
            c = self.cfg.families.a
            return {
                "liq_burst": _ok(g("liq_burst_pct_30"))
                and g("liq_burst_pct_30") >= c.liq_burst_pct,
                "liq_side": _ok(g("liq_imb_30")) and s * g("liq_imb_30") <= -c.liq_imbalance,
                "sell_flow" if s > 0 else "buy_flow": _ok(g("imb_30"))
                and s * g("imb_30") <= -c.flow_imbalance,
            }
        if self.family == "B":
            cb = self.cfg.families.b
            dist = g("dist_lo") if s > 0 else g("dist_hi")
            return {
                "flow_big": _ok(g("abs_delta_pct_30")) and g("abs_delta_pct_30") >= cb.flow_pct,
                "flow_against": _ok(g("imb_30")) and s * g("imb_30") <= -cb.flow_imbalance,
                "at_location": _ok(dist) and dist <= cb.location_atr5m,
            }
        cc = self.cfg.families.c
        repl = g("ask_repl") if s > 0 else g("bid_repl")
        book_ok = g("book_valid") != 1.0 or (_ok(repl) and repl <= cc.ask_deplete_max)
        return {
            "flow_big": _ok(g("abs_delta_pct_30")) and g("abs_delta_pct_30") >= cc.flow_pct,
            "flow_with": _ok(g("imb_30")) and s * g("imb_30") >= cc.flow_imbalance,
            "efficient": (
                _ok(g("abs_impact_pct_30"))
                and g("abs_impact_pct_30") >= cc.efficient_impact_pct
                and s * g("ret_sign_30") > 0
                and s * g("flow_sign_30") > 0
            ),
            "intensity": _ok(g("intensity_pct_10"))
            and g("intensity_pct_10") >= cc.intensity_pct_10,
            "liquidity_consumed": book_ok,
        }

    def trigger_conditions(self, f: dict[str, float]) -> Conds:
        s, st = self.s, self.st

        def g(k: str) -> float:
            return f.get(k, math.nan)

        last, a1, a5 = g("last"), g("atr1"), g("atr5")
        if self.family in ("A", "B"):
            c = self.cfg.families.a if self.family == "A" else self.cfg.families.b
            turn = (
                _ok(last)
                and _ok(a1)
                and _ok(st.extreme)
                and s * (last - st.extreme) >= c.turn_atr1m * a1
            )
            out = {
                "absorption_seen": st.absorption_seen,
                "flow_reversal": _ok(g("imb_10")) and s * g("imb_10") >= c.reversal_imbalance_10,
                "turn": turn,
            }
            if self.family == "B":
                cb = self.cfg.families.b
                repl = g("bid_repl") if s > 0 else g("ask_repl")
                out["liquidity_stable"] = g("book_valid") != 1.0 or (
                    _ok(repl) and repl >= cb.bid_replenish_min
                )
            return out
        cc = self.cfg.families.c
        level = g("range_hi_15") if s > 0 else g("range_lo_15")
        brk = _ok(last) and _ok(level) and s * (last - level) > 0
        return {
            "structure_break": brk,
            "no_chase": _ok(last)
            and _ok(level)
            and _ok(a5)
            and s * (last - level) <= cc.chase_max_atr5m * a5,
            "arm_travel": _ok(last)
            and _ok(a5)
            and _ok(st.arm_price)
            and s * (last - st.arm_price) <= cc.arm_travel_max_atr5m * a5,
        }

    def _invalidate_reason(self, f: dict[str, float]) -> str | None:
        s, st = self.s, self.st
        last, a5 = f.get("last", math.nan), f.get("atr5", math.nan)
        if self.family in ("A", "B"):
            c = self.cfg.families.a if self.family == "A" else self.cfg.families.b
            if _ok(last) and _ok(a5) and s * (last - st.arm_price) <= -c.invalidate_atr5m * a5:
                return "ADVERSE_PROGRESS_CONTINUED"
            return None
        imb = f.get("imb_30", math.nan)
        if _ok(imb) and s * imb < 0:
            return "FLOW_REVERSED"
        return None

    def _expire_s(self) -> int:
        fam = self.cfg.families
        return {"A": fam.a.expire_s, "B": fam.b.expire_s, "C": fam.c.expire_s}[self.family]

    # ------------------------------------------------------------------ step
    def step(self, f: dict[str, float], now_ms: int, new_episode_id: str) -> list[Transition]:
        """Advance the machine one tick; returns the transitions that happened (0 or 1 normally,
        2 when ARMED and TRIGGERED on the same tick is impossible by construction)."""
        out: list[Transition] = []
        st, s = self.st, self.s
        last = f.get("last", math.nan)
        if f.get("warm", 0.0) != 1.0 or not _ok(last):
            st.last_reason = "WARMUP" if f.get("warm", 0.0) != 1.0 else "NO_PRICE"
            return out
        if st.state == "IDLE":
            conds = self.arm_conditions(f)
            if all(conds.values()):
                st.state, st.episode_id, st.armed_ms = "ARMED", new_episode_id, now_ms
                st.arm_price = st.extreme = st.favourable = last
                st.absorption_seen = False
                st.arm_features = dict(f)
                st.last_reason = "ARMED"
                out.append(
                    Transition("ARMED", self.family, int(s), now_ms, conds, {"arm_price": last})
                )
            else:
                st.last_reason = "IDLE: " + ", ".join(k for k, v in conds.items() if not v)
            return out
        if st.state == "ARMED":
            st.extreme = min(st.extreme, last) if s > 0 else max(st.extreme, last)
            st.favourable = max(st.favourable, last) if s > 0 else min(st.favourable, last)
            if self.family in ("A", "B"):
                c = self.cfg.families.a if self.family == "A" else self.cfg.families.b
                if absorption(f, c.absorption_flow_pct, c.absorption_impact_pct):
                    st.absorption_seen = True
            why = self._invalidate_reason(f)
            if why is not None:
                out.append(
                    Transition("INVALIDATED", self.family, int(s), now_ms, {}, {"reason": why})
                )
                self._reset(why)
                return out
            if st.armed_ms is not None and now_ms - st.armed_ms >= self._expire_s() * 1000:
                tc = self.trigger_conditions(f)
                out.append(
                    Transition(
                        "EXPIRED",
                        self.family,
                        int(s),
                        now_ms,
                        tc,
                        {"missing": [k for k, v in tc.items() if not v]},
                    )
                )
                self._reset("EXPIRED")
                return out
            tc = self.trigger_conditions(f)
            if all(tc.values()):
                st.state = "TRIGGERED"
                st.last_reason = "TRIGGERED"
                out.append(
                    Transition(
                        "TRIGGERED",
                        self.family,
                        int(s),
                        now_ms,
                        tc,
                        {"arm_price": st.arm_price, "extreme": st.extreme, "armed_ms": st.armed_ms},
                    )
                )
            else:
                st.last_reason = "ARMED: waiting " + ", ".join(k for k, v in tc.items() if not v)
            return out
        return out  # TRIGGERED: the engine decides and resets

    def _reset(self, reason: str) -> None:
        self.st = FamState(self.family, int(self.s), last_reason=reason)

    def reset_after_decision(self, reason: str) -> None:
        self._reset(reason)

    def near_miss(self, f: dict[str, float]) -> tuple[str, str, Conds] | None:
        """(stage, failing condition, conditions) when exactly one condition fails."""
        if f.get("warm", 0.0) != 1.0:
            return None
        if self.st.state == "IDLE":
            c = self.arm_conditions(f)
            stage = "ARM"
        elif self.st.state == "ARMED":
            c = self.trigger_conditions(f)
            stage = "TRIGGER"
        else:
            return None
        bad = [k for k, v in c.items() if not v]
        if len(bad) == 1:
            return stage, bad[0], c
        return None


def all_families(cfg: V10Config) -> list[Family]:
    return [Family(fam, side, cfg) for fam in FAMILIES for side in (1, -1)]
