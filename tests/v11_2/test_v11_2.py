"""V11.2 counterfactual gating study: variant gates, BASE identity with frozen V11, isolation."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from btc_swing.v11.config import load_v11_config
from btc_swing.v11.data import load_inputs, ms
from btc_swing.v11.engine import MS_1M, V11Engine
from btc_swing.v11.features import build_frame_v11
from btc_swing.v11.scoring import score_frame
from btc_swing.v11_2.study import VARIANTS, GatingEngine, Variant

ROOT = Path(__file__).resolve().parents[2]
DAY = 86_400_000
T0 = 1_800_000_000_000 - (1_800_000_000_000 % DAY)


def _rows(days: int, seed: int = 3) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    n = days * 1440
    r = rng.normal(0, 0.0007, n) + 0.0005 * np.sin(np.arange(n) / 250.0)
    c = 50_000 * np.exp(np.cumsum(r))
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


@pytest.fixture(scope="module")
def frame():
    cfg = load_v11_config(ROOT / "config" / "btc_swing_v11.yaml")
    fr = build_frame_v11(_rows(25), cfg)
    return cfg, fr, score_frame(fr, cfg)


def _run(cfg, fr, sc, v=None):
    eng = V11Engine(cfg, fr, sc) if v is None else GatingEngine(cfg, fr, sc, v)
    eng.run(int(fr.t_open[22 * 1440]), int(fr.t_open[-1]))
    return eng


def test_base_variant_is_identical_to_frozen_v11(frame) -> None:
    cfg, fr, sc = frame
    ref, base = _run(cfg, fr, sc), _run(cfg, fr, sc, VARIANTS[0])
    assert VARIANTS[0].name == "V11_BASE"
    assert [t.as_dict()["signal_id"] for t in ref.trades] == [
        t.as_dict()["signal_id"] for t in base.trades
    ]
    assert [round(t.net_usd, 9) for t in ref.trades] == [round(t.net_usd, 9) for t in base.trades]
    assert ref.counters == base.counters


def test_variant_gates_are_supersets_of_the_frozen_gate(frame) -> None:
    cfg, fr, sc = frame
    base = GatingEngine(cfg, fr, sc, VARIANTS[0])
    by = {v.name: GatingEngine(cfg, fr, sc, v) for v in VARIANTS}
    rng = range(22 * 1440, len(fr) - 1)
    for s in (1, -1):
        a0 = [base.structure("A", s, i) for i in rng]
        b0 = [base.structure("B", s, i) for i in rng]
        for n in ("A1_NEUTRAL_OK", "A2_WITHIN2", "A3_WITHIN3"):
            an = [by[n].structure("A", s, i) for i in rng]
            assert all(x <= y for x, y in zip(a0, an, strict=True)), n  # relaxation only adds
        a2 = [by["A2_WITHIN2"].structure("A", s, i) for i in rng]
        a3 = [by["A3_WITHIN3"].structure("A", s, i) for i in rng]
        assert all(x <= y for x, y in zip(a2, a3, strict=True))
        prev = b0
        for n in ("B1_NEAR_040", "B2_NEAR_050", "B3_NEAR_070"):
            bn = [by[n].structure("B", s, i) for i in rng]
            assert all(x <= y for x, y in zip(prev, bn, strict=True)), n
            prev = bn
        # A variants never touch B / C; B variants never touch A / C
        assert [by["A1_NEUTRAL_OK"].structure("B", s, i) for i in rng] == b0
        assert [by["B3_NEAR_070"].structure("A", s, i) for i in rng] == a0


def test_within_k_uses_only_completed_15m_bars(frame) -> None:
    cfg, fr, sc = frame
    eng = GatingEngine(cfg, fr, sc, Variant("t", "A", "t", a_struct_mode="within2"))
    i = 23 * 1440 + 7
    X = eng.X  # noqa: N806
    X["a_struct_long"][i] = 1.0
    X["accel5"][i] = 1.0
    X["close"][i], X["atr5"][i] = 50_000.0, 50.0
    X["struct15"][i], X["struct15"][i - 15], X["struct15"][i - 30] = 0.0, 1.0, -1.0
    assert eng.structure("A", 1, i)  # previous completed 15m bar aligned
    X["struct15"][i - 15] = 0.0
    assert not eng.structure("A", 1, i)
    X["struct15"][i - 30] = 1.0
    assert not eng.structure("A", 1, i)  # 3 bars back is outside "within 2"
    assert GatingEngine(cfg, fr, sc, Variant("t3", "A", "t", a_struct_mode="within3")).structure(
        "A", 1, i
    )


def test_c_only_variants_disable_a_and_b_and_c1_ignores_score_gates(frame) -> None:
    cfg, fr, sc = frame
    c0 = GatingEngine(cfg, fr, sc, next(v for v in VARIANTS if v.name == "C0_C_ONLY"))
    c1 = GatingEngine(cfg, fr, sc, next(v for v in VARIANTS if v.name == "C1_C_ONLY_NO_SCORE"))
    i = 23 * 1440
    assert not c0.structure("A", 1, i) and not c0.structure("B", -1, i)
    assert (
        c0.cfg.thresholds.armed == 70
        and c1.cfg.thresholds.armed < 0
        and c1.cfg.thresholds.entry < 0
    )
    assert cfg.thresholds.armed == 70  # the shared frozen config object is never mutated


def test_holdout_is_refused_and_frozen_v11_untouched() -> None:
    cfg = load_v11_config(ROOT / "config" / "btc_swing_v11.yaml")
    with pytest.raises(RuntimeError):
        load_inputs(cfg, ROOT / "data", ms("2025-06-01"), ms("2025-08-01"))
    src = "".join(p.read_text() for p in (ROOT / "btc_swing" / "v11_2").glob("*.py"))
    for tok in (
        "forward_v1",
        "import write_freeze",
        "v11.freeze",
        "btc_swing.v10",
        "btc_swing.v11_warm",
        "btc_swing.v11.live",
        "btc_swing.v11.runner",
        "BybitDemoClient",
        "place_order",
    ):
        assert tok not in src, tok
    fz = ROOT / "manifests" / "v11_forward_freeze.json"
    assert hashlib.sha256(fz.read_bytes()).hexdigest()  # exists; strategy hash pinned below
    assert cfg.strategy_hash.startswith("a324dbcb0394")
