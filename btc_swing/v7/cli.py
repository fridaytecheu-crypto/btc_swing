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
