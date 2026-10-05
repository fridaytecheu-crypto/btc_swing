"""Render reports/BTC_SWING_V1_PHASE2_4_SHORT_REGIME.md (18 sections + one classification)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import polars as pl

from btc_swing.research.phase2_report import _summary_rows
from btc_swing.research.phase21_report import (
    _arm_rows,
    _compare_block,
    _fam_compare,
    _ms,
    _n,
    _null_rows,
    _p,
    _se,
    _side_cell,
    _table,
)
from btc_swing.research.phase22_report import _row_check_text
from btc_swing.research.phase24 import VARIANT, Phase24Result

SUMMARY_HEADERS = [
    "cell",
    "n",
    "win rate",
    "exp. R",
    "exp. acct",
    "PF",
    "median R",
    "mean hold h",
    "reliable",
]
STAT_HEADERS = [
    "group",
    "n",
    "win",
    "mean R",
    "median R",
    "sum R",
    "PF",
    "MFE R",
    "MAE R",
    "mean hold h",
    "target first",
    "sum P&L",
]


def _stat_row(label: str, s: dict[str, Any]) -> list[str]:
    if not s or not s.get("n"):
        return [label, "0", *[""] * 10]
    return [
        label,
        str(s["n"]),
        _p(s["win_rate"], 0),
        _n(s["mean_R"]),
        _n(s["median_R"]),
        _n(s["sum_R"], 1),
        _n(s["profit_factor"], 2),
        _n(s["mean_MFE_R"], 2),
        _n(s["mean_MAE_R"], 2),
        _n(s["mean_holding_hours"], 1),
        _p(s["target_first_share"], 0),
        _n(s["sum_pnl"], 0),
    ]


def _by_rows(rows: list[dict[str, Any]], key: str) -> list[list[str]]:
    return [_stat_row(str(d[key]), d) for d in rows]


def render_phase24(res: Phase24Result) -> str:
    c, v = res.control, res.variant
    m = res.manifest
    seg_names = [s.name for s in res.segments]
    rm, oc = res.removed, res.removed.get("opportunity_cost", {})
    rem, lg, sq = res.remaining, res.longs, res.sequencing
    lines: list[str] = [
        "# BTC Swing V1 — Phase 2.4: SHORT REGIME ELIGIBILITY (CONTROL vs NO_NEW_SHORT_IN_TREND_DOWN)",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · period {_ms(m['period_start_ms'])} -> {_ms(m['period_end_ms'])} UTC · "
        f"CONTROL result hash `{c.result.result_hash[:12]}` · variant result hash `{v.result.result_hash[:12]}` · code `{c.result.manifest['code_version']}`",
        "",
        "**Central question.** Does preventing new SHORT entries once BTC is already in a TREND_DOWN regime remove structurally late short entries and produce a more stable out-of-sample swing strategy?",
        "",
        "Paper/backtest only. No live trading, no authenticated exchange access, no real money. 2025+ data untouched and not inspected.",
        "",
        "## 1. Hypothesis",
        "",
        "Phase 2 (frozen defaults) showed LONG expectancy positive in both chronological segments, SHORT expectancy deteriorating materially in 2024 (+0.10R -> -0.26R), trades entered in the TREND_DOWN regime at -0.23R, BREAKDOWN_SHORT (which originates from RANGE / BREAKOUT_REGIME / LOW_VOLATILITY) the only SHORT family positive in both segments, and TREND_PULLBACK_SHORT negative in both. "
        "Pre-registered hypothesis H4: opening a new SHORT after the market is already classified TREND_DOWN is systematically too late; preventing new SHORT entries in TREND_DOWN (uniformly, all SHORT families, entry eligibility only) improves combined and 2024 net expectancy, SHORT expectancy and profit factor without materially worsening drawdown, through a coherent removed population rather than a few outliers, and without damaging LONG performance.",
        "",
        "## 2. Baseline verification",
        "",
        *_table(
            ["check", "value"],
            [
                [
                    "Phase 2 validation result hash",
                    f"`{res.baseline_check['expected_phase2_result_hash']}`",
                ],
                [
                    "CONTROL result hash (this run)",
                    f"`{res.baseline_check['control_result_hash']}`",
                ],
                ["identical", _n(res.baseline_check["identical"])],
                [
                    "row-level check vs persisted Phase 2 trades",
                    _row_check_text(res.baseline_check.get("row_check_vs_phase2_trades", {})),
                ],
                [
                    "CONTROL trades / net expectancy",
                    f"{res.baseline_check['control_trades']} / {_n(res.baseline_check['control_expectancy_R'])}R",
                ],
                [
                    "config hash CONTROL / variant",
                    f"`{res.baseline_check['control_config_hash'][:12]}` / `{res.baseline_check['variant_config_hash'][:12]}` (differ only in `experiment.block_short_in_trend_down`)",
                ],
            ],
        ),
        "## 3. Exact variant",
        "",
        "- `experiment.block_short_in_trend_down: true`. At the decision bar where a SHORT episode's entry trigger fires, the engine reads the PIT regime of that bar (completed 1d/4h bars only). If it is TREND_DOWN the trade is not opened and the episode ends `REGIME_BLOCKED:TREND_DOWN` (cooldown as for an invalidation). Otherwise nothing differs from CONTROL.",
        "- Uniform across TREND_PULLBACK_SHORT, RESISTANCE_REJECTION_SHORT, MOMENTUM_CONTINUATION_SHORT and BREAKDOWN_SHORT; family definitions and eligibility tables unchanged; LONG logic untouched; open positions never closed by a regime change; detection, confirmation, entry geometry, stops, TP1/TP2, breakeven, trailing, sizing, leverage, costs and max hold frozen; all other experiment switches at their CONTROL defaults.",
        "- Because TREND_PULLBACK_SHORT is only eligible in TREND_DOWN, the rule removes essentially all of its entries; that is a consequence of the uniform regime rule, not a family-selection choice, and the family was not modified.",
        "",
        "## 4. PIT audit",
        "",
        f"- {res.pit['visibility_rule']}. Scope: {res.pit['rule_scope']}.",
        f"- Deterministic rerun: CONTROL {_n(res.pit['control_deterministic'])}, variant {_n(res.pit['variant_deterministic'])}.",
        f"- Truncation audit (variant): decisions up to {res.pit['variant_truncation']['cut'][:16]} identical with later data removed: {_n(res.pit['variant_truncation']['identical'])} ({res.pit['variant_truncation']['rows_compared']} rows).",
        "",
    ]
    cr = _arm_rows("CONTROL", c.metrics, c.account, c.costs, c.result.manifest["n_episodes"])
    vr = _arm_rows(VARIANT, v.metrics, v.account, v.costs, v.result.manifest["n_episodes"])
    co, vo = c.metrics["overall"], v.metrics["overall"]
    lines += [
        "## 5. Overall results (combined 2022-01 -> 2024-12)",
        "",
        *_compare_block(cr, vr),
        *_table(
            ["metric", "CONTROL", VARIANT],
            [
                [
                    "LONG trades / SHORT trades",
                    f"{c.metrics['by_side'].get('LONG', {}).get('n', 0)} / {c.metrics['by_side'].get('SHORT', {}).get('n', 0)}",
                    f"{v.metrics['by_side'].get('LONG', {}).get('n', 0)} / {v.metrics['by_side'].get('SHORT', {}).get('n', 0)}",
                ],
                [
                    "Sortino (daily marks)",
                    _n(c.account.get("sortino_daily_annualised"), 2),
                    _n(v.account.get("sortino_daily_annualised"), 2),
                ],
                ["t-stat of mean R", _n(co.get("t_stat_R"), 2), _n(vo.get("t_stat_R"), 2)],
                [
                    "episodes REGIME_BLOCKED",
                    "0",
                    str(
                        v.episodes.filter(pl.col("outcome_class") == "REGIME_BLOCKED").height
                        if v.episodes.height
                        else 0
                    ),
                ],
            ],
        ),
    ]
    for i, sn in enumerate(seg_names):
        cs, vs = c.segments[sn], v.segments[sn]
        n_ep_c = sum(d["len"] for d in cs["episode_outcomes"]) or 0
        n_ep_v = sum(d["len"] for d in vs["episode_outcomes"]) or 0
        lines += [
            f"## {6 + i}. {sn}",
            "",
            *_compare_block(
                _arm_rows("CONTROL", cs["metrics"], cs["account"], cs["costs"], n_ep_c),
                _arm_rows(VARIANT, vs["metrics"], vs["account"], vs["costs"], n_ep_v),
            ),
        ]
        rows = [
            [
                side,
                _side_cell(cs["metrics"]["by_side"].get(side, {})),
                _side_cell(vs["metrics"]["by_side"].get(side, {})),
            ]
            for side in ("LONG", "SHORT")
        ]
        lines += [f"Sides in {sn}:", "", *_table(["side", "CONTROL", VARIANT], rows)]
    rows = [
        [
            side,
            _side_cell(c.metrics["by_side"].get(side, {})),
            _side_cell(v.metrics["by_side"].get(side, {})),
        ]
        for side in ("LONG", "SHORT")
    ]
    lines += ["## 8. LONG vs SHORT (combined)", "", *_table(["side", "CONTROL", VARIANT], rows)]
    # 9 removed trades
    lines += [
        "## 9. Removed trades (CONTROL SHORT trades whose episode ends REGIME_BLOCKED in the variant)",
        "",
        f"- CONTROL SHORT trades: {rm['control_short_trades']}; removed by the rule: {rm['removed_by_rule']}; kept (traded in both arms): {rm['kept_in_variant']}; lost to sequencing or other causes: {rm['short_lost_to_sequencing_or_other']}"
        + (
            " ("
            + ", ".join(
                f"{d['variant_outcome']}={d['len']}" for d in rm["unmatched_short_outcomes"]
            )
            + ")"
            if rm["unmatched_short_outcomes"]
            else ""
        )
        + ".",
        "- Regime at the trigger bar of the removed trades: "
        + ", ".join(f"{d['regime_at_trigger']}={d['len']}" for d in rm["removed_regime_at_trigger"])
        + ".",
        "",
        *_table(
            STAT_HEADERS,
            [
                _stat_row("removed SHORT trades", rm["removed_stats"]),
                _stat_row("kept SHORT trades (traded in both arms)", rm["kept_stats"]),
            ],
        ),
        "By family:",
        "",
        *_table(STAT_HEADERS, _by_rows(rm["removed_by_family"], "family")),
        "By segment:",
        "",
        *_table(STAT_HEADERS, _by_rows(rm["removed_by_segment"], "segment")),
        "By regime at detection:",
        "",
        *_table(
            STAT_HEADERS, _by_rows(rm["removed_by_regime_at_detection"], "regime_at_detection")
        ),
        "By family and segment:",
        "",
        *_table(
            ["family", "segment", "n", "mean R", "sum R", "sum P&L"],
            [
                [
                    d["family"],
                    d["segment"],
                    str(d["n"]),
                    _n(d["mean_R"]),
                    _n(d["sum_R"], 1),
                    _n(d["sum_pnl"], 0),
                ]
                for d in rm["removed_by_family_segment"]
            ],
        ),
    ]
    rr = rm.get("removed_rows")
    if isinstance(rr, pl.DataFrame) and rr.height:
        lines += [
            f"Every removed trade ({rr.height}; also in `removed_short_trades.parquet`):",
            "",
            *_table(
                [
                    "family",
                    "entry (UTC)",
                    "segment",
                    "regime det./trigger",
                    "R",
                    "P&L",
                    "MFE R",
                    "MAE R",
                    "hold h",
                    "exit",
                    "target first",
                ],
                [
                    [
                        r["family"].replace("_", " "),
                        _ms(r["entry_ms"]),
                        r["segment"],
                        f"{r['regime_at_detection']}/{r['regime_at_trigger']}",
                        _n(r["R_MULTIPLE"], 2),
                        _n(r["POSITION_PNL"], 0),
                        _n(r["MFE_R"], 2),
                        _n(r["MAE_R"], 2),
                        _n(r["holding_hours"], 1),
                        r["exit_reason"],
                        ("yes" if r.get("path_outcome") == "target_first" else "no"),
                    ]
                    for r in rr.to_dicts()
                ],
            ),
        ]
    # 10 opportunity cost
    lines += ["## 10. Winners sacrificed vs losses avoided", ""]
    if oc:
        lines += _table(
            ["metric", "value"],
            [
                [
                    "winners removed / losers removed",
                    f"{oc['winners_removed']} / {oc['losers_removed']}",
                ],
                ["positive R removed", _n(oc["positive_R_removed"], 1)],
                ["negative R avoided", _n(oc["negative_R_avoided"], 1)],
                [
                    "net R effect of removal (avoided - removed)",
                    _n(oc["net_R_effect_of_removal"], 1),
                ],
                [
                    "winners P&L removed / losers P&L avoided (USDT)",
                    f"{_n(oc['winners_pnl_removed'], 0)} / {_n(oc['losers_pnl_avoided'], 0)}",
                ],
                ["net P&L effect of removal (USDT)", _n(oc["net_pnl_effect_of_removal"], 0)],
                ["median removed R", _n(oc["median_removed_R"])],
                [
                    "removed R quantiles q10/q25/q50/q75/q90",
                    "/".join(
                        _n(oc["removed_R_quantiles"][q], 2) for q in (0.1, 0.25, 0.5, 0.75, 0.9)
                    ),
                ],
                [
                    "share of avoided negative R from the worst 3 trades",
                    _p(oc["worst3_share_of_negative_R"], 0),
                ],
                [
                    "share of removed positive R from the best 3 trades",
                    _p(oc["best3_share_of_positive_R"], 0),
                ],
                ["mean removed R excluding the worst 3", _n(oc["removed_without_worst3_mean_R"])],
            ],
        )
    # 11 remaining short
    lines += [
        "## 11. Remaining SHORT population",
        "",
        *_table(
            STAT_HEADERS,
            [
                _stat_row("CONTROL all SHORT", rem["control"]),
                _stat_row(f"{VARIANT} remaining SHORT", rem["variant"]),
            ],
        ),
    ]
    if rem.get("variant_dd_window"):
        dw = rem["variant_dd_window"]
        lines += [
            f"- Variant max-drawdown window ({dw['n_trades']} trades, {_n(dw['max_dd_currency'], 0)} USDT): SHORT contribution {_n(dw['short_pnl_in_window'], 0)} USDT, LONG contribution {_n(dw['long_pnl_in_window'], 0)} USDT.",
            "",
        ]
    lines += [
        "Remaining SHORT by regime at trigger:",
        "",
        *_table(STAT_HEADERS, _by_rows(rem["variant_by_regime_at_trigger"], "regime_at_trigger")),
    ]
    # 12 family breakdown
    lines += [
        "## 12. Setup-family breakdown",
        "",
        *_fam_compare(c.families, v.families, "All families (combined)")[2:],
        "SHORT families, CONTROL vs variant:",
        "",
        *_table(STAT_HEADERS, [*_by_rows(rem["control_by_family"], "family")]),
    ]
    lines += ["(variant)", "", *_table(STAT_HEADERS, _by_rows(rem["variant_by_family"], "family"))]
    for sn in seg_names:
        lines += _fam_compare(
            c.segments[sn]["families"], v.segments[sn]["families"], f"Families in {sn}"
        )
    # 13 sequencing
    lines += [
        "## 13. Single-slot sequencing effects (LONG control check and effect decomposition)",
        "",
        *_table(
            ["metric", "value"],
            [
                ["LONG trades CONTROL / variant", f"{lg['control_long']} / {lg['variant_long']}"],
                [
                    "LONG trades paired on identical detection: identical R / different R",
                    f"{lg['paired_identical_R']} / {lg['paired_different_R']}",
                ],
                [
                    "LONG trades only in CONTROL (slot taken by a short in the variant, or shifted)",
                    f"{lg['control_only_long']} (P&L {_n(lg['control_only_long_pnl'], 0)} USDT, mean R {_n(lg['control_only_long_mean_R'])})",
                ],
                [
                    "LONG trades only in the variant (freed slots)",
                    f"{lg['variant_only_long']} (P&L {_n(lg['variant_only_long_pnl'], 0)} USDT, mean R {_n(lg['variant_only_long_mean_R'])})",
                ],
                [
                    "LONG expectancy CONTROL / variant",
                    f"{_n(lg['control_long_stats'].get('mean_R'))} / {_n(lg['variant_long_stats'].get('mean_R'))}",
                ],
                [
                    "LONG profit factor CONTROL / variant",
                    f"{_n(lg['control_long_stats'].get('profit_factor'), 2)} / {_n(lg['variant_long_stats'].get('profit_factor'), 2)}",
                ],
            ],
        ),
        "Decomposition of the net P&L change:",
        "",
        *_table(
            ["component", "USDT"],
            [
                ["total net P&L change (variant - CONTROL)", _n(sq["total_net_pnl_change"], 0)],
                [
                    "direct effect: removed TREND_DOWN SHORT trades (P&L they had in CONTROL, sign reversed)",
                    _n(sq["direct_effect_removed_trades"], 0),
                ],
                ["secondary effect: freed slots / shifted sequence", _n(sq["secondary_effect"], 0)],
                [
                    "variant-only trades (new, enabled by freed slots)",
                    f"n={sq['variant_only_trades']['n']}, P&L {_n(sq['variant_only_trades']['pnl'], 0)}; "
                    + ", ".join(
                        f"{d['side']}: n={d['n']}, {_n(d['pnl'], 0)} USDT, {_n(d['mean_R'])}R"
                        for d in sq["variant_only_trades"]["by_side"]
                    ),
                ],
                [
                    "CONTROL-only trades not blocked by the rule (lost to sequencing)",
                    f"n={sq['control_only_trades_not_blocked']['n']}, P&L {_n(sq['control_only_trades_not_blocked']['pnl'], 0)}; "
                    + ", ".join(
                        f"{d['side']}: n={d['n']}, {_n(d['pnl'], 0)} USDT, {_n(d['mean_R'])}R"
                        for d in sq["control_only_trades_not_blocked"]["by_side"]
                    ),
                ],
            ],
        ),
    ]
    # 14 MFE/MAE
    cte, vte = c.metrics.get("target_evaluation", {}), v.metrics.get("target_evaluation", {})
    lines += [
        "## 14. MFE / MAE",
        "",
        *_table(
            ["metric", "CONTROL", VARIANT],
            [
                [
                    "mean MFE (R) / mean MAE (R)",
                    f"{_n(co.get('mean_MFE_R'))} / {_n(co.get('mean_MAE_R'))}",
                    f"{_n(vo.get('mean_MFE_R'))} / {_n(vo.get('mean_MAE_R'))}",
                ],
                ["worst MAE (R)", _n(co.get("worst_MAE_R")), _n(vo.get("worst_MAE_R"))],
                ["realised mean winner (R)", _n(co.get("avg_win_R")), _n(vo.get("avg_win_R"))],
                *[
                    [
                        f"counterfactual {k.replace('hit_rate_', '')} before initial stop",
                        _p(cte.get(k), 1),
                        _p(vte.get(k), 1),
                    ]
                    for k in cte
                    if k.startswith("hit_rate_")
                ],
            ],
        ),
    ]
    # 15 costs
    cst, vst = c.costs, v.costs
    lines += [
        "## 15. Costs (USDT, combined)",
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
                    "gross expectancy (R) / net expectancy (R)",
                    f"{_n(cst.get('expectancy_R_before_costs'))} / {_n(cst.get('expectancy_R_net'))}",
                    f"{_n(vst.get('expectancy_R_before_costs'))} / {_n(vst.get('expectancy_R_net'))}",
                ],
                [
                    "cost drag per trade (R)",
                    _n(cst.get("cost_drag_R_per_trade")),
                    _n(vst.get("cost_drag_R_per_trade")),
                ],
            ],
        ),
        "Fewer trades is not counted as an improvement by itself; the per-trade expectancy and profit factor above carry the comparison.",
        "",
    ]
    # 16 drawdown
    ca, va = c.account, v.account
    lines += [
        "## 16. Drawdown / equity (sequential, one position, fixed research equity for sizing)",
        "",
        *_table(
            ["metric", "CONTROL", VARIANT],
            [
                ["final equity", _n(ca.get("final_equity"), 2), _n(va.get("final_equity"), 2)],
                [
                    "total return / CAGR",
                    f"{_p(ca.get('total_return'))} / {_p(ca.get('cagr'))}",
                    f"{_p(va.get('total_return'))} / {_p(va.get('cagr'))}",
                ],
                [
                    "max drawdown (trade curve / daily mtm)",
                    f"{_p(ca.get('max_drawdown_frac_trade_curve'))} / {_p(ca.get('max_drawdown_frac_daily_mtm'))}",
                    f"{_p(va.get('max_drawdown_frac_trade_curve'))} / {_p(va.get('max_drawdown_frac_daily_mtm'))}",
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
                    "trades / week",
                    _n(ca.get("trades_per_week"), 2),
                    _n(va.get("trades_per_week"), 2),
                ],
            ],
        ),
        "Null benchmark (geometry-matched, as in Phase 2):",
        "",
        *_table(
            ["series", "n", "mean R", "win rate", "strategy - null (R)", "z", "P(null >= strat)"],
            [*_null_rows("CONTROL", c.null), *_null_rows(VARIANT, v.null)],
        ),
    ]
    lines += [
        "Regime at entry — CONTROL:",
        "",
        *_table(
            SUMMARY_HEADERS,
            _summary_rows(c.metrics["by_regime"], int(c.metrics.get("min_cell_n", 20))),
        ),
        f"Regime at entry — {VARIANT}:",
        "",
        *_table(
            SUMMARY_HEADERS,
            _summary_rows(v.metrics["by_regime"], int(v.metrics.get("min_cell_n", 20))),
        ),
    ]
    # 17 uncertainty
    cs24, vs24 = (
        c.segments.get("val_2024", {}).get("metrics", {}).get("overall", {}),
        v.segments.get("val_2024", {}).get("metrics", {}).get("overall", {}),
    )
    lines += [
        "## 17. Uncertainty",
        "",
        f"- Standard error of mean R: CONTROL {_n(_se(co))} (n={co.get('n', 0)}), variant {_n(_se(vo))} (n={vo.get('n', 0)}); 95% intervals CONTROL [{_n(co.get('expectancy_R', 0) - 1.96 * _se(co))}, {_n(co.get('expectancy_R', 0) + 1.96 * _se(co))}], variant [{_n(vo.get('expectancy_R', 0) - 1.96 * _se(vo))}, {_n(vo.get('expectancy_R', 0) + 1.96 * _se(vo))}]. Both intervals include zero.",
        f"- The variant differs from CONTROL by removing {rm['removed_by_rule']} trades (mean {_n(rm['removed_stats'].get('mean_R'))}R, t = {_n(rm['removed_stats'].get('t_stat'), 2)}) plus sequencing effects; the removed population's own t-statistic is the direct evidence that the regime rule targets a negative-expectancy group.",
        f"- 2024: CONTROL {_n(cs24.get('expectancy_R'))}R (n={cs24.get('n', 0)}) vs variant {_n(vs24.get('expectancy_R'))}R (n={vs24.get('n', 0)}); SHORT trades in 2024 fall to n={v.segments.get('val_2024', {}).get('metrics', {}).get('by_side', {}).get('SHORT', {}).get('n', 0)}, so the remaining 2024 SHORT estimate is weak.",
        "- The hypothesis was motivated by the Phase 2 regime table on the same window; this run therefore confirms the in-sample observation under a pre-declared rule but is NOT out-of-sample evidence. Only an untouched 2025+ run can be.",
        "- Single instrument, single 3-year window, one regime classifier with frozen thresholds; the regime label itself is a model output.",
    ]
    # 18 recommendation
    lines += ["", "## 18. Final recommendation", ""]
    lines += _recommendation(res)
    lines += [
        "",
        "## Appendix — frozen configuration (CONTROL; the variant differs only in `experiment.block_short_in_trend_down`)",
        "",
        "```yaml",
        m["config_yaml"].strip(),
        "```",
        "",
    ]
    return "\n".join(lines)


def _recommendation(res: Phase24Result) -> list[str]:
    c, v = res.control, res.variant
    co, vo = c.metrics["overall"], v.metrics["overall"]
    oc = res.removed.get("opportunity_cost", {})
    lg, sq = res.longs, res.sequencing
    net_c, net_v = co.get("expectancy_R", 0.0) or 0.0, vo.get("expectancy_R", 0.0) or 0.0
    c24 = (
        c.segments.get("val_2024", {})
        .get("metrics", {})
        .get("overall", {})
        .get("expectancy_R", 0.0)
        or 0.0
    )
    v24 = (
        v.segments.get("val_2024", {})
        .get("metrics", {})
        .get("overall", {})
        .get("expectancy_R", 0.0)
        or 0.0
    )
    sc = c.metrics["by_side"].get("SHORT", {}).get("expectancy_R", 0.0) or 0.0
    sv = v.metrics["by_side"].get("SHORT", {}).get("expectancy_R", 0.0) or 0.0
    dd_c = abs(c.account.get("max_drawdown_frac_trade_curve", 0.0) or 0.0)
    dd_v = abs(v.account.get("max_drawdown_frac_trade_curve", 0.0) or 0.0)
    rm = res.removed.get("removed_stats", {})
    coherent = (
        bool(oc)
        and rm.get("n", 0) >= 20
        and (oc.get("losers_removed", 0) / max(rm.get("n", 1), 1)) >= 0.55
        and (oc.get("worst3_share_of_negative_R") or 1.0) < 0.35
        and (oc.get("removed_without_worst3_mean_R") or 0.0) < 0
    )
    lc, lv = (
        lg["control_long_stats"].get("mean_R", 0.0) or 0.0,
        lg["variant_long_stats"].get("mean_R", 0.0) or 0.0,
    )
    criteria = [
        (
            "1. combined net expectancy improves materially (> +0.05R over CONTROL)",
            net_v > net_c + 0.05,
            f"{_n(net_c)} -> {_n(net_v)}",
        ),
        (
            "2. 2024 expectancy improves materially (> +0.05R over CONTROL 2024)",
            v24 > c24 + 0.05,
            f"{_n(c24)} -> {_n(v24)}",
        ),
        ("3. SHORT expectancy improves", sv > sc, f"{_n(sc)} -> {_n(sv)}"),
        (
            "4. profit factor improves",
            (vo.get("profit_factor") or 0) > (co.get("profit_factor") or 0),
            f"{_n(co.get('profit_factor'), 2)} -> {_n(vo.get('profit_factor'), 2)}",
        ),
        (
            "5. drawdown does not materially worsen (<= 1.25x CONTROL + 1pt)",
            dd_v <= dd_c * 1.25 + 0.01,
            f"{_p(dd_c)} -> {_p(dd_v)}",
        ),
        (
            "6. coherent removed population (n >= 20, >= 55% losers, worst-3 < 35% of avoided R, still negative without the worst 3)",
            coherent,
            f"n={rm.get('n', 0)}, losers {oc.get('losers_removed', 0)}, worst-3 share {_p(oc.get('worst3_share_of_negative_R'), 0)}, mean R without worst 3 {_n(oc.get('removed_without_worst3_mean_R'))}",
        ),
        (
            "7. LONG not materially damaged by sequencing (LONG expectancy change >= -0.05R, identical R on paired LONGs)",
            lv >= lc - 0.05 and lg["paired_different_R"] == 0,
            f"LONG {_n(lc)} -> {_n(lv)}; paired LONGs with different R: {lg['paired_different_R']}",
        ),
    ]
    lines = [
        "Pre-declared criteria (from the hypothesis; thresholds stated so the reader can disagree):",
        "",
        *_table(
            ["criterion", "met", "evidence"],
            [[k, "yes" if ok else "no", ev] for k, ok, ev in criteria],
        ),
    ]
    n_met = sum(1 for _, ok, _ in criteria if ok)
    if n_met == len(criteria):
        cls, why = (
            "A — ADOPTABLE FOR UNTOUCHED VALIDATION",
            "all seven pre-declared criteria are met; the removed population is a coherent negative-expectancy group, the gain survives costs and sequencing, and LONG is unaffected. The only legitimate next step is one owner-approved confirmatory run on the untouched 2025+ window with `block_short_in_trend_down: true` and nothing else changed. A single run, decided in advance, no iteration afterwards.",
        )
    elif net_v > net_c and n_met >= 5:
        cls, why = (
            "B — INTERESTING BUT INSUFFICIENT",
            "the variant improves the baseline but fails at least one pre-declared criterion (see table). It must not be adopted on this evidence and must not be refined by trying other regimes or combinations; the owner decides whether the failed criterion is disqualifying.",
        )
    else:
        cls, why = (
            "C — NOT SUPPORTED",
            "the regime rule does not improve the frozen baseline on this window in the pre-declared sense.",
        )
    lines += ["", f"- **Classification: {cls}.** {why}"]
    if oc:
        lines.append(
            f"- Removed population: {rm.get('n', 0)} TREND_DOWN SHORT entries, mean {_n(rm.get('mean_R'))}R (median {_n(rm.get('median_R'))}R, t = {_n(rm.get('t_stat'), 2)}): {oc['winners_removed']} winners worth {_n(oc['positive_R_removed'], 1)}R sacrificed, {oc['losers_removed']} losers worth {_n(oc['negative_R_avoided'], 1)}R avoided, net {_n(oc['net_R_effect_of_removal'], 1)}R / {_n(oc['net_pnl_effect_of_removal'], 0)} USDT."
        )
    lines.append(
        f"- Decomposition of the net P&L change ({_n(sq['total_net_pnl_change'], 0)} USDT): direct effect of the removed trades {_n(sq['direct_effect_removed_trades'], 0)} USDT, secondary sequencing effect {_n(sq['secondary_effect'], 0)} USDT ({sq['variant_only_trades']['n']} new trades from freed slots, {sq['control_only_trades_not_blocked']['n']} CONTROL trades lost to shifted sequencing)."
    )
    lines.append(
        "- Caveat that applies regardless of the classification: this hypothesis was derived from the Phase 2 regime table on the same 2022-2024 window, so it is a confirmation of an in-sample pattern under a pre-declared rule, not out-of-sample evidence. 2025+ stays untouched until the owner pre-registers the confirmatory run. No live trading. No tuning. The frozen default remains `block_short_in_trend_down: false`."
    )
    return lines
