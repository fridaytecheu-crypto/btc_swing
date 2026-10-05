"""Render reports/BTC_SWING_V1_PHASE2_2_EARLY_ENTRY_CONFIRMATION_EXIT.md (19 sections)."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import polars as pl

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
from btc_swing.research.phase22 import VARIANT, Phase22Result


def _stat_row(label: str, s: dict[str, Any]) -> list[str]:
    if not s or not s.get("n"):
        return [label, "0", "", "", "", "", "", "", "", "", ""]
    return [
        label,
        str(s["n"]),
        _p(s["win_rate"], 0),
        _n(s["mean_R"]),
        _n(s["median_R"]),
        _n(s["profit_factor"], 2),
        _n(s["mean_MFE_R"], 2),
        _n(s["mean_MAE_R"], 2),
        _n(s["mean_holding_hours"], 1),
        _n(s["fees_plus_slippage_per_trade"], 2),
        _n(s["sum_pnl"], 0),
    ]


STAT_HEADERS = [
    "group",
    "n",
    "win",
    "mean R",
    "median R",
    "PF",
    "MFE R",
    "MAE R",
    "mean hold h",
    "fees+slip/trade",
    "sum P&L",
]


def render_phase22(res: Phase22Result) -> str:
    c, v = res.control, res.variant
    m = res.manifest
    seg_names = [s.name for s in res.segments]
    w = res.confirmation_window_bars
    lines: list[str] = []
    lines += [
        "# BTC Swing V1 — Phase 2.2: EARLY ZONE ENTRY + CONFIRMATION-BASED EARLY EXIT",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · period {_ms(m['period_start_ms'])} -> {_ms(m['period_end_ms'])} UTC · "
        f"CONTROL result hash `{c.result.result_hash[:12]}` · {VARIANT} result hash `{v.result.result_hash[:12]}` · code `{c.result.manifest['code_version']}`",
        "",
        "**Central question.** Can we capture the timing advantage of entering at the plan zone while using the existing confirmation logic as an early risk filter rather than as a prerequisite for entry?",
        "",
        "Paper/backtest only. No live trading, no authenticated exchange access, no real money. 2025+ data untouched.",
        "",
        "## 1. Hypothesis",
        "",
        "Phase 2.1 showed that entering at the pre-defined zone improves timing on shared setups (+0.086R paired on 198 episodes) and recovers profitable never-triggered plans (73 trades, +0.78R), but admits plans that fail right after reaching the zone (70 trades CONTROL had classified INVALIDATED, -1.11R each), and those losses dominate. "
        "Pre-registered hypothesis H2: entering at the zone and then treating the ORIGINAL 15m confirmation as an early risk filter (exit if it does not arrive within the existing confirmation-lifecycle timeout, or if the plan's invalidation level is breached first) keeps most of the timing gain and the recovered opportunities while removing most of the early failures, giving net expectancy meaningfully above CONTROL or at least clearly above Phase 2.1 ZONE_ENTRY, materially lower drawdown than ZONE_ENTRY, and no clear breakdown in 2024.",
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
                    "CONTROL trades / episodes",
                    f"{c.result.manifest['n_trades']} / {c.result.manifest['n_episodes']}",
                ],
                [
                    "config hash CONTROL / variant",
                    f"`{res.baseline_check['control_config_hash'][:12]}` / `{res.baseline_check['variant_config_hash'][:12]}` (differ only in `experiment.entry_mode`)",
                ],
            ],
        ),
        "## 3. Exact variant definition",
        "",
        "- Entry: identical to Phase 2.1 ZONE_ENTRY — WATCH -> TRIGGERED on the first completed 5m bar that reaches the frozen plan zone while the plan is valid; fill at the next 5m open plus slippage. Sizing, leverage, stop, TP1/TP2, breakeven, structural trail, time cap, fees, slippage, funding, mark-price liquidation: unchanged and active from entry.",
        "- Confirmation predicate: the Phase 2 `ZoneSetupDetector.confirmed()` test, unchanged (completed 15m bar closes back through its EMA20 in the trade direction, bar in the trade direction, close not further than one zone pad beyond the zone). It is evaluated at the zone-reached decision (if already true, the trade starts confirmed) and at every later 5m close while unconfirmed.",
        f"- Confirmation window: the existing explicit confirmation-lifecycle timeout `episode.entry_ready_timeout_bars` = {w} five-minute bars ({w * 5 / 60:.0f} h), reused unchanged and counted from the entry bar. This is the only short timeout in the frozen state machine (the time CONTROL allows between confirmation and entry); the alternative, the 96-bar watch timeout, would let an unconfirmed position run for up to 8 h and was not tested. No other window was tried.",
        "- Early exits (before confirmation only): (a) a completed 5m close beyond the plan's invalidation level (the Phase 2 `LEVEL_BREACHED` test, unchanged) -> exit at the next 5m open with stop-type slippage (`EARLY_EXIT_INVALIDATION`); (b) no confirmation by the deadline -> exit at the next 5m open with stop-type slippage (`EARLY_EXIT_NO_CONFIRMATION`). Once confirmed, nothing differs from CONTROL's lifecycle.",
        "- Quirk kept deliberately (same predicate, no new rule): the confirmation test requires price to be within one pad of the zone, so a position that runs far in its favour immediately may fail to 'confirm' and be exited at the deadline with a profit; these cases are counted and shown.",
        "",
        "## 4. PIT audit",
        "",
        f"- {res.pit['visibility_rule']}. Zone: {res.pit['zone_definition']}. Confirmation: {res.pit['confirmation_predicate']}.",
        f"- Deterministic rerun: CONTROL {_n(res.pit['control_deterministic'])}, variant {_n(res.pit['variant_deterministic'])}.",
        f"- Truncation audit (variant): decisions up to {res.pit['variant_truncation']['cut'][:16]} identical with later data removed: {_n(res.pit['variant_truncation']['identical'])} ({res.pit['variant_truncation']['rows_compared']} rows).",
        "- At entry the engine cannot know whether confirmation will arrive; the position is real (margin, fees, funding, stop, liquidation) from the zone fill until confirmation, early exit or a normal exit.",
        "",
    ]
    # 5 overall
    tm = res.timing
    cr = _arm_rows("CONTROL", c.metrics, c.account, c.costs, c.result.manifest["n_episodes"])
    vr = _arm_rows(VARIANT, v.metrics, v.account, v.costs, v.result.manifest["n_episodes"])
    lines += [
        "## 5. Overall comparison (combined 2022-01 -> 2024-12)",
        "",
        *_compare_block(cr, vr),
        *_table(
            ["variant lifecycle counts", "n", "share of trades"],
            [
                [
                    "confirmed at entry (confirmation already true at the zone bar)",
                    str(tm.get("n_confirmed_at_entry", 0)),
                    _p(tm.get("n_confirmed_at_entry", 0) / max(tm.get("n_trades", 1), 1), 0),
                ],
                [
                    "confirmed after entry",
                    str(tm.get("n_confirmed_later", 0)),
                    _p(tm.get("n_confirmed_later", 0) / max(tm.get("n_trades", 1), 1), 0),
                ],
                [
                    "early exit: no confirmation by deadline",
                    str(tm.get("n_early_exit_no_confirmation", 0)),
                    _p(
                        tm.get("n_early_exit_no_confirmation", 0) / max(tm.get("n_trades", 1), 1), 0
                    ),
                ],
                [
                    "early exit: invalidation before confirmation",
                    str(tm.get("n_early_exit_invalidation", 0)),
                    _p(tm.get("n_early_exit_invalidation", 0) / max(tm.get("n_trades", 1), 1), 0),
                ],
                [
                    "unconfirmed, closed by a normal exit (stop/TP) inside the window",
                    str(tm.get("n_unconfirmed_other_exit", 0)),
                    _p(tm.get("n_unconfirmed_other_exit", 0) / max(tm.get("n_trades", 1), 1), 0),
                ],
            ],
        ),
    ]
    ref = res.phase21_ref
    if ref:
        lines += [
            "Reference — Phase 2.1 ZONE_ENTRY on the same window (from its persisted summary):",
            "",
            *_table(
                ["metric", "Phase 2.1 ZONE_ENTRY", "Phase 2.2 variant", "CONTROL"],
                [
                    [
                        "trades",
                        str(ref["n_trades"]),
                        str(v.metrics["overall"].get("n", 0)),
                        str(c.metrics["overall"].get("n", 0)),
                    ],
                    [
                        "net expectancy (R)",
                        _n(ref["expectancy_R"]),
                        _n(v.metrics["overall"].get("expectancy_R")),
                        _n(c.metrics["overall"].get("expectancy_R")),
                    ],
                    [
                        "profit factor",
                        _n(ref["profit_factor"], 2),
                        _n(v.metrics["overall"].get("profit_factor"), 2),
                        _n(c.metrics["overall"].get("profit_factor"), 2),
                    ],
                    [
                        "max drawdown",
                        _p(ref["max_drawdown_frac"]),
                        _p(v.account.get("max_drawdown_frac_trade_curve")),
                        _p(c.account.get("max_drawdown_frac_trade_curve")),
                    ],
                    [
                        "net P&L",
                        _n(ref["net"], 0),
                        _n(v.costs.get("net"), 0),
                        _n(c.costs.get("net"), 0),
                    ],
                    [
                        "cost drag per trade (R)",
                        _n(ref["cost_drag_R_per_trade"]),
                        _n(v.costs.get("cost_drag_R_per_trade")),
                        _n(c.costs.get("cost_drag_R_per_trade")),
                    ],
                    [
                        "CONTROL-INVALIDATED group entered",
                        f"n={ref['invalidated_group']['n']}, {_n(ref['invalidated_group']['expectancy_R'])}R, {_n(ref['invalidated_group']['sum_pnl'], 0)} USDT"
                        if ref.get("invalidated_group")
                        else "n/a",
                        _inval_group_cell(res),
                        "—",
                    ],
                ],
            ),
        ]
    # 6/7 segments
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
        if ref and sn in ref["segments"]:
            r = ref["segments"][sn]
            lines += [
                f"Phase 2.1 ZONE_ENTRY in {sn}: n={r['n']}, expectancy {_n(r['expectancy_R'])}R, max DD {_p(r['max_drawdown_frac'])}.",
                "",
            ]
        lines += _fam_compare(cs["families"], vs["families"], f"Families in {sn}")
    # 8 matched
    mt = res.matched
    lines += [
        "## 8. Matched episodes (same detection in both arms: key = family + detection time)",
        "",
        f"- Episodes: CONTROL {mt['control_episodes']}, variant {mt['variant_episodes']}, matched {mt['matched']}, CONTROL-only {mt['control_only']}, variant-only {mt['variant_only']}.",
        "",
        "Outcome transition for matched episodes (CONTROL -> variant):",
        "",
        *_table(
            ["CONTROL", VARIANT, "n"],
            [
                [d["outcome_class"], d["outcome_class_v"], str(d["len"])]
                for d in mt["transition_matrix"]
            ],
        ),
    ]
    if mt.get("variant_trades_by_control_outcome"):
        lines += [
            "Variant trades decomposed by what CONTROL did on the same episode:",
            "",
            *_table(
                [
                    "CONTROL outcome",
                    "n",
                    "win",
                    "exp. R",
                    "median R",
                    "PF",
                    "MFE R",
                    "MAE R",
                    "target first",
                    "fees+slip / trade",
                    "sum P&L",
                ],
                [
                    [
                        d["control_outcome"],
                        str(d["n"]),
                        _p(d["win_rate"], 0),
                        _n(d["expectancy_R"]),
                        _n(d["median_R"]),
                        _n(d["profit_factor"], 2),
                        _n(d["mean_MFE_R"], 2),
                        _n(d["mean_MAE_R"], 2),
                        _p(d["target_first_share"], 0),
                        _n(d["fees_plus_slippage_per_trade"], 2),
                        _n(d["sum_pnl"], 0),
                    ]
                    for d in mt["variant_trades_by_control_outcome"]
                ],
            ),
        ]
    bt = mt.get("both_traded")
    pairs = mt.get("pairs")
    if bt:
        lines += [
            "### Episodes traded by BOTH arms (core analysis: same plan, different entry mechanics)",
            "",
            *_table(
                ["metric", "CONTROL", VARIANT],
                [
                    ["pairs", str(bt["n"]), str(bt["n"])],
                    ["mean realised R", _n(bt["control_mean_R"]), _n(bt["zone_mean_R"])],
                    ["win rate", _p(bt["control_win_rate"], 1), _p(bt["zone_win_rate"], 1)],
                    [
                        "mean stop distance",
                        _p(bt["control_mean_stop_pct"]),
                        _p(bt["zone_mean_stop_pct"]),
                    ],
                    [
                        "mean MFE R / MAE R",
                        f"{_n(bt['control_mean_MFE_R'])} / {_n(bt['control_mean_MAE_R'])}",
                        f"{_n(bt['zone_mean_MFE_R'])} / {_n(bt['zone_mean_MAE_R'])}",
                    ],
                    ["sum net P&L", _n(bt["control_sum_pnl"], 0), _n(bt["zone_sum_pnl"], 0)],
                ],
            ),
            f"- Paired difference (variant - CONTROL) in R: mean {_n(bt['paired_mean_diff_R'])}, paired t = {_n(bt['paired_t'], 2)}, variant better in {_p(bt['frac_zone_better_R'], 0)} of pairs.",
            f"- Time saved: variant entered earlier in {_p(bt['frac_zone_earlier'], 0)} of pairs, mean {_n(bt['mean_hours_earlier'], 1)} h (median {_n(bt['median_hours_earlier'], 1)} h); better entry price in {_p(bt['frac_better_price'], 0)} of pairs, mean improvement {_p(bt['mean_price_improvement_pct'])} of entry price.",
            f"- Structural target reached before invalidation (plan label, identical for both arms): {_p(bt['target_first_share'], 0)} of pairs.",
            "",
        ]
        if (
            isinstance(pairs, pl.DataFrame)
            and pairs.height
            and "zone_confirmed_after_entry" in pairs.columns
        ):
            conf = pairs.filter(pl.col("zone_confirmed_after_entry") == True)  # noqa: E712
            unc = pairs.filter(pl.col("zone_confirmed_after_entry") != True)  # noqa: E712
            lines += [
                "Shared episodes split by whether the variant's confirmation eventually arrived:",
                "",
                *_table(
                    [
                        "group",
                        "pairs",
                        "CONTROL mean R",
                        "variant mean R",
                        "variant mean h to confirmation",
                        "MFE before conf. (R)",
                        "MAE before conf. (R)",
                    ],
                    [
                        [
                            "confirmation arrived",
                            str(conf.height),
                            _n(conf["control_R"].mean()) if conf.height else "n/a",
                            _n(conf["zone_R"].mean()) if conf.height else "n/a",
                            _n(conf["zone_hours_to_confirmation"].mean()) if conf.height else "n/a",
                            _n(conf["zone_mfe_before_confirm_R"].mean()) if conf.height else "n/a",
                            _n(conf["zone_mae_before_confirm_R"].mean()) if conf.height else "n/a",
                        ],
                        [
                            "no confirmation (early exit / stopped first)",
                            str(unc.height),
                            _n(unc["control_R"].mean()) if unc.height else "n/a",
                            _n(unc["zone_R"].mean()) if unc.height else "n/a",
                            "—",
                            _n(unc["zone_mfe_before_confirm_R"].mean()) if unc.height else "n/a",
                            _n(unc["zone_mae_before_confirm_R"].mean()) if unc.height else "n/a",
                        ],
                    ],
                ),
            ]
        lines += [
            "By family (pairs):",
            "",
            *_table(
                [
                    "family",
                    "pairs",
                    "CONTROL mean R",
                    "variant mean R",
                    "mean h earlier",
                    "mean price impr.",
                ],
                [
                    [
                        d["family"],
                        str(d["n"]),
                        _n(d["control_mean_R"]),
                        _n(d["zone_mean_R"]),
                        _n(d["mean_hours_earlier"], 1),
                        _p(d["mean_price_improvement_pct"]),
                    ]
                    for d in bt["by_family"]
                ],
            ),
        ]
        if isinstance(pairs, pl.DataFrame) and pairs.height:
            rows = []
            for r in pairs.head(25).to_dicts():
                rows.append(
                    [
                        r["family"].replace("_", " "),
                        r["side"],
                        _ms(r["control_entry_ms"]),
                        _ms(r["zone_entry_ms"]),
                        _n(r["hours_earlier"], 1),
                        _p(r["price_improvement_pct"]),
                        "yes" if r.get("zone_confirmed_after_entry") else "no",
                        _n(r.get("zone_hours_to_confirmation"), 1),
                        f"{_n(r.get('zone_mfe_before_confirm_R'), 2)}/{_n(r.get('zone_mae_before_confirm_R'), 2)}",
                        f"{_n(r['control_R'], 2)}/{_n(r['zone_R'], 2)}",
                        "yes" if r["path_outcome"] == "target_first" else "no",
                    ]
                )
            lines += [
                "First 25 matched pairs (full table in `matched_pairs.parquet`):",
                "",
                *_table(
                    [
                        "family",
                        "side",
                        "CONTROL entry",
                        "variant entry",
                        "h saved",
                        "price diff",
                        "confirmed",
                        "h to conf.",
                        "MFE/MAE before conf. (R)",
                        "R C/V",
                        "target first",
                    ],
                    rows,
                ),
            ]
    # 9 confirmation timing
    lines += [
        "## 9. Confirmation timing (variant)",
        "",
        *_table(
            ["metric", "value"],
            [
                ["trades", str(tm.get("n_trades", 0))],
                [
                    "confirmed (any time)",
                    f"{tm.get('n_confirmed', 0)} ({_p(tm.get('confirmed_share'), 0)})",
                ],
                [
                    "confirmed at entry / after entry",
                    f"{tm.get('n_confirmed_at_entry', 0)} / {tm.get('n_confirmed_later', 0)}",
                ],
                [
                    "hours to confirmation (after entry): mean / median / p90",
                    f"{_n(tm.get('hours_to_confirmation_mean'), 2)} / {_n(tm.get('hours_to_confirmation_median'), 2)} / {_n(tm.get('hours_to_confirmation_p90'), 2)}",
                ],
                [
                    "MFE / MAE before confirmation (R, confirmed-after-entry trades)",
                    f"{_n(tm.get('mfe_before_confirm_R_mean'))} / {_n(tm.get('mae_before_confirm_R_mean'))}",
                ],
            ],
        ),
        *_table(
            STAT_HEADERS,
            [
                _stat_row("confirmed (all)", tm.get("confirmed", {})),
                _stat_row("confirmed at entry", tm.get("confirmed_at_entry", {})),
                _stat_row("confirmed after entry", tm.get("confirmed_later", {})),
                _stat_row("never confirmed", tm.get("unconfirmed", {})),
            ],
        ),
        "By family:",
        "",
        *_table(
            [
                "family",
                "n",
                "confirmed share",
                "early exit no-conf",
                "early exit inval",
                "confirmed mean R",
                "unconfirmed mean R",
            ],
            [
                [
                    d["family"],
                    str(d["n"]),
                    _p(d["confirmed_share"], 0),
                    str(d["early_exit_no_conf"]),
                    str(d["early_exit_inval"]),
                    _n(d["confirmed_mean_R"]),
                    _n(d["unconfirmed_mean_R"]),
                ]
                for d in tm.get("by_family", [])
            ],
        ),
    ]
    # 10 early exit no confirmation
    for title, sec, key in (
        (
            "## 10. Early-exit outcomes: no confirmation within the window",
            res.early_no_conf,
            "no_conf",
        ),
        ("## 11. Invalidated-before-confirmation outcomes", res.early_inval, "inval"),
    ):
        lines += [title, ""]
        if not sec.get("n"):
            lines += ["None.", ""]
            continue
        lines += [
            *_table(STAT_HEADERS, [_stat_row("all", sec)]),
            f"- Profitable share {_p(sec['frac_profitable'], 0)}; R quantiles q10={_n(sec['r_quantiles'][0.1], 2)}, q25={_n(sec['r_quantiles'][0.25], 2)}, q50={_n(sec['r_quantiles'][0.5], 2)}, q75={_n(sec['r_quantiles'][0.75], 2)}, q90={_n(sec['r_quantiles'][0.9], 2)}; fees+slippage total {_n(sec['sum_fees_plus_slippage'], 0)} USDT, funding {_n(sec['sum_funding'], 0)} USDT.",
            "",
            "What CONTROL eventually classified the same episode as:",
            "",
            *_table(
                [
                    "CONTROL outcome",
                    "n",
                    "win",
                    "mean R",
                    "median R",
                    "PF",
                    "MFE R",
                    "MAE R",
                    "mean hold h",
                    "sum P&L",
                ],
                [
                    [
                        d["control_outcome"],
                        str(d["n"]),
                        _p(d["win_rate"], 0),
                        _n(d["expectancy_R"]),
                        _n(d["median_R"]),
                        _n(d["profit_factor"], 2),
                        _n(d["mean_MFE_R"], 2),
                        _n(d["mean_MAE_R"], 2),
                        _n(d["mean_holding_hours"], 1),
                        _n(d["sum_pnl"], 0),
                    ]
                    for d in sec["by_control_outcome"]
                ],
            ),
            "By family:",
            "",
            *_table(
                ["family", "n", "win", "mean R", "PF", "sum P&L"],
                [
                    [
                        d["family"],
                        str(d["n"]),
                        _p(d["win_rate"], 0),
                        _n(d["expectancy_R"]),
                        _n(d["profit_factor"], 2),
                        _n(d["sum_pnl"], 0),
                    ]
                    for d in sec["by_family"]
                ],
            ),
        ]
    if ref and ref.get("invalidated_group"):
        lines += [
            f"Phase 2.1 reference: the CONTROL-INVALIDATED group entered by ZONE_ENTRY was n={ref['invalidated_group']['n']}, {_n(ref['invalidated_group']['expectancy_R'])}R, {_n(ref['invalidated_group']['sum_pnl'], 0)} USDT. Section 8 above shows the same group under the variant.",
            "",
        ]
    # 12 recovered
    rc = res.recovery
    lines += [
        "## 12. Recovered NEVER_TRIGGERED episodes (CONTROL never traded them)",
        "",
        f"- CONTROL never-triggered: {rc['control_never_triggered']}; matched in variant: {rc['matched_in_variant']}; became trades: {rc['became_trades']}"
        + (
            f"; later confirmed {rc.get('later_confirmed', 0)}, early exit no-confirmation {rc.get('early_exit_no_confirmation', 0)}, early exit invalidation {rc.get('early_exit_invalidation', 0)}."
            if rc.get("became_trades")
            else "."
        ),
        "",
    ]
    if rc.get("became_trades"):
        lines += [
            *_table(
                [*STAT_HEADERS, "target first"],
                [
                    [
                        *_stat_row("all recovered", rc["all"]),
                        _p(rc["all"].get("target_first_share"), 0),
                    ],
                    [
                        *_stat_row("recovered & confirmed", rc["confirmed"]),
                        _p(rc["confirmed"].get("target_first_share"), 0),
                    ],
                    [
                        *_stat_row("recovered & never confirmed", rc["unconfirmed"]),
                        _p(rc["unconfirmed"].get("target_first_share"), 0),
                    ],
                ],
            ),
            "By family:",
            "",
            *_table(
                ["family", "n", "win", "mean R", "PF", "target first", "sum P&L"],
                [
                    [
                        d["family"],
                        str(d["n"]),
                        _p(d["win_rate"], 0),
                        _n(d["expectancy_R"]),
                        _n(d["profit_factor"], 2),
                        _p(d["target_first_share"], 0),
                        _n(d["sum_pnl"], 0),
                    ]
                    for d in rc["by_family"]
                ],
            ),
            "By CONTROL end reason:",
            "",
            *_table(
                ["CONTROL end reason", "n", "win", "mean R", "PF", "sum P&L"],
                [
                    [
                        d["control_end_reason"],
                        str(d["n"]),
                        _p(d["win_rate"], 0),
                        _n(d["expectancy_R"]),
                        _n(d["profit_factor"], 2),
                        _n(d["sum_pnl"], 0),
                    ]
                    for d in rc["by_control_end_reason"]
                ],
            ),
        ]
        if ref and ref.get("recovered"):
            rr = ref["recovered"]
            lines += [
                f"Phase 2.1 reference: ZONE_ENTRY recovered n={rr['n']} at {_n(rr['expectancy_R'])}R ({_n(rr['sum_pnl'], 0)} USDT).",
                "",
            ]
    # 13 families
    lines += [
        "## 13. Family results (combined; no family disabled)",
        "",
        *_fam_compare(c.families, v.families, "All families")[2:],
    ]
    # 14 sides
    rows = [
        [
            side,
            _side_cell(c.metrics["by_side"].get(side, {})),
            _side_cell(v.metrics["by_side"].get(side, {})),
        ]
        for side in ("LONG", "SHORT")
    ]
    lines += ["## 14. LONG vs SHORT", "", *_table(["side", "CONTROL", VARIANT], rows)]
    for sn in seg_names:
        rows = [
            [
                side,
                _side_cell(c.segments[sn]["metrics"]["by_side"].get(side, {})),
                _side_cell(v.segments[sn]["metrics"]["by_side"].get(side, {})),
            ]
            for side in ("LONG", "SHORT")
        ]
        lines += [f"{sn}:", "", *_table(["side", "CONTROL", VARIANT], rows)]
    # 15 MFE/MAE
    co, vo = c.metrics["overall"], v.metrics["overall"]
    cte, vte = c.metrics.get("target_evaluation", {}), v.metrics.get("target_evaluation", {})
    lines += [
        "## 15. MFE / MAE",
        "",
        *_table(
            ["metric", "CONTROL", VARIANT],
            [
                ["mean MFE (R)", _n(co.get("mean_MFE_R")), _n(vo.get("mean_MFE_R"))],
                ["mean MAE (R)", _n(co.get("mean_MAE_R")), _n(vo.get("mean_MAE_R"))],
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
                ["TP1 executed", _p(cte.get("tp1_hit_rate"), 1), _p(vte.get("tp1_hit_rate"), 1)],
            ],
        ),
    ]
    # 16 costs
    cc, vc = c.costs, v.costs
    lines += [
        "## 16. Fees / slippage / funding impact (USDT, combined)",
        "",
        *_table(
            ["component", "CONTROL", VARIANT],
            [
                ["trades", str(co.get("n", 0)), str(vo.get("n", 0))],
                [
                    "gross P&L before slippage",
                    _n(cc.get("gross_before_slippage"), 0),
                    _n(vc.get("gross_before_slippage"), 0),
                ],
                ["slippage", _n(cc.get("slippage"), 0), _n(vc.get("slippage"), 0)],
                ["fees", _n(cc.get("fees"), 0), _n(vc.get("fees"), 0)],
                ["funding", _n(cc.get("funding"), 0), _n(vc.get("funding"), 0)],
                ["net P&L", _n(cc.get("net"), 0), _n(vc.get("net"), 0)],
                [
                    "gross expectancy (R, before slippage)",
                    _n(cc.get("expectancy_R_before_costs")),
                    _n(vc.get("expectancy_R_before_costs")),
                ],
                [
                    "net expectancy (R)",
                    _n(cc.get("expectancy_R_net")),
                    _n(vc.get("expectancy_R_net")),
                ],
                [
                    "cost drag per trade (R)",
                    _n(cc.get("cost_drag_R_per_trade")),
                    _n(vc.get("cost_drag_R_per_trade")),
                ],
            ],
        ),
    ]
    # 17 account
    ca, va = c.account, v.account
    lines += [
        "## 17. Drawdown / equity (sequential, one position, fixed research equity for sizing)",
        "",
        *_table(
            ["metric", "CONTROL", VARIANT],
            [
                ["final equity", _n(ca.get("final_equity"), 2), _n(va.get("final_equity"), 2)],
                ["total return", _p(ca.get("total_return")), _p(va.get("total_return"))],
                ["CAGR", _p(ca.get("cagr")), _p(va.get("cagr"))],
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
        "Null benchmark (same geometry-matched nulls as Phase 2):",
        "",
        *_table(
            ["series", "n", "mean R", "win rate", "strategy - null (R)", "z", "P(null >= strat)"],
            [*_null_rows("CONTROL", c.null), *_null_rows(VARIANT, v.null)],
        ),
    ]
    # 18 uncertainty
    lines += [
        "## 18. Uncertainty",
        "",
        f"- Standard error of mean R: CONTROL {_n(_se(co))} (n={co.get('n', 0)}), variant {_n(_se(vo))} (n={vo.get('n', 0)}); approximate 95% intervals CONTROL [{_n(co.get('expectancy_R', 0) - 1.96 * _se(co))}, {_n(co.get('expectancy_R', 0) + 1.96 * _se(co))}], variant [{_n(vo.get('expectancy_R', 0) - 1.96 * _se(vo))}, {_n(vo.get('expectancy_R', 0) + 1.96 * _se(vo))}].",
    ]
    if bt:
        lines.append(
            f"- Paired comparison on {bt['n']} shared episodes: mean difference {_n(bt['paired_mean_diff_R'])}R, paired t = {_n(bt['paired_t'], 2)}."
        )
    for sn in seg_names:
        so_c, so_v = c.segments[sn]["metrics"]["overall"], v.segments[sn]["metrics"]["overall"]
        lines.append(
            f"- {sn}: CONTROL {_n(so_c.get('expectancy_R'))}R (n={so_c.get('n', 0)}, t={_n(so_c.get('t_stat_R'), 2)}) vs variant {_n(so_v.get('expectancy_R'))}R (n={so_v.get('n', 0)}, t={_n(so_v.get('t_stat_R'), 2)})."
        )
    lines += [
        "- One confirmation window was tested (the frozen 24-bar timeout); results for other windows are unknown by design.",
        "- Early exits are modelled at the next 5m open with stop-type slippage; many are small losses whose size is cost-dominated, so the cost assumptions matter more for this variant than for CONTROL.",
        "- Per-family cells are small; family-level differences are directional. Single instrument, single 3-year window, no out-of-sample confirmation yet.",
    ]
    # 19 recommendation
    lines += ["", "## 19. Recommendation (not implemented)", ""]
    lines += _recommendation(res)
    lines += [
        "",
        "## Appendix — frozen configuration (CONTROL; the variant differs only in `experiment.entry_mode`)",
        "",
        "```yaml",
        m["config_yaml"].strip(),
        "```",
        "",
    ]
    return "\n".join(lines)


def _inval_group_cell(res: Phase22Result) -> str:
    d = next(
        (
            x
            for x in res.matched.get("variant_trades_by_control_outcome", [])
            if x.get("control_outcome") == "INVALIDATED"
        ),
        None,
    )
    return f"n={d['n']}, {_n(d['expectancy_R'])}R, {_n(d['sum_pnl'], 0)} USDT" if d else "n=0"


def _recommendation(res: Phase22Result) -> list[str]:
    c, v, ref = res.control, res.variant, res.phase21_ref
    co, vo = c.metrics["overall"], v.metrics["overall"]
    net_c, net_v = co.get("expectancy_R", 0.0) or 0.0, vo.get("expectancy_R", 0.0) or 0.0
    dd_c = abs(c.account.get("max_drawdown_frac_trade_curve", 0.0) or 0.0)
    dd_v = abs(v.account.get("max_drawdown_frac_trade_curve", 0.0) or 0.0)
    net_21 = (ref or {}).get("expectancy_R")
    dd_21 = abs((ref or {}).get("max_drawdown_frac") or 0.0) if ref else math.nan
    inval_21 = ((ref or {}).get("invalidated_group") or {}).get("sum_pnl")
    inval_22 = next(
        (
            x
            for x in res.matched.get("variant_trades_by_control_outcome", [])
            if x.get("control_outcome") == "INVALIDATED"
        ),
        None,
    )
    rec = res.recovery
    rec_21 = ((ref or {}).get("recovered") or {}).get("sum_pnl")
    rec_22 = (rec.get("all") or {}).get("sum_pnl") if rec.get("became_trades") else 0.0
    v24 = v.segments.get("val_2024", {}).get("metrics", {}).get("overall", {})
    c24 = c.segments.get("val_2024", {}).get("metrics", {}).get("overall", {})
    criteria = [
        (
            "net expectancy meaningfully above CONTROL (> +0.05R) OR clearly above Phase 2.1 ZONE_ENTRY (> +0.05R)",
            (net_v > net_c + 0.05) or (net_21 is not None and net_v > net_21 + 0.05),
            f"CONTROL {_n(net_c)}, Phase 2.1 {_n(net_21)}, variant {_n(net_v)}",
        ),
        (
            "materially lower drawdown than Phase 2.1 ZONE_ENTRY (<= 0.8x)",
            (not math.isnan(dd_21)) and dd_v <= 0.8 * dd_21,
            f"Phase 2.1 {_p(dd_21)}, variant {_p(dd_v)}, CONTROL {_p(dd_c)}",
        ),
        (
            "early exit removes a large share (>= 50%) of the failed zone-entry loss",
            inval_21 is not None and inval_22 is not None and inval_22["sum_pnl"] > 0.5 * inval_21,
            f"CONTROL-INVALIDATED group: Phase 2.1 {_n(inval_21, 0)} USDT -> variant {_n(inval_22['sum_pnl'], 0) if inval_22 else 'n/a'} USDT (n={inval_22['n'] if inval_22 else 0})",
        ),
        (
            "recovered NEVER_TRIGGERED value mostly kept (>= 50% of Phase 2.1 recovered P&L)",
            rec_21 is not None and rec_22 is not None and rec_22 >= 0.5 * rec_21,
            f"Phase 2.1 {_n(rec_21, 0)} USDT -> variant {_n(rec_22, 0)} USDT (n={rec.get('became_trades', 0)})",
        ),
        (
            "2024 not clearly broken (variant 2024 expectancy >= CONTROL 2024 - 0.05R and > -0.10R)",
            (v24.get("expectancy_R", -1) or -1) >= (c24.get("expectancy_R", 0) or 0) - 0.05
            and (v24.get("expectancy_R", -1) or -1) > -0.10,
            f"2024: CONTROL {_n(c24.get('expectancy_R'))}, variant {_n(v24.get('expectancy_R'))}",
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
    if n_met == len(criteria):
        verdict = "H2 SUPPORTED on this window: the confirmation filter works as an early exit while the zone entry keeps its timing gain. Recommended next step: owner review; if accepted, freeze ZONE_ENTRY_CONFIRM_EXIT as the pre-registered baseline candidate and plan the single confirmatory run on 2025+ (owner approval required). No other change."
    elif n_met >= 3:
        verdict = "H2 PARTIALLY SUPPORTED: the mechanism does what it was designed to do on some criteria but not all. It must not be adopted on this evidence. Recommended next step: owner review of the failed criteria (sections 8-12, 16); do not iterate on the window or the predicate without a new pre-registered hypothesis."
    else:
        verdict = "H2 NOT SUPPORTED: using the confirmation as an early exit does not recover enough of the filter's value, or the extra trades' costs consume the timing gain. Recommended next step: stop changing entry mechanics; remaining pre-registered candidates from Phase 2 are the exit design and the regime eligibility of the SHORT families, each needing a new single hypothesis and owner approval."
    lines += ["", f"- **Verdict: {verdict}**"]
    pop = res.matched.get("variant_trades_by_control_outcome") or []
    if pop:
        lines.append(
            "- Where the variant's result comes from (its trades split by CONTROL's outcome on the same episode): "
            + "; ".join(
                f"{d['control_outcome']}: n={d['n']}, {_n(d['expectancy_R'])}R, {_n(d['sum_pnl'], 0)} USDT"
                for d in pop
            )
            + "."
        )
    tm = res.timing
    if tm.get("n_trades"):
        lines.append(
            f"- Lifecycle: {_p(tm.get('confirmed_share'), 0)} of variant trades confirmed ({tm.get('n_confirmed_at_entry', 0)} at entry, {tm.get('n_confirmed_later', 0)} later, median {_n(tm.get('hours_to_confirmation_median'), 2)} h); {tm.get('n_early_exit_no_confirmation', 0)} exited at the deadline and {tm.get('n_early_exit_invalidation', 0)} on invalidation before confirmation."
        )
    lines.append(
        "- 2025+ stays untouched. No live trading. No tuning. The `experiment.entry_mode` default remains CONFIRMED_TRIGGER."
    )
    return lines
