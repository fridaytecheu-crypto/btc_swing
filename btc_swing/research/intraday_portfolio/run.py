"""Research run orchestrator: Phase 0 inventory -> E1 / E2 benchmark replay -> portfolio -> E4 event
study -> gates -> artefacts under reports/intraday_portfolio_research/<RUN_ID>/.

ORDER-DISABLED by construction: this package has no exchange client and no order type; outputs are
files only. Deterministic for identical inputs (sorted iteration, no randomness, no wall-clock in any
table; only the manifest records `generated_at_utc`)."""

from __future__ import annotations

import hashlib
import json
import math
import platform
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from btc_swing.research.intraday_portfolio import ORDER_CAPABLE, RESEARCH_ONLY
from btc_swing.research.intraday_portfolio import e1 as e1mod
from btc_swing.research.intraday_portfolio import e2 as e2mod
from btc_swing.research.intraday_portfolio import e4 as e4mod
from btc_swing.research.intraday_portfolio import metrics as mx
from btc_swing.research.intraday_portfolio import replay as rp
from btc_swing.research.intraday_portfolio.contract import MS_1M
from btc_swing.research.intraday_portfolio.data import load_bybit_bars, load_funding_proxy
from btc_swing.research.intraday_portfolio.episodes import ParentEpisode
from btc_swing.research.intraday_portfolio.inventory import (
    build_inventory,
    raw_roots,
    write_inventory,
)
from btc_swing.research.intraday_portfolio.ledger import (
    BASE_COST_BPS,
    COST_SCENARIOS_BPS,
    STRESS_COST_BPS,
    TAKER_FEE_BPS,
    CostBreakdown,
)
from btc_swing.research.intraday_portfolio.portfolio import PortfolioResult, simulate
from btc_swing.research.intraday_portfolio.primitives import (
    ATR_PERIOD,
    ER_BARS,
    ER_RANGE_MAX,
    Bars,
    efficiency_ratio,
    hour_boxes,
    wilder_atr,
)

MARKOUT_HORIZONS_MIN = (1, 5, 15, 30, 60, 120)
INTERPRETATIONS = {
    "episode_release_after_rejection": (
        "E1 cooldown (position closed, >= 60 min, two 5m closes back inside the original box) and the E2 "
        "new-excursion rule (VWAP touch, two closes inside, >= 30 min) govern parents that became a "
        "position. A setup rejected or cancelled before entry ends its episode at that time: further "
        "signals on the SAME frozen hour box are repeats of that parent, a later box can open a new "
        "candidate. Without this a single rejected breakout would block its side until price returned "
        "inside that box, possibly never."
    ),
    "benchmark_entry": "open of the first Bybit 1m bar after the decision close (no BBO in the archive)",
    "exit_priority_same_bar": "stop before target before 5m-close invalidation before time (conservative)",
    "er_regime_at": "the ER of the 5m bar completing the pattern (E1: 2nd breakout close; E2: sweep bar)",
}
ENGINE_ORDER = ("E1", "E2")
VERDICTS = ("REJECT", "INCONCLUSIVE", "CONTINUE RESEARCH", "ELIGIBLE FOR RUNNER ENGINEERING")


@dataclass
class RunResult:
    run_id: str
    out_dir: Path
    verdicts: dict[str, dict[str, str]]
    files: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _package_hashes() -> dict[str, str]:
    here = Path(__file__).parent
    return {p.name: _sha256_file(p) for p in sorted(here.glob("*.py"))}


def _write_table(rows: list[dict[str, Any]], base: Path, parquet: bool = True) -> list[str]:
    """CSV (+ parquet) from row dicts; an empty table still produces a header-less CSV file."""
    written: list[str] = []
    if not rows:
        base.with_suffix(".csv").write_text("")
        return [base.with_suffix(".csv").name]
    keys = sorted({k for r in rows for k in r})
    norm = [{k: r.get(k) for k in keys} for r in rows]
    df = pl.from_dicts(norm, infer_schema_length=None)
    df.write_csv(base.with_suffix(".csv"))
    written.append(base.with_suffix(".csv").name)
    if parquet:
        df.write_parquet(base.with_suffix(".parquet"))
        written.append(base.with_suffix(".parquet").name)
    return written


def _f(x: float | None, nd: int = 2) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "n/a"
    return f"{x:.{nd}f}"


def benchmark_markouts(parents: list[ParentEpisode], b1: Bars) -> list[dict[str, Any]]:
    """Gross markout (bps, signed by side) at fixed horizons after the BENCHMARK entry using 1m closes.
    No receipt time exists in the archive, so these are labelled benchmark (not observable-receipt)."""
    out: list[dict[str, Any]] = []
    for p in parents:
        t0 = p.times.benchmark_entry_time
        if t0 is None or p.entry_benchmark is None:
            continue
        s = 1 if p.side == "LONG" else -1
        row: dict[str, Any] = {
            "parent_id": p.parent_id,
            "engine": p.engine,
            "side": p.side,
            "eligibility_state": p.eligibility_state,
            "entry_benchmark_ms": t0,
            "ledger": "BENCHMARK",
            "receipt_time": "UNAVAILABLE",
        }
        for h in MARKOUT_HORIZONS_MIN:
            tc = t0 + h * MS_1M
            j = int(np.searchsorted(b1.close_ms, tc))
            if j < len(b1) and int(b1.close_ms[j]) == tc:
                row[f"mk_bench_{h}m_bps"] = (
                    s * (float(b1.close[j]) - p.entry_benchmark) / p.entry_benchmark * 1e4
                )
            else:
                row[f"mk_bench_{h}m_bps"] = None
        out.append(row)
    return out


def _cost_rows(engine: str, trades: list[ParentEpisode]) -> list[dict[str, Any]]:
    rows = []
    for c in COST_SCENARIOS_BPS:
        s = mx.summarize(trades, c)
        cb = CostBreakdown.taker_taker(c)
        rows.append(
            {
                "engine": engine,
                "ledger": "BENCHMARK",
                "execution_model": "taker/taker",
                "cost_scenario_bps": c,
                "commission_bps": cb.commission_bps,
                "spread_bps_doc": cb.spread_bps,
                "shortfall_bps_doc": cb.shortfall_bps,
                "latency_bps_doc": cb.latency_bps,
                "n": s.get("n"),
                "gross_bps_mean": s.get("gross_bps_mean"),
                "net_bps_mean": s.get("net_bps_mean"),
                "net_bps_lower_bound_95_day_cluster": s.get("net_bps_lower_bound_95_day_cluster"),
                "net_r_sum": s.get("net_r_sum"),
                "net_usdt": s.get("net_usdt"),
                "profit_factor": s.get("profit_factor"),
                "win_rate": s.get("win_rate"),
                "max_dd_usdt": s.get("max_dd_usdt"),
            }
        )
    return rows


def _frequency_rows(
    engine: str, fn: dict[str, Any], pf: PortfolioResult | None = None
) -> dict[str, Any]:
    row: dict[str, Any] = {"engine": engine}
    row.update({k: v for k, v in fn.items() if k != "rejection_reasons"})
    row["daily_frequency_p10_p50_p90"] = "|".join(
        str(x) for x in fn.get("daily_frequency_p10_p50_p90", [])
    )
    if pf is not None:
        n_el = sum(1 for r in pf.rows if r.status != "NOT_ELIGIBLE")
        row["portfolio_executed"] = len(pf.trades)
        row["portfolio_occupancy_loss"] = pf.occupancy_loss()
        row["portfolio_abstain_opposite"] = sum(
            1 for r in pf.rows if r.status == "ABSTAIN_OPPOSITE"
        )
        row["overlap_rate"] = (pf.occupancy_loss() / n_el) if n_el else None
        days = fn.get("calendar_days") or 0
        row["executed_per_day"] = len(pf.trades) / days if days else None
    return row


def _regime_rows(engine: str, trades: list[ParentEpisode]) -> list[dict[str, Any]]:
    rows = []
    for dim, groups in mx.breakdowns(trades, BASE_COST_BPS).items():
        for bucket, s in groups.items():
            rows.append(
                {
                    "engine": engine,
                    "dimension": dim,
                    "bucket": bucket,
                    "n": s.get("n"),
                    "gross_bps_mean": s.get("gross_bps_mean"),
                    "net_bps_mean": s.get("net_bps_mean"),
                    "net_r_sum": s.get("net_r_sum"),
                    "net_usdt": s.get("net_usdt"),
                    "profit_factor": s.get("profit_factor"),
                    "win_rate": s.get("win_rate"),
                }
            )
    return rows


def _portfolio_summary(pf: PortfolioResult) -> dict[str, Any]:
    tr = pf.trades
    nets = [r.net_bps for r in tr if r.net_bps is not None]
    rs = [r.net_r for r in tr if r.net_r is not None]
    pos = sum(x for x in rs if x > 0)
    neg = -sum(x for x in rs if x < 0)
    return {
        "name": pf.name,
        "rows": len(pf.rows),
        "executed": len(tr),
        "occupancy_loss": pf.occupancy_loss(),
        "abstain_opposite": sum(1 for r in pf.rows if r.status == "ABSTAIN_OPPOSITE"),
        "net_bps_mean": float(np.mean(nets)) if nets else None,
        "net_r_sum": float(sum(rs)) if rs else None,
        "profit_factor": (pos / neg) if neg > 0 else (math.inf if pos > 0 else None),
        "by_engine": dict(Counter(r.engine for r in tr)),
    }


def _e4_verdict(e4: dict[str, Any], cov: dict[str, Any]) -> dict[str, str]:
    n_complete = int(cov.get("n_complete_days", 0))
    status = e4.get("status", "NOT_RUN")
    n_shocks = len(e4.get("shocks", []))
    if status != "OK":
        return {
            "verdict": "INCONCLUSIVE",
            "reason": (
                f"E4 lagged 99th-percentile thresholds need 30 complete UTC days of receipt-timed "
                f"trades + allLiquidation; local raw stores hold {n_complete} complete days "
                f"({cov.get('n_days_touched', 0)} days touched, "
                f"{cov.get('trade_minutes_total', 0)} trade-minutes). Status {status}; "
                f"{n_shocks} qualifying shocks. INSUFFICIENT EVIDENCE — requirement not lowered."
            ),
        }
    arms = Counter(s.get("arm") for s in e4.get("shocks", []))
    promoted = max(arms.get("REVERSAL", 0), arms.get("CONTINUATION", 0))
    if n_shocks < 300 or promoted < 150:
        return {
            "verdict": "INCONCLUSIVE",
            "reason": (
                f"{n_shocks} parent shocks (need >= 300) and {promoted} events in the largest arm "
                "(need >= 150): INSUFFICIENT EVIDENCE."
            ),
        }
    return {
        "verdict": "CONTINUE RESEARCH",
        "reason": "sample reached; economic gates evaluated in e4",
    }


def run(
    data_dir: Path,
    out_root: Path,
    start: date | None = None,
    end: date | None = None,
    run_id: str | None = None,
    skip_e4: bool = False,
) -> RunResult:
    assert RESEARCH_ONLY and not ORDER_CAPABLE
    run_id = run_id or datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out = out_root / run_id
    out.mkdir(parents=True, exist_ok=True)
    files: list[str] = []

    # ---- Phase 0
    inv = build_inventory(data_dir)
    write_inventory(inv, out / "data_inventory.json")
    files.append("data_inventory.json")

    # ---- data (development span only; the holdout cap is enforced by the loader)
    b5, prov5, days5 = load_bybit_bars(data_dir, "5m", start, end)
    b1, prov1, days1 = load_bybit_bars(data_dir, "1m", start, end)
    fund_t, fund_r, prov_f = load_funding_proxy(data_dir)
    first_day, last_day = (days5[0], days5[-1]) if days5 else (None, None)
    atr = wilder_atr(b5, ATR_PERIOD)
    er = efficiency_ratio(b5, ER_BARS)
    boxes = hour_boxes(b5)

    # ---- E1 / E2 benchmark replay
    e1_parents = e1mod.run_e1(e1mod.E1Inputs(b5, b1, atr, er, boxes, fund_t, fund_r, BASE_COST_BPS))
    e2_parents = e2mod.run_e2(e2mod.E2Inputs(b5, b1, atr, er, boxes, fund_t, fund_r, BASE_COST_BPS))
    engines = {"E1": e1_parents, "E2": e2_parents}
    for name, ps in engines.items():
        files += _write_table([p.as_row() for p in ps], out / f"{name.lower()}_parents")
    trades = {k: [p for p in v if p.traded] for k, v in engines.items()}
    files += _write_table(
        [p.as_row() for k in ENGINE_ORDER for p in trades[k]], out / "trade_replay"
    )
    files += _write_table(
        benchmark_markouts(e1_parents + e2_parents, b1), out / "markouts_e1_e2_benchmark"
    )

    # ---- portfolio: E1 alone, E2 alone, E1+E2 (fixed engine order, never optimised)
    portfolios = {
        "E1": simulate("E1", e1_parents, ENGINE_ORDER),
        "E2": simulate("E2", e2_parents, ENGINE_ORDER),
        "E1+E2": simulate("E1+E2", e1_parents + e2_parents, ENGINE_ORDER),
    }
    files += _write_table(
        [dict(r.as_dict(), portfolio=n) for n, pf in portfolios.items() for r in pf.rows],
        out / "portfolio_replay",
    )

    # ---- metrics, gates, tables
    funnels = {k: mx.funnel(v, first_day, last_day) for k, v in engines.items()}
    gate_res = {
        k: mx.gates(trades[k], first_day, last_day, n_parents=len(engines[k])) for k in engines
    }
    cost_rows = [r for k in ENGINE_ORDER for r in _cost_rows(k, trades[k])]
    freq_rows = [_frequency_rows(k, funnels[k], portfolios[k]) for k in ENGINE_ORDER]
    pf_all = portfolios["E1+E2"]
    freq_rows.append(
        _frequency_rows(
            "E1+E2",
            mx.funnel(e1_parents + e2_parents, first_day, last_day),
            pf_all,
        )
    )
    regime_rows = [r for k in ENGINE_ORDER for r in _regime_rows(k, trades[k])]
    files += _write_table(cost_rows, out / "cost_analysis", parquet=False)
    files += _write_table(freq_rows, out / "frequency_analysis", parquet=False)
    files += _write_table(regime_rows, out / "regime_analysis", parquet=False)

    # ---- E4 event study on every local raw store
    roots = raw_roots(data_dir)
    cov = inv["raw_store_coverage"]
    e4: dict[str, Any] = {"status": "SKIPPED"} if skip_e4 else e4mod.run_e4(roots)
    files += _write_table(e4.get("shocks", []), out / "e4_parents")
    files += _write_table(e4.get("flow_parents", []), out / "e4_flow_parents")
    files += _write_table(
        [
            {"parent_id": pid, "control_t_ms": t}
            for pid, ts in sorted(e4.get("controls", {}).items())
            for t in ts
        ],
        out / "e4_controls",
        parquet=False,
    )
    files += _write_table(
        [
            {k: v for k, v in s.items() if k.startswith("mk_") or k in ("parent_id", "kind")}
            for s in e4.get("shocks", []) + e4.get("flow_parents", [])
        ],
        out / "markouts_e4",
        parquet=False,
    )
    e4_summary = {k: v for k, v in e4.items() if k not in ("shocks", "flow_parents", "controls")}
    e4_summary["n_shocks"] = len(e4.get("shocks", []))
    e4_summary["n_flow_parents"] = len(e4.get("flow_parents", []))
    e4_summary["arms"] = dict(Counter(s.get("arm") for s in e4.get("shocks", [])))

    verdicts = {
        k: {"verdict": gate_res[k]["verdict"], "reason": gate_res[k]["reason"]} for k in engines
    }
    verdicts["E4"] = _e4_verdict(e4, cov)
    for v in verdicts.values():
        assert v["verdict"] in VERDICTS

    # ---- manifest
    manifest: dict[str, Any] = {
        "schema": "intraday_portfolio_experiment_manifest-1",
        "run_id": run_id,
        "generated_at_utc": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "research_only": RESEARCH_ONLY,
        "order_capable": ORDER_CAPABLE,
        "python": platform.python_version(),
        "package_sha256": _package_hashes(),
        "data": {
            "bybit_5m": {
                "provenance": prov5.__dict__,
                "days": len(days5),
                "first": first_day,
                "last": last_day,
            },
            "bybit_1m": {"provenance": prov1.__dict__, "days": len(days1)},
            "funding_proxy": prov_f.__dict__,
            "raw_roots": [str(r) for r in roots],
            "requested_start": start.isoformat() if start else None,
            "requested_end": end.isoformat() if end else None,
        },
        "frozen_parameters": {
            "atr_period": ATR_PERIOD,
            "er_bars_4h": ER_BARS,
            "er_range_max": ER_RANGE_MAX,
            "stop_atr_buffer": rp.STOP_ATR_BUFFER,
            "stop_bps_min_max": [rp.STOP_MIN_BPS, rp.STOP_MAX_BPS],
            "e1": {
                "retest_bars": e1mod.RETEST_BARS,
                "max_hold_min": e1mod.MAX_HOLD_MIN,
                "target_r": e1mod.TARGET_R,
                "cooldown_min": e1mod.COOLDOWN_MIN,
                "cooldown_inside_bars": e1mod.COOLDOWN_INSIDE_BARS,
            },
            "e2": {
                "sweep_atr": e2mod.SWEEP_ATR,
                "vwap_min_bps": e2mod.VWAP_MIN_BPS,
                "vwap_min_stop_mult": e2mod.VWAP_MIN_STOP_MULT,
                "max_hold_min": e2mod.MAX_HOLD_MIN,
                "new_excursion_min": e2mod.NEW_EXCURSION_MIN,
                "new_excursion_inside_bars": e2mod.NEW_EXCURSION_INSIDE_BARS,
            },
            "e4": {
                "window_s": e4mod.WINDOW_S,
                "percentile": e4mod.PCT,
                "history_days": e4mod.HISTORY_DAYS,
                "dedupe_s": e4mod.DEDUPE_S,
                "new_shock_gap_s": e4mod.NEW_SHOCK_GAP_S,
                "shock_atr_mult": e4mod.SHOCK_ATR_MULT,
                "horizons_s": list(e4mod.HORIZONS_S),
                "latencies_s": list(e4mod.LATENCIES_S),
                "bbo_max_age_ms": e4mod.BBO_MAX_AGE_MS,
            },
            "costs": {
                "scenarios_bps": list(COST_SCENARIOS_BPS),
                "base_bps": BASE_COST_BPS,
                "stress_bps": STRESS_COST_BPS,
                "taker_fee_bps_per_side": TAKER_FEE_BPS,
                "primary_model": "taker/taker",
            },
            "portfolio": {"engine_order": list(ENGINE_ORDER), "max_positions": 1},
            "gates": mx.GATES,
            "sample_min_parents": mx.SAMPLE_MIN_PARENTS,
            "sample_min_months": mx.SAMPLE_MIN_MONTHS,
            "risk_reference_usdt_per_trade": 5.0,
        },
        "interpretations": INTERPRETATIONS,
        "results": {
            "funnels": funnels,
            "gates": gate_res,
            "portfolios": {k: _portfolio_summary(v) for k, v in portfolios.items()},
            "e4": e4_summary,
            "verdicts": verdicts,
        },
        "files": sorted(set(files)),
    }
    (out / "experiment_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n"
    )
    files.append("experiment_manifest.json")

    # ---- README / SUMMARY
    (out / "README.md").write_text(_readme(run_id, inv, manifest))
    (out / "SUMMARY.md").write_text(_summary(run_id, inv, manifest, cov))
    files += ["README.md", "SUMMARY.md"]
    results: dict[str, Any] = manifest["results"]
    return RunResult(run_id, out, verdicts, sorted(set(files)), results)


def _readme(run_id: str, inv: dict[str, Any], man: dict[str, Any]) -> str:
    lines = [
        f"# Intraday portfolio research run `{run_id}`",
        "",
        "RESEARCH ONLY. No order-capable component exists in `btc_swing/research/intraday_portfolio/`.",
        "No frozen runner (V5/V5.1, V10, V11, V11-WARM, V11-LITE) was read, modified or imported.",
        "",
        "## Files",
        "",
        "| file | content |",
        "|---|---|",
        "| data_inventory.json | Phase 0 inventory: AVAILABLE / PARTIAL / MISSING per required input |",
        "| experiment_manifest.json | frozen parameters, package hashes, data spans, every metric |",
        "| e1_parents.{parquet,csv} / e2_parents.* | every parent episode incl. rejected (reasons kept) |",
        "| trade_replay.* | the BENCHMARK-ledger completed trades of E1 and E2 |",
        "| markouts_e1_e2_benchmark.* | gross markouts after the benchmark entry (no receipt time) |",
        "| portfolio_replay.* | one-position simulator rows for E1, E2, E1+E2 |",
        "| cost_analysis.csv | scenarios 14/16/20/24 bps, taker/taker, per engine |",
        "| frequency_analysis.csv | candidates / eligible / fills / executed per day, overlap, occupancy |",
        "| regime_analysis.csv | side, chronological block, weekday/weekend, vol bucket, exit reason |",
        "| e4_parents.* / e4_flow_parents.* / e4_controls.csv / markouts_e4.csv | E4 event study |",
        "| SUMMARY.md | one verdict per engine with the exact reason |",
        "",
        "## Ledgers",
        "",
        "BENCHMARK: entry = open of the first Bybit 1m bar after the decision close; cost scenario",
        "subtracted once (commission + spread + shortfall + latency); funding = Binance proxy (labelled).",
        "EXECUTABLE: UNAVAILABLE for E1/E2 (no historical BBO / receipt time) — stated, never inferred.",
        "",
        "## Data",
        "",
        f"Bybit 5m/1m trade-built bars {man['data']['bybit_5m']['first']}..{man['data']['bybit_5m']['last']}",
        f"({man['data']['bybit_5m']['days']} days); locked holdout from {inv['holdout_locked_never_read_from']}",
        "never read. E4 raw stores: see `raw_store_coverage` in data_inventory.json.",
        "",
    ]
    return "\n".join(lines)


def _summary(run_id: str, inv: dict[str, Any], man: dict[str, Any], cov: dict[str, Any]) -> str:
    r = man["results"]
    out = [f"# SUMMARY — intraday portfolio research `{run_id}`", ""]
    out.append("| engine | verdict | reason |")
    out.append("|---|---|---|")
    for k in ("E1", "E2", "E4"):
        v = r["verdicts"][k]
        out.append(f"| {k} | **{v['verdict']}** | {v['reason']} |")
    out.append("")
    out.append("## Data coverage")
    d = man["data"]
    out.append(
        f"- Bybit 5m/1m trade-built bars: {d['bybit_5m']['first']}..{d['bybit_5m']['last']} "
        f"({d['bybit_5m']['days']} days); holdout from {inv['holdout_locked_never_read_from']} never read."
    )
    out.append(
        "- Historical BBO / receipt time / liquidations: MISSING -> E1/E2 EXECUTABLE replay UNAVAILABLE "
        "(BENCHMARK ledger only; feasibility / cost-envelope analysis)."
    )
    out.append(
        f"- Raw Bybit receipt-timed stores: {cov.get('n_days_touched', 0)} UTC days touched, "
        f"{cov.get('n_complete_days', 0)} complete (E4 threshold needs 30), "
        f"{cov.get('trade_minutes_total', 0)} trade-minutes, {cov.get('bbo_minutes_total', 0)} BBO-minutes."
    )
    out.append("")
    out.append("## E1 / E2 benchmark economics (taker/taker, 16 bps base; 24 bps stress)")
    out.append("")
    out.append(
        "| engine | parents | eligible | trades | trades/day | gross bps | net bps @16 | LB95 day-cluster | net R | net USDT | PF @16 | win | net bps @24 | PF @24 | MFE | MAE | hold min | maxDD USDT |"
    )
    out.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k in ("E1", "E2"):
        fn, g = r["funnels"][k], r["gates"][k]
        s16, s24 = g["summary_16"], g["summary_24"]
        out.append(
            f"| {k} | {fn['parent_episodes']} | {fn['eligible_entries']} | {fn['completed_trades']} | "
            f"{_f(fn['trades_per_day'], 3)} | {_f(s16.get('gross_bps_mean'))} | {_f(s16.get('net_bps_mean'))} | "
            f"{_f(s16.get('net_bps_lower_bound_95_day_cluster'))} | {_f(s16.get('net_r_sum'))} | "
            f"{_f(s16.get('net_usdt'))} | {_f(s16.get('profit_factor'))} | {_f(s16.get('win_rate'), 3)} | "
            f"{_f(s24.get('net_bps_mean'))} | {_f(s24.get('profit_factor'))} | {_f(s16.get('mfe_bps_mean'))} | "
            f"{_f(s16.get('mae_bps_mean'))} | {_f(s16.get('hold_min_mean'), 1)} | {_f(s16.get('max_dd_usdt'))} |"
        )
    out.append("")
    out.append("## Frequency")
    out.append("")
    for k in ("E1", "E2"):
        fn = r["funnels"][k]
        out.append(
            f"- {k}: candidates/day {_f(fn['candidates_per_day'], 3)}, eligible/day {_f(fn['eligible_per_day'], 3)}, "
            f"trades/day {_f(fn['trades_per_day'], 3)}, zero-trade days {fn['zero_trade_days']} of {fn['calendar_days']}, "
            f"daily p10/p50/p90 {fn['daily_frequency_p10_p50_p90']}, rejection rate {_f(fn['rejection_rate'], 3)}; "
            f"rejections {json.dumps(fn['rejection_reasons'], sort_keys=True)}"
        )
    out.append("")
    out.append("## Portfolio (one position, engine order E1 > E2, later signals telemetry only)")
    out.append("")
    out.append(
        "| portfolio | executed | occupancy loss | abstain opposite | net bps | net R | PF |"
    )
    out.append("|---|---|---|---|---|---|---|")
    for k, p in r["portfolios"].items():
        out.append(
            f"| {k} | {p['executed']} | {p['occupancy_loss']} | {p['abstain_opposite']} | "
            f"{_f(p['net_bps_mean'])} | {_f(p['net_r_sum'])} | {_f(p['profit_factor'])} |"
        )
    out.append("")
    out.append("## Gates (per engine)")
    out.append("")
    for k in ("E1", "E2"):
        g = r["gates"][k]
        failed = [c for c, ok in g["checks"].items() if not ok]
        out.append(
            f"- {k}: failed {failed if failed else 'none'}; positive blocks {g['positive_blocks']}/4; "
            f"max block share {_f(g['max_block_share_of_positive_pnl'], 3)}; "
            f"mean net without top-5 winners {_f(g['fragility_mean_net_without_top5_bps'])} bps"
        )
    out.append("")
    e4 = r["e4"]
    out.append("## E4 event study")
    out.append("")
    out.append(
        f"- status {e4.get('status')}; messages: trades {e4.get('n_trades', 0)}, liquidations "
        f"{e4.get('n_liquidations', 0)}, BBO updates {e4.get('n_bbo', 0)}; grid seconds {e4.get('grid_seconds', 0)}; "
        f"threshold hours available {e4.get('threshold_hours_available')}; shocks {e4.get('n_shocks', 0)}; "
        f"flow-only parents {e4.get('n_flow_parents', 0)}; arms {e4.get('arms')}"
    )
    out.append("")
    out.append("## Missing datasets (exact)")
    out.append("")
    for name, item in inv["inputs"].items():
        if item["status"] in ("MISSING", "PARTIAL"):
            out.append(f"- {name}: {item['status']} — {item.get('note') or item.get('source')}")
    out.append("")
    out.append("## Interpretations applied (not parameters)")
    out.append("")
    for k, v in man["interpretations"].items():
        out.append(f"- {k}: {v}")
    out.append("")
    out.append("No engine is ELIGIBLE FOR RUNNER ENGINEERING unless its verdict above says so.")
    out.append("")
    return "\n".join(out)
