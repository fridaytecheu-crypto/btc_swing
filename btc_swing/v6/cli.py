"""`btc-swing v6` — isolated paper-only research commands. No command here can reach an exchange."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from btc_swing.core.settings import get_settings
from btc_swing.v6.config import load_v6_config

v6_app = typer.Typer(help="V6 ACTIVE INTRADAY research (paper only; no exchange client exists)")
log = logging.getLogger(__name__)


@v6_app.command("config")
def v6_config(config: Path = Path("config/btc_swing_v6.yaml")) -> None:
    """Print the config hash and the enabled (historical) families."""
    cfg = load_v6_config(config)
    typer.echo(
        json.dumps(
            {"config_hash": cfg.config_hash, "families": [f.value for f in cfg.enabled_families()]}
        )
    )


@v6_app.command("research")
def v6_research(
    config: Path = Path("config/btc_swing_v6.yaml"),
    out_dir: Path = Path("reports/v6"),
    report: Path = Path("reports/BTC_SWING_V6_ACTIVE_INTRADAY_RESEARCH.md"),
    oos: bool = typer.Option(
        False, help="ALSO run the once-only OOS window (irreversible; refused twice)"
    ),
    confirm_once_only_oos: bool = typer.Option(
        False, help="required with --oos: the owner confirms the window is spent by this run"
    ),
) -> None:
    """Development + validation with the pre-registered protocol; writes reports/v6 and the markdown report."""
    from btc_swing.v6.report import render_report
    from btc_swing.v6.research import run_v6_research

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if oos and not confirm_once_only_oos:
        raise typer.BadParameter("--oos requires --confirm-once-only-oos")
    cfg = load_v6_config(config)
    res = run_v6_research(cfg, get_settings().data_dir, out_dir, oos=oos)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_report(res, cfg))
    g = res.validation["combined"]["gate"]
    typer.echo(
        json.dumps(
            {
                "classification_validation": g["classification"],
                "hard_gate_passed": g["hard_passed"],
                "trades_per_calendar_day": res.validation["combined"]["frequency"][
                    "mean_trades_per_calendar_day"
                ],
                "net_r_mean": res.validation["combined"]["stats"].get("net_r_mean"),
                "profit_factor": res.validation["combined"]["stats"].get("profit_factor"),
                "bottleneck": res.bottleneck["summary"],
                "report": str(report),
            },
            default=str,
        )
    )


@v6_app.command("render")
def v6_render(
    config: Path = Path("config/btc_swing_v6.yaml"),
    out_dir: Path = Path("reports/v6"),
    report: Path = Path("reports/BTC_SWING_V6_ACTIVE_INTRADAY_RESEARCH.md"),
) -> None:
    """Re-render the markdown report from the persisted summary.json / manifest.json (no new run)."""
    from btc_swing.v6.report import render_report
    from btc_swing.v6.research import V6Research

    cfg = load_v6_config(config)
    summary = json.loads((out_dir / "summary.json").read_text())
    manifest = json.loads((out_dir / "manifest.json").read_text())
    res = V6Research(
        manifest,
        summary["development"],
        summary["validation"],
        summary["frozen"],
        summary["bottleneck"],
        summary.get("oos"),
    )
    report.write_text(render_report(res, cfg))
    typer.echo(str(report))
