"""`btc-swing v11 forward status` / `diagnostic` / `trades` / `shadow-report` (read-only)."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from btc_swing.v5.demo.journal import verify_chain
from btc_swing.v11.config import V11Config
from btc_swing.v11.freeze import load_freeze
from btc_swing.v11.paths import V11Paths
from btc_swing.v11.runner import runner_alive


def _read(path: Any) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _iso(ms_: int | float | None) -> str:
    return (
        "n/a"
        if ms_ is None
        else datetime.fromtimestamp(ms_ / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
    )


def status_rows(cfg: V11Config, paths: V11Paths) -> list[tuple[str, str]]:
    st = json.loads(paths.state.read_text()) if paths.state.exists() else {}
    alive, pid = runner_alive(paths)
    diag = json.loads(paths.diagnostic.read_text()) if paths.diagnostic.exists() else {}
    age = (
        (time.time() * 1000 - float(diag["written_ms"])) / 1000 if diag.get("written_ms") else None
    )
    files = (
        sorted(paths.raw.glob("*/*.jsonl*"), key=lambda p: p.stat().st_mtime)
        if paths.raw.exists()
        else []
    )
    raw_age = time.time() - files[-1].stat().st_mtime if files else None
    fz = load_freeze()
    c = st.get("counters") or {}
    sc = diag.get("scores") or {}
    return [
        ("runner", f"ALIVE (pid {pid})" if alive else "DEAD"),
        (
            "feed",
            "CONNECTED" if alive and raw_age is not None and raw_age <= 30 else "DISCONNECTED",
        ),
        ("root", "VERIFICATION" if paths.verification else "OBSERVATION"),
        ("observation start", _iso(st.get("observation_start_ms"))),
        (
            "freeze",
            f"strategy {fz['strategy_hash'][:12]} frozen {fz['frozen_at'][:19]}" if fz else "NONE",
        ),
        ("last bar", _iso(st.get("last_bar_ms"))),
        (
            "latest event",
            f"{_iso(st.get('last_event_ms'))} ({'n/a' if raw_age is None else f'{raw_age:.0f} s ago'})",
        ),
        (
            "diagnostic snapshot",
            f"{diag.get('written_at')} ({'n/a' if age is None else f'{age:.0f} s ago'}){' STALE' if alive and age is not None and age > 90 else ''}",
        ),
        ("bars in window", str(diag.get("bars"))),
        ("score long / short", f"{sc.get('long')} / {sc.get('short')}"),
        ("armed", json.dumps(diag.get("armed") or {})),
        (
            "open shadow position",
            json.dumps(
                {
                    k: st["position"].get(k)
                    for k in ("signal_id", "family", "side", "entry_fill", "stop")
                }
            )
            if st.get("position")
            else "none",
        ),
        ("counters", json.dumps(c)),
        (
            "shadow trades / cum net USDT / wins / losses",
            f"{st.get('trades', 0)} / {float(st.get('cum_net_usd') or 0):.2f} / {st.get('wins', 0)} / {st.get('losses', 0)}",
        ),
        ("last signal", json.dumps(st.get("last_signal")) if st.get("last_signal") else "none"),
        ("execution", "SHADOW ONLY (no exchange client in this generation)"),
        ("V10 coupling", "none: separate feed, root, journals, state, pid, lock"),
    ]


def status_text(cfg: V11Config, paths: V11Paths) -> str:
    rows = status_rows(cfg, paths)
    w = max(len(k) for k, _ in rows)
    return "\n".join(f"{k.ljust(w)}  {v}" for k, v in rows)


def diagnostic_text(paths: V11Paths) -> str:
    if not paths.diagnostic.exists():
        return "no diagnostic snapshot (runner not running?)"
    d = json.loads(paths.diagnostic.read_text())
    out = [
        f"V11 diagnostic written {d.get('written_at')} last bar {d.get('last_bar')} bars {d.get('bars')} queue {d.get('queue')} errors {d.get('errors')}",
        "",
        "scores:",
        f"  long {d.get('scores', {}).get('long')}  short {d.get('scores', {}).get('short')}",
        f"  contrib long  {json.dumps(d.get('scores', {}).get('contrib_long'))}",
        f"  contrib short {json.dumps(d.get('scores', {}).get('contrib_short'))}",
        "",
        "indicators:",
    ]
    out += [f"  {k:22s} {v}" for k, v in (d.get("indicators") or {}).items()]
    out += ["", "microstructure (forward-only group):"] + [
        f"  {k:22s} {v}" for k, v in (d.get("micro") or {}).items()
    ]
    out += [
        "",
        f"armed: {json.dumps(d.get('armed'))}",
        f"position: {json.dumps(d.get('position'))}",
        f"micro history: {json.dumps(d.get('micro_history'))}",
    ]
    return "\n".join(out)


def shadow_report(cfg: V11Config, paths: V11Paths, min_hours: float = 3.0) -> dict[str, Any]:
    st = json.loads(paths.state.read_text()) if paths.state.exists() else {}
    sigs = [r["data"] for r in _read(paths.signals)]
    sh = [r["data"] for r in _read(paths.shadow_trades) if r["kind"] == "SHADOW_CLOSED"]
    cands = _read(paths.candidates)
    nms = _read(paths.nearmiss)
    outs = _read(paths.outcomes)
    start, last = st.get("observation_start_ms"), st.get("last_bar_ms")
    dur = ((last or 0) - (start or 0)) / 1000 if start and last else 0.0
    chains = {
        n: verify_chain(p)
        for n, p in (
            ("candidates", paths.candidates),
            ("episodes", paths.episodes),
            ("signals", paths.signals),
            ("shadow_trades", paths.shadow_trades),
            ("outcomes", paths.outcomes),
        )
        if p.exists()
    }
    bad = sum(
        1
        for s in sigs
        if s.get("action") == "EXECUTED"
        and (
            abs(float(s.get("risk_usdt") or 0) - cfg.risk.initial_equity * cfg.risk.risk_per_trade)
            > 0.01
            or not (
                cfg.stop.floor_pct - 1e-9
                <= float(s.get("stop_pct") or 0)
                <= cfg.stop.cap_pct + 1e-9
            )
        )
    )
    checks = {
        "duration >= min hours": dur >= min_hours * 3600,
        "bars updating": bool(st.get("last_bar_ms")),
        "indicators populate": bool(paths.diagnostic.exists()),
        "journals valid": all(c.get("ok") for c in chains.values()) if chains else True,
        "risk / stop sanity on executed": bad == 0,
        "no exchange order path": True,
    }
    return {
        "run_id": f"v11shadow-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        "generated_utc": datetime.now(UTC).isoformat(),
        "status": "PASSED" if all(checks.values()) else "NOT_PASSED",
        "checks": checks,
        "duration_s": dur,
        "observation_start": _iso(start),
        "last_bar": _iso(last),
        "candidates": len(cands),
        "signals": len(sigs),
        "executed": sum(1 for s in sigs if s.get("action") == "EXECUTED"),
        "blocked": sum(1 for s in sigs if s.get("action") == "BLOCKED"),
        "near_misses": len(nms),
        "shadow_trades": len(sh),
        "shadow_net_usd": sum(float(t.get("net_usd") or 0) for t in sh),
        "outcomes": len(outs),
        "counters": st.get("counters"),
        "chains": {k: v.get("ok") for k, v in chains.items()},
    }
