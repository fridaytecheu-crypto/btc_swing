"""Full-history metrics computed from journals only. Every value is derived from records that exist;
anything a generation does not journal is returned as the string "N/A" (never inferred)."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from btc_swing.forward_report.spec import (
    GenerationSpec,
    iso,
    read_chain,
    read_json,
    read_jsonl,
)

NA = "N/A"
DAY_MS = 86_400_000
FAMILIES = ("A", "B", "C")
SIDES = ("LONG", "SHORT")


def _side(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        u = v.upper()
        return u if u in SIDES else None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return "LONG" if f > 0 else ("SHORT" if f < 0 else None)


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _q(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    k = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return s[k]


def _dist(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "mean": statistics.fmean(xs),
        "p10": _q(xs, 0.10),
        "p50": _q(xs, 0.50),
        "p90": _q(xs, 0.90),
        "min": min(xs),
        "max": max(xs),
    }


def _by_fs(items: list[dict[str, Any]]) -> dict[str, int]:
    out = {f"{f}-{s}": 0 for f in FAMILIES for s in SIDES}
    out.update({f: 0 for f in FAMILIES})
    out.update({s: 0 for s in SIDES})
    for it in items:
        fam, side = (
            it.get("family"),
            _side(it.get("side") if it.get("side") is not None else it.get("side_sign")),
        )
        if fam in FAMILIES:
            out[fam] += 1
        if side:
            out[side] += 1
        if fam in FAMILIES and side:
            out[f"{fam}-{side}"] += 1
    return out


def trade_metrics(trades: list[dict[str, Any]], equity: float | None) -> dict[str, Any]:
    if not trades:
        return {"n": 0}
    net_usd = [v for v in (_f(t.get("net_usd")) for t in trades) if v is not None]
    net_r = [v for v in (_f(t.get("net_r")) for t in trades) if v is not None]
    gross_r = [v for v in (_f(t.get("gross_r")) for t in trades) if v is not None]
    hold = [v for v in (_f(t.get("hold_min")) for t in trades) if v is not None]
    wins = [x for x in net_usd if x > 0]
    losses = [x for x in net_usd if x <= 0]
    curve = 0.0
    peak = 0.0
    dd = 0.0
    for x in net_usd:
        curve += x
        peak = max(peak, curve)
        dd = max(dd, peak - curve)
    pf: float | str
    if losses and sum(losses) < 0:
        pf = sum(wins) / -sum(losses)
    elif wins:
        pf = math.inf
    else:
        pf = NA
    return {
        "n": len(trades),
        "win_rate": (len(wins) / len(net_usd)) if net_usd else NA,
        "net_usd_total": sum(net_usd) if net_usd else NA,
        "net_usd_mean": statistics.fmean(net_usd) if net_usd else NA,
        "net_r_mean": statistics.fmean(net_r) if net_r else NA,
        "net_r_total": sum(net_r) if net_r else NA,
        "gross_r_mean": statistics.fmean(gross_r) if gross_r else NA,
        "avg_winner_usd": statistics.fmean(wins) if wins else NA,
        "avg_loser_usd": statistics.fmean(losses) if losses else NA,
        "profit_factor": pf,
        "max_dd_usd": dd if net_usd else NA,
        "max_dd_pct_equity": (100.0 * dd / equity) if (net_usd and equity) else NA,
        "hold_min_median": _q(hold, 0.5) if hold else NA,
        "hold_min_p90": _q(hold, 0.9) if hold else NA,
        "exit_reasons": dict(Counter(str(t.get("exit_reason")) for t in trades)),
        "by_family_side": _by_fs(trades),
        "net_r_by_family": {
            f: (
                statistics.fmean(
                    [
                        v
                        for v in (_f(t.get("net_r")) for t in trades if t.get("family") == f)
                        if v is not None
                    ]
                )
                if any(t.get("family") == f and _f(t.get("net_r")) is not None for t in trades)
                else NA
            )
            for f in FAMILIES
        },
        "net_r_by_side": {
            s: (
                statistics.fmean(
                    [
                        v
                        for v in (
                            _f(t.get("net_r"))
                            for t in trades
                            if _side(
                                t.get("side") if t.get("side") is not None else t.get("side_sign")
                            )
                            == s
                        )
                        if v is not None
                    ]
                )
                if any(
                    _side(t.get("side") if t.get("side") is not None else t.get("side_sign")) == s
                    and _f(t.get("net_r")) is not None
                    for t in trades
                )
                else NA
            )
            for s in SIDES
        },
    }


def _records(spec: GenerationSpec, name: str) -> list[dict[str, Any]]:
    return read_chain(spec.journals.get(name))


def _day_of(ms: Any) -> str | None:
    v = _f(ms)
    return None if v is None else datetime.fromtimestamp(v / 1000, tz=UTC).strftime("%Y-%m-%d")


def full_history(spec: GenerationSpec, equity: float | None = None) -> dict[str, Any]:
    """Everything the journals say, since observation_start. Pure function of the files."""
    state = read_json(spec.state)
    freeze = read_json(spec.freeze)
    eps = _records(spec, "episodes")
    sigs = [r for r in _records(spec, "signals") if str(r.get("kind", "")).startswith("SIGNAL_")]
    other_sig_kinds = Counter(
        str(r.get("kind"))
        for r in _records(spec, "signals")
        if not str(r.get("kind", "")).startswith("SIGNAL_")
    )
    cands = _records(spec, "candidates") if "candidates" in spec.journals else None
    nms = _records(spec, "nearmiss")
    sh_all = _records(spec, "shadow_trades")
    sh = [r["data"] for r in sh_all if r.get("kind") == "SHADOW_CLOSED"]
    outs = [r["data"] for r in _records(spec, "outcomes") if isinstance(r.get("data"), dict)]
    malformed = sum(
        1 for n in spec.journals for r in _records(spec, n) if r.get("kind") == "__MALFORMED__"
    )

    # ---- episodes: V10 journals EPISODE_ARMED/TRIGGERED/INVALIDATED/EXPIRED; V11 journals ARMED (+ RUNNER_*)
    kinds = Counter(str(r.get("kind")) for r in eps)
    armed = [r["data"] for r in eps if r.get("kind") in ("EPISODE_ARMED", "ARMED")]
    triggered = [r["data"] for r in eps if r.get("kind") == "EPISODE_TRIGGERED"]
    invalidated = [r["data"] for r in eps if r.get("kind") == "EPISODE_INVALIDATED"]
    expired = [r["data"] for r in eps if r.get("kind") == "EPISODE_EXPIRED"]
    executed = [r["data"] for r in sigs if r.get("kind") == "SIGNAL_EXECUTED"]
    blocked = [r["data"] for r in sigs if r.get("kind") == "SIGNAL_BLOCKED"]
    raw_counters = state.get("counters")
    counters: dict[str, Any] = raw_counters if isinstance(raw_counters, dict) else {}

    # triggers: V10 journals them as episodes; V11 counts them in state only
    triggers_journaled: int | str = (
        len(triggered) if triggered or "EPISODE_TRIGGERED" in kinds else NA
    )
    triggers_counter = counters.get("triggers", counters.get("triggered"))

    # ---- blockers / failed conditions
    blockers: Counter[str] = Counter()
    for b in blocked:
        for x in b.get("blockers") or ([b["blocked_reason"]] if b.get("blocked_reason") else []):
            blockers[str(x)] += 1
    failed_conditions: dict[str, int] | str = NA
    if invalidated or expired:
        fc: Counter[str] = Counter()
        for e in invalidated + expired:
            d = e.get("detail")
            if isinstance(d, str) and d:
                fc[d] += 1
            cond = e.get("conditions")
            if isinstance(cond, dict):
                for k, v in cond.items():
                    if v is False:
                        fc[f"cond:{k}"] += 1
        failed_conditions = dict(fc)

    # ---- score distributions (V11-family journals carry `score`; V10 has no score)
    def scores(items: list[dict[str, Any]] | None) -> dict[str, Any] | str:
        if items is None:
            return NA
        xs = [v for v in (_f(i.get("score")) for i in items) if v is not None]
        return _dist(xs) if xs or items else {"n": 0}

    cand_data = [r["data"] for r in cands] if cands is not None else None
    score_block: dict[str, Any] = {
        "candidates": scores(cand_data),
        "armed": scores(armed) if any("score" in a for a in armed) else NA,
        "executed": scores(executed) if any("score" in e for e in executed) else NA,
        "near_misses": scores([r["data"] for r in nms])
        if any("score" in r.get("data", {}) for r in nms)
        else NA,
    }
    if cand_data is not None:
        score_block["candidates_by_side"] = {
            s: scores([c for c in cand_data if _side(c.get("side")) == s]) for s in SIDES
        }

    # ---- outcomes
    out_block: dict[str, Any] = {"n": len(outs)}
    if outs:
        grp: dict[tuple[str, str], list[float]] = defaultdict(list)
        for o in outs:
            v = _f(o.get("return_bps"))
            if v is not None:
                grp[(str(o.get("kind")), str(o.get("horizon_min")))].append(v)
        out_block["mean_return_bps_by_kind_horizon"] = {
            f"{k}@{h}m": {"n": len(v), "mean_bps": statistics.fmean(v)}
            for (k, h), v in sorted(grp.items())
        }

    # ---- daily table
    daily: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for a in armed:
        d = _day_of(a.get("ts_ms") or a.get("event_ts_ms") or a.get("armed_ms"))
        if d is None and isinstance(a.get("ts"), str):
            d = a["ts"][:10]
        if d is None and isinstance(a.get("armed_ts"), str):
            d = a["armed_ts"][:10]
        if d:
            daily[d]["armed"] += 1
    for e in executed:
        d = _day_of(e.get("decision_ms") or e.get("ts_ms"))
        if d is None and isinstance(e.get("at"), str):
            d = e["at"][:10]
        if d:
            daily[d]["executed"] += 1
    for t in sh:
        d = _day_of(t.get("exit_ms"))
        if d:
            daily[d]["shadow_closed"] += 1
            daily[d]["shadow_net_usd_x100"] += round(100 * (_f(t.get("net_usd")) or 0.0))
    if cand_data is not None:
        for c in cand_data:
            d = _day_of(c.get("ts_ms"))
            if d:
                daily[d]["candidates"] += 1

    # ---- demo (V10 only)
    demo: dict[str, Any] | str = NA
    if spec.has_demo:
        dj = read_chain(spec.demo_journal)
        dt = read_jsonl(spec.demo_trades)
        ds = read_json(spec.demo_state)
        activation = [r for r in dj if str(r.get("kind", "")).endswith("ACTIVATED")]
        demo = {
            "journal_records": len(dj),
            "journal_kinds": dict(Counter(str(r.get("kind")) for r in dj)),
            "activation_events": [
                {
                    "kind": r.get("kind"),
                    "ts": r.get("ts"),
                    "host": (r.get("data") or {}).get("host"),
                }
                for r in activation
            ],
            "trades": trade_metrics(
                [
                    {
                        **t,
                        "net_usd": t.get("net_pnl", t.get("net_usd")),
                        "net_r": t.get("net_r"),
                        "hold_min": t.get("hold_min"),
                    }
                    for t in dt
                    if not t.get("__malformed__")
                ],
                equity,
            )
            if dt
            else {"n": 0},
            "state_keys": sorted(ds.keys()) if ds else [],
        }

    start_ms = state.get("observation_start_ms") or freeze.get("observation_start_ms")
    last_ms = state.get("last_bar_ms") or state.get("last_tick_ms")
    dur_days = (
        ((float(last_ms) - float(start_ms)) / DAY_MS)
        if (start_ms and last_ms and last_ms > start_ms)
        else None
    )

    def per_day(n: int) -> float | str:
        return (n / dur_days) if (dur_days and dur_days > 0) else NA

    trades = trade_metrics(sh, equity)
    funnel = {
        "candidates": len(cand_data) if cand_data is not None else NA,
        "armed": len(armed),
        "triggers_journaled": triggers_journaled,
        "triggers_counter": triggers_counter if triggers_counter is not None else NA,
        "invalidated": len(invalidated) if invalidated or "EPISODE_INVALIDATED" in kinds else NA,
        "expired": len(expired) if expired or "EPISODE_EXPIRED" in kinds else NA,
        "executed": len(executed),
        "blocked": len(blocked),
        "near_misses": len(nms),
        "shadow_open_records": sum(1 for r in sh_all if r.get("kind") == "SHADOW_OPEN"),
        "shadow_closed": len(sh),
        "outcomes": len(outs),
    }
    return {
        "generation": spec.name,
        "label": spec.label,
        "generated_utc": datetime.now(UTC).isoformat(),
        "root": str(spec.root),
        "verification_root": spec.verification,
        "execution_modes": list(spec.execution_modes),
        "read_only": True,
        "freeze": {
            "present": bool(freeze),
            "strategy_hash": freeze.get("strategy_hash") or freeze.get("v11_strategy_hash") or NA,
            "frozen_at": freeze.get("frozen_at", NA),
            "observation_start": freeze.get("observation_start", NA),
        },
        "config_strategy_hash": spec.strategy_hash or NA,
        "observation_start": iso(start_ms) if start_ms else NA,
        "last_bar_or_tick": iso(last_ms) if last_ms else NA,
        "duration_days": dur_days if dur_days is not None else NA,
        "state_counters": counters or NA,
        "funnel": funnel,
        "rates_per_day": {
            "candidates": per_day(len(cand_data)) if cand_data is not None else NA,
            "armed": per_day(len(armed)),
            "executed": per_day(len(executed)),
            "shadow_closed": per_day(len(sh)),
        },
        "by_family_side": {
            "armed": _by_fs(armed),
            "triggered": _by_fs(triggered) if triggers_journaled != NA else NA,
            "executed": _by_fs(executed),
            "blocked": _by_fs(blocked),
            "near_misses": _by_fs([r["data"] for r in nms]),
            "shadow_closed": _by_fs(sh),
        },
        "blockers": dict(blockers) if blocked else {},
        "failed_conditions": failed_conditions,
        "scores": score_block,
        "shadow_trades": trades,
        "outcomes": out_block,
        "daily": {d: dict(v) for d, v in sorted(daily.items())},
        "episode_kinds": dict(kinds),
        "other_signal_journal_kinds": dict(other_sig_kinds),
        "demo": demo,
        "journal_files": {n: str(p) for n, p in spec.journals.items()},
        "malformed_lines": malformed,
        "notes": spec.notes,
    }
