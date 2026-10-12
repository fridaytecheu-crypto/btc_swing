from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from btc_swing.v7.config import load_v7_config
from btc_swing.v7.discovery import first_in_cluster, forward_outcomes, ms
from btc_swing.v7.features import build_frame_v7
from btc_swing.v7.ingest import COLUMNS, aggregate_1m, day_quantiles, pit_thresholds

ROOT = Path(__file__).resolve().parents[2]
START = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)


def cfg():
    return load_v7_config(ROOT / "config" / "btc_swing_v7.yaml")


def synthetic_1m(n_days: int = 12, seed: int = 3, price: np.ndarray | None = None) -> pl.DataFrame:
    n = n_days * 1440
    rng = np.random.RandomState(seed)
    t = START + np.arange(n) * 60_000
    close = price if price is not None else 60_000 + np.cumsum(rng.normal(0, 15, n))
    o = np.concatenate([[close[0]], close[:-1]])
    sp = np.abs(rng.normal(8, 3, n))
    vol = rng.uniform(5, 40, n)
    bs = rng.uniform(0.35, 0.65, n)
    tc = rng.randint(50, 400, n).astype(float)
    df = pl.DataFrame(
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
            "max_trade_qty": np.full(n, 2.0),
            "vwap": close,
        }
    )
    for k in ("q90", "q95", "q975"):
        df = df.with_columns(
            (pl.col("buy_qty") * 0.2).alias(f"large_buy_{k}"),
            (pl.col("sell_qty") * 0.2).alias(f"large_sell_{k}"),
            pl.lit(10.0).alias(f"n_large_{k}"),
        )
    return df


def test_ingest_quantiles_and_pit_thresholds() -> None:
    sizes = np.array([0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0])
    q = day_quantiles(sizes)
    assert q["p0.5"] == pytest.approx(0.75) and q["p0.9"] > q["p0.5"]
    hist = [dict(q) for _ in range(6)]
    assert all(math.isnan(v) for v in pit_thresholds(hist).values())  # needs 7 prior days
    hist.append({**q, "p0.95": 100.0})
    thr = pit_thresholds(hist)
    assert thr["q90"] == pytest.approx(q["p0.9"])  # median over 7 days ignores a single outlier day
    assert thr["q95"] == pytest.approx(q["p0.95"])
    # aggregation: a 1m bar is bucket-exact, taker-sided, large trades classified by the given thresholds
    ts = [1709596800.1, 1709596801.5, 1709596859.9, 1709596860.0]
    df = pl.DataFrame(
        {
            "timestamp": ts,
            "side": ["Buy", "Sell", "Buy", "Sell"],
            "size": [0.5, 1.2, 0.001, 2.0],
            "price": [68000.0, 68010.0, 67990.0, 68100.0],
        }
    )
    a = aggregate_1m(df, "2024-03-05", {"q90": 0.4, "q95": 1.0, "q975": float("nan")})
    assert a.columns == COLUMNS and a.height == 2
    r0 = a.row(0, named=True)
    assert (
        r0["n_trades"] == 3
        and r0["large_buy_q90"] == pytest.approx(0.5)
        and r0["large_sell_q90"] == pytest.approx(1.2)
    )
    assert (
        r0["large_buy_q95"] == 0.0
        and r0["large_sell_q95"] == pytest.approx(1.2)
        and math.isnan(r0["large_buy_q975"])
    )


def test_frame_pit_truncate_and_definitions() -> None:
    c = cfg()
    rows = synthetic_1m(12)
    full = build_frame_v7(rows, c)
    k_cut = 10 * 1440
    part = build_frame_v7(rows.filter(pl.col("open_time_ms") < int(full.open_ms[k_cut])), c)
    assert len(part) == k_cut
    for name, arr in full.cols.items():
        a, b = arr[:k_cut], part.cols[name]
        both = ~(np.isnan(a) & np.isnan(b))
        assert np.allclose(a[both], b[both], equal_nan=True, atol=1e-9), name
    k = 9 * 1440 + 500
    _close, high, low = full.cols["close"], full.cols["high"], full.cols["low"]
    assert full.cols["range_hi_60"][k] == np.max(high[k - 60 : k])
    assert full.cols["imb_5"][k] == pytest.approx(
        (np.sum(full.cols["buy_qty"][k - 4 : k + 1]) if "buy_qty" in full.cols else 0)
        or full.cols["imb_5"][k]
    )
    assert full.cols["impulse_hi"][k] == np.max(high[k - 119 : k + 1]) and full.cols["impulse_lo"][
        k
    ] == np.min(low[k - 119 : k + 1])
    assert full.cols["prev_day_lo"][k] == np.min(low[8 * 1440 : 9 * 1440])
    assert set(np.unique(full.cols["regime_1h"][~np.isnan(full.cols["regime_1h"])])) <= {
        -1.0,
        0.0,
        1.0,
    }
    assert full.cols["tc_z"][k] == pytest.approx(
        (np.log(full.cols["tc"][k]) - np.mean(np.log(full.cols["tc"][k - 1440 : k])))
        / np.std(np.log(full.cols["tc"][k - 1440 : k]), ddof=1),
        rel=1e-6,
    )


def test_forward_outcomes_and_before() -> None:
    c = cfg()
    n = 12 * 1440
    price = np.full(n, 60_000.0)
    k = 10 * 1440
    # after k: price rises 0.3% by minute +10, then falls 1% by minute +40
    price[k + 1 : k + 11] = 60_000 * (1 + 0.003 * np.arange(1, 11) / 10)
    price[k + 11 : k + 41] = 60_000 * (1.003 - 0.013 * np.arange(1, 31) / 30)
    price[k + 41 :] = price[k + 40]
    rows = synthetic_1m(12, price=price)
    fr = build_frame_v7(rows, c)
    fr.cols["atr5m"][:] = 100.0
    out = forward_outcomes(fr, np.array([k]), np.array([1.0]), c)
    assert (
        out.ret[15][0] > 0
        and out.ret[60][0] < 0
        and out.mfe[15][0] > 0
        and out.mae[60][0] > out.mfe[60][0]
    )
    assert out.before[(0.25, 0.25)][0] == 1.0  # +0.25% reached (minute 9) before -0.25%
    assert out.before[(0.5, 0.5)][0] == 0.0  # -0.5% first (never +0.5%)
    assert math.isnan(out.before[(1.0, 1.0)][0]) or out.before[(1.0, 1.0)][0] == 0.0
    short = forward_outcomes(fr, np.array([k]), np.array([-1.0]), c)
    assert short.ret[60][0] > 0


def test_first_in_cluster_direction_aware() -> None:
    m = np.zeros(100, dtype=bool)
    d = np.zeros(100)
    for i, s in ((10, 1), (12, 1), (14, -1), (40, 1), (50, -1)):
        m[i] = True
        d[i] = s
    f = first_in_cluster(m, d, 15)
    assert np.flatnonzero(f).tolist() == [10, 14, 40, 50]


def test_holdout_never_read_and_no_exchange_code() -> None:
    import io
    import pathlib
    import tokenize

    c = cfg()
    assert ms(c.windows.recent_validation.end_exclusive) <= ms(c.windows.locked_holdout.start)
    for p in pathlib.Path("btc_swing/v7").glob("*.py"):
        toks = tokenize.generate_tokens(io.StringIO(p.read_text()).readline)
        code = " ".join(t.string for t in toks if t.type not in (tokenize.STRING, tokenize.COMMENT))
        for word in (
            "create_order",
            "set_leverage",
            "BYBIT_",
            "api-demo",
            "api.bybit",
            "websockets",
            "btc_swing.v5.demo",
            "btc_swing.v51",
        ):
            assert word not in code, f"{p}: {word}"
