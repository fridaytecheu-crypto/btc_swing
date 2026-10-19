"""Parent-episode model: one underlying BTC market move = one parent episode. Repeated triggers from the
same move are recorded on the parent (`repeated_triggers`) and never counted as independent trades. Every
candidate is kept, including no-trade / rejected ones (eligibility_state + rejection_reasons)."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from btc_swing.research.intraday_portfolio.contract import EventTimes


@dataclass
class Exit:
    reason: str  # STOP / TARGET / VWAP / INVALIDATION / TIME / NONE
    time_ms: int | None
    price: float | None
    benchmark: bool = True  # benchmark (bar-based) exit price vs executable fill


@dataclass
class ParentEpisode:
    parent_id: str
    engine: str  # E1 / E2 / E4
    labels: list[str]  # engine labels, e.g. ["E1", "LONG", "DIRECTIONAL"]
    side: str  # LONG / SHORT
    first_setup_time_ms: int  # first observable setup time (exchange time of the completing bar)
    receipt_time_ms: int | None  # local receipt when observed live; None for archive replays
    times: EventTimes
    frozen_levels: dict[str, float]  # box high / low / vwap, atr, stop, target ...
    regime: str  # RANGE / DIRECTIONAL / UNAVAILABLE
    er: float
    eligibility_state: str = "CANDIDATE"  # CANDIDATE / ELIGIBLE / REJECTED / ENTERED / CLOSED
    rejection_reasons: list[str] = field(default_factory=list)
    repeated_triggers: int = 0
    repeated_trigger_times_ms: list[int] = field(default_factory=list)
    entry_benchmark: float | None = (
        None  # first executable-side print after the decision (archive: next bar open)
    )
    entry_executable: float | None = None  # actual / replayed bid-ask fill when quotes exist
    stop: float | None = None
    target: float | None = None
    stop_bps: float | None = None
    exit: Exit | None = None
    gross_bps: float | None = None  # side-correct benchmark return, before any cost
    cost_bps: float | None = None  # at the base scenario (16 bps) unless stated
    net_bps: float | None = None
    net_r: float | None = None
    mfe_bps: float | None = None
    mae_bps: float | None = None
    hold_min: float | None = None
    executable_replay: str = "UNAVAILABLE"  # UNAVAILABLE / AVAILABLE
    notes: dict[str, Any] = field(default_factory=dict)

    def mark_rejected(self, code: str) -> None:
        self.eligibility_state = "REJECTED"
        if code not in self.rejection_reasons:
            self.rejection_reasons.append(code)

    def add_repeat(self, t_ms: int) -> None:
        self.repeated_triggers += 1
        self.repeated_trigger_times_ms.append(int(t_ms))

    @property
    def traded(self) -> bool:
        return (
            self.eligibility_state == "CLOSED"
            and self.exit is not None
            and self.net_bps is not None
        )

    def as_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["times"] = self.times.as_dict()
        for k in ("labels", "rejection_reasons"):
            d[k] = "|".join(d[k])
        d["repeated_trigger_times_ms"] = "|".join(str(x) for x in self.repeated_trigger_times_ms)
        fl = d.pop("frozen_levels")
        for k, v in fl.items():
            d[f"lvl_{k}"] = v
        ex = d.pop("exit")
        d["exit_reason"] = ex["reason"] if ex else "NONE"
        d["exit_time_ms"] = ex["time_ms"] if ex else None
        d["exit_price"] = ex["price"] if ex else None
        notes = d.pop("notes")
        for k, v in notes.items():
            d[f"note_{k}"] = v if isinstance(v, int | float | str | bool) or v is None else str(v)
        t = d.pop("times")
        for k, v in t.items():
            d[f"t_{k}"] = v
        return d


def dedupe_key(engine: str, side: str, box_hour_ms: int, direction_tag: str) -> str:
    """Parent identity: engine, side and the frozen reference the move is measured against."""
    return f"{engine}-{side}-{box_hour_ms}-{direction_tag}"


def r_multiple(net_bps: float | None, stop_bps: float | None) -> float | None:
    if net_bps is None or stop_bps is None or not math.isfinite(stop_bps) or stop_bps <= 0:
        return None
    return net_bps / stop_bps
