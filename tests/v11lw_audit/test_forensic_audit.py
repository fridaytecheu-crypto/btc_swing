"""scripts/v11lw_forward_forensic_audit.py on a synthetic V11-LITE-WARM root: per-trade path metrics,
loss classes, strict-gate and soft-filter counterfactuals, twin comparison, pathology checks; the
audit is read-only (input hashes unchanged, writes only under its out_dir)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "v11lw_forward_forensic_audit.py"
T0 = 1_792_200_000_000 - (1_792_200_000_000 % 60_000)
MS_1M = 60_000


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("v11lw_forward_forensic_audit", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["v11lw_forward_forensic_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


AUD = _load()


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _rec(kind: str, data: dict[str, Any], ts_ms: int, seq: int) -> str:
    return (
        json.dumps(
            {
                "kind": kind,
                "data": data,
                "ts": _iso(ts_ms),
                "seq": seq,
                "prev_hash": f"h{seq - 1}",
                "hash": f"h{seq}",
            }
        )
        + "\n"
    )


def _price(k: int) -> tuple[float, float, float]:
    """close, high, low of minute k (relative to T0); flat 60000 outside the three trade episodes."""
    if k < 0 or 41 <= k < 120 or 140 <= k < 200:
        c = 60000.0
        return c, c + 1, c - 1
    if 0 <= k < 10:  # trade 1 (LONG): straight down through the stop at 59790
        c = 60000.0 - 22.0 * k
        return c, 60000.0 - 20.0 * k + 5.0, 60000.0 - 25.0 * k
    if 10 <= k < 41:  # after the stop: rally through TP1 (60126)
        c = min(59800.0 + 15.0 * (k - 10), 60250.0)
        return c, c + 2, c - 2
    if 120 <= k < 140:  # trade 2 (SHORT): clean fall to 59700
        c = 60000.0 - 15.0 * (k - 119)
        return c, c + 5, c - 5
    if 200 <= k < 260:  # trade 3 (LONG): +60 then flat (0.29R)
        c = 60000.0 + min(60.0, 10.0 * (k - 199))
        return c, c + 2, c - 2
    c = 60060.0
    return c, c + 1, c - 1


def _detail(a_long: bool, b_short: bool, c_long: bool) -> dict[str, Any]:
    def a(ok: bool) -> dict[str, bool]:
        return {"data_valid": True, "impulse_pullback": ok, "struct15_aligned": ok, "turn": ok}

    def b(ok: bool) -> dict[str, bool]:
        return {"data_valid": True, "compression": ok, "near_boundary": ok}

    def c(ok: bool) -> dict[str, bool]:
        return {"data_valid": True, "sweep_recent": ok, "rsi_exhausted": ok}

    return {
        "A": {"LONG": a(a_long), "SHORT": a(False)},
        "B": {"LONG": b(False), "SHORT": b(b_short)},
        "C": {"LONG": c(c_long), "SHORT": c(False)},
    }


def _decision(
    sid: str,
    side: int,
    tmpl: str,
    score: float,
    contrib: dict[str, float | None],
    labels: dict[str, bool],
    detail: dict[str, Any],
    dec_ms: int,
    stop_note: str,
    structural: float,
) -> dict[str, Any]:
    strict = labels[tmpl]
    return {
        "signal_id": sid,
        "action": "EXECUTED",
        "side": "LONG" if side > 0 else "SHORT",
        "side_sign": side,
        "selected_template": tmpl,
        "score": score,
        "score_armed": score - 2,
        "score_long": score if side > 0 else 30.0,
        "score_short": score if side < 0 else 30.0,
        "decision_ms": dec_ms,
        "arm_since_ms": dec_ms - 7 * MS_1M,
        "arm_episode_id": f"{'L' if side > 0 else 'S'}-{dec_ms - 7 * MS_1M}",
        "contrib": contrib,
        "contrib_other_side": {g: 3.0 for g in AUD.GROUPS},
        "structure_labels": labels,
        "control_twin": {
            "would_take": strict,
            "reason": "CONTROL_SAME_DECISION" if strict else f"STRUCTURE_{tmpl}_FALSE:x",
            "failed_conditions": [] if strict else ["x"],
            "twin_decision": None,
        },
        "incremental": not strict,
        "stop_note": stop_note,
        "structural_stop": structural,
        "indicators": {
            "close": 60000.0,
            "rsi7": 55.0,
            "rsi14": 52.0,
            "adx14": 24.0,
            "atr5": 60.0,
            "vol_tercile": 1.0,
            "struct15": float(side),
            "ema21_slope": 0.1 * side,
            "hi60": 60040.0,
            "lo60": 59960.0,
            "rvol5": 1.5,
            "oi": 60000.0,
            "oi_chg_15m": 0.001,
            "oi_chg_1h": 0.002,
            "funding": 1e-5,
            "dist_vwap": 0.2 * side,
            "vwap_slope": 0.05 * side,
            "imb5": 0.1 * side,
            "imb15": 0.2 * side,
            "delta5": 10.0 * side,
            "delta15": 20.0 * side,
        },
        "context": {
            "structure_detail": detail,
            "fib": {
                "available": True,
                "bucket_atr": "<= 0.10",
                "direction": "UP" if side > 0 else "DOWN",
                "nearest_ratio": "0.618",
                "nearest_dist_bps": 1.0,
                "retrace_frac": 0.6,
                "leg_bps": 40.0,
                "swing_high_row": 10,
                "swing_low_row": 5,
            },
            "vwap": {
                "dist_atr": 0.2 * side,
                "dist_bps": 3.0 * side,
                "slope_atr": 0.05 * side,
                "vwap": 59990.0,
            },
            "range": {"60m": {"available": True, "position": 0.5, "width_bps": 30.0}},
            "location_atr": {"to_hi60": 0.7, "to_lo60": 0.7},
            "flow": {
                "imb5": 0.1 * side,
                "imb15": 0.2 * side,
                "delta5": 10.0 * side,
                "delta15": 20.0 * side,
                "rvol5": 1.5,
            },
            "microstructure": {
                "depth_imb": 0.3 * side,
                "imb_10": 0.2,
                "imb_30": 0.1,
                "imb_60": 0.0,
                "delta_30": 1.0,
                "absorption_30": None,
                "liq_burst_pct_120": 0.5,
                "liq_imb_120": None,
                "spread_bps": 0.01,
            },
        },
    }


def _trade(
    dec: dict[str, Any],
    side: int,
    entry_ms: int,
    exit_ms: int,
    exit_reason: str,
    gross_r: float,
    net_r: float,
    mfe_r: float,
    mae_r: float,
    legs: list[dict[str, Any]],
) -> dict[str, Any]:
    ref = 60000.0
    stop0 = ref - side * 210.0
    risk = 7.5
    return {
        "signal_id": dec["signal_id"],
        "selected_template": dec["selected_template"],
        "side": side,
        "decision_ms": dec["decision_ms"],
        "entry_ms": entry_ms,
        "exit_ms": exit_ms,
        "entry_ref": ref,
        "entry_fill": ref * (1 + side * 1.5e-4),
        "stop0": stop0,
        "stop": stop0,
        "stop_pct": 0.35,
        "tp1": ref + side * 126.0,
        "tp2": ref + side * 252.0,
        "qty": risk / 210.0,
        "leverage": 2.0,
        "risk_usdt": risk,
        "score_entry": dec["score"],
        "exit_reason": exit_reason,
        "hold_min": (exit_ms - entry_ms) / MS_1M,
        "gross_usd": gross_r * risk,
        "fee_usd": 0.3 * risk,
        "slip_usd": (gross_r - net_r - 0.3) * risk,
        "funding_usd": 0.0,
        "net_usd": net_r * risk,
        "net_r": net_r,
        "gross_r": gross_r,
        "cost_r": gross_r - net_r,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "tp1_done": any(lg["kind"] == "TP1" for lg in legs),
        "tp2_done": any(lg["kind"] == "TP2" for lg in legs),
        "legs": legs,
        "trigger_low": ref - side * 30.0,
        "decision": dec,
    }


def build_root(tmp: Path) -> Path:
    root = tmp / "btc" / "forward_v11_lite_warm"
    (root / "journals").mkdir(parents=True)
    (root / "derived").mkdir()
    with (root / "derived" / "bars_1m.jsonl").open("w") as fh:
        for k in range(-120, 330):
            c, h, lo = _price(k)
            fh.write(
                json.dumps(
                    {
                        "open_ms": T0 + k * MS_1M,
                        "o": c,
                        "h": h,
                        "l": lo,
                        "c": c,
                        "vol": 1.0,
                        "buy": 0.5,
                        "sell": 0.5,
                        "n": 10,
                        "src": "live" if k > -100 else "kline_seamfill",
                    }
                )
                + "\n"
            )
    t1, t2, t3 = T0, T0 + 120 * MS_1M, T0 + 200 * MS_1M
    c_fail = {
        "trend": 15.0,
        "momentum": 12.0,
        "volatility": None,
        "volume": 9.0,
        "location": 12.0,
        "derivatives": 8.0,
        "microstructure": 13.3,
    }
    d1 = _decision(
        "V11LWS-1-AL",
        1,
        "A",
        AUD.score_from_contrib(c_fail),
        c_fail,
        {"A": False, "B": False, "C": False},
        _detail(False, False, False),
        t1,
        "A:min(pullback,swing)-buffer|FLOORED",
        59900.0,
    )
    c_pass = {
        "trend": 16.0,
        "momentum": 12.0,
        "volatility": 8.0,
        "volume": 12.0,
        "location": 12.0,
        "derivatives": 8.0,
        "microstructure": 12.0,
    }
    d2 = _decision(
        "V11LWS-2-BS",
        -1,
        "B",
        AUD.score_from_contrib(c_pass),
        c_pass,
        {"A": False, "B": True, "C": False},
        _detail(False, True, False),
        t2,
        "B:breakout level-buffer",
        59750.0,
    )
    c_hi = {
        "trend": 18.0,
        "momentum": 13.0,
        "volatility": 9.0,
        "volume": 13.0,
        "location": 13.0,
        "derivatives": 9.0,
        "microstructure": 11.0,
    }
    d3 = _decision(
        "V11LWS-3-CL",
        1,
        "C",
        AUD.score_from_contrib(c_hi),
        c_hi,
        {"A": True, "B": False, "C": False},
        _detail(True, False, False),
        t3,
        "C:sweep extreme-buffer",
        59800.0,
    )
    d3["indicators"]["hi60"] = (
        59980.0  # close 20 above hi60 (< 0.5 ATR5), rvol 1.5: the B trigger also fires
    )
    tr1 = _trade(
        d1,
        1,
        t1,
        t1 + 10 * MS_1M,
        "STOP",
        -1.0,
        -1.08,
        0.024,
        -1.02,
        [{"frac": 1.0, "ref": 59790.0, "fill": 59770.0, "kind": "STOP", "t_ms": t1 + 10 * MS_1M}],
    )
    tr2 = _trade(
        d2,
        -1,
        t2,
        t2 + 20 * MS_1M,
        "TRAIL",
        0.9,
        0.5,
        1.4,
        0.0,
        [
            {"frac": 0.4, "ref": 59874.0, "fill": 59880.0, "kind": "TP1", "t_ms": t2 + 9 * MS_1M},
            {"frac": 0.4, "ref": 59748.0, "fill": 59755.0, "kind": "TP2", "t_ms": t2 + 17 * MS_1M},
            {
                "frac": 0.2,
                "ref": 59760.0,
                "fill": 59770.0,
                "kind": "TRAIL",
                "t_ms": t2 + 20 * MS_1M,
            },
        ],
    )
    tr3 = _trade(
        d3,
        1,
        t3,
        t3 + 60 * MS_1M,
        "TIME",
        0.1,
        -0.3,
        0.29,
        -0.01,
        [{"frac": 1.0, "ref": 60060.0, "fill": 60050.0, "kind": "TIME", "t_ms": t3 + 60 * MS_1M}],
    )
    with (root / "journals" / "shadow_trades.jsonl").open("w") as fh:
        for i, (_d, tr) in enumerate(((d1, tr1), (d2, tr2), (d3, tr3))):
            fh.write(
                _rec(
                    "SHADOW_OPEN",
                    {k: v for k, v in tr.items() if k != "decision"},
                    tr["entry_ms"],
                    2 * i,
                )
            )
            fh.write(_rec("SHADOW_CLOSED", tr, tr["exit_ms"], 2 * i + 1))
    with (root / "journals" / "signals.jsonl").open("w") as fh:
        for i, d in enumerate((d1, d2, d3)):
            fh.write(_rec("SIGNAL_EXECUTED", d, d["decision_ms"], i))
        fh.write(
            _rec(
                "SIGNAL_BLOCKED",
                {
                    "signal_id": "V11LWS-b",
                    "action": "BLOCKED",
                    "blockers": ["POSITION_OPEN"],
                    "decision_ms": t1 + 3 * MS_1M,
                },
                t1 + 3 * MS_1M,
                3,
            )
        )
    nm_ts = T0 + 60 * MS_1M
    with (root / "journals" / "nearmiss.jsonl").open("w") as fh:
        fh.write(
            _rec(
                "NEAR_MISS",
                {
                    "ts": _iso(nm_ts),
                    "ts_ms": nm_ts,
                    "side": "LONG",
                    "score": 72.0,
                    "selected_template": "A",
                    "templates_triggered": ["A"],
                    "structure_labels": {"A": True, "B": False, "C": False},
                },
                nm_ts,
                0,
            )
        )
        fh.write(
            _rec(
                "NEAR_MISS",
                {
                    "ts": _iso(nm_ts + MS_1M),
                    "ts_ms": nm_ts + MS_1M,
                    "side": "SHORT",
                    "score": 73.0,
                    "selected_template": "C",
                    "templates_triggered": ["C"],
                    "structure_labels": {"A": False, "B": False, "C": False},
                },
                nm_ts + MS_1M,
                1,
            )
        )
    with (root / "journals" / "outcomes.jsonl").open("w") as fh:
        for i, h in enumerate((5, 15, 30, 60)):
            fh.write(
                _rec(
                    "OUTCOME",
                    {
                        "id": f"V11LWN-{nm_ts}-L",
                        "kind": "NEAR_MISS",
                        "horizon_min": h,
                        "t_ms": nm_ts,
                        "return_bps": 12.0,
                        "mfe_bps": 20.0,
                        "mae_bps": -3.0,
                    },
                    nm_ts + h * MS_1M,
                    i,
                )
            )
        fh.write(
            _rec(
                "OUTCOME",
                {
                    "id": "V11LWS-1-AL",
                    "kind": "EXECUTED",
                    "horizon_min": 60,
                    "t_ms": t1,
                    "return_bps": 40.0,
                    "mfe_bps": 42.0,
                    "mae_bps": -37.0,
                },
                t1 + 60 * MS_1M,
                4,
            )
        )
    with (root / "journals" / "control_twin.jsonl").open("w") as fh:
        fh.write(
            _rec(
                "TWIN_EXECUTED",
                {
                    "signal_id": "V11S-x-AL",
                    "family": "A",
                    "side": "LONG",
                    "side_sign": 1,
                    "action": "EXECUTED",
                    "decision_ms": t1 + 4 * MS_1M,
                    "score": 78.0,
                },
                t1 + 4 * MS_1M,
                0,
            )
        )
        fh.write(
            _rec(
                "TWIN_CLOSED",
                {
                    "signal_id": "V11S-x-AL",
                    "family": "A",
                    "side": 1,
                    "decision_ms": t1 + 4 * MS_1M,
                    "exit_reason": "STOP",
                    "net_r": -0.53,
                    "net_usd": -3.97,
                    "mfe_r": 0.1,
                },
                t1 + 30 * MS_1M,
                1,
            )
        )
        fh.write(
            _rec(
                "TWIN_EXECUTED",
                {
                    "signal_id": "V11S-y-BS",
                    "family": "B",
                    "side": "SHORT",
                    "side_sign": -1,
                    "action": "EXECUTED",
                    "decision_ms": t2,
                    "score": 80.0,
                },
                t2,
                2,
            )
        )
    (root / "journals" / "episodes.jsonl").write_text(
        _rec("RUNNER_STARTED", {"observation_start_ms": T0 - 60 * MS_1M}, T0 - 90 * MS_1M, 0)
    )
    (root / "state.json").write_text(
        json.dumps(
            {
                "observation_start_ms": T0 - 60 * MS_1M,
                "counters": {"executed": 3, "blocked": 1},
                "trades": 3,
                "wins": 1,
                "losses": 2,
                "cum_net_usd": -6.6,
                "position": None,
            }
        )
    )
    return root


def _hashes(root: Path) -> dict[str, str]:
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()
    }


def test_forensic_audit_per_trade_classes_counterfactuals_twin_and_read_only(
    tmp_path: Path,
) -> None:
    root = build_root(tmp_path)
    before = _hashes(root)
    out = tmp_path / "reports"
    md, js, rep = AUD.run_audit(root, out, cutoff_ms=T0 + 330 * MS_1M)
    assert _hashes(root) == before, "the audit must not modify any input"
    assert {
        p.parent
        for p in tmp_path.rglob("*")
        if p.is_file() and p.suffix in (".md", ".json") and "forensic" in p.name
    } == {out}
    assert rep["n_closed"] == 3 and md.exists() and js.exists()
    t1, t2, t3 = rep["trades"]
    # ---- trade 1: immediately wrong, floored stop, TP1 printed after the stop -> class A + D flags
    assert t1["strict_gate"] == "FAIL" and t1["loss_class"] == "A"
    assert t1["mae_r_path"] <= -1.0 and t1["first_touch_min"]["-1.0R"] == 9.0
    assert t1["reached_before_loss"] == {"+0.25R": False, "+0.5R": False, "+1.0R": False}
    assert t1["post_exit_tp1_touched"] is True and any(f.startswith("D") for f in t1["loss_flags"])
    assert (
        t1["stop_floored"] and t1["labels"]["A"]["LONG"] is False and t1["score_mismatch"] is False
    )
    assert t1["decision_close_matches_bar"] is True and t1["pre60_non_live_bars"] == 0
    assert t1["outcomes_from_decision_bps"] == {60: 40.0}
    # ---- trade 2: strict PASS win; path MFE in R from the plan geometry
    assert t2["strict_gate"] == "PASS" and t2["loss_class"] is None and t2["mfe_r_path"] > 1.3
    assert t2["reached_before_loss"]["+1.0R"] is True and t2["labels"]["B"]["SHORT"] is True
    # ---- trade 3: gross positive, net negative -> cost-made loss E; B trigger was also true (order bias check)
    assert t3["loss_class"] == "E" and 0.25 < t3["mfe_r_path"] < 0.35
    assert t3["opposite_side_labels_true"] == [] and t3["labels"]["A"]["LONG"] is True
    assert (
        t3["b_trigger_also_true"] is True
        and "V11LWS-3-CL" in rep["pathologies"]["non_B_selections_where_B_trigger_was_also_true"]
    )
    # ---- aggregates
    assert (
        rep["by_strict_gate"]["PASS"]["n"] == 1
        and rep["by_strict_gate"]["PASS"]["net_r_sum"] == 0.5
    )
    assert (
        rep["by_strict_gate"]["FAIL"]["n"] == 2
        and rep["loss_classes"]["A"]["n"] == 1
        and rep["loss_classes"]["E"]["n"] == 1
    )
    assert rep["by_template"] == {k: v for k, v in rep["by_template"].items()} and set(
        rep["by_template"]
    ) == {"A", "B", "C"}
    assert (
        rep["path_stats"]["losses_n"] == 2
        and rep["path_stats"]["post_exit_tp1_touched_losses"] == 1
    )
    assert rep["cost_decomposition"]["trades_gross_positive_net_negative"] == ["V11LWS-3-CL"]
    # ---- counterfactuals: strict-only subset exact; soft filter removes the 77-FAIL trade at +/-5 and +/-10
    cf = rep["counterfactuals"]
    assert (
        cf["strict_only_subset"]["n"] == 1
        and abs(cf["strict_only_subset"]["net_usd"] - 3.75) < 1e-9
    )
    assert (
        cf["soft_filter"]["+/-5"]["removed_ids"] == ["V11LWS-1-AL"]
        and cf["soft_filter"]["+/-5"]["executed_kept"]["n"] == 2
    )
    assert cf["soft_filter"]["+/-10"]["removed_ids"] == ["V11LWS-1-AL"]
    assert (
        cf["soft_filter"]["+/-5"]["near_miss_additions_n"] == 1
        and cf["soft_filter"]["+/-5"]["near_miss_additions_60m_mean_bps"] == 12.0
    )
    assert cf["soft_filter"]["+/-10"]["near_miss_additions_n"] == 1
    # ---- control twin: one twin execution while LITE was already in trade 1; one on the same bar as trade 2
    tw = rep["control_twin"]
    assert (
        tw["twin_executions_while_lite_in_position"] == 1 and abs(tw["twin_net_usd"] + 3.97) < 1e-9
    )
    assert tw["twin_decisions"][1]["lite_same_bar_trade"] == "V11LWS-2-BS"
    assert tw["lite_trades_twin_would_take"] == 1 and tw["lite_trades_twin_would_reject"] == 2
    # ---- pathologies and integrity
    pa = rep["pathologies"]
    assert pa["selected_template_counts"] == {"A": 1, "B": 1, "C": 1} and pa[
        "blocked_decisions_by_reason"
    ] == {"POSITION_OPEN": 1}
    assert pa["score_mismatches"] == [] and pa["null_groups_at_decision"] == {"volatility": 1}
    assert rep["snapshot"]["verify"]["bytes_appended_during_audit"] == 0 and all(
        rep["journal_chain_links_ok"].values()
    )
    text = md.read_text()
    assert (
        "strict gate **FAIL**" in text and "## 3. Counterfactuals" in text and "V11LWS-2-BS" in text
    )


def test_audit_ignores_appends_after_the_snapshot_and_fails_on_truncation(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    snap = AUD.PrefixSnapshot(T0 + 330 * MS_1M)
    p = root / "journals" / "shadow_trades.jsonl"
    n = len(snap.jsonl(p))
    with p.open("a") as fh:
        fh.write('{"kind":"SHADOW_OPEN","data":{}}\n')
    assert len(snap.jsonl(p)) == n and snap.verify()["bytes_appended_during_audit"] > 0
    p.write_text(p.read_text()[:100])
    try:
        snap.verify()
    except AUD.SnapshotIntegrityError:
        return
    raise AssertionError("a truncated input must fail the audit")


def test_labels_both_sides_and_score_recompute() -> None:
    lab = AUD.labels_both_sides(_detail(True, True, False))
    assert (
        lab["A"] == {"LONG": True, "SHORT": False}
        and lab["B"] == {"LONG": False, "SHORT": True}
        and lab["C"] == {"LONG": False, "SHORT": False}
    )
    c = {
        "derivatives": 10.0,
        "location": 15.0,
        "microstructure": 9.0,
        "momentum": 15.0,
        "trend": 15.0,
        "volatility": None,
        "volume": 9.0,
    }
    assert abs(AUD.score_from_contrib(c) - 81.11111111111111) < 1e-9  # the verification-root trade
    assert AUD.score_from_contrib({g: None for g in AUD.GROUPS}) is None


# ----------------------------------------------------------------------------- mutable state.json (live runner)
def _atomic_rewrite(path: Path, payload: dict[str, Any]) -> None:
    """Exactly how the V11 live runner saves: write a .tmp, then rename over state.json."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True))
    tmp.replace(path)


def test_audit_survives_atomic_state_replacement_while_running(tmp_path: Path) -> None:
    """Regression for `state.json: prefix modified during the audit`: a live runner replaces state.json
    atomically every tick; the audit must capture one consistent version at startup and must not treat
    the later replacements as a prefix violation, while journals keep the full prefix guarantee."""
    import threading

    root = build_root(tmp_path)
    before = _hashes(root)
    state_path = root / "state.json"
    base = json.loads(state_path.read_text())
    stop = threading.Event()
    writes = [0]

    def writer() -> None:
        k = 0
        while not stop.is_set():
            k += 1
            _atomic_rewrite(state_path, base | {"last_bar_ms": T0 + k * MS_1M, "tick": k})
            writes[0] += 1

    th = threading.Thread(target=writer, daemon=True)
    th.start()
    try:
        _md, _js, rep = AUD.run_audit(root, tmp_path / "reports", cutoff_ms=T0 + 330 * MS_1M)
    finally:
        stop.set()
        th.join(timeout=10)
    assert writes[0] > 0, "the writer must have replaced state.json during the audit"
    assert rep["n_closed"] == 3 and rep["state_totals"]["trades"] == 3
    st_entry = next(f for f in rep["snapshot"]["files"] if f["path"].endswith("state.json"))
    assert (
        st_entry["kind"] == "mutable"
        and st_entry["version"]["inode"]
        and st_entry["capture_attempts"] >= 1
    )
    assert rep["snapshot"]["verify"]["mutable_files_captured"] == 1
    assert rep["snapshot"]["verify"]["bytes_appended_during_audit"] == 0
    journals_before = {k: v for k, v in before.items() if "/journals/" in k or "bars_1m" in k}
    journals_after = {k: v for k, v in _hashes(root).items() if "/journals/" in k or "bars_1m" in k}
    assert journals_before == journals_after


def test_mutable_capture_requires_a_stable_version_and_fails_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = build_root(tmp_path)
    snap = AUD.PrefixSnapshot(T0 + 330 * MS_1M)
    p = root / "state.json"
    # a version that changes between every stat: no consistent capture is possible -> explicit failure
    counter = [0]
    real = AUD._stat_sig

    def unstable(st: Any) -> tuple[int, int, int]:
        counter[0] += 1
        ino, size, mt = real(st)
        return (ino, size, mt + counter[0])

    monkeypatch.setattr(AUD, "_stat_sig", unstable)
    with pytest.raises(AUD.SnapshotIntegrityError, match="no internally consistent snapshot"):
        snap.read_mutable(p, tries=3, delay_s=0.0)
    monkeypatch.setattr(AUD, "_stat_sig", real)
    # a half-written (non-JSON) state is never accepted
    p.write_text('{"trades": 3, "cum_net_usd": -6.')
    with pytest.raises(AUD.SnapshotIntegrityError, match="no internally consistent snapshot"):
        snap.read_mutable(p, tries=2, delay_s=0.0)
    # a stable version is captured once and reused; it is never re-verified against later versions
    _atomic_rewrite(p, {"trades": 9})
    assert json.loads(snap.read_mutable(p)) == {"trades": 9}
    _atomic_rewrite(p, {"trades": 10})
    assert json.loads(snap.read_mutable(p)) == {"trades": 9}
    assert snap.verify()["mutable_files_captured"] == 1
    # the two capture modes are exclusive per path
    with pytest.raises(AUD.SnapshotIntegrityError, match="refused"):
        snap.read(p)
    j = root / "journals" / "signals.jsonl"
    snap.read(j)
    with pytest.raises(AUD.SnapshotIntegrityError, match="refused"):
        snap.read_mutable(j)
    # a missing mutable file is recorded as missing, not an error
    assert snap.read_mutable(root / "absent.json") == b""


def test_fixed_cutoff_excludes_records_journaled_after_it(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    late = T0 + 400 * MS_1M
    # a 4th closed trade and a near miss journaled AFTER the cutoff must not enter the audit
    d4 = _decision(
        "V11LWS-4-AL",
        1,
        "A",
        80.0,
        {g: 12.0 for g in AUD.GROUPS},
        {"A": True, "B": False, "C": False},
        _detail(True, False, False),
        late,
        "A:x",
        59900.0,
    )
    tr4 = _trade(d4, 1, late, late + 5 * MS_1M, "STOP", -1.0, -1.1, 0.0, -1.0, [])
    with (root / "journals" / "shadow_trades.jsonl").open("a") as fh:
        fh.write(_rec("SHADOW_CLOSED", tr4, late + 5 * MS_1M, 6))
    with (root / "journals" / "nearmiss.jsonl").open("a") as fh:
        fh.write(
            _rec(
                "NEAR_MISS",
                {
                    "ts": _iso(late),
                    "ts_ms": late,
                    "side": "LONG",
                    "score": 74.0,
                    "selected_template": "A",
                    "templates_triggered": ["A"],
                    "structure_labels": {"A": True, "B": False, "C": False},
                },
                late,
                2,
            )
        )
    _md, _js, rep = AUD.run_audit(root, tmp_path / "reports", cutoff_ms=T0 + 330 * MS_1M)
    assert rep["n_closed"] == 3 and rep["snapshot"]["cutoff_ms"] == T0 + 330 * MS_1M
    assert rep["counterfactuals"]["soft_filter"]["+/-5"]["near_miss_additions_n"] == 1
    _md, _js, rep_all = AUD.run_audit(root, tmp_path / "reports2", cutoff_ms=late + 10 * MS_1M)
    assert rep_all["n_closed"] == 4
    assert rep_all["counterfactuals"]["soft_filter"]["+/-5"]["near_miss_additions_n"] == 2
    assert AUD.at_cutoff([{"ts": "garbage"}, {"no_ts": 1}], 0) == [{"ts": "garbage"}, {"no_ts": 1}]
