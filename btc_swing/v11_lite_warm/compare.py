"""READ-ONLY comparison V11-WARM (control) vs V11-LITE-WARM (challenger), the incremental V11-LITE
cohort (events the strict V11 rules would reject), the Fibonacci attribution buckets and the mechanical
evaluation of the pre-registered checkpoints. Pure functions of the journals; writes only under
reports/forward_v11_lite_warm/. Small samples are labelled PRELIMINARY; nothing is optimised."""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from btc_swing.forward_report.metrics import _f, _q, trade_metrics
from btc_swing.forward_report.spec import GenerationSpec, iso, read_chain, read_json
from btc_swing.v11_lite_warm.config import LiteCfg

NA = "N/A"
DAY_MS = 86_400_000
MS_1M = 60_000
SIDES = ("LONG", "SHORT")


def _mean(xs: list[float]) -> float | str:
    return statistics.fmean(xs) if xs else NA


def _ci_upper_90(xs: list[float]) -> float | str:
    """One-sided 90% upper confidence bound of the mean (normal approximation; n >= 2)."""
    if len(xs) < 2:
        return NA
    sd = statistics.stdev(xs)
    return statistics.fmean(xs) + 1.2816 * sd / math.sqrt(len(xs))


def _label(n: int, min_n: int) -> str:
    return "PRELIMINARY" if n < min_n else "ok"


def _journals(spec: GenerationSpec) -> dict[str, list[dict[str, Any]]]:
    return {k: read_chain(p) for k, p in spec.journals.items()}


def _data(recs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [r["data"] for r in recs if r.get("kind") == kind and isinstance(r.get("data"), dict)]


def _outcome_index(outs: list[dict[str, Any]]) -> dict[tuple[str, int], float]:
    idx: dict[tuple[str, int], float] = {}
    for o in outs:
        v = _f(o.get("return_bps"))
        if v is not None:
            idx[(str(o.get("id")), int(float(o.get("horizon_min") or 0)))] = v
    return idx


def _ms(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, int | float):
        return int(v)
    try:
        return int(datetime.fromisoformat(str(v)).timestamp() * 1000)
    except ValueError:
        return None


def _tmpl(d: dict[str, Any]) -> str:
    """LITE selected template (pre-cleanup journals called it `family`); the control's family."""
    return str(d.get("selected_template", d.get("family")))


def side_episodes(armed: list[dict[str, Any]]) -> dict[str, list[tuple[int, int, str]]]:
    """Unique SIDE-level arm episodes. LITE journals one ARMED record per episode start carrying
    `arm_episode_id`; V11 / V11-WARM journal ARMED per (family, side) on every refresh minute with
    `valid_until`, so a control episode is the union of overlapping [ts, valid_until] intervals of all
    families of one side. Returns side -> [(start_ms, end_ms, episode_id)]."""
    out: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    if any("arm_episode_id" in a for a in armed):
        for a in armed:
            t0 = _ms(a.get("ts_ms")) or 0
            out[str(a.get("side"))].append((t0, t0, str(a.get("arm_episode_id"))))
        return dict(out)
    by_side: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for a in armed:
        t = _ms(a.get("ts_ms"))
        if t is None:
            continue
        until = _ms(a.get("valid_until")) or t
        by_side[str(a.get("side"))].append((t, max(t, until)))
    for side, iv in by_side.items():
        for t, u in sorted(iv):
            cur = out[side]
            if cur and t <= cur[-1][1]:
                s0, e0, eid = cur[-1]
                cur[-1] = (s0, max(e0, u), eid)
            else:
                cur.append((t, u, f"{side[0]}-{t}"))
    return dict(out)


def _episode_of(rec: dict[str, Any], eps: dict[str, list[tuple[int, int, str]]]) -> str | None:
    if rec.get("arm_episode_id"):
        return str(rec["arm_episode_id"])
    t = _ms(rec.get("decision_ms") or rec.get("ts_ms"))
    if t is None:
        return None
    for s0, e0, eid in eps.get(str(rec.get("side")), []):
        if s0 <= t <= e0:
            return eid
    return None


def unique_counts(
    cands: list[dict[str, Any]],
    armed: list[dict[str, Any]],
    sigs: list[dict[str, Any]],
    nms: list[dict[str, Any]],
    trades: list[dict[str, Any]],
) -> dict[str, Any]:
    """Opportunity counts that do not depend on how many templates / families journal the same
    side-minute: everything is keyed by (side, minute) or by (side, arm episode)."""
    eps = side_episodes(armed)
    dec_sm = {(str(x.get("side")), _ms(x.get("decision_ms"))) for x in sigs}
    blk = [x for x in sigs if x.get("action") == "BLOCKED"]
    exe = [x for x in sigs if x.get("action") == "EXECUTED"]
    ep_exec = {(str(x.get("side")), _episode_of(x, eps)) for x in exe}
    ep_blk = {(str(x.get("side")), _episode_of(x, eps)) for x in blk}
    nm_sm = {(str(x.get("side")), _ms(x.get("ts_ms"))) for x in nms}
    return {
        "candidates": len(cands),
        "side_arm_episodes": sum(len(v) for v in eps.values()),
        "trigger_decisions_side_minutes": len(dec_sm),
        "arm_episodes_with_decision": len(
            {(str(x.get("side")), _episode_of(x, eps)) for x in sigs}
        ),
        "blocked_side_minutes": len({(str(x.get("side")), _ms(x.get("decision_ms"))) for x in blk}),
        "blocked_only_arm_episodes": len(ep_blk - ep_exec),
        "near_miss_side_minutes": len(nm_sm),
        "near_miss_arm_episodes": len({(str(x.get("side")), _episode_of(x, eps)) for x in nms}),
        "executed": len(exe),
        "shadow_trades": len(trades),
        "raw_armed_records": len(armed),
        "raw_near_miss_records": len(nms),
    }


def template_view(executed: list[dict[str, Any]], trades: list[dict[str, Any]]) -> dict[str, Any]:
    """selected_template (LITE: which frozen trigger / stop template fired first; NOT a setup) kept
    apart from the frozen V11 structure labels of the decision bar."""
    lab: list[dict[str, Any]] = [
        x["structure_labels"] for x in executed if isinstance(x.get("structure_labels"), dict)
    ]
    net: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        v = _f(t.get("net_r"))
        if v is not None:
            net[_tmpl(t)].append(v)
    return {
        "selected_template": dict(Counter(_tmpl(x) for x in executed)),
        "frozen_structure_label_A": sum(1 for x in lab if x.get("A")) if lab else NA,
        "frozen_structure_label_B": sum(1 for x in lab if x.get("B")) if lab else NA,
        "frozen_structure_label_C": sum(1 for x in lab if x.get("C")) if lab else NA,
        "no_frozen_structure_label": (
            sum(1 for x in lab if not any(bool(v) for v in x.values())) if lab else NA
        ),
        "net_r_mean_by_template": {k: _mean(v) for k, v in sorted(net.items())},
    }


def generation_summary(
    spec: GenerationSpec, equity: float | None, horizons: list[int]
) -> dict[str, Any]:
    j = _journals(spec)
    state = read_json(spec.state)
    freeze = read_json(spec.freeze)
    start_ms = state.get("observation_start_ms") or freeze.get("observation_start_ms")
    last_ms = state.get("last_bar_ms")
    days = (
        (float(last_ms) - float(start_ms)) / DAY_MS
        if (start_ms and last_ms and last_ms > start_ms)
        else 0.0
    )
    cands = _data(j.get("candidates", []), "CANDIDATE")
    armed = _data(j.get("episodes", []), "ARMED")
    sigs = [
        r["data"]
        for r in j.get("signals", [])
        if str(r.get("kind", "")).startswith("SIGNAL_") and isinstance(r.get("data"), dict)
    ]
    executed = [s for s in sigs if s.get("action") == "EXECUTED"]
    blocked = [s for s in sigs if s.get("action") == "BLOCKED"]
    trades = _data(j.get("shadow_trades", []), "SHADOW_CLOSED")
    outs = _data(j.get("outcomes", []), "OUTCOME")
    nms = _data(j.get("nearmiss", []), "NEAR_MISS")
    uniq = unique_counts(cands, armed, sigs, nms, trades)
    raw_counters = state.get("counters")
    counters: dict[str, Any] = raw_counters if isinstance(raw_counters, dict) else {}
    tm = trade_metrics(trades, equity)

    def per_day(n: int) -> float | str:
        return n / days if days > 0 else NA

    def sc_dist(items: list[dict[str, Any]]) -> dict[str, Any]:
        xs = [v for v in (_f(x.get("score")) for x in items) if v is not None]
        return {
            "n": len(xs),
            "mean": _mean(xs),
            "p50": _q(xs, 0.5) if xs else NA,
            "p90": _q(xs, 0.9) if xs else NA,
            "max": max(xs) if xs else NA,
        }

    side_adj: dict[str, dict[str, Any]] = {}
    by_kind: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for o in outs:
        v = _f(o.get("return_bps"))  # already side-adjusted by the runner (s * return)
        if v is not None:
            by_kind[str(o.get("kind"))][int(float(o.get("horizon_min") or 0))].append(v)
    for kind, hs in by_kind.items():
        side_adj[kind] = {
            f"{h}m": {"n": len(hs.get(h, [])), "mean_bps": _mean(hs.get(h, []))} for h in horizons
        }
    n_starts = int(uniq["side_arm_episodes"])
    gross_r = [v for v in (_f(t.get("gross_r")) for t in trades) if v is not None]
    net_r = [v for v in (_f(t.get("net_r")) for t in trades) if v is not None]
    risk_usdt = [v for v in (_f(t.get("risk_usdt")) for t in trades) if v is not None]
    risk_unit = statistics.fmean(risk_usdt) if risk_usdt else None
    return {
        "generation": spec.name,
        "label": spec.label,
        "root": str(spec.root),
        "observation_start": iso(start_ms) if start_ms else NA,
        "last_bar": iso(last_ms) if last_ms else NA,
        "observed_days": days,
        "freeze_present": bool(freeze),
        "strategy_hash": freeze.get("lite_strategy_hash") or freeze.get("v11_strategy_hash") or NA,
        "counts": {
            "candidates": len(cands),
            "armed_records": len(armed),
            "armed_starts": n_starts,
            "triggers_counter": counters.get("triggers", NA),
            "executed": len(executed),
            "blocked": len(blocked),
            "shadow_closed": len(trades),
            "outcomes": len(outs),
        },
        "unique": uniq,
        "per_day": {
            "candidates": per_day(len(cands)),
            "armed_starts": per_day(n_starts),
            "executed": per_day(len(executed)),
            "shadow_closed": per_day(len(trades)),
        },
        "conversion": {
            "candidates_to_executed": (len(executed) / len(cands)) if cands else NA,
            "armed_starts_to_executed": (len(executed) / n_starts) if n_starts else NA,
        },
        "sides": {
            "candidates": dict(Counter(str(c.get("side")) for c in cands)),
            "executed": dict(Counter(str(e.get("side")) for e in executed)),
            "shadow_closed": dict(
                Counter("LONG" if float(t.get("side") or 0) > 0 else "SHORT" for t in trades)
            ),
        },
        "templates": template_view(executed, trades),
        "scores": {"candidates": sc_dist(cands), "executed": sc_dist(executed)},
        "blockers": dict(Counter(str(b) for s in blocked for b in (s.get("blockers") or []))),
        "trades": {
            "n": len(trades),
            "gross_r_mean": _mean(gross_r),
            "net_r_mean": _mean(net_r),
            "net_r_total": sum(net_r) if net_r else NA,
            "net_r_ci90_upper": _ci_upper_90(net_r),
            "profit_factor": tm.get("profit_factor", NA),
            "win_rate": tm.get("win_rate", NA),
            "max_dd_usd": tm.get("max_dd_usd", NA),
            "max_dd_r": (
                float(tm["max_dd_usd"]) / risk_unit
                if (isinstance(tm.get("max_dd_usd"), float | int) and risk_unit)
                else NA
            ),
            "hold_min_median": tm.get("hold_min_median", NA),
            "exit_reasons": tm.get("exit_reasons", {}),
            "net_r_by_side": tm.get("net_r_by_side", NA),
        },
        "side_adjusted_outcomes_bps": side_adj,
    }


def incremental_cohort(
    spec_lite: GenerationSpec, horizons: list[int]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    j = _journals(spec_lite)
    sigs = [
        r["data"]
        for r in j.get("signals", [])
        if str(r.get("kind", "")).startswith("SIGNAL_") and isinstance(r.get("data"), dict)
    ]
    trades = {
        str(t.get("signal_id")): t for t in _data(j.get("shadow_trades", []), "SHADOW_CLOSED")
    }
    oidx = _outcome_index(_data(j.get("outcomes", []), "OUTCOME"))
    rows: list[dict[str, Any]] = []
    for s in sigs:
        ctl = s.get("control_twin") or {}
        if not s.get("incremental", not ctl.get("would_take", True)):
            continue
        ctx = s.get("context") or {}
        fib = ctx.get("fib") or {}
        rng = ctx.get("range") or {}
        sw = ctx.get("swings") or {}
        sid = str(s.get("signal_id"))
        tr = trades.get(sid)
        rows.append(
            {
                "signal_id": sid,
                "ts": s.get("ts"),
                "side": s.get("side"),
                "selected_template": _tmpl(s),
                "action": s.get("action"),
                "score": s.get("score"),
                **{
                    f"frozen_structure_label_{f}": (
                        (s.get("structure_labels") or {}).get(f)
                        if isinstance(s.get("structure_labels"), dict)
                        else (s.get("family_labels") or {}).get(f, {}).get(str(s.get("side")))
                    )
                    for f in ("A", "B", "C")
                },
                "control_rejection_reason": ctl.get("reason"),
                "control_failed_conditions": ctl.get("failed_conditions"),
                "micro_score": (s.get("contrib") or {}).get("microstructure"),
                "returns_bps": {f"{h}m": oidx.get((sid, h), NA) for h in horizons},
                "net_r": tr.get("net_r") if tr else NA,
                "gross_r": tr.get("gross_r") if tr else NA,
                "exit_reason": tr.get("exit_reason") if tr else NA,
                "fib_nearest_ratio": fib.get("nearest_ratio", NA),
                "fib_nearest_dist_bps": fib.get("nearest_dist_bps", NA),
                "fib_nearest_abs_dist_atr": fib.get("nearest_abs_dist_atr", NA),
                "fib_bucket_atr": fib.get("bucket_atr", NA),
                "fib_direction": fib.get("direction", NA),
                "vwap_dist_bps": (ctx.get("vwap") or {}).get("dist_bps", NA),
                "range_pos_60m": (rng.get("60m") or {}).get("position", NA),
                "range_pos_240m": (rng.get("240m") or {}).get("position", NA),
                "swing_hi5_dist_atr": (sw.get("swing_hi5") or {}).get("atr", NA),
                "swing_lo5_dist_atr": (sw.get("swing_lo5") or {}).get("atr", NA),
            }
        )
    net = [v for v in (_f(r["net_r"]) for r in rows) if v is not None]
    executed_rows = [r for r in rows if r["action"] == "EXECUTED"]
    summary = {
        "n_signals": len(rows),
        "n_executed": len(executed_rows),
        "n_blocked": len(rows) - len(executed_rows),
        "n_closed_trades": len(net),
        "net_r_mean": _mean(net),
        "net_r_ci90_upper": _ci_upper_90(net),
        "gross_r_mean": _mean([v for v in (_f(r["gross_r"]) for r in rows) if v is not None]),
        "returns_bps_mean": {  # EXECUTED decisions only (blocked rows repeat an open opportunity)
            f"{h}m": _mean(
                [
                    v
                    for v in (_f(r["returns_bps"].get(f"{h}m")) for r in executed_rows)
                    if v is not None
                ]
            )
            for h in horizons
        },
        "rejection_reasons": dict(
            Counter(str(r["control_rejection_reason"]).split(":")[0] for r in rows)
        ),
        "sides": dict(Counter(str(r["side"]) for r in rows)),
    }
    return rows, summary


def fib_attribution(
    spec_lite: GenerationSpec, lite: LiteCfg, horizons: list[int]
) -> dict[str, Any]:
    j = _journals(spec_lite)
    executed = [
        r["data"]
        for r in j.get("signals", [])
        if r.get("kind") == "SIGNAL_EXECUTED" and isinstance(r.get("data"), dict)
    ]
    cands = _data(j.get("candidates", []), "CANDIDATE")
    trades = {
        str(t.get("signal_id")): t for t in _data(j.get("shadow_trades", []), "SHADOW_CLOSED")
    }
    oidx = _outcome_index(_data(j.get("outcomes", []), "OUTCOME"))
    edges = list(lite.fib_bucket_edges_atr)
    order = (
        [f"<= {edges[0]:.2f}"]
        + [f"{a:.2f}-{b:.2f}" for a, b in pairwise(edges)]
        + [f"> {edges[-1]:.2f}", "N/A"]
    )

    def table(items: list[dict[str, Any]], id_key: str, id_prefix: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for b in order:
            sel = [
                x
                for x in items
                if ((x.get("context") or {}).get("fib") or {}).get("bucket_atr", "N/A") == b
            ]
            ids = [
                str(x.get(id_key))
                if id_key == "signal_id"
                else f"{id_prefix}{int(float(x.get('ts_ms') or 0))}-{str(x.get('side'))[0]}"
                for x in sel
            ]
            r30 = [v for v in (oidx.get((i, 30)) for i in ids) if v is not None]
            r60 = [v for v in (oidx.get((i, 60)) for i in ids) if v is not None]
            rr = [
                v for v in (_f(trades[i].get("net_r")) for i in ids if i in trades) if v is not None
            ]
            out[b] = {
                "n": len(sel),
                "ret_30m_bps": _mean(r30),
                "ret_60m_bps": _mean(r60),
                "closed_trade_net_r": _mean(rr),
                "n_closed": len(rr),
                "label": _label(len(sel), lite.preliminary_min_n),
            }
        return out

    return {
        "bucket_edges_atr": edges,
        "executed_signals": table(executed, "signal_id", ""),
        "candidates": table(cands, "ts_ms", "V11LWC-"),
        "note": "side-adjusted returns from the outcomes journal; PRELIMINARY below "
        f"{lite.preliminary_min_n} samples; no threshold is derived from this table",
    }


def checkpoints(
    lite: LiteCfg, lite_summary: dict[str, Any], inc: dict[str, Any]
) -> list[dict[str, Any]]:
    tr = lite_summary["trades"]
    days = float(lite_summary["observed_days"] or 0)
    n_c = int(lite_summary["counts"]["candidates"])
    n_t = int(tr["n"])
    total_net = _f(tr.get("net_r_total"))
    by_side = tr.get("net_r_by_side") if isinstance(tr.get("net_r_by_side"), dict) else {}

    def one_sided() -> bool | str:
        if total_net is None or total_net <= 0 or n_t == 0:
            return NA
        sides = lite_summary["sides"]["shadow_closed"]
        pos = {
            s: (_f(by_side.get(s)) or 0.0) * int(sides.get(s, 0))
            for s in SIDES
            if (_f(by_side.get(s)) or 0.0) > 0
        }
        return bool(pos) and max(pos.values()) / total_net > 0.80

    crit: dict[str, bool | str] = {
        "gross_r_per_trade_gt_0": (g > 0) if (g := _f(tr.get("gross_r_mean"))) is not None else NA,
        "incremental_cohort_not_clearly_negative": (
            (u > 0) if (u := _f(inc.get("net_r_ci90_upper"))) is not None else NA
        ),
        "net_r_per_trade_gt_0": (m > 0) if (m := _f(tr.get("net_r_mean"))) is not None else NA,
        "profit_factor_gt_1_1": (pf > 1.1)
        if (pf := _f(tr.get("profit_factor"))) is not None
        else NA,
        "max_dd_le_15r": (dd <= 15.0) if (dd := _f(tr.get("max_dd_r"))) is not None else NA,
        "not_one_sided": (not v) if isinstance(v := one_sided(), bool) else NA,
        "not_one_period": NA,  # evaluated from the daily table below
    }
    out = []
    for cp in lite.checkpoints:
        reach = cp.reached_when_any
        reached = any(
            (k == "fully_warm_days" and days >= v)
            or (k == "candidates" and n_c >= v)
            or (k == "closed_trades" and n_t >= v)
            for k, v in reach.items()
        )
        res = {k: crit.get(k, NA) for k in cp.require}
        passed: bool | str
        if not reached:
            passed = "NOT_REACHED"
        elif any(v == NA for v in res.values()):
            passed = "INSUFFICIENT_DATA"
        else:
            passed = all(bool(v) for v in res.values())
        out.append(
            {
                "name": cp.name,
                "reached_when_any": reach,
                "reached": reached,
                "progress": {
                    "fully_warm_days": days,
                    "candidates": n_c,
                    "closed_trades": n_t,
                },
                "require": res,
                "passed": passed,
            }
        )
    return out


def not_one_period(spec_lite: GenerationSpec) -> bool | str:
    """No single UTC day contributes more than 50% of a positive total net R."""
    j = _journals(spec_lite)
    trades = _data(j.get("shadow_trades", []), "SHADOW_CLOSED")
    per_day: dict[str, float] = defaultdict(float)
    total = 0.0
    for t in trades:
        v = _f(t.get("net_r"))
        if v is None:
            continue
        d = datetime.fromtimestamp(float(t.get("exit_ms") or 0) / 1000, tz=UTC).strftime("%Y-%m-%d")
        per_day[d] += v
        total += v
    if not trades or total <= 0:
        return NA
    return max(per_day.values()) / total <= 0.50


def compare(
    spec_ctl: GenerationSpec,
    spec_lite: GenerationSpec,
    lite: LiteCfg,
    equity: float | None,
) -> dict[str, Any]:
    horizons = list(lite.outcome_horizons_min)
    ctl = generation_summary(spec_ctl, equity, horizons)
    lw = generation_summary(spec_lite, equity, horizons)
    rows, inc = incremental_cohort(spec_lite, horizons)
    cps = checkpoints(lite, lw, inc)
    nop = not_one_period(spec_lite)
    for cp in cps:
        if "not_one_period" in cp["require"]:
            cp["require"]["not_one_period"] = nop
            if cp["reached"] and cp["passed"] != "NOT_REACHED":
                vals = list(cp["require"].values())
                cp["passed"] = (
                    "INSUFFICIENT_DATA"
                    if any(v == NA for v in vals)
                    else all(bool(v) for v in vals)
                )
    return {
        "generated_utc": datetime.now(UTC).isoformat(),
        "read_only": True,
        "control": ctl,
        "challenger": lw,
        "delta": {
            "candidates_per_day": _delta(lw["per_day"]["candidates"], ctl["per_day"]["candidates"]),
            "armed_starts_per_day": _delta(
                lw["per_day"]["armed_starts"], ctl["per_day"]["armed_starts"]
            ),
            "executed_per_day": _delta(lw["per_day"]["executed"], ctl["per_day"]["executed"]),
            "shadow_closed_per_day": _delta(
                lw["per_day"]["shadow_closed"], ctl["per_day"]["shadow_closed"]
            ),
            "net_r_mean": _delta(lw["trades"]["net_r_mean"], ctl["trades"]["net_r_mean"]),
            "gross_r_mean": _delta(lw["trades"]["gross_r_mean"], ctl["trades"]["gross_r_mean"]),
        },
        "incremental_cohort": {"summary": inc, "rows": rows},
        "fib_attribution": fib_attribution(spec_lite, lite, horizons),
        "checkpoints": cps,
        "preliminary_min_n": lite.preliminary_min_n,
        "labels": {
            "challenger_trades": _label(int(lw["trades"]["n"]), lite.preliminary_min_n),
            "control_trades": _label(int(ctl["trades"]["n"]), lite.preliminary_min_n),
            "incremental_cohort": _label(int(inc["n_closed_trades"]), lite.preliminary_min_n),
        },
    }


def _delta(a: Any, b: Any) -> float | str:
    fa, fb = _f(a), _f(b)
    return (fa - fb) if (fa is not None and fb is not None) else NA


# ----------------------------------------------------------------------------- markdown
def _v(x: Any, nd: int = 3) -> str:
    if x is None:
        return NA
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, str):
        return x
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        if x != x:
            return NA
        if x in (math.inf, -math.inf):
            return "inf"
        return f"{x:.{nd}f}"
    return str(x)


def render_compare(r: dict[str, Any]) -> str:
    c, w = r["control"], r["challenger"]
    out = ["# V11-WARM (control) vs V11-LITE-WARM (challenger) — read-only forward comparison\n"]
    out.append(
        f"Generated {r['generated_utc']}. Control root `{c['root']}` (observation from {c['observation_start']}, "
        f"{_v(c['observed_days'], 2)} days); challenger root `{w['root']}` (observation from "
        f"{w['observation_start']}, {_v(w['observed_days'], 2)} days). Both SHADOW ONLY. Samples below "
        f"{r['preliminary_min_n']} are PRELIMINARY (challenger trades: {r['labels']['challenger_trades']}, "
        f"control trades: {r['labels']['control_trades']}, incremental cohort: {r['labels']['incremental_cohort']}). "
        "No threshold is derived from this report.\n"
    )
    out.append("## 1. Funnel and rates\n")
    out += ["| metric | V11-WARM | V11-LITE-WARM | Δ |", "|---|---|---|---|"]
    out += [
        "**Unique opportunities** (keyed by side-minute or side-level arm episode; independent of how many "
        "templates / families journal the same minute)\n",
        "| metric | V11-WARM | V11-LITE-WARM |",
        "|---|---|---|",
    ]
    for k in c["unique"]:
        out.append(f"| {k} | {_v(c['unique'][k])} | {_v(w['unique'][k])} |")
    out += [
        "",
        "**Journal records** (raw)\n",
        "| metric | V11-WARM | V11-LITE-WARM | Δ |",
        "|---|---|---|---|",
    ]
    for k in (
        "candidates",
        "armed_records",
        "armed_starts",
        "triggers_counter",
        "executed",
        "blocked",
        "shadow_closed",
        "outcomes",
    ):
        out.append(f"| {k} | {_v(c['counts'][k])} | {_v(w['counts'][k])} | |")
    for k in ("candidates", "armed_starts", "executed", "shadow_closed"):
        out.append(
            f"| {k} / day | {_v(c['per_day'][k], 2)} | {_v(w['per_day'][k], 2)} | {_v(r['delta'].get(f'{k}_per_day'), 2)} |"
        )
    for k in ("candidates_to_executed", "armed_starts_to_executed"):
        out.append(f"| conversion {k} | {_v(c['conversion'][k])} | {_v(w['conversion'][k])} | |")
    out.append("")
    out.append("## 2. Sides, templates, frozen structure labels, scores\n")
    out += ["| metric | V11-WARM | V11-LITE-WARM |", "|---|---|---|"]
    for k in ("candidates", "executed", "shadow_closed"):
        out.append(f"| sides {k} | {c['sides'][k]} | {w['sides'][k]} |")
    out.append(
        f"| executed by frozen V11 family (control: setup present) / selected_template (LITE: first trigger "
        f"template, NOT a setup) | {c['templates']['selected_template']} | {w['templates']['selected_template']} |"
    )
    for k in (
        "frozen_structure_label_A",
        "frozen_structure_label_B",
        "frozen_structure_label_C",
        "no_frozen_structure_label",
    ):
        out.append(f"| executed with {k} | {_v(c['templates'][k])} | {_v(w['templates'][k])} |")
    for k in ("candidates", "executed"):
        sc, sw = c["scores"][k], w["scores"][k]
        out.append(
            f"| score {k} (n / mean / p50 / p90 / max) | {sc['n']} / {_v(sc['mean'], 1)} / {_v(sc['p50'], 1)} / {_v(sc['p90'], 1)} / {_v(sc['max'], 1)} "
            f"| {sw['n']} / {_v(sw['mean'], 1)} / {_v(sw['p50'], 1)} / {_v(sw['p90'], 1)} / {_v(sw['max'], 1)} |"
        )
    out.append(f"| blockers | {c['blockers']} | {w['blockers']} |")
    out.append("")
    out.append("## 3. Shadow trades (PnL / R)\n")
    out += ["| metric | V11-WARM | V11-LITE-WARM | Δ |", "|---|---|---|---|"]
    for k in (
        "n",
        "gross_r_mean",
        "net_r_mean",
        "net_r_total",
        "net_r_ci90_upper",
        "profit_factor",
        "win_rate",
        "max_dd_usd",
        "max_dd_r",
        "hold_min_median",
    ):
        d = r["delta"].get(k, "")
        out.append(
            f"| {k} | {_v(c['trades'][k])} | {_v(w['trades'][k])} | {_v(d) if d != '' else ''} |"
        )
    out.append(
        f"| exit reasons | {c['trades']['exit_reasons']} | {w['trades']['exit_reasons']} | |"
    )
    out.append(
        f"| net R by side | {c['trades']['net_r_by_side']} | {w['trades']['net_r_by_side']} | |"
    )
    out.append(
        f"| net R by family (control) / selected_template (LITE) | {c['templates']['net_r_mean_by_template']} | {w['templates']['net_r_mean_by_template']} | |"
    )
    out.append("")
    out.append("## 4. Side-adjusted outcomes (mean bps by kind and horizon)\n")
    kinds = sorted(set(c["side_adjusted_outcomes_bps"]) | set(w["side_adjusted_outcomes_bps"]))
    if kinds:
        hs = [
            f"{h}m"
            for h in sorted(
                {
                    int(h[:-1])
                    for g in (c, w)
                    for k in g["side_adjusted_outcomes_bps"].values()
                    for h in k
                }
            )
        ]
        out.append("| generation | kind | " + " | ".join(hs) + " |")
        out.append("|---|---|" + "---|" * len(hs))
        for name, g in (("V11-WARM", c), ("V11-LITE-WARM", w)):
            for k in kinds:
                d = g["side_adjusted_outcomes_bps"].get(k, {})
                out.append(
                    f"| {name} | {k} | "
                    + " | ".join(
                        f"{_v(d.get(h, {}).get('mean_bps'), 1)} (n={d.get(h, {}).get('n', 0)})"
                        for h in hs
                    )
                    + " |"
                )
        out.append("")
    else:
        out.append("no outcome records yet\n")
    out.append("## 5. INCREMENTAL V11-LITE COHORT (the main experiment)\n")
    inc = r["incremental_cohort"]["summary"]
    out.append(
        "Signals V11-LITE-WARM took that the strict V11 rules (in-process control twin on identical inputs) would "
        "have rejected. "
        + " · ".join(
            f"{k} {_v(v)}"
            for k, v in inc.items()
            if k not in ("returns_bps_mean", "rejection_reasons", "sides")
        )
        + f" · mean side-adjusted returns of EXECUTED incremental decisions {inc['returns_bps_mean']} · "
        f"reasons {inc['rejection_reasons']} · sides {inc['sides']}. selected_template is the first frozen "
        "trigger template that fired; it is NOT evidence that a frozen V11 family setup was present (see the "
        "frozen_structure_label columns).\n"
    )
    rows = r["incremental_cohort"]["rows"]
    if rows:
        hs = list(rows[0]["returns_bps"].keys())
        out.append(
            "| ts | side | selected_template | score | frozen_structure_label A/B/C | control rejection | micro | "
            + " | ".join(hs)
            + " | net R | exit | fib ratio | fib dist bps | fib |ATR| | bucket | vwap bps | rng60 | rng240 | swHi5 ATR | swLo5 ATR |"
        )
        out.append("|---|" * (18 + len(hs)))
        for x in rows[-200:]:
            out.append(
                f"| {str(x['ts'])[:16]} | {x['side']} | {x['selected_template']} | {_v(x['score'], 1)} | "
                f"{'/'.join('1' if x[f'frozen_structure_label_{f}'] else '0' for f in ('A', 'B', 'C'))} | {x['control_rejection_reason']} | {_v(x['micro_score'], 2)} | "
                + " | ".join(_v(x["returns_bps"][h], 1) for h in hs)
                + f" | {_v(x['net_r'], 2)} | {x['exit_reason']} | {x['fib_nearest_ratio']} | {_v(x['fib_nearest_dist_bps'], 1)} | "
                f"{_v(x['fib_nearest_abs_dist_atr'], 2)} | {x['fib_bucket_atr']} | {_v(x['vwap_dist_bps'], 1)} | {_v(x['range_pos_60m'], 2)} | "
                f"{_v(x['range_pos_240m'], 2)} | {_v(x['swing_hi5_dist_atr'], 2)} | {_v(x['swing_lo5_dist_atr'], 2)} |"
            )
        out.append("")
    else:
        out.append("no incremental signal yet\n")
    out.append("## 6. Fibonacci attribution (read-only; |distance to nearest level| / ATR5)\n")
    fa = r["fib_attribution"]
    for name in ("executed_signals", "candidates"):
        out.append(f"**{name}**\n")
        out += [
            "| bucket | n | 30m bps | 60m bps | closed-trade net R (n) | label |",
            "|---|---|---|---|---|---|",
        ]
        for b, d in fa[name].items():
            out.append(
                f"| {b} | {d['n']} | {_v(d['ret_30m_bps'], 1)} | {_v(d['ret_60m_bps'], 1)} | {_v(d['closed_trade_net_r'])} ({d['n_closed']}) | {d['label']} |"
            )
        out.append("")
    out.append(f"{fa['note']}\n")
    out.append("## 7. Pre-registered checkpoints (mechanical)\n")
    out += [
        "| checkpoint | reached when any | progress | reached | requirements | result |",
        "|---|---|---|---|---|---|",
    ]
    for cp in r["checkpoints"]:
        out.append(
            f"| {cp['name']} | {cp['reached_when_any']} | {{days {_v(cp['progress']['fully_warm_days'], 1)}, candidates {cp['progress']['candidates']}, trades {cp['progress']['closed_trades']}}} | "
            f"{_v(cp['reached'])} | {cp['require'] or 'inspection only'} | {_v(cp['passed'])} |"
        )
    out.append("")
    out.append(
        "Interpretation rules (pre-registered): checkpoint_2 requires gross R / trade > 0 AND the incremental cohort's "
        "one-sided 90% upper confidence bound of net R > 0; checkpoint_3 requires net R / trade > 0, PF > 1.1, max "
        "drawdown <= 15 R, no side > 80% of a positive total and no single day > 50% of a positive total. A failed "
        "checkpoint stops the experiment; nothing is tuned forward.\n"
    )
    return "\n".join(out) + "\n"


def write_compare(
    config: Path, control_config: Path, verification: bool
) -> tuple[Path, Path, dict[str, Any]]:
    """Reads both generations through their GenerationSpecs; writes compare_<ts>.{json,md} and
    latest_compare.* under reports/forward_v11_lite_warm/ only."""
    from btc_swing.forward_report.cli import _clean
    from btc_swing.forward_report.generations import spec_v11lw, spec_v11w
    from btc_swing.v11_lite_warm.config import load_v11lw_config
    from btc_swing.v11_lite_warm.paths import REPORTS_DIR

    lc, _v11 = load_v11lw_config(config)
    spec_ctl, _eq_ctl = spec_v11w(control_config, verification)
    spec_lite, equity = spec_v11lw(config, verification)
    rd = REPORTS_DIR
    if rd.parts[:1] != ("reports",) or len(rd.parts) < 2:
        raise RuntimeError(f"reports must be written under reports/<generation>/, not {rd}")
    rep = _clean(compare(spec_ctl, spec_lite, lc.lite, equity))
    rd.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    js = rd / f"compare_{ts}.json"
    md = rd / f"compare_{ts}.md"
    js.write_text(json.dumps(rep, indent=1, sort_keys=True, default=str))
    md.write_text(render_compare(rep))
    (rd / "latest_compare.json").write_text(js.read_text())
    (rd / "latest_compare.md").write_text(md.read_text())
    return js, md, rep
