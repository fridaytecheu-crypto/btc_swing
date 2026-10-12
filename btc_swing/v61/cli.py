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
