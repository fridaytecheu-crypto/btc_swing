"""`btc-swing v11lw ...` — V11-LITE-WARM operational commands (seed, forward shadow, reports). The shadow
commands have no exchange client; the optional Bybit DEMO execution layer lives in the `demo` sub-package
(`btc-swing v11lw demo ...`, owner activation only). No V10 / V11 / V11-WARM forward path is ever written."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from btc_swing.core.settings import get_settings
from btc_swing.v11_lite_warm.config import STRATEGY_DELTA
from btc_swing.v11_lite_warm.demo.cli import demo11lw
from btc_swing.v11_warm.config import BOOTSTRAP_MANIFEST, BOOTSTRAP_PARQUET

v11lw_app = typer.Typer(
    help="V11-LITE-WARM: warm-started V11 shadow with A/B/C structure as labels (shadow only)"
)
CFG = Path("config/btc_swing_v11_lite_warm.yaml")
CFG_CONTROL = Path("config/btc_swing_v11_warm.yaml")


def _load(config: Path, verification: bool) -> tuple[Any, Any, Any]:
    from btc_swing.v11_lite_warm.config import load_v11lw_config
    from btc_swing.v11_lite_warm.paths import V11LiteWarmPaths

    lc, v11 = load_v11lw_config(config)
    return lc, v11, V11LiteWarmPaths(get_settings().data_dir, v11.symbol, verification=verification)


@v11lw_app.command("config")
def v11lw_config(config: Path = CFG) -> None:
    from btc_swing.v11_lite_warm.config import lite_strategy_hash

    lc, v11, _ = _load(config, False)
    typer.echo(
        json.dumps(
            {
                "strategy": lc.strategy_name,
                "strategy_delta": STRATEGY_DELTA,
                "v11_strategy_hash": v11.strategy_hash,
                "lite_strategy_hash": lite_strategy_hash(v11, lc),
                "v11_config_hash": v11.config_hash,
                "lite_config_hash": lc.config_hash,
                "outcome_horizons_min": list(v11.outcomes.horizons_min),
                "warm": json.loads(lc.warm.model_dump_json()),
                "lite": json.loads(lc.lite.model_dump_json()),
            }
        )
    )


@v11lw_app.command("seed")
def v11lw_seed(
    config: Path = CFG,
    verification: bool = False,
    gapfill: bool = typer.Option(
        True, help="append Bybit public klines between the archive end and now (Mac only; REST)"
    ),
) -> None:
    """Seed the V11-LITE-WARM root with the committed V11-WARM bootstrap bars (+ optional kline
    gap-fill). Refused when the root already holds bars."""
    from btc_swing.v11_lite_warm.freeze import load_freeze
    from btc_swing.v11_warm.bootstrap import (
        bars_sha256,
        gapfill_klines,
        load_bootstrap,
        write_bootstrap_bars,
    )

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    lc, v11, paths = _load(config, verification)
    paths.ensure()
    man = json.loads(BOOTSTRAP_MANIFEST.read_text())
    df = load_bootstrap(BOOTSTRAP_PARQUET)
    if bars_sha256(df) != man["sha256_bars"]:
        raise typer.Exit(code=_fail("bootstrap parquet does not match its manifest hash"))
    fz = load_freeze()
    obs = int(fz["observation_start_ms"]) if (fz and not verification) else None
    n_boot = write_bootstrap_bars(df, paths.bars_1m, obs)
    n_gap = 0
    gap_meta: dict[str, Any] = {}
    if gapfill:
        try:
            n_gap, gap_meta = gapfill_klines(
                v11.symbol,
                int(df["open_ms"][-1]) + 60_000,
                paths.bars_1m,
                lc.warm.gapfill_max_hours,
                obs,
            )
        except Exception as e:  # REST is geo-blocked in the cloud container: gap warms live instead
            gap_meta = {"error": repr(e)}
            typer.echo(f"gap-fill unavailable ({e!r}); the gap warms live", err=True)
    rec = {
        "seeded_at": datetime.now(UTC).isoformat(),
        "bootstrap_bars": n_boot,
        "gapfill_bars": n_gap,
        "gapfill": gap_meta,
        "first_open_ms": int(df["open_ms"][0]),
        "last_close": datetime.fromtimestamp(
            (int(df["open_ms"][-1]) + 60_000) / 1000, tz=UTC
        ).isoformat(),
        "sha256_bars": man["sha256_bars"],
        "manifest": str(BOOTSTRAP_MANIFEST),
    }
    paths.seed_record.write_text(json.dumps(rec, indent=1, sort_keys=True))
    typer.echo(json.dumps(rec))


def _fail(msg: str) -> int:
    typer.echo(msg, err=True)
    return 2


forward11lw = typer.Typer(help="forward shadow (no orders)")
v11lw_app.add_typer(forward11lw, name="forward")
report11lw = typer.Typer(help="read-only reports (journals are the source of truth)")
v11lw_app.add_typer(report11lw, name="report")
v11lw_app.add_typer(demo11lw, name="demo")


@forward11lw.command("run")
def forward_run(
    config: Path = CFG,
    duration: float | None = None,
    shadow_verification: bool = typer.Option(
        False, help="pre-freeze verification root; no freeze needed"
    ),
) -> None:
    from btc_swing.v11_lite_warm.runner import run_forward

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    lc, v11, paths = _load(config, shadow_verification)
    out = run_forward(lc, v11, paths, duration, shadow_verification)
    typer.echo(
        json.dumps(
            {
                k: out.get(k)
                for k in ("messages", "reconnects", "sequence_gaps", "duplicates", "engine_errors")
            }
        )
    )


@forward11lw.command("status")
def forward_status(config: Path = CFG, verification: bool = False) -> None:
    from btc_swing.v11_lite_warm.status import status_text

    lc, v11, paths = _load(config, verification)
    typer.echo(status_text(lc, v11, paths))


@forward11lw.command("diagnostic")
def forward_diagnostic(config: Path = CFG, verification: bool = False) -> None:
    from btc_swing.v11_lite_warm.status import diagnostic_text

    _lc, _v11, paths = _load(config, verification)
    typer.echo(diagnostic_text(paths))


@forward11lw.command("verify-warm")
def forward_verify_warm(config: Path = CFG, verification: bool = False) -> None:
    """Prove the pre-declared fields / groups, labels and context telemetry are populated."""
    from btc_swing.v11_lite_warm.status import verify_warm

    lc, _v11, paths = _load(config, verification)
    typer.echo(json.dumps(verify_warm(lc, paths), indent=1, default=str))


@forward11lw.command("shadow-report")
def forward_shadow_report(
    config: Path = CFG,
    verification: bool = True,
    min_hours: float = 0.5,
    out_dir: Path = Path("reports/forward_v11_lite_warm"),
) -> None:
    from btc_swing.v11_lite_warm.status import shadow_report

    lc, v11, paths = _load(config, verification)
    rep = shadow_report(lc, v11, paths, min_hours)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{rep['run_id']}.json").write_text(
        json.dumps(rep, indent=1, sort_keys=True, default=str)
    )
    (out_dir / "latest_shadow_report.json").write_text(
        json.dumps(rep, indent=1, sort_keys=True, default=str)
    )
    typer.echo(json.dumps(rep, indent=1, default=str))


@forward11lw.command("freeze")
def forward_freeze(
    config: Path = CFG,
    observation_start: str = typer.Option(
        None,
        help="explicit UTC launch instant (ISO, e.g. 2026-10-15T12:00:00+00:00); must be >= now + "
        "live warm-up; default now + live warm-up",
    ),
) -> None:
    """Write manifests/v11_lite_warm_forward_freeze.json once."""
    from btc_swing.v11_lite_warm.freeze import FREEZE_PATH_V11LW, write_freeze

    lc, v11, _ = _load(config, False)
    rep = Path("reports/forward_v11_lite_warm/latest_shadow_report.json")
    obs_ms = (
        int(datetime.fromisoformat(observation_start).timestamp() * 1000)
        if observation_start
        else None
    )
    rec = write_freeze(
        lc, v11, json.loads(rep.read_text()) if rep.exists() else None, observation_start_ms=obs_ms
    )
    typer.echo(
        json.dumps(
            {
                "path": str(FREEZE_PATH_V11LW),
                "v11_strategy_hash": rec["v11_strategy_hash"],
                "lite_strategy_hash": rec["lite_strategy_hash"],
                "bookkeeping_version": rec.get("bookkeeping_version"),
                "frozen_at": rec["frozen_at"],
                "observation_start": rec["observation_start"],
                "code_version": rec["code_version"],
                "bootstrap_sha256": rec["bootstrap"]["sha256_bars"],
            }
        )
    )


@forward11lw.command("trades")
def forward_trades(config: Path = CFG, verification: bool = False) -> None:
    from btc_swing.v11_lite_warm.status import _read

    _lc, _v11, paths = _load(config, verification)
    rows = [r["data"] for r in _read(paths.shadow_trades) if r["kind"] == "SHADOW_CLOSED"]
    if not rows:
        typer.echo("no closed shadow trades")
        return
    typer.echo(
        "| # | selected_template | side | frozen labels A/B/C | incremental | entry fill | stop % | exit reason | hold min | net USDT | net R | score |"
    )
    typer.echo("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, t in enumerate(rows, 1):
        d = t.get("decision") or {}
        lab = d.get("structure_labels") or {}
        side = "LONG" if int(t["side"]) > 0 else "SHORT"
        labels = "/".join("1" if lab.get(x) else "0" for x in ("A", "B", "C"))
        tmpl = t.get("selected_template", t.get("family"))
        typer.echo(
            f"| {i} | {tmpl} | {side} | {labels} | {'yes' if d.get('incremental') else 'no'} | {float(t['entry_fill']):.1f} | {float(t['stop_pct']):.2f} | {t['exit_reason']} | {float(t['hold_min']):.0f} | {float(t['net_usd']):.2f} | {float(t['net_r']):.2f} | {float(t['score_entry']):.1f} |"
        )


@report11lw.command("full-history")
def report_full_history(
    config: Path = CFG,
    verification: bool = typer.Option(False, help="read the verification root instead"),
    stdout: bool = typer.Option(False, help="also print the markdown"),
) -> None:
    """Generic read-only full-history report (same layer as v10 / v11 / v11w)."""
    from btc_swing.forward_report.cli import write_full_history
    from btc_swing.forward_report.generations import spec_v11lw

    spec, equity = spec_v11lw(config, verification)
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
            }
        )
    )


@report11lw.command("compare")
def report_compare(
    config: Path = CFG,
    control_config: Path = CFG_CONTROL,
    verification: bool = typer.Option(False, help="read both verification roots instead"),
    stdout: bool = typer.Option(False, help="also print the markdown"),
) -> None:
    """READ-ONLY: V11-WARM (control) vs V11-LITE-WARM (challenger), incremental cohort, Fibonacci
    attribution, checkpoints. Writes only under reports/forward_v11_lite_warm/."""
    from btc_swing.v11_lite_warm.compare import write_compare

    js, md, rep = write_compare(config, control_config, verification)
    if stdout:
        typer.echo(md.read_text())
    typer.echo(
        json.dumps(
            {
                "json": str(js),
                "markdown": str(md),
                "control": rep["control"]["counts"],
                "challenger": rep["challenger"]["counts"],
                "incremental": rep["incremental_cohort"]["summary"],
                "checkpoints": [(c["name"], c["passed"]) for c in rep["checkpoints"]],
            },
            default=str,
        )
    )
