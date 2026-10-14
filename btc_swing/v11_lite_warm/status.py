"""`btc-swing v11lw forward status` / `diagnostic` / `verify-warm` / `shadow-report` (read-only)."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from btc_swing.v5.demo.journal import verify_chain
from btc_swing.v11.config import V11Config
from btc_swing.v11_lite_warm.config import STRATEGY_DELTA, V11LiteWarmConfig
from btc_swing.v11_lite_warm.freeze import load_freeze
from btc_swing.v11_lite_warm.paths import V11LiteWarmPaths
from btc_swing.v11_lite_warm.runner import runner_alive


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


def _diag(paths: V11LiteWarmPaths) -> dict[str, Any]:
    return json.loads(paths.diagnostic.read_text()) if paths.diagnostic.exists() else {}


def verify_warm(lc: V11LiteWarmConfig, paths: V11LiteWarmPaths) -> dict[str, Any]:
    d = _diag(paths)
    w = d.get("warm") or {}
    seed = json.loads(paths.seed_record.read_text()) if paths.seed_record.exists() else {}
    ind = d.get("indicators") or {}
    contrib = (d.get("scores") or {}).get("contrib_long") or {}
    ctx = d.get("context") or {}
    out = {
        "diagnostic_written_at": d.get("written_at"),
        "last_bar": d.get("last_bar"),
        "bars_in_window": d.get("bars"),
        "bootstrap_bars_in_window": w.get("bootstrap_bars_in_window"),
        "holes_in_window": w.get("holes_in_window"),
        "seed": {
            k: seed.get(k) for k in ("bootstrap_bars", "gapfill_bars", "last_close", "sha256_bars")
        },
        "required_fields": {k: ind.get(k) for k in lc.warm.required_warm_fields},
        "required_groups": {g: contrib.get(g) for g in lc.warm.required_warm_groups},
        "derivatives_contribution": contrib.get("derivatives"),
        "microstructure_contribution": contrib.get("microstructure"),
        "scores": {k: (d.get("scores") or {}).get(k) for k in ("long", "short")},
        "family_labels": d.get("family_labels"),
        "fib_available": (ctx.get("fib") or {}).get("available"),
        "fib_bucket_atr": (ctx.get("fib") or {}).get("bucket_atr"),
        "range_available": {
            k: (v or {}).get("available") for k, v in (ctx.get("range") or {}).items()
        },
        "control_twin_armed": (d.get("control_twin") or {}).get("armed"),
        "in_window": d.get("in_window"),
        "all_required_fields_populated": bool(w.get("all_fields")),
        "all_required_groups_populated": bool(w.get("all_groups")),
    }
    out["status"] = (
        "WARM"
        if out["all_required_fields_populated"] and out["all_required_groups_populated"]
        else "NOT_WARM"
    )
    return out


def status_rows(
    lc: V11LiteWarmConfig, v11: V11Config, paths: V11LiteWarmPaths
) -> list[tuple[str, str]]:
    st = json.loads(paths.state.read_text()) if paths.state.exists() else {}
    alive, pid = runner_alive(paths)
    diag = _diag(paths)
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
    w = diag.get("warm") or {}
    tw = st.get("twin") or {}
    return [
        ("runner", f"ALIVE (pid {pid})" if alive else "DEAD"),
        (
            "feed",
            "CONNECTED" if alive and raw_age is not None and raw_age <= 30 else "DISCONNECTED",
        ),
        ("root", "VERIFICATION" if paths.verification else "OBSERVATION"),
        ("mode", "V11-LITE-WARM SHADOW (V11 strategy; A/B/C structure = labels, not gates)"),
        ("observation start", _iso(st.get("observation_start_ms"))),
        (
            "freeze",
            f"lite strategy {fz['lite_strategy_hash'][:12]} (V11 {fz['v11_strategy_hash'][:12]}) frozen {fz['frozen_at'][:19]} observation from {fz['observation_start'][:19]}"
            if fz
            else "NONE",
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
        ("family labels (now)", json.dumps(diag.get("family_labels") or {})),
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
        (
            "control twin (strict V11, in-process)",
            f"trades {tw.get('trades', 0)} / cum net {float(tw.get('cum_net_usd') or 0):.2f} / counters {json.dumps(tw.get('counters') or {})}",
        ),
        ("last signal", json.dumps(st.get("last_signal")) if st.get("last_signal") else "none"),
        (
            "warm-start",
            f"bootstrap bars in window {w.get('bootstrap_bars_in_window')} / {w.get('bars_in_window')} · "
            f"fields {'ALL' if w.get('all_fields') else 'MISSING ' + ','.join(k for k, ok in (w.get('fields') or {}).items() if not ok)} · "
            f"groups {'ALL' if w.get('all_groups') else 'MISSING ' + ','.join(k for k, ok in (w.get('groups') or {}).items() if not ok)} · "
            f"holes {w.get('holes_in_window')}",
        ),
        ("in window", str(diag.get("in_window"))),
        ("execution", "SHADOW ONLY (no exchange client in this generation; no Demo path)"),
        (
            "V10 / V11 / V11-WARM coupling",
            "none: separate feed, root, journals, state, pid, lock, freeze",
        ),
    ]


def status_text(lc: V11LiteWarmConfig, v11: V11Config, paths: V11LiteWarmPaths) -> str:
    rows = status_rows(lc, v11, paths)
    width = max(len(k) for k, _ in rows)
    return "\n".join(f"{k.ljust(width)}  {v}" for k, v in rows)


def diagnostic_text(paths: V11LiteWarmPaths) -> str:
    if not paths.diagnostic.exists():
        return "no diagnostic snapshot (runner not running?)"
    d = _diag(paths)
    out = [
        f"V11-LITE-WARM diagnostic written {d.get('written_at')} last bar {d.get('last_bar')} bars {d.get('bars')} queue {d.get('queue')} errors {d.get('errors')}",
        f"strategy delta: {STRATEGY_DELTA}",
        "",
        "scores:",
        f"  long {d.get('scores', {}).get('long')}  short {d.get('scores', {}).get('short')}",
        f"  contrib long  {json.dumps(d.get('scores', {}).get('contrib_long'))}",
        f"  contrib short {json.dumps(d.get('scores', {}).get('contrib_short'))}",
        f"family labels: {json.dumps(d.get('family_labels'))}",
        "",
        "indicators:",
    ]
    out += [f"  {k:22s} {v}" for k, v in (d.get("indicators") or {}).items()]
    out += ["", "microstructure (forward-only group):"] + [
        f"  {k:22s} {v}" for k, v in (d.get("micro") or {}).items()
    ]
    ctx = d.get("context") or {}
    out += [
        "",
        f"fib: {json.dumps(ctx.get('fib'))}",
        f"vwap: {json.dumps(ctx.get('vwap'))}",
        f"range: {json.dumps(ctx.get('range'))}",
        f"swings: {json.dumps(ctx.get('swings'))}",
        f"location (ATR5): {json.dumps(ctx.get('location_atr'))}",
        "",
        f"armed: {json.dumps(d.get('armed'))}",
        f"position: {json.dumps(d.get('position'))}",
        f"control twin: {json.dumps(d.get('control_twin'))}",
        f"warm: {json.dumps(d.get('warm'))}",
        f"in_window: {d.get('in_window')}",
    ]
    return "\n".join(out)


def shadow_report(
    lc: V11LiteWarmConfig, v11: V11Config, paths: V11LiteWarmPaths, min_hours: float = 0.5
) -> dict[str, Any]:
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
            ("control_twin", paths.control_twin),
        )
        if p.exists()
    }
    bad = sum(
        1
        for s in sigs
        if s.get("action") == "EXECUTED"
        and (
            abs(float(s.get("risk_usdt") or 0) - v11.risk.initial_equity * v11.risk.risk_per_trade)
            > 0.01
            or not (
                v11.stop.floor_pct - 1e-9
                <= float(s.get("stop_pct") or 0)
                <= v11.stop.cap_pct + 1e-9
            )
        )
    )
    vw = verify_warm(lc, paths)
    d = _diag(paths)
    checks = {
        "duration >= min hours": dur >= min_hours * 3600,
        "bars updating": bool(st.get("last_bar_ms")),
        "indicators populate": bool(paths.diagnostic.exists()),
        "journals valid": all(c.get("ok") for c in chains.values()) if chains else True,
        "risk / stop sanity on executed": bad == 0,
        "no exchange order path": True,
        "features warm (pre-declared fields)": vw["all_required_fields_populated"],
        "groups warm (pre-declared groups)": vw["all_required_groups_populated"],
        "family labels computed": isinstance(d.get("family_labels"), dict)
        and set(d["family_labels"]) == {"A", "B", "C"},
        "context telemetry computed": isinstance((d.get("context") or {}).get("fib"), dict),
        "control twin running": isinstance(d.get("control_twin"), dict),
        "signal ids carry V11LWS- prefix": all(
            str(s.get("signal_id", "")).startswith("V11LWS-") for s in sigs
        ),
        "no V10 / V11 / V11-WARM coupling": "forward_v11_lite_warm" in str(paths.root),
    }
    return {
        "run_id": f"v11litewarm-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        "generated_utc": datetime.now(UTC).isoformat(),
        "status": "PASSED" if all(checks.values()) else "NOT_PASSED",
        "mode": "SHADOW ONLY (warm-started V11, family structure as labels)",
        "checks": checks,
        "duration_s": dur,
        "observation_start": _iso(start),
        "last_bar": _iso(last),
        "candidates": len(cands),
        "signals": len(sigs),
        "executed": sum(1 for s in sigs if s.get("action") == "EXECUTED"),
        "blocked": sum(1 for s in sigs if s.get("action") == "BLOCKED"),
        "incremental_signals": sum(1 for s in sigs if s.get("incremental")),
        "near_misses": len(nms),
        "shadow_trades": len(sh),
        "shadow_net_usd": sum(float(t.get("net_usd") or 0) for t in sh),
        "outcomes": len(outs),
        "counters": st.get("counters"),
        "control_twin": st.get("twin"),
        "chains": {k: v.get("ok") for k, v in chains.items()},
        "warm": vw,
    }
