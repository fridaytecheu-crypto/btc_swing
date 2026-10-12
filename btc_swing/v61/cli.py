"""`btc-swing v61` — V6.1 isolated paper-only research commands. No exchange client exists here."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from btc_swing.core.settings import get_settings

v61_app = typer.Typer(help="V6.1 STRUCTURE + FLOW INTRADAY research (paper only)")


@v61_app.command("ingest")
def v61_ingest(
    start: str = typer.Option("2021-11-15", help="first UTC day (45 d warm-up before development)"),
    end: str = typer.Option("2025-06-30", help="last UTC day (validation end; holdout refused)"),
    prefetch: int = typer.Option(3, help="parallel downloads"),
    allow_holdout: bool = typer.Option(False, help="owner only: permit days >= 2025-07-01"),
    stats_out: Path = Path("manifests/v61_bybit_ingest_stats.json"),
) -> None:
    """Stream the Bybit public trade archive into immutable 5m rows (raw gz not retained)."""
    from btc_swing.v61.ingest import BybitIngestStats, BybitTradeIngestor

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    st = BybitIngestStats()
    ing = BybitTradeIngestor(get_settings().data_dir, prefetch=prefetch)
    ing.ingest(start, end, st, allow_holdout=allow_holdout)
    stats_out.parent.mkdir(parents=True, exist_ok=True)
    stats_out.write_text(json.dumps(st.as_dict(), indent=1, sort_keys=True))
    typer.echo(json.dumps(st.as_dict()))


@v61_app.command("research")
def v61_research(
    config: Path = Path("config/btc_swing_v6_1.yaml"),
    out_dir: Path = Path("reports/v61"),
    report: Path = Path("reports/BTC_SWING_V6_1_STRUCTURE_FLOW_RESEARCH.md"),
) -> None:
    """Development + validation with the pre-registered protocol; STOPS before the locked holdout."""
    from btc_swing.v61.config import load_v61_config
    from btc_swing.v61.report import render_report_v61
    from btc_swing.v61.research import run_v61_research

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_v61_config(config)
    res = run_v61_research(cfg, get_settings().data_dir, out_dir)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_report_v61(res, cfg))
    v = res.verdict
    typer.echo(
        json.dumps(
            {
                "frequency_pass": v["frequency_pass"],
                "net_edge_pass": v["net_edge_pass"],
                "recommendation": v["recommendation"],
                "report": str(report),
            }
        )
    )


@v61_app.command("render")
def v61_render(
    config: Path = Path("config/btc_swing_v6_1.yaml"),
    out_dir: Path = Path("reports/v61"),
    report: Path = Path("reports/BTC_SWING_V6_1_STRUCTURE_FLOW_RESEARCH.md"),
) -> None:
    """Re-render the report from the persisted summary.json / manifest.json."""
    from btc_swing.v61.config import load_v61_config
    from btc_swing.v61.report import render_report_v61
    from btc_swing.v61.research import V61Research

    cfg = load_v61_config(config)
    summary = json.loads((out_dir / "summary.json").read_text())
    manifest = json.loads((out_dir / "manifest.json").read_text())
    res = V61Research(
        manifest,
        summary["development"],
        summary["validation"],
        summary["frozen"],
        summary["final"],
        summary["verdict"],
    )
    report.write_text(render_report_v61(res, cfg))
    typer.echo(str(report))
