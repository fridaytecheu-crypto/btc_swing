"""Bybit public history seeds: pagination, immutable raw pages with metadata, PIT split (warm-up
strictly before the V5 observation start), alignment/unit verification against live rows, retries."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from btc_swing.v51 import history as h
from tests.v51.fake_bybit_history import (
    FakeBybitHistory,
    funding_at,
    live_bars_matching,
    mark_at,
    oi_at,
)

MS_5M = 300_000
DAY = 86_400_000
T0 = 1_788_000_000_000 // MS_5M * MS_5M  # a 5m boundary
V5_START = T0 + 40 * DAY + 7 * 3_600_000 + 123  # the V5 observation start (not on a boundary)


def _client(fake: FakeBybitHistory) -> h.BybitPublicHistory:
    return h.BybitPublicHistory(transport=fake.transport(), sleep=lambda s: None)


def test_open_interest_pagination_metadata_and_immutability(tmp_path: Path) -> None:
    fake = FakeBybitHistory(T0, T0 + 45 * DAY)
    store = h.SeedStore(tmp_path / "oi", "oi")
    start = V5_START - 30 * DAY
    tbl = h.fetch_oi_seed(_client(fake), store, "BTCUSDT", start, V5_START + 2 * DAY, "5min")
    n_expected = len(
        [t for t in range(T0, T0 + 45 * DAY + 1, MS_5M) if start <= t <= V5_START + 2 * DAY]
    )
    assert tbl.height == n_expected and tbl["time_ms"].is_sorted()
    assert tbl["time_ms"].n_unique() == tbl.height
    assert len([r for r in fake.requests if r["path"] == "/v5/market/open-interest"]) == -(
        -n_expected // 200
    )
    assert set(tbl.columns) == {"time_ms", "open_interest", "source", "retrieved_at", "page_sha256"}
    assert tbl["source"].unique().to_list() == ["bybit_rest_open-interest_5min"]
    # every row's page exists, is immutable and hashes to the recorded sha
    pages = {
        json.loads(ln)["sha256"]: json.loads(ln) for ln in store.manifest.read_text().splitlines()
    }
    assert set(tbl["page_sha256"].unique().to_list()) <= set(pages)
    for sha, m in pages.items():
        p = store.raw / m["file"]
        d = json.loads(p.read_text())
        assert d["sha256"] == sha and d["params"]["intervalTime"] == "5min" and d["retrieved_at"]
    before = {p.name: p.read_bytes() for p in store.raw.iterdir()}
    # a second fetch of the same window changes no stored page and no stored row
    tbl2 = h.fetch_oi_seed(_client(fake), store, "BTCUSDT", start, V5_START + 2 * DAY, "5min")
    assert tbl2.height == tbl.height
    assert all(p.read_bytes() == before[p.name] for p in store.raw.iterdir() if p.name in before)
    assert tbl2["retrieved_at"].to_list() == tbl["retrieved_at"].to_list()  # first retrieval kept


def test_pit_split_keeps_warmup_strictly_before_the_v5_start(tmp_path: Path) -> None:
    fake = FakeBybitHistory(T0, T0 + 45 * DAY)
    store = h.SeedStore(tmp_path / "oi", "oi")
    tbl = h.fetch_oi_seed(
        _client(fake), store, "BTCUSDT", V5_START - 3 * DAY, V5_START + DAY, "5min"
    )
    warm, verif = h.split_warmup(tbl, "time_ms", V5_START)
    assert int(warm["time_ms"].max()) < V5_START <= int(verif["time_ms"].min())
    assert warm.height + verif.height == tbl.height
    assert warm.height == len(
        [t for t in range(T0, T0 + 45 * DAY + 1, MS_5M) if V5_START - 3 * DAY <= t < V5_START]
    )


def test_oi_alignment_units_and_timestamp_semantics(tmp_path: Path) -> None:
    fake = FakeBybitHistory(T0, T0 + 45 * DAY)
    store = h.SeedStore(tmp_path / "oi", "oi")
    tbl = h.fetch_oi_seed(_client(fake), store, "BTCUSDT", V5_START - DAY, V5_START + DAY, "5min")
    live_start = (V5_START // MS_5M + 1) * MS_5M
    live = pl.DataFrame(live_bars_matching(live_start, 200))
    _, verif = h.split_warmup(tbl, "time_ms", V5_START)
    r = h.verify_oi_alignment(verif, live, 0.005)
    assert r["ok"] and r["n_overlap"] >= 150
    assert r["rel_diff_vs_bar_closing_at_timestamp"]["median"] < 2e-5
    assert (
        r["rel_diff_vs_bar_opening_at_timestamp (control)"]["median"]
        > r["rel_diff_vs_bar_closing_at_timestamp"]["median"]
    )
    assert 0.999 < r["unit_ratio_seed_over_live"] < 1.001
    # a unit mismatch (e.g. contracts vs BTC, or USD value) is detected
    bad = live.with_columns(pl.col("oi_last") * 1000.0)
    assert not h.verify_oi_alignment(verif, bad, 0.005)["ok"]
    # outage rows never take part in the comparison
    gappy = live.with_columns(pl.lit(0).alias("trades"), pl.lit(1.0).alias("gap_filled"))
    assert h.verify_oi_alignment(verif, gappy, 0.005)["n_overlap"] == 0


def test_premium_and_funding_seeds_match_the_live_definitions(tmp_path: Path) -> None:
    fake = FakeBybitHistory(T0, T0 + 45 * DAY)
    cl = _client(fake)
    ps = h.SeedStore(tmp_path / "premium", "premium")
    prem = h.fetch_premium_seed(cl, ps, "BTCUSDT", V5_START - 2 * DAY, V5_START + DAY, "5")
    assert {"open_time_ms", "close_time_ms", "mark_close", "index_close", "close", "source"} <= set(
        prem.columns
    )
    t = int(prem["open_time_ms"][0])
    row = prem.filter(pl.col("open_time_ms") == t).row(0, named=True)
    assert row["close"] == pytest.approx(mark_at(t) / (mark_at(t) * (1 - 2e-4)) - 1, abs=1e-6)
    assert row["close_time_ms"] == t + MS_5M
    live_start = (V5_START // MS_5M + 1) * MS_5M
    live = pl.DataFrame(live_bars_matching(live_start, 100))
    _, verif = h.split_warmup(prem, "close_time_ms", V5_START)
    r = h.verify_premium_alignment(verif, live, 0.0005)
    assert r["ok"] and r["n_overlap"] >= 90 and r["mark_rel_diff"]["max"] < 1e-9
    fs = h.SeedStore(tmp_path / "funding", "funding")
    fund = h.fetch_funding_seed(cl, fs, "BTCUSDT", V5_START - 20 * DAY, V5_START + DAY)
    assert fund.height >= 60 and fund["time_ms"].is_sorted()
    tf = int(fund["time_ms"][0])
    assert fund["funding_rate"][0] == pytest.approx(funding_at(tf))
    live_f = pl.DataFrame(
        {"time_ms": fund["time_ms"].tail(3), "funding_rate": fund["funding_rate"].tail(3)}
    )
    assert h.verify_funding_alignment(fund, live_f)["ok"]
    assert not h.verify_funding_alignment(fund, live_f.with_columns(pl.col("funding_rate") + 1e-5))[
        "ok"
    ]


def test_transport_retry_rate_limit_and_allowlist(tmp_path: Path) -> None:
    fake = FakeBybitHistory(T0, T0 + DAY, fail_next=2, rate_limit_next=1)
    cl = _client(fake)
    page = cl.get(
        "/v5/market/open-interest",
        {"category": "linear", "symbol": "BTCUSDT", "intervalTime": "5min", "limit": 5},
    )
    assert page.http_status == 200 and len(fake.requests) >= 4  # 2 failures + rate limit + ok
    with pytest.raises(h.HistoryError):
        cl.get("/v5/account/wallet-balance", {})
    with pytest.raises(h.HistoryError):
        h.BybitPublicHistory(base="http://api.bybit.com")
    assert oi_at(T0) > 0
