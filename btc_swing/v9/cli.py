"""`btc-swing v9` — SIMPLE TREND + VOLUME PULLBACK research commands (paper only)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from btc_swing.core.settings import get_settings

v9_app = typer.Typer(help="V9 SIMPLE TREND + VOLUME PULLBACK research (paper only)")


@v9_app.command("config")
def v9_config(config: Path = Path("config/btc_swing_v9.yaml")) -> None:
    """Validate the configuration and print its hash."""
    from btc_swing.v9.config import load_v9_config

    cfg = load_v9_config(config)
    typer.echo(json.dumps({"strategy": cfg.strategy_name, "config_hash": cfg.config_hash}))


@v9_app.command("research")
def v9_research(
    config: Path = Path("config/btc_swing_v9.yaml"),
    out_dir: Path = Path("reports/v9"),
    report: Path = Path("reports/BTC_SWING_V9_SIMPLE_TREND_VOLUME_PULLBACK.md"),
) -> None:
    """TRAIN ladder/funnel/neighbourhoods, freeze, one pass per validation window; STOPS at the report."""
    from btc_swing.v7.ingest import dataset_hash_1m
    from btc_swing.v9.config import load_v9_config
    from btc_swing.v9.data import load_funding, load_rows, ms
    from btc_swing.v9.features import build_frame_v9
    from btc_swing.v9.report import render_report
    from btc_swing.v9.research import run_research
    from btc_swing.v9.setups import build_tables

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_v9_config(config)
    data_dir = get_settings().data_dir
    start = ms(cfg.windows.train.start) - cfg.windows.warmup_days * 86_400_000
    end = ms(cfg.windows.recent_validation.end_exclusive)  # the locked holdout is never read
    rows = load_rows(data_dir, start, end)
    if rows.is_empty():
        raise typer.BadParameter("no 1m rows; the V7 Bybit 1m dataset is required")
    fr = build_frame_v9(rows, cfg)
    tables = build_tables(fr, cfg)
    funding = load_funding(data_dir, "BTCUSDT", start, end)
    res = run_research(cfg, fr, tables, funding, out_dir, dataset_hash_1m(data_dir))
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_report(res, cfg))
    typer.echo(
        json.dumps(
            {
                "verdict": res.verdict["per_window"],
                "frozen": res.frozen["spec"]["name"],
                "report": str(report),
            }
        )
    )


@v9_app.command("render")
def v9_render(
    config: Path = Path("config/btc_swing_v9.yaml"),
    out_dir: Path = Path("reports/v9"),
    report: Path = Path("reports/BTC_SWING_V9_SIMPLE_TREND_VOLUME_PULLBACK.md"),
) -> None:
    """Re-render the report from reports/v9/research.json."""
    from btc_swing.v9.config import load_v9_config
    from btc_swing.v9.report import render_report
    from btc_swing.v9.research import V9Research

    cfg = load_v9_config(config)
    d = json.loads((out_dir / "research.json").read_text())
    res = V9Research(
        d["manifest"], d["train"], d["validation"], d["frozen"], d["verdict"], d.get("extra", {})
    )
    report.write_text(render_report(res, cfg))
    typer.echo(str(report))
