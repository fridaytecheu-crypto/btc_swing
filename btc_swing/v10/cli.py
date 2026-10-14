"""`btc-swing v10 forward ...` and `btc-swing v10 demo ...` (forward-only; Demo only after an owner
activation event; nothing here touches V5 / V5.1 state)."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer

from btc_swing.core.settings import get_settings

v10_app = typer.Typer(help="V10 LIVE MICROSTRUCTURE SNIPER (forward-only; paper / Bybit DEMO)")
forward10 = typer.Typer(help="forward observation")
demo10 = typer.Typer(help="Bybit DEMO execution (owner-activated)")
v10_app.add_typer(forward10, name="forward")
v10_app.add_typer(demo10, name="demo")
CFG = Path("config/btc_swing_v10.yaml")
DEMO_CFG = Path("config/btc_swing_v5_demo.yaml")


def _report_app_v10() -> typer.Typer:
    from btc_swing.forward_report.cli import make_report_app
    from btc_swing.forward_report.generations import spec_v10

    return make_report_app(spec_v10, CFG)


v10_app.add_typer(_report_app_v10(), name="report")


def _ctx(config: Path, verification: bool = False) -> tuple[Any, Any]:
    from btc_swing.v10.config import load_v10_config
    from btc_swing.v10.paths import V10Paths

    cfg = load_v10_config(config)
    return cfg, V10Paths(get_settings().data_dir, cfg.symbol, verification=verification)


@v10_app.command("config")
def v10_config(config: Path = CFG) -> None:
    cfg, _ = _ctx(config)
    typer.echo(json.dumps({"strategy": cfg.strategy_name, "config_hash": cfg.config_hash}))


@forward10.command("run")
def forward_run(
    config: Path = CFG,
    duration: float | None = typer.Option(None, help="seconds; default: until SIGTERM"),
    shadow_verification: bool = typer.Option(
        False, help="pre-freeze verification root; no freeze needed; never Demo"
    ),
) -> None:
    """Run the V10 feed + engine. Observation mode needs the freeze; verification mode writes to forward_v10_verify."""
    from btc_swing.v10.runner import run_forward

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg, paths = _ctx(config, shadow_verification)
    out = run_forward(cfg, paths, duration, shadow_verification)
    typer.echo(
        json.dumps(
            {
                k: out.get(k)
                for k in ("messages", "reconnects", "sequence_gaps", "duplicates", "engine_errors")
            }
        )
    )


@forward10.command("status")
def forward_status(
    config: Path = CFG,
    live: bool = typer.Option(False, help="also query the Bybit Demo account (GET only)"),
    verification: bool = False,
) -> None:
    from btc_swing.v10.status import status_text

    cfg, paths = _ctx(config, verification)
    typer.echo(status_text(cfg, paths, live))


@forward10.command("diagnostic")
def forward_diagnostic(config: Path = CFG, verification: bool = False) -> None:
    """Current feature values and exactly why each family is IDLE / ARMED / TRIGGERED / BLOCKED."""
    from btc_swing.v10.status import diagnostic_text

    _cfg, paths = _ctx(config, verification)
    typer.echo(diagnostic_text(paths))


@forward10.command("shadow-report")
def forward_shadow_report(
    config: Path = CFG,
    verification: bool = typer.Option(
        True,
        "--verification/--no-verification",
        help="root: --verification (pre-freeze, default) or --no-verification (observation)",
    ),
    observation: bool = typer.Option(False, "--observation", help="alias for --no-verification"),
    min_hours: float = 3.0,
    out_dir: Path = Path("reports/forward_v10"),
) -> None:
    """Shadow verification checklist (feed, mechanics, sanity); writes JSON + markdown."""
    from btc_swing.v10.report import render_shadow_report, shadow_report

    cfg, paths = _ctx(config, verification and not observation)
    rep = shadow_report(cfg, paths, min_hours)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{rep['run_id']}.json").write_text(
        json.dumps(rep, indent=1, sort_keys=True, default=str)
    )
    (out_dir / f"{rep['run_id']}.md").write_text(render_shadow_report(rep))
    (out_dir / "latest_shadow_report.json").write_text(
        json.dumps(rep, indent=1, sort_keys=True, default=str)
    )
    typer.echo(render_shadow_report(rep))


@forward10.command("freeze")
def forward_freeze(config: Path = CFG) -> None:
    """Write manifests/v10_forward_freeze.json once (observation_start = now)."""
    from btc_swing.v10.freeze import FREEZE_PATH_V10, write_freeze

    cfg, _ = _ctx(config)
    latest = Path("reports/forward_v10/latest_shadow_report.json")
    rep = json.loads(latest.read_text()) if latest.exists() else None
    rec = write_freeze(cfg, rep)
    typer.echo(
        json.dumps(
            {
                "path": str(FREEZE_PATH_V10),
                "config_hash": rec["config_hash"],
                "observation_start": rec["observation_start"],
                "code_version": rec["code_version"],
            }
        )
    )


@forward10.command("report")
def forward_report(
    day: str = typer.Option(..., help="UTC day YYYY-MM-DD"),
    config: Path = CFG,
    verification: bool = False,
) -> None:
    from btc_swing.v10.report import daily_report

    cfg, paths = _ctx(config, verification)
    typer.echo(daily_report(cfg, paths, day))


@forward10.command("warmup")
def forward_warmup(config: Path = CFG, verification: bool = False) -> None:
    """When the engine's warm-up completed, reconstructed from persisted state and bars (read-only)."""
    from btc_swing.v10.status import warmup_reconstruction

    cfg, paths = _ctx(config, verification)
    typer.echo(json.dumps(warmup_reconstruction(cfg, paths), indent=1, default=str))


@forward10.command("trades")
def forward_trades(config: Path = CFG, verification: bool = False) -> None:
    """Closed shadow trades with their decision records (warm / valid flags at trigger; read-only)."""
    from btc_swing.v10.status import trades_table

    cfg, paths = _ctx(config, verification)
    rows = trades_table(cfg, paths)
    if not rows:
        typer.echo("no closed shadow trades")
        return
    typer.echo(
        "| # | decision | family | side | entry ref/fill | stop (%, note) | tp1 | tp2 | exit | reason | legs | "
        "net USDT | net R | hold min | warm@trigger | atr5 | book | trade age s |"
    )
    typer.echo("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        typer.echo(
            f"| {i} | {r['decision_ts']} | {r['family']} | {r['side']} | {r['entry_ref']:.1f} / {r['entry_fill']:.1f} | "
            f"{r['stop']:.1f} ({r['stop_pct']:.2f}, {r['stop_note']}) | {r['tp1']:.1f} | {r['tp2']:.1f} | {r['exit_ts']} | "
            f"{r['exit_reason']} | {r['legs']} | {r['net_usd']:.2f} | {r['net_r']:.2f} | {r['hold_min']:.0f} | "
            f"{r['warm_at_trigger']} | {r['atr5_at_trigger']} | {r['book_valid_at_trigger']} | {r['trade_age_s_at_trigger']} |"
        )
    typer.echo(
        json.dumps(
            {
                "trades": len(rows),
                "all_warm_at_trigger": all(r["warm_at_trigger"] == 1.0 for r in rows),
                "net_usd": sum(r["net_usd"] for r in rows),
            }
        )
    )


@forward10.command("verify-raw")
def forward_verify_raw(config: Path = CFG, verification: bool = False) -> None:
    """Re-hash every stored raw payload."""
    from btc_swing.v10.feed import V10RawStore

    _cfg, paths = _ctx(config, verification)
    typer.echo(json.dumps(V10RawStore(paths.raw).verify()))


# ----------------------------------------------------------------------------- demo
@demo10.command("status")
def demo_status(config: Path = CFG, live: bool = False) -> None:
    from btc_swing.v10.activation import activation_state, conflict_checks, effective_activation
    from btc_swing.v10.status import exchange_snapshot

    _cfg, paths = _ctx(config)
    out: dict[str, Any] = {
        "activation": activation_state(paths),
        "effective_here": effective_activation(paths) is not None,
        "conflicts": conflict_checks(paths),
    }
    if live:
        out["exchange"] = exchange_snapshot(str(DEMO_CFG))
    typer.echo(json.dumps(out, indent=1, default=str))


@demo10.command("activate")
def demo_activate(
    note: str = typer.Option(..., help="owner's activation note"), config: Path = CFG
) -> None:
    """Owner-only V10 Demo switch: every gate (design section 6) must pass; then STRATEGY_DEMO_V10_ACTIVATED."""
    from btc_swing.v5.demo.config import ExecutionMode, load_demo_config
    from btc_swing.v5.demo.credentials import credentials_present
    from btc_swing.v5.demo.journal import verify_chain
    from btc_swing.v10.activation import record_activation, v10_gates
    from btc_swing.v10.freeze import load_freeze
    from btc_swing.v10.runner import latest_raw_age_s, runner_alive
    from btc_swing.v10.status import exchange_snapshot

    cfg, paths = _ctx(config)
    typer.echo("== test suite")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        check=False,
    )
    tests = {
        "ok": r.returncode == 0,
        "summary": (r.stdout.strip().splitlines() or ["(no output)"])[-1],
    }
    typer.echo(f"   {tests['summary']}")
    dcfg = load_demo_config(DEMO_CFG, ExecutionMode.DISABLED)
    latest = Path("reports/forward_v10/latest_shadow_report.json")
    rep = json.loads(latest.read_text()) if latest.exists() else None
    dst = json.loads(paths.demo_state.read_text()) if paths.demo_state.exists() else {}
    chains = {
        n: verify_chain(p)
        for n, p in (
            ("episodes", paths.episodes),
            ("signals", paths.signals),
            ("demo", paths.demo_journal),
        )
        if p.exists()
    }
    alive, _pid = runner_alive(paths)
    gates = v10_gates(
        paths=paths,
        cfg=cfg,
        freeze=load_freeze(),
        tests=tests,
        shadow_report=rep,
        exchange=exchange_snapshot(str(DEMO_CFG)),
        credentials_present=credentials_present(dcfg),
        runner_alive=alive,
        feed_age_s=latest_raw_age_s(paths),
        chains=chains,
        demo_state=dst,
        config_mode=dcfg.mode.value,
    )
    failed = [k for k, v in gates.items() if not v["ok"]]
    for k, v in gates.items():
        typer.echo(
            f"   [{'PASS' if v['ok'] else 'FAIL'}] {k}: {json.dumps(v['detail'], default=str)[:200]}"
        )
    if failed:
        typer.echo(json.dumps({"status": "NOT ACTIVATED", "failed_gates": failed}))
        raise typer.Exit(code=1)
    rec = record_activation(paths, gates, note)
    typer.echo(
        json.dumps(
            {
                "status": "STRATEGY_DEMO_V10_ACTIVATED",
                "activated_at": rec["activated_at"],
                "host": rec["hostname"],
                "config_hash": cfg.config_hash,
                "rule": rec["rule"],
            }
        )
    )


@demo10.command("deactivate")
def demo_deactivate(note: str = typer.Option(...), config: Path = CFG) -> None:
    from btc_swing.v10.activation import record_deactivation

    _cfg, paths = _ctx(config)
    dst = json.loads(paths.demo_state.read_text()) if paths.demo_state.exists() else {}
    rec = record_deactivation(paths, note, dst.get("position") is not None)
    typer.echo(json.dumps({"status": "STRATEGY_DEMO_V10_DEACTIVATED", **rec}, default=str))


@demo10.command("reconcile-ack")
def demo_reconcile_ack(note: str = typer.Option(...), config: Path = CFG) -> None:
    """Clear a V10 reconciliation flag after a human check (journaled; no exchange call)."""
    from btc_swing.v5.demo.journal import HashChainJournal

    _cfg, paths = _ctx(config)
    dst = json.loads(paths.demo_state.read_text()) if paths.demo_state.exists() else {}
    if not dst.get("reconcile_required"):
        typer.echo("no V10 reconciliation pending")
        return
    HashChainJournal(paths.demo_journal, "strategy_demo_v10").append(
        "RECONCILIATION_ACKNOWLEDGED",
        {"note": note, "reason": dst.get("reconcile_reason")},
        "STRATEGY_DEMO_V10",
    )
    dst["reconcile_required"], dst["reconcile_reason"] = False, None
    paths.demo_state.write_text(json.dumps(dst, sort_keys=True, default=str))
    typer.echo("reconciliation acknowledged (V10)")
