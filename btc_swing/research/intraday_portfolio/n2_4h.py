"""N2 FOUR-HOUR PERSISTENCE TEST — bounded re-horizon of the FROZEN N2 parent events.

The events are READ from the frozen run's `n2_parents.parquet` (never re-detected): same parent ids,
sides, decision timestamps, the same causal entry convention (OPEN of the first Bybit 1m bar at
decision + 1 minute) and no new filter. Only the holding horizon changes: 240 minutes primary,
120 / 480 minutes as prespecified diagnostics (never selected if the primary fails). Funding is applied
from the Binance 8h series as a LABELLED PROXY wherever a settlement is spanned (Bybit funding history is
not on disk); NaN funding is never silently zero. Cadence is recounted under 4h occupancy with the
harness's one-position replay restricted to N2 itself."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from btc_swing.research.intraday_portfolio import ORDER_CAPABLE, RESEARCH_ONLY
from btc_swing.research.intraday_portfolio import metrics as mx
from btc_swing.research.intraday_portfolio import ncensus as nc
from btc_swing.research.intraday_portfolio.contract import MS_1M, EventTimes
from btc_swing.research.intraday_portfolio.data import load_bybit_bars, load_funding_proxy
from btc_swing.research.intraday_portfolio.episodes import Exit, ParentEpisode, r_multiple
from btc_swing.research.intraday_portfolio.ledger import (
    BASE_COST_BPS,
    COST_SCENARIOS_BPS,
    STRESS_COST_BPS,
    benchmark_net_bps,
    funding_bps,
)
from btc_swing.research.intraday_portfolio.nengines import EMBARGO_MIN, horizon_outcome
from btc_swing.research.intraday_portfolio.primitives import Bars
from btc_swing.research.intraday_portfolio.run import _f, _package_hashes, _write_table

ENGINE = "N2S_4H"
HORIZON_PRIMARY_MIN = 240
HORIZONS_DIAG_MIN = (120, 480)
DEFAULT_SOURCE = Path("reports/intraday_portfolio_research/20261019T180134Z/n2_parents.parquet")
FUNDING_SOURCE_LABEL = "BINANCE_8H_PROXY (Bybit funding history not on disk)"


@dataclass(frozen=True)
class FrozenEvent:
    parent_id: str
    side: str
    decision_ms: int
    entry_ms: int
    atr_bps: float
    atr_setup: float
    er: float
    regime: str
    eligibility_state: str
    flow15: float
    threshold: float
    prior_15m_ret_bps: float


def load_frozen_events(path: Path) -> tuple[list[FrozenEvent], dict[str, Any]]:
    """The frozen N2 parents, verbatim. Fails closed on duplicate ids or a broken entry convention."""
    df = pl.read_parquet(path)
    need = {"parent_id", "side", "t_decision_time", "t_benchmark_entry_time", "lvl_atr_bps"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"frozen N2 parquet lacks {sorted(missing)}")
    evs: list[FrozenEvent] = []
    for r in df.sort(["t_decision_time", "parent_id"]).iter_rows(named=True):
        dec = int(r["t_decision_time"])
        ent = int(r["t_benchmark_entry_time"])
        if ent != dec + EMBARGO_MIN * MS_1M:
            raise ValueError(f"{r['parent_id']}: entry {ent} != decision + embargo")
        evs.append(
            FrozenEvent(
                str(r["parent_id"]),
                str(r["side"]),
                dec,
                ent,
                float(r["lvl_atr_bps"]),
                float(r.get("lvl_atr_setup", math.nan)),
                float(r.get("er", math.nan)),
                str(r.get("regime", "")),
                str(r.get("eligibility_state", "")),
                float(r.get("lvl_flow15_signed_quote", math.nan)),
                float(r.get("lvl_threshold_abs_flow", math.nan)),
                float(r.get("note_prior_15m_ret_bps") or math.nan),
            )
        )
    ids = [e.parent_id for e in evs]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate parent ids in the frozen source")
    meta = {
        "source": str(path),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "rows": df.height,
        "columns": df.columns,
    }
    return evs, meta


def replay_event(
    ev: FrozenEvent,
    b1: Bars,
    fund_t: Any,
    fund_rate: Any,
    cost_bps: float = BASE_COST_BPS,
) -> ParentEpisode:
    s = 1 if ev.side == "LONG" else -1
    p = ParentEpisode(
        parent_id=ev.parent_id,
        engine=ENGINE,
        labels=[ENGINE, ev.side, ev.regime, "FROZEN_N2S_EVENT"],
        side=ev.side,
        first_setup_time_ms=ev.decision_ms,
        receipt_time_ms=None,
        times=EventTimes(ev.decision_ms, None, None, ev.decision_ms, ev.entry_ms, None),
        frozen_levels={
            "atr_setup": ev.atr_setup,
            "atr_bps": ev.atr_bps,
            "flow15_signed_quote": ev.flow15,
            "threshold_abs_flow": ev.threshold,
        },
        regime=ev.regime,
        er=ev.er,
    )
    p.notes["horizon_min"] = HORIZON_PRIMARY_MIN
    p.notes["risk_unit"] = "ATR20_bps"
    p.notes["funding_source"] = FUNDING_SOURCE_LABEL
    p.notes["utc_hour"] = datetime.fromtimestamp(ev.decision_ms / 1000, tz=UTC).hour
    p.notes["prior_15m_ret_bps"] = ev.prior_15m_ret_bps
    p.stop_bps = ev.atr_bps
    if ev.eligibility_state == "REJECTED":
        p.mark_rejected("FROZEN_EVENT_WAS_REJECTED")
        return p
    if not math.isfinite(ev.atr_bps) or ev.atr_bps <= 0:
        p.mark_rejected("ATR_UNAVAILABLE")
        return p
    for h in (*HORIZONS_DIAG_MIN, HORIZON_PRIMARY_MIN):
        oc = horizon_outcome(b1, ev.entry_ms, h, s)
        if oc is None:
            p.notes[f"gross_{h}m_bps"] = math.nan
            p.notes[f"funding_{h}m_bps"] = math.nan
            continue
        g = s * (oc.exit_px / oc.entry_px - 1) * 1e4
        f = funding_bps(s, oc.entry_ms, oc.exit_ms, fund_t, fund_rate)
        p.notes[f"gross_{h}m_bps"] = g
        p.notes[f"funding_{h}m_bps"] = f
        if h == HORIZON_PRIMARY_MIN:
            p.eligibility_state = "ELIGIBLE"
            p.entry_benchmark = oc.entry_px
            p.exit = Exit("HORIZON", oc.exit_ms, oc.exit_px)
            p.gross_bps, p.cost_bps = g, cost_bps
            p.notes["funding_bps_proxy"] = f  # read by metrics.net_at for every cost scenario
            p.net_bps = benchmark_net_bps(g, cost_bps, f)
            p.net_r = r_multiple(p.net_bps, ev.atr_bps)
            p.mfe_bps, p.mae_bps, p.hold_min = oc.mfe_bps, oc.mae_bps, float(h)
            p.eligibility_state = "CLOSED"
    if p.eligibility_state != "CLOSED":
        p.mark_rejected("ENTRY_OR_HORIZON_PATH_UNAVAILABLE_240M")
    return p


def _hrow(h: int, ps: list[ParentEpisode], subset: str) -> dict[str, Any]:
    vals, days, fund = [], [], []
    for p in ps:
        g = p.notes.get(f"gross_{h}m_bps")
        if isinstance(g, float) and math.isfinite(g):
            vals.append(g)
            days.append(nc._day(p.times.benchmark_entry_time or p.first_setup_time_ms))
            f = p.notes.get(f"funding_{h}m_bps")
            if isinstance(f, float) and math.isfinite(f):
                fund.append(f)
    return {
        "engine": ENGINE,
        "horizon_min": h,
        "subset": subset,
        "role": "PRIMARY" if h == HORIZON_PRIMARY_MIN else "DIAGNOSTIC",
        "n": len(vals),
        "gross_bps_mean": statistics.fmean(vals) if vals else None,
        "gross_bps_median": statistics.median(vals) if vals else None,
        "gross_bps_lb95_day_cluster": mx.cluster_lower_bound(vals, days) if vals else None,
        "win_rate_gross": sum(1 for v in vals if v > 0) / len(vals) if vals else None,
        "funding_bps_mean_proxy": statistics.fmean(fund) if fund else None,
        "funding_data_available_share": len(fund) / len(vals) if vals else None,
        "net_bps_mean_at_16": (
            statistics.fmean([v - BASE_COST_BPS - f for v, f in zip(vals, fund, strict=False)])
            if fund and len(fund) == len(vals)
            else None
        ),
    }


def decision(gate: dict[str, Any]) -> dict[str, Any]:
    s16 = gate["summary_16"]
    checks = {
        "mean_gross_ge_25bps": (s16.get("gross_bps_mean") or -1) >= mx.GATES["mean_gross_bps_min"],
        "mean_net_ge_9bps_at_16": (s16.get("net_bps_mean") or -1)
        >= mx.GATES["mean_net_bps_min_at_16"],
        "day_cluster_lower_bound_gt_0": (s16.get("net_bps_lower_bound_95_day_cluster") or -1) > 0,
        "chronological_stability_ge_3_of_4": gate["positive_blocks"] >= mx.BLOCKS_POSITIVE_MIN,
    }
    ok = all(checks.values())
    return {
        "verdict": "PASS TO FULL POLICY RESEARCH" if ok else "REJECT N2 AS DIRECTIONAL ALPHA",
        "checks": checks,
        "reason": (
            "4h primary passes every predeclared gate"
            if ok
            else "4h primary fails: " + ", ".join(k for k, v in checks.items() if not v)
        ),
        "diagnostics_rule": "2h / 8h are diagnostics only and are never selected when 4h fails",
    }


def run_n2_4h(
    data_dir: Path, out_root: Path, source: Path = DEFAULT_SOURCE, run_id: str | None = None
) -> dict[str, Any]:
    assert RESEARCH_ONLY and not ORDER_CAPABLE
    run_id = run_id or datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out = out_root / run_id
    out.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    events, src_meta = load_frozen_events(source)
    prereg = {
        "run_id": run_id,
        "status": "PREREGISTERED_BEFORE_ANY_OUTCOME_WAS_COMPUTED",
        "experiment": "N2 FOUR-HOUR PERSISTENCE TEST (bounded re-horizon of the frozen N2 spot-flow events)",
        "frozen_source": src_meta,
        "events": len(events),
        "event_reuse": "same parent ids, sides, decision timestamps; no re-detection; no new filter",
        "entry_convention": f"OPEN of the first Bybit 1m bar at decision + {EMBARGO_MIN} min (unchanged)",
        "primary_horizon_min": HORIZON_PRIMARY_MIN,
        "diagnostic_horizons_min": list(HORIZONS_DIAG_MIN),
        "funding": FUNDING_SOURCE_LABEL + "; sign-correct per side; NaN when the series is absent",
        "costs_bps": {
            "scenarios": list(COST_SCENARIOS_BPS),
            "base": BASE_COST_BPS,
            "stress": STRESS_COST_BPS,
        },
        "risk_unit": "ATR20 bps at setup (frozen value)",
        "occupancy": "one-position replay restricted to N2 events, fixed 240-min hold",
        "decision_rule": {
            "pass_requires": [
                "mean gross >= 25 bps",
                "mean net >= 9 bps at 16 bps",
                "day-cluster 95% lower bound > 0",
                ">= 3 of 4 chronological blocks positive at 16 bps",
            ],
            "else": "REJECT N2 as directional alpha; 2h / 8h never selected",
        },
        "package_sha256": _package_hashes(),
    }
    prereg_sha = hashlib.sha256(
        json.dumps(prereg, sort_keys=True, default=str).encode()
    ).hexdigest()
    man_path = out / "experiment_manifest_n2_4h.json"
    man_path.write_text(
        json.dumps(
            {"preregistration": prereg, "preregistration_sha256": prereg_sha},
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n"
    )
    files.append(man_path.name)

    # ---- outcomes
    b1, _p1, days1 = load_bybit_bars(data_dir, "1m")
    fund_t, fund_rate, prov_f = load_funding_proxy(data_dir)
    parents = [replay_event(e, b1, fund_t, fund_rate, BASE_COST_BPS) for e in events]
    trades = [p for p in parents if p.traded]
    first_day, last_day = (days1[0], days1[-1]) if days1 else (None, None)
    files += _write_table([p.as_row() for p in parents], out / "n2_4h_parents")

    # ---- cadence under 4h occupancy (N2 only)
    occ = nc.overlap_replay({ENGINE: trades}, (ENGINE,))
    avail_ids = {r.parent_id for r in occ if r.available}
    available = [p for p in trades if p.parent_id in avail_ids]
    n_days = len(nc._all_days(first_day, last_day)) if first_day and last_day else 0
    cadence = {
        "frozen_independent_episodes": len(events),
        "eligible_4h_paths": len(trades),
        "available_after_4h_occupancy": len(available),
        "occupied_by_open_n2_position": len(trades) - len(available),
        "same_direction_occupied": sum(1 for r in occ if not r.available and r.same_direction),
        "opposite_direction_occupied": sum(
            1 for r in occ if not r.available and r.same_direction is False
        ),
        "calendar_days": n_days,
        "eligible_per_day": len(trades) / n_days if n_days else None,
        "available_per_day": len(available) / n_days if n_days else None,
        "zero_available_days": n_days - len({nc._day(p.first_setup_time_ms) for p in available})
        if n_days
        else None,
    }
    files += _write_table([r.__dict__ for r in occ], out / "n2_4h_occupancy", parquet=False)

    # ---- markouts, costs, blocks, gates
    mk = [_hrow(h, trades, "ALL") for h in (120, 240, 480)]
    for side in ("LONG", "SHORT"):
        mk += [_hrow(h, [p for p in trades if p.side == side], side) for h in (120, 240, 480)]
    mk += [_hrow(240, available, "AVAILABLE_AFTER_OCCUPANCY")]
    files += _write_table(mk, out / "n2_4h_markouts", parquet=False)
    files += _write_table(
        nc.cost_rows(ENGINE, trades) + nc.cost_rows(ENGINE + "_AVAILABLE", available),
        out / "n2_4h_cost_screen",
        parquet=False,
    )
    files += _write_table(nc.block_rows(ENGINE, trades), out / "n2_4h_blocks", parquet=False)
    gate_all = mx.gates(trades, first_day, last_day, n_parents=len(events))
    gate_av = mx.gates(available, first_day, last_day, n_parents=len(events))
    dec = decision(gate_all)
    results = {
        "cadence": cadence,
        "markouts": mk,
        "gates_all_eligible": gate_all,
        "gates_available_after_occupancy": gate_av,
        "funding_provenance": prov_f.__dict__,
        "decision": dec,
    }
    manifest = {
        "schema": "intraday_portfolio_experiment_manifest_n2_4h-1",
        "run_id": run_id,
        "generated_at_utc": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "research_only": RESEARCH_ONLY,
        "order_capable": ORDER_CAPABLE,
        "preregistration": prereg,
        "preregistration_sha256": prereg_sha,
        "data": {"bybit_1m_days": len(days1), "first_day": first_day, "last_day": last_day},
        "results": results,
        "files": [*sorted(set(files)), "SUMMARY.md"],
    }
    man_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")
    (out / "SUMMARY.md").write_text(_summary(run_id, manifest))
    return {"run_id": run_id, "out_dir": out, "decision": dec, "results": results}


def _summary(run_id: str, man: dict[str, Any]) -> str:
    r = man["results"]
    d = r["decision"]
    c = r["cadence"]
    g = r["gates_all_eligible"]
    ga = r["gates_available_after_occupancy"]
    o = [f"# SUMMARY — N2 four-hour persistence test `{run_id}`", ""]
    o.append(f"**{d['verdict']}** — {d['reason']}. {d['diagnostics_rule']}.")
    o.append("")
    o.append(
        f"Frozen source `{man['preregistration']['frozen_source']['source']}` (sha256 `{man['preregistration']['frozen_source']['source_sha256'][:16]}…`, {man['preregistration']['events']} events); preregistration sha256 `{man['preregistration_sha256'][:16]}…`; funding = {man['preregistration']['funding']}."
    )
    o.append("")
    o.append("## Gross markouts from the unchanged post-embargo entry (side-adjusted bps)")
    o.append("")
    o.append(
        "| horizon | role | subset | n | gross mean | median | LB95 day-cluster | win | funding mean (proxy) | funding data available | net @16 |"
    )
    o.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for row in r["markouts"]:
        o.append(
            f"| {row['horizon_min']} m | {row['role']} | {row['subset']} | {row['n']} | {_f(row['gross_bps_mean'])} | {_f(row['gross_bps_median'])} | {_f(row['gross_bps_lb95_day_cluster'])} | {_f(row['win_rate_gross'], 3)} | {_f(row['funding_bps_mean_proxy'])} | {_f(row['funding_data_available_share'], 3)} | {_f(row['net_bps_mean_at_16'])} |"
        )
    o.append("")
    o.append("## 4h primary economics (all eligible events / available after 4h occupancy)")
    o.append("")
    o.append(
        "| set | n | gross | net@14 | net@16 | net@24 | LB95@16 | PF@16 | PF@24 | net R | net USDT | win | blocks+ | max block share | w/o top-5 |"
    )
    o.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for name, gg in (("all eligible", g), ("available", ga)):
        s14, s16, s24 = gg["scenarios"]["14.0"], gg["summary_16"], gg["summary_24"]
        o.append(
            f"| {name} | {s16.get('n', 0)} | {_f(s16.get('gross_bps_mean'))} | {_f(s14.get('net_bps_mean'))} | {_f(s16.get('net_bps_mean'))} | {_f(s24.get('net_bps_mean'))} | {_f(s16.get('net_bps_lower_bound_95_day_cluster'))} | {_f(s16.get('profit_factor'))} | {_f(s24.get('profit_factor'))} | {_f(s16.get('net_r_sum'))} | {_f(s16.get('net_usdt'))} | {_f(s16.get('win_rate'), 3)} | {gg['positive_blocks']}/4 | {_f(gg['max_block_share_of_positive_pnl'], 3)} | {_f(gg['fragility_mean_net_without_top5_bps'])} |"
        )
    o.append("")
    o.append("## Decision checks (4h primary, all eligible events)")
    o.append("")
    for k, v in d["checks"].items():
        o.append(f"- {k}: {'PASS' if v else 'FAIL'}")
    o.append("")
    o.append("## Cadence after 4h occupancy (N2 only)")
    o.append("")
    o.append(
        f"- frozen independent episodes {c['frozen_independent_episodes']}; eligible 4h paths {c['eligible_4h_paths']} ({_f(c['eligible_per_day'], 3)}/day); available after occupancy {c['available_after_4h_occupancy']} ({_f(c['available_per_day'], 3)}/day); occupied {c['occupied_by_open_n2_position']} (same direction {c['same_direction_occupied']}, opposite {c['opposite_direction_occupied']}); zero-available days {c['zero_available_days']} of {c['calendar_days']}."
    )
    o.append("")
    o.append(
        "Funding: Binance 8h rates applied as a labelled proxy (Bybit funding history is not on disk); a NaN funding never becomes zero in the per-trade ledger."
    )
    o.append("")
    return "\n".join(o)
