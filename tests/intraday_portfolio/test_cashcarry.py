"""Cash-funded spot / short-perp carry: funding signs and settlement eligibility, equal quantities and
hedge PnL, no basis double counting, four execution costs, total-capital denominator, causal protection
exit with full-window benchmarking, cash benchmark, continuous diagnostic without free collateral resets,
frozen manifest and determinism."""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from btc_swing.research.intraday_portfolio import cashcarry as cc

MS_5M, MS_8H, MS_D = 300_000, 8 * 3_600_000, 86_400_000
T0 = int(
    datetime(2023, 12, 20, tzinfo=UTC).timestamp() * 1000
)  # data start, before the 2024-01-01 decision
ENTRY = int(datetime(2024, 1, 1, 0, 10, tzinfo=UTC).timestamp() * 1000)


def _write(root: Path, rel: str, df: pl.DataFrame, stem: str) -> None:
    d = root / "btc" / "datasets" / rel
    d.mkdir(parents=True, exist_ok=True)
    df.write_parquet(d / f"{stem}.parquet")


def make_data(
    root: Path,
    n_days: int = 110,
    spot0: float = 50_000.0,
    perp_premium: float = 50.0,
    spot_drift_per_bar: float = 0.0,
    rate: float = 0.0001,
    rate_overrides: dict[int, float] | None = None,
    sofr_pct: float = 5.0,
    usd0: float = 1.0,
    usd_t: float = 1.0,
    spike_minute: int | None = None,
    spike_mult: float = 1.0,
) -> None:
    n = n_days * 288
    t = (T0 + np.arange(n, dtype=np.int64) * MS_5M).astype(np.int64)
    spot = spot0 + spot_drift_per_bar * np.arange(n)
    if spike_minute is not None:
        spot = spot.copy()
        spot[spike_minute // 5 :] *= spike_mult
    perp = spot + perp_premium
    mark = perp.copy()
    one = np.ones(n)
    _write(
        root,
        "binance_spot_klines/BTCUSDT/5m",
        pl.DataFrame(
            {
                "open_time_ms": t,
                "open": spot,
                "high": spot + 1,
                "low": spot - 1,
                "close": spot,
                "volume": one,
                "quote_volume": spot,
                "trades": one.astype(int),
                "taker_buy_volume": one / 2,
                "taker_buy_quote_volume": spot / 2,
            }
        ),
        "2024-01",
    )
    _write(
        root,
        "perp_klines/BTCUSDT/5m",
        pl.DataFrame(
            {
                "open_time_ms": t,
                "open": perp,
                "high": perp + 1,
                "low": perp - 1,
                "close": perp,
                "volume": one,
                "quote_volume": perp,
                "taker_buy_volume": one / 2,
                "taker_buy_quote_volume": perp / 2,
            }
        ),
        "2024-01",
    )
    _write(
        root,
        "mark_price/BTCUSDT/5m",
        pl.DataFrame(
            {
                "open_time_ms": t,
                "open": mark,
                "high": mark + 1,
                "low": mark - 1,
                "close": mark + 0.5,
            }
        ),
        "2024-01",
    )
    n_set = n_days * 3
    ts = (T0 + np.arange(n_set, dtype=np.int64) * MS_8H).astype(
        np.int64
    ) + 12  # ms jitter like Binance
    r = np.full(n_set, rate)
    for k, v in (rate_overrides or {}).items():
        r[k] = v
    _write(
        root,
        "funding/BTCUSDT/na",
        pl.DataFrame({"time_ms": ts, "funding_rate": r, "funding_interval_hours": 8}),
        "2024-01",
    )
    days = [
        datetime.fromtimestamp(T0 / 1000, tz=UTC).date() + timedelta(days=k)
        for k in range(n_days + 1)
    ]
    sp = cc.sofr_path(root)
    sp.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "effective_date": [d.isoformat() for d in days if d.weekday() < 5],
            "percent_rate": [sofr_pct] * sum(1 for d in days if d.weekday() < 5),
            "source": ["test"] * sum(1 for d in days if d.weekday() < 5),
        }
    ).write_parquet(sp)
    up = cc.usdtusd_path(root)
    up.parent.mkdir(parents=True, exist_ok=True)
    dm = [int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000) for d in days]
    opens = [usd0 if m < ENTRY + 89 * MS_D else usd_t for m in dm]
    pl.DataFrame(
        {
            "day_open_ms": dm,
            "open": opens,
            "high": opens,
            "low": opens,
            "close": opens,
            "volume": [1.0] * len(dm),
            "source": ["test"] * len(dm),
        }
    ).write_parquet(up)


def inp_settlements(root: Path, w: cc.Allocation) -> list[tuple[int, float]]:
    inp, _fp = cc.load_inputs(root)
    return inp.venue.settlements_in(w.entry_ms, w.exit_ms, closed_left=False)


def _one(root: Path) -> cc.Allocation:
    inp, _fp = cc.load_inputs(root)
    return cc.run_window(inp, date(2024, 1, 1), "W")


def test_funding_signs_eligibility_and_settlement_count(tmp_path: Path) -> None:
    # settlement index of 2024-01-01 00:00 = (12 days) * 3 = 36 (the pre-entry midnight), exit midnight = 36 + 270
    k_entry_midnight = 12 * 3
    make_data(
        tmp_path,
        rate=0.0,
        rate_overrides={
            k_entry_midnight: 0.01,
            k_entry_midnight + 1: 0.001,
            k_entry_midnight + 270: 0.002,
            k_entry_midnight + 271: 0.01,
            k_entry_midnight + 100: -0.0005,
        },
    )
    w = _one(tmp_path)
    assert w.valid and not w.early_exit and w.funding_count == 270 and w.funding_negative_count == 1
    # short receives positive funding on the settlement mark (= mark-bar open containing the stamp = perp price)
    p0 = w.p0
    assert math.isclose(
        w.funding, p0 * 0.001 + p0 * 0.002 - p0 * 0.0005, rel_tol=1e-9
    )  # 0.01 before entry and after exit excluded
    assert w.settlement_mark_fallbacks == 0


def test_hedge_pnl_basis_identity_costs_and_denominators(tmp_path: Path) -> None:
    make_data(
        tmp_path, spot_drift_per_bar=0.5, rate=0.0
    )  # both legs rise together; premium constant
    w = _one(tmp_path)
    assert w.valid
    assert math.isclose(w.spot_pnl, w.st - w.s0) and math.isclose(w.perp_pnl, w.p0 - w.pt)
    assert math.isclose(w.basis_pnl, w.spot_pnl + w.perp_pnl) and math.isclose(
        w.basis_pnl, 0.0, abs_tol=1e-6
    )
    exp_cost = 12e-4 * (w.s0 + w.st) + 8e-4 * (w.p0 + w.pt)
    assert math.isclose(w.cost_primary, exp_cost) and math.isclose(
        w.cost_stress, 20e-4 * (w.s0 + w.st) + 12e-4 * (w.p0 + w.pt)
    )
    assert math.isclose(w.net_primary, w.basis_pnl + w.funding - w.cost_primary)
    assert math.isclose(w.c0, w.s0 + w.p0 + 12e-4 * w.s0 + 8e-4 * w.p0)
    assert math.isclose(w.n0, (w.s0 + w.p0) / 2) and math.isclose(
        w.net_bps_n0, 1e4 * w.net_primary / w.n0
    )
    assert (
        math.isclose(w.ret_capital, w.net_primary / w.c0) and w.ret_capital < 0
    )  # costs only -> negative
    assert w.exit_ms == ENTRY + 90 * MS_D and w.exit_sched_ms == w.exit_ms


def test_protection_exit_is_causal_and_full_window_benchmarked(tmp_path: Path) -> None:
    # spot/perp jump x1.7 at minute 30 of day 20 after entry: equity = 2 P0 - M -> ratio < 0.25 -> exit next bar + 5 min
    spike = (12 + 20) * 1440 + 30
    make_data(tmp_path, spike_minute=spike, spike_mult=1.7, rate=0.0001)
    w = _one(tmp_path)
    assert w.valid and w.early_exit and w.protection_breach_ms is not None
    breach_bar = T0 + (spike // 5) * MS_5M
    assert (
        w.protection_breach_ms == breach_bar and w.exit_ms == breach_bar + MS_5M
    )  # first bar >= breach + 5 min
    assert (
        w.exit_sched_ms == ENTRY + 90 * MS_D and w.funding_count < 270
    )  # funding stops at the exit
    assert w.min_equity_ratio <= 0.25
    # the window is still benchmarked over the full 90 days (cash return over 90 days, not 20)
    assert math.isclose(w.cash_ret, (1 + 5.0 / 100 / 360) ** 90 - 1, rel_tol=1e-9)
    assert math.isclose(w.excess_usd, w.ret_usd - w.cash_ret)  # benchmarked over the full window
    assert w.funding_count == sum(1 for t_, _r in inp_settlements(tmp_path, w))


def test_usd_translation_and_cash_benchmark(tmp_path: Path) -> None:
    make_data(tmp_path, rate=0.0, usd0=1.0, usd_t=0.99)
    w = _one(tmp_path)
    assert math.isclose(w.usdtusd_entry, 1.0) and math.isclose(w.usdtusd_exit, 0.99)
    assert math.isclose(w.ret_usd, (w.c0 + w.net_primary) * 0.99 / w.c0 - 1)
    assert math.isclose(w.excess_usd, w.ret_usd - w.cash_ret) and math.isclose(
        w.excess_annualized, w.excess_usd * 365 / 90
    )
    g, miss = cc.cash_growth({"2024-01-01": 5.0}, date(2024, 1, 1), date(2024, 1, 11))
    assert miss == 0 and math.isclose(g, (1 + 5.0 / 100 / 360) ** 10)
    assert cc.cash_growth({"2024-01-05": 5.0}, date(2024, 1, 1), date(2024, 1, 3)) == (1.0, 2)


def test_continuous_diagnostic_has_no_free_resets(tmp_path: Path) -> None:
    make_data(tmp_path, n_days=200, rate=0.0001)
    inp, _fp = cc.load_inputs(tmp_path)
    days = [date(2024, 1, 1), date(2024, 4, 1)]
    cont = cc.run_continuous(inp, days)
    s = cont.summary
    assert (
        s["status"] == "OK" and s["executions"] == 4 and s["protection_exits"] == 0
    )  # one entry, one exit
    w1 = cc.run_window(inp, days[0], "a")
    w2 = cc.run_window(inp, days[1], "b")
    # same capital base as the first window, funding over both windows, costs charged once each way
    assert math.isclose(s["c0_usdt"], w1.c0)
    # funding accrues continuously, including the day between the first window's exit and the second entry
    v = inp.venue
    exp_f = sum(
        m * r
        for t_, r in v.settlements_in(w1.entry_ms, w2.exit_sched_ms, closed_left=False)
        for m in [v.mark_at(t_) or 0.0]
    )
    assert math.isclose(s["funding_usdt"], exp_f, rel_tol=1e-9) and exp_f > w1.funding + w2.funding
    assert math.isclose(s["execution_costs_usdt"], 12e-4 * (w1.s0 + w2.st) + 8e-4 * (w1.p0 + w2.pt))
    assert (
        s["net_usdt"] > w1.net_primary + w2.net_primary
    )  # the boundary close/reopen costs are not paid
    assert len(cont.rows) == len(days) and all(
        r["label"] == "MTM_NO_BOUNDARY_COSTS" for r in cont.rows
    )


def test_run_manifest_frozen_and_deterministic(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        cc.run_cashcarry(tmp_path / "none", tmp_path / "o")
    make_data(tmp_path, n_days=110, rate=0.0001)
    r1 = cc.run_cashcarry(tmp_path, tmp_path / "out1", run_id="t")
    r2 = cc.run_cashcarry(tmp_path, tmp_path / "out2", run_id="t")
    assert (
        r1["results"]["valid_windows"] == 1 and r1["verdict"]["verdict"] == "INSUFFICIENT EVIDENCE"
    )
    m = json.loads((tmp_path / "out1" / "t" / "cashcarry_manifest.json").read_text())
    assert m["preregistration"]["status"] == "FROZEN_BEFORE_ANY_PNL" and m["order_capable"] is False
    assert m["preregistration"]["rules"]["holdout"].startswith("post-June-2025")
    assert r1["results"]["summary"] == r2["results"]["summary"]
    for f in (
        "cashcarry_allocations.csv",
        "cashcarry_summary.csv",
        "cashcarry_continuous.csv",
        "SUMMARY.md",
    ):
        assert (tmp_path / "out1" / "t" / f).read_bytes() == (
            tmp_path / "out2" / "t" / f
        ).read_bytes()
    b = cc.block_bootstrap_mean([0.01, -0.02, 0.03, 0.04, -0.01, 0.02])
    assert b["status"] == "OK" and b["ci95_low"] <= b["mean"] <= b["ci95_high"]
