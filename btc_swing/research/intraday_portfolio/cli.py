"""`btc-swing ipr` — intraday portfolio research (E1 / E2 / E4). Files only; nothing order-capable."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from btc_swing.core.settings import get_settings
from btc_swing.research.intraday_portfolio.inventory import build_inventory, write_inventory
from btc_swing.research.intraday_portfolio.run import run

ipr_app = typer.Typer(
    help="Intraday portfolio research harness (E1 / E2 / E4). RESEARCH ONLY, order-disabled.",
    no_args_is_help=True,
)
console = Console()
DEFAULT_OUT = Path("reports/intraday_portfolio_research")


def _data_dir(data_dir: Path | None) -> Path:
    return data_dir if data_dir is not None else Path(get_settings().data_dir)


@ipr_app.command("inventory")
def inventory_cmd(
    data_dir: Annotated[Path | None, typer.Option(help="BTC_DATA_DIR override")] = None,
    out: Annotated[Path | None, typer.Option(help="write data_inventory.json here")] = None,
) -> None:
    """Phase 0 data inventory (AVAILABLE / PARTIAL / MISSING per required input)."""
    inv = build_inventory(_data_dir(data_dir))
    if out is not None:
        write_inventory(inv, out)
    console.print(json.dumps(inv["consequences"], indent=2))


@ipr_app.command("research")
def research_cmd(
    data_dir: Annotated[Path | None, typer.Option(help="BTC_DATA_DIR override")] = None,
    out_root: Annotated[Path, typer.Option(help="report root")] = DEFAULT_OUT,
    start: Annotated[str | None, typer.Option(help="first UTC day (YYYY-MM-DD)")] = None,
    end: Annotated[str | None, typer.Option(help="last UTC day (YYYY-MM-DD), < holdout")] = None,
    run_id: Annotated[
        str | None, typer.Option(help="fixed RUN_ID (default: UTC timestamp)")
    ] = None,
    skip_e4: Annotated[bool, typer.Option(help="skip the E4 raw-store event study")] = False,
) -> None:
    """Run E1 / E2 benchmark replay, the one-position portfolio and the E4 event study."""
    res = run(
        _data_dir(data_dir),
        out_root,
        date.fromisoformat(start) if start else None,
        date.fromisoformat(end) if end else None,
        run_id,
        skip_e4,
    )
    console.print(f"run {res.run_id} -> {res.out_dir}")
    for k, v in res.verdicts.items():
        console.print(f"  {k}: {v['verdict']} — {v['reason']}")


@ipr_app.command("ingest-spot")
def ingest_spot_cmd(
    data_dir: Annotated[Path | None, typer.Option(help="BTC_DATA_DIR override")] = None,
) -> None:
    """Binance SPOT 5m klines (checksummed monthly archive) up to the month before the holdout."""
    from btc_swing.research.intraday_portfolio.spot import ingest

    rows = ingest(_data_dir(data_dir), Path("manifests/binance_spot_klines_5m_manifest.jsonl"))
    console.print(f"{len(rows)} new months written")


@ipr_app.command("research-n")
def research_n_cmd(
    data_dir: Annotated[Path | None, typer.Option(help="BTC_DATA_DIR override")] = None,
    out_root: Annotated[Path, typer.Option(help="report root")] = DEFAULT_OUT,
    start: Annotated[str | None, typer.Option(help="first UTC day (YYYY-MM-DD)")] = None,
    end: Annotated[str | None, typer.Option(help="last UTC day (YYYY-MM-DD), < holdout")] = None,
    run_id: Annotated[
        str | None, typer.Option(help="fixed RUN_ID (default: UTC timestamp)")
    ] = None,
) -> None:
    """N1 / N2 / N4 preregistered screens: census, gross markouts, cost screen, overlap, E4 audit."""
    from btc_swing.research.intraday_portfolio.run_n import run_n

    res = run_n(
        _data_dir(data_dir),
        out_root,
        date.fromisoformat(start) if start else None,
        date.fromisoformat(end) if end else None,
        run_id,
    )
    console.print(f"run {res.run_id} -> {res.out_dir}")
    for k, v in res.verdicts.items():
        console.print(f"  {k}: EDGE {v['edge']['verdict']} | CADENCE {v['cadence']['verdict']}")


@ipr_app.command("e4-audit")
def e4_audit_cmd(
    data_dir: Annotated[Path | None, typer.Option(help="BTC_DATA_DIR override")] = None,
    out: Annotated[Path | None, typer.Option(help="write e4_collection_audit.json here")] = None,
) -> None:
    """Read-only audit of what the current collectors preserve for E4 (nothing modified)."""
    from btc_swing.research.intraday_portfolio.audit import e4_collection_audit

    a = e4_collection_audit(_data_dir(data_dir))
    if out is not None:
        out.write_text(json.dumps(a, indent=2, sort_keys=True, default=str) + "\n")
    console.print(json.dumps({k: v["status"] for k, v in a["fields"].items()}, indent=2))


@ipr_app.command("research-n2-4h")
def research_n2_4h_cmd(
    data_dir: Annotated[Path | None, typer.Option(help="BTC_DATA_DIR override")] = None,
    out_root: Annotated[Path, typer.Option(help="report root")] = DEFAULT_OUT,
    source: Annotated[Path | None, typer.Option(help="frozen n2_parents.parquet")] = None,
    run_id: Annotated[
        str | None, typer.Option(help="fixed RUN_ID (default: UTC timestamp)")
    ] = None,
) -> None:
    """N2 four-hour persistence test on the FROZEN N2 events (no re-detection, no new filter)."""
    from btc_swing.research.intraday_portfolio.n2_4h import DEFAULT_SOURCE, run_n2_4h

    res = run_n2_4h(_data_dir(data_dir), out_root, source or DEFAULT_SOURCE, run_id)
    console.print(f"run {res['run_id']} -> {res['out_dir']}")
    console.print(f"  {res['decision']['verdict']} — {res['decision']['reason']}")


@ipr_app.command("research-carry")
def research_carry_cmd(
    data_dir: Annotated[Path | None, typer.Option(help="BTC_DATA_DIR override")] = None,
    out_root: Annotated[Path, typer.Option(help="report root")] = DEFAULT_OUT,
    run_id: Annotated[
        str | None, typer.Option(help="fixed RUN_ID (default: UTC timestamp)")
    ] = None,
) -> None:
    """Bybit/Binance conditional funding carry: one frozen bar/mark-based economic screen (no orders)."""
    from btc_swing.research.intraday_portfolio.carry import run_carry

    res = run_carry(_data_dir(data_dir), out_root, run_id)
    console.print(f"run {res['run_id']} -> {res['out_dir']}")
    console.print(
        f"  {res['verdict']['verdict']} (n {res['verdict']['n']}, clusters {res['verdict']['clusters']})"
    )


@ipr_app.command("fetch-cash-inputs")
def fetch_cash_inputs_cmd(
    data_dir: Annotated[Path | None, typer.Option(help="BTC_DATA_DIR override")] = None,
    start: Annotated[str, typer.Option(help="first day (YYYY-MM-DD)")] = "2021-11-01",
    end: Annotated[str, typer.Option(help="last day (YYYY-MM-DD), < holdout")] = "2025-06-30",
) -> None:
    """Public references for the cash-carry screen: NY Fed SOFR daily, Coinbase USDT-USD daily."""
    from btc_swing.research.intraday_portfolio.extdata import fetch_sofr, fetch_usdtusd

    d = _data_dir(data_dir)
    s = fetch_sofr(d, date.fromisoformat(start), date.fromisoformat(end))
    u = fetch_usdtusd(d, date.fromisoformat(start), date.fromisoformat(end))
    console.print(f"sofr rows {s.height}; usdtusd rows {u.height}")


@ipr_app.command("research-cashcarry")
def research_cashcarry_cmd(
    data_dir: Annotated[Path | None, typer.Option(help="BTC_DATA_DIR override")] = None,
    out_root: Annotated[Path, typer.Option(help="report root")] = DEFAULT_OUT,
    run_id: Annotated[
        str | None, typer.Option(help="fixed RUN_ID (default: UTC timestamp)")
    ] = None,
) -> None:
    """Cash-funded spot / short-perp funding carry: one frozen economic feasibility screen (Binance)."""
    from btc_swing.research.intraday_portfolio.cashcarry import run_cashcarry

    res = run_cashcarry(_data_dir(data_dir), out_root, run_id)
    console.print(f"run {res['run_id']} -> {res['out_dir']}")
    console.print(f"  {res['verdict']['verdict']} (valid windows {res['verdict']['n_valid']})")
