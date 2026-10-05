"""Render reports/BTC_SWING_V1_PHASE2_3_POST_TP1_EXIT.md."""

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
from btc_swing.research.phase23 import VARIANT, Phase23Result

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


def _capture_rows(cc: dict[str, Any], vc: dict[str, Any]) -> list[list[str]]:
    return [
        ["TP1 frequency", _p(cc.get("tp1_frequency"), 1), _p(vc.get("tp1_frequency"), 1)],
        ["TP2 frequency", _p(cc.get("tp2_frequency"), 1), _p(vc.get("tp2_frequency"), 1)],
        [
            "stop-outs after TP1 (remainder closed below entry)",
            str(cc.get("stop_outs_after_tp1")),
            str(vc.get("stop_outs_after_tp1")),
        ],
        [
            "after-TP1 exits on: breakeven stop / initial stop / structural trail",
            f"{cc.get('breakeven_stops_after_tp1')} / {cc.get('initial_stop_after_tp1')} / {cc.get('trail_stops_after_tp1')}",
            f"{vc.get('breakeven_stops_after_tp1')} / {vc.get('initial_stop_after_tp1')} / {vc.get('trail_stops_after_tp1')}",
        ],
        [
            "mean realised R of TP1 trades",
            _n(cc.get("mean_realised_R_tp1")),
            _n(vc.get("mean_realised_R_tp1")),
        ],
        ["mean MFE R of TP1 trades", _n(cc.get("mean_MFE_R_tp1")), _n(vc.get("mean_MFE_R_tp1"))],
        [
            "MFE captured as realised R (TP1 trades, mean/mean)",
            _p(cc.get("capture_ratio_tp1"), 1),
            _p(vc.get("capture_ratio_tp1"), 1),
        ],
        [
            "median per-trade capture (TP1 trades)",
            _p(cc.get("median_per_trade_capture_tp1"), 1),
            _p(vc.get("median_per_trade_capture_tp1"), 1),
        ],
        [
            "mean realised winner (R) / mean MFE all trades",
            f"{_n(cc.get('mean_realised_winner_R'))} / {_n(cc.get('mean_MFE_R_all'))}",
            f"{_n(vc.get('mean_realised_winner_R'))} / {_n(vc.get('mean_MFE_R_all'))}",
        ],
        [
            "mean holding of TP1 trades (h)",
            _n(cc.get("mean_holding_hours_tp1"), 1),
            _n(vc.get("mean_holding_hours_tp1"), 1),
        ],
        [
            "funding paid on TP1 trades (USDT, negative = paid)",
            _n(cc.get("funding_tp1_sum"), 0),
            _n(vc.get("funding_tp1_sum"), 0),
        ],
    ]


def render_phase23(res: Phase23Result) -> str:
    c, v = res.control, res.variant
    m = res.manifest
    seg_names = [s.name for s in res.segments]
    pa = res.paired
    ts, be, dn = pa.get("tp1_summary", {}), pa.get("breakeven_stops", {}), pa.get("downside", {})
    cc, vc = res.capture["CONTROL"], res.capture[VARIANT]
    lines: list[str] = [
        "# BTC Swing V1 — Phase 2.3: POST-TP1 EXIT DESIGN (CONTROL vs STRUCTURAL_TRAIL_AFTER_TP1)",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · period {_ms(m['period_start_ms'])} -> {_ms(m['period_end_ms'])} UTC · "
        f"CONTROL result hash `{c.result.result_hash[:12]}` · variant result hash `{v.result.result_hash[:12]}` · code `{c.result.manifest['code_version']}`",
        "",
        "**Central question.** Does forcing breakeven immediately after TP1 prematurely truncate valid BTC swing winners, and can the existing structural trail capture more of their MFE without materially increasing downside?",
        "",
        "Paper/backtest only. No live trading, no authenticated exchange access, no real money. 2025+ data untouched.",
        "",
        "## 1. Hypothesis",
        "",
        "Phase 2 (frozen defaults): realised mean winner 1.42R, mean MFE 2.07R, TP1 at 1.5R closing 40%, then the stop moves to breakeven (`breakeven_after_tp1: true`) and the remainder trails the 1h swing structure minus 0.5 ATR. "
        "Pre-registered hypothesis H3: the forced breakeven move cuts valid winners before the structural trail has had time to work; removing ONLY that move (keeping the initial stop until the existing `structure_atr` trail moves it) improves realised expectancy materially (> +0.05R over +0.081R), improves 2024 rather than deteriorating it, raises the profit factor and MFE capture, without a disproportionate drawdown increase, and the extra holding/funding cost does not erase the gain.",
        "",
        "## 2. Frozen baseline verification",
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
                    "CONTROL trades / episodes",
                    f"{c.result.manifest['n_trades']} / {c.result.manifest['n_episodes']}",
                ],
                [
                    "config hash CONTROL / variant",
                    f"`{res.baseline_check['control_config_hash'][:12]}` / `{res.baseline_check['variant_config_hash'][:12]}` (differ only in `exits.breakeven_after_tp1`)",
                ],
            ],
        ),
        "## 3. Exact variant definition",
        "",
        "- CONTROL: `exits.breakeven_after_tp1: true` — at TP1 (1.5R, 40% closed) the stop is moved to the entry price from the next bar; the remainder then trails the 1h swing structure minus 0.5 ATR (ratchet only); TP2 at 3R closes 30%; 240 h cap.",
        "- STRUCTURAL_TRAIL_AFTER_TP1: `exits.breakeven_after_tp1: false` — identical, except that after TP1 the INITIAL stop stays in place until the existing structural trail moves it. No other stop rule, no other change. Setup discovery, entry, confirmation, regime eligibility, sizing, leverage, TP levels and fractions, trail parameters, max hold and costs are frozen.",
        "- Entries are therefore identical in both arms; only the exit path of trades that reach TP1 can differ (a longer variant trade can occupy the single slot and shift later detections, which is why the trade counts may differ slightly).",
        "",
        "## 4. PIT audit",
        "",
        f"- {res.pit['visibility_rule']}.",
        f"- Deterministic rerun: CONTROL {_n(res.pit['control_deterministic'])}, variant {_n(res.pit['variant_deterministic'])}.",
        f"- Truncation audit (variant): decisions up to {res.pit['variant_truncation']['cut'][:16]} identical with later data removed: {_n(res.pit['variant_truncation']['identical'])} ({res.pit['variant_truncation']['rows_compared']} rows).",
        f"- Resume labels: {res.pit['resume_label']}.",
        "",
    ]
    # 5 overall
    cr = _arm_rows("CONTROL", c.metrics, c.account, c.costs, c.result.manifest["n_episodes"])
    vr = _arm_rows(VARIANT, v.metrics, v.account, v.costs, v.result.manifest["n_episodes"])
    lines += [
        "## 5. Main comparison (combined 2022-01 -> 2024-12)",
        "",
        *_compare_block(cr, vr),
        "Exit-path and MFE capture:",
        "",
        *_table(["metric", "CONTROL", VARIANT], _capture_rows(cc, vc)),
    ]
    # 6 segments
    lines += ["## 6. Segment stability", ""]
    for sn in seg_names:
        cs, vs = c.segments[sn], v.segments[sn]
        n_ep_c = sum(d["len"] for d in cs["episode_outcomes"]) or 0
        n_ep_v = sum(d["len"] for d in vs["episode_outcomes"]) or 0
        lines += [
            f"### {sn}",
            "",
            *_compare_block(
                _arm_rows("CONTROL", cs["metrics"], cs["account"], cs["costs"], n_ep_c),
                _arm_rows(VARIANT, vs["metrics"], vs["account"], vs["costs"], n_ep_v),
            ),
        ]
    # 7 matched trades
    lines += [
        "## 7. Matched-trade analysis (identical entries; trades that reached TP1 in both arms)",
        "",
        f"- Paired trades: {pa.get('paired', 0)} (identical entry time in {pa.get('paired_identical_entry', 0)}); TP1 reached in both arms: {pa.get('tp1_pairs', 0)}; TP1 in one arm only: {pa.get('tp1_only_one_arm', 0)}. "
        f"Across all paired trades, realised R differs in {pa.get('all_pairs', {}).get('n_different_R', 0)} trades (sum P&L CONTROL {_n(pa.get('all_pairs', {}).get('control_sum_pnl'), 0)} vs variant {_n(pa.get('all_pairs', {}).get('variant_sum_pnl'), 0)} USDT).",
        "",
    ]
    if ts:
        lines += [
            *_table(
                ["metric", "CONTROL", VARIANT],
                [
                    ["TP1 pairs", str(ts["n"]), str(ts["n"])],
                    ["mean realised R", _n(ts["control_mean_R"]), _n(ts["variant_mean_R"])],
                    ["sum net P&L", _n(ts["control_sum_pnl"], 0), _n(ts["variant_sum_pnl"], 0)],
                    [
                        "mean MFE after TP1 (R from entry)",
                        _n(ts["control_mean_mfe_after_tp1_R"]),
                        _n(ts["variant_mean_mfe_after_tp1_R"]),
                    ],
                    [
                        "mean MAE after TP1 (R from entry)",
                        _n(ts["control_mean_mae_after_tp1_R"]),
                        _n(ts["variant_mean_mae_after_tp1_R"]),
                    ],
                    ["TP2 reached", str(ts["control_tp2"]), str(ts["variant_tp2"])],
                    [
                        "mean holding (h)",
                        _n(ts["control_mean_hold_h"], 1),
                        _n(ts["variant_mean_hold_h"], 1),
                    ],
                    [
                        "funding on these trades (USDT)",
                        _n(ts["control_funding_sum"], 0),
                        _n(ts["variant_funding_sum"], 0),
                    ],
                    [
                        "MFE captured (mean realised R / mean full-path MFE)",
                        _p(ts["mfe_capture_control"], 1),
                        _p(ts["mfe_capture_variant"], 1),
                    ],
                ],
            ),
            f"- Paired difference (variant - CONTROL): mean {_n(ts['paired_mean_diff_R'])}R, paired t = {_n(ts['paired_t'], 2)}; variant better in {_p(ts['frac_variant_better'], 0)}, worse in {_p(ts['frac_variant_worse'], 0)}, identical in {_p(ts['frac_identical'], 0)} of pairs.",
            "- CONTROL exit mix after TP1: "
            + ", ".join(
                f"{d['control_exit']}/{d['control_stop_source']}={d['len']}"
                for d in ts["control_exit_mix"]
            )
            + ".",
            "- Variant exit mix after TP1: "
            + ", ".join(
                f"{d['variant_exit']}/{d['variant_stop_source']}={d['len']}"
                for d in ts["variant_exit_mix"]
            )
            + ".",
            "",
        ]
        pairs = pa.get("pairs")
        if isinstance(pairs, pl.DataFrame) and pairs.height:
            diff = pairs.filter(pl.col("diff_R").abs() > 1e-9).sort("diff_R", descending=True)
            lines += [
                f"Pairs where the exit path differs ({diff.height}; full table in `tp1_pairs.parquet`), largest gains first then largest losses:",
                "",
            ]
            rows = []
            for r in [*diff.head(15).to_dicts(), *diff.tail(10).to_dicts()]:
                rows.append(
                    [
                        r["family"].replace("_", " "),
                        r["side"],
                        _ms(r["entry_ms"]),
                        _n(r["hours_to_tp1"], 1),
                        f"{r['control_exit']}/{r['control_stop_source']}",
                        f"{r['variant_exit']}/{r['variant_stop_source']}",
                        f"{_n(r['control_mfe_after_tp1_R'], 2)}/{_n(r['variant_mfe_after_tp1_R'], 2)}",
                        f"{_n(r['control_mae_after_tp1_R'], 2)}/{_n(r['variant_mae_after_tp1_R'], 2)}",
                        "yes" if r["control_stopped_at_breakeven"] else "no",
                        ("2R" if r["resumed_2R"] else "") + ("+3R" if r["resumed_3R"] else "")
                        or ("stop" if r["stopped_first"] else "no"),
                        f"{_n(r['control_R'], 2)}/{_n(r['variant_R'], 2)}",
                    ]
                )
            lines += _table(
                [
                    "family",
                    "side",
                    "entry",
                    "h to TP1",
                    "CONTROL exit",
                    "variant exit",
                    "MFE after TP1 C/V",
                    "MAE after TP1 C/V",
                    "BE stop",
                    "resumed",
                    "R C/V",
                ],
                rows,
            )
    # 8 breakeven then resume
    lines += ["## 8. CONTROL trades stopped at breakeven after TP1: did price resume?", ""]
    if be:
        lines += [
            *_table(
                ["metric", "value"],
                [
                    ["CONTROL trades stopped at breakeven after TP1", str(be["n"])],
                    [
                        "... that later reached 2R (before the initial stop, within the max-hold horizon)",
                        str(be["resumed_2R"]),
                    ],
                    ["... that later reached 3R", str(be["resumed_3R"])],
                    [
                        "... that hit the initial stop first (breakeven protected a loss)",
                        str(be["stopped_first"]),
                    ],
                    ["... neither within the horizon", str(be["neither"])],
                    [
                        "mean realised R: CONTROL / variant on these trades",
                        f"{_n(be['control_mean_R'])} / {_n(be['variant_mean_R'])}",
                    ],
                    [
                        "variant trades reaching >= 2R / >= 3R realised",
                        f"{be['variant_reached_2R_or_more']} / {be['variant_reached_3R_or_more']}",
                    ],
                    [
                        "variant mean R when price resumed to 2R / when the initial stop came first",
                        f"{_n(be['variant_mean_R_when_resumed_2R'])} / {_n(be['variant_mean_R_when_stopped_first'])}",
                    ],
                    [
                        "net P&L difference on these trades (variant - CONTROL, USDT)",
                        _n(be["variant_sum_pnl_minus_control"], 0),
                    ],
                ],
            ),
            "By family:",
            "",
            *_table(
                ["family", "n", "resumed to 2R", "CONTROL mean R", "variant mean R", "P&L diff"],
                [
                    [
                        d["family"],
                        str(d["n"]),
                        str(d["resumed_2R"]),
                        _n(d["control_mean_R"]),
                        _n(d["variant_mean_R"]),
                        _n(d["pnl_diff"], 0),
                    ]
                    for d in be["by_family"]
                ],
            ),
        ]
    # 9 downside
    lines += [
        "## 9. Downside analysis: trades CONTROL protected at breakeven that lose under the variant",
        "",
    ]
    if dn:
        lines += _table(
            ["metric", "value"],
            [
                ["trades protected at breakeven by CONTROL", str(dn["protected_by_breakeven_n"])],
                [
                    "worse under the variant",
                    f"{dn['worse_under_variant_n']} (mean diff {_n(dn['worse_mean_diff_R'])}R, total impact {_n(dn['worse_total_pnl_impact'], 0)} USDT)",
                ],
                [
                    "meaningful losses under the variant (realised R <= -0.25)",
                    f"{dn['meaningful_loss_n']} (variant mean {_n(dn['meaningful_loss_mean_variant_R'])}R vs CONTROL {_n(dn['meaningful_loss_mean_control_R'])}R, total impact {_n(dn['meaningful_loss_total_pnl_impact'], 0)} USDT)",
                ],
                [
                    "better under the variant",
                    f"{dn['better_under_variant_n']} (total impact {_n(dn['better_total_pnl_impact'], 0)} USDT)",
                ],
                [
                    "variant TP1 trades that ended on the INITIAL stop",
                    str(dn["variant_initial_stop_after_tp1_n"]),
                ],
            ],
        )
    # 10 family / side / regime
    lines += [
        "## 10. Family / side / regime",
        "",
        *_fam_compare(c.families, v.families, "By setup family (combined)"),
    ]
    for sn in seg_names:
        lines += _fam_compare(
            c.segments[sn]["families"], v.segments[sn]["families"], f"By setup family — {sn}"
        )
    rows = [
        [
            side,
            _side_cell(c.metrics["by_side"].get(side, {})),
            _side_cell(v.metrics["by_side"].get(side, {})),
        ]
        for side in ("LONG", "SHORT")
    ]
    lines += ["### LONG vs SHORT", "", *_table(["side", "CONTROL", VARIANT], rows)]
    lines += [
        "### By regime at entry — CONTROL",
        "",
        *_table(
            SUMMARY_HEADERS,
            _summary_rows(c.metrics["by_regime"], int(c.metrics.get("min_cell_n", 20))),
        ),
        f"### By regime at entry — {VARIANT}",
        "",
        *_table(
            SUMMARY_HEADERS,
            _summary_rows(v.metrics["by_regime"], int(v.metrics.get("min_cell_n", 20))),
        ),
    ]
    # 11 costs
    cst, vst = c.costs, v.costs
    lines += [
        "## 11. Costs (USDT, combined)",
        "",
        *_table(
            ["component", "CONTROL", VARIANT],
            [
                [
                    "trades",
                    str(c.metrics["overall"].get("n", 0)),
                    str(v.metrics["overall"].get("n", 0)),
                ],
                [
                    "gross P&L before slippage",
                    _n(cst.get("gross_before_slippage"), 0),
                    _n(vst.get("gross_before_slippage"), 0),
                ],
                ["slippage", _n(cst.get("slippage"), 0), _n(vst.get("slippage"), 0)],
                ["fees", _n(cst.get("fees"), 0), _n(vst.get("fees"), 0)],
                ["funding (negative = paid)", _n(cst.get("funding"), 0), _n(vst.get("funding"), 0)],
                [
                    "funding events while in position",
                    str(cst.get("n_funding_events")),
                    str(vst.get("n_funding_events")),
                ],
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
                [
                    "mean holding (h)",
                    _n(c.metrics["overall"].get("mean_holding_hours"), 1),
                    _n(v.metrics["overall"].get("mean_holding_hours"), 1),
                ],
            ],
        ),
    ]
    # 12 drawdown
    ca, va = c.account, v.account
    lines += [
        "## 12. Drawdown / equity (sequential, one position, fixed research equity for sizing)",
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
            ],
        ),
        "Null benchmark (geometry-matched, as in Phase 2):",
        "",
        *_table(
            ["series", "n", "mean R", "win rate", "strategy - null (R)", "z", "P(null >= strat)"],
            [*_null_rows("CONTROL", c.null), *_null_rows(VARIANT, v.null)],
        ),
    ]
    # 13 uncertainty
    co, vo = c.metrics["overall"], v.metrics["overall"]
    lines += [
        "## 13. Uncertainty",
        "",
        f"- Standard error of mean R: CONTROL {_n(_se(co))} (n={co.get('n', 0)}), variant {_n(_se(vo))} (n={vo.get('n', 0)}); 95% intervals CONTROL [{_n(co.get('expectancy_R', 0) - 1.96 * _se(co))}, {_n(co.get('expectancy_R', 0) + 1.96 * _se(co))}], variant [{_n(vo.get('expectancy_R', 0) - 1.96 * _se(vo))}, {_n(vo.get('expectancy_R', 0) + 1.96 * _se(vo))}].",
        f"- The paired comparison on {ts.get('n', 0)} TP1 trades is the direct estimate of the exit-rule effect: mean difference {_n(ts.get('paired_mean_diff_R'))}R, paired t = {_n(ts.get('paired_t'), 2)}; only {pa.get('all_pairs', {}).get('n_different_R', 0)} trades differ at all, so the whole experiment rests on a few dozen exit paths.",
        *[
            f"- {sn}: CONTROL {_n(c.segments[sn]['metrics']['overall'].get('expectancy_R'))}R (n={c.segments[sn]['metrics']['overall'].get('n', 0)}) vs variant {_n(v.segments[sn]['metrics']['overall'].get('expectancy_R'))}R (n={v.segments[sn]['metrics']['overall'].get('n', 0)})."
            for sn in seg_names
        ],
        "- The resume labels use the initial stop as the failure condition and the max-hold horizon; a different horizon would change counts. Single instrument, single 3-year window, no out-of-sample confirmation.",
    ]
    # 14 verdict
    lines += ["", "## 14. Verdict", ""]
    lines += _verdict(res)
    lines += [
        "",
        "## Appendix — frozen configuration (CONTROL; the variant differs only in `exits.breakeven_after_tp1`)",
        "",
        "```yaml",
        m["config_yaml"].strip(),
        "```",
        "",
    ]
    return "\n".join(lines)


def _verdict(res: Phase23Result) -> list[str]:
    c, v = res.control, res.variant
    co, vo = c.metrics["overall"], v.metrics["overall"]
    cc, vc = res.capture["CONTROL"], res.capture[VARIANT]
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
    dd_c = abs(c.account.get("max_drawdown_frac_trade_curve", 0.0) or 0.0)
    dd_v = abs(v.account.get("max_drawdown_frac_trade_curve", 0.0) or 0.0)
    fund_extra = (c.costs.get("funding", 0.0) or 0.0) - (
        v.costs.get("funding", 0.0) or 0.0
    )  # positive = variant paid more
    gain = (v.costs.get("net", 0.0) or 0.0) - (c.costs.get("net", 0.0) or 0.0)
    criteria = [
        (
            "net expectancy improves materially (> +0.05R over CONTROL)",
            net_v > net_c + 0.05,
            f"{_n(net_c)} -> {_n(net_v)}",
        ),
        ("2024 improves rather than deteriorates", v24 > c24, f"2024: {_n(c24)} -> {_n(v24)}"),
        (
            "profit factor improves",
            (vo.get("profit_factor") or 0) > (co.get("profit_factor") or 0),
            f"{_n(co.get('profit_factor'), 2)} -> {_n(vo.get('profit_factor'), 2)}",
        ),
        (
            "MFE capture improves (TP1 trades)",
            (vc.get("capture_ratio_tp1") or 0) > (cc.get("capture_ratio_tp1") or 0),
            f"{_p(cc.get('capture_ratio_tp1'), 1)} -> {_p(vc.get('capture_ratio_tp1'), 1)}",
        ),
        (
            "drawdown does not increase disproportionately (<= 1.25x CONTROL + 1pt)",
            dd_v <= dd_c * 1.25 + 0.01,
            f"{_p(dd_c)} -> {_p(dd_v)}",
        ),
        (
            "extra holding/funding cost does not erase the gain (net P&L gain > extra funding paid)",
            gain > max(fund_extra, 0.0),
            f"net P&L change {_n(gain, 0)} USDT vs extra funding paid {_n(fund_extra, 0)} USDT",
        ),
    ]
    lines = [
        "Pre-declared criteria (from the hypothesis, thresholds stated so the reader can disagree):",
        "",
        *_table(
            ["criterion", "met", "evidence"],
            [[k, "yes" if ok else "no", ev] for k, ok, ev in criteria],
        ),
    ]
    n_met = sum(1 for _, ok, _ in criteria if ok)
    improves = net_v > net_c
    if n_met == len(criteria):
        verdict = "ADOPTABLE FOR CONFIRMATORY VALIDATION"
        why = "all pre-declared criteria are met on both segments; the next step would be a single owner-approved confirmatory run on the untouched 2025+ window with `breakeven_after_tp1: false` and nothing else changed."
    elif improves and n_met >= 3:
        verdict = "INTERESTING BUT NOT ROBUST"
        why = "the variant improves net expectancy but fails at least one pre-declared criterion (see table); it must not be adopted on this evidence and must not be iterated into a parameter search."
    else:
        verdict = "NOT SUPPORTED"
        why = "removing the breakeven move does not improve realised expectancy on this window; the breakeven stop protects more P&L than the structural trail recovers."
    lines += ["", f"- **Verdict: {verdict}.** {why}"]
    be, dn, ts = (
        res.paired.get("breakeven_stops", {}),
        res.paired.get("downside", {}),
        res.paired.get("tp1_summary", {}),
    )
    if be:
        lines.append(
            f"- Trade-off in numbers: of {be['n']} CONTROL trades stopped at breakeven, {be['resumed_2R']} later resumed to 2R ({be['resumed_3R']} to 3R) and {be['stopped_first']} would have hit the initial stop first. Under the variant those trades realise {_n(be['variant_mean_R'])}R on average vs {_n(be['control_mean_R'])}R, a net P&L difference of {_n(be['variant_sum_pnl_minus_control'], 0)} USDT; {dn.get('meaningful_loss_n', 0)} of them become meaningful losses (<= -0.25R, total impact {_n(dn.get('meaningful_loss_total_pnl_impact'), 0)} USDT) while {dn.get('better_under_variant_n', 0)} improve (+{_n(dn.get('better_total_pnl_impact'), 0)} USDT)."
        )
    if ts:
        lines.append(
            f"- Across all {ts['n']} paired TP1 trades the exit-rule effect is {_n(ts['paired_mean_diff_R'])}R per trade (paired t {_n(ts['paired_t'], 2)}); MFE capture {_p(ts['mfe_capture_control'], 1)} -> {_p(ts['mfe_capture_variant'], 1)}; funding on these trades {_n(ts['control_funding_sum'], 0)} -> {_n(ts['variant_funding_sum'], 0)} USDT."
        )
    lines.append(
        "- 2025+ stays untouched. No live trading. No tuning. The frozen default remains `breakeven_after_tp1: true` unless the owner decides otherwise."
    )
    return lines
