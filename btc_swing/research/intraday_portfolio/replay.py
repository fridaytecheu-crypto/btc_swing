"""Shared bar-replay helpers for E1 / E2 on archive bars (benchmark ledger).

Benchmark entry = the OPEN of the first completed 1m bar that starts at the signal bar's close (the first
trade print after the decision). If that minute has no trades the entry is UNAVAILABLE (never a mid, never
the previous close). Exits are evaluated on the 1m path after the entry: stop first (conservative), then
target, then a completed-5m-close invalidation at 5m boundaries, then the time limit. MFE / MAE from the
entry over the held 1m path. Executable replay (bid / ask) is UNAVAILABLE for the archive and is labelled so."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from btc_swing.research.intraday_portfolio.contract import MS_1M, MS_5M
from btc_swing.research.intraday_portfolio.episodes import Exit
from btc_swing.research.intraday_portfolio.primitives import Bars

STOP_MIN_BPS = 20.0
STOP_MAX_BPS = 150.0
STOP_ATR_BUFFER = 0.25


@dataclass(frozen=True)
class Entry:
    time_ms: int  # open of the first 1m bar after the decision (benchmark entry time)
    price: float  # its open (first print)
    idx1: int  # index into the 1m bars


def benchmark_entry(b1: Bars, decision_close_ms: int) -> Entry | None:
    """First 1m bar opening exactly at the decision close; None when that minute is absent."""
    j = int(np.searchsorted(b1.open_ms, decision_close_ms))
    if j >= len(b1) or int(b1.open_ms[j]) != decision_close_ms:
        return None
    return Entry(int(b1.open_ms[j]), float(b1.open[j]), j)


def stop_distance_bps(side: int, entry: float, stop: float) -> float:
    d = side * (entry - stop)
    return d / entry * 1e4 if entry > 0 else math.nan


def stop_reject(d_bps: float) -> str | None:
    if not math.isfinite(d_bps) or d_bps <= 0:
        return "STOP_INVALID"
    if d_bps < STOP_MIN_BPS:
        return "STOP_TOO_TIGHT_LT_20BPS"
    if d_bps > STOP_MAX_BPS:
        return "STOP_TOO_WIDE_GT_150BPS"
    return None


@dataclass(frozen=True)
class PathResult:
    exit: Exit
    mfe_bps: float
    mae_bps: float
    hold_min: float


def replay_exit(
    b1: Bars,
    b5_close_by_close_ms: dict[int, float],
    side: int,
    entry: Entry,
    stop: float,
    target: float | None,
    invalidation_level: float | None,
    invalidation_inside: str,
    max_hold_min: int,
) -> PathResult:
    """Walk the 1m bars from the entry bar. `invalidation_inside` = "below" means a completed 5m close
    <= level invalidates (LONG box high), "above" means >= level (SHORT box low). Target None = no target
    (E2 uses the VWAP as its target, passed as `target`)."""
    s = side
    e = entry.price
    mfe = mae = 0.0
    deadline = entry.time_ms + max_hold_min * MS_1M
    j = entry.idx1
    n = len(b1)
    last_close = e
    last_close_ms = entry.time_ms + MS_1M
    while j < n:
        h, lo, c, cm = float(b1.high[j]), float(b1.low[j]), float(b1.close[j]), int(b1.close_ms[j])
        if cm > deadline:
            break
        fav = (h - e) if s > 0 else (e - lo)
        adv = (lo - e) if s > 0 else (e - h)
        # stop first (conservative), then target, then the 5m-close invalidation, then time
        if (lo <= stop) if s > 0 else (h >= stop):
            mae = min(mae, (stop - e) * s / e * 1e4)
            return PathResult(
                Exit("STOP", cm, stop), mfe / e * 1e4, mae, (cm - entry.time_ms) / MS_1M
            )
        mae = min(mae, adv / e * 1e4)
        if target is not None and ((s > 0 and h >= target) or (s < 0 and lo <= target)):
            mfe = max(mfe, (target - e) * s)
            return PathResult(
                Exit("TARGET", cm, target), mfe / e * 1e4, mae, (cm - entry.time_ms) / MS_1M
            )
        mfe = max(mfe, fav)
        last_close, last_close_ms = c, cm
        if invalidation_level is not None and cm % MS_5M == 0:
            c5 = b5_close_by_close_ms.get(cm)
            if c5 is not None and (
                (invalidation_inside == "below" and c5 <= invalidation_level)
                or (invalidation_inside == "above" and c5 >= invalidation_level)
            ):
                return PathResult(
                    Exit("INVALIDATION", cm, c5), mfe / e * 1e4, mae, (cm - entry.time_ms) / MS_1M
                )
        if cm >= deadline:
            return PathResult(Exit("TIME", cm, c), mfe / e * 1e4, mae, (cm - entry.time_ms) / MS_1M)
        j += 1
    # the 1m path ended (data gap or end of data): close at the last known print, labelled
    return PathResult(
        Exit("DATA_END", last_close_ms, last_close),
        mfe / e * 1e4,
        mae,
        (last_close_ms - entry.time_ms) / MS_1M,
    )
