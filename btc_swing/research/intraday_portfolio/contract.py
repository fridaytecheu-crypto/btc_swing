"""Common data contract: every event distinguishes exchange / receive / processing / decision /
benchmark-entry / executable-entry times; every input carries source, availability, freshness,
provenance and a synthetic-or-repaired flag. Missing or stale required inputs fail closed: they are never
coerced into a favourable value (no zero flow, no perfect-mid fill, no reconstructed liquidation)."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

MS_1M = 60_000
MS_5M = 300_000
MS_1H = 3_600_000


class Availability(StrEnum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"


class MissingInputError(ValueError):
    """A required input is missing, stale or synthetic where a real value is required."""


@dataclass(frozen=True)
class Provenance:
    source: str  # e.g. "bybit_trades_5m parquet", "bybit public ws raw store"
    availability: Availability
    freshness_ms: int | None = None  # age of the newest contributing observation at decision time
    provenance: str = ""  # file / channel / derivation note
    synthetic: bool = False  # True when any contributing observation was repaired or filled

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Input[T]:
    """A value together with where it came from. `require()` fails closed."""

    value: T | None
    prov: Provenance

    def require(self, name: str, allow_synthetic: bool = False) -> T:
        if self.value is None or self.prov.availability is Availability.MISSING:
            raise MissingInputError(f"{name}: missing ({self.prov.source})")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise MissingInputError(f"{name}: non-finite ({self.prov.source})")
        if self.prov.synthetic and not allow_synthetic:
            raise MissingInputError(
                f"{name}: synthetic / repaired input refused ({self.prov.source})"
            )
        return self.value


@dataclass(frozen=True)
class EventTimes:
    """All times are UTC epoch milliseconds. None = not available (never assumed)."""

    exchange_event_time: int | None
    local_receive_time: int | None
    processing_time: int | None
    decision_time: int | None
    benchmark_entry_time: int | None = None
    executable_entry_time: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def historical_bar(close_ms: int) -> EventTimes:
        """Historical archive bars: the exchange close time is known, receipt / processing are NOT
        observed (archive), the decision is at the bar close, the benchmark entry is the next print."""
        return EventTimes(
            exchange_event_time=close_ms,
            local_receive_time=None,
            processing_time=None,
            decision_time=close_ms,
            benchmark_entry_time=close_ms,
            executable_entry_time=None,
        )


@dataclass
class Rejection:
    code: str
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, **self.extra}


def bps(a: float, ref: float) -> float:
    return (a / ref) * 1e4 if ref > 0 else math.nan
