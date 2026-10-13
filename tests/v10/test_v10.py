from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from btc_swing.v10.book import OrderBook
from btc_swing.v10.config import load_v10_config
from btc_swing.v10.engine import V10Engine, read_jsonl
from btc_swing.v10.families import Family
from btc_swing.v10.features import BarBuilder, FeatureEngine, History, Trade
from btc_swing.v10.paths import V10Paths
from btc_swing.v10.shadow import ShadowBook

ROOT = Path(__file__).resolve().parents[2]
T0 = 1_800_000_000_000


def cfg():
    return load_v10_config(ROOT / "config" / "btc_swing_v10.yaml")


def trade_msg(t: int, trades: list[tuple[float, float, str]]) -> dict:
    return {
        "topic": "publicTrade.BTCUSDT",
        "type": "snapshot",
        "ts": t,
        "rx_ms": t + 40,
        "data": [
            {"T": t, "s": "BTCUSDT", "S": s, "v": str(q), "p": str(p), "i": f"{t}-{k}"}
            for k, (p, q, s) in enumerate(trades)
        ],
    }


def liq_msg(t: int, side: str, qty: float, price: float) -> dict:
    return {
        "topic": "allLiquidation.BTCUSDT",
        "type": "snapshot",
        "ts": t,
        "rx_ms": t + 40,
        "data": [{"T": t, "s": "BTCUSDT", "S": side, "v": str(qty), "p": str(price)}],
    }


def test_history_rank_uses_previous_samples_only() -> None:
    h = History(100, 5)
    for v in (1, 2, 3, 4, 5):
        h.add(v)
    assert h.rank(6) == 1.0 and h.rank(0) == 0.0 and math.isclose(h.rank(2.5), 0.4)
    assert math.isnan(History(100, 5).rank(1.0))


def test_orderbook_validity_gap_and_crossed() -> None:
    ob = OrderBook(10.0, 30, 2.0)
    ob.apply(
        {
            "type": "snapshot",
            "ts": T0,
            "rx_ms": T0,
            "data": {
                "b": [["100", "1"], ["99.96", "2"]],
                "a": [["100.1", "1"], ["100.2", "3"]],
                "u": 10,
            },
        }
    )
    m = ob.metrics(T0 + 100)
    assert (
        m["book_valid"] == 1.0
        and m["bid1"] == 100.0
        and m["ask1"] == 100.1
        and m["bid_depth"] == 3.0
    )
    ob.apply(
        {
            "type": "delta",
            "ts": T0 + 200,
            "rx_ms": T0 + 200,
            "data": {"b": [["99.96", "0"]], "a": [], "u": 11},
        }
    )
    assert ob.metrics(T0 + 300)["bid_depth"] == 1.0
    ob.apply(
        {
            "type": "delta",
            "ts": T0 + 400,
            "rx_ms": T0 + 400,
            "seq_gap": True,
            "data": {"b": [], "a": [], "u": 13},
        }
    )
    assert ob.metrics(T0 + 500)["book_valid"] == 0.0
    ob.apply(
        {
            "type": "snapshot",
            "ts": T0 + 600,
            "rx_ms": T0 + 600,
            "data": {"b": [["100", "1"]], "a": [["100.1", "1"]], "u": 20},
        }
    )
    assert ob.metrics(T0 + 700)["book_valid"] == 1.0
    assert ob.metrics(T0 + 5000)["book_valid"] == 0.0  # stale


def test_bars_are_completed_only_by_later_data() -> None:
    bb = BarBuilder()
    bb.add(Trade(T0, 100.0, 1.0, True))
    bb.add(Trade(T0 + 30_000, 101.0, 1.0, False))
    assert len(bb.done) == 0
    bb.add(Trade(T0 + 61_000, 102.0, 1.0, True))
    assert len(bb.done) == 1 and bb.done[0].h == 101.0 and bb.done[0].buy == 1.0
    bb.finalize_until(T0 + 61_000 + 60_000 + 3000)
    assert len(bb.done) == 2


def test_features_are_pit_and_liquidation_sides() -> None:
    c = cfg()
    fe = FeatureEngine(c)
    t = T0
    for k in range(120):
        fe.on_event(trade_msg(t + k * 1000, [(100.0 + 0.01 * k, 0.5, "Buy"), (100.0, 0.2, "Sell")]))
    fe.on_event(liq_msg(t + 100_000, "Buy", 2.0, 100.0))  # a LONG was liquidated
    f = fe.compute(t + 119_500)
    assert f["buy_10"] > f["sell_10"] and f["imb_10"] > 0
    assert f["liq_long_120"] == 200.0 and f["liq_short_120"] == 0.0 and f["liq_imb_120"] == -1.0
    # a trade in the future of the tick is not visible
    fe.on_event(trade_msg(t + 200_000, [(50.0, 5.0, "Sell")]))
    f2 = fe.compute(t + 119_600)
    assert f2["last"] > 99.0 and f2["sell_10"] == f["sell_10"]


def _warm_engine(tmp_path: Path, mode: str = "SHADOW") -> tuple[V10Engine, int]:
    c = cfg()
    paths = V10Paths(tmp_path, "BTCUSDT", verification=True)
    eng = V10Engine(c, paths, observation_start_ms=T0, mode=mode)
    rng = np.random.RandomState(0)
    price = 100.0
    t = T0
    # 80 minutes of calm two-sided flow: warm-up histories and 15 completed 5m bars
    for sec in range(80 * 60):
        price += rng.normal(0, 0.002)
        qb, qs = float(rng.uniform(0.1, 0.6)), float(rng.uniform(0.1, 0.6))
        eng.on_event(trade_msg(t, [(round(price, 3), qb, "Buy"), (round(price, 3), qs, "Sell")]))
        eng.on_event(
            {
                "topic": "orderbook.50.BTCUSDT",
                "type": "snapshot" if sec == 0 else "delta",
                "ts": t,
                "rx_ms": t,
                "data": {
                    "b": [[f"{price - 0.01:.2f}", "5"]],
                    "a": [[f"{price + 0.01:.2f}", "5"]],
                    "u": sec + 1,
                },
            }
        )
        if sec % 60 == 0:
            eng.on_event(liq_msg(t, "Buy" if sec % 120 == 0 else "Sell", 0.01, round(price, 2)))
        eng.tick(t)
        t += 1000
    return eng, t


def test_family_a_arms_triggers_and_shadow_trade_closes(tmp_path: Path) -> None:
    eng, t = _warm_engine(tmp_path)
    f = eng.features.compute(t)
    assert f["warm"] == 1.0, eng.features.history_state()
    price = f["last"]
    # long-liquidation burst + heavy selling, little progress (absorption), then buy recovery
    for sec in range(40):
        eng.on_event(liq_msg(t, "Buy", 3.0, round(price, 2)))
        eng.on_event(
            trade_msg(
                t,
                [
                    (round(price - 0.0003 * sec, 3), 0.05, "Buy"),
                    (round(price - 0.0003 * sec, 3), 2.0, "Sell"),
                ],
            )
        )
        eng.tick(t)
        t += 1000
    fam_a_long = next(x for x in eng.families if x.family == "A" and x.st.side > 0)
    assert fam_a_long.st.state == "ARMED", fam_a_long.st.last_reason
    assert fam_a_long.st.absorption_seen
    low = fam_a_long.st.extreme
    for sec in range(30):  # flow reverses and price turns up
        p = low + 0.02 * (sec + 1)
        eng.on_event(trade_msg(t, [(round(p, 2), 1.5, "Buy"), (round(p, 2), 0.2, "Sell")]))
        out = eng.tick(t)
        t += 1000
        if out["decisions"]:
            break
    sigs = [r for r in read_jsonl(eng.paths.signals) if r["kind"].startswith("SIGNAL_")]
    assert sigs and sigs[0]["kind"] == "SIGNAL_EXECUTED", sigs
    d = sigs[0]["data"]
    assert d["family"] == "A" and d["side"] == "LONG" and d["stop"] < d["entry_ref"]
    assert abs(d["risk_usdt"] - 5.0) < 0.01 and 0.7 - 1e-9 <= d["stop_pct"] <= 1.5 + 1e-9
    assert d["signal_id"].startswith("V10S-") and d["episode_id"].startswith("V10E-")
    assert eng.shadow.pos is not None and eng.has_open_position()
    # price runs to TP2: shadow trade closes with a positive net R
    for sec in range(200):
        p = d["entry_ref"] * (1 + 0.0002 * sec)
        eng.on_event(trade_msg(t, [(round(p, 2), 0.5, "Buy")]))
        eng.tick(t)
        t += 1000
        if eng.shadow.pos is None:
            break
    closed = [r for r in read_jsonl(eng.paths.shadow_trades) if r["kind"] == "SHADOW_CLOSED"]
    assert closed and closed[0]["data"]["exit_reason"] == "TP2" and closed[0]["data"]["net_r"] > 1.0
    # journals chain-verify and state round-trips
    from btc_swing.v5.demo.journal import verify_chain

    assert verify_chain(eng.paths.signals)["ok"] and verify_chain(eng.paths.episodes)["ok"]
    eng.save()
    st = json.loads(eng.paths.state.read_text())
    assert st["counters"]["executed"] == 1 and st["observation_start_ms"] == T0


def test_pre_observation_ticks_never_transition(tmp_path: Path) -> None:
    c = cfg()
    paths = V10Paths(tmp_path, "BTCUSDT", verification=True)
    eng = V10Engine(c, paths, observation_start_ms=T0 + 10**9, mode="SHADOW")
    eng.on_event(trade_msg(T0, [(100.0, 1.0, "Buy")]))
    out = eng.tick(T0 + 1000)
    assert out["transitions"] == [] and not paths.episodes.exists()


def test_daily_safety_blocks_after_consecutive_losses(tmp_path: Path) -> None:
    c = cfg()
    paths = V10Paths(tmp_path, "BTCUSDT", verification=True)
    eng = V10Engine(c, paths, observation_start_ms=T0, mode="SHADOW")
    eng.tick(T0)
    for _ in range(c.safety.max_consecutive_losses):
        eng._register_close(-5.0, -1.0, T0 + 1000)
    assert (
        eng.state["blocked_until_ms"] is not None
        and eng.state["blocked_reason"] == "CONSECUTIVE_LOSSES"
    )
    fam = Family("A", 1, c)
    assert any(
        b.startswith("DAILY_SAFETY")
        for b in eng._safety_blockers(fam, {"warm": 1.0, "trade_age_s": 0.1}, T0 + 2000)
    )
    eng._roll_day(T0 + 86_400_000 + 1000)
    assert eng.state["blocked_until_ms"] is None


def test_shadow_stop_first_and_time_stop() -> None:
    c = cfg()
    sb = ShadowBook(c)
    sig = {
        "signal_id": "V10S-x",
        "episode_id": "V10E-x",
        "family": "B",
        "side": "SHORT",
        "side_sign": -1,
        "decision_ms": T0,
        "entry_ref": 100.0,
        "stop": 101.0,
        "tp1": 99.0,
        "tp2": 98.0,
        "qty": 5.0,
        "leverage": 1.0,
        "risk_usdt": 5.0,
    }
    sb.open(sig, {"spread_bps": 1.0})
    assert sb.pos is not None and sb.pos.entry_fill < 100.0  # short: filled below the reference
    assert sb.on_trade(Trade(T0 + 1000, 100.5, 1.0, True)) is None
    closed = sb.on_trade(Trade(T0 + 2000, 101.2, 1.0, True))
    assert closed is not None and closed["exit_reason"] == "STOP" and closed["net_r"] < -1.0
    sb.open(sig, {"spread_bps": 1.0})
    assert (
        sb.on_tick(T0 + c.exits.time_stop_min * 60_000 + 1, 99.9) is not None
    )  # no progress -> TIME


def test_v10_link_ids_and_activation_refusals(tmp_path: Path) -> None:
    from btc_swing.v10.activation import activation_state, effective_activation, record_activation
    from btc_swing.v10.execution import v10_link_id

    lid = v10_link_id("V10S-abc", "EN")
    assert lid.startswith("V10D-") and len(lid) <= 36
    paths = V10Paths(tmp_path, "BTCUSDT")
    paths.ensure()
    assert (
        activation_state(paths)["status"] == "NEVER_ACTIVATED"
        and effective_activation(paths) is None
    )
    try:
        record_activation(paths, {"x": {"ok": False, "detail": None}}, "no")
        raise AssertionError("must refuse")
    except RuntimeError:
        pass


def test_diagnostic_serialises_numpy_bools_and_status_warmup(tmp_path: Path) -> None:
    import numpy as np

    from btc_swing.v10.engine import _clean
    from btc_swing.v10.status import status_rows, trades_table, warmup_reconstruction

    c = cfg()
    fam = Family("B", 1, c)
    f = {"warm": 1.0, "abs_delta_pct_30": 0.99, "imb_30": -0.5, "dist_lo": np.float64(0.2)}
    conds = fam.arm_conditions(f)
    assert all(type(v) is bool for v in conds.values()) and conds["at_location"] is True
    json.dumps(
        _clean({"x": np.bool_(True), "y": np.float64(1.5), "z": np.int64(3), "n": float("nan")})
    )
    paths = V10Paths(tmp_path, "BTCUSDT", verification=True)
    eng = V10Engine(c, paths, observation_start_ms=T0, mode="SHADOW")
    eng.tick(T0)
    eng.save()
    wu = warmup_reconstruction(c, paths)
    assert wu["warm_now_by_state"] is False and wu["warm_at"] is None
    rows = dict(status_rows(c, paths))
    assert rows["warm-up (from state + bars)"].startswith("WARMING")
    assert trades_table(c, paths) == []
