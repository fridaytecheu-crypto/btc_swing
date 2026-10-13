"""V11.1 COST-AWARE HIGH-CONVICTION INTRADAY — required tests (design section 11). Synthetic data only;
nothing touches the archive, the locked holdout, V10 or the V11 forward artefacts."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

from btc_swing.v11.features import build_frame_v11
from btc_swing.v11.scoring import score_frame
from btc_swing.v11_1 import config as v111_config
from btc_swing.v11_1.config import PREFIX_SIGNAL, STRATEGY, load_v111_config
from btc_swing.v11_1.engine import (
    BLOCK_CONTRA,
    BLOCK_COST,
    BLOCK_EVIDENCE,
    BLOCK_STOP_WIDE,
    MS_1M,
    V111Engine,
)
from btc_swing.v11_1.evidence import evidence_frame

ROOT = Path(__file__).resolve().parents[2]
CFG_PATH = ROOT / "config" / "btc_swing_v11_1.yaml"
DAY_MS = 86_400_000
T0 = 1_800_000_000_000 - (1_800_000_000_000 % DAY_MS)
P0 = 50_000.0


def cfg():
    return load_v111_config(CFG_PATH)


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


def quiet_engine(days: int = 3, cost_mult: float = 1.0) -> tuple[V111Engine, int]:
    """Engine on a synthetic frame with scores NaN (nothing arms by itself) and evidence arrays set so
    that an injected decision passes the evidence / contradiction gates."""
    c = cfg()
    fr = build_frame_v11(synth_rows(days), c.base)
    sc = {k: np.full(len(fr), np.nan) for k in score_frame(fr, c.base)}
    sc.update(evidence_frame(fr, c))
    n = len(fr)
    for tag in ("long", "short"):
        sc[f"evid_n_{tag}"] = np.full(n, 5.0)
        sc[f"contra_{tag}"] = np.zeros(n)
        for g in c.quality.directional_groups:
            sc[f"evid_{tag}_{g}"] = np.ones(n) if g != "microstructure" else np.full(n, np.nan)
    eng = V111Engine(c, fr, sc)
    i = days * 1440 - 200
    assert np.isfinite(fr.c["atr5"][i]) and np.isfinite(fr.c["atr1h"][i])
    return eng, i


def inject(eng: V111Engine, i: int, s: int, stop_frac: float, fam: str = "A") -> dict:
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


def set_path(eng: V111Engine, i0: int, closes: list[float]) -> None:
    X = eng.X  # noqa: N806
    prev = float(X["close"][i0 - 1])
    for k, px in enumerate(closes):
        j = i0 + k
        X["open"][j] = prev
        X["close"][j] = px
        X["high"][j] = max(prev, px)
        X["low"][j] = min(prev, px)
        prev = px


# ----------------------------------------------------------------------------- config
def test_config_is_pre_registered(tmp_path: Path) -> None:
    c = cfg()
    assert c.strategy_name == STRATEGY and c.base.strategy_name != STRATEGY
    assert c.base.stop.floor_pct == 0.55 and c.base.stop.cap_pct == 1.0
    assert c.cost_filter.expected_cost_bps == 14.0 and c.cost_filter.max_cost_r == 0.25
    assert c.quality.min_evidence_groups == 4 and "volatility" not in c.quality.directional_groups
    assert c.base.risk.risk_per_trade == 0.0015 and c.base.risk.max_leverage <= 5
    assert (
        c.base.exits.tp1_r == 0.6 and c.base.exits.tp2_r == 1.2 and c.base.exits.hard_max_min == 60
    )
    raw = yaml.safe_load(CFG_PATH.read_text())
    raw["base"]["stop"]["floor_pct"] = 0.35
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError):
        load_v111_config(bad)
    raw = yaml.safe_load(CFG_PATH.read_text())
    raw["quality"]["directional_groups"].append("volatility")
    bad.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError):
        load_v111_config(bad)
    assert len(c.strategy_hash) == 64 and len(c.config_hash) == 64


# ----------------------------------------------------------------------------- PIT / evidence
def test_evidence_arrays_are_point_in_time() -> None:
    c = cfg()
    rows = synth_rows(25)
    full = evidence_frame(build_frame_v11(rows, c.base), c)
    k = 20 * 1440 + 37
    part = evidence_frame(build_frame_v11(rows[:k], c.base), c)
    for name, arr in full.items():
        assert _same(arr[:k], part[name][:k]), f"lookahead in {name}"


def test_independent_evidence_counts_groups_not_indicators() -> None:
    """One group = one unit of evidence; the whole trend group (three EMA votes, VWAP, structure)
    counts once, volatility never counts, and the rule needs >= 0.60 with a 0.20 margin."""
    c = cfg()
    fr = build_frame_v11(synth_rows(6), c.base)
    ev = evidence_frame(fr, c)
    sc = score_frame(fr, c.base)
    n = len(fr)
    w = c.base.weights.as_dict()
    # recompute from the published contributions: sub = contrib / weight
    for tag, other in (("long", "short"), ("short", "long")):
        count = np.zeros(n)
        for g in c.quality.directional_groups:
            a, b = sc[f"{tag}_{g}"] / w[g], sc[f"{other}_{g}"] / w[g]
            ok = np.isfinite(a) & np.isfinite(b)
            conf = ok & (a >= 0.6) & ((a - b) >= 0.2)
            assert _same(ev[f"evid_{tag}_{g}"], np.where(ok, conf.astype(float), np.nan))
            count += conf
        assert np.array_equal(ev[f"evid_n_{tag}"], count)
        assert f"evid_{tag}_volatility" not in ev
    # the maximum historical count is 5 (microstructure absent) and the trend group contributes 1
    assert np.nanmax(ev["evid_n_long"]) <= 5
    fin = np.isfinite(ev["evid_long_trend"])
    assert set(np.unique(ev["evid_long_trend"][fin])) <= {0.0, 1.0}


def test_entry_requires_evidence_and_no_contradiction() -> None:
    eng, i = quiet_engine()
    key = "A+1"
    c0 = float(eng.X["close"][i])
    # make an armed family-A long and a trigger at row i, then vary the gates
    eng.structure = lambda f, s, j: f == "A" and s == 1  # type: ignore[method-assign]
    eng.trigger = lambda f, s, j: f == "A" and s == 1 and j >= i  # type: ignore[method-assign]
    eng.quality_reason = lambda f, s, j, arm: None  # type: ignore[method-assign]
    eng.stop_level = lambda f, s, j: (c0 * (1 - 0.006), "test")  # type: ignore[method-assign]
    eng.sc["score_long"][i - 1 : i + 1] = 80.0
    eng.process_row(i - 1)  # arms (score >= 70, structure)
    assert key in eng.armed
    # below the entry score: nothing
    eng.sc["score_long"][i] = 74.9
    assert eng.process_row(i) is None and eng.counters["triggers"] >= 1
    eng.sc["score_long"][i] = 80.0
    eng.sc["evid_n_long"][i] = 3.0
    assert eng.process_row(i) is None and eng.quality_rejections[BLOCK_EVIDENCE] == 1
    eng.sc["evid_n_long"][i] = 4.0
    eng.sc["contra_long"][i] = 1.0
    assert eng.process_row(i) is None and eng.quality_rejections[BLOCK_CONTRA] == 1
    eng.sc["contra_long"][i] = 0.0
    rec = eng.process_row(i)
    assert rec is not None and rec["action"] == "EXECUTED" and rec["evidence_n"] == 4
    assert rec["signal_id"].startswith(PREFIX_SIGNAL) and rec["evidence_groups"]


def test_failed_breakout_invalidates_family_b_setup() -> None:
    eng, i = quiet_engine()
    X = eng.X  # noqa: N806
    lvl = float(X["hi60"][i - 3])
    eng.structure = lambda f, s, j: f == "B" and s == 1  # type: ignore[method-assign]
    eng.trigger = lambda f, s, j: f == "B" and s == 1 and j == i  # type: ignore[method-assign]
    eng.sc["score_long"][i - 3 : i + 1] = 80.0
    X["hi60"][i - 3 : i + 1] = lvl
    X["close"][i - 3] = lvl - 1.0  # armed inside the range
    X["close"][i - 2] = lvl + 5.0  # breaks ...
    X["close"][i - 1] = lvl - 5.0  # ... and reclaims inside before any entry: FAILED
    X["close"][i] = lvl + 5.0  # re-break: refused
    for j in range(i - 3, i + 1):
        eng.process_row(j)
    assert eng.armed["B+1"]["failed"] is True
    assert eng.quality_rejections.get("FAILED_BREAKOUT") == 1 and eng.pos is None
    assert eng.quality_rejections.get("FAILED_BREAKOUT_DETECTED") == 1


# ----------------------------------------------------------------------------- stops / cost / sizing
def test_stop_floor_cap_and_cost_filter() -> None:
    eng, i = quiet_engine()
    close = float(eng.X["close"][i])
    rec = inject(
        eng, i, 1, 0.003
    )  # 0.30% structural -> widened to 0.55% -> cost 14/55 > 0.25: rejected
    assert rec["action"] == "BLOCKED" and rec["blockers"] == [BLOCK_COST]
    assert rec["stop_pct"] == pytest.approx(0.55) and "FLOORED" in rec["stop_note"]
    assert rec["expected_cost_r"] == pytest.approx(14.0 / 55.0)
    rec = inject(eng, i, -1, 0.006)  # 0.60% short: cost 0.233R accepted
    assert rec["action"] == "EXECUTED" and rec["stop_pct"] == pytest.approx(0.6)
    assert rec["expected_cost_r"] == pytest.approx(14.0 / 60.0) and rec["stop"] == pytest.approx(
        close * 1.006
    )
    eng.pending_open = None
    rec = inject(eng, i, 1, 0.012)  # 1.20% > 1.00% cap: rejected, never tightened
    assert rec["action"] == "BLOCKED" and rec["blockers"] == [BLOCK_STOP_WIDE]
    assert eng.blockers[BLOCK_STOP_WIDE] == 1 and eng.blockers[BLOCK_COST] == 1
    rec = inject(eng, i, 1, 0.0099)  # inside the cap: accepted
    assert rec["action"] == "EXECUTED" and rec["expected_cost_r"] == pytest.approx(14.0 / 99.0)
    eng.pending_open = None
    rec = inject(eng, i, 1, -0.004)
    assert rec["blockers"] == ["NO_VALID_STRUCTURAL_STOP"]


def test_cost_r_formula_matches_audited_cost_model() -> None:
    c = cfg()
    co = c.base.costs
    assert c.cost_filter.expected_cost_bps == pytest.approx(
        2 * co.taker_fee_bps + 2 * co.half_spread_bps + co.entry_slippage_bps + co.exit_slippage_bps
    )
    # a flat trade closed by TIME realises exactly that cost in R (taker both ways, no stop surcharge)
    eng, i = quiet_engine()
    rec = inject(eng, i, 1, 0.007)
    eng.open_from_pending(i + 1)
    eng.sc["score_long"][i + 1 : i + 80] = 80.0
    set_path(eng, i + 1, [rec["entry_ref"]] * 70)
    for j in range(i + 1, i + 71):
        eng.process_row(j)
        if eng.pos is None:
            break
    t = eng.trades[0]
    assert t.exit_reason == "TIME" and t.gross_r == pytest.approx(0.0, abs=1e-9)
    assert t.cost_r == pytest.approx(rec["expected_cost_r"], rel=0.02)


def test_fixed_risk_sizing_and_leverage_invariance() -> None:
    eng, i = quiet_engine()
    base = eng.cfg
    close = float(eng.X["close"][i])
    recs = {}
    for lev in ([1, 2, 3, 5], [3], [5]):
        eng.cfg = base.model_copy(
            update={
                "risk": base.risk.model_copy(update={"allowed_leverage": lev, "max_leverage": 5})
            }
        )
        eng.pending_open = None
        recs[tuple(lev)] = inject(eng, i, 1, 0.007)
    b = recs[(1, 2, 3, 5)]
    assert b["action"] == "EXECUTED" and b["risk_usdt"] == pytest.approx(7.5)
    assert b["qty"] * close * 0.007 == pytest.approx(7.5)
    for lev in ((3,), (5,)):
        assert recs[lev]["qty"] == pytest.approx(b["qty"]) and recs[lev][
            "risk_usdt"
        ] == pytest.approx(7.5)
        assert recs[lev]["leverage"] == lev[0]
        assert recs[lev]["margin"] == pytest.approx(recs[lev]["notional"] / lev[0])
    assert all(r["leverage"] <= 5 for r in recs.values())


# ----------------------------------------------------------------------------- exits / semantics
def test_next_bar_entry_semantics_and_60_minute_stop() -> None:
    eng, i = quiet_engine()
    rec = inject(eng, i, 1, 0.007)
    assert eng.pos is None and eng.pending_open is not None  # decision at the close of bar i
    pos = eng.open_from_pending(i + 1)
    assert pos is not None and pos.entry_ms == int(eng.t_open[i + 1])  # filled at the next 1m open
    assert pos.entry_ref == float(eng.X["open"][i + 1]) and pos.entry_fill > pos.entry_ref
    c0 = rec["entry_ref"]
    set_path(eng, i + 1, [c0 + 0.0001 * c0 * ((k % 3) - 1) for k in range(70)])
    eng.sc["score_long"][i + 1 : i + 80] = 80.0
    for j in range(i + 1, i + 71):
        eng.process_row(j)
        if eng.pos is None:
            break
    t = eng.trades[0]
    assert t.exit_reason == "TIME" and t.hold_min == 60


def test_tp_ladder_is_inherited_unchanged() -> None:
    eng, i = quiet_engine()
    ex = eng.cfg.exits
    rec = inject(eng, i, 1, 0.007)
    eng.open_from_pending(i + 1)
    c0, d = rec["entry_ref"], rec["entry_ref"] * 0.007
    assert rec["tp1"] == pytest.approx(c0 + 0.6 * d) and rec["tp2"] == pytest.approx(c0 + 1.2 * d)
    path = [c0, c0 + 0.7 * d, c0 + 1.3 * d] + [c0 + 1.25 * d] * 12 + [c0 - 0.2 * d]
    set_path(eng, i + 1, path)
    for j in range(i + 1, i + 1 + len(path)):
        eng.process_row(j)
        if eng.pos is None:
            break
    t = eng.trades[0]
    kinds = [lg["kind"] for lg in t.pos["legs"]]
    assert kinds[:2] == ["TP1", "TP2"] and kinds[-1] == "TRAIL"
    assert [lg["frac"] for lg in t.pos["legs"][:2]] == [ex.tp1_frac, ex.tp2_frac]
    assert sum(lg["frac"] for lg in t.pos["legs"]) == pytest.approx(1.0)


def test_one_position_rule() -> None:
    eng, i = quiet_engine()
    inject(eng, i, 1, 0.007)
    eng.open_from_pending(i + 1)
    for s in (1, -1):
        rec = inject(eng, i + 2, s, 0.007)
        assert rec["action"] == "BLOCKED" and "POSITION_OPEN" in rec["blockers"]
    assert eng.counters["skipped_while_open"] == 2 and eng.counters["executed"] == 1


# ----------------------------------------------------------------------------- isolation
def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_v11_1_is_isolated_from_v10_and_v11() -> None:
    src = "".join(p.read_text() for p in sorted((ROOT / "btc_swing" / "v11_1").glob("*.py")))
    for tok in (
        "btc_swing.v10",
        "btc_swing.v11.live",
        "btc_swing.v11.runner",
        "btc_swing.v11.paths",
        "btc_swing.v11.freeze",
        "btc_swing.v5.demo",
        "BybitDemoClient",
        "forward_v10",
        "forward_v11",
        "v11_forward_freeze",
        "api-demo",
        "api.bybit",
        "place_order",
        "STRATEGY_DEMO",
        "V10D-",
        "V11D-",
    ):
        assert tok not in src, f"V11.1 source references {tok}"
    # only the V11 research library (features, scoring, engine, data, research, report) is imported
    assert "btc_swing.v11.features" in src and "btc_swing.v11.engine" in src
    assert PREFIX_SIGNAL == "V111S-" and not hasattr(v111_config, "PREFIX_DEMO")
    # V11.1 never writes a V11 or V10 artefact: the committed V11 freeze and config are read-only here
    fz = ROOT / "manifests" / "v11_forward_freeze.json"
    v11_cfg = ROOT / "config" / "btc_swing_v11.yaml"
    before = (_sha(fz), _sha(v11_cfg))
    c = cfg()
    fr = build_frame_v11(synth_rows(2), c.base)
    evidence_frame(fr, c)
    assert (_sha(fz), _sha(v11_cfg)) == before


def test_base_sections_equal_frozen_v11_except_stop_geometry() -> None:
    """The V11 information universe is unchanged: every base section equals config/btc_swing_v11.yaml
    except the pre-registered stop floor and the uniform 0.20 ATR buffers."""
    v11 = yaml.safe_load((ROOT / "config" / "btc_swing_v11.yaml").read_text())
    base = yaml.safe_load(CFG_PATH.read_text())["base"]
    for sec in (
        "features",
        "weights",
        "thresholds",
        "exits",
        "costs",
        "risk",
        "windows",
        "outcomes",
    ):
        assert base[sec] == v11[sec], sec
    assert base["stop"] == {"floor_pct": 0.55, "cap_pct": 1.0} and v11["stop"]["floor_pct"] == 0.35
    for fam in ("a", "b", "c"):
        b, v = dict(base["families"][fam]), dict(v11["families"][fam])
        assert b.pop("stop_buffer_atr") == 0.2
        v.pop("stop_buffer_atr")
        assert b == v, fam
    assert math.isclose(sum(base["weights"].values()), 100.0)
