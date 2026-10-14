"""`btc-swing <generation> report full-history`: one typer sub-app factory shared by every generation.
The only side effect is writing the report files under `reports/<generation>/`."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from btc_swing.forward_report.metrics import full_history
from btc_swing.forward_report.render import render_full_history
from btc_swing.forward_report.spec import GenerationSpec

SpecBuilder = Callable[[Path, bool], tuple[GenerationSpec, float | None]]


def _clean(v: Any) -> Any:
    if isinstance(v, float):
        return None if (v != v or v in (float("inf"), float("-inf"))) else v
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_clean(x) for x in v]
    return v


def write_full_history(
    spec: GenerationSpec, equity: float | None
) -> tuple[Path, Path, dict[str, Any]]:
    """Compute and write `<reports_dir>/full_history_<ts>.{json,md}` plus `latest_full_history.*`.
    Refuses any reports_dir that is not under reports/<generation>/."""
    rd = spec.reports_dir
    if rd.parts[:1] != ("reports",) or len(rd.parts) < 2:
        raise RuntimeError(f"reports must be written under reports/<generation>/, not {rd}")
    rep = _clean(full_history(spec, equity))
    rd.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    js = rd / f"full_history_{ts}.json"
    md = rd / f"full_history_{ts}.md"
    js.write_text(json.dumps(rep, indent=1, sort_keys=True, default=str))
    md.write_text(render_full_history(rep))
    (rd / "latest_full_history.json").write_text(js.read_text())
    (rd / "latest_full_history.md").write_text(md.read_text())
    return js, md, rep


def make_report_app(build_spec: SpecBuilder, default_config: Path) -> typer.Typer:
    app = typer.Typer(help="read-only forward reports (journals are the source of truth)")

    @app.command("full-history")
    def full_history_cmd(
        config: Path = default_config,
        verification: bool = typer.Option(False, help="read the verification root instead"),
        stdout: bool = typer.Option(False, help="also print the markdown"),
    ) -> None:
        """Full observation history from the journals: funnel, families, sides, scores, blockers,
        shadow PnL / R, outcomes, demo (where the generation has one). Never modifies anything."""
        spec, equity = build_spec(config, verification)
        js, md, rep = write_full_history(spec, equity)
        if stdout:
            typer.echo(md.read_text())
        typer.echo(
            json.dumps(
                {
                    "generation": spec.name,
                    "json": str(js),
                    "markdown": str(md),
                    "funnel": rep["funnel"],
                    "shadow_trades": rep["shadow_trades"].get("n"),
                    "demo": "N/A"
                    if not isinstance(rep["demo"], dict)
                    else rep["demo"]["trades"].get("n"),
                }
            )
        )

    return app
