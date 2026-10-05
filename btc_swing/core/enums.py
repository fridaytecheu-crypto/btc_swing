"""Enumerations for the BTC swing engine. Stored as text everywhere."""

from __future__ import annotations

from enum import StrEnum


class Timeframe(StrEnum):
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


TF_MINUTES: dict[Timeframe, int] = {
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.H1: 60,
    Timeframe.H4: 240,
    Timeframe.D1: 1440,
}


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def sign(self) -> float:
        return 1.0 if self is Side.LONG else -1.0


class Regime(StrEnum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    BREAKOUT_REGIME = "BREAKOUT_REGIME"
    UNCLEAR = "UNCLEAR"


class SetupFamily(StrEnum):
    TREND_PULLBACK_LONG = "TREND_PULLBACK_LONG"
    BREAKOUT_LONG = "BREAKOUT_LONG"
    SUPPORT_RECLAIM_LONG = "SUPPORT_RECLAIM_LONG"
    MOMENTUM_CONTINUATION_LONG = "MOMENTUM_CONTINUATION_LONG"
    TREND_PULLBACK_SHORT = "TREND_PULLBACK_SHORT"
    BREAKDOWN_SHORT = "BREAKDOWN_SHORT"
    RESISTANCE_REJECTION_SHORT = "RESISTANCE_REJECTION_SHORT"
    MOMENTUM_CONTINUATION_SHORT = "MOMENTUM_CONTINUATION_SHORT"

    @property
    def side(self) -> Side:
        return Side.LONG if self.value.endswith("_LONG") else Side.SHORT


class EpisodeState(StrEnum):
    NO_SETUP = "NO_SETUP"
    WATCH = "WATCH"
    ENTRY_READY = "ENTRY_READY"
    TRIGGERED = "TRIGGERED"
    ACTIVE = "ACTIVE"
    INVALIDATED = "INVALIDATED"
    CLOSED = "CLOSED"


TERMINAL_STATES = frozenset({EpisodeState.INVALIDATED, EpisodeState.CLOSED})


class ExitReason(StrEnum):
    STOP = "STOP"
    TP1 = "TP1"
    TP2 = "TP2"
    TRAIL = "TRAIL"
    LIQUIDATION = "LIQUIDATION"
    TIME_LIMIT = "TIME_LIMIT"
    REGIME_EXIT = "REGIME_EXIT"
    END_OF_DATA = "END_OF_DATA"
    EARLY_EXIT_NO_CONFIRMATION = "EARLY_EXIT_NO_CONFIRMATION"
    EARLY_EXIT_INVALIDATION = "EARLY_EXIT_INVALIDATION"


class InvalidationReason(StrEnum):
    LEVEL_BREACHED = "LEVEL_BREACHED"
    WATCH_TIMEOUT = "WATCH_TIMEOUT"
    REGIME_INELIGIBLE = "REGIME_INELIGIBLE"
    RAN_WITHOUT_US = "RAN_WITHOUT_US"
    RISK_REJECTED = "RISK_REJECTED"


class RunKind(StrEnum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
