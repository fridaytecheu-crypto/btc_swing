"""scripts/strategy_forensics.py on synthetic V10 (Demo + Shadow), V11-WARM and V11-LITE roots: separate
series, per-trade reconstruction, multi-label loss mechanisms incl. UNKNOWN, blocked / missed, cross-window
comparison, typed counterfactuals, read-only (input hashes unchanged, writes only under its out dir)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from tests.v11lw_audit.test_forensic_audit import MS_1M, T0, _rec, build_root

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "strategy_forensics.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("strategy_forensics", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["strategy_forensics"] = mod
    spec.loader.exec_module(mod)
    return mod


SF = _load()


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _bars(path: Path, price: Any) -> None:
    with path.open("w") as fh:
        for k in range(-120, 330):
            c, h, lo = price(k)
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
                        "src": "live",
                    }
                )
                + "\n"
            )


def _v10_root(tmp: Path) -> Path:
    root = tmp / "btc" / "forward_v10"
    for sub in ("journals", "derived", "demo"):
        (root / sub).mkdir(parents=True)

    def price(k: int) -> tuple[float, float, float]:
        if 0 <= k < 8:  # shadow trade: straight down through the 0.7 % floored stop (59580)
            c = 60000.0 - 60.0 * k
            return c, c + 3, c - 5
        if 8 <= k < 60:  # then a rally through TP1 (60420)
            c = min(59520.0 + 25.0 * (k - 8), 60500.0)
            return c, c + 2, c - 2
        if 120 <= k < 150:  # demo trade: up to TP1 then back to the entry (TP1 then stop)
            c = 60000.0 + (22.0 * (k - 119) if k < 135 else 22.0 * 15 - 20.0 * (k - 134))
            return c, c + 3, c - 3
        c = 60000.0
        return c, c + 1, c - 1

    _bars(root / "derived" / "bars_1m.jsonl", price)
    t1, t2 = T0, T0 + 120 * MS_1M
    feats = {
        "last": 60000.0,
        "atr5": 50.0,
        "atr1h": 300.0,
        "spread_bps": 0.01,
        "imb_10": -0.5,
        "imb_30": -0.3,
        "delta_30": -5.0,
        "depth_imb": -0.2,
        "liq_burst_pct_120": 0.9,
        "dist_hi": 0.4,
        "dist_lo": 0.9,
        "warm": 1.0,
        "trade_age_s": 0.5,
    }
    sig1 = {
        "signal_id": "V10S-aaa",
        "family": "A",
        "side": "LONG",
        "side_sign": 1,
        "episode_id": "ep1",
        "decision_ms": t1,
        "armed_ts": _iso(t1 - 3 * MS_1M),
        "decision_latency_ms": 120,
        "trigger_conditions": {"absorption": True, "reversal_print": True},
        "features": feats,
        "entry_ref": 60000.0,
        "stop": 59580.0,
        "stop_pct": 0.7,
        "stop_note": "FLOORED",
        "tp1": 60420.0,
        "tp2": 60840.0,
        "action": "EXECUTED",
    }
    sig2 = {
        **sig1,
        "signal_id": "V10S-bbb",
        "family": "B",
        "episode_id": "ep2",
        "decision_ms": t2,
        "armed_ts": _iso(t2 - 10 * MS_1M),
        "stop_note": "STRUCTURAL",
        "stop_pct": 0.5,
        "stop": 59700.0,
        "tp1": 60300.0,
        "tp2": 60600.0,
    }
    blocked = {
        **sig1,
        "signal_id": "V10S-ccc",
        "action": "BLOCKED",
        "blockers": ["POSITION_OPEN"],
        "decision_ts": _iso(t1 + 2 * MS_1M),
        "ts": _iso(t1 + 2 * MS_1M),
    }
    with (root / "journals" / "signals.jsonl").open("w") as fh:
        fh.write(_rec("SIGNAL_EXECUTED", sig1, t1, 0))
        fh.write(_rec("SIGNAL_BLOCKED", blocked, t1 + 2 * MS_1M, 1))
        fh.write(
            _rec("DEMO_REGIME_STARTED", {"activated_at": _iso(t1 + 60 * MS_1M)}, t1 + 60 * MS_1M, 2)
        )
        fh.write(_rec("SIGNAL_EXECUTED", sig2, t2, 3))
    shadow = {
        "signal_id": "V10S-aaa",
        "episode_id": "ep1",
        "family": "A",
        "side": 1,
        "entry_ms": t1,
        "entry_ref": 60000.0,
        "entry_fill": 60009.0,
        "stop": 59580.0,
        "tp1": 60420.0,
        "tp2": 60840.0,
        "qty": 5 / 420.0,
        "leverage": 2.0,
        "risk_usdt": 5.0,
        "frac_open": 0.0,
        "legs": [
            {"frac": 1.0, "ref": 59580.0, "fill": 59559.0, "kind": "STOP", "t_ms": t1 + 8 * MS_1M}
        ],
        "tp1_done": False,
        "time_checked": False,
        "mfe": 0.01,
        "mae": -1.0,
        "exit_reason": "STOP",
        "exit_ms": t1 + 8 * MS_1M,
        "hold_min": 8.0,
        "gross_usd": -5.0,
        "fee_usd": 0.66,
        "slip_usd": 0.36,
        "net_usd": -6.02,
        "risk_usd": 5.0,
        "gross_r": -1.0,
        "net_r": -1.204,
        "mfe_r": 0.01,
        "mae_r": -1.0,
        "execution_mode": "SHADOW",
    }
    with (root / "journals" / "shadow_trades.jsonl").open("w") as fh:
        fh.write(
            _rec(
                "SHADOW_OPEN",
                {k: v for k, v in shadow.items() if k not in ("exit_reason", "exit_ms")},
                t1,
                0,
            )
        )
        fh.write(_rec("SHADOW_CLOSED", shadow, t1 + 8 * MS_1M, 1))
    demo = {
        "strategy": "BTC_V10",
        "signal_id": "V10S-bbb",
        "episode_id": "ep2",
        "family": "B",
        "side": "LONG",
        "entry_ref": 60000.0,
        "entry_price": 60012.0,
        "stop": 59700.0,
        "tp1": 60300.0,
        "tp2": 60600.0,
        "qty": 0.016,
        "leverage": 2.0,
        "planned_risk_usdt": 5.0,
        "actual_risk_usdt": 4.99,
        "exits": [
            {"price": 60300.0, "qty": 0.008, "link": "V10D-x-T1", "time": _iso(t2 + 15 * MS_1M)},
            {
                "price": 59990.0,
                "qty": 0.008,
                "link": "exchange_tpsl",
                "time": _iso(t2 + 29 * MS_1M),
            },
        ],
        "exit_reason": "TP1_THEN_STOP",
        "fees": 1.06,
        "funding": 0.0,
        "gross_pnl": 0.008 * 288 + 0.008 * -22,
        "net_pnl": 0.008 * 288 + 0.008 * -22 - 1.06,
        "R_net_vs_planned": (0.008 * 288 + 0.008 * -22 - 1.06) / 5.0,
        "execution_mode": "DEMO",
        "decision_ts": _iso(t2),
        "order_sent_ts": _iso(t2 + 400),
        "ack_ts": _iso(t2 + 900),
        "fill_ts": _iso(t2 + 1500),
        "closed_ts": _iso(t2 + 29 * MS_1M),
        "closed_ms": t2 + 29 * MS_1M,
    }
    (root / "demo" / "demo_trades.jsonl").write_text(json.dumps(demo) + "\n")
    (root / "demo" / "strategy_journal.jsonl").write_text(
        _rec("TRADE_CLOSED", demo, t2 + 29 * MS_1M, 0)
    )
    with (root / "journals" / "outcomes.jsonl").open("w") as fh:
        fh.write(
            _rec(
                "OUTCOME",
                {
                    "id": "V10S-ccc",
                    "kind": "BLOCKED",
                    "horizon_min": 60,
                    "t_ms": t1 + 2 * MS_1M,
                    "return_bps": 55.0,
                    "mfe_bps": 70.0,
                    "mae_bps": -10.0,
                },
                t1 + 62 * MS_1M,
                0,
            )
        )
    (root / "journals" / "episodes.jsonl").write_text(
        _rec("RUNNER_STARTED", {}, T0 - 60 * MS_1M, 0)
    )
    (root / "journals" / "nearmiss.jsonl").write_text(
        _rec(
            "NEAR_MISS",
            {
                "nearmiss_id": "V10N-1",
                "family": "A",
                "side": "SHORT",
                "stage": "TRIGGER",
                "failing": ["absorption"],
                "ts": _iso(t1 + 30 * MS_1M),
            },
            t1 + 30 * MS_1M,
            0,
        )
    )
    (root / "state.json").write_text(
        json.dumps(
            {
                "observation_start_ms": T0 - 60 * MS_1M,
                "counters": {"executed": 2, "blocked": 1},
                "shadow_position": None,
                "cum_shadow_net_usd": -6.02,
            }
        )
    )
    return root


def _v11w_root(tmp: Path) -> Path:
    root = tmp / "btc" / "forward_v11_warm"
    (root / "journals").mkdir(parents=True)
    (root / "derived").mkdir()

    def price(k: int) -> tuple[float, float, float]:
        if 200 <= k < 260:  # a strict trade that drifts +0.3R and times out
            c = 60000.0 + min(60.0, 10.0 * (k - 199))
            return c, c + 2, c - 2
        c = 60000.0
        return c, c + 1, c - 1

    _bars(root / "derived" / "bars_1m.jsonl", price)
    t3 = T0 + 200 * MS_1M
    dec = {
        "signal_id": "V11S-w1",
        "family": "A",
        "side": "LONG",
        "side_sign": 1,
        "decision_ms": t3,
        "armed_ms": t3 - 5 * MS_1M,
        "score": 78.0,
        "score_armed": 76.0,
        "score_long": 78.0,
        "score_short": 20.0,
        "contrib": {
            "trend": 18.0,
            "momentum": 13.0,
            "volatility": 8.0,
            "volume": 12.0,
            "location": 12.0,
            "derivatives": 8.0,
            "microstructure": 7.0,
        },
        "indicators": {"rsi7": 60.0, "adx14": 30.0, "atr5": 60.0, "vol_tercile": 1.0},
        "stop_note": "A:min(pullback,swing)-buffer",
        "structural_stop": 59790.0,
        "action": "EXECUTED",
    }
    trade = {
        "signal_id": "V11S-w1",
        "family": "A",
        "side": 1,
        "decision_ms": t3,
        "entry_ms": t3,
        "entry_ref": 60000.0,
        "entry_fill": 60009.0,
        "stop0": 59790.0,
        "stop": 59790.0,
        "stop_pct": 0.35,
        "tp1": 60126.0,
        "tp2": 60252.0,
        "qty": 7.5 / 210,
        "leverage": 2.0,
        "notional": 2142.0,
        "margin": 1071.0,
        "risk_usdt": 7.5,
        "score_entry": 78.0,
        "trigger_low": 59970.0,
        "frac_open": 0.0,
        "tp1_done": False,
        "tp2_done": False,
        "stall_checked": True,
        "mfe_r": 0.29,
        "mae_r": -0.01,
        "legs": [
            {"frac": 1.0, "ref": 60060.0, "fill": 60050.0, "kind": "TIME", "t_ms": t3 + 60 * MS_1M}
        ],
        "funding_usd": 0.0,
        "exit_ms": t3 + 60 * MS_1M,
        "exit_reason": "TIME",
        "hold_min": 60.0,
        "gross_usd": 2.14,
        "fee_usd": 2.36,
        "slip_usd": 0.68,
        "net_usd": -0.9,
        "net_r": -0.12,
        "gross_r": 0.285,
        "cost_r": 0.405,
        "decision": dec,
    }
    with (root / "journals" / "shadow_trades.jsonl").open("w") as fh:
        fh.write(_rec("SHADOW_OPEN", {k: v for k, v in trade.items() if k != "decision"}, t3, 0))
        fh.write(_rec("SHADOW_CLOSED", trade, t3 + 60 * MS_1M, 1))
    with (root / "journals" / "signals.jsonl").open("w") as fh:
        fh.write(_rec("SIGNAL_EXECUTED", dec | {"ts": _iso(t3)}, t3, 0))
        fh.write(
            _rec(
                "SIGNAL_BLOCKED",
                {
                    **dec,
                    "signal_id": "V11S-w2",
                    "action": "BLOCKED",
                    "blockers": ["POSITION_OPEN"],
                    "decision_ms": t3 + 10 * MS_1M,
                    "ts": _iso(t3 + 10 * MS_1M),
                },
                t3 + 10 * MS_1M,
                1,
            )
        )
    (root / "journals" / "nearmiss.jsonl").write_text(
        _rec(
            "NEAR_MISS",
            {
                "ts": _iso(t3 + 30 * MS_1M),
                "ts_ms": t3 + 30 * MS_1M,
                "family": "B",
                "side": "SHORT",
                "score": 72.0,
            },
            t3 + 30 * MS_1M,
            0,
        )
    )
    (root / "journals" / "outcomes.jsonl").write_text(
        _rec(
            "OUTCOME",
            {
                "id": f"V11N-{t3 + 30 * MS_1M}-BS",
                "kind": "NEAR_MISS",
                "horizon_min": 60,
                "t_ms": t3 + 30 * MS_1M,
                "return_bps": -8.0,
                "mfe_bps": 4.0,
                "mae_bps": -12.0,
            },
            t3 + 90 * MS_1M,
            0,
        )
    )
    (root / "journals" / "episodes.jsonl").write_text(
        _rec("RUNNER_STARTED", {}, T0 - 30 * MS_1M, 0)
    )
    (root / "state.json").write_text(
        json.dumps(
            {
                "observation_start_ms": T0 - 30 * MS_1M,
                "counters": {"executed": 1, "blocked": 1, "candidates": 40},
                "position": None,
                "trades": 1,
                "wins": 0,
                "losses": 1,
                "cum_net_usd": -0.9,
            }
        )
    )
    return root


def _hashes(p: Path) -> dict[str, str]:
    return {str(x): hashlib.sha256(x.read_bytes()).hexdigest() for x in p.rglob("*") if x.is_file()}


def test_cross_strategy_forensics_end_to_end(tmp_path: Path) -> None:
    lite = build_root(tmp_path)  # V11-LITE synthetic root (3 trades) from the LITE audit tests
    v10 = _v10_root(tmp_path)
    v11w = _v11w_root(tmp_path)
    data = tmp_path
    before = _hashes(data / "btc")
    out, rep = SF.run(data, tmp_path / "reports" / "strategy_forensics", cutoff_ms=T0 + 330 * MS_1M)
    assert _hashes(data / "btc") == before, "strategy roots must be untouched"
    assert out.parent == tmp_path / "reports" / "strategy_forensics"
    written = {p.name for p in out.iterdir()}
    assert {
        "01_executive_summary.md",
        "02_comparison_table.md",
        "03_per_trade_table.md",
        "04_loss_mechanisms.md",
        "05_blocked_missed.md",
        "06_implementation_checks.md",
        "07_costs_exits.md",
        "08_hypotheses_counterfactuals.md",
        "09_validation_plan.md",
        "10_decisions.md",
        "strategy_forensics.json",
        "v10_demo_audit",
        "v11_lite_audit",
    } <= written
    P = rep["performance"]  # noqa: N806
    # Demo and Shadow are separate series
    assert (
        P["V10_DEMO"]["n"] == 1
        and P["V10_SHADOW"]["n"] == 1
        and P["V11_WARM"]["n"] == 1
        and P["V11_LITE"]["n"] == 3
        and P["V11_LITE_TWIN"]["n"] == 1
    )
    assert abs(P["V10_SHADOW"]["net_usd"] + 6.02) < 1e-9 and P["V10_DEMO"]["net_usd"] > 0
    # V10 shadow: immediately wrong, floored, target printed after the stop
    s10 = rep["trades"]["V10_SHADOW"][0]
    assert s10["stop_floored"] and s10["tp1_r"] == 1.0 and s10["mae_r_path"] <= -1.0
    assert {
        "DIRECTION_WRONG_IMMEDIATELY",
        "FLOORED_STOP_GEOMETRY",
        "STOP_HIT_THEN_TARGET_PRINTED",
    } <= set(s10["mechanisms"])
    assert (
        s10["trigger_conditions_pit"] == {"absorption": True, "reversal_print": True}
        and s10["market_pit"]["liq_burst_pct_120"] == 0.9
    )
    # V10 demo: real fills -> slippage vs reference; TP1 leg then exchange stop; a winner carries no mechanism
    d10 = rep["trades"]["V10_DEMO"][0]
    assert (
        d10["mode"] == "DEMO"
        and abs(d10["entry_slip_bps"] - 2.0) < 1e-6
        and d10["legs"][0]["kind"] == "TP1"
        and d10["legs"][1]["kind"] == "EXCHANGE_STOP"
    )
    assert (
        d10["mechanisms"] == []
        and d10["fill_latency_ms"] == 1500
        and d10["tp1_before_stop"] is True
    )
    # V11-WARM: strict by construction; gross positive, net negative -> cost mechanism; time exit
    w = rep["trades"]["V11_WARM"][0]
    assert (
        w["strict_gate"] == "PASS"
        and "COST_TURNED_GROSS_WIN_INTO_LOSS" in w["mechanisms"]
        and w["exit_reason"] == "TIME"
    )
    assert rep["implementation_checks"]["score_recompute_mismatches"]["V11_WARM"] == []
    # LITE: strict gate per trade; UNKNOWN never silently dropped
    lite_rows = rep["trades"]["V11_LITE"]
    assert [r["strict_gate"] for r in lite_rows] == ["FAIL", "PASS", "FAIL"]
    assert all(r["mechanisms"] for r in lite_rows if (r["net_r"] or 0) <= 0)
    assert rep["by_strict_gate_lite"]["PASS"]["n"] == 1
    # blocked / missed: V10 blocked-by-capacity setup carries its 60-min outcome; LITE near misses present
    assert rep["blocked_missed"]["V10"]["valid_setups_blocked_by_capacity"] == {
        "n": 1,
        "with_outcome": 1,
        "mean_ret_60m_bps": 55.0,
        "median_ret_60m_bps": 55.0,
        "positive_share": 1.0,
    }
    assert (
        rep["blocked_missed"]["V11_WARM"]["near_misses"]["with_outcome"] == 1
        and rep["blocked_missed"]["V11_LITE"]["near_misses"]["n"] == 2
    )
    # cross: overlap window from the latest observation start; same-minute decision matrix; twin displacement
    cr = rep["cross"]
    assert cr["overlap_window_utc"][0] == SF._iso(T0 - 30 * MS_1M)
    sm = cr["same_minute_decisions"]["V10 vs V11_LITE"]
    # T0: V10 LONG and LITE LONG (same side); T0+120: V10 B LONG vs LITE B SHORT (opposite side)
    assert (
        sm["same_minute"] == 2
        and sm["same_minute_same_side"] == 1
        and sm["same_minute_opposite_side"] == 1
    )
    assert (
        cr["lite_vs_strict_twin_at_decision"]["twin_would_reject"] == 2
        and cr["strict_entries_while_lite_in_position"]["displaced"] == 1
    )
    # counterfactuals typed; the LITE audit's exact subsets are carried over
    cf = rep["counterfactuals"]
    assert (
        cf["exact_historical"]["V11_LITE strict-only subset vs all"]["strict_PASS_only"]["n"] == 1
    )
    assert (
        any("exit 100 % at TP1" in k for k in cf["replay_with_assumptions"])
        and cf["not_yet_testable"]
    )
    # V10 shadow trade decided before the Demo activation: no shadow-after-activation bug flagged
    assert rep["implementation_checks"]["v10_shadow_after_demo_activation"] == []
    assert all(d["decision"].startswith("INSUFFICIENT EVIDENCE") for d in rep["decisions"].values())
    assert (
        rep["sub_audits"]["v11_lite_audit"]["n"] == 3
        and rep["sub_audits"]["v10_demo_audit"]["n"] == 1
    )
    text = (out / "01_executive_summary.md").read_text()
    assert (
        "## FACTS" in text and "## STRONG HYPOTHESES" in text and "## TOO EARLY TO CONCLUDE" in text
    )
    assert lite.exists() and v10.exists() and v11w.exists()


def test_missing_roots_and_unknown_mechanism(tmp_path: Path) -> None:
    _out, rep = SF.run(tmp_path, tmp_path / "reports", cutoff_ms=T0)
    assert (
        all(p["n"] == 0 for p in rep["performance"].values()) and not rep["roots"]["V10"]["exists"]
    )
    row = {
        "net_r": -0.3,
        "gross_r": -0.3,
        "cost_r": 0.0,
        "mfe_r": 0.3,
        "tp1_r": 0.6,
        "exit_reason": "STOP",
        "legs": [],
        "stop_floored": False,
    }
    labels, ev = SF.mechanisms(row)
    assert labels == ["UNKNOWN"] and ev
    assert SF.mechanisms({"net_r": 0.4})[0] == []
