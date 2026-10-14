"""`btc-swing v112 study|combos|render` — research-only counterfactual gating study."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from btc_swing.core.settings import get_settings

v112_app = typer.Typer(
    help="V11.2 COUNTERFACTUAL GATING STUDY (research only; consumed windows only)"
)
OUT = Path("reports/v11_2")
REPORT = Path("reports/BTC_V11_2_COUNTERFACTUAL_GATING_STUDY.md")


@v112_app.command("study")
def v112_study(out_dir: Path = OUT, report: Path = REPORT) -> None:
    """Independent variants (A / B / C) versus frozen V11 on TRAIN and VALIDATION."""
    from btc_swing.v11.config import load_v11_config
    from btc_swing.v11_2.report import render_study
    from btc_swing.v11_2.study import run_study

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = run_study(load_v11_config(), get_settings().data_dir, out_dir)
    report.write_text(render_study(res))
    typer.echo(json.dumps({"study": str(out_dir / "study.json"), "report": str(report)}))


@v112_app.command("combos")
def v112_combos(
    names: str = typer.Argument(..., help="comma-separated combos, each 'VARIANT+VARIANT'"),
    out_dir: Path = OUT,
    report: Path = REPORT,
) -> None:
    """Run combinations of independent variants and merge them into study.json."""
    from btc_swing.v11.config import load_v11_config
    from btc_swing.v11_2.report import render_study
    from btc_swing.v11_2.study import VARIANTS, Variant, merge_results, run_study

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    by = {v.name: v for v in VARIANTS}
    combos = []
    for combo in names.split(","):
        parts = [by[p.strip()] for p in combo.split("+")]
        if any(p.group not in ("A", "B") for p in parts):
            raise typer.BadParameter(
                "combinations only mix A and B variants (C is studied separately)"
            )
        a = next((p.a_struct_mode for p in parts if p.a_struct_mode != "exact"), "exact")
        b = next((p.b_near_atr for p in parts if p.b_near_atr is not None), None)
        combos.append(
            Variant(
                "+".join(p.name for p in parts),
                "COMBO",
                " AND ".join(p.description for p in parts),
                a_struct_mode=a,
                b_near_atr=b,
            )
        )
    extra = run_study(
        load_v11_config(), get_settings().data_dir, out_dir / "_combos", tuple(combos)
    )
    res = merge_results(out_dir / "study.json", extra)
    report.write_text(render_study(res))
    typer.echo(json.dumps({"combos": [c.name for c in combos], "report": str(report)}))


@v112_app.command("render")
def v112_render(out_dir: Path = OUT, report: Path = REPORT) -> None:
    from btc_swing.v11_2.report import render_study

    report.write_text(render_study(json.loads((out_dir / "study.json").read_text())))
    typer.echo(str(report))
