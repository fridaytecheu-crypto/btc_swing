"""Chronological ONE-position BTC portfolio simulator over engine parents (benchmark ledger).

Policy (initial, frozen): at most one open position; no pyramiding; no long + short hedge; a later signal
while a position is open is telemetry only (recorded as OCCUPIED, never traded); two opposite eligible
signals at the same decision time -> ABSTAIN both; the owning engine controls its own exit and no other
engine can touch the open position. Engine priority is NOT optimised on PnL: ties are resolved by the
fixed order given by the caller (documented in the manifest)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from btc_swing.research.intraday_portfolio.episodes import ParentEpisode


@dataclass
class PortfolioRow:
    parent_id: str
    engine: str
    side: str
    decision_ms: int
    entry_ms: int | None
    exit_ms: int | None
    status: str  # TRADED / OCCUPIED / ABSTAIN_OPPOSITE / NOT_ELIGIBLE
    net_bps: float | None
    net_r: float | None
    gross_bps: float | None
    hold_min: float | None
    exit_reason: str | None
    blocked_by: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class PortfolioResult:
    name: str
    rows: list[PortfolioRow] = field(default_factory=list)

    @property
    def trades(self) -> list[PortfolioRow]:
        return [r for r in self.rows if r.status == "TRADED"]

    def occupancy_loss(self) -> int:
        return sum(1 for r in self.rows if r.status == "OCCUPIED")


def _decision_ms(p: ParentEpisode) -> int:
    return int(p.times.decision_time or p.first_setup_time_ms)


def simulate(
    name: str, parents: list[ParentEpisode], engine_order: tuple[str, ...] = ("E1", "E2")
) -> PortfolioResult:
    """Only CLOSED parents (an eligible entry with a resolved exit) can trade; every parent is a row."""
    res = PortfolioResult(name)
    order = {e: k for k, e in enumerate(engine_order)}
    ps = sorted(parents, key=lambda p: (_decision_ms(p), order.get(p.engine, 99), p.parent_id))
    open_until: int | None = None
    open_id: str | None = None
    i = 0
    while i < len(ps):
        t = _decision_ms(ps[i])
        group = [ps[i]]
        j = i + 1
        while j < len(ps) and _decision_ms(ps[j]) == t:
            group.append(ps[j])
            j += 1
        eligible = [p for p in group if p.traded]
        sides = {p.side for p in eligible}
        for p in group:
            row = PortfolioRow(
                parent_id=p.parent_id,
                engine=p.engine,
                side=p.side,
                decision_ms=t,
                entry_ms=p.times.benchmark_entry_time,
                exit_ms=p.exit.time_ms if p.exit else None,
                status="NOT_ELIGIBLE",
                net_bps=p.net_bps,
                net_r=p.net_r,
                gross_bps=p.gross_bps,
                hold_min=p.hold_min,
                exit_reason=p.exit.reason if p.exit else None,
            )
            res.rows.append(row)
            if not p.traded:
                continue
            if open_until is not None and t < open_until:
                row.status, row.blocked_by = "OCCUPIED", open_id
                continue
            if len(sides) > 1:
                row.status = "ABSTAIN_OPPOSITE"
                continue
            if open_until is not None and t >= open_until:
                open_until, open_id = None, None
            row.status = "TRADED"
            open_until = int(p.exit.time_ms) if p.exit and p.exit.time_ms else t
            open_id = p.parent_id
        i = j
    return res
