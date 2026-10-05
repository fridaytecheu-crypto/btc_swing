"""Provider entitlement: who delivers the data, under which plan, with which delay.

`provider_available_time = market/close time + delay` is what a DEPLOYMENT_AS_OF run compares
against the decision time; MARKET_AS_OF uses the bar close itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class ProviderEntitlement:
    provider: str
    plan: str
    delay: timedelta
    history_years: int | None = None

    @property
    def delay_minutes(self) -> int:
        return int(self.delay.total_seconds() // 60)
