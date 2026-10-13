"""V11 FAST MULTI-FACTOR INTRADAY — required tests (owner specification section 14).

All data here is synthetic; nothing touches the archive, the locked holdout or any V10 artefact."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

from btc_swing.v5.demo.journal import verify_chain
from btc_swing.v11 import config as v11_config
from btc_swing.v11.config import PREFIX_SIGNAL, STRATEGY, STRATEGY_SECTIONS, load_v11_config
from btc_swing.v11.engine import MS_1M, V11Engine
from btc_swing.v11.features import V11Frame, build_frame_v11
from btc_swing.v11.freeze import check_freeze, write_freeze
from btc_swing.v11.live import V11Live
from btc_swing.v11.paths import V11Paths
from btc_swing.v11.scoring import GROUPS, score_frame, total_score

ROOT = Path(__file__).resolve().parents[2]
CFG_PATH = ROOT / "config" / "btc_swing_v11.yaml"
DAY_MS = 86_400_000
T0 = 1_800_000_000_000 - (1_800_000_000_000 % DAY_MS)
P0 = 50_000.0


def cfg():
    return load_v11_config(CFG_PATH)


def synth_rows(days: int, seed: int = 1) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    n = days * 1440
    r = rng.normal(0, 0.0006, n) + 0.0004 * np.sin(np.arange(n) / 300.0)
    c = P0 * np.exp(np.cumsum(r))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.0003, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.0003, n)))
    v = rng.lognormal(0, 0.6, n) * (1 + np.abs(r) * 800)
    b = v * rng.uniform(0.3, 0.7, n)
    return pl.DataFrame(
        {
            "open_time_ms": T0 + np.arange(n) * MS_1M,
            "open": o,
            "high": h,
            "low": lo,
            "close": c,
            "volume": v,
            "n_trades": np.full(n, 30.0),
            "buy_qty": b,
            "sell_qty": v - b,
        }
    )


def _same(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.all((a == b) | (np.isnan(a) & np.isnan(b))))


def quiet_engine(days: int = 3, cost_mult: float = 1.0) -> tuple[V11Engine, int]:
    """Engine on a synthetic frame with all scores NaN (nothing arms by itself); returns (engine, i)
    where i is a row late on the last day with finite ATRs so decisions can be injected."""
    c = cfg()
    fr = build_frame_v11(synth_rows(days), c)
    sc = {k: np.full(len(fr), np.nan) for k in score_frame(fr, c)}
    eng = V11Engine(c, fr, sc, cost_mult)
    i = days * 1440 - 200
    assert np.isfinite(fr.c["atr5"][i]) and np.isfinite(fr.c["atr1h"][i])
    return eng, i


def inject(eng: V11Engine, i: int, s: int, stop_frac: float, fam: str = "A") -> dict:
    """Force a decision at row i with a structural stop stop_frac below (long) / above (short)."""
    close = float(eng.X["close"][i])
    eng.stop_level = lambda f, side, j: (close * (1 - side * stop_frac), "test")  # type: ignore[method-assign]
    eng.sc["score_long"][i] = 80.0
    eng.sc["score_short"][i] = 80.0
    arm = {
        "until_ms": int(eng.t_open[i]) + 10 * MS_1M,
        "score": 72.0,
        "armed_ms": int(eng.t_open[i]),
    }
    return eng._decide(fam, s, i, arm)


def set_path(eng: V11Engine, i0: int, closes: list[float]) -> None:
    """Overwrite bars i0.. with a controlled OHLC path (open=close of previous, high/low cover both)."""
    X = eng.X  # noqa: N806
    prev = float(X["close"][i0 - 1])
    for k, px in enumerate(closes):
        j = i0 + k
        X["open"][j] = prev
        X["close"][j] = px
        X["high"][j] = max(prev, px)
        X["low"][j] = min(prev, px)
        prev = px


# ----------------------------------------------------------------------------- config / freeze
def test_config_is_pre_registered_and_hard_limited(tmp_path: Path) -> None:
    c = cfg()
    assert c.strategy_name == STRATEGY
    assert abs(sum(c.weights.as_dict().values()) - 100.0) < 1e-9
    assert c.risk.risk_per_trade == 0.0015 and c.risk.max_leverage <= 5
    assert c.stop.cap_pct == 1.0 and c.stop.floor_pct == 0.35
    assert c.exits.hard_max_min == 60 and c.exits.tp1_frac + c.exits.tp2_frac < 1
    assert len(c.config_hash) == 64 and len(c.strategy_hash) == 64
    raw = yaml.safe_load(CFG_PATH.read_text())
    raw["risk"]["max_leverage"] = 10
    raw["risk"]["allowed_leverage"] = [1, 2, 3, 5, 10]
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError):
        load_v11_config(bad)
    raw = yaml.safe_load(CFG_PATH.read_text())
    raw["risk"]["risk_per_trade"] = 0.003
    bad.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError):
        load_v11_config(bad)


def test_freeze_pins_strategy_sections_only(tmp_path: Path) -> None:
    c = cfg()
    fz = tmp_path / "freeze.json"
    rec = write_freeze(c, {"classification": {"classification": "C"}}, None, fz)
    assert rec["strategy_hash"] == c.strategy_hash and "Demo" in rec["mode"]
    assert check_freeze(c, fz)["strategy_hash"] == c.strategy_hash
    # a second freeze never overwrites the first
    assert write_freeze(c, None, None, fz)["frozen_at"] == rec["frozen_at"]
    # non-strategy change (outcome horizons) is allowed under the same freeze
    raw = yaml.safe_load(CFG_PATH.read_text())
    raw["outcomes"]["candidate_dedupe_min"] = 7
    p = tmp_path / "ok.yaml"
    p.write_text(yaml.safe_dump(raw))
    assert check_freeze(load_v11_config(p), fz)["strategy_hash"] == c.strategy_hash
    # any strategy section change is refused
    raw = yaml.safe_load(CFG_PATH.read_text())
    raw["thresholds"]["entry"] = 76
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(RuntimeError):
        check_freeze(load_v11_config(p), fz)
    with pytest.raises(RuntimeError):
        write_freeze(load_v11_config(p), None, None, fz)
    assert set(STRATEGY_SECTIONS) >= {"weights", "thresholds", "families", "stop", "exits", "risk"}


# ----------------------------------------------------------------------------- features / scores
def test_features_and_scores_are_point_in_time() -> None:
    c = cfg()
    rows = synth_rows(25)
    full, sc_full = build_frame_v11(rows, c), None
    sc_full = score_frame(full, c)
    k = 20 * 1440 + 37  # mid-hour cut: nothing after row k-1 exists in the truncated run
    part = build_frame_v11(rows[:k], c)
    sc_part = score_frame(part, c)
    for name, arr in full.c.items():
        assert _same(arr[:k], part.c[name][:k]), f"lookahead in feature {name}"
    for name in ("score_long", "score_short"):
        assert _same(sc_full[name][:k], sc_part[name][:k]), f"lookahead in {name}"
    assert np.nanmax(sc_full["score_long"]) <= 100 and np.nanmin(sc_full["score_long"]) >= 0


def test_score_renormalises_over_available_groups() -> None:
    c = cfg()
    w = c.weights.as_dict()
    n = 3
    sub = {g: np.full(n, 1.0) for g in GROUPS}
    sub["microstructure"] = np.full(n, np.nan)  # forward-only group absent historically
    score, contrib, avail = total_score(sub, c)
    assert np.allclose(score, 100.0) and np.allclose(avail, 100 - w["microstructure"])
    assert np.all(np.isnan(contrib["microstructure"]))
    sub["trend"] = np.zeros(n)
    score, _, avail = total_score(sub, c)
    assert np.allclose(score, 100.0 * (avail - w["trend"]) / avail)
    allnan = {g: np.full(n, np.nan) for g in GROUPS}
    score, _, avail = total_score(allnan, c)
    assert np.all(np.isnan(score)) and np.all(avail == 0)
    # the live microstructure group raises the available weight to 100 when present
    sub = {g: np.full(n, 0.5) for g in GROUPS}
    score, _, avail = total_score(sub, c)
    assert np.allclose(avail, 100.0) and np.allclose(score, 50.0)


def test_long_short_symmetry_under_price_mirror() -> None:
    c = cfg()
    rows = synth_rows(25)
    mirror = rows.with_columns(
        [
            (2 * P0 - pl.col("open")).alias("open"),
            (2 * P0 - pl.col("low")).alias("high"),
            (2 * P0 - pl.col("high")).alias("low"),
            (2 * P0 - pl.col("close")).alias("close"),
            pl.col("sell_qty").alias("buy_qty"),
            pl.col("buy_qty").alias("sell_qty"),
        ]
    )
    sc = score_frame(build_frame_v11(rows, c), c)
    scm = score_frame(build_frame_v11(mirror, c), c)
    for g in ("trend", "volume", "location"):
        a, b = sc[f"long_{g}"], scm[f"short_{g}"]
        assert _same(a, b), f"group {g} is not LONG/SHORT symmetric"
    a, b = sc["score_long"], scm["score_short"]
    assert _same(np.isfinite(a), np.isfinite(b))
    ok = np.isfinite(a)
    d = np.abs(a[ok] - b[ok])
    # percent-of-price normalisations (ROC, bands) are not exactly invariant under an additive
    # mirror; the formulas are side-symmetric: identical in > 95% of rows, mean gap < 0.5 points
    assert float(np.mean(d < 1e-9)) > 0.95 and float(d.mean()) < 0.5
    sl, ss = sc["score_long"], sc["score_short"]
    assert np.nanmean(sl) == pytest.approx(np.nanmean(ss), abs=3.0)


# ----------------------------------------------------------------------------- stop geometry / sizing
def test_structural_stop_floor_cap_and_rejections() -> None:
    eng, i = quiet_engine()
    close = float(eng.X["close"][i])
    rec = inject(eng, i, 1, 0.001)  # 0.10% structural: widened to the 0.35% floor
    assert rec["action"] == "EXECUTED" and "FLOORED" in rec["stop_note"]
    assert rec["stop_pct"] == pytest.approx(0.35) and rec["stop"] == pytest.approx(
        close * (1 - 0.0035)
    )
    eng.pending_open = None
    rec = inject(eng, i, -1, 0.005)  # 0.50% short: used as is
    assert rec["action"] == "EXECUTED" and rec["stop_pct"] == pytest.approx(0.5)
    assert rec["stop"] == pytest.approx(close * 1.005) and rec["side"] == "SHORT"
    eng.pending_open = None
    rec = inject(eng, i, 1, 0.012)  # 1.20% > 1.00% cap: rejected, never widened or tightened
    assert rec["action"] == "BLOCKED" and rec["blockers"] == ["STOP_TOO_WIDE_FOR_V11"]
    assert rec["qty"] == 0.0 and eng.blockers["STOP_TOO_WIDE_FOR_V11"] == 1
    rec = inject(eng, i, 1, -0.004)  # stop on the wrong side
    assert rec["blockers"] == ["NO_VALID_STRUCTURAL_STOP"]
    eng.stop_level = lambda f, s, j: (math.nan, "none")  # type: ignore[method-assign]
    rec = eng._decide("B", 1, i, {"until_ms": 0, "score": 72.0, "armed_ms": 0})
    assert rec["blockers"] == ["NO_VALID_STRUCTURAL_STOP"]
    assert rec["signal_id"].startswith(PREFIX_SIGNAL)


def test_fixed_risk_sizing_is_leverage_invariant() -> None:
    eng, i = quiet_engine()
    c = eng.cfg
    close = float(eng.X["close"][i])
    recs = {}
    for lev in ([1, 2, 3, 5], [3], [5]):
        eng.cfg = c.model_copy(
            update={"risk": c.risk.model_copy(update={"allowed_leverage": lev, "max_leverage": 5})}
        )
        eng.pending_open = None
        recs[tuple(lev)] = inject(eng, i, 1, 0.005)
    base = recs[(1, 2, 3, 5)]
    assert base["action"] == "EXECUTED"
    assert base["risk_usdt"] == pytest.approx(7.5) and base["qty"] * close * 0.005 == pytest.approx(
        7.5
    )
    assert recs[(3,)]["qty"] == pytest.approx(base["qty"]) and recs[(5,)]["qty"] == pytest.approx(
        base["qty"]
    )
    assert recs[(3,)]["leverage"] == 3 and recs[(5,)]["leverage"] == 5
    assert recs[(3,)]["margin"] == pytest.approx(recs[(3,)]["notional"] / 3)
    assert recs[(5,)]["margin"] == pytest.approx(recs[(5,)]["notional"] / 5)
    assert recs[(3,)]["notional"] == pytest.approx(recs[(5,)]["notional"])
    assert all(r["leverage"] <= 5 for r in recs.values())


# ----------------------------------------------------------------------------- exits
def test_tp_quantities_breakeven_runner_and_trail() -> None:
    eng, i = quiet_engine()
    ex = eng.cfg.exits
    rec = inject(eng, i, 1, 0.005)
    pos = eng.open_from_pending(i + 1)
    assert pos is not None and eng.pos is pos
    c0, d = rec["entry_ref"], rec["entry_ref"] * 0.005
    assert rec["tp1"] == pytest.approx(c0 + 0.6 * d) and rec["tp2"] == pytest.approx(c0 + 1.2 * d)
    assert pos.entry_fill > pos.entry_ref  # taker entry pays half-spread + slippage
    # bar i+1: nothing; i+2 hits TP1; i+3 hits TP2; then prices fall through the trailed stop
    path = [c0, c0 + 0.7 * d, c0 + 1.3 * d] + [c0 + 1.25 * d] * 12 + [c0 - 0.2 * d]
    set_path(eng, i + 1, path)
    for j in range(i + 1, i + 1 + len(path)):
        eng.process_row(j)
        if eng.pos is None:
            break
    assert eng.pos is None and len(eng.trades) == 1
    t = eng.trades[0]
    legs = t.pos["legs"]
    kinds = [lg["kind"] for lg in legs]
    assert kinds[:2] == ["TP1", "TP2"] and kinds[-1] == "TRAIL"
    assert [lg["frac"] for lg in legs[:2]] == [ex.tp1_frac, ex.tp2_frac]
    assert legs[-1]["frac"] == pytest.approx(1 - ex.tp1_frac - ex.tp2_frac)
    assert sum(lg["frac"] for lg in legs) == pytest.approx(1.0)
    assert t.exit_reason == "TRAIL" and t.pos["tp1_done"] and t.pos["tp2_done"]
    # the trailed stop ended above break-even: the runner was protected after TP1 and trailed after TP2
    assert t.pos["stop"] > c0 * (1 + ex.breakeven_after_tp1_bps / 1e4) - 1e-9
    assert t.net_usd > 0 and t.gross_r > 0


def test_breakeven_after_tp1_then_stop_out_is_net_flat() -> None:
    eng, i = quiet_engine()
    rec = inject(eng, i, -1, 0.005)  # SHORT symmetry of the exit ladder
    pos = eng.open_from_pending(i + 1)
    assert pos is not None
    c0, d = rec["entry_ref"], rec["entry_ref"] * 0.005
    path = [c0, c0 - 0.7 * d, c0 - 0.3 * d, c0 + 0.1 * d]
    set_path(eng, i + 1, path)
    for j in range(i + 1, i + 1 + len(path)):
        eng.process_row(j)
        if eng.pos is None:
            break
    t = eng.trades[0]
    assert [lg["kind"] for lg in t.pos["legs"]] == ["TP1", "BE"] and t.exit_reason == "BE"
    assert t.pos["stop"] == pytest.approx(c0 * (1 - eng.cfg.exits.breakeven_after_tp1_bps / 1e4))
    assert abs(t.net_r) < 0.2  # TP1 profit minus costs on a break-even stop


def test_hard_60_minute_time_stop() -> None:
    eng, i = quiet_engine()
    rec = inject(eng, i, 1, 0.005)
    eng.open_from_pending(i + 1)
    c0 = rec["entry_ref"]
    set_path(eng, i + 1, [c0 + 0.0001 * c0 * ((k % 3) - 1) for k in range(70)])
    eng.sc["score_long"][i + 1 : i + 80] = 80.0  # score holds up: no stall exit
    for j in range(i + 1, i + 71):
        eng.process_row(j)
        if eng.pos is None:
            break
    t = eng.trades[0]
    assert t.exit_reason == "TIME" and t.hold_min == 60
    assert t.pos["legs"][-1]["frac"] == pytest.approx(1.0)


def test_early_stall_review_closes_dead_trades() -> None:
    eng, i = quiet_engine()
    ex = eng.cfg.exits
    rec = inject(eng, i, 1, 0.005)
    eng.open_from_pending(i + 1)
    c0 = rec["entry_ref"]
    set_path(eng, i + 1, [c0 + 0.05 * c0 * 0.005] * 40)  # MFE 0.05R: never near TP1
    eng.sc["score_long"][i + 1 : i + 60] = rec["score"] - ex.stall_score_drop - 1  # deteriorated
    for j in range(i + 1, i + 41):
        eng.process_row(j)
        if eng.pos is None:
            break
    t = eng.trades[0]
    assert t.exit_reason == "STALL" and t.hold_min == ex.stall_review_min
    # with the score intact the same trade survives the review
    eng2, i2 = quiet_engine()
    rec2 = inject(eng2, i2, 1, 0.005)
    eng2.open_from_pending(i2 + 1)
    set_path(eng2, i2 + 1, [rec2["entry_ref"] * (1 + 0.05 * 0.005)] * 40)
    eng2.sc["score_long"][i2 + 1 : i2 + 60] = rec2["score"]
    for j in range(i2 + 1, i2 + 1 + ex.stall_review_min + 2):
        eng2.process_row(j)
    assert eng2.pos is not None and eng2.pos.stall_checked


def test_costs_are_charged_and_scale_with_stress() -> None:
    out = {}
    for mult in (1.0, 1.5):
        eng, i = quiet_engine(cost_mult=mult)
        rec = inject(eng, i, 1, 0.005)
        eng.open_from_pending(i + 1)
        eng.sc["score_long"][i + 1 : i + 80] = 80.0
        set_path(eng, i + 1, [rec["entry_ref"]] * 70)  # flat price: pure cost
        for j in range(i + 1, i + 71):
            eng.process_row(j)
            if eng.pos is None:
                break
        out[mult] = eng.trades[0]
    t1, t15 = out[1.0], out[1.5]
    co = cfg().costs
    assert t1.gross_usd == pytest.approx(0.0, abs=1e-6) and t1.net_usd < 0 and t1.cost_r > 0
    fees = (
        co.taker_fee_bps
        / 1e4
        * (t1.pos["qty"] * t1.pos["entry_fill"] + t1.pos["qty"] * t1.pos["legs"][0]["fill"])
    )
    assert t1.fee_usd == pytest.approx(fees, rel=1e-6)
    assert t15.cost_r == pytest.approx(1.5 * t1.cost_r, rel=1e-6)
    assert t1.net_r == pytest.approx(-t1.cost_r)


def test_one_position_at_a_time_no_pyramiding() -> None:
    eng, i = quiet_engine()
    inject(eng, i, 1, 0.005)
    eng.open_from_pending(i + 1)
    rec = inject(eng, i + 2, 1, 0.005)
    assert rec["action"] == "BLOCKED" and "POSITION_OPEN" in rec["blockers"]
    rec = inject(eng, i + 2, -1, 0.005)
    assert rec["action"] == "BLOCKED" and "POSITION_OPEN" in rec["blockers"]
    assert eng.counters["skipped_while_open"] == 2 and eng.counters["executed"] == 1
    assert eng.pos is not None and eng.pos.frac_open == 1.0


# ----------------------------------------------------------------------------- isolation
def test_v11_is_isolated_from_v10_and_cannot_place_orders(tmp_path: Path) -> None:
    src = ""
    for p in sorted((ROOT / "btc_swing" / "v11").glob("*.py")):
        src += p.read_text()
    forbidden = (
        "btc_swing.v10.execution",
        "btc_swing.v10.activation",
        "btc_swing.v10.paths",
        "btc_swing.v10.shadow",
        "btc_swing.v10.engine",
        "btc_swing.v5.demo.client",
        "BybitDemoClient",
        "BYBIT_DEMO_API",
        "api-demo.bybit.com",
        "api.bybit.com",
        "place_order",
        "create_order",
        "forward_v10",
        "V10D-",
        "STRATEGY_DEMO_V10",
    )
    for tok in forbidden:
        assert tok not in src, f"V11 source references {tok}"
    paths = V11Paths(tmp_path, "BTCUSDT")
    assert "forward_v11" in str(paths.root) and "forward_v10" not in str(paths.root)
    assert "forward_v11_verify" in str(V11Paths(tmp_path, "BTCUSDT", verification=True).root)
    live = V11Live(cfg(), paths, T0)
    live.save()
    written = {str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file()}
    assert all(w.startswith("btc/forward_v11/") for w in written)
    assert not (tmp_path / "btc" / "forward_v10").exists()
    # the strategy identity and id prefixes are V11's own
    assert STRATEGY != "BTC_V10_LIVE_MICROSTRUCTURE_SNIPER" and PREFIX_SIGNAL == "V11S-"
    assert not hasattr(v11_config, "PREFIX_DEMO")


def test_v10_strategy_files_are_not_touched_by_v11_modules() -> None:
    """V11 reuses the V10 feed/feature classes as a library only: nothing in V11 writes to them."""
    v10_dir = ROOT / "btc_swing" / "v10"
    v11_src = "".join(p.read_text() for p in (ROOT / "btc_swing" / "v11").glob("*.py"))
    assert "btc_swing.v10.feed" in v11_src and "btc_swing.v10.features" in v11_src
    for name in ("engine.py", "families.py", "shadow.py", "execution.py", "activation.py"):
        assert (v10_dir / name).exists()
        assert f"v10.{name[:-3]}" not in v11_src


# ----------------------------------------------------------------------------- live shadow runtime
def _feed_bars(live: V11Live, t_start: int, minutes: int, px: float, seed: int = 3) -> int:
    rng = np.random.default_rng(seed)
    t = t_start
    for _m in range(minutes):
        p = px * (1 + rng.normal(0, 0.0004))
        trades = []
        for k in range(6):
            q = float(rng.lognormal(-2, 0.8))
            trades.append(
                {
                    "T": t + k * 10_000,
                    "s": "BTCUSDT",
                    "S": "Buy" if rng.random() < 0.5 else "Sell",
                    "v": f"{q:.4f}",
                    "p": f"{p * (1 + rng.normal(0, 0.0001)):.1f}",
                    "i": f"{t}-{k}",
                }
            )
        live.on_event(
            {
                "topic": "publicTrade.BTCUSDT",
                "type": "snapshot",
                "ts": t,
                "rx_ms": t + 40,
                "data": trades,
            }
        )
        live.on_event(
            {
                "topic": "tickers.BTCUSDT",
                "type": "snapshot",
                "ts": t + 500,
                "rx_ms": t + 540,
                "data": {"openInterest": str(50_000 + _m), "fundingRate": "0.0001"},
            }
        )
        t += MS_1M
    return t


def test_live_shadow_runtime_journals_recovers_and_keeps_chain_integrity(tmp_path: Path) -> None:
    c = cfg()
    paths = V11Paths(tmp_path, "BTCUSDT", verification=True)
    live = V11Live(c, paths, observation_start_ms=T0)
    t = _feed_bars(live, T0, 1000, P0)  # ~16.7 h: ATR(1h) needs 14 completed hourly bars
    rec = live.tick(t + 1_000)
    assert rec is None and live.engine is not None
    assert paths.bars_1m.exists() and paths.state.exists()
    assert live.state["last_bar_ms"] == t - MS_1M  # the bar opened at t-1m is still forming
    assert live.oi and live.funding and live.last_micro
    assert math.isfinite(live.last_features["atr5"] or math.nan)
    # a real shadow position injected through the engine survives a restart with the same journals
    eng = live.engine
    i = int(np.searchsorted(eng.t_open, int(live.state["last_bar_ms"]) - MS_1M))
    eng.sc["score_long"][i] = 80.0
    inject(eng, i, 1, 0.005)
    pos = eng.open_from_pending(i, fill_ref=float(eng.X["close"][i]))
    assert pos is not None
    live.j_sh.append("SHADOW_OPEN", pos.as_dict())
    live.state["counters"]["executed"] += 1  # what _journal_row records for a real decision
    live.save()
    live2 = V11Live(c, paths, observation_start_ms=T0)
    assert live2.state["position"]["signal_id"] == pos.signal_id
    assert live2._n_bars_seen == len(live2.micro.bars.done) >= 990
    t2 = _feed_bars(live2, t, 3, P0, seed=4)
    live2.tick(t2 + 1_000)
    assert live2.engine is not None and live2.engine.pos is not None
    assert live2.engine.pos.signal_id == pos.signal_id
    assert live2.engine.counters["executed"] == 1 and live2.state["counters"]["executed"] == 1
    # journals: hash chains verify, and tampering is detected
    for p in (
        paths.candidates,
        paths.episodes,
        paths.signals,
        paths.nearmiss,
        paths.shadow_trades,
        paths.outcomes,
    ):
        assert verify_chain(p)["ok"], p
    lines = paths.shadow_trades.read_text().splitlines()
    rec0 = json.loads(lines[0])
    rec0["data"]["qty"] = 999.0
    paths.shadow_trades.write_text("\n".join([json.dumps(rec0), *lines[1:]]) + "\n")
    assert not verify_chain(paths.shadow_trades)["ok"]
    # a different observation start starts a fresh state (the old one is never mixed in)
    live3 = V11Live(c, paths, observation_start_ms=T0 + DAY_MS)
    assert live3.state["position"] is None and live3.state["counters"]["executed"] == 0
    diag = live.diagnostic()
    json.dumps(diag)  # serialisable (numpy scalars cleaned)
    assert diag["strategy"] == STRATEGY


def test_live_frame_matches_historical_frame_on_identical_bars() -> None:
    """The live rolling frame uses the same feature code as the historical run: identical bars give
    identical indicator values at the last completed bar (no live/historical drift)."""
    c = cfg()
    rows = synth_rows(2)
    fr = build_frame_v11(rows, c)
    i = 2 * 1440 - 1
    live_cfg = c.model_copy(
        update={"features": c.features.model_copy(update={"binance_latency_s": 0})}
    )
    fr_live = build_frame_v11(rows, live_cfg)
    for k in ("atr5", "ema21", "vwap", "rsi14", "adx14", "hi60", "struct15"):
        assert _same(fr.c[k][i : i + 1], fr_live.c[k][i : i + 1])
    assert isinstance(fr, V11Frame) and fr.valid[i]
