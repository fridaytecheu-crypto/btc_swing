"""Fees, slippage and funding for a USDT-margined perpetual.

Fees are charged on traded notional (taker by default; maker optionally for resting targets).
Slippage moves the fill price against the trade. Funding is exchanged at each funding timestamp
while a position is open: long pays `rate * qty * price` when the rate is positive, short
receives it. The engine applies funding events strictly in (previous bar close, this bar close].
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

import numpy as np
import polars as pl

from btc_swing.core.config import CostsCfg
from btc_swing.core.enums import Side


@dataclass(frozen=True)
class CostModel:
    cfg: CostsCfg

    def fee(self, notional: float, maker: bool = False) -> float:
        bps = self.cfg.maker_fee_bps if maker else self.cfg.taker_fee_bps
        return abs(notional) * bps / 10_000.0

    def entry_fill(self, price: float, side: Side) -> float:
        return price * (1.0 + side.sign * self.cfg.entry_slippage_bps / 10_000.0)

    def stop_fill(self, price: float, side: Side) -> float:
        """Exit against the position: a long stop fills lower, a short stop fills higher."""
        return price * (1.0 - side.sign * self.cfg.stop_slippage_bps / 10_000.0)

    def target_fill(self, price: float) -> float:
        return price  # resting limit assumed to fill at its price (maker or taker fee applies)

    def target_is_maker(self) -> bool:
        return self.cfg.use_maker_for_targets


class FundingSchedule:
    def __init__(self, funding: pl.DataFrame | None) -> None:
        if funding is None or funding.is_empty():
            self.times: list[int] = []
            self.rates: np.ndarray = np.zeros(0)
        else:
            f = funding.sort("time_ms")
            self.times = [int(x) for x in f["time_ms"].to_list()]
            self.rates = f["funding_rate"].to_numpy().astype(np.float64)

    def events_between(self, after_ms: int, through_ms: int) -> list[tuple[int, float]]:
        """Funding events with after_ms < time <= through_ms."""
        lo = bisect.bisect_right(self.times, after_ms)
        hi = bisect.bisect_right(self.times, through_ms)
        return [(self.times[i], float(self.rates[i])) for i in range(lo, hi)]

    @staticmethod
    def payment(rate: float, qty: float, price: float, side: Side) -> float:
        """Signed cash flow to the account (negative = paid)."""
        return -side.sign * rate * qty * price
