"""Two cost ledgers, never mixed:

BENCHMARK ledger: gross benchmark return (side-correct, from the benchmark entry print to the benchmark
exit print) minus a modelled round-trip cost made of spread + modelled shortfall + latency + commission,
plus funding when the hold spans a funding time. Base research scenarios: 14 / 16 / 20 / 24 bps round
trip (taker / taker is the PRIMARY benchmark; 16 is the base).

EXECUTABLE ledger: gross return from actual / replayed bid-ask fills minus commission and funding ONLY
(the spread is already inside the fills: it is never subtracted a second time).

A predeclared PASSIVE-ENTRY study (same-side best quote, 5 s max wait, cancel remainder, no chasing,
partial fill protected at once) is supported as a bounded, model-dependent variant: a quote touch is
never treated as a fill; without queue data the result is labelled BOUNDED / MODEL_DEPENDENT."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

COST_SCENARIOS_BPS = (14.0, 16.0, 20.0, 24.0)
BASE_COST_BPS = 16.0
STRESS_COST_BPS = 24.0
TAKER_FEE_BPS = 5.5  # per side (frozen cost model shared with the forward runners)


@dataclass(frozen=True)
class CostBreakdown:
    """How a round-trip scenario decomposes (documentation of the model, not fitted)."""

    total_bps: float
    commission_bps: float
    spread_bps: float
    shortfall_bps: float
    latency_bps: float

    @staticmethod
    def taker_taker(total_bps: float) -> CostBreakdown:
        comm = 2 * TAKER_FEE_BPS  # 11 bps round trip
        rest = max(0.0, total_bps - comm)
        # the remainder is split spread 40 % / shortfall 40 % / latency 20 % (documentation only)
        return CostBreakdown(total_bps, comm, 0.4 * rest, 0.4 * rest, 0.2 * rest)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def gross_bps(side: int, entry: float, exit_px: float) -> float:
    return side * (exit_px / entry - 1.0) * 1e4


def funding_bps(
    side: int, entry_ms: int, exit_ms: int, fund_t: np.ndarray, fund_rate: np.ndarray
) -> float:
    """Funding paid (+) / received (-) while the position spans funding timestamps (proxy rates).
    A LONG pays positive funding; sign-correct for the side. 0.0 when no timestamp is spanned."""
    if len(fund_t) == 0:
        return math.nan  # unknown, never zero
    lo = int(np.searchsorted(fund_t, entry_ms, side="right"))
    hi = int(np.searchsorted(fund_t, exit_ms, side="right"))
    if hi <= lo:
        return 0.0
    return float(side * np.sum(fund_rate[lo:hi]) * 1e4)


def benchmark_net_bps(gross: float, scenario_bps: float, funding: float) -> float:
    """Benchmark ledger: gross - modelled round trip - funding (funding NaN -> treated as 0 but flagged
    upstream; here the caller passes 0.0 when it decided so)."""
    return gross - scenario_bps - (funding if math.isfinite(funding) else 0.0)


def executable_net_bps(
    side: int,
    fill_in: float,
    fill_out: float,
    funding: float,
    commission_bps: float = 2 * TAKER_FEE_BPS,
) -> float:
    """Executable ledger: fills already contain the spread; subtract commission + funding only."""
    g = gross_bps(side, fill_in, fill_out)
    return g - commission_bps - (funding if math.isfinite(funding) else 0.0)


@dataclass(frozen=True)
class PassiveEntryResult:
    status: str  # FILLED / PARTIAL / UNFILLED / BOUNDED_MODEL_DEPENDENT
    fill_frac: float
    fill_price: float | None
    note: str


def passive_entry_bounded(
    side: int,
    best_quote_at_submit: float | None,
    traded_through_within_5s: bool | None,
    queue_data_available: bool = False,
) -> PassiveEntryResult:
    """Predeclared passive-entry variant. Without queue-position data a quote touch is NOT a fill: the
    only fill the model accepts is the price trading THROUGH the resting level within 5 s (then a full
    fill at the quote is an upper bound, labelled). Everything else is UNFILLED or MODEL_DEPENDENT."""
    if best_quote_at_submit is None or traded_through_within_5s is None:
        return PassiveEntryResult(
            "BOUNDED_MODEL_DEPENDENT", 0.0, None, "no quote / trade-through data"
        )
    if not queue_data_available:
        if traded_through_within_5s:
            return PassiveEntryResult(
                "BOUNDED_MODEL_DEPENDENT",
                1.0,
                best_quote_at_submit,
                "traded through: fill is an UPPER bound",
            )
        return PassiveEntryResult(
            "UNFILLED", 0.0, None, "quote not traded through within 5 s; no chase"
        )
    return PassiveEntryResult(
        "FILLED" if traded_through_within_5s else "UNFILLED",
        1.0 if traded_through_within_5s else 0.0,
        best_quote_at_submit if traded_through_within_5s else None,
        "queue model",
    )


def risk_usdt(net_r: float | None, risk_per_trade_usdt: float = 5.0) -> float | None:
    """Net USDT at the reference sizing (0.10 % of 5000 USDT = 5 USDT planned stop risk, as the Demo runs)."""
    return None if net_r is None else net_r * risk_per_trade_usdt
