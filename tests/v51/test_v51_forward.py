"""V5.1 forward pipeline: seeds merge strictly before the V5 start, no retrospective signals, own
state only (V5 journals untouched), restart/recovery without duplicates, freeze manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from btc_swing.v5.forward.config import load_forward_config, load_frozen_v5
from btc_swing.v5.forward.freeze import write_freeze
from btc_swing.v5.forward.pipeline import run_cycle
from btc_swing.v51 import forward as fw
from btc_swing.v51 import history as h
from btc_swing.v51.config import EXPECTED_V51_CONFIG_HASH, PARENT_V5_CONFIG_HASH
from btc_swing.v51.freeze import check_v51_freeze, load_v51_freeze, write_v51_freeze
from tests.v51.fake_bybit_history import FakeBybitHistory, oi_at
from tests.v51.helpers import ROOT, START, synthetic_bars, v5_ctx, v51_cfg

MS_5M = 300_000
DAY = 86_400_000
N = 4000


def _fingerprint(root: Path) -> str:
    hh = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            hh.update(f"{p.relative_to(root)}|{p.stat().st_size}".encode())
            hh.update(p.read_bytes())
    return hh.hexdigest()


def _ctx51(tmp: Path, bars: pl.DataFrame, v5_start: int, v51_start: int) -> fw.V51Context:
    ctx5 = v5_ctx(tmp, bars, v5_start)
    return fw.V51Context(ctx5.fcfg, v51_cfg(), fw.V51Paths(tmp, "BTCUSDT"), v51_start, v5_start)  # type: ignore[arg-type]


def _seed_oi(ctx: fw.V51Context, first_ms: int, last_ms: int) -> pl.DataFrame:
    fake = FakeBybitHistory(first_ms, last_ms)
    cl = h.BybitPublicHistory(transport=fake.transport(), sleep=lambda s: None)
    store = h.SeedStore(ctx.paths.seed_dir("oi"), "oi")
    return h.fetch_oi_seed(cl, store, "BTCUSDT", first_ms, last_ms, "5min")


def _archive_like_before(bars: pl.DataFrame, v5_start: int) -> pl.DataFrame:
    """Rows before the V5 start look like archive seed bars: trades only, no ticker fields."""
    pre = pl.col("close_time_ms") <= v5_start
    cols = [
        "oi_last",
        "oi_value_last",
        "mark_open",
        "mark_high",
        "mark_low",
        "mark_close",
        "index_open",
        "index_high",
        "index_low",
        "index_close",
        "next_funding_ms",
        "funding_rate_last",
    ]
    return bars.with_columns(
        [pl.when(pre).then(float("nan")).otherwise(pl.col(c)).alias(c) for c in cols]
    )


def test_seed_oi_enters_only_before_the_v5_start_and_warms_the_feature(tmp_path: Path) -> None:
    v5_start = START + 3000 * MS_5M  # live stream starts late: earlier rows are archive-like
    bars = _archive_like_before(synthetic_bars(N), v5_start)
    ctx = _ctx51(tmp_path, bars, v5_start, v5_start + 100 * MS_5M)
    # the seed covers more than needed on both sides: rows at/after the V5 start must be ignored
    tbl = _seed_oi(ctx, START - 40 * DAY, v5_start + DAY)
    assert int(tbl["time_ms"].max()) > v5_start
    a51 = fw.assemble_v51(ctx)
    m = a51.base.metrics
    assert m is not None
    seed_rows = m.filter(pl.col("time_ms") < v5_start)
    live_rows = m.filter(pl.col("time_ms") >= v5_start)
    assert seed_rows.height > 0 and int(seed_rows["time_ms"].max()) < v5_start
    # live OI rows come from the collector only (never the seed): values differ from the seed series
    assert live_rows.height > 0
    assert not np.allclose(
        live_rows["open_interest"].to_numpy()[:5],
        [oi_at(int(t)) for t in live_rows["time_ms"][:5]],
    )
    b = fw.build_v51(ctx, a51)
    k = b.ff.idx_at(b.last_close_ms)
    assert not np.isnan(b.ff.cols["oi_chg_1h_z"][k])  # warm thanks to the seed
    # without the seed the same frame is not warm (only ~1000 live OI observations)
    ctx_noseed = _ctx51(tmp_path / "noseed", bars, v5_start, v5_start + 100 * MS_5M)
    b2 = fw.build_v51(ctx_noseed, fw.assemble_v51(ctx_noseed))
    assert np.isnan(b2.ff.cols["oi_chg_1h_z"][k])
    assert a51.seeds["oi"]["rows"] == seed_rows.height


def test_cycle_writes_only_v51_state_and_never_signals_before_its_start(tmp_path: Path) -> None:
    bars = synthetic_bars(N, outages=[(2500, 2530)])
    v5_start = START + 1000 * MS_5M
    v51_start = START + 3500 * MS_5M
    ctx = _ctx51(tmp_path, bars, v5_start, v51_start)
    _seed_oi(ctx, START - 35 * DAY, v5_start)
    v5_root = ctx.paths.v5.root
    before_v5 = _fingerprint(v5_root)
    out = fw.run_cycle_v51(ctx, now_ms=int(bars["close_time_ms"][-1]) + 30_000)
    assert out["version"] == "V5.1" and out["bars_forward"] == N
    assert _fingerprint(v5_root) == before_v5  # V5 signals/paper/journals untouched
    sigs = (
        [json.loads(x) for x in ctx.paths.signals_file.read_text().splitlines()]
        if ctx.paths.signals_file.exists()
        else []
    )
    assert all(int(s["t_ms"]) > v51_start for s in sigs)  # nothing retrospective
    assert all(s["frozen_v5_config_hash"] == EXPECTED_V51_CONFIG_HASH for s in sigs)
    paper = json.loads(ctx.paths.paper_state.read_text())
    assert paper["initial_equity"] == 10000.0
    # restart/recovery: a second cycle on the same data duplicates nothing
    n_sig, n_cyc = len(sigs), len(ctx.paths.cycle_log.read_text().splitlines())
    fw.run_cycle_v51(ctx, now_ms=int(bars["close_time_ms"][-1]) + 30_000)
    sigs2 = (
        ctx.paths.signals_file.read_text().splitlines() if ctx.paths.signals_file.exists() else []
    )
    assert len(sigs2) == n_sig and len(ctx.paths.cycle_log.read_text().splitlines()) == n_cyc + 1
    ids = [json.loads(x)["signal_id"] for x in sigs2]
    assert len(ids) == len(set(ids))
    assert out["validity"]["current_1h_window_clean"] and out["validity"]["clean_1h_bars"] > 0


def test_v5_cycle_and_v51_cycle_share_bars_but_keep_separate_journals(tmp_path: Path) -> None:
    bars = synthetic_bars(N)
    v5_start = START + 1000 * MS_5M
    ctx5 = v5_ctx(tmp_path, bars, v5_start)
    out5 = run_cycle(ctx5, now_ms=int(bars["close_time_ms"][-1]) + 30_000, extend=False)
    ctx51 = fw.V51Context(
        ctx5.fcfg, v51_cfg(), fw.V51Paths(tmp_path, "BTCUSDT"), v5_start, v5_start
    )  # type: ignore[arg-type]
    out51 = fw.run_cycle_v51(ctx51, now_ms=int(bars["close_time_ms"][-1]) + 30_000)
    assert out5["bars_forward"] == out51["bars_forward"]
    assert ctx51.paths.signals_file != ctx5.paths.signals_file
    assert ctx51.paths.bars_dir == ctx5.paths.bars_dir
    # identical start and clean data: the same events (V5.1 == V5 on clean data), different journals
    s5 = (
        {json.loads(x)["signal_id"] for x in ctx5.paths.signals_file.read_text().splitlines()}
        if ctx5.paths.signals_file.exists()
        else set()
    )
    s51 = (
        {json.loads(x)["signal_id"] for x in ctx51.paths.signals_file.read_text().splitlines()}
        if ctx51.paths.signals_file.exists()
        else set()
    )
    assert s5 == s51


def test_v51_freeze_is_separate_idempotent_and_bound_to_the_v5_parent(tmp_path: Path) -> None:
    fcfg = load_forward_config(ROOT / "config" / "btc_swing_v5_forward.yaml")
    v5 = load_frozen_v5(fcfg, ROOT)
    cfg = v51_cfg()
    parent = write_freeze(v5, fcfg, tmp_path / "v5_forward_freeze.json")
    p = tmp_path / "v5_1_forward_freeze.json"
    rec = write_v51_freeze(cfg, v5, fcfg, parent, p)  # type: ignore[arg-type]
    assert rec["v51_config_hash"] == EXPECTED_V51_CONFIG_HASH
    assert rec["parent_v5_config_hash"] == PARENT_V5_CONFIG_HASH
    assert rec["observation_start_ms"] >= parent["observation_start_ms"]
    assert (
        rec["rule_versions"]["events"] == "v5-event-1"
        and rec["rule_versions"]["exits"] == "v5-exit-1"
    )
    assert write_v51_freeze(cfg, v5, fcfg, parent, p) == rec  # idempotent
    assert check_v51_freeze(cfg, p)["observation_start"] == rec["observation_start"]  # type: ignore[arg-type]
    assert load_v51_freeze(tmp_path / "missing.json") is None
    with pytest.raises(RuntimeError, match="parent"):
        write_v51_freeze(cfg, v5, fcfg, {"v5_config_hash": "x"}, tmp_path / "other.json")  # type: ignore[arg-type]
    # the V5 freeze file is untouched by the V5.1 freeze
    assert json.loads((tmp_path / "v5_forward_freeze.json").read_text()) == parent
