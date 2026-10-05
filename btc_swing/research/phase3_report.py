"""Render reports/BTC_SWING_V1_PHASE3_UNTOUCHED_VALIDATION.md (19 sections, one classification).

The pre-declared criteria and the classification rule are rendered from `phase3.CRITERIA_TEXT`,
`phase3.THRESHOLDS` and `phase3.classify`, which were committed in the freeze manifest before any
2025+ result existed. Nothing in this renderer evaluates a criterion; it only displays
`Phase3Result.criteria` and `Phase3Result.classification`.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import polars as pl

from btc_swing.research.phase2_report import _summary_rows
from btc_swing.research.phase3 import (
    CLASSIFICATION_RULE,
    CRITERIA_TEXT,
    THRESHOLDS,
    VARIANT,
    VARIANT_RULE,
    Phase3Result,
)
from btc_swing.research.phase21_report import (
    _arm_rows,
    _compare_block,
    _ms,
    _n,
    _null_rows,
    _p,
    _se,
    _side_cell,
    _table,
)
from btc_swing.research.phase24_report import STAT_HEADERS, SUMMARY_HEADERS, _by_rows, _stat_row


def _fam_cell(d: dict[str, Any] | None) -> str:
    if not d or not d.get("n"):
        return "n=0"
    return f"n={d['n']}, R={_n(d['expectancy_R'])}, PF={_n(d['profit_factor'], 2)}, win={_p(d['win_rate'], 0)}"


def _fam_stability(ref: list[dict[str, Any]], cur: list[dict[str, Any]], label: str) -> list[str]:
    fams = sorted({f["family"] for f in ref} | {f["family"] for f in cur})
    rows = []
    for fam in fams:
        r = next((f for f in ref if f["family"] == fam), None)
        c = next((f for f in cur if f["family"] == fam), None)
        rows.append([fam, _fam_cell(r), _fam_cell(c)])
    return [f"{label}:", "", *_table(["family", "Phase 2 window 2022-2024", "2025+ holdout"], rows)]


def _ci(o: dict[str, Any]) -> str:
    e, se = o.get("expectancy_R", 0.0) or 0.0, _se(o)
    if math.isnan(se):
        return "n/a"
    return f"[{_n(e - 1.96 * se)}, {_n(e + 1.96 * se)}]"


def _period_rows(cq: list[dict[str, Any]], vq: list[dict[str, Any]], key: str) -> list[list[str]]:
    keys = sorted({d[key] for d in cq} | {d[key] for d in vq})

    def cell(d: dict[str, Any] | None) -> str:
        if not d or not d.get("n"):
            return "n=0"
        return f"n={d['n']}, R={_n(d['mean_R'])}, sum {_n(d['sum_R'], 1)}R, win {_p(d['win_rate'], 0)}, P&L {_n(d['sum_pnl'], 0)}"

    return [
        [
            k,
            cell(next((d for d in cq if d[key] == k), None)),
            cell(next((d for d in vq if d[key] == k), None)),
        ]
        for k in keys
    ]


def render_phase3(res: Phase3Result) -> str:
    c, v = res.control, res.variant
    m, fz, fc, pf = res.manifest, res.freeze, res.freeze_check, res.proof
    co, vo = c.metrics["overall"], v.metrics["overall"]
    rm, oc = res.removed, res.removed.get("opportunity_cost", {})
    rem, lg, sq = res.remaining, res.longs, res.sequencing
    ref = fz.get("phase24_reference", {})
    ref_arms = ref.get("arms", {}) if ref.get("available") else {}
    ref_c = ref_arms.get("CONTROL", {})
    ref_v = ref_arms.get("NO_NEW_SHORT_IN_TREND_DOWN", {})
    raw = fz.get("raw_archive_before_phase3", {})
    cov = fz.get("coverage_holdout", {})
    seg_names = [s.name for s in res.segments]
    lines: list[str] = [
        "# BTC Swing V1 — Phase 3: UNTOUCHED CONFIRMATORY VALIDATION (CONTROL vs APPROVED_VARIANT)",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · holdout {_ms(m['period_start_ms'])} -> {_ms(m['period_end_ms'])} UTC · "
        f"CONTROL result hash `{c.result.result_hash[:12]}` · APPROVED_VARIANT result hash `{v.result.result_hash[:12]}` · code `{c.result.manifest['code_version']}` · freeze commit `{str(fz.get('code_commit', ''))[:12]}`",
        "",
        f"**Primary question.** Does blocking new SHORT entries in TREND_DOWN ({VARIANT_RULE}) retain its benefit on genuinely untouched 2025-2026 data?",
        "",
        "Confirmatory test, not a research phase: one run per arm, nothing changed after seeing any result. Paper/backtest only. No live trading, no authenticated exchange access, no real money.",
        "",
        "## 1. Frozen strategy proof",
        "",
        *_table(
            ["check", "value"],
            [
                [
                    "freeze manifest",
                    f"`{m['freeze_manifest']}` created {str(fz.get('created_at', ''))[:19]} UTC, committed at `{str(fz.get('code_commit', ''))[:12]}` before any holdout evaluation",
                ],
                [
                    "strategy / version",
                    f"{fz.get('strategy_name')} {fz.get('strategy_version')}; rule versions {', '.join(f'{k}={val}' for k, val in (fz.get('rule_versions') or {}).items())}",
                ],
                [
                    "code commit at run",
                    f"`{str(fc.get('run_commit', ''))[:12]}` ({fc.get('run_code_version')}); strategy code unchanged since the freeze (`btc_swing/`, `config/`, lockfile): {_n(fc.get('strategy_code_unchanged_since_freeze'))}",
                ],
                [
                    "CONTROL config hash",
                    f"`{fz['arms']['CONTROL']['config_hash']}` = Phase 2.4 CONTROL config hash: {_n(fc.get('control_config_hash_matches_phase24'))}",
                ],
                [
                    f"{VARIANT} config hash",
                    f"`{fz['arms'][VARIANT]['config_hash']}` = Phase 2.4 variant config hash: {_n(fc.get('variant_config_hash_matches_phase24'))}",
                ],
                [
                    "live configs match the freeze",
                    f"CONTROL {_n(fc.get('control_config_hash_matches_freeze'))}, {VARIANT} {_n(fc.get('variant_config_hash_matches_freeze'))}",
                ],
                [
                    "experiment switches",
                    f"entry_mode = {fz['experiment_switches']['entry_mode']}; breakeven_after_tp1 = {_n(fz['experiment_switches']['breakeven_after_tp1'])}; block_short_in_trend_down = CONTROL false / {VARIANT} true; no Phase 2.1 / 2.2 / 2.3 variant active",
                ],
                [
                    "re-run 2022-01 -> 2025-01, CONTROL",
                    f"result hash `{pf['control_result_hash'][:16]}` = Phase 2 `{pf['control_expected'][:16]}`: {_n(pf['control_identical'])} ({pf['control_trades']} trades)",
                ],
                [
                    f"re-run 2022-01 -> 2025-01, {VARIANT}",
                    f"result hash `{pf['variant_result_hash'][:16]}` = Phase 2.4 `{pf['variant_expected'][:16]}`: {_n(pf['variant_identical'])} ({pf['variant_trades']} trades)",
                ],
                ["dataset hashes match the freeze", _n(fc.get("dataset_hashes_match_freeze"))],
                [
                    "holdout dates (frozen)",
                    f"{fz['holdout']['start']} -> {fz['holdout']['end_exclusive']} (exclusive); segments {', '.join(s['name'] for s in fz['holdout']['segments'])}",
                ],
                ["runs per arm on the holdout", str(m["runs_per_arm_on_holdout"])],
            ],
        ),
        "## 2. Untouched-window proof",
        "",
        f"- Local raw archive before Phase 3 (committed `manifests/raw_archive_manifest.jsonl` @ `{raw.get('committed_in')}`): {raw.get('entries')} files, periods {raw.get('min_period')} -> {raw.get('max_period')}, files dated 2025 or later: {raw.get('entries_at_or_after_2025')}.",
        "- Every earlier run loaded the store with `open_time < period_end` and ended at or before 2025-01-01: "
        + ", ".join(f"{d['run']} -> {d['period_end']}" for d in fz.get("prior_run_windows", []))
        + ".",
        "- The 2025+ archive files were downloaded for the first time in this phase, after the Phase 2.4 classification and the owner's approval; the freeze manifest (configs, hashes, dates, criteria, thresholds) was written and committed before the first holdout backtest.",
        "- No threshold, family, exit, confirmation, regime rule, indicator, leverage rule or cost assumption was changed in this phase; the only code added is the Phase 3 runner/report (analysis and rendering).",
        "",
        "## 3. Data coverage (holdout window)",
        "",
        *_table(
            ["dataset", "rows", "first", "last", "rows in window", "expected", "coverage"],
            [
                [
                    k,
                    str(d.get("rows", 0)),
                    str(d.get("first", ""))[:16],
                    str(d.get("last", ""))[:16],
                    str(d.get("rows_in_study_window", "")),
                    str(d.get("expected_in_study_window", "")),
                    _p(d.get("coverage_pct", math.nan) / 100.0, 2)
                    if d.get("coverage_pct") is not None
                    else "",
                ]
                for k, d in cov.items()
            ],
        ),
    ]
    ist = fz.get("ingest_stats_2025_plus") or {}
    if ist:
        lines += [
            f"- Ingest 2025-01 -> 2026-09: {ist.get('fetched')} archive files fetched (sha256 verified: {ist.get('fetched', 0) - ist.get('checksum_unverified', 0)}), {ist.get('skipped')} skipped, {ist.get('rows')} rows; missing bars {ist.get('missing_bars')}; anomalies: {len(ist.get('anomalies', []))}"
            + (
                " (" + "; ".join(str(a) for a in ist.get("anomalies", [])[:8]) + ")"
                if ist.get("anomalies")
                else ""
            )
            + ".",
            "",
        ]
    ro = res.pit.get("resample_oracle_holdout", {})
    lines += [
        "## 4. PIT audit",
        "",
        f"- {res.pit['visibility_rule']}. Scope of the rule: {res.pit['rule_scope']}.",
        f"- Deterministic rerun: CONTROL {_n(res.pit['control_deterministic'])}, {VARIANT} {_n(res.pit['variant_deterministic'])}.",
        f"- Truncation audit ({VARIANT}): decisions up to {res.pit['variant_truncation']['cut'][:16]} identical with all later data removed: {_n(res.pit['variant_truncation']['identical'])} ({res.pit['variant_truncation']['rows_compared']} rows).",
        "- Resampling oracle on the holdout (our PIT resample of 5m vs native archive bars): "
        + ", ".join(
            f"{tf}: {d['bars_compared']} bars, {d['bars_differing']} differ ({_n(d['exact_match_pct'], 2)}% exact)"
            for tf, d in ro.items()
        )
        + ".",
        f"- Liquidation basis: {res.pit['liquidation_basis']}. Look-ahead guard: MarketView raises on negative offsets; indicators causal (tests).",
        "",
    ]
    cr = _arm_rows("CONTROL", c.metrics, c.account, c.costs, c.result.manifest["n_episodes"])
    vr = _arm_rows(VARIANT, v.metrics, v.account, v.costs, v.result.manifest["n_episodes"])
    lines += [
        "## 5. Primary results (full holdout)",
        "",
        *_compare_block(cr, vr),
        *_table(
            ["metric", "CONTROL", VARIANT],
            [
                [
                    "gross expectancy R (before fees, slippage, funding)",
                    _n(c.costs.get("expectancy_R_before_costs")),
                    _n(v.costs.get("expectancy_R_before_costs")),
                ],
                ["std of R", _n(co.get("std_R")), _n(vo.get("std_R"))],
                [
                    "total account return / CAGR",
                    f"{_p(c.account.get('total_return'))} / {_p(c.account.get('cagr'))}",
                    f"{_p(v.account.get('total_return'))} / {_p(v.account.get('cagr'))}",
                ],
                [
                    "max drawdown (trade curve / daily mtm)",
                    f"{_p(c.account.get('max_drawdown_frac_trade_curve'))} / {_p(c.account.get('max_drawdown_frac_daily_mtm'))}",
                    f"{_p(v.account.get('max_drawdown_frac_trade_curve'))} / {_p(v.account.get('max_drawdown_frac_daily_mtm'))}",
                ],
                [
                    "Sortino (daily marks)",
                    _n(c.account.get("sortino_daily_annualised"), 2),
                    _n(v.account.get("sortino_daily_annualised"), 2),
                ],
                [
                    "longest losing / winning streak",
                    f"{c.account.get('longest_losing_streak')} / {c.account.get('longest_winning_streak')}",
                    f"{v.account.get('longest_losing_streak')} / {v.account.get('longest_winning_streak')}",
                ],
                [
                    "mean MFE R / mean MAE R / worst MAE R",
                    f"{_n(co.get('mean_MFE_R'))} / {_n(co.get('mean_MAE_R'))} / {_n(co.get('worst_MAE_R'))}",
                    f"{_n(vo.get('mean_MFE_R'))} / {_n(vo.get('mean_MAE_R'))} / {_n(vo.get('worst_MAE_R'))}",
                ],
                [
                    "mean / median holding hours",
                    f"{_n(co.get('mean_holding_hours'), 1)} / {_n(co.get('median_holding_hours'), 1)}",
                    f"{_n(vo.get('mean_holding_hours'), 1)} / {_n(vo.get('median_holding_hours'), 1)}",
                ],
                [
                    "LONG trades / SHORT trades",
                    f"{c.metrics['by_side'].get('LONG', {}).get('n', 0)} / {c.metrics['by_side'].get('SHORT', {}).get('n', 0)}",
                    f"{v.metrics['by_side'].get('LONG', {}).get('n', 0)} / {v.metrics['by_side'].get('SHORT', {}).get('n', 0)}",
                ],
                [
                    "episodes REGIME_BLOCKED",
                    "0",
                    str(
                        v.episodes.filter(pl.col("outcome_class") == "REGIME_BLOCKED").height
                        if v.episodes.height
                        else 0
                    ),
                ],
                [
                    "trades / week",
                    _n(c.account.get("trades_per_week"), 2),
                    _n(v.account.get("trades_per_week"), 2),
                ],
            ],
        ),
        "## 6. CONTROL vs approved variant",
        "",
        f"- Net expectancy {_n(co.get('expectancy_R'))}R -> {_n(vo.get('expectancy_R'))}R ({_n((vo.get('expectancy_R') or 0) - (co.get('expectancy_R') or 0))}R); profit factor {_n(co.get('profit_factor'), 2)} -> {_n(vo.get('profit_factor'), 2)}; net P&L {_n(c.costs.get('net'), 0)} -> {_n(v.costs.get('net'), 0)} USDT; max drawdown {_p(c.account.get('max_drawdown_frac_trade_curve'))} -> {_p(v.account.get('max_drawdown_frac_trade_curve'))}.",
        f"- Trades {co.get('n', 0)} -> {vo.get('n', 0)}; the rule blocked {rm.get('removed_by_rule', 0)} CONTROL SHORT trades (section 8); {sq['variant_only_trades']['n']} trades exist only in the variant (freed single slot), {sq['control_only_trades_not_blocked']['n']} CONTROL trades not blocked by the rule were lost to shifted sequencing.",
        "",
        *_table(
            ["component of the net P&L change", "USDT"],
            [
                ["total (variant - CONTROL)", _n(sq["total_net_pnl_change"], 0)],
                [
                    "direct effect of the blocked trades (their CONTROL P&L, sign reversed)",
                    _n(sq["direct_effect_removed_trades"], 0),
                ],
                ["secondary sequencing effect", _n(sq["secondary_effect"], 0)],
                [
                    "variant-only trades",
                    f"n={sq['variant_only_trades']['n']}, P&L {_n(sq['variant_only_trades']['pnl'], 0)}"
                    + ("; " if sq["variant_only_trades"]["by_side"] else "")
                    + ", ".join(
                        f"{d['side']}: n={d['n']}, {_n(d['pnl'], 0)} USDT, {_n(d['mean_R'])}R"
                        for d in sq["variant_only_trades"]["by_side"]
                    ),
                ],
                [
                    "CONTROL-only trades not blocked (lost to sequencing)",
                    f"n={sq['control_only_trades_not_blocked']['n']}, P&L {_n(sq['control_only_trades_not_blocked']['pnl'], 0)}"
                    + ("; " if sq["control_only_trades_not_blocked"]["by_side"] else "")
                    + ", ".join(
                        f"{d['side']}: n={d['n']}, {_n(d['pnl'], 0)} USDT, {_n(d['mean_R'])}R"
                        for d in sq["control_only_trades_not_blocked"]["by_side"]
                    ),
                ],
            ],
        ),
        "LONG control check (the rule must not touch LONG logic; differences can only come from the single slot):",
        "",
        *_table(
            ["metric", "value"],
            [
                ["LONG trades CONTROL / variant", f"{lg['control_long']} / {lg['variant_long']}"],
                [
                    "paired LONGs (same detection): identical R / different R",
                    f"{lg['paired_identical_R']} / {lg['paired_different_R']}",
                ],
                [
                    "LONG only in CONTROL",
                    f"{lg['control_only_long']} (P&L {_n(lg['control_only_long_pnl'], 0)} USDT, mean R {_n(lg['control_only_long_mean_R'])})",
                ],
                [
                    "LONG only in the variant",
                    f"{lg['variant_only_long']} (P&L {_n(lg['variant_only_long_pnl'], 0)} USDT, mean R {_n(lg['variant_only_long_mean_R'])})",
                ],
                [
                    "LONG expectancy / PF CONTROL vs variant",
                    f"{_n(lg['control_long_stats'].get('mean_R'))} / {_n(lg['control_long_stats'].get('profit_factor'), 2)} vs {_n(lg['variant_long_stats'].get('mean_R'))} / {_n(lg['variant_long_stats'].get('profit_factor'), 2)}",
                ],
            ],
        ),
    ]
    # 7 LONG / SHORT
    rows = [
        [
            side,
            _side_cell(c.metrics["by_side"].get(side, {})),
            _side_cell(v.metrics["by_side"].get(side, {})),
        ]
        for side in ("LONG", "SHORT")
    ]
    dc, dv = res.dd_sides.get("CONTROL", {}), res.dd_sides.get(VARIANT, {})
    lines += [
        "## 7. LONG vs SHORT",
        "",
        *_table(["side", "CONTROL", VARIANT], rows),
        "SHORT populations (CONTROL shorts = blocked + remaining + shorts lost to sequencing):",
        "",
        *_table(
            STAT_HEADERS,
            [
                _stat_row("CONTROL all SHORT", rem["control"]),
                _stat_row("blocked by the rule (CONTROL trades)", rm.get("removed_stats", {})),
                _stat_row("kept in both arms", rm.get("kept_stats", {})),
                _stat_row(f"{VARIANT} remaining SHORT", rem["variant"]),
            ],
        ),
        *_table(
            ["drawdown-window contribution", "CONTROL", VARIANT],
            [
                [
                    "max DD window (trades, USDT)",
                    f"{dc.get('n_trades', 0)} trades, {_n(dc.get('max_dd_currency'), 0)} USDT ({dc.get('peak_entry')} -> {dc.get('trough_entry')})",
                    f"{dv.get('n_trades', 0)} trades, {_n(dv.get('max_dd_currency'), 0)} USDT ({dv.get('peak_entry')} -> {dv.get('trough_entry')})",
                ],
                [
                    "SHORT P&L in the window",
                    _n(dc.get("short_pnl_in_window"), 0),
                    _n(dv.get("short_pnl_in_window"), 0),
                ],
                [
                    "LONG P&L in the window",
                    _n(dc.get("long_pnl_in_window"), 0),
                    _n(dv.get("long_pnl_in_window"), 0),
                ],
            ],
        ),
        "Remaining SHORT by regime at trigger (variant):",
        "",
        *_table(STAT_HEADERS, _by_rows(rem["variant_by_regime_at_trigger"], "regime_at_trigger")),
        "SHORT by family, CONTROL:",
        "",
        *_table(STAT_HEADERS, _by_rows(rem["control_by_family"], "family")),
        f"SHORT by family, {VARIANT}:",
        "",
        *_table(STAT_HEADERS, _by_rows(rem["variant_by_family"], "family")),
    ]
    # 8 blocked
    rr = rm.get("removed_rows")
    lines += [
        "## 8. Blocked-short analysis",
        "",
        f"- CONTROL SHORT trades: {rm.get('control_short_trades', 0)}; blocked by the rule: {rm.get('removed_by_rule', 0)}; kept: {rm.get('kept_in_variant', 0)}; lost to sequencing/other: {rm.get('short_lost_to_sequencing_or_other', 0)}"
        + (
            " ("
            + ", ".join(
                f"{d['variant_outcome']}={d['len']}" for d in rm.get("unmatched_short_outcomes", [])
            )
            + ")"
            if rm.get("unmatched_short_outcomes")
            else ""
        )
        + ".",
        "- Regime at the trigger bar of the blocked trades: "
        + (
            ", ".join(
                f"{d['regime_at_trigger']}={d['len']}"
                for d in rm.get("removed_regime_at_trigger", [])
            )
            or "none"
        )
        + ".",
        "",
    ]
    if isinstance(rr, pl.DataFrame) and rr.height:
        lines += [
            f"Every blocked trade ({rr.height}; also in `blocked_short_trades.parquet`):",
            "",
            *_table(
                [
                    "entry (UTC)",
                    "family",
                    "regime det./trigger",
                    "CONTROL R",
                    "P&L",
                    "MFE R",
                    "MAE R",
                    "target before stop",
                    "hold h",
                    "exit",
                ],
                [
                    [
                        _ms(r["entry_ms"]),
                        r["family"].replace("_", " "),
                        f"{r['regime_at_detection']}/{r['regime_at_trigger']}",
                        _n(r["R_MULTIPLE"], 2),
                        _n(r["POSITION_PNL"], 0),
                        _n(r["MFE_R"], 2),
                        _n(r["MAE_R"], 2),
                        "yes" if r.get("path_outcome") == "target_first" else "no",
                        _n(r["holding_hours"], 1),
                        r["exit_reason"],
                    ]
                    for r in rr.to_dicts()
                ],
            ),
        ]
    if oc:
        lines += [
            "Aggregate:",
            "",
            *_table(
                ["metric", "value"],
                [
                    [
                        "winners removed / losers avoided",
                        f"{oc['winners_removed']} / {oc['losers_removed']}",
                    ],
                    [
                        "positive R sacrificed / negative R avoided",
                        f"{_n(oc['positive_R_removed'], 1)} / {_n(oc['negative_R_avoided'], 1)}",
                    ],
                    ["net R effect (avoided - sacrificed)", _n(oc["net_R_effect_of_removal"], 1)],
                    [
                        "winners P&L sacrificed / losers P&L avoided (USDT)",
                        f"{_n(oc['winners_pnl_removed'], 0)} / {_n(oc['losers_pnl_avoided'], 0)}",
                    ],
                    ["net P&L effect (USDT)", _n(oc["net_pnl_effect_of_removal"], 0)],
                    [
                        "mean / median blocked R",
                        f"{_n(rm['removed_stats'].get('mean_R'))} / {_n(oc['median_removed_R'])}",
                    ],
                    ["t-stat of the blocked population", _n(rm["removed_stats"].get("t_stat"), 2)],
                    [
                        "share of avoided negative R from the worst 3",
                        _p(oc.get("worst3_share_of_negative_R"), 0),
                    ],
                    [
                        "share of sacrificed positive R from the best 3",
                        _p(oc.get("best3_share_of_positive_R"), 0),
                    ],
                    [
                        "mean blocked R excluding the worst 3",
                        _n(oc.get("removed_without_worst3_mean_R")),
                    ],
                ],
            ),
            "By family:",
            "",
            *_table(STAT_HEADERS, _by_rows(rm.get("removed_by_family", []), "family")),
            "By segment:",
            "",
            *_table(STAT_HEADERS, _by_rows(rm.get("removed_by_segment", []), "segment")),
            "Phase 2.4 found the blocked population to be a coherent negative-expectancy group (60 trades, mean -0.19R, median -1.05R, 34 losers / 26 winners). Whether that structural explanation survives is read directly from the table above; it is not re-tuned.",
            "",
        ]
    else:
        lines += [
            "- The rule blocked no trade on the holdout: criterion 4 is not testable and the variant is identical to CONTROL.",
            "",
        ]
    # 9 family stability
    lines += ["## 9. Family stability (every family shown; none removed or hidden)", ""]
    lines += _fam_stability(
        ref_c.get("families", []), c.families, "CONTROL, Phase 2 combined 2022-2024 vs 2025+"
    )
    lines += _fam_stability(
        ref_v.get("families", []), v.families, f"{VARIANT}, Phase 2.4 combined 2022-2024 vs 2025+"
    )
    for sn in seg_names:
        lines += [
            f"Families in {sn}:",
            "",
            *_table(
                ["family", "CONTROL", VARIANT],
                [
                    [
                        f,
                        _fam_cell(
                            next((x for x in c.segments[sn]["families"] if x["family"] == f), None)
                        ),
                        _fam_cell(
                            next((x for x in v.segments[sn]["families"] if x["family"] == f), None)
                        ),
                    ]
                    for f in sorted(
                        {x["family"] for x in c.segments[sn]["families"]}
                        | {x["family"] for x in v.segments[sn]["families"]}
                    )
                ],
            ),
        ]
    # 10 regime stability
    lines += ["## 10. Regime stability (regime at entry; rules unchanged)", ""]
    for name, arm_rows in res.regimes.items():
        lines += [
            f"{name}, 2025+:",
            "",
            *_table(STAT_HEADERS, _by_rows(arm_rows, "regime_at_entry")),
        ]
    for label, r in (("CONTROL", ref_c), (VARIANT, ref_v)):
        if r.get("by_regime"):
            lines += [
                f"{label}, Phase 2 window 2022-2024 (reference):",
                "",
                *_table(SUMMARY_HEADERS, _summary_rows(r["by_regime"], 20)),
            ]
    # 11 chronology
    lines += ["## 11. 2025 vs 2026 YTD", ""]
    for sn in seg_names:
        cs, vs = c.segments[sn], v.segments[sn]
        n_ep_c = sum(d["len"] for d in cs["episode_outcomes"]) or 0
        n_ep_v = sum(d["len"] for d in vs["episode_outcomes"]) or 0
        lines += [
            f"### {sn} ({_ms(next(s.start_ms for s in res.segments if s.name == sn))[:10]} -> {_ms(next(s.end_ms for s in res.segments if s.name == sn))[:10]})",
            "",
            *_compare_block(
                _arm_rows("CONTROL", cs["metrics"], cs["account"], cs["costs"], n_ep_c),
                _arm_rows(VARIANT, vs["metrics"], vs["account"], vs["costs"], n_ep_v),
            ),
        ]
        lines += [
            f"Sides in {sn}:",
            "",
            *_table(
                ["side", "CONTROL", VARIANT],
                [
                    [
                        side,
                        _side_cell(cs["metrics"]["by_side"].get(side, {})),
                        _side_cell(vs["metrics"]["by_side"].get(side, {})),
                    ]
                    for side in ("LONG", "SHORT")
                ],
            ),
        ]
    lines += [
        "Quarterly:",
        "",
        *_table(
            ["quarter", "CONTROL", VARIANT],
            _period_rows(res.quarters.get("CONTROL", []), res.quarters.get(VARIANT, []), "quarter"),
        ),
    ]
    lines += [
        "Monthly:",
        "",
        *_table(
            ["month", "CONTROL", VARIANT],
            _period_rows(res.months.get("CONTROL", []), res.months.get(VARIANT, []), "month"),
        ),
    ]
    # 12 costs
    cst, vst = c.costs, v.costs
    lines += [
        "## 12. Costs (USDT, full holdout; frozen assumptions, no alternative cost run)",
        "",
        *_table(
            ["component", "CONTROL", VARIANT],
            [
                ["trades", str(co.get("n", 0)), str(vo.get("n", 0))],
                [
                    "gross P&L before slippage",
                    _n(cst.get("gross_before_slippage"), 0),
                    _n(vst.get("gross_before_slippage"), 0),
                ],
                ["slippage", _n(cst.get("slippage"), 0), _n(vst.get("slippage"), 0)],
                ["fees", _n(cst.get("fees"), 0), _n(vst.get("fees"), 0)],
                ["funding", _n(cst.get("funding"), 0), _n(vst.get("funding"), 0)],
                ["net P&L", _n(cst.get("net"), 0), _n(vst.get("net"), 0)],
                [
                    "gross expectancy R / net expectancy R",
                    f"{_n(cst.get('expectancy_R_before_costs'))} / {_n(cst.get('expectancy_R_net'))}",
                    f"{_n(vst.get('expectancy_R_before_costs'))} / {_n(vst.get('expectancy_R_net'))}",
                ],
                [
                    "cost drag per trade (R)",
                    _n(cst.get("cost_drag_R_per_trade")),
                    _n(vst.get("cost_drag_R_per_trade")),
                ],
                [
                    "fees as % of gross",
                    _p((cst.get("fees_pct_of_gross") or math.nan) / 100.0, 1),
                    _p((vst.get("fees_pct_of_gross") or math.nan) / 100.0, 1),
                ],
                [
                    "funding events",
                    str(cst.get("n_funding_events", 0)),
                    str(vst.get("n_funding_events", 0)),
                ],
            ],
        ),
    ]
    # 13 leverage
    lc, lv = res.leverage.get("CONTROL", {}), res.leverage.get(VARIANT, {})

    def lev_dist(d: dict[str, Any]) -> str:
        return (
            ", ".join(f"{x['leverage']:g}x: {x['len']}" for x in d.get("leverage_distribution", []))
            or "n/a"
        )

    lines += [
        "## 13. Leverage / liquidation (frozen sizing; safety verification only)",
        "",
        *_table(
            ["metric", "CONTROL", VARIANT],
            [
                ["leverage used (trades per level)", lev_dist(lc), lev_dist(lv)],
                [
                    "mean / max leverage",
                    f"{_n(lc.get('mean_leverage'), 2)} / {_n(lc.get('max_leverage'), 0)}",
                    f"{_n(lv.get('mean_leverage'), 2)} / {_n(lv.get('max_leverage'), 0)}",
                ],
                [
                    "mean / max margin % of equity",
                    f"{_n(lc.get('mean_margin_pct_equity'), 1)} / {_n(lc.get('max_margin_pct_equity'), 1)}",
                    f"{_n(lv.get('mean_margin_pct_equity'), 1)} / {_n(lv.get('max_margin_pct_equity'), 1)}",
                ],
                [
                    "mean / max account risk at stop %",
                    f"{_n(lc.get('mean_account_risk_pct'), 2)} / {_n(lc.get('max_account_risk_pct'), 2)}",
                    f"{_n(lv.get('mean_account_risk_pct'), 2)} / {_n(lv.get('max_account_risk_pct'), 2)}",
                ],
                [
                    "risk-capped trades / risk-rejected episodes",
                    f"{lc.get('n_risk_capped', 0)} / {lc.get('n_risk_rejected_episodes', 0)}",
                    f"{lv.get('n_risk_capped', 0)} / {lv.get('n_risk_rejected_episodes', 0)}",
                ],
                [
                    "min / median stop-to-liquidation ratio",
                    f"{_n(lc.get('min_stop_to_liq_ratio'), 2)} / {_n(lc.get('median_stop_to_liq_ratio'), 2)}",
                    f"{_n(lv.get('min_stop_to_liq_ratio'), 2)} / {_n(lv.get('median_stop_to_liq_ratio'), 2)}",
                ],
                [
                    "min liquidation distance (% / ATR)",
                    f"{_n(lc.get('min_liq_distance_pct'), 2)}% / {_n(lc.get('min_liq_distance_atr'), 2)}",
                    f"{_n(lv.get('min_liq_distance_pct'), 2)}% / {_n(lv.get('min_liq_distance_atr'), 2)}",
                ],
                ["worst MAE %", _n(lc.get("worst_MAE_pct"), 2), _n(lv.get("worst_MAE_pct"), 2)],
                ["liquidations", str(lc.get("liquidations", 0)), str(lv.get("liquidations", 0))],
                [
                    "max planned account loss at stop % / if liquidated %",
                    f"{_n(lc.get('max_planned_account_loss_at_stop_pct'), 2)} / {_n(lc.get('max_account_loss_if_liquidated_pct'), 2)}",
                    f"{_n(lv.get('max_planned_account_loss_at_stop_pct'), 2)} / {_n(lv.get('max_account_loss_if_liquidated_pct'), 2)}",
                ],
                [
                    "liquidation basis",
                    str(lc.get("liquidation_basis", "n/a")),
                    str(lv.get("liquidation_basis", "n/a")),
                ],
            ],
        ),
    ]
    # 14 drawdown / equity
    ca, va = c.account, v.account
    lines += [
        "## 14. Drawdown / equity (sequential, one position, fixed research equity for sizing)",
        "",
        *_table(
            ["metric", "CONTROL", VARIANT],
            [
                [
                    "initial / final equity",
                    f"{_n(ca.get('initial_equity'), 0)} / {_n(ca.get('final_equity'), 2)}",
                    f"{_n(va.get('initial_equity'), 0)} / {_n(va.get('final_equity'), 2)}",
                ],
                [
                    "total return / CAGR",
                    f"{_p(ca.get('total_return'))} / {_p(ca.get('cagr'))}",
                    f"{_p(va.get('total_return'))} / {_p(va.get('cagr'))}",
                ],
                [
                    "max drawdown trade curve / daily mtm",
                    f"{_p(ca.get('max_drawdown_frac_trade_curve'))} / {_p(ca.get('max_drawdown_frac_daily_mtm'))}",
                    f"{_p(va.get('max_drawdown_frac_trade_curve'))} / {_p(va.get('max_drawdown_frac_daily_mtm'))}",
                ],
                [
                    "max drawdown (USDT)",
                    _n(ca.get("max_drawdown_currency"), 0),
                    _n(va.get("max_drawdown_currency"), 0),
                ],
                [
                    "Sharpe / Sortino (daily marks)",
                    f"{_n(ca.get('sharpe_daily_annualised'), 2)} / {_n(ca.get('sortino_daily_annualised'), 2)}",
                    f"{_n(va.get('sharpe_daily_annualised'), 2)} / {_n(va.get('sortino_daily_annualised'), 2)}",
                ],
                [
                    "longest losing streak",
                    str(ca.get("longest_losing_streak")),
                    str(va.get("longest_losing_streak")),
                ],
                [
                    "drawdown-window family contribution",
                    "; ".join(
                        f"{d['family']} {_n(d['pnl_in_dd_window'], 0)} (n={d['n']})"
                        for d in ca.get("drawdown_window_family_contribution", [])
                    ),
                    "; ".join(
                        f"{d['family']} {_n(d['pnl_in_dd_window'], 0)} (n={d['n']})"
                        for d in va.get("drawdown_window_family_contribution", [])
                    ),
                ],
                [
                    "Phase 2.4 variant max DD (reference for the cap)",
                    "",
                    f"{_p(ref_v.get('max_drawdown_frac_trade_curve'))} -> cap {_p(THRESHOLDS['c5_drawdown_cap_frac'])}",
                ],
            ],
        ),
    ]
    # 15 null / uncertainty
    lines += [
        "## 15. Null benchmark and statistical uncertainty",
        "",
        *_table(
            ["arm", "n", "mean R", "std R", "SE", "t", "95% interval (normal approx.)"],
            [
                [
                    label,
                    str(o.get("n", 0)),
                    _n(o.get("expectancy_R")),
                    _n(o.get("std_R")),
                    _n(_se(o)),
                    _n(o.get("t_stat_R"), 2),
                    _ci(o),
                ]
                for label, o in (("CONTROL", co), (VARIANT, vo))
            ]
            + [
                [
                    f"{label} {sn}",
                    str(o.get("n", 0)),
                    _n(o.get("expectancy_R")),
                    _n(o.get("std_R")),
                    _n(_se(o)),
                    _n(o.get("t_stat_R"), 2),
                    _ci(o),
                ]
                for sn in seg_names
                for label, o in (
                    ("CONTROL", c.segments[sn]["metrics"]["overall"]),
                    (VARIANT, v.segments[sn]["metrics"]["overall"]),
                )
            ],
        ),
        "Geometry-matched null benchmark (random entries with the same side, stop %, ATR, sizing, exits and costs; time- and regime-matched; K per trade as in Phase 2):",
        "",
        *_table(
            ["series", "n", "mean R", "win rate", "strategy - null (R)", "z", "P(null >= strat)"],
            [*_null_rows("CONTROL", c.null), *_null_rows(VARIANT, v.null)],
        ),
        "- The interval is the existing normal approximation of the framework (mean ± 1.96·SE); no bootstrap or other statistic was added for this phase, and no statistical selection criterion was introduced after seeing results.",
        f"- Blocked population: n={rm.get('removed_by_rule', 0)}, mean {_n(rm.get('removed_stats', {}).get('mean_R'))}R, t = {_n(rm.get('removed_stats', {}).get('t_stat'), 2)}.",
        "",
    ]
    # 16 criteria
    lines += [
        "## 16. Pre-declared confirmation criteria (committed in the freeze manifest before any result)",
        "",
        *_table(
            ["#", "criterion", "met", "evidence"],
            [
                [str(cr_["id"]), cr_["text"], "yes" if cr_["met"] else "no", cr_["evidence"]]
                for cr_ in res.criteria
            ],
        ),
        f"Classification rule (pre-declared): {CLASSIFICATION_RULE}",
        "",
        "## 17. Final classification",
        "",
        f"**{res.classification}**",
        "",
        f"- Criteria met: {sum(1 for cr_ in res.criteria if cr_['met'])} of {len(res.criteria)}.",
        "",
        "## 18. Limitations",
        "",
        f"- One instrument, one holdout of {_n((m['period_end_ms'] - m['period_start_ms']) / 86_400_000 / 365.25, 2)} years ({co.get('n', 0)} CONTROL / {vo.get('n', 0)} variant trades); the standard errors in section 15 bound what this sample can say.",
        "- The variant rule was derived from the Phase 2 regime table (in-sample on 2022-2024) and confirmed in Phase 2.4 on the same window; this holdout is its first and only out-of-sample test. A single holdout cannot distinguish a durable effect from a favourable regime mix.",
        "- The regime label is a model output with frozen thresholds; the holdout's regime composition (section 10) determines how often the rule fires.",
        "- Costs are the frozen assumptions (taker 5 bps, slippage 2/5 bps, archive funding); liquidation uses mark-price klines where available. No alternative cost or latency scenario was run.",
        "- Sequential single-slot account: results depend on trade ordering; the direct/secondary decomposition in section 6 separates the rule's own effect from sequencing.",
        "- Archive data: Binance Vision monthly/daily files, sha256 verified; two known venue incidents with stale 5m bars exist in the development window; any holdout anomalies are listed in section 3.",
        "",
        "## 19. Recommendation",
        "",
        *_recommendation(res),
        "",
        "## Appendix — frozen configuration (CONTROL; the variant differs only in `experiment.block_short_in_trend_down: true`)",
        "",
        "```yaml",
        m["config_yaml_control"].strip(),
        "```",
        "",
        "Pre-declared criteria text as frozen:",
        "",
        *[f"- {t}" for t in CRITERIA_TEXT],
        "",
    ]
    return "\n".join(lines)


def _recommendation(res: Phase3Result) -> list[str]:
    cls = res.classification
    failed = [str(cr_["id"]) for cr_ in res.criteria if not cr_["met"]]
    if cls.startswith("A"):
        return [
            f"- **{cls}.** All seven pre-declared criteria are met on untouched 2025+ data. The next step is owner-approved PAPER forward testing of the approved variant (`block_short_in_trend_down: true`) with the frozen configuration: no live trading, no exchange keys, no real money, no parameter change. The frozen default in `config/btc_swing.default.yaml` is not changed by this report; adopting the switch as the default is the owner's decision.",
            "- Paper forward testing must be pre-registered the same way (fixed config hash, fixed start date, no changes while it runs).",
        ]
    if cls.startswith("B"):
        return [
            f"- **{cls}.** The approved variant is positive on the holdout but fails pre-declared criterion/criteria {', '.join(failed)} (section 16). It is not confirmed and must not be adopted on this evidence.",
            "- No parameter tweak, alternative regime, family change or re-run is proposed. The owner decides whether to (a) stop, (b) extend the untouched holdout as more archive months are published and re-evaluate the SAME frozen variant against the SAME criteria, or (c) run paper forward testing of the frozen variant purely as observation, with no adoption.",
        ]
    return [
        f"- **{cls}.** The approved variant fails pre-declared criterion/criteria {', '.join(failed)} on untouched 2025+ data (section 16).",
        "- No parameter tweak, alternative filter, regime redesign or re-run is proposed. The Phase 2.4 effect did not transfer out of sample under the frozen rules; the honest conclusion is that the strategy as frozen has no demonstrated edge on this holdout. The owner decides whether the research track ends here.",
    ]
