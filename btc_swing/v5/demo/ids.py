"""Deterministic Bybit `orderLinkId`s (<= 36 chars, [A-Za-z0-9_-]).

Strategy orders: `V5D-<sha256(signal_id)[:20]>-<role>`; the same frozen signal always maps to the
same ids, so a restarted process can find, but never re-create, an order it already sent (Bybit
rejects a duplicate orderLinkId). Smoke orders: `SMOKE-<run_id>-<step>`; never strategy orders."""

from __future__ import annotations

import hashlib

STRATEGY_PREFIX = "V5D-"
SMOKE_PREFIX = "SMOKE-"
ROLES = ("EN", "T1", "T2", "TC", "XC")  # entry, TP1, TP2, time-cap close, manual/failsafe close


def strategy_link_id(signal_id: str, role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"unknown role {role}")
    return f"{STRATEGY_PREFIX}{hashlib.sha256(signal_id.encode()).hexdigest()[:20]}-{role}"


def smoke_link_id(run_id: str, step: str) -> str:
    lid = f"{SMOKE_PREFIX}{run_id}-{step}"
    if len(lid) > 36:
        raise ValueError("orderLinkId longer than 36 characters")
    return lid


def is_strategy_link_id(s: str | None) -> bool:
    return bool(s) and str(s).startswith(STRATEGY_PREFIX)


def is_smoke_link_id(s: str | None) -> bool:
    return bool(s) and str(s).startswith(SMOKE_PREFIX)
