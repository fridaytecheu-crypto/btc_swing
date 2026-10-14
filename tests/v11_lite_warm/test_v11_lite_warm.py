"""V11-LITE-WARM: the warm-started V11 shadow with the A/B/C family structure as labels. Synthetic data
only; no network; nothing under a V10 / V11 / V11-WARM forward root, freeze or journal is touched
(proved by hashes). Equivalence: LITE differs from V11-WARM ONLY because A/B/C are labels, not gates."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from btc_swing.forward_report.generations import spec_v11lw, spec_v11w
from btc_swing.forward_report.spec import GenerationSpec
from btc_swing.v5.demo.journal import HashChainJournal, verify_chain
from btc_swing.v11.config import load_v11_config
from btc_swing.v11.engine import FAMILIES, MS_1M, V11Engine
from btc_swing.v11.features import build_frame_v11
from btc_swing.v11.scoring import score_frame
from btc_swing.v11_lite_warm.compare import compare, render_compare
from btc_swing.v11_lite_warm.config import (
    PREFIX_SIGNAL,
    STRATEGY,
    STRATEGY_DELTA,
    frozen_v11_strategy_hash,
    lite_strategy_hash,
    load_v11lw_config,
)
from btc_swing.v11_lite_warm.context import (
    confirmed_swings,
    context_at,
    fib_bucket,
    fibonacci,
    range_position,
    structure_detail,
)
from btc_swing.v11_lite_warm.engine import V11LiteEngine
from btc_swing.v11_lite_warm.freeze import check_freeze, write_freeze
from btc_swing.v11_lite_warm.live import V11LiteWarmLive
from btc_swing.v11_lite_warm.paths import V11LiteWarmPaths
from btc_swing.v11_warm.bootstrap import write_bootstrap_bars
from btc_swing.v11_warm.config import load_v11w_config
from btc_swing.v11_warm.live import V11WarmLive
from btc_swing.v11_warm.paths import V11WarmPaths
from tests.v11_warm.test_v11_warm import _bars_df, _feed, synth_rows

ROOT = Path(__file__).resolve().parents[2]
CFG = ROOT / "config" / "btc_swing_v11_lite_warm.yaml"
CFG_W = ROOT / "config" / "btc_swing_v11_warm.yaml"
T0 = 1_800_000_000_000 - (1_800_000_000_000 % 86_400_000)
PROTECTED = [
    ROOT / "manifests" / "v10_forward_freeze.json",
    ROOT / "manifests" / "v11_forward_freeze.json",
    ROOT / "manifests" / "v11_warm_forward_freeze.json",
    ROOT / "manifests" / "v11_warm_bootstrap_1m.parquet",
    ROOT / "manifests" / "v11_warm_bootstrap_manifest.json",
    ROOT / "config" / "btc_swing_v10.yaml",
    ROOT / "config" / "btc_swing_v11.yaml",
    ROOT / "config" / "btc_swing_v11_warm.yaml",
    *sorted((ROOT / "btc_swing" / "v10").glob("*.py")),
    *sorted((ROOT / "btc_swing" / "v11").glob("*.py")),
    *sorted((ROOT / "btc_swing" / "v11_warm").glob("*.py")),
]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _protected_hashes() -> dict[str, str]:
    return {str(p): _sha(p) for p in PROTECTED if p.exists()}


def _frame_and_scores(rows: pl.DataFrame) -> tuple[Any, dict[str, Any]]:
    v11 = load_v11_config(ROOT / "config" / "btc_swing_v11.yaml")
    fr = build_frame_v11(
        rows,
        v11.model_copy(
            update={"features": v11.features.model_copy(update={"binance_latency_s": 0})}
        ),
    )
    return fr, score_frame(fr, v11)


# ----------------------------------------------------------------------------- config / delta
def test_config_is_the_frozen_v11_strategy_plus_the_labels_only_delta() -> None:
    lc, v11 = load_v11lw_config(CFG)
    ref = load_v11_config(ROOT / "config" / "btc_swing_v11.yaml")
    wc, _ = load_v11w_config(CFG_W)
    assert lc.strategy_name == STRATEGY
    assert v11.strategy_hash == ref.strategy_hash == frozen_v11_strategy_hash()
    assert lc.warm == wc.warm  # byte-for-byte the V11-WARM warm-start section
    assert lc.lite.family_structure == "labels_only"
    assert list(v11.outcomes.horizons_min) == [5, 15, 30, 60, 120]
    assert ref.outcomes.horizons_min == [5, 15, 30, 45, 60]  # V11's own file is untouched
    # outcomes is not a strategy section: same V11 strategy hash, different config hash
    assert v11.config_hash != ref.config_hash
    assert lite_strategy_hash(v11, lc) != v11.strategy_hash
    assert "labels" in STRATEGY_DELTA


# ----------------------------------------------------------------------------- engine equivalence
class _LiteWithGatesRestored(V11LiteEngine):
    """LITE with the ONE delta reverted: must behave exactly like the frozen V11 engine."""

    def structure(self, fam: str, s: int, i: int) -> bool:
        return V11Engine.structure(self, fam, s, i)


def _run(engine_cls: type[V11Engine], rows: pl.DataFrame, start_ms: int) -> V11Engine:
    fr, sc = _frame_and_scores(rows)
    v11 = load_v11_config(ROOT / "config" / "btc_swing_v11.yaml")
    eng = engine_cls(v11, fr, sc)
    eng.run(start_ms, int(rows["open_time_ms"][-1]) + MS_1M)
    return eng


def test_lite_engine_with_gates_restored_equals_the_frozen_v11_engine() -> None:
    """Everything except `structure` is inherited unchanged: reverting the one override reproduces
    V11 trade for trade (signal ids differ only by the V11LWS- prefix)."""
    rows = synth_rows(25, seed=23)
    start = int(rows["open_time_ms"][22 * 1440])
    ref = _run(V11Engine, rows, start)
    same = _run(_LiteWithGatesRestored, rows, start)
    assert ref.counters == same.counters and ref.trades and ref.decisions
    assert [t.as_dict()["exit_ms"] for t in ref.trades] == [
        t.as_dict()["exit_ms"] for t in same.trades
    ]
    assert [t.net_r for t in ref.trades] == [t.net_r for t in same.trades]
    assert [d["decision_ms"] for d in ref.decisions] == [d["decision_ms"] for d in same.decisions]
    for a, b in zip(ref.decisions, same.decisions, strict=True):
        assert b["signal_id"] == PREFIX_SIGNAL + a["signal_id"].removeprefix("V11S-")
        assert {k: v for k, v in a.items() if k != "signal_id"} == {
            k: v
            for k, v in b.items()
            if k not in ("signal_id", "family_labels", "family_structure_label")
        }


def test_lite_differs_from_v11_only_by_the_structure_gate() -> None:
    """On every row: (1) the LITE labels equal the frozen V11 structure conditions; (2) LITE arms a
    (family, side) exactly when V11 would arm it without the structure condition; (3) wherever V11 is
    armed, LITE is armed too (superset); (4) trigger and stop geometry are identical functions."""
    rows = synth_rows(24, seed=11)
    fr, sc = _frame_and_scores(rows)
    v11 = load_v11_config(ROOT / "config" / "btc_swing_v11.yaml")
    ref, lite = V11Engine(v11, fr, sc), V11LiteEngine(v11, fr, sc)
    i0 = 22 * 1440
    n_label_true = n_label_false = 0
    for i in range(i0, len(fr)):
        labels = lite.family_labels(i)
        for fam in FAMILIES:
            for s, side in ((1, "LONG"), (-1, "SHORT")):
                assert labels[fam][side] == ref.structure(fam, s, i)
                n_label_true += labels[fam][side]
                n_label_false += not labels[fam][side]
                c, a5 = fr.c["close"][i], fr.c["atr5"][i]
                valid = bool(np.isfinite(c) and np.isfinite(a5) and a5 > 0)
                assert lite.structure(fam, s, i) == valid
                assert lite.trigger(fam, s, i) == ref.trigger(fam, s, i)
                assert lite.stop_level(fam, s, i) == ref.stop_level(fam, s, i)
    assert n_label_true > 0 and n_label_false > 0
    # arming superset while both are flat (position state may diverge once LITE trades)
    ref2, lite2 = V11Engine(v11, fr, sc), V11LiteEngine(v11, fr, sc)
    checked = 0
    for i in range(i0, len(fr)):
        ref2.process_row(i, True)
        lite2.process_row(i, True)
        ref2.pending_open = lite2.pending_open = None
        if ref2.pos is None and lite2.pos is None:
            assert set(ref2.armed) <= set(lite2.armed)
            checked += 1
    assert checked > 1000 and lite2.counters["armed"] >= ref2.counters["armed"]


def test_lite_signal_records_carry_labels_and_prefix() -> None:
    rows = synth_rows(24, seed=5)
    lite = _run(V11LiteEngine, rows, int(rows["open_time_ms"][22 * 1440]))
    assert lite.decisions, "synthetic series produced no LITE decision"
    for d in lite.decisions:
        assert d["signal_id"].startswith(PREFIX_SIGNAL)
        assert set(d["family_labels"]) == {"A", "B", "C"}
        assert d["family_structure_label"] == d["family_labels"][d["family"]][d["side"]]


# ----------------------------------------------------------------------------- context telemetry (PIT)
def test_fibonacci_levels_and_direction_on_a_hand_made_swing() -> None:
    tf, k = 5, 2
    n = tf * 40
    high = np.full(n, 100.0)
    low = np.full(n, 99.0)
    # swing low at 5m-bar 10 (low 90), swing high at 5m-bar 20 (high 110): last leg UP
    low[10 * tf : 11 * tf] = 90.0
    high[20 * tf : 21 * tf] = 110.0
    sw = confirmed_swings(high, low, k, tf, n - 1, 10_000)
    assert sw is not None and sw["direction"] == "UP"
    assert sw["swing_high"] == 110.0 and sw["swing_low"] == 90.0
    fib = fibonacci(100.0, 1.0, sw, [0.382, 0.5, 0.618])
    assert fib["available"] and math.isclose(fib["levels"]["0.500"], 100.0)
    assert math.isclose(fib["levels"]["0.382"], 110 - 0.382 * 20)
    assert fib["nearest_ratio"] == "0.500" and math.isclose(fib["nearest_abs_dist_atr"], 0.0)
    assert math.isclose(fib["retrace_frac"], 0.5)
    assert fib_bucket(0.05, [0.1, 0.25, 0.5]) == "<= 0.10"
    assert fib_bucket(0.3, [0.1, 0.25, 0.5]) == "0.25-0.50"
    assert fib_bucket(0.9, [0.1, 0.25, 0.5]) == "> 0.50"
    assert fib_bucket(None, [0.1, 0.25, 0.5]) == "N/A"
    # not confirmable before k later tf-bars closed: the swing high at bar 20 is unknown at bar 21
    sw_early = confirmed_swings(high, low, k, tf, 21 * tf + tf - 1, 10_000)
    assert sw_early is None or sw_early["swing_high"] != 110.0
    assert fibonacci(100.0, 1.0, None, [0.5]) == {"available": False}


def test_context_is_point_in_time() -> None:
    """Context at row i never changes when bars after i change."""
    lc, v11 = load_v11lw_config(CFG)
    rows = synth_rows(23, seed=9)
    fr, _ = _frame_and_scores(rows)
    i = 22 * 1440 + 200
    a = context_at(v11, lc.lite, fr.c, i, {"depth_imb": 0.1})
    x2 = {k: v.copy() for k, v in fr.c.items()}
    for k in ("high", "low", "close", "open", "volume"):
        x2[k][i + 1 :] *= 1.5
    b = context_at(v11, lc.lite, x2, i, {"depth_imb": 0.1})
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
    assert (
        a["fib"]["available"] and a["range"]["60m"]["available"] and a["range"]["240m"]["available"]
    )
    assert 0.0 <= a["range"]["60m"]["position"] <= 1.0
    assert a["structure_detail"]["A"]["LONG"].keys() == {
        "data_valid",
        "impulse_pullback",
        "struct15_aligned",
        "turn",
    }
    assert range_position(fr.c["high"], fr.c["low"], float(fr.c["close"][100]), 100, 240) == {
        "available": False
    }


def test_structure_detail_matches_the_frozen_v11_structure() -> None:
    rows = synth_rows(23, seed=13)
    fr, sc = _frame_and_scores(rows)
    v11 = load_v11_config(ROOT / "config" / "btc_swing_v11.yaml")
    ref = V11Engine(v11, fr, sc)
    for i in range(22 * 1440, len(fr), 7):
        for fam in FAMILIES:
            for s in (1, -1):
                d = structure_detail(v11, fr.c, fam, s, i)
                assert all(d.values()) == ref.structure(fam, s, i), (i, fam, s, d)


# ----------------------------------------------------------------------------- live equivalence with V11-WARM
def _two_lives(
    tmp_path: Path, rows: pl.DataFrame, n_hist: int, warmup_min: int = 0
) -> tuple[V11WarmLive, V11LiteWarmLive]:
    lc, v11 = load_v11lw_config(CFG)
    wc, v11w = load_v11w_config(CFG_W)
    hist = _bars_df(rows[:n_hist])
    pw = V11WarmPaths(tmp_path / "w", "BTCUSDT", verification=True)
    pw.ensure()
    write_bootstrap_bars(hist, pw.bars_1m)
    pl_ = V11LiteWarmPaths(tmp_path / "l", "BTCUSDT", verification=True)
    pl_.ensure()
    write_bootstrap_bars(hist, pl_.bars_1m)
    obs = int(rows["open_time_ms"][n_hist]) + warmup_min * MS_1M
    warm = V11WarmLive(v11w, pw, obs, wc.warm.model_copy(update={"live_warmup_min": warmup_min}))
    lite = V11LiteWarmLive(
        v11, pl_, obs, lc.warm.model_copy(update={"live_warmup_min": warmup_min}), lc.lite
    )
    return warm, lite


def test_live_lite_shares_features_scores_and_its_control_twin_reproduces_v11_warm(
    tmp_path: Path,
) -> None:
    """Same warmed history, same live bars: identical indicators and scores every minute; the LITE
    in-process strict twin makes exactly V11-WARM's decisions; LITE journals labels, context and the
    control attribution; LITE armed keys are a superset of V11-WARM's while both are flat."""
    rows = synth_rows(22, seed=3)
    n_hist = 21 * 1440
    warm, lite = _two_lives(tmp_path, rows, n_hist)
    warm_recs: list[tuple[Any, ...]] = []
    twin_recs: list[tuple[Any, ...]] = []
    n_ticks = 0
    for i in range(n_hist, n_hist + 150):
        t_end = _feed(warm, rows, i, i + 1)
        rw = warm.tick(t_end + 1_000)
        t_end = _feed(lite, rows, i, i + 1)
        rl = lite.tick(t_end + 1_000)
        if not lite.last_scores:
            continue
        n_ticks += 1
        assert warm.last_scores == lite.last_scores
        assert {k: v for k, v in warm.last_features.items()} == {
            k: v for k, v in lite.last_features.items()
        }
        if rw is not None:
            warm_recs.append(
                (rw["decision_ms"], rw["family"], rw["side"], rw["action"], rw["stop"], rw["qty"])
            )
        if rl is not None:
            assert (
                rl["signal_id"].startswith(PREFIX_SIGNAL)
                and "control_twin" in rl
                and "context" in rl
            )
            assert rl["family_structure_label"] == rl["family_labels"][rl["family"]][rl["side"]]
        if (
            warm.engine
            and lite.engine
            and warm.engine.pos is None
            and lite.engine.pos is None
            and lite.twin
            and lite.twin.pos is None
        ):
            assert set(warm.engine.armed) <= set(lite.engine.armed)
    assert n_ticks > 120
    lp = lite.paths
    assert isinstance(lp, V11LiteWarmPaths)
    twin = (
        [json.loads(line) for line in lp.control_twin.read_text().splitlines()]
        if lp.control_twin.exists()
        else []
    )
    for t in twin:
        if t["kind"] in ("TWIN_EXECUTED", "TWIN_BLOCKED"):
            d = t["data"]
            twin_recs.append(
                (d["decision_ms"], d["family"], d["side"], d["action"], d["stop"], d["qty"])
            )
    assert twin_recs == warm_recs
    assert lite.state["twin"]["counters"]["executed"] == warm.state["counters"]["executed"]
    if (
        lp.candidates.exists()
    ):  # labels + context ride on every candidate (content proved in the replay)
        cands = [json.loads(line)["data"] for line in lp.candidates.read_text().splitlines()]
        assert all(
            set(c["family_labels"]) == {"A", "B", "C"} and "fib" in c["context"] for c in cands
        )
    assert lite.state["counters"]["candidates"] == warm.state["counters"]["candidates"]
    for n in ("candidates", "episodes", "signals", "shadow_trades", "outcomes", "control_twin"):
        p = getattr(lite.paths, n)
        if p.exists():
            assert verify_chain(p)["ok"], n
    d = lite.diagnostic()
    assert (
        d["strategy"] == STRATEGY
        and d["family_labels"]
        and d["context"]["fib"]
        and d["control_twin"]
    )


def test_step_replay_twin_equals_frozen_v11_and_incremental_signals_are_attributed(
    tmp_path: Path,
) -> None:
    """Replaying the live `_step` (the exact tick code path after the frame exists) over two days:
    the control twin's decisions and trades equal a standalone frozen V11Engine driven the live way;
    every LITE signal carries labels, context and a control attribution whose `incremental` flag is
    consistent with the twin's decision; incremental signals name the failed structure condition."""
    lc, v11 = load_v11lw_config(CFG)
    rows = synth_rows(25, seed=23)
    fr, sc = _frame_and_scores(rows)
    n_hist = 22 * 1440
    paths = V11LiteWarmPaths(tmp_path, "BTCUSDT", verification=True)
    paths.ensure()
    write_bootstrap_bars(_bars_df(rows[:n_hist]), paths.bars_1m)
    obs = int(rows["open_time_ms"][n_hist])
    lite = V11LiteWarmLive(
        v11, paths, obs, lc.warm.model_copy(update={"live_warmup_min": 0}), lc.lite
    )
    ref = V11Engine(v11, fr, sc)
    ref_recs: list[tuple[Any, ...]] = []
    sigs: list[dict[str, Any]] = []
    for i in range(n_hist, len(fr)):
        t_close = int(fr.t_open[i]) + MS_1M
        r = ref.process_row(i, in_window=t_close > obs)
        if ref.pending_open is not None:
            p = ref.open_from_pending(i, fill_ref=float(fr.c["close"][i]))
            if p is not None:
                p.entry_ms = t_close
        if r is not None:
            ref_recs.append(
                (r["decision_ms"], r["family"], r["side"], r["action"], r["stop"], r["qty"])
            )
        rl = lite._step(fr, sc, i)
        if rl is not None:
            sigs.append(rl)
    twin_recs = [
        (d["decision_ms"], d["family"], d["side"], d["action"], d["stop"], d["qty"])
        for d in (json.loads(line)["data"] for line in paths.control_twin.read_text().splitlines())
        if d.get("decision_ms") is not None and "exit_ms" not in d and "entry_fill" not in d
    ]
    assert twin_recs == ref_recs and ref_recs, "twin must reproduce the frozen V11 decisions"
    assert [t.net_r for t in ref.trades][-10:] == [
        json.loads(line)["data"]["net_r"]
        for line in paths.control_twin.read_text().splitlines()
        if json.loads(line)["kind"] == "TWIN_CLOSED"
    ][-10:]
    assert sigs and len(sigs) >= len(ref_recs), "LITE (no structure gate) decides at least as often"
    n_inc = 0
    for s_ in sigs:
        assert s_["signal_id"].startswith(PREFIX_SIGNAL)
        ctl = s_["control_twin"]
        assert s_["incremental"] == (not ctl["would_take"])
        assert "fib" in s_["context"] and set(s_["family_labels"]) == {"A", "B", "C"}
        if s_["incremental"]:
            n_inc += 1
            assert ctl["reason"].startswith(("STRUCTURE_", "CONTROL_")), ctl["reason"]
            if ctl["reason"].startswith("STRUCTURE_") and "_FALSE" in ctl["reason"]:
                assert ctl["failed_conditions"] and not ctl["family_structure_label"]
        else:
            assert ctl["twin_decision"] is not None and ctl["twin_decision"]["side"] == s_["side"]
    assert n_inc > 0, "the synthetic series produced no incremental LITE signal"
    assert lite.twin is not None and lite.twin.counters["executed"] == ref.counters["executed"]
    for n in ("candidates", "episodes", "signals", "shadow_trades", "control_twin"):
        p_ = getattr(paths, n)
        if p_.exists():
            assert verify_chain(p_)["ok"], n


def test_engine_is_not_in_window_before_live_warmup(tmp_path: Path) -> None:
    rows = synth_rows(22, seed=4)
    n_hist = 21 * 1440
    _warm, lite = _two_lives(tmp_path, rows, n_hist, warmup_min=30)
    for i in range(n_hist, n_hist + 20):
        t_end = _feed(lite, rows, i, i + 1)
        assert lite.tick(t_end + 1_000) is None
    assert lite.state["counters"]["candidates"] == 0 and not lite.paths.candidates.exists()
    assert lite.twin is not None and lite.twin.counters["candidates"] == 0


def test_state_round_trip_restores_engine_and_twin(tmp_path: Path) -> None:
    rows = synth_rows(22, seed=8)
    n_hist = 21 * 1440
    _warm, lite = _two_lives(tmp_path, rows, n_hist)
    for i in range(n_hist, n_hist + 60):
        t_end = _feed(lite, rows, i, i + 1)
        lite.tick(t_end + 1_000)
    lite.save()
    lc, v11 = load_v11lw_config(CFG)
    lp = lite.paths
    assert isinstance(lp, V11LiteWarmPaths)
    again = V11LiteWarmLive(
        v11,
        lp,
        lite.observation_start_ms,
        lc.warm.model_copy(update={"live_warmup_min": 0}),
        lc.lite,
    )
    assert again.state["counters"] == lite.state["counters"]
    assert again.state["twin"]["counters"] == lite.state["twin"]["counters"]
    assert again.state["strategy"] == STRATEGY and again.state["strategy_delta"] == STRATEGY_DELTA


# ----------------------------------------------------------------------------- freeze / isolation / integrity
def test_freeze_pins_v11_strategy_lite_delta_and_checkpoints(tmp_path: Path) -> None:
    lc, v11 = load_v11lw_config(CFG)
    before = _protected_hashes()
    rec = write_freeze(lc, v11, None, tmp_path / "fz.json")
    assert rec["v11_strategy_hash"] == frozen_v11_strategy_hash() == v11.strategy_hash
    assert rec["lite_strategy_hash"] == lite_strategy_hash(v11, lc)
    assert rec["observation_start_ms"] == rec["frozen_ms"] + lc.warm.live_warmup_min * 60_000
    assert [c["name"] for c in rec["checkpoints"]] == [
        "checkpoint_1",
        "checkpoint_2",
        "checkpoint_3",
    ]
    assert rec["outcome_horizons_min"] == [5, 15, 30, 60, 120]
    assert "no order placement" in rec["mode"]
    assert (
        check_freeze(lc, v11, tmp_path / "fz.json")["lite_strategy_hash"]
        == rec["lite_strategy_hash"]
    )
    other = lc.model_copy(update={"lite": lc.lite.model_copy(update={"outcome_horizons_min": [5]})})
    with pytest.raises(RuntimeError):
        check_freeze(other, v11, tmp_path / "fz.json")
    assert _protected_hashes() == before


def test_isolation_from_v10_v11_and_v11_warm(tmp_path: Path) -> None:
    src = "".join(
        p.read_text() for p in sorted((ROOT / "btc_swing" / "v11_lite_warm").glob("*.py"))
    )
    for tok in (
        "btc_swing.v10.execution",
        "btc_swing.v10.activation",
        "btc_swing.v10.paths",
        "btc_swing.v10.engine",
        "btc_swing.v10.shadow",
        "btc_swing.v11.freeze",
        "btc_swing.v11.runner",
        "btc_swing.v11_warm.freeze.write_freeze",
        "btc_swing.v11_warm.runner",
        "btc_swing.v5.demo.client",
        "BybitDemoClient",
        "BYBIT_DEMO_API",
        "api-demo",
        "place_order",
        "create_order",
        "forward_v10",
        "STRATEGY_DEMO",
        "V10D-",
        "V11D-",
        "V11LWD-",
    ):
        assert tok not in src, f"V11-LITE-WARM source references {tok}"
    assert '"forward_v11"' not in src and '"forward_v11_warm"' not in src
    paths = V11LiteWarmPaths(tmp_path, "BTCUSDT")
    assert paths.root.name == "forward_v11_lite_warm"
    assert paths.lock != V11WarmPaths(tmp_path, "BTCUSDT").lock
    assert "forward_v11_lite_warm_verify" in str(
        V11LiteWarmPaths(tmp_path, "BTCUSDT", verification=True).root
    )
    before = _protected_hashes()
    lc, v11 = load_v11lw_config(CFG)
    live = V11LiteWarmLive(v11, paths, T0, lc.warm, lc.lite)
    live.save()
    written = {str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file()}
    assert written and all(w.startswith("btc/forward_v11_lite_warm/") for w in written)
    assert _protected_hashes() == before


def test_committed_freezes_and_journals_of_other_generations_are_untouched_by_lite_module_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loading, seeding, running, freezing and reporting LITE leaves every protected file (V10 / V11 /
    V11-WARM code, configs, freezes, bootstrap) byte-identical and writes nothing under another root."""
    before = _protected_hashes()
    data = tmp_path / "data"
    other_roots = [data / "btc" / r for r in ("forward_v10", "forward_v11", "forward_v11_warm")]
    for r in other_roots:
        (r / "journals").mkdir(parents=True)
        (r / "journals" / "signals.jsonl").write_text('{"kind":"X","data":{}}\n')
    other_before = {str(p): _sha(p) for r in other_roots for p in r.rglob("*") if p.is_file()}
    monkeypatch.setenv("BTC_DATA_DIR", str(data))
    monkeypatch.chdir(ROOT)
    from btc_swing.core.settings import get_settings

    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None
    rows = synth_rows(22, seed=2)
    n_hist = 21 * 1440
    lc, v11 = load_v11lw_config(CFG)
    paths = V11LiteWarmPaths(data, "BTCUSDT", verification=True)
    paths.ensure()
    write_bootstrap_bars(_bars_df(rows[:n_hist]), paths.bars_1m)
    live = V11LiteWarmLive(
        v11,
        paths,
        int(rows["open_time_ms"][n_hist]),
        lc.warm.model_copy(update={"live_warmup_min": 0}),
        lc.lite,
    )
    for i in range(n_hist, n_hist + 30):
        live.tick(_feed(live, rows, i, i + 1) + 1_000)
    live.save()
    write_freeze(lc, v11, None, tmp_path / "fz.json")
    assert _protected_hashes() == before
    assert {
        str(p): _sha(p) for r in other_roots for p in r.rglob("*") if p.is_file()
    } == other_before
    assert not (data / "btc" / "forward_v11_lite_warm").exists()  # verification root only


# ----------------------------------------------------------------------------- comparison report
def _synthetic_pair(root: Path) -> tuple[GenerationSpec, GenerationSpec]:
    t0 = 1_791_900_000_000
    specs = []
    for name, lite_flag in (("v11w", False), ("v11lw", True)):
        r = root / name
        j = r / "journals"
        j.mkdir(parents=True)
        cand = HashChainJournal(j / "candidates.jsonl", "c")
        ep = HashChainJournal(j / "episodes.jsonl", "e")
        sig = HashChainJournal(j / "signals.jsonl", "s")
        sh = HashChainJournal(j / "shadow_trades.jsonl", "t")
        out = HashChainJournal(j / "outcomes.jsonl", "o")
        n_sig = 4 if lite_flag else 1
        for k in range(6):
            ctx = {
                "fib": {
                    "available": True,
                    "bucket_atr": "<= 0.10" if k % 2 else "> 0.50",
                    "nearest_ratio": "0.500",
                    "nearest_dist_bps": 3.0,
                    "nearest_abs_dist_atr": 0.05 if k % 2 else 0.9,
                }
            }
            cand.append(
                "CANDIDATE",
                {
                    "ts_ms": t0 + k * 7 * MS_1M,
                    "side": "LONG" if k % 2 else "SHORT",
                    "score": 66.0 + k,
                    "context": ctx,
                },
            )
        for k in range(3):  # three consecutive ARMED minutes = ONE arm start
            ep.append(
                "ARMED",
                {"ts_ms": t0 + (10 + k) * MS_1M, "family": "A", "side": "LONG", "score": 72.0},
            )
        for k in range(n_sig):
            sid = f"{'V11LWS' if lite_flag else 'V11S'}-{t0 + (20 + k * 30) * MS_1M}-AL"
            ctl = {
                "would_take": k == 0,
                "reason": "CONTROL_SAME_DECISION"
                if k == 0
                else "STRUCTURE_A_FALSE:struct15_aligned",
                "failed_conditions": [] if k == 0 else ["struct15_aligned"],
                "family_structure_label": k == 0,
            }
            d = {
                "signal_id": sid,
                "action": "EXECUTED",
                "family": "A",
                "side": "LONG",
                "side_sign": 1,
                "score": 76.0 + k,
                "decision_ms": t0 + (20 + k * 30) * MS_1M,
                "stop_pct": 0.5,
                "risk_usdt": 7.5,
                "contrib": {"microstructure": 0.6},
            }
            if lite_flag:
                d.update(
                    {
                        "control_twin": ctl,
                        "incremental": k != 0,
                        "family_labels": {
                            "A": {"LONG": k == 0, "SHORT": False},
                            "B": {"LONG": False, "SHORT": False},
                            "C": {"LONG": False, "SHORT": False},
                        },
                        "context": {
                            "fib": {
                                "available": True,
                                "bucket_atr": "0.10-0.25",
                                "nearest_ratio": "0.618",
                                "nearest_dist_bps": -4.0,
                                "nearest_abs_dist_atr": 0.2,
                                "direction": "UP",
                            },
                            "vwap": {"dist_bps": 5.0},
                            "range": {"60m": {"position": 0.7}, "240m": {"position": 0.4}},
                            "swings": {"swing_hi5": {"atr": 0.3}, "swing_lo5": {"atr": -1.1}},
                        },
                    }
                )
            sig.append("SIGNAL_EXECUTED", d)
            sh.append(
                "SHADOW_CLOSED",
                {
                    "signal_id": sid,
                    "family": "A",
                    "side": 1,
                    "net_usd": 3.0 if k % 2 == 0 else -2.0,
                    "net_r": 0.4 if k % 2 == 0 else -0.27,
                    "gross_r": 0.6 if k % 2 == 0 else -0.1,
                    "hold_min": 25,
                    "exit_ms": t0 + (50 + k * 30) * MS_1M,
                    "exit_reason": "TP2",
                    "risk_usdt": 7.5,
                },
            )
            for h in (5, 15, 30, 60, 120):
                out.append(
                    "OUTCOME",
                    {"id": sid, "kind": "EXECUTED", "horizon_min": h, "return_bps": 2.0 * h / 10},
                )
        state = {
            "observation_start_ms": t0,
            "last_bar_ms": t0 + 2 * 86_400_000,
            "counters": {"triggers": 0},
        }
        (r / "state.json").write_text(json.dumps(state))
        specs.append(
            GenerationSpec(
                name=name,
                label=name,
                root=r,
                reports_dir=Path("reports")
                / ("forward_v11_lite_warm" if lite_flag else "forward_v11_warm"),
                state=r / "state.json",
                freeze=r / "missing_freeze.json",
                strategy_hash="x",
                journals={
                    n: j / f"{n}.jsonl"
                    for n in (
                        "candidates",
                        "episodes",
                        "signals",
                        "nearmiss",
                        "shadow_trades",
                        "outcomes",
                    )
                },
            )
        )
    return specs[0], specs[1]


def test_compare_report_metrics_incremental_cohort_fib_buckets_and_checkpoints(
    tmp_path: Path,
) -> None:
    lc, _ = load_v11lw_config(CFG)
    ctl, lite = _synthetic_pair(tmp_path)
    before = {str(p): _sha(p) for p in tmp_path.rglob("*") if p.is_file()}
    rep = compare(ctl, lite, lc.lite, 5000.0)
    assert rep["control"]["counts"] == {
        "candidates": 6,
        "armed_records": 3,
        "armed_starts": 1,
        "triggers_counter": 0,
        "executed": 1,
        "blocked": 0,
        "shadow_closed": 1,
        "outcomes": 5,
    }
    assert (
        rep["challenger"]["counts"]["executed"] == 4
        and rep["challenger"]["counts"]["armed_starts"] == 1
    )
    assert rep["challenger"]["per_day"]["executed"] == pytest.approx(2.0)
    assert rep["delta"]["executed_per_day"] == pytest.approx(1.5)
    inc = rep["incremental_cohort"]
    assert inc["summary"]["n_signals"] == 3 and inc["summary"]["rejection_reasons"] == {
        "STRUCTURE_A_FALSE": 3
    }
    row = inc["rows"][0]
    assert row["control_rejection_reason"].startswith("STRUCTURE_A_FALSE") and row["returns_bps"][
        "120m"
    ] == pytest.approx(24.0)
    assert (
        row["fib_bucket_atr"] == "0.10-0.25"
        and row["range_pos_60m"] == 0.7
        and row["net_r"] in (0.4, -0.27)
    )
    fa = rep["fib_attribution"]
    assert (
        fa["executed_signals"]["0.10-0.25"]["n"] == 4
        and fa["executed_signals"]["0.10-0.25"]["label"] == "PRELIMINARY"
    )
    assert fa["executed_signals"]["0.10-0.25"]["ret_30m_bps"] == pytest.approx(6.0)
    assert fa["candidates"]["<= 0.10"]["n"] == 3 and fa["candidates"]["> 0.50"]["n"] == 3
    assert (
        fa["candidates"]["<= 0.10"]["ret_30m_bps"] == "N/A"
    )  # candidate outcomes absent -> N/A, never inferred
    cps = {c["name"]: c for c in rep["checkpoints"]}
    assert (
        cps["checkpoint_1"]["reached"] is False and cps["checkpoint_1"]["passed"] == "NOT_REACHED"
    )
    assert cps["checkpoint_2"]["require"]["gross_r_per_trade_gt_0"] is True
    assert cps["checkpoint_3"]["require"]["profit_factor_gt_1_1"] is True
    assert rep["challenger"]["side_adjusted_outcomes_bps"]["EXECUTED"]["120m"][
        "mean_bps"
    ] == pytest.approx(24.0)
    md = render_compare(rep)
    assert "INCREMENTAL V11-LITE COHORT" in md and "PRELIMINARY" in md and "checkpoint_3" in md
    assert {str(p): _sha(p) for p in tmp_path.rglob("*") if p.is_file()} == before  # read-only


def test_report_specs_for_lite_read_the_lite_root_and_write_only_under_its_reports_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("BTC_DATA_DIR", str(tmp_path))
    from btc_swing.core import settings as st_mod

    if hasattr(st_mod.get_settings, "cache_clear"):
        st_mod.get_settings.cache_clear()
    spec, eq = spec_v11lw(CFG, True)
    assert (
        spec.name == "v11lw" and spec.root.name == "forward_v11_lite_warm_verify" and eq == 5000.0
    )
    assert (
        spec.reports_dir == Path("reports/forward_v11_lite_warm")
        and "control_twin" in spec.journals
    )
    ctl, _ = spec_v11w(CFG_W, True)
    assert ctl.root != spec.root and ctl.freeze != spec.freeze and ctl.state != spec.state
    assert os.path.commonpath([spec.root, ctl.root]) == str(tmp_path / "btc")
