"""scripts/binance_carry_inputs.py: the ingest plan is exactly funding + mark 5m + base 5m klines with the
frozen V1 config untouched, the holdout month is refused, and the validator reports counts, gaps,
duplicates, nulls and the carry loader's own inventory (VALID / INVALID)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import polars as pl

from tests.intraday_portfolio.test_carry import T0, make_data

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "binance_carry_inputs", ROOT / "scripts" / "binance_carry_inputs.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["binance_carry_inputs"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_plan_is_minimal_and_frozen_config_untouched() -> None:
    from btc_swing.core.config import load_btc_config
    from btc_swing.ingest.pipeline import BtcIngestor
    from btc_swing.providers.synthetic import SyntheticBtcProvider
    from btc_swing.storage.archive_store import ArchiveStore
    from btc_swing.storage.bar_store import BarStore

    m = _load()
    before = (ROOT / "config" / "btc_swing.default.yaml").read_bytes()
    cfg = load_btc_config(None, m.OVERRIDES)
    plan = BtcIngestor(
        SyntheticBtcProvider("2024-01", "2024-01"),
        ArchiveStore(Path("/tmp/x")),
        BarStore(Path("/tmp/x")),
        cfg,
    ).plan()
    assert [(d.value, tf.value if tf else "na") for d, _s, tf in plan] == [
        ("perp_klines", "5m"),
        ("funding", "na"),
        ("mark_price", "5m"),
    ]
    assert (ROOT / "config" / "btc_swing.default.yaml").read_bytes() == before
    assert m.HOLDOUT_FIRST_MONTH == "2025-07" and m.DEFAULT_END == "2025-06"


def test_validate_reports_and_fails_closed(tmp_path: Path) -> None:
    m = _load()
    rep = m.validate(tmp_path / "none")
    assert rep["verdict"] == "INVALID" and rep["funding"]["status"] == "MISSING"
    make_data(tmp_path, n_days=40, bybit_rates={k: 0.001 for k in range(120)})
    rep = m.validate(tmp_path)
    f, mk = rep["funding"], rep["mark_price"]
    assert f["events"] == 120 and f["duplicate_timestamps"] == 0 and f["null_rates"] == 0
    assert (
        f["settlement_interval_hours_distribution"] == {8: 119}
        and f["timestamps_strictly_increasing_in_file_order"]
    )
    assert (
        mk["rows"] == 40 * 288
        and mk["all_on_5m_utc_grid"]
        and mk["gaps_gt_5m"] == 0
        and mk["duplicate_timestamps"] == 0
    )
    assert rep["perp_klines"]["status"] == "MISSING" and rep["carry_loader"]["missing"] == []
    assert rep["verdict"] == "VALID"
    # a removed 5m bar and a duplicated settlement are reported and fail the validation
    p = tmp_path / "btc" / "datasets" / "mark_price" / "BTCUSDT" / "5m" / "2024-01.parquet"
    df = pl.read_parquet(p)
    df.filter(pl.col("open_time_ms") != T0 + 100 * 300_000).write_parquet(p)
    q = tmp_path / "btc" / "datasets" / "funding" / "BTCUSDT" / "na" / "2024-01.parquet"
    fd = pl.read_parquet(q)
    pl.concat([fd, fd.head(1)]).write_parquet(q)
    rep2 = m.validate(tmp_path)
    assert rep2["mark_price"]["gaps_gt_5m"] == 1 and rep2["mark_price"]["missing_bars_implied"] == 1
    assert rep2["funding"]["duplicate_timestamps"] == 1 and rep2["verdict"] == "INVALID"
    assert np.isfinite(rep2["mark_price"]["max_gap_minutes"])
