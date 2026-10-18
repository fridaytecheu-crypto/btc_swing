# ruff: noqa: E501
"""READ-ONLY cross-strategy forensic evaluation: V10 (Demo and Shadow, SEPARATELY), V11-WARM (strict
shadow) and V11-LITE-WARM (relaxed-gate shadow + its strict control twin). Owner request 2026-10-18.

Isolated from every trading engine: it imports nothing from `btc_swing` and only loads the two existing
read-only audit scripts (`v11lw_forward_forensic_audit.py`, `v10_demo_post_trade_audit.py`) as library
modules. Every input is read through their immutable snapshots (append-only prefixes; one consistent
capture of each runner's `state.json`) at ONE fixed cutoff; nothing under a strategy root is written.
Output: `reports/strategy_forensics/<run>/` (executive summary, comparison table, per-trade tables,
loss mechanisms, blocked / missed opportunities, implementation checks, costs / exits, hypotheses with
falsification tests, validation plan, decisions) plus the full JSON, and the two underlying audits.

Point-in-time discipline: every per-trade field marked `pit` comes from the record the runner journaled
AT the decision; path statistics (MFE / MAE timing, what printed after the exit) are evaluation-only and
are never presented as information the strategy had. Demo and Shadow are never merged into one series.
A losing trade is not an incorrect decision: loss MECHANISMS are evidence-labelled, several may apply,
and UNKNOWN is a valid label."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
MS_1M = 60_000
NA = "N/A"
HORIZONS = (5, 15, 30, 60, 120)
STRATS = ("V10_DEMO", "V10_SHADOW", "V11_WARM", "V11_LITE", "V11_LITE_TWIN")
TP1_R = {"V10": 1.0, "V11": 0.6}  # frozen: V10 exits.tp1_r 1.0 (50 %), V11 exits.tp1_r 0.6 (40 %)
V10_FAMILIES = {
    "A": "liquidation absorption reversal",
    "B": "flow absorption at a level",
    "C": "liquidity break continuation",
}
V11_FAMILIES = {"A": "trend pullback", "B": "breakout expansion", "C": "sweep reversal"}


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


LA = _load("v11lw_forward_forensic_audit")  # LITE audit: snapshot, bars / raw path, trade analysis
VA = _load("v10_demo_post_trade_audit")  # V10 Demo audit: fills vs references, execution costs


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _ms(iso: Any) -> int | None:
    if iso is None:
        return None
    if isinstance(iso, int | float):
        return int(iso)
    try:
        return int(datetime.fromisoformat(str(iso)).timestamp() * 1000)
    except ValueError:
        return None


def _iso(ms: int | float | None) -> str:
    return (
        NA if ms is None else datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")
    )


def _v(x: Any, nd: int = 2) -> str:
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, int | float) and math.isfinite(float(x)):
        return f"{x:.{nd}f}"
    return NA if x is None else str(x)


# ----------------------------------------------------------------------------- one root, one snapshot
class Root:
    """A strategy root read through the LITE audit's snapshot (append-only prefixes + one consistent
    capture of the mutable state.json)."""

    def __init__(self, name: str, path: Path, cutoff_ms: int) -> None:
        self.name, self.path, self.cutoff = name, path, cutoff_ms
        self.exists = path.is_dir()
        self.snap = LA.PrefixSnapshot(cutoff_ms)
        self.state: dict[str, Any] = {}
        if self.exists:
            try:
                self.state = json.loads(self.snap.read_mutable(path / "state.json") or b"{}")
            except LA.SnapshotIntegrityError as e:
                self.state = {"__capture_error__": str(e)}
        self.bars = LA.Bars(path / "derived" / "bars_1m.jsonl", self.snap) if self.exists else None
        self.raw = LA.RawTrades(path / "raw" / "BTCUSDT", self.snap) if self.exists else None

    def journal(self, name: str, kind: str | None = None) -> list[dict[str, Any]]:
        if not self.exists:
            return []
        recs = LA.at_cutoff(self.snap.jsonl(self.path / "journals" / f"{name}.jsonl"), self.cutoff)
        return [r for r in recs if kind is None or r.get("kind") == kind]

    def demo_rows(self, name: str) -> list[dict[str, Any]]:
        if not self.exists:
            return []
        p = self.path / "demo" / f"{name}.jsonl"
        rows = self.snap.jsonl(p)
        return [
            r for r in rows if (_ms(r.get("closed_ms")) or _ms(r.get("ts")) or 0) <= self.cutoff
        ]

    def outcomes(self) -> dict[str, dict[int, dict[str, Any]]]:
        out: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
        for r in self.journal("outcomes", "OUTCOME"):
            d = r.get("data") or {}
            if d.get("id") and d.get("horizon_min") is not None:
                out[str(d["id"])][int(d["horizon_min"])] = d
        return out

    def chains(self) -> dict[str, bool]:
        if not self.exists:
            return {}
        out = {}
        for n in (
            "shadow_trades",
            "signals",
            "episodes",
            "nearmiss",
            "outcomes",
            "control_twin",
            "candidates",
        ):
            p = self.path / "journals" / f"{n}.jsonl"
            if p.exists():
                out[n] = bool(LA.chain_links_ok(self.snap.jsonl(p)))
        return out


# ----------------------------------------------------------------------------- path statistics (evaluation-only)
def path_stats(
    root: Root, s: float, ref: float, dist: float, t_in: int, t_out: int, tp1_r: float
) -> dict[str, Any]:
    out: dict[str, Any] = {"path_source": "none"}
    if root.raw is None or root.bars is None or dist <= 0 or t_out <= t_in:
        return out
    path, src = LA.price_path(root.raw, root.bars, t_in, t_out)
    out["path_source"] = src
    if path:
        rp = [(tm, s * (px - ref) / dist) for tm, px in path]
        fav, adv = max(rp, key=lambda x: x[1]), min(rp, key=lambda x: x[1])
        out.update(
            {
                "mfe_r_path": fav[1],
                "mae_r_path": adv[1],
                "min_to_mfe": (fav[0] - t_in) / MS_1M,
                "min_to_mae": (adv[0] - t_in) / MS_1M,
            }
        )
        first: dict[str, float | None] = {}
        for lv in (0.25, 0.5, tp1_r, 1.0, -0.5, -1.0):
            hit = next((x for x in rp if (x[1] >= lv if lv > 0 else x[1] <= lv)), None)
            first[f"{lv:+.2f}R"] = (hit[0] - t_in) / MS_1M if hit else None
        out["first_touch_min"] = first
        stop_t = first["-1.00R"] if first["-1.00R"] is not None else (t_out - t_in) / MS_1M
        tp1_t, half_t = first[f"{tp1_r:+.2f}R"], first["+0.50R"]
        out["tp1_before_stop"] = tp1_t is not None and tp1_t <= stop_t
        out["half_r_before_stop"] = half_t is not None and half_t <= stop_t
    post_end = min(t_out + 60 * MS_1M, root.cutoff)
    post, _ = LA.price_path(root.raw, root.bars, t_out + 1, post_end)
    out["post_exit_window_complete"] = t_out + 60 * MS_1M <= root.cutoff
    if post:
        rs = [s * (px - ref) / dist for _, px in post]
        out["post60_best_r"], out["post60_worst_r"] = max(rs), min(rs)
        out["post60_tp1_printed"] = max(rs) >= tp1_r
    pre = root.bars.between(t_in - 240 * MS_1M, t_in)
    c = [float(r["c"]) for r in pre if _f(r.get("c")) is not None]
    if len(c) >= 61:
        r1 = [(c[i] / c[i - 1] - 1) * 1e4 for i in range(len(c) - 60, len(c))]
        out["pre60_vol_bps"] = statistics.pstdev(r1)
        out["pre60_ret_bps_side_adj"] = s * (c[-1] / c[-61] - 1) * 1e4
        hi = max(float(r["h"]) for r in pre[-60:])
        lo = min(float(r["l"]) for r in pre[-60:])
        out["pre60_range_bps"] = (hi / lo - 1) * 1e4
    if len(c) >= 240:
        out["pre240_ret_bps_side_adj"] = s * (c[-1] / c[-240] - 1) * 1e4
    return out


# ----------------------------------------------------------------------------- loss mechanisms (multi-label, UNKNOWN allowed)
def mechanisms(row: dict[str, Any]) -> tuple[list[str], list[str]]:
    net, gross, cost = row.get("net_r"), row.get("gross_r"), row.get("cost_r")
    if net is None or net > 0:
        return [], []
    labels: list[str] = []
    ev: list[str] = []
    mfe = row.get("mfe_r") if row.get("mfe_r") is not None else row.get("mfe_r_path")
    tp1_r = row["tp1_r"]
    if mfe is not None and mfe < 0.15:
        labels.append("DIRECTION_WRONG_IMMEDIATELY")
        ev.append(f"MFE {mfe:.2f}R < 0.15R")
    if mfe is not None and mfe >= 0.5:
        labels.append("FAVOURABLE_THEN_REVERSED")
        ev.append(f"MFE {mfe:.2f}R then net {net:.2f}R")
    if any(
        str(lg.get("kind", "")).startswith("TP1") or str(lg.get("kind", "")) == "T1"
        for lg in row.get("legs") or []
    ):
        labels.append("PARTIAL_TAKEN_THEN_REST_LOST")
        ev.append("TP1 leg filled, remainder closed at a loss / break-even")
    post = row.get("post60_best_r")
    if (
        row.get("exit_reason") in ("STOP", "BE", "TRAIL", "EXCHANGE_STOP", "TP1_THEN_STOP")
        and post is not None
        and post >= tp1_r
    ):
        labels.append("STOP_HIT_THEN_TARGET_PRINTED")
        ev.append(f"post-exit 60 min best {post:.2f}R >= TP1 {tp1_r}R")
    if row.get("stop_floored"):
        labels.append("FLOORED_STOP_GEOMETRY")
        ev.append(
            f"stop widened to the floor ({row.get('stop_pct')} %), structural {row.get('structural_stop_pct')} %"
        )
    if row.get("exit_reason") in ("TIME", "STALL", "HARD") and post is not None and post >= 0.5:
        labels.append("TIME_EXIT_BEFORE_MOVE")
        ev.append(f"time/stall exit; best {post:.2f}R within 60 min after")
    if gross is not None and gross > 0 >= net:
        labels.append("COST_TURNED_GROSS_WIN_INTO_LOSS")
        ev.append(f"gross +{gross:.2f}R, cost {cost}R")
    elif cost is not None and gross is not None and abs(gross) < cost:
        labels.append("COST_DOMINATED")
        ev.append(f"|gross| {abs(gross):.2f}R < cost {cost:.2f}R")
    es = row.get("entry_slip_bps")
    if es is not None and es > 2.0:
        labels.append("EXECUTION_ENTRY_SLIPPAGE")
        ev.append(f"entry fill {es:.1f} bps worse than the decision reference")
    ss = row.get("stop_exit_slip_bps")
    if ss is not None and ss > 5.0:
        labels.append("EXECUTION_STOP_SLIPPAGE")
        ev.append(f"stop filled {ss:.1f} bps through the level")
    if row.get("armed_min_before_entry") is not None and row["armed_min_before_entry"] >= 20:
        labels.append("STALE_ARM_FLAG")
        ev.append(f"armed {row['armed_min_before_entry']:.0f} min before entry (flag, not a cause)")
    if not labels:
        labels.append("UNKNOWN")
        ev.append("no evidence-supported mechanism from the journaled record and the price path")
    return labels, ev


# ----------------------------------------------------------------------------- per-strategy trade reconstruction
def _common(
    strategy: str,
    mode: str,
    root: Root,
    sid: str,
    side: int,
    dec_ms: int,
    t_in: int,
    t_out: int,
    ref: float,
    stop: float,
    tp1: float,
    tp2: float,
    tp1_r: float,
) -> dict[str, Any]:
    dist = abs(ref - stop)
    row: dict[str, Any] = {
        "strategy": strategy,
        "mode": mode,
        "signal_id": sid,
        "decision_utc": _iso(dec_ms),
        "decision_ms": dec_ms,
        "entry_utc": _iso(t_in),
        "exit_utc": _iso(t_out),
        "hour_utc": datetime.fromtimestamp(dec_ms / 1000, tz=UTC).hour,
        "side": "LONG" if side > 0 else "SHORT",
        "entry_ref": ref,
        "stop": stop,
        "stop_dist_bps": dist / ref * 1e4 if ref else None,
        "tp1": tp1,
        "tp2": tp2,
        "tp1_r": tp1_r,
    }
    row.update(path_stats(root, float(side), ref, dist, t_in, t_out, tp1_r))
    return row


def v10_shadow_rows(
    root: Root, sigs: dict[str, dict[str, Any]], outs: dict[str, dict[int, dict[str, Any]]]
) -> list[dict[str, Any]]:
    rows = []
    for r in root.journal("shadow_trades", "SHADOW_CLOSED"):
        t = r["data"]
        if int(t.get("exit_ms") or 0) > root.cutoff:
            continue
        s = int(t["side"])
        sig = sigs.get(str(t["signal_id"]), {})
        f = sig.get("features") or {}
        row = _common(
            "V10",
            "SHADOW",
            root,
            str(t["signal_id"]),
            s,
            int(sig.get("decision_ms") or t["entry_ms"]),
            int(t["entry_ms"]),
            int(t["exit_ms"]),
            float(t["entry_ref"]),
            float(t["stop"]),
            float(t["tp1"]),
            float(t["tp2"]),
            TP1_R["V10"],
        )
        row.update(
            {
                "family": t.get("family"),
                "setup_class": V10_FAMILIES.get(str(t.get("family")), NA),
                "score": None,
                "contrib": None,
                "trigger_conditions_pit": sig.get("trigger_conditions"),
                "gate": "V10 family state machine (ARMED->TRIGGERED)",
                "stop_pct": _f(sig.get("stop_pct")),
                "stop_floored": sig.get("stop_note") == "FLOORED",
                "structural_stop_pct": None,
                "entry_fill": _f(t.get("entry_fill")),
                "entry_slip_bps": None,
                "stop_exit_slip_bps": None,
                "market_pit": {
                    k: _f(f.get(k))
                    for k in (
                        "last",
                        "atr5",
                        "atr1h",
                        "spread_bps",
                        "imb_10",
                        "imb_30",
                        "imb_60",
                        "delta_30",
                        "depth_imb",
                        "liq_burst_pct_120",
                        "liq_imb_120",
                        "dist_hi",
                        "dist_lo",
                        "intensity_pct_10",
                        "ask_repl",
                        "bid_repl",
                    )
                    if k in f
                },
                "legs": t.get("legs"),
                "exits": None,
                "exit_reason": t.get("exit_reason"),
                "hold_min": _f(t.get("hold_min")),
                "gross_usd": _f(t.get("gross_usd")),
                "fee_usd": _f(t.get("fee_usd")),
                "slip_usd": _f(t.get("slip_usd")),
                "funding_usd": 0.0,
                "net_usd": _f(t.get("net_usd")),
                "net_r": _f(t.get("net_r")),
                "gross_r": _f(t.get("gross_r")),
                "cost_r": ((_f(t.get("gross_r")) or 0) - (_f(t.get("net_r")) or 0)),
                "mfe_r": _f(t.get("mfe_r")),
                "mae_r": _f(t.get("mae_r")),
                "armed_min_before_entry": (
                    (int(t["entry_ms"]) - (_ms(sig.get("armed_ts")) or int(t["entry_ms"]))) / MS_1M
                )
                if sig.get("armed_ts")
                else None,
                "decision_latency_ms": sig.get("decision_latency_ms"),
                "qty": _f(t.get("qty")),
                "leverage": _f(t.get("leverage")),
                "risk_usdt": _f(t.get("risk_usdt")),
                "outcomes_bps": {
                    h: _f(o.get("return_bps"))
                    for h, o in sorted(outs.get(str(t["signal_id"]), {}).items())
                },
            }
        )
        row["mechanisms"], row["mechanism_evidence"] = mechanisms(row)
        rows.append(row)
    return rows


def v10_demo_rows(
    root: Root, sigs: dict[str, dict[str, Any]], outs: dict[str, dict[int, dict[str, Any]]]
) -> list[dict[str, Any]]:
    rows = []
    for t in root.demo_rows("demo_trades"):
        if not t.get("signal_id"):
            continue
        s = 1 if t.get("side") == "LONG" else -1
        sig = sigs.get(str(t["signal_id"]), {})
        f = sig.get("features") or {}
        dec = _ms(t.get("decision_ts")) or int(sig.get("decision_ms") or 0)
        t_in = _ms(t.get("fill_ts")) or dec
        ex_ms = [m for m in (_ms(e.get("time")) for e in (t.get("exits") or [])) if m]
        t_out = (
            max(ex_ms) if ex_ms else (_ms(t.get("closed_ms")) or _ms(t.get("closed_ts")) or t_in)
        )
        ref, fill, stop = float(t["entry_ref"]), _f(t.get("entry_price")), float(t["stop"])
        row = _common(
            "V10",
            "DEMO",
            root,
            str(t["signal_id"]),
            s,
            dec,
            t_in,
            t_out,
            ref,
            stop,
            float(t["tp1"]),
            float(t["tp2"]),
            TP1_R["V10"],
        )
        risk_planned = _f(t.get("planned_risk_usdt")) or 5.0
        stop_legs = [
            _f(e.get("price"))
            for e in (t.get("exits") or [])
            if not str(e.get("link") or "").endswith(("-T1", "-T2", "-TC"))
            and _f(e.get("price")) is not None
        ]
        legs = [
            {
                "kind": (
                    "TP1"
                    if str(e.get("link", "")).endswith("-T1")
                    else "TP2"
                    if str(e.get("link", "")).endswith("-T2")
                    else "MARKET_CLOSE"
                    if str(e.get("link", "")).endswith("-TC")
                    else "EXCHANGE_STOP"
                ),
                "price": _f(e.get("price")),
                "qty": _f(e.get("qty")),
                "time": e.get("time"),
            }
            for e in (t.get("exits") or [])
        ]
        net, gross, fees = _f(t.get("net_pnl")), _f(t.get("gross_pnl")), _f(t.get("fees")) or 0.0
        row.update(
            {
                "family": t.get("family"),
                "setup_class": V10_FAMILIES.get(str(t.get("family")), NA),
                "score": None,
                "contrib": None,
                "trigger_conditions_pit": sig.get("trigger_conditions"),
                "gate": "V10 family state machine (ARMED->TRIGGERED)",
                "stop_pct": _f(sig.get("stop_pct")) or (abs(ref - stop) / ref * 100),
                "stop_floored": sig.get("stop_note") == "FLOORED",
                "structural_stop_pct": None,
                "entry_fill": fill,
                "entry_slip_bps": (s * (fill / ref - 1) * 1e4) if fill else None,
                "stop_exit_slip_bps": (
                    statistics.fmean(
                        s * (stop - px) / stop * 1e4 for px in stop_legs if px is not None
                    )
                    if stop_legs
                    else None
                ),
                "market_pit": {
                    k: _f(f.get(k))
                    for k in (
                        "last",
                        "atr5",
                        "atr1h",
                        "spread_bps",
                        "imb_10",
                        "imb_30",
                        "imb_60",
                        "delta_30",
                        "depth_imb",
                        "liq_burst_pct_120",
                        "liq_imb_120",
                        "dist_hi",
                        "dist_lo",
                        "intensity_pct_10",
                        "ask_repl",
                        "bid_repl",
                    )
                    if k in f
                },
                "legs": legs,
                "exits": t.get("exits"),
                "exit_reason": t.get("exit_reason"),
                "hold_min": (t_out - t_in) / MS_1M if t_out and t_in else None,
                "gross_usd": gross,
                "fee_usd": fees,
                "slip_usd": ((fill - ref) * s * float(t.get("qty") or 0)) if fill else None,
                "funding_usd": _f(t.get("funding")) or 0.0,
                "net_usd": net,
                "net_r": _f(t.get("R_net_vs_planned")),
                "gross_r": (gross / risk_planned) if gross is not None else None,
                "cost_r": ((gross or 0) - (net or 0)) / risk_planned,
                "mfe_r": None,
                "mae_r": None,
                "armed_min_before_entry": ((dec - (_ms(sig.get("armed_ts")) or dec)) / MS_1M)
                if sig.get("armed_ts")
                else None,
                "decision_latency_ms": sig.get("decision_latency_ms"),
                "fill_latency_ms": (t_in - dec) if (t_in and dec) else None,
                "qty": _f(t.get("qty")),
                "leverage": _f(t.get("leverage")),
                "risk_usdt": risk_planned,
                "actual_risk_usdt": _f(t.get("actual_risk_usdt")),
                "outcomes_bps": {
                    h: _f(o.get("return_bps"))
                    for h, o in sorted(outs.get(str(t["signal_id"]), {}).items())
                },
            }
        )
        row["mfe_r"], row["mae_r"] = row.get("mfe_r_path"), row.get("mae_r_path")
        row["mechanisms"], row["mechanism_evidence"] = mechanisms(row)
        rows.append(row)
    return rows


def v11_rows(
    root: Root, strategy: str, mode: str, outs: dict[str, dict[int, dict[str, Any]]], lite: bool
) -> list[dict[str, Any]]:
    """V11-WARM shadow trades, V11-LITE shadow trades, or the LITE control twin's trades (TWIN_CLOSED)."""
    recs = (
        root.journal("control_twin", "TWIN_CLOSED")
        if mode == "TWIN"
        else root.journal("shadow_trades", "SHADOW_CLOSED")
    )
    rows = []
    for r in recs:
        t = r["data"]
        if int(t.get("exit_ms") or 0) > root.cutoff:
            continue
        s = int(t["side"])
        d = t.get("decision") or {}
        tmpl = (
            t.get("selected_template")
            or t.get("family")
            or d.get("selected_template")
            or d.get("family")
        )
        ind, contrib, ctx, ctl = (
            d.get("indicators") or {},
            d.get("contrib") or {},
            d.get("context") or {},
            d.get("control_twin") or {},
        )
        if any(t.get(k) is None for k in ("entry_ref", "stop0", "tp1", "tp2", "decision_ms")):
            # a record without the plan geometry (e.g. an abbreviated twin record): keep what exists
            rows.append({
                "strategy": strategy, "mode": mode, "signal_id": str(t.get("signal_id")), "reconstruction": "partial",
                "decision_ms": int(t.get("decision_ms") or 0), "decision_utc": _iso(_f(t.get("decision_ms"))), "hour_utc": datetime.fromtimestamp(int(t.get("decision_ms") or 0) / 1000, tz=UTC).hour,
                "side": "LONG" if s > 0 else "SHORT", "family": tmpl, "setup_class": V11_FAMILIES.get(str(tmpl), NA), "score": _f(t.get("score_entry")),
                "exit_reason": t.get("exit_reason"), "hold_min": _f(t.get("hold_min")), "net_usd": _f(t.get("net_usd")), "net_r": _f(t.get("net_r")),
                "gross_r": _f(t.get("gross_r")), "cost_r": _f(t.get("cost_r")), "mfe_r": _f(t.get("mfe_r")), "mae_r": _f(t.get("mae_r")), "tp1_r": TP1_R["V11"],
                "legs": t.get("legs") or [], "stop_floored": False, "strict_gate": "PASS" if not lite or mode == "TWIN" else NA, "mechanisms": [], "mechanism_evidence": [],
            })  # fmt: skip
            rows[-1]["mechanisms"], rows[-1]["mechanism_evidence"] = mechanisms(rows[-1])
            continue
        entry_ms = int(t.get("entry_ms") or t["decision_ms"])
        exit_ms = int(t.get("exit_ms") or (entry_ms + (_f(t.get("hold_min")) or 0) * MS_1M))
        row = _common(
            strategy,
            mode,
            root,
            str(t["signal_id"]),
            s,
            int(t["decision_ms"]),
            entry_ms,
            exit_ms,
            float(t["entry_ref"]),
            float(t["stop0"]),
            float(t["tp1"]),
            float(t["tp2"]),
            TP1_R["V11"],
        )
        labels = d.get("structure_labels") or {}
        strict = labels.get(tmpl) if lite else True
        if lite and mode == "SHADOW":
            gate = f"structure labels only; selected template {tmpl} strict gate {'PASS' if strict else 'FAIL' if strict is False else NA}"
        else:
            gate = "strict V11 structure gate PASSED by construction"
        a5 = _f(ind.get("atr5"))
        row.update(
            {
                "family": tmpl,
                "setup_class": V11_FAMILIES.get(str(tmpl), NA),
                "score": _f(t.get("score_entry")) or _f(d.get("score")),
                "contrib": {g: _f(contrib.get(g)) for g in LA.GROUPS} if contrib else None,
                "score_other_side": _f(d.get("score_short") if s > 0 else d.get("score_long")),
                "trigger_conditions_pit": None,
                "gate": gate,
                "strict_gate": ("PASS" if strict else "FAIL" if strict is False else NA)
                if lite and mode == "SHADOW"
                else "PASS",
                "control_would_take": ctl.get("would_take"),
                "control_reason": ctl.get("reason"),
                "incremental": d.get("incremental"),
                "stop_pct": _f(t.get("stop_pct")),
                "stop_floored": "FLOORED" in str(d.get("stop_note") or ""),
                "structural_stop_pct": (
                    abs(float(t["entry_ref"]) - float(d["structural_stop"]))
                    / float(t["entry_ref"])
                    * 100
                )
                if _f(d.get("structural_stop")) is not None
                else None,
                "entry_fill": _f(t.get("entry_fill")),
                "entry_slip_bps": None,
                "stop_exit_slip_bps": None,
                "market_pit": {
                    "rsi7": _f(ind.get("rsi7")),
                    "rsi14": _f(ind.get("rsi14")),
                    "adx14": _f(ind.get("adx14")),
                    "atr5_bps": (a5 / float(t["entry_ref"]) * 1e4) if a5 else None,
                    "vol_tercile": _f(ind.get("vol_tercile")),
                    "struct15": _f(ind.get("struct15")),
                    "dist_vwap_atr": _f(ind.get("dist_vwap")),
                    "vwap_slope": _f(ind.get("vwap_slope")),
                    "imb5": _f(ind.get("imb5")),
                    "imb15": _f(ind.get("imb15")),
                    "delta15": _f(ind.get("delta15")),
                    "rvol5": _f(ind.get("rvol5")),
                    "oi_chg_15m": _f(ind.get("oi_chg_15m")),
                    "funding": _f(ind.get("funding")),
                    "fib_bucket": (ctx.get("fib") or {}).get("bucket_atr"),
                    "range60_pos": _f(((ctx.get("range") or {}).get("60m") or {}).get("position")),
                },
                "legs": t.get("legs"),
                "exits": None,
                "exit_reason": t.get("exit_reason"),
                "hold_min": _f(t.get("hold_min")),
                "gross_usd": _f(t.get("gross_usd")),
                "fee_usd": _f(t.get("fee_usd")),
                "slip_usd": _f(t.get("slip_usd")),
                "funding_usd": _f(t.get("funding_usd")),
                "net_usd": _f(t.get("net_usd")),
                "net_r": _f(t.get("net_r")),
                "gross_r": _f(t.get("gross_r")),
                "cost_r": _f(t.get("cost_r")),
                "mfe_r": _f(t.get("mfe_r")),
                "mae_r": _f(t.get("mae_r")),
                "armed_min_before_entry": ((int(t["decision_ms"]) - int(d["arm_since_ms"])) / MS_1M)
                if d.get("arm_since_ms")
                else (
                    ((int(t["decision_ms"]) - int(d["armed_ms"])) / MS_1M)
                    if d.get("armed_ms")
                    else None
                ),
                "decision_latency_ms": None,
                "qty": _f(t.get("qty")),
                "leverage": _f(t.get("leverage")),
                "risk_usdt": _f(t.get("risk_usdt")),
                "outcomes_bps": {
                    h: _f(o.get("return_bps"))
                    for h, o in sorted(outs.get(str(t["signal_id"]), {}).items())
                },
            }
        )
        row["mechanisms"], row["mechanism_evidence"] = mechanisms(row)
        rows.append(row)
    return rows


# ----------------------------------------------------------------------------- aggregation
def perf(rows: list[dict[str, Any]]) -> dict[str, Any]:
    net = [r["net_usd"] for r in rows if r.get("net_usd") is not None]
    rr = [r["net_r"] for r in rows if r.get("net_r") is not None]
    gr = [r["gross_r"] for r in rows if r.get("gross_r") is not None]
    cr = [r["cost_r"] for r in rows if r.get("cost_r") is not None]
    w, losses = [x for x in net if x > 0], [x for x in net if x <= 0]
    cum = peak = dd = 0.0
    for x in net:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    hold = [r["hold_min"] for r in rows if r.get("hold_min") is not None]
    return {
        "n": len(rows),
        "wins": len(w),
        "losses": len(losses),
        "win_rate": len(w) / len(net) if net else None,
        "net_usd": sum(net),
        "net_r_sum": sum(rr),
        "avg_net_r": statistics.fmean(rr) if rr else None,
        "avg_gross_r": statistics.fmean(gr) if gr else None,
        "avg_cost_r": statistics.fmean(cr) if cr else None,
        "pf": (sum(w) / -sum(losses)) if losses and sum(losses) < 0 else (math.inf if w else None),
        "max_dd_usd": dd,
        "avg_hold_min": statistics.fmean(hold) if hold else None,
        "fees_usd": sum(r.get("fee_usd") or 0 for r in rows),
        "slip_usd": sum(r.get("slip_usd") or 0 for r in rows),
        "funding_usd": sum(r.get("funding_usd") or 0 for r in rows),
        "avg_mfe_r": statistics.fmean([r["mfe_r"] for r in rows if r.get("mfe_r") is not None])
        if any(r.get("mfe_r") is not None for r in rows)
        else None,
        "avg_mae_r": statistics.fmean([r["mae_r"] for r in rows if r.get("mae_r") is not None])
        if any(r.get("mae_r") is not None for r in rows)
        else None,
        "first_utc": _iso(min(r["decision_ms"] for r in rows)) if rows else NA,
        "last_utc": _iso(max(r["decision_ms"] for r in rows)) if rows else NA,
    }


def by(rows: list[dict[str, Any]], key: Any) -> dict[str, dict[str, Any]]:
    g: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        k = key(r)
        g[str(k) if k is not None else NA].append(r)
    return {k: perf(v) for k, v in sorted(g.items())}


def windows(rows: list[dict[str, Any]], cutoff: int) -> dict[str, dict[str, Any]]:
    return {
        name: perf([r for r in rows if h is None or r["decision_ms"] >= cutoff - h * 3_600_000])
        for name, h in (("24h", 24), ("72h", 72), ("7d", 168), ("all", None))
    }


def hour_bucket(h: int) -> str:
    return "00-08 UTC" if h < 8 else "08-16 UTC" if h < 16 else "16-24 UTC"


def vol_bucket(rows: list[dict[str, Any]], r: dict[str, Any]) -> str:
    v = r.get("pre60_vol_bps")
    vals = sorted(x["pre60_vol_bps"] for x in rows if x.get("pre60_vol_bps") is not None)
    if v is None or len(vals) < 3:
        return NA
    lo, hi = vals[len(vals) // 3], vals[2 * len(vals) // 3]
    return "low vol" if v <= lo else "high vol" if v >= hi else "mid vol"


# ----------------------------------------------------------------------------- blocked / missed opportunities
def blocked_missed(
    root: Root, strategy: str, outs: dict[str, dict[int, dict[str, Any]]], nm_id: Any
) -> dict[str, Any]:
    blocked = [r["data"] for r in root.journal("signals", "SIGNAL_BLOCKED")]
    by_block: Counter[str] = Counter()
    valid_setups = []  # blocked only by capacity rules (position open / caps / safety), not by an invalid plan
    for d in blocked:
        bl = list(d.get("blockers") or [])
        for b in bl:
            by_block[str(b).split(":")[0]] += 1
        if bl and all(
            str(b).startswith(("POSITION_OPEN", "MAX_", "DAILY_SAFETY", "MAX_TRADES", "DEMO_CAP"))
            for b in bl
        ):
            o = outs.get(str(d.get("signal_id")), {})
            valid_setups.append(
                {
                    "signal_id": d.get("signal_id"),
                    "ts": d.get("ts") or d.get("decision_ts"),
                    "family": d.get("family") or d.get("selected_template"),
                    "side": d.get("side"),
                    "score": _f(d.get("score")),
                    "blockers": bl,
                    "ret_60m_bps": _f((o.get(60) or {}).get("return_bps")),
                    "mfe_60m_bps": _f((o.get(60) or {}).get("mfe_bps")),
                    "mae_60m_bps": _f((o.get(60) or {}).get("mae_bps")),
                }
            )
    nms = [r["data"] for r in root.journal("nearmiss", "NEAR_MISS")]
    nm_rows = []
    for d in nms:
        rid = nm_id(d)
        o = outs.get(rid, {}) if rid else {}
        nm_rows.append(
            {
                "id": rid,
                "ts": d.get("ts"),
                "family": d.get("family") or d.get("selected_template"),
                "side": d.get("side"),
                "score": _f(d.get("score")),
                "stage": d.get("stage"),
                "failing": d.get("failing"),
                "ret_60m_bps": _f((o.get(60) or {}).get("return_bps")),
                "mfe_60m_bps": _f((o.get(60) or {}).get("mfe_bps")),
            }
        )

    def dir_stats(xs: list[dict[str, Any]]) -> dict[str, Any]:
        v = [x["ret_60m_bps"] for x in xs if x.get("ret_60m_bps") is not None]
        return {
            "n": len(xs),
            "with_outcome": len(v),
            "mean_ret_60m_bps": statistics.fmean(v) if v else None,
            "median_ret_60m_bps": statistics.median(v) if v else None,
            "positive_share": (sum(1 for x in v if x > 0) / len(v)) if v else None,
        }

    return {
        "strategy": strategy,
        "blocked_total": len(blocked),
        "blocked_by_reason": dict(by_block),
        "valid_setups_blocked_by_capacity": dir_stats(valid_setups),
        "valid_setups_blocked_rows": valid_setups[-50:],
        "near_misses": dir_stats(nm_rows),
        "near_miss_by_family": {
            k: dir_stats(v) for k, v in sorted(_group(nm_rows, "family").items())
        },
        "near_miss_rows": nm_rows[-50:],
        "note": "60-min returns from the decision close in the direction of the setup; directional opportunity study only (no stop, no sizing, no costs)",
    }


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    g: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        g[str(r.get(key))].append(r)
    return g


# ----------------------------------------------------------------------------- cross-strategy (overlapping window, opportunity level)
def cross(
    roots: dict[str, Root], series: dict[str, list[dict[str, Any]]], cutoff: int
) -> dict[str, Any]:
    starts = {
        n: int(r.state.get("observation_start_ms") or 0)
        for n, r in roots.items()
        if r.exists and r.state.get("observation_start_ms")
    }
    overlap_start = max(starts.values()) if starts else None
    out: dict[str, Any] = {
        "observation_start_utc": {k: _iso(v) for k, v in starts.items()},
        "overlap_window_utc": [_iso(overlap_start), _iso(cutoff)] if overlap_start else None,
        "in_overlap": {},
    }
    if overlap_start:
        for name, rows in series.items():
            sub = [r for r in rows if r["decision_ms"] >= overlap_start]
            out["in_overlap"][name] = perf(sub) | {
                "loss_mechanisms": dict(Counter(m for r in sub for m in r.get("mechanisms", [])))
            }

    # decision-level comparison at the same minute: V11-WARM (strict) vs V11-LITE (relaxed) vs V10
    def decisions(root: Root) -> dict[int, dict[str, Any]]:
        d: dict[int, dict[str, Any]] = {}
        for r in root.journal("signals"):
            if r.get("kind") in ("SIGNAL_EXECUTED", "SIGNAL_BLOCKED"):
                x = r["data"]
                m = int(x.get("decision_ms") or _ms(x.get("decision_ts")) or _ms(x.get("ts")) or 0)
                m -= m % MS_1M
                d[m] = {
                    "kind": r["kind"],
                    "side": x.get("side"),
                    "family": x.get("family") or x.get("selected_template"),
                    "blockers": x.get("blockers"),
                    "score": _f(x.get("score")),
                }
        return d

    dec = {n: decisions(r) for n, r in roots.items() if r.exists}
    pairs = {}
    names = list(dec)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            same_minute = set(dec[a]) & set(dec[b])
            agree = sum(1 for m in same_minute if dec[a][m]["side"] == dec[b][m]["side"])
            pairs[f"{a} vs {b}"] = {
                "decisions_a": len(dec[a]),
                "decisions_b": len(dec[b]),
                "same_minute": len(same_minute),
                "same_minute_same_side": agree,
                "same_minute_opposite_side": len(same_minute) - agree,
                "only_a": len(set(dec[a]) - set(dec[b])),
                "only_b": len(set(dec[b]) - set(dec[a])),
            }
    out["same_minute_decisions"] = pairs
    lite = roots.get("V11_LITE")
    if lite and lite.exists:
        sig = [r["data"] for r in lite.journal("signals", "SIGNAL_EXECUTED")]
        ctl = Counter(
            str((d.get("control_twin") or {}).get("reason", NA)).split("|")[0].split(":")[0]
            for d in sig
        )
        out["lite_vs_strict_twin_at_decision"] = {
            "lite_executed": len(sig),
            "twin_would_take": sum(
                1 for d in sig if (d.get("control_twin") or {}).get("would_take")
            ),
            "twin_would_reject": sum(
                1 for d in sig if (d.get("control_twin") or {}).get("would_take") is False
            ),
            "reject_reasons": dict(ctl),
        }
        tw = [r["data"] for r in lite.journal("control_twin") if r.get("kind") == "TWIN_EXECUTED"]
        trades = series.get("V11_LITE", [])
        displaced = [
            t
            for t in tw
            if any(
                x["decision_ms"]
                <= int(t.get("decision_ms") or 0)
                < x["decision_ms"] + (x.get("hold_min") or 0) * MS_1M
                and x["decision_ms"] != int(t.get("decision_ms") or 0)
                for x in trades
            )
        ]
        out["strict_entries_while_lite_in_position"] = {
            "twin_executed": len(tw),
            "displaced": len(displaced),
            "rows": [
                {
                    "ts": _iso(int(t.get("decision_ms") or 0)),
                    "family": t.get("family"),
                    "side": t.get("side"),
                    "score": _f(t.get("score")),
                }
                for t in displaced
            ],
        }
    return out


# ----------------------------------------------------------------------------- counterfactuals (typed)
def counterfactuals(
    series: dict[str, list[dict[str, Any]]], lite_rep: dict[str, Any] | None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "exact_historical": {},
        "replay_with_assumptions": {},
        "directional_opportunity": {},
        "not_yet_testable": [],
    }
    if lite_rep:
        cf = lite_rep.get("counterfactuals") or {}
        out["exact_historical"]["V11_LITE strict-only subset vs all"] = {
            "all": cf.get("baseline"),
            "strict_PASS_only": cf.get("strict_only_subset"),
            "strict_FAIL_only": cf.get("strict_fail_subset"),
        }
        for k, v in (cf.get("soft_filter") or {}).items():
            out["exact_historical"][f"V11_LITE soft structure term {k} (executed subset)"] = {
                "kept": v.get("executed_kept"),
                "removed": v.get("executed_removed"),
                "removed_ids": v.get("removed_ids"),
            }
            out["directional_opportunity"][f"V11_LITE soft term {k}: near-miss additions"] = {
                "n": v.get("near_miss_additions_n"),
                "mean_60m_bps": v.get("near_miss_additions_60m_mean_bps"),
                "positive_share": v.get("near_miss_additions_60m_positive_share"),
                "caveat": v.get("caveat"),
            }
    for name, rows in series.items():
        if not rows:
            continue
        # exit-all-at-TP1 replay: fill at the TP1 level when the path reached it before -1R, else the actual result
        alt = []
        for r in rows:
            if r.get("tp1_before_stop") is True and r.get("cost_r") is not None:
                alt.append(r["tp1_r"] - r["cost_r"])
            elif r.get("net_r") is not None:
                alt.append(r["net_r"])
        if alt:
            out["replay_with_assumptions"][
                f"{name}: exit 100 % at TP1 ({rows[0]['tp1_r']}R) when reached before -1R, else actual"
            ] = {
                "n": len(alt),
                "net_r_sum": sum(alt),
                "actual_net_r_sum": sum(r["net_r"] or 0 for r in rows),
                "assumptions": "fill exactly at the TP1 level; the trade's own cost_r kept; path from raw trades / 1m bars (bar-level ordering ambiguous when both +TP1 and -1R print in one bar)",
            }
        # structural (unfloored) stop study: LITE / V11 journal the structural stop
        st = [r for r in rows if r.get("stop_floored") and r.get("structural_stop_pct") is not None]
        if st:
            out["directional_opportunity"][f"{name}: FLOORED stops"] = {
                "n_floored": len(st),
                "n_total": len(rows),
                "net_r_floored": sum(r["net_r"] or 0 for r in st),
                "net_r_structural_trades": sum(
                    r["net_r"] or 0 for r in rows if not r.get("stop_floored")
                ),
                "note": "a tighter structural stop changes qty and R; whether it would have been hit first needs the tick path per trade (the LITE audit's first-touch table)",
            }
    out["not_yet_testable"] = [
        "regime-aware filtering (ADX / volatility tercile / session): sub-buckets hold 1-6 trades; needs >= 30 trades per bucket or a historical replay on the consumed V11 TRAIN / VALIDATION frames with a NEW pre-registration",
        "momentum / entry-timing confirmation (wait N bars after trigger): requires a replay engine run; the live journals do not contain the un-taken alternative entries",
        "fee-aware minimum expected move (skip when stop distance < k x round-trip cost): can be stated exactly from the journals as a filter on stop_dist_bps, but its effect on the resulting trade set is a replay (position-open blocking changes)",
        "V10 template priority / A-B overlap: V10 families are independent state machines; overlap statistics need the episode journal replay",
    ]
    return out


# ----------------------------------------------------------------------------- hypotheses, plan, decisions
def hypotheses(
    series: dict[str, list[dict[str, Any]]], mech: dict[str, Counter[str]]
) -> list[dict[str, Any]]:
    hs: list[dict[str, Any]] = []
    lite = series.get("V11_LITE", [])
    if lite:
        fail = [r for r in lite if r.get("strict_gate") == "FAIL"]
        hs.append(
            {
                "id": "H1",
                "hypothesis": "Removing the A/B/C structure gate admits entries with systematically worse expectancy (the strict-FAIL subset carries the loss).",
                "evidence_now": f"LITE strict-FAIL {len(fail)} trades net {sum(r['net_r'] or 0 for r in fail):.2f}R vs strict-PASS {len(lite) - len(fail)} trades net {sum(r['net_r'] or 0 for r in lite if r.get('strict_gate') != 'FAIL'):.2f}R",
                "falsification": "pre-register: over the next >= 30 LITE trades the strict-FAIL subset's mean net R is NOT below the strict-PASS subset's by more than 0.2R (one-sided, 90 %); if not falsified, structure as a soft term is the LITE.1 candidate",
                "status": "testable forward; exact on the executed subset",
            }
        )
    for name in ("V10_DEMO", "V10_SHADOW", "V11_WARM", "V11_LITE"):
        rows = series.get(name, [])
        m = mech.get(name, Counter())
        n_loss = sum(1 for r in rows if (r.get("net_r") or 0) <= 0)
        if rows and m.get("COST_TURNED_GROSS_WIN_INTO_LOSS", 0) + m.get("COST_DOMINATED", 0) >= max(
            2, n_loss // 3
        ):
            hs.append(
                {
                    "id": f"H-cost-{name}",
                    "hypothesis": f"{name}: cost drag (fees + slippage ~0.3-0.45R per trade on floored stops) is the dominant loss mechanism; gross expectancy is ~0.",
                    "evidence_now": f"{m.get('COST_TURNED_GROSS_WIN_INTO_LOSS', 0) + m.get('COST_DOMINATED', 0)} of {n_loss} losses cost-labelled; avg gross R {perf(rows)['avg_gross_r']}",
                    "falsification": "if avg gross R over the next 30 trades is <= 0, the strategy has no edge to protect and cost reduction is moot; if avg gross R >= 0.25R while avg net R <= 0, a wider minimum stop distance / fee-aware filter is the candidate",
                    "status": "testable forward",
                }
            )
        if rows and m.get("STOP_HIT_THEN_TARGET_PRINTED", 0) >= max(2, n_loss // 3):
            hs.append(
                {
                    "id": f"H-stop-{name}",
                    "hypothesis": f"{name}: stops sit inside the noise of the entry bar; price reaches TP1 after stopping out.",
                    "evidence_now": f"{m['STOP_HIT_THEN_TARGET_PRINTED']} of {n_loss} losses printed TP1 within 60 min after the stop",
                    "falsification": "replay with the structural (unfloored) stop OR a stop at 1.5x the current distance on the SAME trades: if net R does not improve by > cost of the wider risk, reject",
                    "status": "replay with assumptions (path-based)",
                }
            )
        if rows and m.get("DIRECTION_WRONG_IMMEDIATELY", 0) >= max(2, n_loss // 2):
            hs.append(
                {
                    "id": f"H-dir-{name}",
                    "hypothesis": f"{name}: entries are directionally wrong at once (MFE < 0.15R): the trigger fires at exhaustion / late in the move.",
                    "evidence_now": f"{m['DIRECTION_WRONG_IMMEDIATELY']} of {n_loss} losses never moved 0.15R in favour",
                    "falsification": "if the 15-min forward return after BLOCKED-by-capacity and near-miss decisions of the same side is also <= 0, the signal itself has no directional edge (not a timing problem); otherwise test a one-bar confirmation entry in replay",
                    "status": "directional study + replay",
                }
            )
    hs.append(
        {
            "id": "H-displace",
            "hypothesis": "LITE's extra positions displace strict entries (LITE is in a trade when the strict rules would enter), so LITE under-performs the strict twin by more than its own losers.",
            "evidence_now": "see cross.strict_entries_while_lite_in_position",
            "falsification": "if displaced strict entries have mean 60-min return <= 0, displacement costs nothing",
            "status": "exact from journals",
        }
    )
    return hs[:5]


def validation_plan() -> dict[str, Any]:
    return {
        "principles": [
            "one pre-registered change per generation (V10.1 / V11.1 / V11-LITE.1), its own freeze and window",
            "the running strategies are the controls and keep running unchanged",
            "no parameter search; a change is a stated hypothesis with a falsification test written before the window opens",
            "shadow first; Demo only after the shadow checkpoint; Demo on an isolated account per strategy",
        ],
        "V10.1": {
            "candidate_only_if": "H-cost-V10 or H-stop-V10 survives 30 more Demo trades",
            "change": "ONE of: fee-aware minimum stop distance, structural stop without floor, or TP1 at 0.6R/40 % (V11 geometry)",
            "window": "shadow 30 trades or 21 days, then Demo 30 trades",
            "pass": "net R > 0 and PF > 1.1 on the shadow window, Demo fills within 3 bps of shadow references",
        },
        "V11.1": {
            "candidate_only_if": "V11-WARM reaches >= 30 trades with net <= 0 AND a mechanism dominates",
            "change": "ONE of: entry at the next bar close with momentum confirmation, or stop at 1.5x structural buffer",
            "window": "replay on the consumed TRAIN / VALIDATION frames as discovery (already consumed, selection-biased), then a NEW forward window >= 30 trades as validation",
            "pass": "validation net R > 0, PF > 1.1, gross R > cost R",
        },
        "V11-LITE.1": {
            "candidate_only_if": "H1 not falsified at checkpoint 2 (30 trades or 21 days)",
            "change": "structure as a soft score term (+/-5 or +/-10) instead of a gate; template priority B before A when both trigger",
            "window": "shadow next to the running LITE and V11-WARM for 30 trades or 21 days",
            "pass": "net R > 0, PF > 1.1, and incremental cohort 90 % upper CI > 0 (the existing checkpoint rules)",
        },
    }


def decide(name: str, rows: list[dict[str, Any]], checks: dict[str, Any]) -> dict[str, Any]:
    p = perf(rows)
    n = p["n"]
    if checks.get("integrity_issue"):
        return {"decision": "INVESTIGATE SAFETY", "why": checks["integrity_issue"]}
    if n < 30:
        return {
            "decision": "INSUFFICIENT EVIDENCE -> CONTINUE UNCHANGED",
            "why": f"{n} closed trades; sign of the sum is not separable from noise (<30)",
            "mode": "DEMO" if name == "V10_DEMO" else "SHADOW",
        }
    return {
        "decision": "CONTINUE UNCHANGED (sample reached; evaluate hypotheses)",
        "why": f"{n} trades, net {p['net_usd']:.2f} USDT, PF {_v(p['pf'])}",
    }


# ----------------------------------------------------------------------------- main
def run(
    data_dir: Path, out_root: Path, cutoff_ms: int | None = None
) -> tuple[Path, dict[str, Any]]:
    cutoff = cutoff_ms if cutoff_ms is not None else int(time.time() * 1000) - MS_1M
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = out_root / run_id
    out.mkdir(parents=True, exist_ok=True)
    roots = {
        "V10": Root("V10", data_dir / "btc" / "forward_v10", cutoff),
        "V11_WARM": Root("V11_WARM", data_dir / "btc" / "forward_v11_warm", cutoff),
        "V11_LITE": Root("V11_LITE", data_dir / "btc" / "forward_v11_lite_warm", cutoff),
    }
    series: dict[str, list[dict[str, Any]]] = {}
    sub_audits: dict[str, Any] = {}
    # ---- V10: Demo and Shadow separately; the Demo fills audit as a sub-report
    v10 = roots["V10"]
    sigs10 = {
        r["data"]["signal_id"]: r["data"]
        for r in v10.journal("signals", "SIGNAL_EXECUTED")
        if isinstance(r.get("data"), dict)
    }
    outs10 = v10.outcomes()
    series["V10_DEMO"] = v10_demo_rows(v10, sigs10, outs10)
    series["V10_SHADOW"] = v10_shadow_rows(v10, sigs10, outs10)
    if series["V10_DEMO"]:
        try:
            md10, js10, rep10 = VA.run_audit(
                v10.path, out / "v10_demo_audit", n_last=10**6, cutoff_ms=cutoff
            )
            sub_audits["v10_demo_audit"] = {
                "markdown": str(md10),
                "json": str(js10),
                "n": len(rep10.get("trades", [])),
            }
        except (
            SystemExit,
            Exception,
        ) as e:  # the sub-audit is supplementary; its failure is reported, not hidden
            sub_audits["v10_demo_audit"] = {"error": f"{type(e).__name__}: {e}"[:300]}
    # ---- V11-WARM
    vw = roots["V11_WARM"]
    series["V11_WARM"] = v11_rows(vw, "V11_WARM", "SHADOW", vw.outcomes(), lite=False)
    # ---- V11-LITE + twin; the LITE forensic audit is the authoritative primary diagnostic
    vl = roots["V11_LITE"]
    outs_l = vl.outcomes()
    series["V11_LITE"] = v11_rows(vl, "V11_LITE", "SHADOW", outs_l, lite=True)
    series["V11_LITE_TWIN"] = v11_rows(vl, "V11_LITE", "TWIN", outs_l, lite=False)
    lite_rep: dict[str, Any] | None = None
    if vl.exists:
        try:
            mdl, jsl, lite_rep = LA.run_audit(vl.path, out / "v11_lite_audit", cutoff)
            sub_audits["v11_lite_audit"] = {
                "markdown": str(mdl),
                "json": str(jsl),
                "n": lite_rep.get("n_closed"),
            }
        except Exception as e:
            sub_audits["v11_lite_audit"] = {"error": f"{type(e).__name__}: {e}"[:300]}
    mech = {k: Counter(m for r in v for m in r.get("mechanisms", [])) for k, v in series.items()}
    checks: dict[str, dict[str, Any]] = {}
    for n, r in roots.items():
        c: dict[str, Any] = {
            "root": str(r.path),
            "exists": r.exists,
            "state_capture": r.state.get("__capture_error__", "ok"),
            "chain_links_ok": r.chains(),
            "observation_start_utc": _iso(r.state.get("observation_start_ms")),
            "counters": r.state.get("counters"),
            "open_position_at_cutoff": bool(
                r.state.get("position") or r.state.get("shadow_position")
            ),
        }
        bad = [k for k, v in c["chain_links_ok"].items() if not v]
        if bad or "__capture_error__" in r.state:
            c["integrity_issue"] = f"chain link break in {bad}" if bad else c["state_capture"]
        checks[n] = c
    # score recomputation + template bias checks for V11 series
    impl: dict[str, Any] = {
        "score_recompute_mismatches": {},
        "lite_non_B_with_B_trigger": (lite_rep or {})
        .get("pathologies", {})
        .get("non_B_selections_where_B_trigger_was_also_true"),
        "lite_repeated_setups": (lite_rep or {})
        .get("pathologies", {})
        .get("repeated_same_setup_pairs"),
        "lite_null_groups": (lite_rep or {}).get("pathologies", {}).get("null_groups_at_decision"),
        "v10_demo_vs_shadow_concurrent": "V10 opens a SHADOW position only while no Demo executor is attached (engine._decide): the two series never overlap in time; a non-empty shadow series after the Demo activation would be a bug",
        "v10_shadow_after_demo_activation": None,
    }
    for name in ("V11_WARM", "V11_LITE", "V11_LITE_TWIN"):
        mm = []
        for row in series[name]:
            if row.get("contrib") and row.get("score") is not None:
                rc = LA.score_from_contrib(row["contrib"])
                if rc is not None and abs(rc - row["score"]) > 1e-6:
                    mm.append(row["signal_id"])
        impl["score_recompute_mismatches"][name] = mm
    act = [r["data"] for r in v10.journal("signals", "DEMO_REGIME_STARTED")]
    if act and series["V10_SHADOW"]:
        a_ms = min(_ms(a.get("activated_at")) or 10**18 for a in act)
        impl["v10_shadow_after_demo_activation"] = [
            r["signal_id"] for r in series["V10_SHADOW"] if r["decision_ms"] > a_ms
        ]
    rep: dict[str, Any] = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "read_only": True,
        "cutoff_utc": _iso(cutoff),
        "cutoff_ms": cutoff,
        "run_id": run_id,
        "roots": checks,
        "sub_audits": sub_audits,
        "performance": {k: perf(v) for k, v in series.items()},
        "windows": {k: windows(v, cutoff) for k, v in series.items()},
        "by_family": {k: by(v, lambda r: r.get("family")) for k, v in series.items()},
        "by_side": {k: by(v, lambda r: r["side"]) for k, v in series.items()},
        "by_exit": {k: by(v, lambda r: r.get("exit_reason")) for k, v in series.items()},
        "by_hour": {k: by(v, lambda r: hour_bucket(r["hour_utc"])) for k, v in series.items()},
        "by_vol": {k: by(v, lambda r, v=v: vol_bucket(v, r)) for k, v in series.items()},
        "by_stop": {
            k: by(v, lambda r: "FLOORED" if r.get("stop_floored") else "structural")
            for k, v in series.items()
        },
        "by_strict_gate_lite": by(series["V11_LITE"], lambda r: r.get("strict_gate")),
        "mechanisms": {k: dict(v) for k, v in mech.items()},
        "mechanism_trade_ids": {
            k: {m: [r["signal_id"] for r in rows if m in r.get("mechanisms", [])] for m in mech[k]}
            for k, rows in series.items()
        },
        "blocked_missed": {
            "V10": blocked_missed(v10, "V10", outs10, lambda d: d.get("nearmiss_id")),
            "V11_WARM": blocked_missed(
                vw,
                "V11_WARM",
                vw.outcomes(),
                lambda d: f"V11N-{d.get('ts_ms')}-{d.get('family')}{str(d.get('side', 'X'))[0]}",
            ),
            "V11_LITE": blocked_missed(
                vl,
                "V11_LITE",
                outs_l,
                lambda d: f"V11LWN-{d.get('ts_ms')}-{str(d.get('side', 'X'))[0]}",
            ),
        },
        "cross": cross(roots, series, cutoff),
        "counterfactuals": counterfactuals(series, lite_rep),
        "implementation_checks": impl,
        "hypotheses": hypotheses(series, mech),
        "validation_plan": validation_plan(),
        "decisions": {
            k: decide(
                k,
                v,
                checks.get(
                    k.split("_")[0]
                    if k.startswith("V10")
                    else ("V11_LITE" if "LITE" in k else "V11_WARM"),
                    {},
                ),
            )
            for k, v in series.items()
        },
        "trades": series,
        "snapshots": {
            n: r.snap.verify() | {"files": r.snap.manifest()} for n, r in roots.items() if r.exists
        },
    }
    (out / "strategy_forensics.json").write_text(
        json.dumps(rep, indent=1, sort_keys=True, default=str)
    )
    write_markdown(out, rep)
    return out, rep


# ----------------------------------------------------------------------------- markdown deliverables
def _tbl(d: dict[str, dict[str, Any]], title: str) -> list[str]:
    o = [
        f"### {title}",
        "",
        "| bucket | n | W/L | win rate | net USDT | net R sum | avg net R | avg gross R | avg cost R | PF | max DD USDT | avg hold min | avg MFE R | avg MAE R |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for k, p in d.items():
        o.append(
            f"| {k} | {p['n']} | {p['wins']}/{p['losses']} | {_v(p['win_rate'])} | {_v(p['net_usd'])} | {_v(p['net_r_sum'])} | {_v(p['avg_net_r'])} | {_v(p['avg_gross_r'])} | {_v(p['avg_cost_r'])} | {_v(p['pf'])} | {_v(p['max_dd_usd'])} | {_v(p['avg_hold_min'], 0)} | {_v(p['avg_mfe_r'])} | {_v(p['avg_mae_r'])} |"
        )
    return [*o, ""]


def write_markdown(out: Path, rep: dict[str, Any]) -> None:
    P = rep["performance"]  # noqa: N806
    small = [k for k, p in P.items() if p["n"] and p["n"] < 30]
    ex = [
        f"# Strategy forensics — executive summary (read-only, run {rep['run_id']})",
        "",
        f"Cutoff {rep['cutoff_utc']} UTC. Every number below is from the journaled records at or before the cutoff; path statistics are evaluation-only. Demo and Shadow are separate series.",
        "",
        "## FACTS",
        "",
    ]
    for k, p in P.items():
        if p["n"]:
            ex.append(
                f"- **{k}**: {p['n']} closed ({p['first_utc']} .. {p['last_utc']}), {p['wins']}W/{p['losses']}L, net {_v(p['net_usd'])} USDT / {_v(p['net_r_sum'])} R, avg net {_v(p['avg_net_r'])} R = gross {_v(p['avg_gross_r'])} R - cost {_v(p['avg_cost_r'])} R, PF {_v(p['pf'])}, max DD {_v(p['max_dd_usd'])} USDT, mechanisms {rep['mechanisms'][k]}"
            )
        else:
            ex.append(f"- **{k}**: no closed trades at the cutoff")
    for n, c in rep["roots"].items():
        ex.append(
            f"- root {n}: exists {c['exists']}, state capture {c['state_capture']}, chain links {c['chain_links_ok']}, observation start {c['observation_start_utc']}, open position at cutoff {c['open_position_at_cutoff']}"
        )
    cr = rep["cross"]
    ex += [
        "",
        f"- overlap window (all three observing): {cr.get('overlap_window_utc')}; same-minute decisions: {cr.get('same_minute_decisions')}",
        f"- LITE vs strict twin at LITE's own decisions: {cr.get('lite_vs_strict_twin_at_decision')}; strict entries while LITE held a position: {(cr.get('strict_entries_while_lite_in_position') or {}).get('displaced')} of {(cr.get('strict_entries_while_lite_in_position') or {}).get('twin_executed')}",
        "",
    ]
    ex += ["## STRONG HYPOTHESES (each with a falsification test; see hypotheses.md)", ""] + [
        f"- {h['id']}: {h['hypothesis']} — evidence now: {h['evidence_now']}"
        for h in rep["hypotheses"]
    ]
    ex += [
        "",
        "## TOO EARLY TO CONCLUDE",
        "",
        f"- series with fewer than 30 trades: {small}; every per-bucket split (family, side, hour, volatility, strict gate) holds 1-15 trades and is descriptive only",
        "- no threshold is proposed; counterfactuals are typed (exact / replay with assumptions / directional / not yet testable) in counterfactuals.md",
        "",
        "## Decisions",
        "",
    ] + [f"- **{k}**: {d['decision']} — {d['why']}" for k, d in rep["decisions"].items()]
    (out / "01_executive_summary.md").write_text("\n".join(ex) + "\n")
    cmp_ = [
        "# Comparison table",
        "",
        "| metric | " + " | ".join(P) + " |",
        "|---|" + "---|" * len(P),
    ]
    for m in (
        "n",
        "wins",
        "losses",
        "win_rate",
        "net_usd",
        "net_r_sum",
        "avg_net_r",
        "avg_gross_r",
        "avg_cost_r",
        "pf",
        "max_dd_usd",
        "avg_hold_min",
        "fees_usd",
        "slip_usd",
        "funding_usd",
        "avg_mfe_r",
        "avg_mae_r",
        "first_utc",
        "last_utc",
    ):
        cmp_.append(f"| {m} | " + " | ".join(_v(P[k][m]) for k in P) + " |")
    cmp_ += ["", "## Windows (by decision time)", ""]
    for k, w in rep["windows"].items():
        cmp_.append(
            f"- {k}: "
            + "; ".join(
                f"{wn}: n {x['n']} net {_v(x['net_usd'])} USDT / {_v(x['net_r_sum'])} R"
                for wn, x in w.items()
            )
        )
    cmp_ += [
        "",
        "## Evidence quality",
        "",
        "- V10_DEMO: actual Bybit Demo executions (paper money) — fills, fees and slippage are real exchange values",
        "- V10_SHADOW, V11_WARM, V11_LITE, V11_LITE_TWIN: simulated fills at the frozen cost model (taker 5.5 bps, spread 0.5, slippage 1-3 bps)",
        "- anything with n < 30 is preliminary",
    ]
    (out / "02_comparison_table.md").write_text("\n".join(cmp_) + "\n")
    tr = ["# Per-trade forensic table", ""]
    for k, rows in rep["trades"].items():
        tr += [
            f"## {k} ({len(rows)})",
            "",
            "| # | decision UTC | side | fam | setup | score | gate | entry ref | fill | stop % | floored | TP1 | exit | hold | gross R | cost R | net R | net USDT | MFE R (min) | MAE R (min) | TP1 before stop | post-exit best R | pre-60m ret/vol bps | mechanisms |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for i, r in enumerate(rows, 1):
            tr.append(
                f"| {i} | {r['decision_utc']} | {r['side']} | {r.get('family')} | {r.get('setup_class')} | {_v(r.get('score'), 1)} | {r.get('strict_gate', NA)} | {_v(r.get('entry_ref'), 1)} | {_v(r.get('entry_fill'), 1)} | {_v(r.get('stop_pct'))} | {_v(r.get('stop_floored'))} | {_v(r.get('tp1'), 1)} | {r.get('exit_reason')} | {_v(r.get('hold_min'), 0)} | {_v(r.get('gross_r'))} | {_v(r.get('cost_r'))} | {_v(r.get('net_r'))} | {_v(r.get('net_usd'))} | {_v(r.get('mfe_r'))} ({_v(r.get('min_to_mfe'), 0)}) | {_v(r.get('mae_r'))} ({_v(r.get('min_to_mae'), 0)}) | {_v(r.get('tp1_before_stop'))} | {_v(r.get('post60_best_r'))} | {_v(r.get('pre60_ret_bps_side_adj'), 0)}/{_v(r.get('pre60_vol_bps'), 1)} | {','.join(r.get('mechanisms', []))} |"
            )
        tr.append("")
    (out / "03_per_trade_table.md").write_text("\n".join(tr) + "\n")
    lm = ["# Recurring loss mechanisms (multi-label; a loss is not an incorrect decision)", ""]
    for k, m in rep["mechanisms"].items():
        lm += (
            [f"## {k}", ""]
            + [
                f"- {name}: {cnt} — {rep['mechanism_trade_ids'][k][name]}"
                for name, cnt in sorted(m.items(), key=lambda x: -x[1])
            ]
            + [""]
        )
    (out / "04_loss_mechanisms.md").write_text("\n".join(lm) + "\n")
    bm = ["# Blocked and missed opportunities", ""]
    for k, b in rep["blocked_missed"].items():
        bm += [
            f"## {k}",
            "",
            f"- blocked decisions: {b['blocked_total']} by reason {b['blocked_by_reason']}",
            f"- valid setups blocked only by capacity (position open / caps / safety): {b['valid_setups_blocked_by_capacity']}",
            f"- near misses: {b['near_misses']}; by family {b['near_miss_by_family']}",
            f"- note: {b['note']}",
            "",
        ]
    bm += [
        "## LITE vs strict twin",
        "",
        json.dumps(rep["cross"].get("lite_vs_strict_twin_at_decision")),
        json.dumps(rep["cross"].get("strict_entries_while_lite_in_position"), default=str),
    ]
    (out / "05_blocked_missed.md").write_text("\n".join(bm) + "\n")
    ic = (
        ["# Implementation checks", ""]
        + [f"- {k}: {v}" for k, v in rep["implementation_checks"].items()]
        + ["", "## Root integrity", ""]
        + [f"- {n}: {c}" for n, c in rep["roots"].items()]
        + ["", f"## Sub-audits: {rep['sub_audits']}"]
    )
    (out / "06_implementation_checks.md").write_text("\n".join(ic) + "\n")
    ce = ["# Costs and exit quality", ""]
    for k, rows in rep["trades"].items():
        if rows:
            p = P[k]
            ce += [
                f"## {k}",
                "",
                f"- fees {_v(p['fees_usd'])} USDT, slippage {_v(p['slip_usd'])} USDT, funding {_v(p['funding_usd'])} USDT over {p['n']} trades; avg cost {_v(p['avg_cost_r'])} R vs avg gross {_v(p['avg_gross_r'])} R",
                f"- by exit reason: {json.dumps({kk: {'n': v['n'], 'net_r': round(v['net_r_sum'], 3)} for kk, v in rep['by_exit'][k].items()})}",
                f"- by stop geometry: {json.dumps({kk: {'n': v['n'], 'net_r': round(v['net_r_sum'], 3)} for kk, v in rep['by_stop'][k].items()})}",
                "",
            ]
            if k == "V10_DEMO":
                es = [r["entry_slip_bps"] for r in rows if r.get("entry_slip_bps") is not None]
                ss = [
                    r["stop_exit_slip_bps"] for r in rows if r.get("stop_exit_slip_bps") is not None
                ]
                ce += [
                    f"- Demo entry slippage vs decision reference: mean {_v(statistics.fmean(es) if es else None, 2)} bps (n {len(es)}); stop-leg slippage through the level: mean {_v(statistics.fmean(ss) if ss else None, 2)} bps (n {len(ss)}); fill latency ms: {[r.get('fill_latency_ms') for r in rows][-10:]}",
                    "",
                ]
    (out / "07_costs_exits.md").write_text("\n".join(ce) + "\n")
    hy = (
        ["# Improvement hypotheses with falsification tests", ""]
        + [
            f"## {h['id']}\n\n- hypothesis: {h['hypothesis']}\n- evidence now: {h['evidence_now']}\n- falsification: {h['falsification']}\n- status: {h['status']}\n"
            for h in rep["hypotheses"]
        ]
        + [
            "# Counterfactuals (typed)",
            "",
            json.dumps(rep["counterfactuals"], indent=1, default=str),
        ]
    )
    (out / "08_hypotheses_counterfactuals.md").write_text("\n".join(hy) + "\n")
    (out / "09_validation_plan.md").write_text(
        "# Validation plan\n\n" + json.dumps(rep["validation_plan"], indent=1) + "\n"
    )
    (out / "10_decisions.md").write_text(
        "# Decisions\n\n"
        + "\n".join(f"- **{k}**: {d['decision']} — {d['why']}" for k, d in rep["decisions"].items())
        + "\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", type=Path, default=Path(os.environ.get("BTC_DATA_DIR", "./data")))
    ap.add_argument("--out", type=Path, default=Path("reports/strategy_forensics"))
    ap.add_argument("--cutoff", type=str, default=None, help="ISO UTC cutoff (default now - 1 min)")
    a = ap.parse_args()
    cutoff = int(datetime.fromisoformat(a.cutoff).timestamp() * 1000) if a.cutoff else None
    out, _rep = run(a.data_dir, a.out, cutoff)
    sys.stdout.write((out / "01_executive_summary.md").read_text())
    sys.stdout.write(f"\nwritten: {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
