from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from btc_swing.features.context import AuxSeries
from btc_swing.features.view import MultiTfSeries
from btc_swing.v5.collector import BybitPublicCollector, RawStore
from btc_swing.v5.config import V5Config, V5Family, load_v5_config
from btc_swing.v5.engine import V5Engine
from btc_swing.v5.evaluation import classify, entry_delay, funnel
from btc_swing.v5.events import build_v5_detectors
from btc_swing.v5.execution import (
    BybitDemoAdapter,
    BybitDemoConfig,
    DryRunAdapter,
    NotActivatedError,
    OrderRequest,
    OrderSide,
)
from btc_swing.v5.features import (
    FEATURE_COLUMNS,
    V5Inputs,
    book_levels,
    build_feature_frame,
    rolling_z,
)
from btc_swing.v5.ingest import aggregate_aggtrades
from btc_swing.v5.null import run_v5_null
from btc_swing.v5.stage_a import (
    scan_events,
    stage_a_gate,
    stage_a_null,
    stage_a_summary,
    unconditional,
)

ROOT = Path(__file__).resolve().parents[2]


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


def _cfg() -> V5Config:
    return load_v5_config(ROOT / "config" / "btc_swing_v5.yaml")


def test_v5_config_frozen() -> None:
    cfg = _cfg()
    assert cfg.risk.risk_per_trade == 0.0025 and cfg.execution.vol_floor_atr == 1.25
    assert cfg.exits.tp1_r == 1.0 and cfg.exits.tp2_r == 2.0 and cfg.exits.max_hold_hours == 24
    assert list(cfg.episode.family_priority) == list(V5Family)
    assert cfg.risk.max_leverage <= 10 and cfg.risk.risk_per_trade <= 0.02
    assert len(cfg.config_hash) == 64


def test_rolling_z_is_causal() -> None:
    x = np.arange(200, dtype=float)
    z = rolling_z(x, 20, 10)
    assert np.isnan(z[:10]).all() and np.allclose(z[40:], z[40], atol=1e-9)
    x2 = x.copy()
    x2[100] = 1e6
    assert np.allclose(z[:100], rolling_z(x2, 20, 10)[:100], atol=1e-9, equal_nan=True)


def test_aggtrades_aggregation_and_book_pivot() -> None:
    t0 = _ms(2024, 1, 1)
    rows = [
        f"1,100.0,2.0,1,1,{t0 + 1000},false",  # taker buy 2 BTC (big)
        f"2,100.0,0.5,2,2,{t0 + 2000},true",  # taker sell 0.5
        f"3,101.0,0.4,3,3,{t0 + 300_000 + 10},false",  # next bar
    ]
    csv = (
        "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n"
        + "\n".join(rows)
    ).encode()
    df = aggregate_aggtrades(csv)
    assert df.height == 2
    r0 = df.row(0, named=True)
    assert r0["buy_qty"] == pytest.approx(2.0) and r0["sell_qty"] == pytest.approx(0.5)
    assert r0["big_buy_qty"] == pytest.approx(2.0) and r0["big_sell_qty"] == 0.0
    book = pl.DataFrame(
        {
            "time_ms": [1, 1, 1, 1, 2, 2, 2, 2],
            "percentage": [-5, -1, 1, 5] * 2,
            "depth": [50.0, 10.0, 8.0, 40.0, 60.0, 12.0, 9.0, 45.0],
            "notional": [0.0] * 8,
        }
    )
    lv = book_levels(book)
    assert lv.columns == ["time_ms", "bid1", "ask1", "bid5", "ask5"] and lv["bid1"].to_list() == [
        10.0,
        12.0,
    ]


def _synthetic_metrics(bars: pl.DataFrame) -> pl.DataFrame:
    rng = np.random.RandomState(3)
    n = bars.height
    oi = 100_000.0 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    return pl.DataFrame(
        {
            "time_ms": bars["close_time_ms"],
            "open_interest": oi,
            "open_interest_value": oi * 30_000.0,
            "long_short_ratio_accounts": 1.0 + rng.normal(0, 0.1, n),
            "top_trader_long_short_ratio_positions": 1.0 + rng.normal(0, 0.1, n),
            "taker_long_short_volume_ratio": 1.0 + rng.normal(0, 0.2, n),
        }
    )


def _synthetic_flow(bars: pl.DataFrame) -> pl.DataFrame:
    """aggTrades-style 5m aggregates derived from the synthetic klines (half the days missing to
    exercise the kline fallback)."""
    rng = np.random.RandomState(5)
    b = bars.filter((pl.col("open_time_ms") // 86_400_000) % 2 == 0)
    tb = b["taker_buy_volume"].fill_null(0.0).to_numpy()
    v = b["volume"].to_numpy()
    n = b.height
    return pl.DataFrame(
        {
            "open_time_ms": b["open_time_ms"],
            "n_trades": rng.randint(50, 500, n).astype(np.uint32),
            "n_buy": rng.randint(20, 250, n).astype(np.uint32),
            "n_sell": rng.randint(20, 250, n).astype(np.uint32),
            "buy_qty": tb,
            "sell_qty": v - tb,
            "buy_notional": tb * 30_000.0,
            "sell_notional": (v - tb) * 30_000.0,
            "big_buy_qty": tb * 0.3,
            "big_sell_qty": (v - tb) * 0.3,
            "big_buy_notional_100k": tb * 0.1 * 30_000.0,
            "big_sell_notional_100k": (v - tb) * 0.1 * 30_000.0,
            "max_trade_qty": rng.uniform(0.5, 20.0, n),
            "vwap": b["close"],
        }
    )


def test_v5_feature_frame_events_and_stage_a_pit(synthetic_data: dict[str, object]) -> None:
    cfg = _cfg()
    bars, funding = synthetic_data["bars"], synthetic_data["funding"]
    assert isinstance(bars, pl.DataFrame) and isinstance(funding, pl.DataFrame)
    aux = AuxSeries.build(funding, _synthetic_metrics(bars), None, None, 0)
    series = MultiTfSeries(bars, cfg.indicators, 5)
    extra = V5Inputs(_synthetic_flow(bars), pl.DataFrame(), pl.DataFrame())
    ff = build_feature_frame(series, aux, cfg, extra)
    assert (
        set(FEATURE_COLUMNS) <= set(ff.cols)
        and len(ff.regime) == len(ff.close_ms) == series.base.close_ms.shape[0]
    )
    fs = ff.cols["flow_source"]
    assert 0.3 < np.nanmean(fs) < 0.7  # half the days use aggTrades, half the kline fallback
    # kline fallback and aggTrades agree by construction here, so delta is defined everywhere
    assert np.isnan(ff.cols["delta_5m"]).sum() == 0
    dets = build_v5_detectors(cfg, ff)
    assert len(dets) == 8
    for d in dets:  # scalar lookup equals the vectorised mask
        ks = np.flatnonzero(d.mask)[:5]
        for k in ks:
            e1 = d.event_at(int(k))
            assert e1 is not None and e1.strength == float(d.strength[k])
        assert d.event_at(int(np.flatnonzero(~d.mask)[0])) is None
    s, e = _ms(2023, 3, 1), _ms(2023, 8, 1)
    ev = scan_events(ff, dets, cfg, s, e)
    unc = unconditional(ff, cfg, s, e)
    sa = stage_a_summary(ev, cfg, unc)
    gate = stage_a_gate(sa, cfg)
    assert "passed" in gate and unc["n_bars"] > 0
    if ev.height:
        nl = stage_a_null(ev, ff, cfg, s, e, reps=5)
        assert all("time" in v and "regime" in v for v in nl.values())
        # forward returns never use the same bar: fwd_0.25h of an event at k is close[k+3]/close[k]-1
        r = ev.row(0, named=True)
        k = int(r["k"])
        sgn = 1.0 if r["side"] == "LONG" else -1.0
        assert r["fwd_0.25h"] == pytest.approx(
            sgn * (ff.cols["close"][k + 3] / ff.cols["close"][k] - 1.0)
        )
    # truncation: events before a cut are identical when later data is removed
    cut = _ms(2023, 6, 1)
    trunc = bars.filter(pl.col("open_time_ms") + 300_000 <= cut)
    series_t = MultiTfSeries(trunc, cfg.indicators, 5)
    extra_t = V5Inputs(
        extra.flow.filter(pl.col("open_time_ms") <= cut), pl.DataFrame(), pl.DataFrame()
    )
    aux_t = AuxSeries.build(
        funding.filter(pl.col("time_ms") <= cut),
        _synthetic_metrics(bars).filter(pl.col("time_ms") <= cut),
        None,
        None,
        0,
    )
    ff_t = build_feature_frame(series_t, aux_t, cfg, extra_t)
    ev_t = scan_events(ff_t, build_v5_detectors(cfg, ff_t), cfg, s, cut)
    if ev.height:
        a = ev.filter(pl.col("t_ms") <= cut - 86_400_000)
        b = ev_t.filter(pl.col("t_ms") <= cut - 86_400_000) if ev_t.height else ev_t
        assert a.height == b.height
        if a.height:
            assert a["t_ms"].to_list() == b["t_ms"].to_list()
            assert np.allclose(a["strength"].to_numpy(), b["strength"].to_numpy(), atol=1e-9)


def test_v5_engine_stops_one_exposure_and_delay(synthetic_data: dict[str, object]) -> None:
    cfg = _cfg()
    bars, funding = synthetic_data["bars"], synthetic_data["funding"]
    assert isinstance(bars, pl.DataFrame) and isinstance(funding, pl.DataFrame)
    aux = AuxSeries.build(funding, _synthetic_metrics(bars), None, None, 0)
    series = MultiTfSeries(bars, cfg.indicators, 5)
    extra = V5Inputs(_synthetic_flow(bars), pl.DataFrame(), pl.DataFrame())
    ff = build_feature_frame(series, aux, cfg, extra)
    s, e = _ms(2023, 3, 1), _ms(2023, 8, 1)
    r1 = V5Engine(cfg, bars, funding, None, aux, series, ff).run(s, e)
    r2 = V5Engine(cfg, bars, funding, None, aux, series, ff).run(s, e)
    assert r1.result_hash == r2.result_hash and r1.decisions.height > 0
    assert r1.episodes.height > 0
    t = r1.trades
    ev = scan_events(ff, build_v5_detectors(cfg, ff), cfg, s, e)
    fun = funnel(ev, r1.episodes, t, r1.blocked, s, e)
    assert fun["events"] >= fun["episodes"] >= fun["confirmed"] >= fun["trades"]
    if t.height:
        srt = t.sort("entry_ms")
        assert (srt["entry_ms"].to_numpy()[1:] >= srt["exit_ms"].to_numpy()[:-1]).all()
        assert (t["stop_distance_atr"] >= cfg.execution.vol_floor_atr * 0.9).all()
        assert (t["stop_distance_atr"] <= cfg.execution.max_stop_atr * 1.1).all()
        assert (t["leverage"] <= cfg.risk.max_leverage).all()
        # entry strictly after the event and after the confirmation, inside the 2 h + 6 bar window
        assert (t["entry_ms"] > t["event_ms"]).all() and (t["entry_ms"] > t["confirm_ms"]).all()
        assert (t["entry_delay_min"] <= 2 * 60 + 7 * 5).all()
        assert (t["holding_hours"] <= cfg.exits.max_hold_hours + 0.1).all()
        d = entry_delay(t)
        assert d["n"] == t.height and "p50" in d["entry_delay_min"]
        nl = run_v5_null(
            V5Engine(cfg, bars, funding, None, aux, series, ff), t.head(3), r1.decisions, s, e, 2, 1
        )
        assert nl["n_trades"] == 3


def test_v5_classification_rule() -> None:
    cfg = _cfg()

    def crit(met: dict[int, bool]) -> list[dict[str, object]]:
        return [{"id": i, "met": met.get(i, True)} for i in range(1, 11)]

    assert classify(cfg, crit({}), {"expectancy_R_before_costs": 0.2}).startswith("A")
    assert classify(
        cfg, crit({3: False, 7: False}), {"expectancy_R_before_costs": 0.12}
    ).startswith("B")
    assert classify(cfg, crit({1: False}), {"expectancy_R_before_costs": 0.2}).startswith("C")
    assert classify(cfg, crit({10: False}), {"expectancy_R_before_costs": 0.2}).startswith("C")
    assert classify(cfg, crit({4: False}), {"expectancy_R_before_costs": 0.05}).startswith("C")


def test_collector_store_dedupe_and_sequence(tmp_path: Path) -> None:
    cfg = _cfg().collector
    col = BybitPublicCollector(cfg, tmp_path)
    snap = json.dumps(
        {
            "topic": "orderbook.50.BTCUSDT",
            "type": "snapshot",
            "ts": 1000,
            "data": {"u": 10, "seq": 1},
        }
    )
    d1 = json.dumps(
        {"topic": "orderbook.50.BTCUSDT", "type": "delta", "ts": 1001, "data": {"u": 11, "seq": 2}}
    )
    d3 = json.dumps(
        {"topic": "orderbook.50.BTCUSDT", "type": "delta", "ts": 1003, "data": {"u": 13, "seq": 4}}
    )
    tr = json.dumps(
        {
            "topic": "publicTrade.BTCUSDT",
            "type": "snapshot",
            "ts": 1002,
            "data": [{"i": "a1", "T": 1002, "p": "1", "v": "1", "S": "Buy"}],
        }
    )
    for m in (json.dumps({"op": "subscribe", "success": True}), snap, d1, d1, tr, d3):
        col._on_message(m)
    st = col.stats
    assert (
        st.messages == 6
        and st.duplicates == 1
        and st.orderbook_deltas == 2
        and st.sequence_gaps == 1
    )
    col.store.flush()
    col._save()
    col.store.close()
    v = RawStore(tmp_path, cfg.symbol).verify()
    assert (
        v["rows"] == 4
        and v["hash_mismatches"] == 0
        and v["by_channel"]["orderbook.50.BTCUSDT"] == 3
    )
    # resumability: a new collector reads the last order-book update id from state
    col2 = BybitPublicCollector(cfg, tmp_path)
    assert col2._last_u == 13 and col2._resumed_from


def test_execution_abstraction_not_activated() -> None:
    dry = DryRunAdapter()
    ack = dry.place_order(OrderRequest("BTCUSDT", OrderSide.BUY, 0.01))
    assert (
        ack.accepted
        and dry.intents[0]["kind"] == "place_order"
        and dry.position_state("BTCUSDT").size == 0.0
    )
    demo = BybitDemoAdapter()
    with pytest.raises(NotActivatedError):
        demo.place_order(OrderRequest("BTCUSDT", OrderSide.BUY, 0.01))
    with pytest.raises(NotActivatedError):
        demo.account_balance()
    with pytest.raises(NotActivatedError):
        BybitDemoAdapter(BybitDemoConfig(activated=True))
