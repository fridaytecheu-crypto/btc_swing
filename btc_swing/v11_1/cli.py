"""`btc-swing v111 ...` — V11.1 research commands only. No shadow runner, no exchange client."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from btc_swing.core.settings import get_settings

v111_app = typer.Typer(help="V11.1 COST-AWARE HIGH-CONVICTION INTRADAY (research only)")
CFG = Path("config/btc_swing_v11_1.yaml")
REPORT = Path("reports/BTC_V11_1_COST_AWARE_INTRADAY_BACKTEST.md")
REPORT_JSON = Path("reports/BTC_V11_1_COST_AWARE_INTRADAY_BACKTEST.json")


@v111_app.command("config")
def v111_config(config: Path = CFG) -> None:
    from btc_swing.v11_1.config import load_v111_config

    cfg = load_v111_config(config)
    typer.echo(
        json.dumps(
            {
                "strategy": cfg.strategy_name,
                "config_hash": cfg.config_hash,
                "strategy_hash": cfg.strategy_hash,
                "base_v11_strategy_hash": cfg.base.strategy_hash,
            }
        )
    )


@v111_app.command("research")
def v111_research(
    config: Path = CFG, out_dir: Path = Path("reports/v11_1"), report: Path = REPORT
) -> None:
    """The ONE pre-registered historical experiment (TRAIN + VALIDATION; holdout never read)."""
    from btc_swing.v11_1.config import load_v111_config
    from btc_swing.v11_1.report import render_backtest
    from btc_swing.v11_1.research import run_research

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_v111_config(config)
    res = run_research(cfg, get_settings().data_dir, out_dir)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_backtest(res))
    REPORT_JSON.write_text((out_dir / "backtest.json").read_text())
    typer.echo(json.dumps({"classification": res["classification"], "report": str(report)}))


@v111_app.command("render")
def v111_render(out_dir: Path = Path("reports/v11_1"), report: Path = REPORT) -> None:
    from btc_swing.v11_1.report import render_backtest

    res = json.loads((out_dir / "backtest.json").read_text())
    report.write_text(render_backtest(res))
    typer.echo(str(report))
