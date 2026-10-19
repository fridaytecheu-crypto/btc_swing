"""Carry screen accounting and timing on synthetic venues: funding signs incl. negative rates, the
pre-entry midnight excluded and the final midnight included (9 settlements per venue), four execution
costs and the one-leg denominator, basis identity (no double counting), signal window strictness and
threshold, exact 72h holding with non-overlapping renewal, exclusions on incomplete windows, B-roll
turnover, clusters, determinism, and the fail-closed refusal when inputs are missing."""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from btc_swing.research.intraday_portfolio import carry

T0 = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
MS_5M, MS_8H, MS_D = 300_000, 8 * 3_600_000, 86_400_000


def _write(root: Path, rel: str, df: pl.DataFrame, stem: str) -> None:
    d = root / "btc" / "datasets" / rel
    d.mkdir(parents=True, exist_ok=True)
    df.write_parquet(d / f"{stem}.parquet")


def make_data(
    root: Path,
    n_days: int = 40,
    bybit_rates: dict[int, float] | None = None,
    binance_rates: dict[int, float] | None = None,
    bybit_px: float = 50_000.0,
    binance_px: float = 50_000.0,
    bybit_drift_per_bar: float = 0.0,
    drop_bybit_mark_minute: int | None = None,
    drop_binance_settlement: int | None = None,
) -> None:
    """Flat marks (optional Bybit drift), zero funding except the given settlement overrides
    (keyed by settlement index k -> rate), 8h schedule on both venues from T0."""
    n_bars = n_days * 288
    t = (T0 + np.arange(n_bars, dtype=np.int64) * MS_5M).astype(np.int64)
    by = bybit_px + bybit_drift_per_bar * np.arange(n_bars)
    bn = np.full(n_bars, binance_px)
    for name, px in (("bybit_mark_5m/BTCUSDT/5m", by), ("mark_price/BTCUSDT/5m", bn)):
        keep = np.ones(n_bars, dtype=bool)
        if name.startswith("bybit") and drop_bybit_mark_minute is not None:
            keep[drop_bybit_mark_minute // 5] = False
        df = pl.DataFrame(
            {
                "open_time_ms": t[keep],
                "open": px[keep],
                "high": px[keep] + 1,
                "low": px[keep] - 1,
                "close": px[keep] + 0.5,
            }
        )
        _write(root, name, df, "2024-01")
    n_set = n_days * 3
    ts = (T0 + np.arange(n_set, dtype=np.int64) * MS_8H).astype(np.int64)
    for name, rates, drop in (
        ("bybit_funding/BTCUSDT/na", bybit_rates or {}, None),
        ("funding/BTCUSDT/na", binance_rates or {}, drop_binance_settlement),
    ):
        r = np.zeros(n_set)
        for k, v in rates.items():
            if k < n_set:
                r[k] = v
        keep = np.ones(n_set, dtype=bool)
        if drop is not None:
            keep[drop] = False
        _write(
            root,
            name,
            pl.DataFrame(
                {"time_ms": ts[keep], "funding_rate": r[keep], "funding_interval_hours": 8}
            ),
            "2024-01",
        )


def _set_idx(day: int, hour: int) -> int:
    return day * 3 + hour // 8


def test_signal_window_strictness_threshold_and_direction(tmp_path: Path) -> None:
    # day 5 decision at 00:05: window (day4 00:05, day5 00:05) holds day4 08:00, day4 16:00, day5 00:00
    make_data(
        tmp_path,
        bybit_rates={
            _set_idx(4, 0): 0.01,
            _set_idx(4, 8): 0.0012,
            _set_idx(5, 0): 0.0005,
            _set_idx(5, 8): 0.01,
        },
    )
    venues, inv = carry.load_venues(tmp_path)
    assert inv["missing"] == [] and inv["BYBIT"]["funding_interval_hours_observed"] == 8
    dec = carry.signal(date(2024, 1, 6), venues)
    assert dec.valid and math.isclose(
        dec.a_bybit or 0, 0.0017
    )  # day4 00:00 (0.01) and day5 08:00 excluded
    assert (
        math.isclose(dec.projected_72h_bps or 0, 1e4 * 3 * 0.0017)
        and dec.qualified
        and dec.direction == 1
    )
    dec0 = carry.signal(date(2024, 1, 3), venues)
    assert dec0.valid and dec0.d == 0 and not dec0.qualified and dec0.direction == 0
    # 0.00112788 per 8h on one venue -> 11.2788 bps -> projected 3 x (sum of three) ; a single 0.0016 -> 48 bps < 50
    make_data(tmp_path / "b", bybit_rates={_set_idx(8, 0): 0.0016})
    v2, _ = carry.load_venues(tmp_path / "b")
    d2 = carry.signal(date(2024, 1, 9), v2)
    assert math.isclose(d2.projected_72h_bps or 0, 48.0) and not d2.qualified
    # an incomplete trailing window excludes the decision (never forward-filled)
    make_data(tmp_path / "c", drop_binance_settlement=_set_idx(4, 8))
    v3, _ = carry.load_venues(tmp_path / "c")
    d3 = carry.signal(date(2024, 1, 6), v3)
    assert (
        not d3.valid
        and d3.exclusion is not None
        and d3.exclusion.startswith("INCOMPLETE_TRAILING_24H_FUNDING_BINANCE")
    )


def test_position_accounting_funding_costs_basis_and_timing(tmp_path: Path) -> None:
    # Bybit funding +0.001 at every settlement (SHORT Bybit receives), Binance -0.0005 (LONG Binance pays? no: a
    # LONG receives negative funding). Bybit drifts up 0.1 per 5m bar; Binance flat.
    k_all_by = {k: 0.001 for k in range(120)}
    k_all_bn = {k: -0.0005 for k in range(120)}
    make_data(tmp_path, bybit_rates=k_all_by, binance_rates=k_all_bn, bybit_drift_per_bar=0.1)
    venues, _ = carry.load_venues(tmp_path)
    dec = carry.signal(date(2024, 1, 10), venues)
    assert dec.direction == 1  # D = 3*0.001 - 3*(-0.0005) > 0 -> SHORT Bybit, LONG Binance
    entry = T0 + 9 * MS_D + 10 * 60_000
    p = carry.build_position("A-0001", "A", entry, 1, False, dec, venues)
    assert isinstance(p, carry.Position)
    assert p.exit_ms == entry + 72 * 3_600_000 and p.s_bybit == -1 and p.s_binance == 1
    # settlements in (entry, exit]: day9 08:00, 16:00, day10 00:00 ... day12 00:00 = 9 per venue; day9 00:00 excluded
    assert p.funding_count == {"BYBIT": 9, "BINANCE": 9}
    # SHORT Bybit with positive rate RECEIVES: -s*q*M*r = +M*0.001 per settlement
    m_by = [venues["BYBIT"].mark_at(entry + k * MS_8H - 10 * 60_000 + MS_8H) for k in range(9)]
    exp_by = sum((m or 0) * 0.001 for m in m_by)
    assert math.isclose(p.funding["BYBIT"], exp_by, rel_tol=1e-9)
    # LONG Binance with NEGATIVE rate receives: -(+1)*q*M*(-0.0005) = +M*0.0005
    assert math.isclose(p.funding["BINANCE"], 9 * 50_000.0 * 0.0005, rel_tol=1e-9)
    assert math.isclose(p.f_total, p.funding["BYBIT"] + p.funding["BINANCE"])
    # price: Bybit short loses the drift; Binance flat -> B = -(P_T - P_0)_bybit
    assert (
        math.isclose(p.price_pnl["BYBIT"], -(p.p_t["BYBIT"] - p.p0["BYBIT"]))
        and p.price_pnl["BINANCE"] == 0.0
    )
    assert math.isclose(p.b_total, p.price_pnl["BYBIT"] + p.price_pnl["BINANCE"]) and math.isclose(
        p.gross, p.f_total + p.b_total
    )
    # four executions at 8 bps on the proxy prices; N0 = one-leg average entry notional
    exp_cost = 8e-4 * (p.p0["BYBIT"] + p.p0["BINANCE"] + p.p_t["BYBIT"] + p.p_t["BINANCE"])
    assert math.isclose(p.cost_primary, exp_cost) and math.isclose(p.cost_stress, exp_cost * 1.5)
    assert math.isclose(p.n0, (p.p0["BYBIT"] + p.p0["BINANCE"]) / 2)
    assert math.isclose(p.net_bps, 1e4 * (p.f_total + p.b_total - p.cost_primary) / p.n0)
    assert math.isclose(p.funding_bps + p.basis_bps - p.cost_bps, p.net_bps, abs_tol=1e-9)
    assert (
        math.isclose(p.funding_h0_24 + p.funding_h24_48 + p.funding_h48_72, p.f_total)
        and p.funding_h0_24 > 0
    )
    # path metrics on bar opens: the Bybit short bleeds monotonically -> basis MAE = final basis, DD likewise
    assert (
        math.isclose(p.basis_mae, p.b_total, rel_tol=1e-9) and p.basis_dd <= 0 and p.total_dd <= 0
    )
    assert p.min_equity["BYBIT"] < p.p0["BYBIT"] and p.min_equity["BINANCE"] >= p.p0["BINANCE"]
    assert p.secondary_net_bps is None  # no traded bars in the synthetic set


def test_conditional_run_renewal_suppression_clusters_and_exclusions(tmp_path: Path) -> None:
    # Bybit +0.002 every settlement from day 3 onward -> every decision from day 4 qualifies (180 bps)
    make_data(
        tmp_path,
        n_days=40,
        bybit_rates={k: 0.002 for k in range(9, 120)},
        drop_bybit_mark_minute=20 * 1440 + 400,
    )
    venues, _ = carry.load_venues(tmp_path)
    days = [date(2024, 1, 1) + timedelta(days=k) for k in range(40)]
    log = carry.run_conditional(days, venues)
    pos_a = log.positions
    assert pos_a and all(
        b.entry_ms == a.exit_ms for a, b in pairwise(pos_a) if b.entry_ms - a.exit_ms < MS_D
    )
    assert all(p.exit_ms - p.entry_ms == 72 * 3_600_000 for p in pos_a)
    assert (
        sum(1 for p in pos_a if p.renewal) >= 1 and log.suppressed_by_active >= 2
    )  # days 2 and 3 of each hold
    # the position whose 72h window crosses the removed Bybit bar (day 20) is excluded, not truncated
    assert log.excluded.get("INCOMPLETE_FORWARD_72H_WINDOW_BYBIT", 0) >= 1
    assert not any(p.entry_ms <= T0 + 20 * MS_D + 400 * 60_000 <= p.exit_ms for p in pos_a)
    carry.assign_clusters(pos_a)
    assert all(p.cluster_id >= 1 for p in pos_a)
    # cluster rule: gap previous exit -> next entry <= 72h joins, > 72h splits (direction irrelevant)
    dec = carry.signal(date(2024, 1, 6), venues)
    e5 = T0 + 5 * MS_D + 10 * 60_000
    p1 = carry.build_position("x1", "A", e5, 1, False, dec, venues)
    p2 = carry.build_position("x2", "A", e5 + 3 * MS_D, -1, True, dec, venues)  # gap 0
    p3 = carry.build_position("x3", "A", e5 + 10 * MS_D, 1, False, dec, venues)  # gap 4 days
    assert all(isinstance(x, carry.Position) for x in (p1, p2, p3))
    assert carry.assign_clusters([p1, p2, p3]) == 2 and [
        p1.cluster_id,
        p2.cluster_id,
        p3.cluster_id,
    ] == [1, 1, 2]  # type: ignore[list-item]
    # determinism of the whole run
    log2 = carry.run_conditional(days, venues)
    carry.assign_clusters(log2.positions)
    assert [p.as_row() for p in log2.positions] == [p.as_row() for p in pos_a]


def test_control_b_schedule_and_broll_turnover(tmp_path: Path) -> None:
    rates = {k: (0.001 if k < 30 else -0.001) for k in range(120)}  # direction flips at day 10
    make_data(tmp_path, n_days=30, bybit_rates=rates)
    venues, _ = carry.load_venues(tmp_path)
    days = [date(2024, 1, 1) + timedelta(days=k) for k in range(30)]
    b = carry.run_control_b(days, venues, roll=False)
    r = carry.run_control_b(days, venues, roll=True)
    assert len(b.positions) == len(r.positions) >= 6
    assert all(q.entry_ms == p.exit_ms for p, q in pairwise(b.positions))
    assert all(p.cost_primary > 0 and p.funding_count["BYBIT"] == 9 for p in b.positions)
    # B-roll: a block whose neighbours keep the direction carries no close/reopen cost; a flip pays
    # the close of the old block and the open of the new one; the first and last blocks pay one side
    rp = r.positions
    inner_same = [
        q
        for p, q, n in zip(rp, rp[1:], rp[2:], strict=False)
        if p.direction == q.direction == n.direction
    ]
    assert inner_same and all(math.isclose(q.cost_primary, 0.0) for q in inner_same)
    flips = [(p, q) for p, q in pairwise(rp) if p.direction != q.direction]
    assert flips and all(p.cost_primary > 0 and q.cost_primary > 0 for p, q in flips)
    assert rp[0].cost_primary > 0 and rp[-1].cost_primary > 0
    assert sum(p.cost_primary for p in r.positions) < sum(p.cost_primary for p in b.positions)


def test_run_carry_writes_frozen_manifest_and_refuses_missing_inputs(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        carry.run_carry(tmp_path / "empty", tmp_path / "out")
    make_data(tmp_path, n_days=60, bybit_rates={k: 0.002 for k in range(9, 180)})
    res = carry.run_carry(tmp_path, tmp_path / "out", run_id="t")
    man = json.loads((tmp_path / "out" / "t" / "carry_manifest.json").read_text())
    assert (
        man["preregistration"]["status"] == "FROZEN_BEFORE_ANY_PNL"
        and man["order_capable"] is False
    )
    assert len(man["preregistration"]["blocks_equal_calendar_duration_from_valid_entry_dates"]) == 4
    assert res["verdict"]["verdict"] in (
        "PARK — INSUFFICIENT EVENTS",
        "INCONCLUSIVE",
        "REJECT — ECONOMIC SCREEN",
        "ELIGIBLE FOR EXECUTION-QUALIFIED REPLAY",
    )
    assert res["results"]["census"]["completed_positions"] >= 10
    for f in (
        "carry_positions.csv",
        "carry_blocks.csv",
        "carry_concentration.csv",
        "carry_cost_stress.csv",
        "carry_controls.csv",
        "carry_decisions.csv",
        "SUMMARY.md",
    ):
        assert (tmp_path / "out" / "t" / f).exists()
    res2 = carry.run_carry(tmp_path, tmp_path / "out2", run_id="t")
    assert (
        res2["results"]["A"] == res["results"]["A"]
        and res2["results"]["cluster_bootstrap"] == res["results"]["cluster_bootstrap"]
    )
