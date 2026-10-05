"""Render reports/BTC_SWING_V1_PHASE2_1_ENTRY_MECHANICS.md (17 sections)."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import polars as pl

from btc_swing.research.phase21 import Phase21Result


def _p(v: Any, nd: int = 2) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "n/a"
    if math.isnan(x) or math.isinf(x):
        return "n/a"
    return f"{x * 100:.{nd}f}%"


def _n(v: Any, nd: int = 3) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return str(v)
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if math.isnan(x):
        return "n/a"
    if math.isinf(x):
        return "inf"
    return f"{x:.{nd}f}"


def _ms(ms: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0, tz=UTC).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "n/a"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    return [
        "| " + " | ".join(headers) + " |",
        "|" + "---|" * len(headers),
        *["| " + " | ".join(r) + " |" for r in rows],
        "",
    ]


def _arm_rows(
    label: str, m: dict[str, Any], a: dict[str, Any], c: dict[str, Any], ep_total: int
) -> dict[str, str]:
    o = m["overall"]
    n = o.get("n", 0)
    return {
        "arm": label,
        "trades": str(n),
        "episodes": str(ep_total),
        "setup->entry": _p(n / ep_total if ep_total else math.nan, 1),
        "exp. R (net)": _n(o.get("expectancy_R")),
        "exp. R (gross)": _n(c.get("expectancy_R_before_costs")),
        "t": _n(o.get("t_stat_R"), 2),
        "PF": _n(o.get("profit_factor"), 2),
        "win rate": _p(o.get("win_rate"), 1),
        "avg win R": _n(o.get("avg_win_R")),
        "avg loss R": _n(o.get("avg_loss_R")),
        "median R": _n(o.get("median_R")),
        "MFE R": _n(o.get("mean_MFE_R")),
        "MAE R": _n(o.get("mean_MAE_R")),
        "max DD": _p(a.get("max_drawdown_frac_trade_curve")),
        "Sharpe": _n(a.get("sharpe_daily_annualised"), 2),
        "median hold h": _n(o.get("median_holding_hours"), 1),
        "fees": _n(c.get("fees"), 0),
        "slippage": _n(c.get("slippage"), 0),
        "funding": _n(c.get("funding"), 0),
        "net P&L": _n(c.get("net"), 0),
        "net return": _p(a.get("total_return")),
    }


def _compare_block(control: dict[str, str], variant: dict[str, str]) -> list[str]:
    keys = [k for k in control if k != "arm"]
    return _table(["metric", "CONTROL", "ZONE_ENTRY"], [[k, control[k], variant[k]] for k in keys])


def _fam_compare(cf: list[dict[str, Any]], vf: list[dict[str, Any]], title: str) -> list[str]:
    fams = sorted({f["family"] for f in cf} | {f["family"] for f in vf})
    rows = []
    for fam in fams:
        c = next((f for f in cf if f["family"] == fam), None)
        v = next((f for f in vf if f["family"] == fam), None)

        def cell(d: dict[str, Any] | None) -> str:
            if not d:
                return "n=0"
            return f"n={d['n']}, R={_n(d['expectancy_R'])}, PF={_n(d['profit_factor'], 2)}, win={_p(d['win_rate'], 0)}, MFE={_n(d['mean_MFE_R'], 2)}, MAE={_n(d['mean_MAE_R'], 2)}, acct={_p(d['sum_account_return'])}"

        rows.append([fam, cell(c), cell(v)])
    return [f"### {title}", "", *_table(["family", "CONTROL", "ZONE_ENTRY"], rows)]


def _null_rows(label: str, nl: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    if not nl.get("n_trades"):
        return [[label, "0", "", "", "", "", ""]]
    st = nl["strategy"]
    rows.append(
        [
            f"{label} strategy",
            str(nl["n_trades"]),
            _n(st["mean_R"]),
            _p(st["win_rate"], 1),
            "",
            "",
            "",
        ]
    )
    for v in ("time", "regime"):
        d = nl.get(v)
        if d:
            rows.append(
                [
                    f"{label} null ({v}-matched)",
                    str(d["n_samples"]),
                    _n(d["null_mean_R"]),
                    _p(d["null_win_rate"], 1),
                    _n(d["strategy_minus_null_mean_R"]),
                    f"z={_n(d['z_vs_null_reps'], 2)}",
                    _p(d["frac_reps_with_mean_R_ge_strategy"], 0),
                ]
            )
    return rows


def _side_cell(d: dict[str, Any]) -> str:
    if not d.get("n"):
        return "n=0"
    return f"n={d.get('n', 0)}, R={_n(d.get('expectancy_R'))}, PF={_n(d.get('profit_factor'), 2)}, win={_p(d.get('win_rate'), 0)}"


def render_phase21(res: Phase21Result) -> str:
    c, v = res.control, res.variant
    m = res.manifest
    seg_names = [s.name for s in res.segments]
    lines: list[str] = []
    lines += [
        "# BTC Swing V1 — Phase 2.1: ENTRY MECHANICS (CONTROL vs ZONE_ENTRY)",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · period {_ms(m['period_start_ms'])} -> {_ms(m['period_end_ms'])} UTC · "
        f"CONTROL result hash `{c.result.result_hash[:12]}` · ZONE_ENTRY result hash `{v.result.result_hash[:12]}` · code `{c.result.manifest['code_version']}`",
        "",
        "**Central question.** Is the current confirmation trigger destroying edge by making us enter too late or miss valid BTC swing setups that already reached the planned entry zone?",
        "",
        "Paper/backtest only. No live trading, no authenticated exchange access, no real money. 2025+ data untouched.",
        "",
        "## 1. Hypothesis",
        "",
        "Phase 2 (frozen defaults) found: traded episodes had a mean signed 24h forward return of about +0.34% from the detection close, never-triggered episodes about +1.13%, and never-triggered episodes reached their structural target before invalidation more often (66% vs 42%). "
        "Pre-registered hypothesis H1: entering when a valid setup first reaches its pre-defined entry zone (ZONE_ENTRY) has materially stronger NET expectancy than waiting for the 15m confirmation and 5m trigger (CONTROL), without an unacceptable increase in drawdown, in both 2022-23 and 2024, and it recovers useful never-triggered setups. "
        "Only the WATCH -> TRIGGERED transition changes; setup detection, zone, invalidation, stop, sizing, leverage, exits, costs and regime rules are identical.",
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
                    "config hash CONTROL / ZONE_ENTRY",
                    f"`{res.baseline_check['control_config_hash'][:12]}` / `{res.baseline_check['variant_config_hash'][:12]}`",
                ],
                ["note", res.baseline_check["note"]],
            ],
        ),
        "## 3. Exact variant definition",
        "",
        "- CONTROL (`experiment.entry_mode: CONFIRMED_TRIGGER`): WATCH -> ENTRY_READY when the completed 5m bar reaches the zone AND the completed 15m bar closes back through its EMA20 in the trade direction; ENTRY_READY -> TRIGGERED when a completed 5m bar closes beyond the previous 5m extreme on the right side of the zone; fill at the next 5m open plus slippage.",
        "- ZONE_ENTRY (`experiment.entry_mode: ZONE_ENTRY`): WATCH -> TRIGGERED on the first completed 5m bar that reaches the zone (`zone_reached`: bar traded into the zone and closed on the correct side of its far edge) while the plan is still valid; fill at the next 5m open plus slippage. No ENTRY_READY state, no 15m confirmation, no 5m trigger.",
        "- Unchanged in both arms: families, regime eligibility, detection, the frozen `SetupPlan` (zone, invalidation, stop, structural target), watch timeout and run-away invalidation, cooldown and anchor de-duplication, single slot, sizing and leverage ladder, TP1/TP2/breakeven/structural trail/time cap, fees, slippage, funding, mark-price liquidation.",
        '- Implementation: one `if self.entry_mode == "ZONE_ENTRY"` branch in `EpisodeManager.step`, covered by `tests/test_state_machine.py`.',
        "",
        "## 4. PIT audit",
        "",
        f"- Visibility: {res.pit['visibility_rule']}. Zone: {res.pit['zone_definition']}.",
        f"- Deterministic rerun: CONTROL {_n(res.pit['control_deterministic'])}, ZONE_ENTRY {_n(res.pit['variant_deterministic'])}.",
        f"- Truncation audit (ZONE_ENTRY): decisions up to {res.pit['variant_truncation']['cut'][:16]} identical with later data removed: {_n(res.pit['variant_truncation']['identical'])} ({res.pit['variant_truncation']['rows_compared']} rows).",
        "- Data, resampling oracle and venue incidents: unchanged from the Phase 2 report (same ingested series, same hashes).",
        "",
    ]
    # 5 overall
    cr = _arm_rows("CONTROL", c.metrics, c.account, c.costs, c.result.manifest["n_episodes"])
    vr = _arm_rows("ZONE_ENTRY", v.metrics, v.account, v.costs, v.result.manifest["n_episodes"])
    lines += [
        "## 5. Overall CONTROL vs ZONE_ENTRY (combined 2022-01 -> 2024-12)",
        "",
        *_compare_block(cr, vr),
    ]
    # 6/7 segments
    for i, sn in enumerate(seg_names):
        cs, vs = c.segments[sn], v.segments[sn]
        n_ep_c = sum(d["len"] for d in cs["episode_outcomes"]) or 0
        n_ep_v = sum(d["len"] for d in vs["episode_outcomes"]) or 0
        lines += [
            f"## {6 + i}. {sn} comparison",
            "",
            *_compare_block(
                _arm_rows("CONTROL", cs["metrics"], cs["account"], cs["costs"], n_ep_c),
                _arm_rows("ZONE_ENTRY", vs["metrics"], vs["account"], vs["costs"], n_ep_v),
            ),
        ]
        lines += _fam_compare(cs["families"], vs["families"], f"Families in {sn}")
    # 8 matched
    mt = res.matched
    lines += [
        "## 8. Matched-episode analysis (same detection in both arms: key = family + detection time)",
        "",
        f"- Episodes: CONTROL {mt['control_episodes']}, ZONE_ENTRY {mt['variant_episodes']}, matched {mt['matched']}, CONTROL-only {mt['control_only']}, ZONE_ENTRY-only {mt['variant_only']} (arm-only episodes arise because an earlier entry occupies the single slot and shifts later detections).",
        "",
        "Outcome transition for matched episodes (CONTROL outcome -> ZONE_ENTRY outcome):",
        "",
        *_table(
            ["CONTROL", "ZONE_ENTRY", "n"],
            [
                [d["outcome_class"], d["outcome_class_v"], str(d["len"])]
                for d in mt["transition_matrix"]
            ],
        ),
    ]
    if mt.get("variant_trades_by_control_outcome"):
        lines += [
            "ZONE_ENTRY trades decomposed by what CONTROL did on the same episode (population check):",
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
    if bt:
        lines += [
            "### Episodes traded by BOTH arms (timing effect only, identical plan)",
            "",
            *_table(
                ["metric", "CONTROL", "ZONE_ENTRY"],
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
            f"- Paired difference (ZONE_ENTRY - CONTROL) in R: mean {_n(bt['paired_mean_diff_R'])}, paired t = {_n(bt['paired_t'], 2)}, ZONE_ENTRY better in {_p(bt['frac_zone_better_R'], 0)} of pairs.",
            f"- Timing: ZONE_ENTRY entered earlier in {_p(bt['frac_zone_earlier'], 0)} of pairs, mean {_n(bt['mean_hours_earlier'], 1)} h (median {_n(bt['median_hours_earlier'], 1)} h) earlier; better entry price in {_p(bt['frac_better_price'], 0)} of pairs, mean price improvement {_p(bt['mean_price_improvement_pct'])} of entry price (positive = cheaper for longs / higher for shorts).",
            f"- Structural target reached before invalidation (plan label, identical for both arms): {_p(bt['target_first_share'], 0)} of pairs.",
            "",
            "By family (pairs):",
            "",
            *_table(
                [
                    "family",
                    "pairs",
                    "CONTROL mean R",
                    "ZONE mean R",
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
        pairs = mt["pairs"]
        if isinstance(pairs, pl.DataFrame) and pairs.height:
            lines += [
                "First 25 matched pairs (full table in `matched_pairs.parquet`):",
                "",
                *_table(
                    [
                        "family",
                        "side",
                        "CONTROL entry",
                        "ZONE entry",
                        "h earlier",
                        "price impr.",
                        "stop% C/Z",
                        "MFE R C/Z",
                        "MAE R C/Z",
                        "R C/Z",
                        "target first",
                    ],
                    [
                        [
                            r["family"].replace("_", " "),
                            r["side"],
                            _ms(r["control_entry_ms"]),
                            _ms(r["zone_entry_ms"]),
                            _n(r["hours_earlier"], 1),
                            _p(r["price_improvement_pct"]),
                            f"{_p(r['control_stop_pct'], 2)}/{_p(r['zone_stop_pct'], 2)}",
                            f"{_n(r['control_MFE_R'], 2)}/{_n(r['zone_MFE_R'], 2)}",
                            f"{_n(r['control_MAE_R'], 2)}/{_n(r['zone_MAE_R'], 2)}",
                            f"{_n(r['control_R'], 2)}/{_n(r['zone_R'], 2)}",
                            "yes" if r["path_outcome"] == "target_first" else "no",
                        ]
                        for r in pairs.head(25).to_dicts()
                    ],
                ),
            ]
    # 9 recovery
    rc = res.recovery
    lines += [
        "## 9. Never-triggered recovery (Phase 2 CONTROL episodes classified NEVER_TRIGGERED)",
        "",
        f"- CONTROL never-triggered episodes: {rc['control_never_triggered']} (mean signed 24h fwd return {_p(rc['control_fwd_24h_mean_all'])}, target-before-invalidation {_p(rc['control_target_first_share_all'], 0)}); matched in ZONE_ENTRY: {rc['matched_in_variant']}.",
        "- ZONE_ENTRY outcome for those episodes: "
        + ", ".join(f"{d['outcome_class']}={d['len']}" for d in rc["variant_outcomes"])
        + ".",
        "",
    ]
    rt = rc.get("recovered_trades", {})
    if rt.get("n"):
        lines += [
            *_table(
                [
                    "recovered trades",
                    "n",
                    "win",
                    "exp. R net",
                    "exp. R gross",
                    "median R",
                    "PF",
                    "MFE R",
                    "MAE R",
                    "target first",
                    "fees+slip / trade",
                    "funding / trade",
                    "sum P&L",
                ],
                [
                    [
                        "all",
                        str(rt["n"]),
                        _p(rt["win_rate"], 0),
                        _n(rt["expectancy_R"]),
                        _n(rc.get("recovered_expectancy_R_gross")),
                        _n(rt["median_R"]),
                        _n(rt["profit_factor"], 2),
                        _n(rt["mean_MFE_R"], 2),
                        _n(rt["mean_MAE_R"], 2),
                        _p(rt["target_first_share"], 0),
                        _n(rt["fees_plus_slippage_per_trade"], 2),
                        _n(rt["funding_per_trade"], 2),
                        _n(rt["sum_pnl"], 0),
                    ]
                ],
            ),
            f"Mean signed 24h forward return of the recovered episodes (from the plan label): {_p(rc.get('recovered_fwd_24h_mean'))}.",
            "",
            "By family:",
            "",
            *_table(
                ["family", "n", "win", "exp. R", "PF", "target first", "sum P&L"],
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
                    for d in rc["recovered_by_family"]
                ],
            ),
            "By CONTROL end reason:",
            "",
            *_table(
                ["CONTROL end reason", "n", "win", "exp. R", "PF", "sum P&L"],
                [
                    [
                        d["control_end_reason"],
                        str(d["n"]),
                        _p(d["win_rate"], 0),
                        _n(d["expectancy_R"]),
                        _n(d["profit_factor"], 2),
                        _n(d["sum_pnl"], 0),
                    ]
                    for d in rc["recovered_by_control_end_reason"]
                ],
            ),
        ]
    else:
        lines += ["No never-triggered episode became a trade under ZONE_ENTRY.", ""]
    # 10 families
    lines += [
        "## 10. Family-level results (combined; no family disabled)",
        "",
        *_fam_compare(c.families, v.families, "All families, 2022-01 -> 2024-12")[2:],
    ]
    # 11 sides
    rows = []
    for side in ("LONG", "SHORT"):
        cs_, vs_ = c.metrics["by_side"].get(side, {}), v.metrics["by_side"].get(side, {})
        rows.append([side, _side_cell(cs_), _side_cell(vs_)])
    lines += ["## 11. LONG vs SHORT", "", *_table(["side", "CONTROL", "ZONE_ENTRY"], rows)]
    for sn in seg_names:
        rows = []
        for side in ("LONG", "SHORT"):
            cs_ = c.segments[sn]["metrics"]["by_side"].get(side, {})
            vs_ = v.segments[sn]["metrics"]["by_side"].get(side, {})
            rows.append([side, _side_cell(cs_), _side_cell(vs_)])
        lines += [f"{sn}:", "", *_table(["side", "CONTROL", "ZONE_ENTRY"], rows)]
    # 12 MFE/MAE
    co, vo = c.metrics["overall"], v.metrics["overall"]
    cte, vte = c.metrics.get("target_evaluation", {}), v.metrics.get("target_evaluation", {})
    lines += [
        "## 12. MFE / MAE",
        "",
        *_table(
            ["metric", "CONTROL", "ZONE_ENTRY"],
            [
                ["mean MFE (R)", _n(co.get("mean_MFE_R")), _n(vo.get("mean_MFE_R"))],
                ["mean MAE (R)", _n(co.get("mean_MAE_R")), _n(vo.get("mean_MAE_R"))],
                ["worst MAE (R)", _n(co.get("worst_MAE_R")), _n(vo.get("worst_MAE_R"))],
                ["realised mean winner (R)", _n(co.get("avg_win_R")), _n(vo.get("avg_win_R"))],
                *[
                    [
                        f"counterfactual {k.replace('hit_rate_', '')} reached before initial stop",
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
    # 13 costs
    cc, vc = c.costs, v.costs
    lines += [
        "## 13. Fees / slippage / funding impact (USDT, combined)",
        "",
        *_table(
            ["component", "CONTROL", "ZONE_ENTRY"],
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
                [
                    "total cost (slippage + fees - funding)",
                    _n(
                        (cc.get("slippage", 0) or 0) * -1
                        + (cc.get("fees", 0) or 0) * -1
                        - (cc.get("funding", 0) or 0),
                        0,
                    ),
                    _n(
                        (vc.get("slippage", 0) or 0) * -1
                        + (vc.get("fees", 0) or 0) * -1
                        - (vc.get("funding", 0) or 0),
                        0,
                    ),
                ],
            ],
        ),
    ]
    # 14 account
    ca, va = c.account, v.account
    lines += [
        "## 14. Account return / drawdown (sequential, one position, fixed research equity for sizing)",
        "",
        *_table(
            ["metric", "CONTROL", "ZONE_ENTRY"],
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
                [
                    "median hours between entries",
                    _n(ca.get("median_hours_between_entries"), 1),
                    _n(va.get("median_hours_between_entries"), 1),
                ],
            ],
        ),
    ]
    # 15 null
    lines += [
        "## 15. Null benchmark comparison (K random geometry-matched entries per trade; time- and regime-matched)",
        "",
        *_table(
            [
                "series",
                "n",
                "mean R",
                "win rate",
                "strategy - null (R)",
                "z vs replicate means",
                "P(null >= strategy)",
            ],
            [*_null_rows("CONTROL", c.null), *_null_rows("ZONE_ENTRY", v.null)],
        ),
    ]
    for sn in seg_names:
        lines += [
            f"{sn}:",
            "",
            *_table(
                [
                    "series",
                    "n",
                    "mean R",
                    "win rate",
                    "strategy - null (R)",
                    "z",
                    "P(null >= strat)",
                ],
                [
                    *_null_rows("CONTROL", c.segments[sn]["null"]),
                    *_null_rows("ZONE_ENTRY", v.segments[sn]["null"]),
                ],
            ),
        ]
    # 16 uncertainty
    lines += ["## 16. Uncertainty", ""]
    lines += _uncertainty(res)
    # 17 recommendation
    lines += ["", "## 17. Recommendation (not implemented)", ""]
    lines += _recommendation(res)
    lines += [
        "",
        "## Appendix — frozen configuration (CONTROL; ZONE_ENTRY differs only in `experiment.entry_mode`)",
        "",
        "```yaml",
        m["config_yaml"].strip(),
        "```",
        "",
    ]
    return "\n".join(lines)


def _se(o: dict[str, Any]) -> float:
    n, sd = o.get("n", 0), o.get("std_R")
    return float(sd) / math.sqrt(n) if n and sd and not math.isnan(float(sd)) else math.nan


def _uncertainty(res: Phase21Result) -> list[str]:
    c, v = res.control, res.variant
    co, vo = c.metrics["overall"], v.metrics["overall"]
    lines = [
        f"- Standard error of mean R: CONTROL {_n(_se(co))} (n={co.get('n', 0)}), ZONE_ENTRY {_n(_se(vo))} (n={vo.get('n', 0)}). Approximate 95% intervals: CONTROL [{_n(co.get('expectancy_R', 0) - 1.96 * _se(co))}, {_n(co.get('expectancy_R', 0) + 1.96 * _se(co))}], ZONE_ENTRY [{_n(vo.get('expectancy_R', 0) - 1.96 * _se(vo))}, {_n(vo.get('expectancy_R', 0) + 1.96 * _se(vo))}]. The intervals overlap heavily; neither arm is distinguishable from zero expectancy at conventional levels.",
    ]
    bt = res.matched.get("both_traded")
    if bt:
        lines.append(
            f"- The paired comparison on {bt['n']} shared episodes is the cleanest timing estimate: mean difference {_n(bt['paired_mean_diff_R'])}R, paired t = {_n(bt['paired_t'], 2)}."
        )
    for sn in [s.name for s in res.segments]:
        so_c, so_v = c.segments[sn]["metrics"]["overall"], v.segments[sn]["metrics"]["overall"]
        lines.append(
            f"- {sn}: CONTROL {_n(so_c.get('expectancy_R'))}R (n={so_c.get('n', 0)}, t={_n(so_c.get('t_stat_R'), 2)}) vs ZONE_ENTRY {_n(so_v.get('expectancy_R'))}R (n={so_v.get('n', 0)}, t={_n(so_v.get('t_stat_R'), 2)})."
        )
    lines += [
        "- Per-family cells are small (most n < 60); family-level differences are directional evidence only.",
        "- The two arms share the same data, costs and exit engine, so differences are not data noise, but a single 3-year window of one instrument cannot establish generality.",
        "- The forward labels and the null benchmark are themselves estimated on the same window (no out-of-sample confirmation yet).",
    ]
    return lines


def _recommendation(res: Phase21Result) -> list[str]:
    c, v = res.control, res.variant
    co, vo = c.metrics["overall"], v.metrics["overall"]
    ca, va = c.account, v.account
    bt = res.matched.get("both_traded") or {}
    rt = res.recovery.get("recovered_trades", {})
    net_c, net_v = co.get("expectancy_R", 0.0), vo.get("expectancy_R", 0.0)
    dd_c, dd_v = (
        abs(ca.get("max_drawdown_frac_trade_curve", 0.0) or 0.0),
        abs(va.get("max_drawdown_frac_trade_curve", 0.0) or 0.0),
    )
    segs_ok = all(
        (v.segments[s]["metrics"]["overall"].get("expectancy_R") or 0) > 0 for s in v.segments
    )
    both_segs_better = all(
        (v.segments[s]["metrics"]["overall"].get("expectancy_R") or 0)
        > (c.segments[s]["metrics"]["overall"].get("expectancy_R") or 0)
        for s in v.segments
    )
    rec_ok = bool(rt.get("n")) and (rt.get("expectancy_R") or 0) > 0
    stronger = net_v > net_c + 0.05
    dd_ok = dd_v <= dd_c * 1.25 + 0.01
    criteria = [
        (
            "materially stronger NET expectancy (> +0.05R over CONTROL)",
            stronger,
            f"{_n(net_c)} -> {_n(net_v)}",
        ),
        (
            "no unacceptable drawdown increase (<= 1.25x CONTROL + 1pt)",
            dd_ok,
            f"{_p(dd_c)} -> {_p(dd_v)}",
        ),
        (
            "positive and better than CONTROL in BOTH segments",
            segs_ok and both_segs_better,
            "; ".join(
                f"{s}: {_n(c.segments[s]['metrics']['overall'].get('expectancy_R'))} -> {_n(v.segments[s]['metrics']['overall'].get('expectancy_R'))}"
                for s in v.segments
            ),
        ),
        (
            "recovered never-triggered setups have positive net expectancy",
            rec_ok,
            f"n={rt.get('n', 0)}, R={_n(rt.get('expectancy_R'))}",
        ),
        (
            "timing effect on shared episodes is positive",
            (bt.get("paired_mean_diff_R") or 0) > 0,
            f"paired diff {_n(bt.get('paired_mean_diff_R'))}R (t={_n(bt.get('paired_t'), 2)}) on {bt.get('n', 0)} pairs",
        ),
    ]
    lines = [
        "Pre-declared success criteria (set in the hypothesis, not after the results):",
        "",
        *_table(
            ["criterion", "met", "evidence"],
            [[k, "yes" if ok else "no", ev] for k, ok, ev in criteria],
        ),
    ]
    n_met = sum(1 for _, ok, _ in criteria if ok)
    if n_met == len(criteria):
        verdict = "H1 SUPPORTED on this window. Recommended next step: freeze ZONE_ENTRY as the new pre-registered baseline candidate and run the single confirmatory test on 2025+ only after owner approval. No other change."
    elif n_met >= 3:
        verdict = "H1 PARTIALLY SUPPORTED. The variant improves some pre-declared criteria but not all; it must not be adopted on this evidence. Recommended next step: owner review of which criterion failed and why (sections 8, 9, 13), then either (a) stop here, or (b) pre-register a second, narrower hypothesis derived from the matched analysis (for example confirmation-free entry only where the paired timing effect is positive) for one more run on 2022-2024. No parameter search, no family dropped."
    else:
        verdict = "H1 NOT SUPPORTED. Entering at the zone without confirmation does not produce materially stronger net expectancy on this window. The confirmation trigger is not the main source of the missing edge; the never-triggered population's forward returns are not converted into realised R once stops, exits and costs apply. Recommended next step: stop changing entry mechanics; the remaining pre-registered candidates from Phase 2 are the exit design (section 12: realised winner vs MFE) and the regime eligibility of the SHORT families. Any of them needs a new single pre-registered hypothesis and owner approval."
    lines += ["", f"- **Verdict: {verdict}**"]
    lines += ["- 2025+ stays untouched. No live trading. No tuning."]
    return lines
