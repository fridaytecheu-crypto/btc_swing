"""`btc-swing v11w ...` — V11-WARM operational commands (bootstrap, seed, gap-fill, forward shadow).
No exchange client, no Demo authority, no V10 / V11 path is ever read or written."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import typer

from btc_swing.core.settings import get_settings
from btc_swing.v11_warm.config import BOOTSTRAP_MANIFEST, BOOTSTRAP_PARQUET

v11w_app = typer.Typer(help="V11-WARM: warm-started V11 shadow (same strategy; shadow only)")
CFG = Path("config/btc_swing_v11_warm.yaml")


def _load(config: Path, verification: bool) -> tuple[Any, Any, Any]:
    from btc_swing.v11_warm.config import load_v11w_config
    from btc_swing.v11_warm.paths import V11WarmPaths

    wc, v11 = load_v11w_config(config)
    return wc, v11, V11WarmPaths(get_settings().data_dir, v11.symbol, verification=verification)


@v11w_app.command("config")
def v11w_config(config: Path = CFG) -> None:
    wc, v11, _ = _load(config, False)
    typer.echo(
        json.dumps(
            {
                "strategy": wc.strategy_name,
                "v11_strategy_hash": v11.strategy_hash,
                "v11_config_hash": v11.config_hash,
                "warm_config_hash": wc.config_hash,
                "warm": json.loads(wc.warm.model_dump_json()),
            }
        )
    )


@v11w_app.command("bootstrap")
def v11w_bootstrap(
    config: Path = CFG,
    end_exclusive: str = typer.Option(None, help="UTC day (YYYY-MM-DD); default today"),
    out_parquet: Path = BOOTSTRAP_PARQUET,
    out_manifest: Path = BOOTSTRAP_MANIFEST,
) -> None:
    """Fetch the bootstrap bars (public trade archive, streamed, raw not retained) into the repo
    manifests. Only needed to (re)build the committed bootstrap; `seed` uses the committed files."""
    from btc_swing.v11_warm.bootstrap import fetch_bootstrap

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    wc, v11, _ = _load(config, False)
    end = date.fromisoformat(end_exclusive) if end_exclusive else datetime.now(UTC).date()
    st = fetch_bootstrap(v11.symbol, end, wc.warm.bootstrap_days, out_parquet, out_manifest)
    typer.echo(
        json.dumps(
            {
                "days_fetched": st.days_fetched,
                "days_unavailable": st.days_unavailable,
                "bars": st.bars,
                "last_close_ms": st.last_close_ms,
                "sha256_bars": st.sha256_bars,
            }
        )
    )


@v11w_app.command("seed")
def v11w_seed(
    config: Path = CFG,
    verification: bool = False,
    gapfill: bool = typer.Option(
        True, help="append Bybit public klines between the archive end and now (Mac only; REST)"
    ),
) -> None:
    """Seed the V11-WARM root with the committed bootstrap bars (+ optional kline gap-fill).
    Refused when the root already holds bars."""
    from btc_swing.v11_warm.bootstrap import (
        bars_sha256,
        gapfill_klines,
        load_bootstrap,
        write_bootstrap_bars,
    )
    from btc_swing.v11_warm.freeze import load_freeze

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    wc, v11, paths = _load(config, verification)
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
                wc.warm.gapfill_max_hours,
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


forward11w = typer.Typer(help="forward shadow (no orders)")
v11w_app.add_typer(forward11w, name="forward")


def _report_app_v11w() -> typer.Typer:
    from btc_swing.forward_report.cli import make_report_app
    from btc_swing.forward_report.generations import spec_v11w

    return make_report_app(spec_v11w, CFG)


v11w_app.add_typer(_report_app_v11w(), name="report")


@forward11w.command("run")
def forward_run(
    config: Path = CFG,
    duration: float | None = None,
    shadow_verification: bool = typer.Option(
        False, help="pre-freeze verification root; no freeze needed"
    ),
) -> None:
    from btc_swing.v11_warm.runner import run_forward

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    wc, v11, paths = _load(config, shadow_verification)
    out = run_forward(wc, v11, paths, duration, shadow_verification)
    typer.echo(
        json.dumps(
            {
                k: out.get(k)
                for k in ("messages", "reconnects", "sequence_gaps", "duplicates", "engine_errors")
            }
        )
    )


@forward11w.command("status")
def forward_status(config: Path = CFG, verification: bool = False) -> None:
    from btc_swing.v11_warm.status import status_text

    wc, v11, paths = _load(config, verification)
    typer.echo(status_text(wc, v11, paths))


@forward11w.command("diagnostic")
def forward_diagnostic(config: Path = CFG, verification: bool = False) -> None:
    from btc_swing.v11_warm.status import diagnostic_text

    _wc, _v11, paths = _load(config, verification)
    typer.echo(diagnostic_text(paths))


@forward11w.command("verify-warm")
def forward_verify_warm(config: Path = CFG, verification: bool = False) -> None:
    """Prove the pre-declared fields / groups are populated on the latest completed bar."""
    from btc_swing.v11_warm.status import verify_warm

    wc, _v11, paths = _load(config, verification)
    typer.echo(json.dumps(verify_warm(wc, paths), indent=1, default=str))


@forward11w.command("shadow-report")
def forward_shadow_report(
    config: Path = CFG,
    verification: bool = True,
    min_hours: float = 0.5,
    out_dir: Path = Path("reports/forward_v11_warm"),
) -> None:
    from btc_swing.v11_warm.status import shadow_report

    wc, v11, paths = _load(config, verification)
    rep = shadow_report(wc, v11, paths, min_hours)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{rep['run_id']}.json").write_text(
        json.dumps(rep, indent=1, sort_keys=True, default=str)
    )
    (out_dir / "latest_shadow_report.json").write_text(
        json.dumps(rep, indent=1, sort_keys=True, default=str)
    )
    typer.echo(json.dumps(rep, indent=1, default=str))


@forward11w.command("freeze")
def forward_freeze(config: Path = CFG) -> None:
    """Write manifests/v11_warm_forward_freeze.json once (observation_start = now + live warm-up)."""
    from btc_swing.v11_warm.freeze import FREEZE_PATH_V11W, write_freeze

    wc, v11, _ = _load(config, False)
    rep = Path("reports/forward_v11_warm/latest_shadow_report.json")
    rec = write_freeze(wc, v11, json.loads(rep.read_text()) if rep.exists() else None)
    typer.echo(
        json.dumps(
            {
                "path": str(FREEZE_PATH_V11W),
                "v11_strategy_hash": rec["v11_strategy_hash"],
                "frozen_at": rec["frozen_at"],
                "observation_start": rec["observation_start"],
                "code_version": rec["code_version"],
                "bootstrap_sha256": rec["bootstrap"]["sha256_bars"],
            }
        )
    )


@forward11w.command("trades")
def forward_trades(config: Path = CFG, verification: bool = False) -> None:
    from btc_swing.v11.status import _read

    _wc, _v11, paths = _load(config, verification)
    rows = [r["data"] for r in _read(paths.shadow_trades) if r["kind"] == "SHADOW_CLOSED"]
    if not rows:
        typer.echo("no closed shadow trades")
        return
    typer.echo(
        "| # | family | side | entry fill | stop % | exit reason | hold min | net USDT | net R | score |"
    )
    typer.echo("|---|---|---|---|---|---|---|---|---|---|")
    for i, t in enumerate(rows, 1):
        typer.echo(
            f"| {i} | {t['family']} | {'LONG' if int(t['side']) > 0 else 'SHORT'} | {float(t['entry_fill']):.1f} | {float(t['stop_pct']):.2f} | {t['exit_reason']} | {float(t['hold_min']):.0f} | {float(t['net_usd']):.2f} | {float(t['net_r']):.2f} | {float(t['score_entry']):.1f} |"
        )
