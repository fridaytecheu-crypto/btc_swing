"""End-to-end: synthetic parquet day files -> run() twice -> identical artefacts (determinism), journal /
report reconciliation, holdout day files never read, no exchange / runner imports in the package."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

import btc_swing.research.intraday_portfolio as pkg
from btc_swing.research.intraday_portfolio.contract import MS_1M
from btc_swing.research.intraday_portfolio.data import load_bybit_bars
from btc_swing.research.intraday_portfolio.primitives import Bars, bars_1m_to_5m
from btc_swing.research.intraday_portfolio.run import run
from tests.intraday_portfolio.synth import bars_from_ohlc, e1_long_path, e2_long_path

DAY_A = datetime(2024, 3, 1, tzinfo=UTC)
FORBIDDEN = re.compile(
    r"^\s*(from|import)\s+(btc_swing\.(v5|v51|v10|v11|v11_warm|v11_lite_warm|v11_1|v11_2)|"
    r"websocket|websockets|requests|httpx|aiohttp|pybit|ccxt|urllib)"
)


def _day_rows(
    path: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    last = path[-1][3]
    return path + [(last, last + 1, last - 1, last)] * (1440 - len(path))


def _write_day(root: Path, day: str, b: Bars, tf: str) -> None:
    d = root / "btc" / "datasets" / f"bybit_trades_{tf}" / "BTCUSDT" / tf
    d.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "open_time_ms": b.open_ms,
            "close_time_ms": b.close_ms,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
            "vwap": b.vwap,
            "buy_qty": b.buy,
            "sell_qty": b.sell,
        }
    ).write_parquet(d / f"{day}.parquet")


def _data_dir(root: Path) -> Path:
    for k, (day, path) in enumerate(
        (
            ("2024-03-01", e1_long_path()),
            ("2024-03-02", e2_long_path()),
            ("2025-07-01", e1_long_path()),
        )
    ):
        t0 = int((DAY_A.timestamp() + (k if k < 2 else 0) * 86400) * 1000)
        if day == "2025-07-01":
            t0 = int(datetime(2025, 7, 1, tzinfo=UTC).timestamp() * 1000)
        b1 = bars_from_ohlc(_day_rows(path), t0)
        _write_day(root, day, b1, "1m")
        _write_day(root, day, bars_1m_to_5m(b1), "5m")
    return root


def test_holdout_day_files_are_never_read(tmp_path: Path) -> None:
    dd = _data_dir(tmp_path / "data")
    b5, prov, days = load_bybit_bars(dd, "5m")
    assert days == ["2024-03-01", "2024-03-02"] and len(b5) == 2 * 288
    assert "2025-07-01" not in days and prov.availability == "AVAILABLE"
    assert int(b5.open_ms[-1]) < int(datetime(2025, 7, 1, tzinfo=UTC).timestamp() * 1000)


def test_run_is_deterministic_and_reconciles(tmp_path: Path) -> None:
    dd = _data_dir(tmp_path / "data")
    r1 = run(dd, tmp_path / "out1", run_id="t1")
    r2 = run(dd, tmp_path / "out2", run_id="t1")
    assert r1.verdicts == r2.verdicts
    assert set(r1.files) >= {
        "data_inventory.json", "experiment_manifest.json", "e1_parents.parquet", "e1_parents.csv",
        "e2_parents.parquet", "e2_parents.csv", "e4_parents.csv", "markouts_e1_e2_benchmark.csv",
        "markouts_e4.csv", "trade_replay.csv", "portfolio_replay.csv", "cost_analysis.csv",
        "frequency_analysis.csv", "regime_analysis.csv", "README.md", "SUMMARY.md",
    }  # fmt: skip
    for name in r1.files:
        a, b = (tmp_path / "out1" / "t1" / name), (tmp_path / "out2" / "t1" / name)
        if name.endswith(".json"):
            ja, jb = json.loads(a.read_text()), json.loads(b.read_text())
            ja.pop("generated_at_utc"), jb.pop("generated_at_utc")
            assert ja == jb, name
        else:
            assert a.read_bytes() == b.read_bytes(), name
    man = json.loads((tmp_path / "out1" / "t1" / "experiment_manifest.json").read_text())
    assert man["order_capable"] is False and man["research_only"] is True
    assert man["data"]["bybit_5m"]["days"] == 2 and man["data"]["bybit_5m"]["last"] == "2024-03-02"
    # journal / report reconciliation
    out = tmp_path / "out1" / "t1"
    e1 = pl.read_csv(out / "e1_parents.csv")
    e2 = pl.read_csv(out / "e2_parents.csv")
    tr = pl.read_csv(out / "trade_replay.csv")
    fn = man["results"]["funnels"]
    assert e1.height == fn["E1"]["parent_episodes"] and e2.height == fn["E2"]["parent_episodes"]
    assert tr.height == fn["E1"]["completed_trades"] + fn["E2"]["completed_trades"] >= 2
    assert tr.filter(pl.col("engine") == "E1").height == fn["E1"]["completed_trades"]
    assert e1.filter(pl.col("eligibility_state") == "CLOSED").height == fn["E1"]["completed_trades"]
    pf = pl.read_csv(out / "portfolio_replay.csv")
    both = pf.filter((pl.col("portfolio") == "E1+E2") & (pl.col("status") == "TRADED"))
    assert both.height == man["results"]["portfolios"]["E1+E2"]["executed"]
    assert pf.filter(pl.col("portfolio") == "E1+E2").height == e1.height + e2.height
    s16 = man["results"]["gates"]["E1"]["summary_16"]
    assert abs(s16["net_bps_mean"] - tr.filter(pl.col("engine") == "E1")["net_bps"].mean()) < 1e-9
    summary = (out / "SUMMARY.md").read_text()
    for k, v in r1.verdicts.items():
        assert f"| {k} | **{v['verdict']}** |" in summary
        assert v["verdict"] == "INCONCLUSIVE"  # tiny sample: never promoted
    assert man["results"]["e4"]["status"] == "NO_TRADES"
    inv = json.loads((out / "data_inventory.json").read_text())
    assert inv["inputs"]["historical_bbo_bid_ask"]["status"] == "MISSING"
    assert inv["consequences"]["E1"]["executable_replay"].startswith("UNAVAILABLE")
    # markouts are labelled benchmark with no receipt time; no same-close fill anywhere
    mk = pl.read_csv(out / "markouts_e1_e2_benchmark.csv")
    assert set(mk["ledger"].unique()) == {"BENCHMARK"} and set(mk["receipt_time"].unique()) == {
        "UNAVAILABLE"
    }
    assert (tr["t_benchmark_entry_time"] == tr["t_decision_time"]).all()
    assert ((tr["t_benchmark_entry_time"] % MS_1M) == 0).all()


def test_package_is_order_disabled_and_isolated() -> None:
    assert pkg.RESEARCH_ONLY is True and pkg.ORDER_CAPABLE is False
    here = Path(pkg.__file__).parent
    for f in sorted(here.glob("*.py")):
        text = f.read_text()
        for line in text.splitlines():
            if f.name == "spot.py" and line.strip() == "import httpx":
                continue  # the ONLY network use: the public, checksummed Binance Vision archive
            assert not FORBIDDEN.match(line), f"{f.name}: {line}"
            assert "place_order" not in line and "api_key" not in line.lower(), f"{f.name}: {line}"
        urls = set(re.findall(r"https?://[A-Za-z0-9./_-]+", text))
        assert urls <= {"https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/5m/"}, (
            f.name,
            urls,
        )
        assert "wss://" not in text and "stream.bybit" not in text and "api.bybit" not in text, (
            f.name
        )
    assert not np.any([("demo" in f.name) for f in here.glob("*.py")])
