"""STRATEGY_DEMO activation: an immutable, host-bound event in the hash-chained strategy journal.

`config/btc_swing_v5_demo.yaml` stays `mode: DISABLED`. STRATEGY_DEMO is effective only while the
LAST activation event in `<forward>/demo/strategy_journal.jsonl` is `STRATEGY_DEMO_ACTIVATED` and it
was recorded by THIS host (after a cold migration the new host must activate again). The event is
appended only by `btc-swing v5 demo activate` after every gate passed (fresh DEMO_EXECUTION_PREFLIGHT
and FORWARD_HOST_PREFLIGHT, PASSED smoke report, freeze/observation start, integrity, no
reconciliation pending, test suite, sizing config). Triggers whose bar closed at or before the
activation timestamp are refused by the executor. `btc-swing v5 demo deactivate` appends
`STRATEGY_DEMO_DEACTIVATED`; journal records are never rewritten.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from btc_swing.v5.demo.config import STRATEGY_TAG
from btc_swing.v5.demo.journal import HashChainJournal
from btc_swing.v5.forward.host import host_identity

ACTIVATED = "STRATEGY_DEMO_ACTIVATED"
DEACTIVATED = "STRATEGY_DEMO_DEACTIVATED"
REQUIRED_REFERENCE_EQUITY = 5000.0  # owner decision 2026-10-08 (demo virtual accounting only)


def strategy_journal_path(forward_root: Path) -> Path:
    return forward_root / "demo" / "strategy_journal.jsonl"


def activation_state(forward_root: Path) -> dict[str, Any]:
    p = strategy_journal_path(forward_root)
    recs = HashChainJournal(p, "strategy_demo").records() if p.exists() else []
    events = [r for r in recs if r["kind"] in (ACTIVATED, DEACTIVATED)]
    last = events[-1] if events else None
    return {
        "status": "NEVER_ACTIVATED" if last is None else last["kind"],
        "events": len(events),
        "last": dict(last["data"]) if last else None,
        "seq": last["seq"] if last else None,
    }


def effective_activation(forward_root: Path) -> dict[str, Any] | None:
    """The active activation record for THIS host, or None (STRATEGY_DEMO not in effect)."""
    st = activation_state(forward_root)
    last = st["last"]
    if st["status"] != ACTIVATED or last is None:
        return None
    if last.get("host_id") != host_identity()["host_id"]:
        return None
    return last


def record_activation(
    forward_root: Path, gates: dict[str, Any], note: str, version: str = "V5"
) -> dict[str, Any]:
    """Append STRATEGY_DEMO_ACTIVATED (exact UTC timestamp). Callers must have verified `gates`."""
    if not gates or not all(g.get("ok") for g in gates.values()):
        raise RuntimeError("refusing to activate: not every gate passed")
    if effective_activation(forward_root) is not None:
        raise RuntimeError("STRATEGY_DEMO is already active on this host")
    now = datetime.now(UTC)
    rec = {
        **host_identity(),
        "version": version,
        "activated_at": now.isoformat(),
        "activated_at_ms": int(now.timestamp() * 1000),
        "note": note,
        "gates": gates,
        "rule": "only triggers whose bar closes strictly after activated_at may be traded",
    }
    j = HashChainJournal(strategy_journal_path(forward_root), "strategy_demo")
    j.append(ACTIVATED, rec, STRATEGY_TAG)
    return rec


def record_deactivation(forward_root: Path, note: str, position_open: bool) -> dict[str, Any]:
    if effective_activation(forward_root) is None:
        raise RuntimeError("STRATEGY_DEMO is not active on this host")
    if position_open:
        raise RuntimeError(
            "a STRATEGY_DEMO position is open: let the frozen exits close it before deactivating"
        )
    now = datetime.now(UTC)
    rec = {**host_identity(), "deactivated_at": now.isoformat(), "note": note}
    HashChainJournal(strategy_journal_path(forward_root), "strategy_demo").append(
        DEACTIVATED, rec, STRATEGY_TAG
    )
    return rec


def activation_gates(
    *,
    tests: dict[str, Any],
    preflight: dict[str, Any],
    smoke: dict[str, Any] | None,
    v5_hash: str,
    freeze: dict[str, Any] | None,
    start_ms: int,
    coverage: dict[str, Any],
    chains: dict[str, dict[str, Any]],
    demo_state: dict[str, Any],
    reference_equity: float,
    risk_per_trade: float,
    frozen_risk: float,
    config_mode: str,
    expected_v5_hash: str,
    expected_start_ms: int,
) -> dict[str, dict[str, Any]]:
    """Every condition that must hold before STRATEGY_DEMO_ACTIVATED may be written."""
    from btc_swing.v5.demo.preflight import DEMO_GATE, HOST_GATE

    g = preflight.get("gates") or {}
    fz = freeze or {}
    dups = coverage.get("duplicates") or {}
    pos = demo_state.get("position")

    def gate(ok: bool, detail: Any) -> dict[str, Any]:
        return {"ok": bool(ok), "detail": detail}

    return {
        "tests": gate(tests.get("ok", False), tests.get("summary")),
        DEMO_GATE: gate(
            (g.get(DEMO_GATE) or {}).get("status") == "PASSED",
            {"status": (g.get(DEMO_GATE) or {}).get("status"), "run_id": preflight.get("run_id")},
        ),
        HOST_GATE: gate(
            (g.get(HOST_GATE) or {}).get("status") == "PASSED",
            {
                "status": (g.get(HOST_GATE) or {}).get("status"),
                "failed": [
                    c["check"] for c in (g.get(HOST_GATE) or {}).get("checks", []) if not c["ok"]
                ],
            },
        ),
        "real DEMO smoke PASSED": gate(
            (smoke or {}).get("status") == "PASSED",
            {"run_id": (smoke or {}).get("run_id"), "status": (smoke or {}).get("status")},
        ),
        "freeze: V5 config hash": gate(
            v5_hash == expected_v5_hash == fz.get("v5_config_hash"), v5_hash[:12]
        ),
        "freeze: observation start": gate(
            start_ms == expected_start_ms == int(fz.get("observation_start_ms", -1)),
            fz.get("observation_start"),
        ),
        "integrity: no duplicate bars/signals/paper trades": gate(
            not any(int(v) for v in dups.values()), dups
        ),
        "integrity: hash-chained journals valid": gate(
            all(c.get("ok") for c in chains.values()),
            {k: {"records": v.get("records"), "ok": v.get("ok")} for k, v in chains.items()},
        ),
        "reconciliation: none pending, no strategy position open": gate(
            not demo_state.get("reconcile_required") and pos is None,
            {
                "reconcile_required": bool(demo_state.get("reconcile_required")),
                "position": None if pos is None else pos.get("status"),
            },
        ),
        "sizing: reference equity 5000 USDT, risk 0.25% = frozen": gate(
            abs(reference_equity - REQUIRED_REFERENCE_EQUITY) < 1e-9
            and abs(risk_per_trade - frozen_risk) < 1e-12
            and abs(risk_per_trade - 0.0025) < 1e-12,
            {"reference_equity": reference_equity, "risk_per_trade": risk_per_trade},
        ),
        "config mode file stays DISABLED (activation event is the only switch)": gate(
            config_mode == "DISABLED", config_mode
        ),
    }
