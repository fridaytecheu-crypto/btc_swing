"""READ-ONLY V11-LITE Demo report: execution event counts, Demo performance, the current position, and
FULL SHADOW vs EXECUTABLE DEMO SUBSET (every shadow EXECUTED decision after activation with its Demo
outcome and, if not executed, the exact blocker). Reads journals only; writes reports only."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from btc_swing.forward_report.spec import read_chain, read_json, read_jsonl

NA = "N/A"
EVENT_KINDS = (
    "ENTRY_SUBMIT", "ENTRY_ACKED", "EXECUTION_CONFIRMED", "ENTRY_FILLED", "ENTRY_PARTIAL_FILL",
    "ENTRY_NOT_FILLED", "ENTRY_REJECTED", "STOP_SET", "STOP_MOVED", "STOP_MOVE_REJECTED", "TP_PLACED",
    "TP_LEG_NOT_PLACED", "TP_FILLED", "TP_PARTIAL_FILL", "TIME_CLOSE", "TRADE_CLOSED", "DEMO_ENTRY_BLOCKED",
    "PRE_ACTIVATION_SIGNAL_REFUSED", "MANAGE_ERROR", "api_error", "RECONCILIATION_ACKNOWLEDGED",
    "DEMO_HALTED", "DEMO_RESUMED", "DEMO_EXECUTOR_ATTACHED", "DEMO_EXECUTOR_DETACHED", "DEMO_ATTACH_FAILED",
)  # fmt: skip


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def perf(trades: list[dict[str, Any]]) -> dict[str, Any]:
    net = [v for v in (_f(t.get("net_pnl")) for t in trades) if v is not None]
    if not net:
        return {"closed_trades": 0}
    wins, losses = [x for x in net if x > 0], [x for x in net if x <= 0]
    curve = peak = dd = 0.0
    for x in net:
        curve += x
        peak = max(peak, curve)
        dd = max(dd, peak - curve)
    slip = [v for v in (_f(t.get("entry_slippage_bps")) for t in trades) if v is not None]
    sslip = [v for v in (_f(t.get("stop_exit_slippage_bps")) for t in trades) if v is not None]

    def grp(key: str) -> dict[str, Any]:
        g: dict[str, list[float]] = defaultdict(list)
        for t in trades:
            if _f(t.get("net_pnl")) is not None:
                g[str(t.get(key))].append(float(t["net_pnl"]))
        return {
            k: {"n": len(v), "net_usd": sum(v), "win_rate": sum(1 for x in v if x > 0) / len(v)}
            for k, v in sorted(g.items())
        }

    return {
        "closed_trades": len(net), "wins": len(wins), "losses": len(losses), "win_rate": len(wins) / len(net),
        "gross_pnl": sum(_f(t.get("gross_pnl")) or 0.0 for t in trades),
        "fees": sum(_f(t.get("fees")) or 0.0 for t in trades),
        "funding": sum(_f(t.get("funding")) or 0.0 for t in trades),
        "net_pnl": sum(net), "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else (math.inf if wins else NA),
        "max_dd_usd": dd, "net_r_mean_vs_planned": statistics.fmean(_f(t.get("R_net_vs_planned")) or 0.0 for t in trades),
        "entry_slippage_bps_mean": statistics.fmean(slip) if slip else NA,
        "stop_exit_slippage_bps_mean": statistics.fmean(sslip) if sslip else NA,
        "by_side": grp("side"), "by_selected_template": grp("selected_template"), "by_exit_reason": grp("exit_reason"),
    }  # fmt: skip


def demo_report(lite_root: Path) -> dict[str, Any]:
    ddir = lite_root / "demo"
    dj = read_chain(ddir / "strategy_journal.jsonl")
    trades = [t for t in read_jsonl(ddir / "demo_trades.jsonl") if not t.get("__malformed__")]
    st = read_json(ddir / "strategy_state.json")
    kinds = Counter(str(r.get("kind")) for r in dj)
    act = [
        r
        for r in dj
        if r.get("kind") in ("STRATEGY_DEMO_V11LW_ACTIVATED", "STRATEGY_DEMO_V11LW_DEACTIVATED")
    ]
    windows: list[list[int]] = []  # [activated_at_ms, deactivated_ms) per activation period
    for r in act:
        d = r.get("data") or {}
        if r["kind"] == "STRATEGY_DEMO_V11LW_ACTIVATED":
            windows.append([int(d.get("activated_at_ms") or 0), 2**62])
        elif windows and windows[-1][1] == 2**62:
            t = datetime.fromisoformat(str(d.get("deactivated_at") or r.get("ts")))
            windows[-1][1] = int(t.timestamp() * 1000)
    # shadow vs demo
    shadow = [
        r["data"] for r in read_chain(lite_root / "journals" / "signals.jsonl")
        if r.get("kind") == "SIGNAL_EXECUTED" and isinstance(r.get("data"), dict)
    ]  # fmt: skip
    sh_closed = {
        str(r["data"].get("signal_id")): r["data"] for r in read_chain(lite_root / "journals" / "shadow_trades.jsonl")
        if r.get("kind") == "SHADOW_CLOSED" and isinstance(r.get("data"), dict)
    }  # fmt: skip
    by_sig: dict[str, dict[str, Any]] = {}
    for r in dj:
        d = r.get("data") or {}
        sid = d.get("signal_id")
        if not sid:
            continue
        k = r.get("kind")
        if k == "DEMO_ENTRY_BLOCKED":
            by_sig[sid] = {"demo": "BLOCKED", "blockers": d.get("blockers")}
        elif k == "PRE_ACTIVATION_SIGNAL_REFUSED":
            by_sig[sid] = {"demo": "REFUSED_BEFORE_ACTIVATION", "blockers": []}
        elif k == "ENTRY_SUBMIT":
            by_sig[sid] = {"demo": "SUBMITTED", "blockers": []}
        elif k in ("ENTRY_REJECTED", "ENTRY_NOT_FILLED"):
            by_sig[sid] = {"demo": k, "blockers": []}
    demo_closed = {str(t.get("signal_id")): t for t in trades}
    rows = []
    for s in shadow:
        dms = int(s.get("decision_ms") or 0)
        if not any(lo < dms < hi for lo, hi in windows):
            continue
        sid = str(s.get("signal_id"))
        o = by_sig.get(sid, {"demo": "NOT_SEEN_BY_DEMO (executor not attached)", "blockers": []})
        rows.append({
            "signal_id": sid, "decision": s.get("ts"), "side": s.get("side"), "selected_template": s.get("selected_template"),
            "score": s.get("score"), "shadow_net_r": (sh_closed.get(sid) or {}).get("net_r", NA),
            "demo": o["demo"] if sid not in demo_closed else "EXECUTED", "demo_blockers": o["blockers"],
            "demo_net_usd": (demo_closed.get(sid) or {}).get("net_pnl", NA),
            "demo_R_vs_planned": (demo_closed.get(sid) or {}).get("R_net_vs_planned", NA),
            "demo_exit_reason": (demo_closed.get(sid) or {}).get("exit_reason", NA),
        })  # fmt: skip
    not_exec = [r for r in rows if r["demo"] != "EXECUTED"]
    return {
        "generated_utc": datetime.now(UTC).isoformat(),
        "read_only": True,
        "activation_events": [{"kind": r["kind"], "ts": r.get("ts")} for r in act],
        "activation_windows_ms": windows,
        "account_mode": st.get("account_mode", NA),
        "events": {k: kinds.get(k, 0) for k in EVENT_KINDS},
        "alerts": {k: v for k, v in kinds.items() if str(k).startswith("ALERT_")},
        "reconcile_required": st.get("reconcile_required", NA),
        "halted": st.get("halted", NA),
        "current_position": st.get("position"),
        "performance": perf(trades),
        "shadow_vs_demo": {
            "shadow_executed_after_activation": len(rows),
            "demo_executed": len(rows) - len(not_exec),
            "not_executed_reasons": dict(
                Counter(";".join(r["demo_blockers"]) or r["demo"] for r in not_exec)
            ),
            "rows": rows,
        },
    }


def render_demo_report(r: dict[str, Any]) -> str:
    def v(x: Any, nd: int = 2) -> str:
        return f"{x:.{nd}f}" if isinstance(x, float) and math.isfinite(x) else str(x)

    out = ["# V11-LITE-WARM Bybit DEMO report (paper money; read-only)\n",
           f"Generated {r['generated_utc']} · account mode {r['account_mode']} · reconcile {r['reconcile_required']} · halted {r['halted']}\n",
           "## Execution events\n", "| event | count |", "|---|---|"]  # fmt: skip
    out += [f"| {k} | {n} |" for k, n in r["events"].items()]
    out += [
        "",
        f"alerts: {r['alerts'] or 'none'}",
        "",
        "## Demo performance\n",
        "| metric | value |",
        "|---|---|",
    ]
    p = r["performance"]
    out += [f"| {k} | {v(x)} |" for k, x in p.items() if not isinstance(x, dict)]
    for k in ("by_side", "by_selected_template", "by_exit_reason"):
        if isinstance(p.get(k), dict):
            out.append(f"| {k} | {p[k]} |")
    out += ["", f"current open position: {r['current_position'] or 'none'}", "",
            "## Shadow vs Demo (every shadow EXECUTED decision after activation)\n"]  # fmt: skip
    sv = r["shadow_vs_demo"]
    out.append(
        f"shadow {sv['shadow_executed_after_activation']} · demo executed {sv['demo_executed']} · not executed by reason {sv['not_executed_reasons']}\n"
    )
    out += [
        "| decision | side | template | score | shadow net R | demo | blockers | demo net USDT | demo R | demo exit |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for x in sv["rows"][-300:]:
        out.append(
            f"| {str(x['decision'])[:19]} | {x['side']} | {x['selected_template']} | {v(_f(x['score']) or 0.0, 1)} | {v(x['shadow_net_r'])} | {x['demo']} | {','.join(x['demo_blockers'] or [])} | {v(x['demo_net_usd'])} | {v(x['demo_R_vs_planned'])} | {x['demo_exit_reason']} |"
        )
    return "\n".join(out) + "\n"
