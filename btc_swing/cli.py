"""btc-swing CLI — research only. No order placement exists in this package."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from btc_swing.core.config import BtcStrategyConfig, load_btc_config
from btc_swing.core.enums import Timeframe
from btc_swing.core.settings import get_settings
from btc_swing.providers.base import CryptoMarketDataProvider, Dataset
from btc_swing.storage.archive_store import ArchiveStore
from btc_swing.storage.bar_store import BarStore

app = typer.Typer(no_args_is_help=True, help="BTC Leveraged Swing Engine V1 (paper/backtest only)")
data_app = typer.Typer(help="Data: probe, ingest")
app.add_typer(data_app, name="data")
console = Console()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

ConfigOpt = Annotated[
    Path | None,
    typer.Option("--config", help="strategy YAML (default config/btc_swing.default.yaml)"),
]


def _cfg(path: Path | None, provider: str | None) -> BtcStrategyConfig:
    ov: dict[str, Any] = {"data": {"provider": provider}} if provider else {}
    return load_btc_config(path, ov or None)


def _data_dir() -> Path:
    return get_settings().data_dir


def _provider(cfg: BtcStrategyConfig, start: str, end: str) -> CryptoMarketDataProvider:
    if cfg.data.provider == "synthetic":
        from btc_swing.providers.synthetic import SyntheticBtcProvider

        return SyntheticBtcProvider(start, end)
    from btc_swing.providers.binance_vision import BinanceVisionProvider, default_entitlement

    return BinanceVisionProvider(default_entitlement(cfg.data.latency_minutes))


@data_app.command("probe")
def data_probe(config: ConfigOpt = None, symbol: str = "BTCUSDT") -> None:
    """List the periods the Binance Vision archive publishes for every dataset we may use."""
    from btc_swing.providers.binance_vision import BinanceVisionProvider

    p = BinanceVisionProvider()
    rows: dict[str, Any] = {}
    for ds, tf in [
        (Dataset.PERP_KLINES, Timeframe.M5),
        (Dataset.PERP_KLINES, Timeframe.M15),
        (Dataset.PERP_KLINES, Timeframe.H1),
        (Dataset.PERP_KLINES, Timeframe.H4),
        (Dataset.PERP_KLINES, Timeframe.D1),
        (Dataset.SPOT_KLINES, Timeframe.M5),
        (Dataset.FUNDING, None),
        (Dataset.PREMIUM_INDEX, Timeframe.M5),
        (Dataset.MARK_PRICE, Timeframe.M5),
        (Dataset.METRICS, None),
    ]:
        periods = p.list_periods(ds, symbol, tf)
        rows[f"{ds.value}/{tf.value if tf else 'na'}"] = {
            "n_periods": len(periods),
            "first": periods[0] if periods else None,
            "last": periods[-1] if periods else None,
        }
    console.print_json(json.dumps(rows))


@data_app.command("ingest")
def data_ingest(
    start: Annotated[str, typer.Option("--from", help="YYYY-MM")],
    end: Annotated[str, typer.Option("--to", help="YYYY-MM")],
    config: ConfigOpt = None,
    provider: str | None = None,
) -> None:
    """Resumable ingestion of archive files into immutable raw storage and Parquet datasets."""
    from btc_swing.ingest.pipeline import BtcIngestor

    cfg = _cfg(config, provider)
    d = _data_dir()
    ing = BtcIngestor(_provider(cfg, start, end), ArchiveStore(d), BarStore(d), cfg)
    stats = ing.ingest(start, end)
    console.print_json(json.dumps(stats.as_dict()))


def _load_bars(cfg: BtcStrategyConfig, d: Path) -> tuple[Any, Any, dict[str, str]]:
    import polars as pl

    bs = BarStore(d)
    sym = cfg.instrument.symbol
    bars = bs.scan(Dataset.PERP_KLINES, sym, Timeframe.M5)
    if bars.is_empty():
        raise typer.BadParameter("no 5m bars ingested; run `btc-swing data ingest` first")
    funding: pl.DataFrame | None = None
    hashes = {"perp_klines_5m": bs.series_hash(Dataset.PERP_KLINES, sym, Timeframe.M5)}
    if cfg.costs.apply_funding:
        funding = bs.scan(Dataset.FUNDING, sym, None, time_col="time_ms")
        hashes["funding"] = bs.series_hash(Dataset.FUNDING, sym, None)
    return bars, funding, hashes


def _ms(s: str) -> int:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


@app.command("backtest")
def backtest(
    start: Annotated[str, typer.Option("--from", help="ISO date/time UTC, e.g. 2024-01-01")],
    end: Annotated[str, typer.Option("--to", help="ISO date/time UTC (exclusive)")],
    config: ConfigOpt = None,
    provider: str | None = None,
    out: Path | None = None,
    title: str = "BTC Swing V1 — backtest",
    verify_determinism: bool = False,
) -> None:
    """Chronological 5m backtest on ingested data; writes trades/episodes/decisions/report."""
    from btc_swing.backtest.engine import BacktestEngine
    from btc_swing.research.metrics import compute_metrics
    from btc_swing.research.report import render_report

    cfg = _cfg(config, provider)
    d = _data_dir()
    bars, funding, hashes = _load_bars(cfg, d)
    eng = BacktestEngine(cfg, bars, funding, hashes)
    res = eng.run(_ms(start), _ms(end))
    if verify_determinism:
        res2 = BacktestEngine(cfg, bars, funding, hashes).run(_ms(start), _ms(end))
        identical = res2.result_hash == res.result_hash
        res.manifest["determinism_check"] = {"identical": identical, "hash_b": res2.result_hash}
        if not identical:
            raise typer.Exit(code=2)
    metrics = compute_metrics(
        res.trades, res.episodes, res.daily_equity, res.manifest, cfg.research
    )
    run_dir = out or (
        d / "btc" / "runs" / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{res.result_hash[:8]}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(res.manifest, indent=1, sort_keys=True, default=str)
    )
    (run_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=1, sort_keys=True, default=str)
    )
    for name, df in (
        ("trades", res.trades),
        ("episodes", res.episodes),
        ("decisions", res.decisions),
        ("daily_equity", res.daily_equity),
    ):
        if not df.is_empty():
            df.write_parquet(run_dir / f"{name}.parquet")
    caveats = [
        "Paper/backtest research output. No live trading, no real money, no authenticated exchange access.",
        "Pre-registered default parameters, untuned. One setup family pair (TREND_PULLBACK) is implemented in the V1 foundation.",
        "Fees, slippage and funding are modelled; liquidation is modelled on traded-price extremes (mark price not yet used).",
        "A small trade count says nothing about edge in either direction; see `reliable` flags (n >= min_cell_n).",
    ]
    (run_dir / "report.md").write_text(
        render_report(res.manifest, metrics, cfg.canonical_yaml(), title, caveats)
    )
    console.print_json(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "result_hash": res.result_hash,
                "n_trades": res.manifest["n_trades"],
                "n_episodes": res.manifest["n_episodes"],
                "final_equity": res.manifest["final_equity"],
                "determinism_check": res.manifest.get("determinism_check"),
            }
        )
    )


@app.command("phase2")
def phase2(
    config: ConfigOpt = None,
    dev_start: str = "2022-01-01",
    dev_end: str = "2024-01-01",
    val_start: str = "2024-01-01",
    val_end: str = "2025-01-01",
    null_k: int = 20,
    seed: int = 7,
    out: Path | None = None,
    report: Path = Path("reports/BTC_SWING_V1_PHASE2_VALIDATION.md"),
) -> None:
    """Phase 2 validation: frozen defaults, two chronological segments, leverage comparison,
    null benchmark, forward labels, PIT audit, 21-section report. Research only."""
    from btc_swing.research.phase2 import Segment, load_inputs, run_phase2
    from btc_swing.research.phase2_report import render_phase2

    cfg = _cfg(config, None)
    d = _data_dir()
    segs = [
        Segment("dev_2022_2023", _ms(dev_start), _ms(dev_end)),
        Segment("val_2024", _ms(val_start), _ms(val_end)),
    ]
    inp = load_inputs(cfg, d, segs[0].start_ms, segs[-1].end_ms)
    run_dir = out or (d / "btc" / "runs" / f"phase2_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    res = run_phase2(cfg, inp, segs, [1.0, 3.0, 5.0, 10.0], null_k, seed, run_dir)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_phase2(res))
    console.print_json(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "report": str(report),
                "result_hash": res.manifest["result_hash"],
                "deterministic": res.manifest["deterministic"],
                "n_trades": res.manifest["n_trades"],
                "n_episodes": res.manifest["n_episodes"],
                "expectancy_R": res.metrics_all["overall"].get("expectancy_R"),
            }
        )
    )


@app.command("phase21")
def phase21(
    config: ConfigOpt = None,
    dev_start: str = "2022-01-01",
    dev_end: str = "2024-01-01",
    val_start: str = "2024-01-01",
    val_end: str = "2025-01-01",
    null_k: int = 20,
    seed: int = 7,
    out: Path | None = None,
    report: Path = Path("reports/BTC_SWING_V1_PHASE2_1_ENTRY_MECHANICS.md"),
    expected_control_hash: Path = Path("manifests/phase2_validation_run_manifest.json"),
) -> None:
    """Phase 2.1: CONTROL (frozen Phase 2) vs ZONE_ENTRY on identical data; 17-section report."""
    from btc_swing.research.phase2 import Segment, load_inputs
    from btc_swing.research.phase21 import run_phase21
    from btc_swing.research.phase21_report import render_phase21

    cfg = _cfg(config, None)
    d = _data_dir()
    segs = [
        Segment("dev_2022_2023", _ms(dev_start), _ms(dev_end)),
        Segment("val_2024", _ms(val_start), _ms(val_end)),
    ]
    inp = load_inputs(cfg, d, segs[0].start_ms, segs[-1].end_ms)
    expected = (
        json.loads(expected_control_hash.read_text())["result_hash"]
        if expected_control_hash.exists()
        else None
    )
    run_dir = out or (
        d / "btc" / "runs" / f"phase21_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    res = run_phase21(cfg, inp, segs, null_k, seed, run_dir, expected)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_phase21(res))
    console.print_json(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "report": str(report),
                "baseline_identical": res.baseline_check["identical"],
                "control": {
                    "trades": res.control.result.manifest["n_trades"],
                    "expectancy_R": res.control.metrics["overall"].get("expectancy_R"),
                },
                "zone_entry": {
                    "trades": res.variant.result.manifest["n_trades"],
                    "expectancy_R": res.variant.metrics["overall"].get("expectancy_R"),
                },
            }
        )
    )


@app.command("phase22")
def phase22(
    config: ConfigOpt = None,
    dev_start: str = "2022-01-01",
    dev_end: str = "2024-01-01",
    val_start: str = "2024-01-01",
    val_end: str = "2025-01-01",
    null_k: int = 20,
    seed: int = 7,
    out: Path | None = None,
    report: Path = Path("reports/BTC_SWING_V1_PHASE2_2_EARLY_ENTRY_CONFIRMATION_EXIT.md"),
    expected_control_hash: Path = Path("manifests/phase2_validation_run_manifest.json"),
    phase21_summary: Path = Path("data/btc/runs/phase21_entry_mechanics/summary.json"),
) -> None:
    """Phase 2.2: CONTROL vs ZONE_ENTRY_CONFIRM_EXIT on identical data; 19-section report."""
    from btc_swing.research.phase2 import Segment, load_inputs
    from btc_swing.research.phase22 import run_phase22
    from btc_swing.research.phase22_report import render_phase22

    cfg = _cfg(config, None)
    d = _data_dir()
    segs = [
        Segment("dev_2022_2023", _ms(dev_start), _ms(dev_end)),
        Segment("val_2024", _ms(val_start), _ms(val_end)),
    ]
    inp = load_inputs(cfg, d, segs[0].start_ms, segs[-1].end_ms)
    expected = (
        json.loads(expected_control_hash.read_text())["result_hash"]
        if expected_control_hash.exists()
        else None
    )
    run_dir = out or (
        d / "btc" / "runs" / f"phase22_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    res = run_phase22(
        cfg,
        inp,
        segs,
        null_k,
        seed,
        run_dir,
        expected,
        phase21_summary if phase21_summary.exists() else None,
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_phase22(res))
    console.print_json(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "report": str(report),
                "baseline_identical": res.baseline_check["identical"],
                "control": {
                    "trades": res.control.result.manifest["n_trades"],
                    "expectancy_R": res.control.metrics["overall"].get("expectancy_R"),
                },
                "variant": {
                    "trades": res.variant.result.manifest["n_trades"],
                    "expectancy_R": res.variant.metrics["overall"].get("expectancy_R"),
                    "confirmed_share": res.timing.get("confirmed_share"),
                },
            }
        )
    )


@app.command("phase23")
def phase23(
    config: ConfigOpt = None,
    dev_start: str = "2022-01-01",
    dev_end: str = "2024-01-01",
    val_start: str = "2024-01-01",
    val_end: str = "2025-01-01",
    null_k: int = 20,
    seed: int = 7,
    out: Path | None = None,
    report: Path = Path("reports/BTC_SWING_V1_PHASE2_3_POST_TP1_EXIT.md"),
    expected_control_hash: Path = Path("manifests/phase2_validation_run_manifest.json"),
) -> None:
    """Phase 2.3: CONTROL (breakeven after TP1) vs STRUCTURAL_TRAIL_AFTER_TP1 on identical data."""
    from btc_swing.research.phase2 import Segment, load_inputs
    from btc_swing.research.phase23 import run_phase23
    from btc_swing.research.phase23_report import render_phase23

    cfg = _cfg(config, None)
    d = _data_dir()
    segs = [
        Segment("dev_2022_2023", _ms(dev_start), _ms(dev_end)),
        Segment("val_2024", _ms(val_start), _ms(val_end)),
    ]
    inp = load_inputs(cfg, d, segs[0].start_ms, segs[-1].end_ms)
    expected = (
        json.loads(expected_control_hash.read_text())["result_hash"]
        if expected_control_hash.exists()
        else None
    )
    run_dir = out or (
        d / "btc" / "runs" / f"phase23_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    res = run_phase23(cfg, inp, segs, null_k, seed, run_dir, expected)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_phase23(res))
    console.print_json(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "report": str(report),
                "baseline_identical": res.baseline_check["identical"],
                "control": {
                    "trades": res.control.result.manifest["n_trades"],
                    "expectancy_R": res.control.metrics["overall"].get("expectancy_R"),
                },
                "variant": {
                    "trades": res.variant.result.manifest["n_trades"],
                    "expectancy_R": res.variant.metrics["overall"].get("expectancy_R"),
                },
                "tp1_pairs": res.paired.get("tp1_pairs"),
            }
        )
    )


@app.command("phase24")
def phase24(
    config: ConfigOpt = None,
    dev_start: str = "2022-01-01",
    dev_end: str = "2024-01-01",
    val_start: str = "2024-01-01",
    val_end: str = "2025-01-01",
    null_k: int = 20,
    seed: int = 7,
    out: Path | None = None,
    report: Path = Path("reports/BTC_SWING_V1_PHASE2_4_SHORT_REGIME.md"),
    expected_control_hash: Path = Path("manifests/phase2_validation_run_manifest.json"),
) -> None:
    """Phase 2.4: CONTROL vs NO_NEW_SHORT_IN_TREND_DOWN on identical data; 18-section report."""
    from btc_swing.research.phase2 import Segment, load_inputs
    from btc_swing.research.phase24 import run_phase24
    from btc_swing.research.phase24_report import render_phase24

    cfg = _cfg(config, None)
    d = _data_dir()
    segs = [
        Segment("dev_2022_2023", _ms(dev_start), _ms(dev_end)),
        Segment("val_2024", _ms(val_start), _ms(val_end)),
    ]
    inp = load_inputs(cfg, d, segs[0].start_ms, segs[-1].end_ms)
    expected = (
        json.loads(expected_control_hash.read_text())["result_hash"]
        if expected_control_hash.exists()
        else None
    )
    run_dir = out or (
        d / "btc" / "runs" / f"phase24_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    res = run_phase24(cfg, inp, segs, null_k, seed, run_dir, expected)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_phase24(res))
    console.print_json(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "report": str(report),
                "baseline_identical": res.baseline_check["identical"],
                "control": {
                    "trades": res.control.result.manifest["n_trades"],
                    "expectancy_R": res.control.metrics["overall"].get("expectancy_R"),
                },
                "variant": {
                    "trades": res.variant.result.manifest["n_trades"],
                    "expectancy_R": res.variant.metrics["overall"].get("expectancy_R"),
                },
                "removed": res.removed.get("removed_by_rule"),
            }
        )
    )


@app.command("phase3-freeze")
def phase3_freeze(
    config: ConfigOpt = None,
    raw_manifest: Path = Path("manifests/raw_archive_manifest.jsonl"),
    phase24_summary: Path = Path("data/btc/runs/phase24_short_regime/summary.json"),
    ingest_stats: Path | None = None,
    out: Path = Path("manifests/phase3_freeze_manifest.json"),
) -> None:
    """Phase 3 step 1: freeze configs, hashes, code commit, dataset hashes, holdout dates and the
    pre-declared criteria BEFORE any holdout evaluation. Commit the output before `phase3`."""
    from btc_swing.research.phase2 import load_inputs
    from btc_swing.research.phase3 import HOLDOUT_END, HOLDOUT_START, build_freeze

    cfg = _cfg(config, None)
    d = _data_dir()
    inp = load_inputs(cfg, d, _ms(HOLDOUT_START), _ms(HOLDOUT_END))
    stats = json.loads(ingest_stats.read_text()) if ingest_stats and ingest_stats.exists() else None
    fz = build_freeze(cfg, inp, raw_manifest, d / "btc" / "runs", phase24_summary, stats)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fz, indent=1, sort_keys=True, default=str))
    console.print_json(
        json.dumps(
            {
                "freeze": str(out),
                "code_commit": fz["code_commit"],
                "control_config_hash": fz["arms"]["CONTROL"]["config_hash"],
                "variant_config_hash": fz["arms"]["APPROVED_VARIANT"]["config_hash"],
                "holdout": fz["holdout"],
                "raw_archive_before_phase3": fz["raw_archive_before_phase3"],
            }
        )
    )


@app.command("phase3")
def phase3(
    config: ConfigOpt = None,
    null_k: int = 20,
    seed: int = 7,
    freeze: Path = Path("manifests/phase3_freeze_manifest.json"),
    out: Path | None = None,
    report: Path = Path("reports/BTC_SWING_V1_PHASE3_UNTOUCHED_VALIDATION.md"),
) -> None:
    """Phase 3 step 2: ONE confirmatory run per arm (CONTROL vs APPROVED_VARIANT) on the untouched
    2025+ holdout frozen in the freeze manifest; 19-section report with one classification."""
    from btc_swing.research.phase2 import load_inputs
    from btc_swing.research.phase3 import HOLDOUT_END, HOLDOUT_START, run_phase3
    from btc_swing.research.phase3_report import render_phase3

    if not freeze.exists():
        raise typer.BadParameter(f"freeze manifest {freeze} missing; run `phase3-freeze` first")
    fz = json.loads(freeze.read_text())
    cfg = _cfg(config, None)
    d = _data_dir()
    inp = load_inputs(cfg, d, _ms(HOLDOUT_START), _ms(HOLDOUT_END))
    run_dir = out or (d / "btc" / "runs" / f"phase3_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    res = run_phase3(cfg, inp, fz, null_k, seed, run_dir)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_phase3(res))
    console.print_json(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "report": str(report),
                "classification": res.classification,
                "criteria_met": [c["id"] for c in res.criteria if c["met"]],
                "control": {
                    "trades": res.control.result.manifest["n_trades"],
                    "expectancy_R": res.control.metrics["overall"].get("expectancy_R"),
                },
                "variant": {
                    "trades": res.variant.result.manifest["n_trades"],
                    "expectancy_R": res.variant.metrics["overall"].get("expectancy_R"),
                },
                "blocked": res.removed.get("removed_by_rule"),
            }
        )
    )


v2_app = typer.Typer(help="V2: cost-aware learned opportunity ranking (offline research only)")
app.add_typer(v2_app, name="v2")


@v2_app.command("research")
def v2_research(
    config: ConfigOpt = None,
    v2_config: Path = Path("config/btc_swing_v2.default.yaml"),
    out: Path | None = None,
    report: Path = Path("reports/BTC_SWING_V2_RANKING_RESEARCH.md"),
) -> None:
    """V2 offline research pipeline: candidates -> labels -> features -> walk-forward models ->
    evaluation -> 21-section report with one classification. No trading engine."""
    from btc_swing.research.phase2 import load_inputs
    from btc_swing.v2.config import load_v2_config
    from btc_swing.v2.report import render_v2
    from btc_swing.v2.research import run_v2_research

    cfg2 = load_v2_config(v2_config)
    cfg1 = _cfg(config or Path(cfg2.v1_config_path), None)
    d = _data_dir()
    inp = load_inputs(cfg1, d, _ms(cfg2.candidates.start), _ms(cfg2.candidates.end_exclusive))
    run_dir = out or (d / "btc" / "runs" / f"v2_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    res = run_v2_research(cfg2, cfg1, inp, run_dir)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_v2(res))
    console.print_json(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "report": str(report),
                "classification": res.classification,
                "criteria_met": [c["id"] for c in res.criteria if c["met"]],
                "candidates": res.population.get("n_candidates"),
                "evaluable": res.population.get("n_evaluable_walk_forward"),
                "spearman_M1": res.rank["M1_logistic"].get("spearman"),
                "gate_passed": res.gate.get("passed"),
            }
        )
    )


v3_app = typer.Typer(help="V3: active multi-timeframe swing (deterministic research only)")
app.add_typer(v3_app, name="v3")


@v3_app.command("research")
def v3_research(
    v3_config: Path = Path("config/btc_swing_v3.yaml"),
    out: Path | None = None,
    report: Path = Path("reports/BTC_SWING_V3_ACTIVE_SWING_RESEARCH.md"),
) -> None:
    """V3 research: one pre-registered configuration run once (raw 0.25% risk) plus reporting-only
    streams; 26-section report with one classification. No trading engine."""
    from btc_swing.v3.config import load_v3_config
    from btc_swing.v3.report import render_v3
    from btc_swing.v3.research import load_v3_inputs, run_v3_research

    cfg = load_v3_config(v3_config)
    d = _data_dir()
    inp = load_v3_inputs(cfg, d)
    run_dir = out or (d / "btc" / "runs" / f"v3_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    res = run_v3_research(cfg, inp, run_dir)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_v3(res))
    console.print_json(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "report": str(report),
                "classification": res.classification,
                "criteria_met": [c["id"] for c in res.criteria if c["met"]],
                "trades": res.overall.get("n"),
                "expectancy_R": res.overall.get("mean_R"),
                "trades_per_day": res.freq.get("trades_per_day"),
                "median_hold_h": res.hold.get("quantiles", {}).get(0.5),
            }
        )
    )


v4_app = typer.Typer(
    help="V4: event & positioning driven active swing (deterministic research only)"
)
app.add_typer(v4_app, name="v4")


@v4_app.command("research")
def v4_research(
    v4_config: Path = Path("config/btc_swing_v4.yaml"),
    out: Path | None = None,
    report: Path = Path("reports/BTC_SWING_V4_EVENT_POSITIONING_RESEARCH.md"),
) -> None:
    """V4 two-stage research: Stage A event forward returns, Stage B one frozen run plus
    reporting-only streams; 32-section report with one classification. No trading engine."""
    from btc_swing.v4.config import load_v4_config
    from btc_swing.v4.report import render_v4
    from btc_swing.v4.research import load_v4_inputs, run_v4_research

    cfg = load_v4_config(v4_config)
    d = _data_dir()
    inp = load_v4_inputs(cfg, d)
    run_dir = out or (d / "btc" / "runs" / f"v4_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    res = run_v4_research(cfg, inp, run_dir)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_v4(res))
    console.print_json(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "report": str(report),
                "classification": res.classification,
                "criteria_met": [c["id"] for c in res.criteria if c["met"]],
                "events": res.events.height,
                "trades": res.overall.get("n"),
                "gross_R": res.costs.get("expectancy_R_before_costs"),
                "net_R": res.overall.get("mean_R"),
                "trades_per_day": res.freq.get("trades_per_day"),
                "stage_a_gate": res.gate.get("passed"),
            }
        )
    )


v5_app = typer.Typer(
    help="V5: microstructure & liquidation driven active swing (deterministic research; "
    "forward PUBLIC-data collector; no trading)"
)
app.add_typer(v5_app, name="v5")


@v5_app.command("ingest")
def v5_ingest(
    start: str = "2021-12-01",
    end: str = "2026-09-30",
    datasets: str = "index_klines,book_depth,aggtrades_flow",
) -> None:
    """Ingest the V5 archive datasets (index klines, bookDepth, aggTrades -> 5m flow aggregates)
    from Binance Vision with checksum verification; append-only manifest."""
    from btc_swing.v5.ingest import run_v5_ingest

    stats = run_v5_ingest(_data_dir(), start, end, [d.strip() for d in datasets.split(",") if d])
    console.print_json(json.dumps(stats, default=str))


@v5_app.command("collect")
def v5_collect(
    duration: float = 600.0,
    reconnect_after: float | None = None,
    out: Path | None = None,
    v5_config: Path = Path("config/btc_swing_v5.yaml"),
) -> None:
    """Run the Bybit PUBLIC WebSocket collector for a bounded time (no authentication, no orders):
    immutable raw events, dedupe, sequence-gap detection, reconnect, storage verification."""
    from btc_swing.v5.collector import run_collector_test
    from btc_swing.v5.config import load_v5_config

    cfg = load_v5_config(v5_config)
    d = _data_dir()
    stats = run_collector_test(cfg.collector, d, duration, reconnect_after)
    path = out or (
        d
        / "btc"
        / "forward"
        / "bybit"
        / f"collector_stats_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stats, indent=1, sort_keys=True))
    console.print_json(json.dumps({"stats_path": str(path), **stats}, default=str))


@v5_app.command("research")
def v5_research(
    v5_config: Path = Path("config/btc_swing_v5.yaml"),
    out: Path | None = None,
    report: Path = Path("reports/BTC_SWING_V5_MICROSTRUCTURE_RESEARCH.md"),
    collector_stats: Path | None = None,
) -> None:
    """V5 two-stage research: Stage A event forward returns and gate, Stage B one frozen run plus
    reporting-only streams; 36-section report with one classification. No trading engine."""
    from btc_swing.v5.config import load_v5_config
    from btc_swing.v5.report import render_v5
    from btc_swing.v5.research import load_v5_inputs, run_v5_research

    cfg = load_v5_config(v5_config)
    d = _data_dir()
    inp, extra = load_v5_inputs(cfg, d)
    run_dir = out or (d / "btc" / "runs" / f"v5_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    cstats = json.loads(collector_stats.read_text()) if collector_stats else None
    res = run_v5_research(cfg, inp, extra, run_dir, cstats)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_v5(res))
    console.print_json(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "report": str(report),
                "classification": res.classification,
                "criteria_met": [c["id"] for c in res.criteria if c["met"]],
                "events": res.events.height,
                "trades": res.overall.get("n"),
                "gross_R": res.costs.get("expectancy_R_before_costs"),
                "net_R": res.overall.get("mean_R"),
                "trades_per_day": res.freq.get("trades_per_day"),
                "stage_a_gate": res.gate.get("passed"),
            }
        )
    )


forward_app = typer.Typer(
    help="V5 FORWARD OBSERVATION MODE: frozen V5 signals recorded prospectively on live Bybit "
    "public data; virtual paper ledger; no orders, no credentials"
)
v5_app.add_typer(forward_app, name="forward")


def _forward_ctx(forward_config: Path) -> Any:
    from btc_swing.v5.forward.config import ForwardPaths, load_forward_config, load_frozen_v5
    from btc_swing.v5.forward.freeze import check_freeze
    from btc_swing.v5.forward.pipeline import ForwardContext

    fcfg = load_forward_config(forward_config)
    cfg = load_frozen_v5(fcfg)
    rec = check_freeze(cfg)
    return ForwardContext(
        fcfg, cfg, ForwardPaths(_data_dir(), fcfg.symbol), int(rec["observation_start_ms"])
    )


@forward_app.command("freeze")
def forward_freeze(forward_config: Path = Path("config/btc_swing_v5_forward.yaml")) -> None:
    """Record the frozen V5 config hash, forward config hash, code commit and the observation
    start timestamp (manifests/v5_forward_freeze.json). Idempotent; refuses a changed V5 hash."""
    from btc_swing.v5.forward.config import load_forward_config, load_frozen_v5
    from btc_swing.v5.forward.freeze import write_freeze

    fcfg = load_forward_config(forward_config)
    rec = write_freeze(load_frozen_v5(fcfg), fcfg)
    console.print_json(json.dumps({k: v for k, v in rec.items() if not k.endswith("_yaml")}))


@forward_app.command("seed")
def forward_seed(forward_config: Path = Path("config/btc_swing_v5_forward.yaml")) -> None:
    """Warm-up bars from the Bybit PUBLIC trading archive for the days before the observation
    start (never on or after the collector's first day)."""
    from btc_swing.v5.forward.pipeline import extend_seed
    from btc_swing.v5.forward.seed import seed_summary

    ctx = _forward_ctx(forward_config)
    out = extend_seed(ctx, int(datetime.now(UTC).timestamp() * 1000))
    console.print_json(
        json.dumps({"ingest": out, "seed": seed_summary(ctx.paths.seed)}, default=str)
    )


@forward_app.command("cycle")
def forward_cycle(forward_config: Path = Path("config/btc_swing_v5_forward.yaml")) -> None:
    """One evaluation cycle (raw -> bars -> frozen signals -> paper ledger -> outcomes)."""
    from btc_swing.v5.forward.pipeline import run_cycle

    out = run_cycle(_forward_ctx(forward_config))
    console.print_json(
        json.dumps(
            {k: v for k, v in out.items() if k != "paper"}
            | {"paper": {k: v for k, v in out["paper"].items() if k != "open_position"}},
            default=str,
        )
    )


@forward_app.command("run")
def forward_run(
    forward_config: Path = Path("config/btc_swing_v5_forward.yaml"),
    duration: float | None = None,
    reports_dir: Path = Path("reports/forward"),
) -> None:
    """Run the collector and the 5-minute cycle continuously (or for --duration seconds); the
    previous UTC day's immutable report is written at 00:05 UTC. Ctrl-C / SIGTERM stops cleanly."""
    from btc_swing.v5.forward.runner import run_forward

    out = run_forward(_forward_ctx(forward_config), reports_dir, duration)
    console.print_json(json.dumps(out, default=str))


@forward_app.command("status")
def forward_status(forward_config: Path = Path("config/btc_swing_v5_forward.yaml")) -> None:
    """Live status: collector, last message, open paper position, signals/trades today, cumulative result."""
    from btc_swing.v5.forward.report import status

    console.print_json(json.dumps(status(_forward_ctx(forward_config)), default=str))


@forward_app.command("report")
def forward_report(
    day: str | None = None,
    forward_config: Path = Path("config/btc_swing_v5_forward.yaml"),
    reports_dir: Path = Path("reports/forward"),
) -> None:
    """Write the immutable daily snapshot for DAY (default: today, labelled PARTIAL while the day runs)."""
    from btc_swing.v5.forward.report import write_day_report

    d = day or datetime.now(UTC).strftime("%Y-%m-%d")
    p = write_day_report(_forward_ctx(forward_config), d, reports_dir)
    console.print_json(
        json.dumps(
            {
                "day": d,
                "written": str(p) if p else None,
                "note": None if p else "already exists (immutable)",
            }
        )
    )


@forward_app.command("health")
def forward_health(
    forward_config: Path = Path("config/btc_swing_v5_forward.yaml"),
    stale_seconds: float = 120.0,
    bar_stale_seconds: float = 900.0,
    min_free_gb: float = 5.0,
) -> None:
    """Monitoring check (exit 1 on a problem): runner alive, collector heartbeat, stale data,
    bar lag, disk. Run it from a systemd timer or cron."""
    from btc_swing.v5.forward.ops import health

    out = health(
        _forward_ctx(forward_config), _data_dir(), stale_seconds, bar_stale_seconds, min_free_gb
    )
    console.print_json(json.dumps(out, default=str))
    if not out["ok"]:
        raise typer.Exit(code=1)


@forward_app.command("status-text")
def forward_status_text(forward_config: Path = Path("config/btc_swing_v5_forward.yaml")) -> None:
    """One-screen plain-text status (runner, collector, last message/bar, latency, gaps,
    duplicates, signals, paper ledger, disk, observation start, frozen hash)."""
    from btc_swing.v5.forward.ops import status_text

    typer.echo(status_text(_forward_ctx(forward_config), _data_dir()))


@forward_app.command("integrity")
def forward_integrity(
    out: Path | None = None,
    compare: Path | None = None,
    forward_config: Path = Path("config/btc_swing_v5_forward.yaml"),
) -> None:
    """Integrity snapshot of every stateful artefact (counts, uniqueness, hashes). With
    --compare BEFORE.json, check that the current state is a faithful continuation."""
    from btc_swing.v5.forward.ops import integrity_compare, integrity_snapshot

    ctx = _forward_ctx(forward_config)
    snap = integrity_snapshot(ctx)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snap, indent=1, sort_keys=True, default=str))
    if compare:
        res = integrity_compare(json.loads(compare.read_text()), snap, ctx)
        console.print_json(json.dumps(res, default=str))
        if not res["ok"]:
            raise typer.Exit(code=1)
    else:
        console.print_json(
            json.dumps(
                {k: v for k, v in snap.items() if k not in ("raw", "seed", "bars")}
                | {
                    "bars": {k: v for k, v in snap["bars"].items() if k != "partition_sha256"},
                    "raw_files": snap["raw"]["files"],
                    "seed_days": snap["seed"]["days"],
                    "written": str(out) if out else None,
                },
                default=str,
            )
        )


@forward_app.command("export")
def forward_export(
    out: Path = Path("data/btc/forward_export/v5_forward_state.tar.gz"),
    forward_config: Path = Path("config/btc_swing_v5_forward.yaml"),
    include_seed_raw: bool = False,
) -> None:
    """Cold migration export: tar.gz of raw data, collector state, seed, derived bars, processor
    offsets, signal/outcome journals, paper ledger, logs, freeze manifest and configs, plus a
    sha256 manifest. Stop the runner first."""
    from btc_swing.v5.forward.freeze import FREEZE_PATH
    from btc_swing.v5.forward.ops import export_state

    ctx = _forward_ctx(forward_config)
    man = export_state(
        ctx, out, FREEZE_PATH, [forward_config, Path(ctx.fcfg.v5_config)], include_seed_raw
    )
    console.print_json(
        json.dumps(
            {k: v for k, v in man.items() if k not in ("files", "integrity")}
            | {
                "integrity_bars": man["integrity"]["bars"]["rows"],
                "integrity_signals": man["integrity"]["signals"]["lines"],
            },
            default=str,
        )
    )


@forward_app.command("verify")
def forward_verify(
    manifest: Path = Path("data/btc/forward_export/v5_forward_state.manifest.json"),
    forward_config: Path = Path("config/btc_swing_v5_forward.yaml"),
) -> None:
    """On the target host after extraction: re-hash every migrated file against the manifest and
    check the freeze hash. Exit 1 on any mismatch."""
    from btc_swing.v5.forward.freeze import FREEZE_PATH
    from btc_swing.v5.forward.ops import verify_state

    res = verify_state(_data_dir(), manifest, FREEZE_PATH, forward_config.parent)
    console.print_json(json.dumps(res, default=str))
    if not res["ok"]:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
