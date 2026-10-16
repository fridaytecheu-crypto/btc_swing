"""`btc-swing v11lw demo ...` — V11-LITE-WARM Bybit DEMO execution (paper money only). Owner commands:
`freeze` (Demo execution manifest), `activate` (every gate must pass; writes the host-bound event; never
automatic), `deactivate`, `reconcile-ack`, `resume` (journaled acknowledgements the executor applies),
`status [--live]` and `report` (read-only). Credentials come from the environment only and are never
printed; the demo host is enforced by the shared client."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import typer

from btc_swing.core.settings import get_settings

demo11lw = typer.Typer(help="Bybit DEMO execution of the frozen V11-LITE shadow decisions (paper)")
CFG = Path("config/btc_swing_v11_lite_warm.yaml")
DEMO_CFG = Path("config/btc_swing_v11_lite_warm_demo.yaml")
REPORT_DIR = Path("reports/forward_v11_lite_warm")


def _ctx(config: Path, demo_config: Path) -> tuple[Any, Any, Any, Any]:
    from btc_swing.v11_lite_warm.config import load_v11lw_config
    from btc_swing.v11_lite_warm.demo.config import load_lite_demo_config
    from btc_swing.v11_lite_warm.paths import V11LiteWarmPaths

    lc, v11 = load_v11lw_config(config)
    dc = load_lite_demo_config(demo_config)
    return lc, v11, dc, V11LiteWarmPaths(get_settings().data_dir, v11.symbol, verification=False)


def _demo_state(lite_root: Path) -> dict[str, Any]:
    p = lite_root / "demo" / "strategy_state.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _raw_age_s(raw: Path) -> float | None:
    files = sorted(raw.glob("*/*.jsonl*"), key=lambda p: p.stat().st_mtime) if raw.exists() else []
    return time.time() - files[-1].stat().st_mtime if files else None


@demo11lw.command("freeze")
def demo_freeze(config: Path = CFG, demo_config: Path = DEMO_CFG) -> None:
    """Write manifests/v11_lite_warm_demo_execution_freeze.json once (execution layer only)."""
    from btc_swing.v11_lite_warm.demo.freeze import DEMO_FREEZE_PATH, write_demo_freeze

    lc, v11, dc, _ = _ctx(config, demo_config)
    rec = write_demo_freeze(lc, v11, dc)
    typer.echo(
        json.dumps(
            {
                "path": str(DEMO_FREEZE_PATH),
                **{
                    k: rec[k]
                    for k in (
                        "demo_config_hash",
                        "lite_strategy_hash",
                        "v11_strategy_hash",
                        "bookkeeping_version",
                        "frozen_at",
                        "code_version",
                    )
                },
            }
        )
    )


@demo11lw.command("status")
def demo_status(
    config: Path = CFG,
    demo_config: Path = DEMO_CFG,
    live: bool = typer.Option(False, help="also read the Demo account(s) (GET only)"),
) -> None:
    from btc_swing.v5.demo.config import ExecutionMode
    from btc_swing.v11_lite_warm.demo.activation import activation_state, effective_activation
    from btc_swing.v11_lite_warm.demo.config import exec_config
    from btc_swing.v11_lite_warm.demo.conflict import v10_local_state
    from btc_swing.v11_lite_warm.demo.gates import account_snapshot, v10_view
    from btc_swing.v11_lite_warm.demo.worker import v10_root_of

    _lc, _v11, dc, paths = _ctx(config, demo_config)
    st = _demo_state(paths.root)
    out: dict[str, Any] = {
        "execution": "BYBIT DEMO ONLY (paper money)",
        "account_mode": dc.account_mode,
        "activation": activation_state(paths.root),
        "effective_here": effective_activation(paths.root) is not None,
        "state": {
            k: st.get(k)
            for k in (
                "reconcile_required",
                "reconcile_reason",
                "halted",
                "halted_reason",
                "closed_trades",
                "wins",
                "losses",
                "cum_net_pnl",
                "day",
                "day_realised_usd",
                "consecutive_full_losses",
            )
        }
        | {
            "executions_rolling_24h": len(
                [t for t in st.get("executed_ms") or [] if time.time() * 1000 - t < 86_400_000]
            ),
            "position": st.get("position"),
        },
        "v10_local": v10_local_state(v10_root_of(paths.root)),
    }
    if live:
        scratch = paths.root / "demo" / "readonly"
        out["lite_account"] = {
            k: v
            for k, v in account_snapshot(
                exec_config(dc, ExecutionMode.DISABLED), scratch, "V11LW_VIEW"
            ).items()
            if k != "user_id"
        }
        v10 = v10_view(dc, v10_root_of(paths.root), scratch)
        out["v10_account"] = {k: v for k, v in v10["account"].items() if k != "user_id"}
    typer.echo(json.dumps(out, indent=1, default=str))


@demo11lw.command("activate")
def demo_activate(
    note: str = typer.Option(..., help="owner's activation note"),
    shared_account: bool = typer.Option(
        False, help="explicitly accept sharing V10's Demo account (account_mode: shared only)"
    ),
    config: Path = CFG,
    demo_config: Path = DEMO_CFG,
) -> None:
    """Owner-only: every gate must pass, then STRATEGY_DEMO_V11LW_ACTIVATED (host-bound). The running
    shadow runner attaches the Demo executor within ~30 s; nothing is sent before activated_at."""
    from btc_swing.v5.demo.config import ExecutionMode, load_demo_config
    from btc_swing.v5.demo.journal import verify_chain
    from btc_swing.v5.demo.preflight import is_cloud_session_container
    from btc_swing.v5.forward.host import authority_state, host_identity
    from btc_swing.v11_lite_warm.demo.activation import demo_dir, record_activation
    from btc_swing.v11_lite_warm.demo.config import exec_config, v10_exec_config
    from btc_swing.v11_lite_warm.demo.freeze import check_demo_freeze
    from btc_swing.v11_lite_warm.demo.gates import account_snapshot, demo_gates, warm_ready
    from btc_swing.v11_lite_warm.demo.worker import v10_root_of
    from btc_swing.v11_lite_warm.freeze import check_freeze
    from btc_swing.v11_lite_warm.runner import runner_alive
    from btc_swing.v11_lite_warm.status import verify_warm

    lc, v11, dc, paths = _ctx(config, demo_config)
    scratch = demo_dir(paths.root) / "readonly"

    def ok_of(fn: Any) -> tuple[bool, str]:
        try:
            fn()
            return True, "ok"
        except Exception as e:
            return False, str(e)[:200]

    v5_root = paths.root.parent / "forward"
    auth = authority_state(v5_root) if v5_root.exists() else {"status": "NONE", "host_id": None}
    me = host_identity()
    lease = (
        auth["status"] == "CLAIMED" and auth["host_id"] == me["host_id"],
        f"lease {auth['status']} holder {auth.get('hostname')} this host {me['hostname']}",
    )
    vw = verify_warm(lc, paths)
    chains = {
        n: verify_chain(getattr(paths, n))
        for n in ("candidates", "episodes", "signals", "shadow_trades", "control_twin")
        if getattr(paths, n).exists()
    }
    dj = demo_dir(paths.root) / "strategy_journal.jsonl"
    if dj.exists():
        chains["demo"] = verify_chain(dj)
    alive, _pid = runner_alive(paths)
    lite_snap = account_snapshot(exec_config(dc, ExecutionMode.DISABLED), scratch, "V11LW_VIEW")
    v10_snap = (
        account_snapshot(v10_exec_config(dc), scratch, "V10_VIEW")
        if dc.account_mode == "isolated"
        else None
    )
    gates = demo_gates(
        dc=dc,
        lite_snapshot=lite_snap,
        v10_snapshot=v10_snap,
        v10_root=v10_root_of(paths.root),
        shared_flag=shared_account,
        runner_alive=alive,
        feed_age_s=_raw_age_s(paths.raw),
        warm=warm_ready(vw),
        in_window=bool(vw.get("in_window")),
        freeze_ok=ok_of(lambda: check_freeze(lc, v11)),
        demo_freeze_ok=ok_of(lambda: check_demo_freeze(lc, v11, dc)),
        chains=chains,
        demo_state=_demo_state(paths.root),
        lease_ok=lease,
        cloud=is_cloud_session_container(),
        base_mode=load_demo_config(dc.demo_exec_config, ExecutionMode.DISABLED).mode.value,
    )
    for k, v in gates.items():
        typer.echo(
            f"   [{'PASS' if v['ok'] else 'FAIL'}] {k}: {json.dumps(v['detail'], default=str)[:200]}"
        )
    failed = [k for k, v in gates.items() if not v["ok"]]
    if failed:
        typer.echo(json.dumps({"status": "NOT ACTIVATED", "failed_gates": failed}))
        raise typer.Exit(code=1)
    rec = record_activation(paths.root, gates, note, dc.account_mode)
    typer.echo(
        json.dumps(
            {
                "status": "STRATEGY_DEMO_V11LW_ACTIVATED",
                "activated_at": rec["activated_at"],
                "host": rec["hostname"],
                "account_mode": dc.account_mode,
                "demo_config_hash": dc.config_hash,
                "rule": rec["rule"],
            }
        )
    )


@demo11lw.command("deactivate")
def demo_deactivate(
    note: str = typer.Option(...), config: Path = CFG, demo_config: Path = DEMO_CFG
) -> None:
    """Stop sending NEW shadow decisions to Demo (refused while a Demo position is open)."""
    from btc_swing.v11_lite_warm.demo.activation import record_deactivation

    _lc, _v11, _dc, paths = _ctx(config, demo_config)
    rec = record_deactivation(paths.root, note, _demo_state(paths.root).get("position") is not None)
    typer.echo(json.dumps({"status": "STRATEGY_DEMO_V11LW_DEACTIVATED", **rec}, default=str))


@demo11lw.command("reconcile-ack")
def demo_reconcile_ack(
    note: str = typer.Option(...), config: Path = CFG, demo_config: Path = DEMO_CFG
) -> None:
    """Acknowledge a V11-LITE Demo reconciliation after a human check (journaled; the executor applies
    it; no exchange call). A mismatch that is still present is detected again."""
    from btc_swing.v11_lite_warm.demo.activation import RECON_ACK, record_owner_event

    _lc, _v11, _dc, paths = _ctx(config, demo_config)
    if not _demo_state(paths.root).get("reconcile_required"):
        typer.echo("no V11-LITE Demo reconciliation pending")
        return
    record_owner_event(paths.root, RECON_ACK, note)
    typer.echo("reconciliation acknowledged (V11-LITE Demo); applied by the executor")


@demo11lw.command("resume")
def demo_resume(
    note: str = typer.Option(...), config: Path = CFG, demo_config: Path = DEMO_CFG
) -> None:
    """Clear a Demo halt (3 consecutive full-risk losses) after review (journaled)."""
    from btc_swing.v11_lite_warm.demo.activation import RESUMED, record_owner_event

    _lc, _v11, _dc, paths = _ctx(config, demo_config)
    if not _demo_state(paths.root).get("halted"):
        typer.echo("V11-LITE Demo is not halted")
        return
    record_owner_event(paths.root, RESUMED, note)
    typer.echo("resume recorded (V11-LITE Demo); applied by the executor")


@demo11lw.command("report")
def demo_report_cmd(
    config: Path = CFG,
    demo_config: Path = DEMO_CFG,
    stdout: bool = typer.Option(False, help="also print the markdown"),
) -> None:
    """READ-ONLY Demo report incl. FULL SHADOW vs EXECUTABLE DEMO SUBSET. Writes only
    reports/forward_v11_lite_warm/demo_report.{json,md}."""
    from btc_swing.v11_lite_warm.demo.report import demo_report, render_demo_report

    _lc, _v11, _dc, paths = _ctx(config, demo_config)
    rep = demo_report(paths.root)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    js, md = REPORT_DIR / "demo_report.json", REPORT_DIR / "demo_report.md"
    js.write_text(json.dumps(rep, indent=1, sort_keys=True, default=str))
    md.write_text(render_demo_report(rep))
    if stdout:
        typer.echo(md.read_text())
    sv = rep["shadow_vs_demo"]
    typer.echo(
        json.dumps(
            {
                "json": str(js),
                "markdown": str(md),
                "closed_demo_trades": rep["performance"].get("closed_trades"),
                "shadow_executed_after_activation": sv["shadow_executed_after_activation"],
                "demo_executed": sv["demo_executed"],
                "not_executed_reasons": sv["not_executed_reasons"],
            },
            default=str,
        )
    )
