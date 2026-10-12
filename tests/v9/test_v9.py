from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

from btc_swing.v9.config import load_v9_config
from btc_swing.v9.engine import V9Engine, ladder
from btc_swing.v9.features import build_frame_v9, slot_median_prev_days
from btc_swing.v9.setups import build_tables

ROOT = Path(__file__).resolve().parents[2]
START = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)


def cfg():
    return load_v9_config(ROOT / "config" / "btc_swing_v9.yaml")


def synthetic_1m(n_days: int = 30, seed: int = 5) -> tuple[pl.DataFrame, np.ndarray]:
    n = n_days * 1440
    rng = np.random.RandomState(seed)
    close = 60_000 + np.cumsum(rng.normal(0, 30, n))
    vol = rng.uniform(5, 15, n)
    return _frame(close, vol, rng), close


def _frame(close: np.ndarray, vol: np.ndarray, rng: np.random.RandomState) -> pl.DataFrame:
    n = len(close)
    t = START + np.arange(n) * 60_000
    o = np.concatenate([[close[0]], close[:-1]])
    sp = np.abs(rng.normal(2, 1, n))
    bs = rng.uniform(0.4, 0.6, n)
    tc = rng.randint(50, 400, n).astype(float)
    return pl.DataFrame(
        {
            "open_time_ms": t,
            "close_time_ms": t + 60_000,
            "open": o,
            "high": np.maximum(o, close) + sp,
            "low": np.minimum(o, close) - sp,
            "close": close,
            "volume": vol,
            "quote_volume": vol * close,
            "n_trades": tc,
            "n_buy": tc * bs,
            "n_sell": tc * (1 - bs),
            "buy_qty": vol * bs,
            "sell_qty": vol * (1 - bs),
            "buy_notional": vol * bs * close,
            "sell_notional": vol * (1 - bs) * close,
            "max_trade_qty": vol / 10,
            "vwap": close,
        }
    )


def test_frame_is_pit_and_shapes() -> None:
    rows, _close = synthetic_1m()
    c = cfg()
    fr = build_frame_v9(rows, c)
    assert fr.n5 == len(rows) // 5
    assert np.isnan(fr.m["rvol1"][:20]).all()  # needs 20 completed previous minutes
    # changing the future must not change the past: perturb the last day and compare
    rows2 = rows.with_columns(
        pl.when(pl.col("open_time_ms") >= START + 29 * 86_400_000)
        .then(pl.col("close") * 1.05)
        .otherwise(pl.col("close"))
        .alias("close")
    )
    fr2 = build_frame_v9(rows2, c)
    cut = 29 * 288
    for k in ("atr5", "rvol5", "trend"):
        a, b = fr.b5[k][:cut], fr2.b5[k][:cut]
        assert np.array_equal(np.isnan(a), np.isnan(b)) and np.allclose(
            a[~np.isnan(a)], b[~np.isnan(b)]
        )


def test_slot_median_uses_previous_days_only() -> None:
    x = np.arange(10 * 4, dtype=float)  # 10 days x 4 slots, strictly increasing
    out = slot_median_prev_days(x, 4, 3, 2)
    assert np.isnan(out[: 3 * 4]).all()
    # day 3 slot 0 = median of days 0..2 slot 0 = median(0, 4, 8) = 4
    assert out[12] == 4.0


def test_constructed_impulse_pullback_breakout_trades_long() -> None:
    c = cfg()
    n_days = 30
    n = n_days * 1440
    rng = np.random.RandomState(1)
    close = 60_000 + np.cumsum(rng.normal(0, 0.5, n))
    vol = rng.uniform(8, 12, n)
    # day 25: a rising 15m trend, then an impulse of ~1.5% over 20 minutes with high volume, a 15-minute
    # pullback of ~40% on low volume, then a 1m breakout with volume and buy imbalance
    d0 = 25 * 1440
    for k in range(d0 - 600, d0):
        close[k] = close[k - 1] + 0.4
    imp = range(d0, d0 + 20)
    for k in imp:
        close[k] = close[k - 1] + 45.0
        vol[k] = 40.0
    for k in range(d0 + 20, d0 + 35):
        close[k] = close[k - 1] - 24.0
        vol[k] = 4.0
    for k in range(d0 + 35, d0 + 47):
        close[k] = close[k - 1] + 0.5
        vol[k] = 5.0
    close[d0 + 47] = close[d0 + 46] + 60.0  # breakout minute
    vol[d0 + 47] = 50.0
    for k in range(d0 + 48, d0 + 300):
        close[k] = close[k - 1] + 3.0  # runs to the target
        vol[k] = 10.0
    rows = _frame(close, vol, rng)
    # make the breakout minute buy-dominated
    rows = rows.with_columns(
        pl.when(pl.col("open_time_ms") == START + (d0 + 47) * 60_000)
        .then(pl.col("volume") * 0.9)
        .otherwise(pl.col("buy_qty"))
        .alias("buy_qty"),
        pl.when(pl.col("open_time_ms") == START + (d0 + 47) * 60_000)
        .then(pl.col("volume") * 0.1)
        .otherwise(pl.col("sell_qty"))
        .alias("sell_qty"),
    )
    fr = build_frame_v9(rows, c)
    tables = build_tables(fr, c)
    eng = V9Engine(fr, tables, c)
    a = type(ladder(c)[0])(**{**ladder(c)[0].as_dict(), "min_stop_pct": 0.05})
    res = eng.run(a, START + 24 * 86_400_000, START + 27 * 86_400_000)
    longs = [t for t in res.trades if t.direction > 0 and 25 * 1440 <= t.entry_minute < 26 * 1440]
    assert longs, "the constructed long setup must trade under PRICE_ONLY"
    t = longs[0]
    assert t.entry_minute == d0 + 48
    assert t.stop_ref < t.entry_ref and t.stop_pct >= 0.05
    assert any(lg[2].startswith("TP") for lg in t.legs)
    assert (
        t.r(t.gross_usd) > 1.4 and t.r(t.net_usd) > 0 and t.r(t.fee_usd) > 0 and t.r(t.slip_usd) > 0
    )
    # the full volume version also takes it (volume and flow were constructed to pass)
    d = type(ladder(c)[3])(**{**ladder(c)[3].as_dict(), "min_stop_pct": 0.05})
    res_d = eng.run(d, START + 24 * 86_400_000, START + 27 * 86_400_000)
    assert any(x.entry_minute == d0 + 48 for x in res_d.trades)
    # the anchor minimum stop (0.50%) rejects this micro-stop and counts it; floor mode takes it widened
    res_r = eng.run(ladder(c)[0], START + 24 * 86_400_000, START + 27 * 86_400_000)
    assert res_r.funnel.rejected_micro_stop >= 1
    fl = type(ladder(c)[0])(**{**ladder(c)[0].as_dict(), "stop_mode": "floor"})
    res_f = eng.run(fl, START + 24 * 86_400_000, START + 27 * 86_400_000)
    tf = next(x for x in res_f.trades if x.entry_minute == d0 + 48)
    assert (
        abs(tf.stop_pct - c.stop.min_distance_pct) < 1e-9 and res_f.funnel.floored_micro_stop >= 1
    )
    assert res_d.funnel.executed == len(res_d.trades)


def test_short_is_mirror_of_long() -> None:
    c = cfg()
    rows, _close = synthetic_1m(seed=11)
    fr = build_frame_v9(rows, c)
    tables = build_tables(fr, c)
    eng = V9Engine(fr, tables, c)
    spec = type(ladder(c)[0])(**{**ladder(c)[0].as_dict(), "min_stop_pct": 0.05})
    res = eng.run(spec, START + 5 * 86_400_000, START + 30 * 86_400_000)
    # mirrored prices -> mirrored trades (same minutes, opposite direction, same R)
    rows_m = rows.with_columns(
        (120_000 - pl.col("open")).alias("open"),
        (120_000 - pl.col("low")).alias("high"),
        (120_000 - pl.col("high")).alias("low"),
        (120_000 - pl.col("close")).alias("close"),
        pl.col("sell_qty").alias("buy_qty"),
        pl.col("buy_qty").alias("sell_qty"),
    )
    fr_m = build_frame_v9(rows_m, c)
    tables_m = build_tables(fr_m, c)
    res_m = V9Engine(fr_m, tables_m, c).run(spec, START + 5 * 86_400_000, START + 30 * 86_400_000)
    a = [(t.entry_minute, t.direction) for t in res.trades]
    b = [(t.entry_minute, -t.direction) for t in res_m.trades]
    assert a == b
    assert len(res.trades) > 0


def test_stop_first_when_both_touched_and_min_stop_rejects() -> None:
    c = cfg()
    rows, _ = synthetic_1m(seed=2)
    fr = build_frame_v9(rows, c)
    tables = build_tables(fr, c)
    eng = V9Engine(fr, tables, c)
    spec = ladder(c)[0]
    # a huge minimum stop rejects every trade and counts the rejections
    big = type(spec)(**{**spec.as_dict(), "min_stop_pct": 50.0})
    res = eng.run(big, START + 5 * 86_400_000, START + 30 * 86_400_000)
    assert res.trades == [] and res.funnel.rejected_micro_stop >= 0
    # simulate directly: a minute whose range covers both stop and target -> STOP first
    m0 = 10 * 1440
    fr.valid[m0] = True
    fr.m["open"][m0] = 100.0
    fr.m["high"][m0] = 103.0
    fr.m["low"][m0] = 98.0
    fr.m["close"][m0] = 101.0
    eng2 = V9Engine(fr, tables, c)
    legs, m_exit = eng2._simulate(spec, 1, m0, 100.0, 99.0, 1.0)
    assert legs[0][2] == "STOP" and m_exit == m0


def test_exit_targets_parse_generic_modes() -> None:
    from btc_swing.v9.engine import exit_targets

    ex = cfg().exits
    assert exit_targets("A", ex) == [(1.0, ex.exit_a_r)]
    assert exit_targets("B", ex) == [(1.0, ex.exit_b_r)]
    assert exit_targets("C", ex) == [(0.5, ex.split_tp1_r), (0.5, ex.split_tp2_r)]
    assert exit_targets("R2.0", ex) == [(1.0, 2.0)]
    assert exit_targets("S1.0/2.0", ex) == [(0.5, 1.0), (0.5, 2.0)]


def test_floor_mode_keeps_usdt_risk_constant_as_stops_widen() -> None:
    c = cfg()
    rows, _close = synthetic_1m(seed=11)
    fr = build_frame_v9(rows, c)
    tables = build_tables(fr, c)
    eng = V9Engine(fr, tables, c)
    base = ladder(c)[0]
    risks = {}
    for floor in (0.5, 1.5):
        sp = type(base)(
            **{**base.as_dict(), "min_stop_pct": floor, "stop_mode": "floor", "exit": "R2.0"}
        )
        res = eng.run(sp, START + 5 * 86_400_000, START + 30 * 86_400_000)
        assert res.trades
        risks[floor] = [round(t.risk_usd, 6) for t in res.trades]
        assert all(t.stop_pct >= floor - 1e-9 for t in res.trades)
    planned = c.risk.initial_equity * c.risk.risk_per_trade
    assert all(abs(r - planned) < 1e-6 for r in risks[0.5] + risks[1.5])
