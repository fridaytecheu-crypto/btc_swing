from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from btc_swing.core.config import BtcStrategyConfig, load_btc_config
from btc_swing.core.enums import Timeframe
from btc_swing.ingest.pipeline import BtcIngestor
from btc_swing.providers.base import Dataset
from btc_swing.providers.synthetic import SyntheticBtcProvider
from btc_swing.storage.archive_store import ArchiveStore
from btc_swing.storage.bar_store import BarStore

ROOT = Path(__file__).resolve().parents[1]
SYNTH_OVERRIDES = {
    "data": {
        "provider": "synthetic",
        "ingest_metrics": False,
        "ingest_premium_index": False,
        "ingest_mark_price": False,
    }
}


@pytest.fixture
def btc_cfg() -> BtcStrategyConfig:
    return load_btc_config(ROOT / "config" / "btc_swing.default.yaml", overrides=SYNTH_OVERRIDES)


@pytest.fixture(scope="session")
def synthetic_data(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    cfg = load_btc_config(ROOT / "config" / "btc_swing.default.yaml", overrides=SYNTH_OVERRIDES)
    d = tmp_path_factory.mktemp("btcdata")
    prov = SyntheticBtcProvider("2023-01", "2023-08")
    ing = BtcIngestor(prov, ArchiveStore(d), BarStore(d), cfg)
    stats = ing.ingest("2023-01", "2023-08")
    bs = BarStore(d)
    return {
        "dir": d,
        "provider": prov,
        "cfg": cfg,
        "stats": stats,
        "bars": bs.scan(Dataset.PERP_KLINES, "BTCUSDT", Timeframe.M5),
        "funding": bs.scan(Dataset.FUNDING, "BTCUSDT", None, time_col="time_ms"),
        "store": bs,
    }


@pytest.fixture
def bars_5m(synthetic_data: dict[str, object]) -> pl.DataFrame:
    b = synthetic_data["bars"]
    assert isinstance(b, pl.DataFrame)
    return b
