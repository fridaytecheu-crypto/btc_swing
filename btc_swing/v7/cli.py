"""`btc-swing v7` — V7 microstructure research commands (paper only; no exchange client)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from btc_swing.core.settings import get_settings

v7_app = typer.Typer(help="V7 MICROSTRUCTURE SPECIALIST research (paper only)")


@v7_app.command("ingest")
def v7_ingest(
    start: str = typer.Option("2021-11-15", help="first UTC day (warm-up before discovery)"),
    end: str = typer.Option(
        "2025-06-30", help="last UTC day (recent validation end; holdout refused)"
    ),
    prefetch: int = typer.Option(2, help="parallel downloads"),
    allow_holdout: bool = typer.Option(False, help="owner only: permit days >= 2025-07-01"),
    stats_out: Path = Path("manifests/v7_bybit_1m_ingest_stats.json"),
) -> None:
    """Stream the Bybit trade archive into 1m microstructure bars with PIT large-trade thresholds."""
    from btc_swing.v7.ingest import BybitTrade1mIngestor, V7IngestStats

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    st = V7IngestStats()
    BybitTrade1mIngestor(get_settings().data_dir, prefetch=prefetch).ingest(
        start, end, st, allow_holdout=allow_holdout
    )
    stats_out.parent.mkdir(parents=True, exist_ok=True)
    stats_out.write_text(json.dumps(st.as_dict(), indent=1, sort_keys=True))
    typer.echo(json.dumps(st.as_dict()))


@v7_app.command("discover")
def v7_discover(
    config: Path = Path("config/btc_swing_v7.yaml"),
    out_dir: Path = Path("reports/v7"),
    report: Path = Path("reports/BTC_SWING_V7_EDGE_DISCOVERY.md"),
) -> None:
    """Phase 1 edge discovery with matched controls on the 1m Bybit frame; STOPS at the report."""
    from btc_swing.v7.config import load_v7_config
    from btc_swing.v7.discovery import ms, run_discovery
    from btc_swing.v7.features import build_frame_v7
    from btc_swing.v7.ingest import dataset_hash_1m, load_bybit_1m
    from btc_swing.v7.report import render_discovery

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_v7_config(config)
    data_dir = get_settings().data_dir
    start = ms(cfg.windows.discovery.start) - cfg.windows.warmup_days * 86_400_000
    end = ms(cfg.windows.recent_validation.end_exclusive)  # the locked holdout is never read
    rows = load_bybit_1m(data_dir, start_ms=start, end_ms=end)
    if rows.is_empty():
        raise typer.BadParameter("no 1m rows; run `btc-swing v7 ingest` first")
    fr = build_frame_v7(rows, cfg)
    res = run_discovery(cfg, fr, out_dir, dataset_hash_1m(data_dir))
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_discovery(res, cfg))
    typer.echo(
        json.dumps(
            {
                "repeatable_discovery": res.summary["repeatable"],
                "survive_2025": res.summary["survive_2025"],
                "report": str(report),
            }
        )
    )


@v7_app.command("render")
def v7_render(
    config: Path = Path("config/btc_swing_v7.yaml"),
    out_dir: Path = Path("reports/v7"),
    report: Path = Path("reports/BTC_SWING_V7_EDGE_DISCOVERY.md"),
) -> None:
    """Re-render the discovery report from reports/v7/discovery.json."""
    from btc_swing.v7.config import load_v7_config
    from btc_swing.v7.discovery import V7Discovery
    from btc_swing.v7.report import render_discovery

    cfg = load_v7_config(config)
    d = json.loads((out_dir / "discovery.json").read_text())
    manifest = json.loads((out_dir / "manifest.json").read_text())
    res = V7Discovery(manifest, d["discovery"], d["validation_1"], d["recent"], d["summary"])
    report.write_text(render_discovery(res, cfg))
    typer.echo(str(report))
