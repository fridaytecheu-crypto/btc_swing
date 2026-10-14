"""`btc-swing v11 ...` — research (historical) and forward shadow commands. No exchange client."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import typer

from btc_swing.core.settings import get_settings

v11_app = typer.Typer(help="V11 FAST MULTI-FACTOR INTRADAY (research + shadow only)")
CFG = Path("config/btc_swing_v11.yaml")


@v11_app.command("config")
def v11_config(config: Path = CFG) -> None:
    from btc_swing.v11.config import load_v11_config

    cfg = load_v11_config(config)
    typer.echo(
        json.dumps(
            {
                "strategy": cfg.strategy_name,
                "config_hash": cfg.config_hash,
                "strategy_hash": cfg.strategy_hash,
            }
        )
    )


@v11_app.command("research")
def v11_research(
    config: Path = CFG,
    out_dir: Path = Path("reports/v11"),
    report: Path = Path("reports/BTC_V11_FAST_MULTIFACTOR_BACKTEST.md"),
) -> None:
    """The ONE pre-registered historical experiment (TRAIN + VALIDATION; holdout never read)."""
    from btc_swing.v11.config import load_v11_config
    from btc_swing.v11.report import render_backtest
    from btc_swing.v11.research import run_research

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_v11_config(config)
    res = run_research(cfg, get_settings().data_dir, out_dir)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_backtest(res))
    Path("reports/BTC_V11_FAST_MULTIFACTOR_BACKTEST.json").write_text(
        (out_dir / "backtest.json").read_text()
    )
    typer.echo(json.dumps({"classification": res["classification"], "report": str(report)}))


@v11_app.command("render")
def v11_render(
    out_dir: Path = Path("reports/v11"),
    report: Path = Path("reports/BTC_V11_FAST_MULTIFACTOR_BACKTEST.md"),
) -> None:
    from btc_swing.v11.report import render_backtest

    res = json.loads((out_dir / "backtest.json").read_text())
    report.write_text(render_backtest(res))
    typer.echo(str(report))


forward11 = typer.Typer(help="forward shadow (no orders)")
v11_app.add_typer(forward11, name="forward")


def _report_app_v11() -> typer.Typer:
    from btc_swing.forward_report.cli import make_report_app
    from btc_swing.forward_report.generations import spec_v11

    return make_report_app(spec_v11, CFG)


v11_app.add_typer(_report_app_v11(), name="report")


def _paths(config: Path, verification: bool) -> tuple[Any, Any]:
    from btc_swing.v11.config import load_v11_config
    from btc_swing.v11.paths import V11Paths

    cfg = load_v11_config(config)
    return cfg, V11Paths(get_settings().data_dir, cfg.symbol, verification=verification)


@forward11.command("run")
def forward_run(
    config: Path = CFG,
    duration: float | None = None,
    shadow_verification: bool = typer.Option(
        False, help="pre-freeze verification root; no freeze needed"
    ),
) -> None:
    from btc_swing.v11.runner import run_forward

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg, paths = _paths(config, shadow_verification)
    out = run_forward(cfg, paths, duration, shadow_verification)
    typer.echo(
        json.dumps(
            {
                k: out.get(k)
                for k in ("messages", "reconnects", "sequence_gaps", "duplicates", "engine_errors")
            }
        )
    )


@forward11.command("status")
def forward_status(config: Path = CFG, verification: bool = False) -> None:
    from btc_swing.v11.status import status_text

    cfg, paths = _paths(config, verification)
    typer.echo(status_text(cfg, paths))


@forward11.command("diagnostic")
def forward_diagnostic(config: Path = CFG, verification: bool = False) -> None:
    from btc_swing.v11.status import diagnostic_text

    _cfg, paths = _paths(config, verification)
    typer.echo(diagnostic_text(paths))


@forward11.command("shadow-report")
def forward_shadow_report(
    config: Path = CFG,
    verification: bool = True,
    min_hours: float = 3.0,
    out_dir: Path = Path("reports/forward_v11"),
) -> None:
    from btc_swing.v11.status import shadow_report

    cfg, paths = _paths(config, verification)
    rep = shadow_report(cfg, paths, min_hours)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{rep['run_id']}.json").write_text(
        json.dumps(rep, indent=1, sort_keys=True, default=str)
    )
    (out_dir / "latest_shadow_report.json").write_text(
        json.dumps(rep, indent=1, sort_keys=True, default=str)
    )
    typer.echo(json.dumps(rep, indent=1, default=str))


@forward11.command("freeze")
def forward_freeze(config: Path = CFG) -> None:
    """Write manifests/v11_forward_freeze.json once (observation_start = now)."""
    from btc_swing.v11.freeze import FREEZE_PATH_V11, write_freeze

    cfg, _ = _paths(config, False)
    hist = Path("reports/BTC_V11_FAST_MULTIFACTOR_BACKTEST.json")
    rep = Path("reports/forward_v11/latest_shadow_report.json")
    rec = write_freeze(
        cfg,
        json.loads(hist.read_text()).get("classification") if hist.exists() else None,
        json.loads(rep.read_text()) if rep.exists() else None,
    )
    typer.echo(
        json.dumps(
            {
                "path": str(FREEZE_PATH_V11),
                "strategy_hash": rec["strategy_hash"],
                "observation_start": rec["observation_start"],
                "code_version": rec["code_version"],
            }
        )
    )


@forward11.command("trades")
def forward_trades(config: Path = CFG, verification: bool = False) -> None:
    from btc_swing.v11.status import _read

    _cfg, paths = _paths(config, verification)
    rows = [r["data"] for r in _read(paths.shadow_trades) if r["kind"] == "SHADOW_CLOSED"]
    if not rows:
        typer.echo("no closed shadow trades")
        return
    typer.echo(
        "| # | family | side | entry fill | stop % | exit reason | hold min | net USDT | net R | score |"
    )
    typer.echo("|---|---|---|---|---|---|---|---|---|---|")
    for i, t in enumerate(rows, 1):
        typer.echo(
            f"| {i} | {t['family']} | {'LONG' if int(t['side']) > 0 else 'SHORT'} | {float(t['entry_fill']):.1f} | {float(t['stop_pct']):.2f} | {t['exit_reason']} | {float(t['hold_min']):.0f} | {float(t['net_usd']):.2f} | {float(t['net_r']):.2f} | {float(t['score_entry']):.1f} |"
        )
