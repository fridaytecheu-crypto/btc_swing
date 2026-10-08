"""Glue between the forward observation cycle and the demo executor, the smoke gate, and the
demo status. In mode DISABLED nothing here creates a client or touches credentials."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from btc_swing.v5.demo.client import BybitDemoClient
from btc_swing.v5.demo.config import SMOKE_TAG, STRATEGY_TAG, DemoExecConfig, ExecutionMode
from btc_swing.v5.demo.credentials import credentials_present
from btc_swing.v5.demo.journal import HashChainJournal, verify_chain
from btc_swing.v5.demo.preflight import require_forward_host
from btc_swing.v5.demo.strategy import (
    CapturingEngine,
    StrategyDemoExecutor,
    Trigger,
    trail_candidate,
)
from btc_swing.v5.forward.config import ForwardContextLike
from btc_swing.v5.forward.freeze import check_freeze, load_freeze
from btc_swing.v5.forward.pipeline import _read_jsonl

SMOKE_REPORTS = Path("reports/forward/demo_smoke")
SMOKE_SUMMARY = Path("reports/forward/BYBIT_DEMO_EXECUTION_SMOKE.md")


class DemoPaths:
    def __init__(self, forward_root: Path) -> None:
        self.root = forward_root / "demo"
        self.root.mkdir(parents=True, exist_ok=True)
        self.smoke_journal = self.root / "smoke_journal.jsonl"
        self.strategy_journal = self.root / "strategy_journal.jsonl"
        self.state = self.root / "strategy_state.json"
        self.trades = self.root / "demo_trades.jsonl"


def latest_smoke_report(reports_dir: Path = SMOKE_REPORTS) -> dict[str, Any] | None:
    files = sorted(reports_dir.glob("*.json")) if reports_dir.exists() else []
    return json.loads(files[-1].read_text()) if files else None


def latest_passed_smoke(reports_dir: Path = SMOKE_REPORTS) -> dict[str, Any] | None:
    for f in sorted(reports_dir.glob("*.json"), reverse=True) if reports_dir.exists() else []:
        d = json.loads(f.read_text())
        if d.get("status") == "PASSED":
            return dict(d)
    return None


def build_executor(
    ctx: ForwardContextLike, dcfg: DemoExecConfig, reports_dir: Path = SMOKE_REPORTS
) -> StrategyDemoExecutor:
    """Only for mode STRATEGY_DEMO, only after a PASSED smoke run; credentials from env (fail closed)."""
    if dcfg.mode is not ExecutionMode.STRATEGY_DEMO:
        raise RuntimeError("build_executor called while mode is not STRATEGY_DEMO")
    # FORWARD_HOST_PREFLIGHT re-evaluated live: STRATEGY_DEMO fails closed off the authoritative host
    require_forward_host(load_freeze(), ctx.cfg.config_hash, ctx.paths.run_pid)
    dp = DemoPaths(ctx.paths.root)
    journal = HashChainJournal(dp.strategy_journal, "strategy_demo", forbid_tags=(SMOKE_TAG,))
    client = BybitDemoClient(dcfg, dcfg.mode, journal, STRATEGY_TAG)
    return StrategyDemoExecutor(
        ctx.cfg,
        dcfg,
        client,
        journal,
        dp.state,
        dp.trades,
        ctx.paths.signals_file,
        ctx.paths.paper_trades,
        latest_passed_smoke(reports_dir),
    )


def _raw_age_s(raw: Path) -> float | None:
    days = sorted(p for p in raw.glob("*") if p.is_dir())
    for d in reversed(days):
        files = sorted(d.glob("*.jsonl*"), key=lambda p: p.stat().st_mtime)
        if files:
            return time.time() - files[-1].stat().st_mtime
    return None


def demo_cycle(
    ex: StrategyDemoExecutor,
    ctx: ForwardContextLike,
    a: Any,
    b: Any,
    res: Any,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    fs = ex.dcfg.failsafe
    blockers: list[str] = []
    try:
        check_freeze(ctx.cfg)
    except RuntimeError:
        blockers.append("FREEZE_CONFIG_MISMATCH")
    eng = CapturingEngine(ctx.cfg, a.bars, a.funding, None, b.aux, b.series, b.ff)
    r = eng.run(ctx.start_ms, b.last_close_ms + 1)
    if r.result_hash != res.result_hash:
        blockers.append("ENGINE_RESULT_MISMATCH")
    age = _raw_age_s(ctx.paths.raw)
    if age is None or age > fs.collector_stale_s:
        blockers.append("COLLECTOR_STALE")
    if now - b.last_close_ms > fs.bar_stale_s * 1000:
        blockers.append("RUNNER_STALE")
    rows = (
        a.rows.sort("open_time_ms").tail(max(1, fs.gap_lookback_bars))
        if fs.gap_lookback_bars
        else None
    )
    if (
        rows is not None
        and "gap_filled" in rows.columns
        and float(rows["gap_filled"].fill_null(0.0).max() or 0.0) > 0
    ):
        blockers.append("DATA_GAP_IN_FEATURE_WINDOW")
    triggers = [Trigger(**c) for c in eng.captured if c["t_ms"] == b.last_close_ms]
    if triggers and now - b.last_close_ms > fs.entry_max_delay_s * 1000:
        blockers.append("ENTRY_TOO_LATE")
    known = {s["signal_id"] for s in _read_jsonl(ctx.paths.signals_file)}
    if any(t.signal_id not in known for t in triggers):
        blockers.append("SIGNAL_NOT_IN_FORWARD_JOURNAL")
    cand = None
    p = ex.state.get("position")
    if p is not None and p.get("status") == "ACTIVE":
        view = b.series.view_at(b.last_close_ms)
        cand = trail_candidate(
            eng, view, float(b.series.base.close[-1]), p["side"], float(p["stop"])
        )
    out = ex.step(triggers, blockers, cand, now)
    out["triggers"] = [t.signal_id for t in triggers]
    return out


def demo_status(ctx: ForwardContextLike, dcfg: DemoExecConfig) -> dict[str, Any]:
    """Read-only status (no API call is made)."""
    dp = DemoPaths(ctx.paths.root)
    st = json.loads(dp.state.read_text()) if dp.state.exists() else {}
    sigs = _read_jsonl(ctx.paths.signals_file)
    paper = json.loads(ctx.paths.paper_state.read_text()) if ctx.paths.paper_state.exists() else {}
    scale = dcfg.reference_equity_usdt / ctx.cfg.risk.initial_equity
    paper_pnl = float(paper.get("cumulative_net_pnl") or 0.0) * scale
    demo_pnl = float(st.get("demo_cum_net_pnl") or 0.0)
    last_ok = st.get("last_api_ok_ms")
    connected = (
        dcfg.mode is not ExecutionMode.DISABLED
        and last_ok is not None
        and time.time() * 1000 - float(last_ok) < 600_000
    )
    p = st.get("position")
    xp = st.get("last_exchange_position") or {}
    smoke = latest_smoke_report()
    return {
        "frozen_v5_config_hash": ctx.cfg.config_hash,
        "execution_mode": dcfg.mode.value,
        "demo_endpoint": dcfg.rest_base,
        "credentials_present": credentials_present(dcfg),
        "demo_api_connected": connected,
        "last_api_ok_ms": last_ok,
        "latest_smoke": {"run_id": smoke.get("run_id"), "status": smoke.get("status")}
        if smoke
        else None,
        "latest_signal": {
            k: sigs[-1].get(k) for k in ("signal_id", "t", "family", "side", "strength")
        }
        if sigs
        else None,
        "open_strategy_position": None
        if p is None
        else {
            "signal_id": p.get("signal_id"),
            "status": p.get("status"),
            "side": p.get("side"),
            "entry": p.get("entry_price"),
            "stop": p.get("stop"),
            "targets": {k: v.get("price") for k, v in (p.get("legs") or {}).items()},
            "leverage": p.get("leverage"),
            "planned_risk_usdt": p.get("planned_risk_usdt"),
            "qty": p.get("filled_qty") or p.get("planned_qty"),
            "unrealised_pnl": xp.get("unrealised_pnl"),
        },
        "paper_pnl_at_reference_equity": paper_pnl,
        "demo_pnl": demo_pnl,
        "paper_vs_demo_diff": demo_pnl - paper_pnl,
        "demo_closed_trades": st.get("demo_closed_trades", 0),
        "last_order": st.get("last_order"),
        "last_fill": st.get("last_fill"),
        "execution_errors": (st.get("errors") or [])[-5:],
        "alerts": (st.get("alerts") or [])[-5:],
        "reconcile_required": st.get("reconcile_required", False),
        "journals": {
            "smoke": verify_chain(dp.smoke_journal),
            "strategy": verify_chain(dp.strategy_journal),
        },
    }


def demo_status_lines(d: dict[str, Any]) -> list[tuple[str, str]]:
    p = d["open_strategy_position"]
    errs = d["execution_errors"]
    return [
        ("execution mode", d["execution_mode"] + f" (endpoint {d['demo_endpoint']})"),
        (
            "Demo API connected",
            "yes"
            if d["demo_api_connected"]
            else (
                "no (mode DISABLED: no authenticated request)"
                if d["execution_mode"] == "DISABLED"
                else "no"
            ),
        ),
        ("demo credentials in environment", "present" if d["credentials_present"] else "missing"),
        (
            "latest smoke run",
            f"{d['latest_smoke']['run_id']} {d['latest_smoke']['status']}"
            if d["latest_smoke"]
            else "none",
        ),
        (
            "latest signal",
            f"{d['latest_signal']['t']} {d['latest_signal']['family']} {d['latest_signal']['side']}"
            if d["latest_signal"]
            else "none",
        ),
        (
            "open strategy position",
            "none"
            if p is None
            else f"{p['side']} {p['qty']} BTC entry {p['entry']} stop {p['stop']} targets {p['targets']} leverage {p['leverage']}x planned risk {p['planned_risk_usdt']} USDT unrealised {p['unrealised_pnl']} ({p['status']})",
        ),
        (
            "paper PnL @ reference equity / Demo PnL / diff",
            f"{d['paper_pnl_at_reference_equity']:.4f} / {d['demo_pnl']:.4f} / {d['paper_vs_demo_diff']:.4f} USDT",
        ),
        ("demo closed trades", str(d["demo_closed_trades"])),
        ("last order / last fill", f"{d['last_order'] or 'none'} / {d['last_fill'] or 'none'}"),
        (
            "execution errors",
            "none"
            if not errs
            else "; ".join(f"{e['at'][:19]} {e['where']}: {e['error'][:80]}" for e in errs),
        ),
        ("reconciliation required", "YES" if d["reconcile_required"] else "no"),
        (
            "demo journals (hash chain)",
            f"smoke {d['journals']['smoke']['records']} ok={d['journals']['smoke']['ok']}, strategy {d['journals']['strategy']['records']} ok={d['journals']['strategy']['ok']}",
        ),
    ]
