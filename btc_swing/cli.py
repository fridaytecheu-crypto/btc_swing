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


if __name__ == "__main__":
    app()
