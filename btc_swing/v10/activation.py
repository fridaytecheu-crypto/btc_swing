"""V10 STRATEGY_DEMO activation: an immutable host-bound event in forward_v10/demo/strategy_journal.jsonl,
written only by `btc-swing v10 demo activate` after EVERY gate passed, including the V5 / V5.1 conflict
gates (design section 6). V5.1 is never deactivated here."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from btc_swing.v5.demo.activation import activation_state as v5_activation_state
from btc_swing.v5.demo.activation import effective_activation as v5_effective_activation
from btc_swing.v5.demo.journal import HashChainJournal
from btc_swing.v5.forward.host import authority_state, host_identity
from btc_swing.v10.config import V10Config, strategy_hash_of
from btc_swing.v10.paths import V10Paths

ACTIVATED = "STRATEGY_DEMO_V10_ACTIVATED"
DEACTIVATED = "STRATEGY_DEMO_V10_DEACTIVATED"
TAG = "STRATEGY_DEMO_V10"


def activation_state(paths: V10Paths) -> dict[str, Any]:
    p = paths.demo_journal
    recs = HashChainJournal(p, "strategy_demo_v10").records() if p.exists() else []
    events = [r for r in recs if r["kind"] in (ACTIVATED, DEACTIVATED)]
    last = events[-1] if events else None
    return {
        "status": "NEVER_ACTIVATED" if last is None else last["kind"],
        "events": len(events),
        "last": dict(last["data"]) if last else None,
    }


def effective_activation(paths: V10Paths) -> dict[str, Any] | None:
    st = activation_state(paths)
    last = st["last"]
    if st["status"] != ACTIVATED or last is None:
        return None
    if last.get("host_id") != host_identity()["host_id"]:
        return None
    return last


def record_activation(paths: V10Paths, gates: dict[str, Any], note: str) -> dict[str, Any]:
    if not gates or not all(g.get("ok") for g in gates.values()):
        raise RuntimeError("refusing to activate: not every gate passed")
    if effective_activation(paths) is not None:
        raise RuntimeError("STRATEGY_DEMO (V10) is already active on this host")
    now = datetime.now(UTC)
    rec = {
        **host_identity(),
        "version": "V10",
        "activated_at": now.isoformat(),
        "activated_at_ms": int(now.timestamp() * 1000),
        "note": note,
        "gates": gates,
        "rule": "only V10 decisions strictly after activated_at may be traded",
    }
    HashChainJournal(paths.demo_journal, "strategy_demo_v10").append(ACTIVATED, rec, TAG)
    return rec


def record_deactivation(paths: V10Paths, note: str, position_open: bool) -> dict[str, Any]:
    if effective_activation(paths) is None:
        raise RuntimeError("STRATEGY_DEMO (V10) is not active on this host")
    if position_open:
        raise RuntimeError("a V10 Demo position is open: let the frozen exits close it first")
    now = datetime.now(UTC)
    rec = {**host_identity(), "deactivated_at": now.isoformat(), "note": note}
    HashChainJournal(paths.demo_journal, "strategy_demo_v10").append(DEACTIVATED, rec, TAG)
    return rec


def _state_position(root: Path) -> dict[str, Any] | None:
    p = root / "demo" / "strategy_state.json"
    if not p.exists():
        return None
    st = json.loads(p.read_text())
    pos = st.get("position")
    return dict(pos) if pos else None


def conflict_checks(paths: V10Paths) -> dict[str, dict[str, Any]]:
    """V5 / V5.1 STRATEGY_DEMO must be inactive on this host with no strategy position record."""
    out: dict[str, dict[str, Any]] = {}
    for label, root in (("V5", paths.v5_root), ("V5.1", paths.v51_root)):
        act = v5_effective_activation(root) if root.exists() else None
        st = v5_activation_state(root) if root.exists() else {"status": "NO_ROOT", "last": None}
        pos = _state_position(root)
        out[f"{label} STRATEGY_DEMO inactive on this host"] = {
            "ok": act is None,
            "detail": {"journal_status": st["status"], "effective_here": act is not None},
        }
        out[f"{label} no strategy position record"] = {
            "ok": pos is None,
            "detail": None
            if pos is None
            else {"status": pos.get("status"), "signal_id": pos.get("signal_id")},
        }
    return out


def v10_gates(
    *,
    paths: V10Paths,
    cfg: V10Config,
    freeze: dict[str, Any] | None,
    tests: dict[str, Any],
    shadow_report: dict[str, Any] | None,
    exchange: dict[str, Any] | None,
    credentials_present: bool,
    runner_alive: bool,
    feed_age_s: float | None,
    chains: dict[str, dict[str, Any]],
    demo_state: dict[str, Any],
    config_mode: str,
) -> dict[str, dict[str, Any]]:
    def gate(ok: bool, detail: Any) -> dict[str, Any]:
        return {"ok": bool(ok), "detail": detail}

    fz = freeze or {}
    auth = (
        authority_state(paths.v5_root)
        if paths.v5_root.exists()
        else {"status": "NONE", "host_id": None, "hostname": None}
    )
    me = host_identity()
    ex = exchange or {}
    g: dict[str, dict[str, Any]] = {}
    g.update(conflict_checks(paths))
    g["Bybit Demo BTCUSDT position flat"] = gate(
        ex.get("position_size", 1.0) == 0.0, {"size": ex.get("position_size")}
    )
    g["no open BTCUSDT orders"] = gate(
        ex.get("open_orders", 1) == 0, {"open_orders": ex.get("open_orders")}
    )
    g["exchange reachable (demo endpoint)"] = gate(bool(ex.get("reachable")), ex.get("error"))
    g["no V10 reconciliation pending"] = gate(
        not demo_state.get("reconcile_required") and demo_state.get("position") is None,
        {
            "reconcile_required": demo_state.get("reconcile_required"),
            "position": demo_state.get("position") is not None,
        },
    )
    g["Demo credentials present (env)"] = gate(
        credentials_present, "present" if credentials_present else "missing"
    )
    g["authority lease held by this host"] = gate(
        auth["status"] == "CLAIMED" and auth["host_id"] == me["host_id"],
        {"lease": auth["status"], "holder": auth.get("hostname"), "this_host": me["hostname"]},
    )
    g["V10 runner alive and feed fresh"] = gate(
        runner_alive
        and feed_age_s is not None
        and feed_age_s <= cfg.features.trade_feed_max_age_s * 12,
        {"runner_alive": runner_alive, "feed_age_s": feed_age_s},
    )
    frozen_strategy = (
        strategy_hash_of(yaml.safe_load(fz["config_yaml"])) if fz.get("config_yaml") else None
    )
    g["V10 freeze present and STRATEGY sections unchanged"] = gate(
        frozen_strategy == cfg.strategy_hash,
        {
            "freeze": str(frozen_strategy or "missing")[:12],
            "config": cfg.strategy_hash[:12],
            "operational_changed": fz.get("config_hash") != cfg.config_hash,
        },
    )
    g["test suite green"] = gate(tests.get("ok", False), tests.get("summary"))
    g["shadow verification PASSED"] = gate(
        (shadow_report or {}).get("status") == "PASSED",
        {
            "status": (shadow_report or {}).get("status"),
            "run_id": (shadow_report or {}).get("run_id"),
        },
    )
    g["hash-chained journals valid"] = gate(
        all(c.get("ok") for c in chains.values()), {k: v.get("ok") for k, v in chains.items()}
    )
    g["config mode file stays DISABLED"] = gate(config_mode == "DISABLED", config_mode)
    g["risk 0.10% of 5000 USDT reference equity"] = gate(
        abs(cfg.risk.risk_per_trade - 0.001) < 1e-12
        and abs(cfg.risk.initial_equity - 5000.0) < 1e-9,
        {"risk": cfg.risk.risk_per_trade, "equity": cfg.risk.initial_equity},
    )
    return g
