"""Render reports/BTC_SWING_V1_PHASE2_VALIDATION.md (21 sections) from a Phase2Result."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from btc_swing.research.phase2 import Phase2Result


def _p(v: Any, nd: int = 2) -> str:
    """Percent from a fraction."""
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "n/a"
    return f"{float(v) * 100:.{nd}f}%"


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


def _fl(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return [*out, ""]


def _summary_rows(cells: dict[str, dict[str, Any]], min_n: int) -> list[list[str]]:
    rows: list[list[str]] = []
    for k, s in cells.items():
        if s.get("n", 0) == 0:
            rows.append([k, "0", "", "", "", "", "", "", "no"])
            continue
        rows.append(
            [
                k,
                str(s["n"]),
                _p(s["win_rate"], 1),
                _n(s["expectancy_R"]),
                _p(s["expectancy_account_pct"]),
                _n(s["profit_factor"], 2),
                _n(s["median_R"]),
                _n(s["mean_holding_hours"], 1),
                "yes" if s["n"] >= min_n else "no",
            ]
        )
    return rows


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


def render_phase2(res: Phase2Result) -> str:
    m = res.manifest
    ma = res.metrics_all
    o = ma["overall"]
    acct = res.account
    min_n = int(ma.get("min_cell_n", 20))
    seg_names = list(res.segments)
    lines: list[str] = []
    lines += [
        "# BTC Swing V1 — Phase 2 validation (frozen pre-registered defaults)",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · strategy `{m['strategy_name']}` {m['strategy_version']} · "
        f"config `{m['config_hash'][:12]}` · code `{m['code_version']}` · result hash `{m['result_hash'][:12]}` · deterministic rerun: {_n(m['deterministic'])}",
        "",
        "**Central question.** With the pre-registered strategy and realistic costs/risk controls, does this BTC 5-minute-scanned, "
        "multi-timeframe LONG/SHORT swing engine show repeatable positive expectancy before any optimisation?",
        "",
        "**Scope.** Paper/backtest only. No live trading, no authenticated exchange access, no real money. All thresholds are the frozen V1 "
        "defaults plus the pre-registered Phase 2 defaults for the six new families (set before any run on these data). Nothing was tuned, "
        "no family was dropped. Development/first validation 2022-01-01 -> 2023-12-31; chronological validation 2024-01-01 -> 2024-12-31; "
        "2025 onward untouched.",
        "",
    ]
    # 1 data coverage
    lines += [
        "## 1. Data coverage",
        "",
        "Source: Binance Vision public archive (USDT-margined perpetual BTCUSDT), checksum-verified zip files, immutable raw store.",
        "",
    ]
    rows = []
    for k, v in res.coverage.items():
        rows.append(
            [
                k,
                str(v.get("rows", 0)),
                str(v.get("first", ""))[:16],
                str(v.get("last", ""))[:16],
                _n(v.get("coverage_pct"), 2) + ("%" if "coverage_pct" in v else ""),
            ]
        )
    lines += _table(["dataset", "rows", "first", "last", "coverage in study window"], rows)
    # 2 PIT audit
    pa = res.pit_audit
    lines += [
        "## 2. PIT audit",
        "",
        f"- Visibility rule: {pa['visibility_rule']}",
        f"- Look-ahead guard: {pa['look_ahead_guard']}",
        f"- Deterministic rerun (identical result hash): {_n(pa['determinism_identical'])}",
        f"- Truncation audit: decision journal up to {pa['truncation']['cut'][:16]} identical with data after the cut removed: {_n(pa['truncation']['identical'])} ({pa['truncation']['rows_compared']} rows)",
        f"- Liquidation evaluated on: {pa['liquidation_basis']} price",
        "",
        "Resampling oracle (5m -> higher timeframes vs Binance native bars):",
        "",
    ]
    rows = [
        [
            tf,
            str(v["bars_compared"]),
            str(v["bars_differing"]),
            _n(v["exact_match_pct"], 3) + "%",
            ", ".join(x[:16] for x in v["differing_times"][:6]),
        ]
        for tf, v in pa["resample_oracle"].items()
    ]
    lines += _table(
        [
            "timeframe",
            "bars compared",
            "differing",
            "exact match",
            "first differing bars (venue incidents)",
        ],
        rows,
    )
    # 3 regime distribution
    lines += ["## 3. Regime distribution (5m bars)", ""]
    tot = max(sum(m["regime_bar_counts"].values()), 1)
    rows = []
    for reg, cnt in m["regime_bar_counts"].items():
        seg_cells = []
        for sn in seg_names:
            sc = {d["regime"]: d["len"] for d in res.segments[sn]["regime_bar_counts"]}
            st = max(sum(sc.values()), 1)
            seg_cells.append(_p(sc.get(reg, 0) / st, 1))
        rows.append([reg, str(cnt), _p(cnt / tot, 1), *seg_cells])
    lines += _table(
        ["regime", "bars (all)", "share (all)", *[f"share {s}" for s in seg_names]], rows
    )
    # 4 setup counts
    ep = res.episodes_labelled
    lines += [
        "## 4. Setup counts (every episode recorded: WATCH, ENTRY_READY, TRIGGERED, INVALIDATED, EXPIRED/NEVER_TRIGGERED)",
        "",
    ]
    if ep.height:
        import polars as pl

        piv = ep.group_by("family", "outcome_class").len().sort("family", "outcome_class")
        fams = sorted(ep["family"].unique().to_list())
        classes = ["TRADED", "INVALIDATED", "NEVER_TRIGGERED", "RISK_REJECTED"]
        rows = []
        for f in fams:
            d = {r["outcome_class"]: r["len"] for r in piv.filter(pl.col("family") == f).to_dicts()}
            tot_f = sum(d.values())
            er = ep.filter(pl.col("family") == f)["reached_entry_ready"].cast(pl.Float64).mean()
            exp_ = ep.filter(pl.col("family") == f)["expired"].cast(pl.Float64).mean()
            rows.append(
                [f, str(tot_f), *[str(d.get(c, 0)) for c in classes], _p(er, 0), _p(exp_, 0)]
            )
        lines += _table(
            ["family", "episodes", *classes, "reached ENTRY_READY", "expired (watch timeout)"], rows
        )
        sh = res.baseline.shadow
        if sh.height:
            lines += [
                f"Shadow detections (a setup fired while the single slot was occupied by another episode or an open position): {sh.height}. By family: "
                + ", ".join(
                    f"{r['family']}={r['len']}"
                    for r in sh.group_by("family").len().sort("family").to_dicts()
                ),
                "",
            ]
    else:
        lines += ["No episodes.", ""]
    # 5 entry frequency
    fq = ma["frequency"]
    lines += [
        "## 5. Entry frequency (naturally produced, no quota)",
        "",
        *_table(
            ["metric", "value"],
            [
                ["setup episodes / day", _n(fq["setups_per_day"])],
                ["trades / day", _n(acct.get("trades_per_day"))],
                ["trades / week", _n(acct.get("trades_per_week"))],
                ["trades / month", _n(acct.get("trades_per_month"))],
                ["median hours between entries", _n(acct.get("median_hours_between_entries"), 1)],
                ["median holding (h)", _n(o.get("median_holding_hours"), 1)],
                [
                    "simultaneously active trades (max)",
                    str(acct.get("max_simultaneous_positions", "n/a")),
                ],
                ["setup -> entry conversion", _p(fq["setup_to_entry_conversion"], 1)],
            ],
        ),
    ]
    # 6 overall
    lines += [
        "## 6. Overall performance (combined 2022-01 -> 2024-12, sequential single-position account)",
        "",
    ]
    if o.get("n", 0):
        lines += _table(
            ["metric", "value"],
            [
                ["trades", str(o["n"])],
                ["win rate", _p(o["win_rate"], 1)],
                [
                    "expectancy (R)",
                    f"**{_n(o['expectancy_R'])}** (t = {_n(o['t_stat_R'], 2)}, sd {_n(o['std_R'])})",
                ],
                ["expectancy (account)", _p(o["expectancy_account_pct"])],
                ["profit factor", _n(o["profit_factor"], 2)],
                ["average winner / loser (R)", f"{_n(o['avg_win_R'])} / {_n(o['avg_loss_R'])}"],
                [
                    "average winner / loser (account)",
                    f"{_p(o['avg_win_account_pct'])} / {_p(o['avg_loss_account_pct'])}",
                ],
                ["median R", _n(o["median_R"])],
                ["return on underlying (sum signed BTC_RETURN)", _p(o["sum_BTC_RETURN"])],
                ["return on margin (mean per trade)", _p(o["mean_RETURN_ON_MARGIN"])],
                ["return on account (sum)", _p(o["sum_ACCOUNT_RETURN"])],
            ],
        )
    else:
        lines += ["No trades.", ""]
    # 7 long vs short
    lines += [
        "## 7. LONG vs SHORT",
        "",
        *_table(SUMMARY_HEADERS, _summary_rows(ma["by_side"], min_n)),
    ]
    # 8 families
    lines += ["## 8. Setup-family performance (all eight families shown; none hidden)", ""]
    rows = [
        [
            f["family"],
            f["side"],
            str(f["n"]),
            _p(f["win_rate"], 1),
            _n(f["expectancy_R"]),
            _n(f["profit_factor"], 2),
            _n(f["median_R"]),
            _n(f["mean_MFE_R"]),
            _n(f["mean_MAE_R"]),
            _n(f["median_holding_hours"], 1),
            _p(f["sum_account_return"]),
            "yes" if f["reliable"] else "no",
        ]
        for f in res.families
    ]
    lines += _table(
        [
            "family",
            "side",
            "n",
            "win rate",
            "exp. R",
            "PF",
            "median R",
            "MFE R",
            "MAE R",
            "median hold h",
            "sum acct ret",
            "reliable",
        ],
        rows,
    )
    dd = acct.get("drawdown_window_family_contribution", [])
    if dd:
        lines += [
            "Contribution to the maximum drawdown window (trade-sequence equity curve, peak to trough): "
            + ", ".join(
                f"{d['family']} {d['pnl_in_dd_window']:+.0f} USDT ({d['n']} trades)" for d in dd
            ),
            "",
        ]
    # 9 regimes
    lines += [
        "## 9. Regime performance (regime at entry)",
        "",
        *_table(SUMMARY_HEADERS, _summary_rows(ma["by_regime"], min_n)),
    ]
    if ep.height:
        rows = []
        for reg in sorted(ep["regime_at_detection"].unique().to_list()):
            sub = ep.filter(pl.col("regime_at_detection") == reg)
            rows.append(
                [
                    reg,
                    str(sub.height),
                    _p(
                        sub.filter(pl.col("outcome_class") == "TRADED").height / max(sub.height, 1),
                        0,
                    ),
                ]
            )
        lines += [
            "Episodes by regime at detection and share that became trades:",
            "",
            *_table(["regime", "episodes", "traded"], rows),
        ]
    # 10 MFE/MAE
    if o.get("n", 0):
        lines += [
            "## 10. MFE / MAE",
            "",
            *_table(
                ["metric", "value"],
                [
                    [
                        "mean MFE (R) / mean MAE (R)",
                        f"{_n(o['mean_MFE_R'])} / {_n(o['mean_MAE_R'])}",
                    ],
                    ["worst MAE (R)", _n(o["worst_MAE_R"])],
                    [
                        "realised mean win (R) vs mean MFE (R)",
                        f"{_n(o['avg_win_R'])} vs {_n(o['mean_MFE_R'])} (exit design question, not tuned)",
                    ],
                ],
            ),
        ]
        te = ma["target_evaluation"]
        lines += [
            "Counterfactual target reach before the initial stop (fixed stop, no trailing):",
            "",
            *_table(
                ["target", "hit rate"],
                [
                    [k.replace("hit_rate_", ""), _p(v, 1)]
                    for k, v in te.items()
                    if k.startswith("hit_rate_")
                ]
                + [
                    ["TP1 executed", _p(te["tp1_hit_rate"], 1)],
                    ["TP2 executed", _p(te["tp2_hit_rate"], 1)],
                    ["structural target median distance (R)", _n(te["structural_target_median_R"])],
                ],
            ),
        ]
        # 11 R distribution
        rd = ma["r_distribution"]
        lines += [
            "## 11. R-multiple distribution",
            "",
            *_table(
                ["bin", "count"],
                [[b, str(c)] for b, c in zip(rd["bins"], rd["counts"], strict=True)],
            ),
            "quantiles: "
            + ", ".join(f"q{int(q * 100)}={v:.2f}" for q, v in rd["quantiles"].items()),
            "",
        ]
        # 12 holding
        ht = ma["holding_time"]
        lines += [
            "## 12. Holding-period distribution",
            "",
            *_table(
                ["metric", "value"],
                [
                    [
                        "mean / median / p90 / max (h)",
                        f"{_n(ht['mean_hours'], 1)} / {_n(ht['median_hours'], 1)} / {_n(ht['p90_hours'], 1)} / {_n(ht['max_hours'], 1)}",
                    ],
                    ["< 1h", _p(ht["frac_under_1h"], 1)],
                    ["1h - 1d", _p(ht["frac_1h_to_1d"], 1)],
                    ["1d - 3d", _p(ht["frac_1d_to_3d"], 1)],
                    ["> 3d", _p(ht["frac_over_3d"], 1)],
                ],
            ),
        ]
        # 13 costs
        c = res.costs
        lines += [
            "## 13. Fees / slippage / funding impact (USDT, combined period)",
            "",
            *_table(
                ["component", "value"],
                [
                    ["gross P&L before slippage", _n(c["gross_before_slippage"], 2)],
                    ["slippage", _n(c["slippage"], 2)],
                    ["fees", _n(c["fees"], 2)],
                    ["funding (received positive)", _n(c["funding"], 2)],
                    ["net P&L", _n(c["net"], 2)],
                    [
                        "expectancy R before costs / net",
                        f"{_n(c['expectancy_R_before_costs'])} / {_n(c['expectancy_R_net'])}",
                    ],
                    ["cost drag per trade (R)", _n(c["cost_drag_R_per_trade"])],
                    ["fees as % of gross", _n(c["fees_pct_of_gross"], 1) + "%"],
                    ["funding events while in position", str(c["n_funding_events"])],
                ],
            ),
        ]
    # 14 leverage
    lines += [
        "## 14. Leverage comparison (same account-risk methodology; max leverage capped, ladder truncated; margin cap 25% of equity)",
        "",
    ]
    rows = [
        [
            f"{r['max_leverage']:g}x",
            str(r["n_trades"]),
            str(r["n_risk_rejected"]),
            _n(r["pct_trades_full_risk"], 1) + "%",
            _n(r["mean_leverage_used"], 2),
            _n(r["mean_margin_pct_equity"], 1) + "%",
            _n(r["mean_account_risk_pct"], 3) + "%",
            _n(r["min_stop_to_liq_ratio"], 1),
            _n(r["min_liq_distance_pct"], 1) + "%",
            str(r["liquidations"]),
            _n(r["expectancy_R"]),
            _n(r["total_return_pct"], 2) + "%",
            _n(r["max_drawdown_pct"], 2) + "%",
            _n(r["mean_RETURN_ON_MARGIN_pct"], 2) + "%",
        ]
        for r in res.leverage
    ]
    lines += _table(
        [
            "max lev",
            "trades",
            "risk-rejected",
            "sized at full risk",
            "mean lev used",
            "mean margin/equity",
            "mean acct risk",
            "min stop:liq",
            "min liq dist",
            "liq.",
            "exp. R",
            "total return",
            "max DD",
            "mean RoM",
        ],
        rows,
    )
    lines += [
        "Reading: the risk per trade is identical across rows; leverage only changes how much margin a given position needs. "
        "A lower cap forces tight-stop setups to be sized below target risk (fewer trades at full risk), a higher cap uses less margin but moves liquidation closer.",
        "",
    ]
    # 15 liquidation safety
    lr = ma.get("leverage_risk", {})
    if lr:
        lines += [
            "## 15. Liquidation safety (mark-price based where mark data exists)",
            "",
            *_table(
                ["metric", "value"],
                [
                    [
                        "leverage used",
                        ", ".join(
                            f"{d['leverage']:g}x={d['len']}" for d in lr["leverage_distribution"]
                        ),
                    ],
                    ["min stop-to-liquidation ratio", _n(lr["min_stop_to_liquidation_ratio"], 1)],
                    [
                        "min liquidation distance (% / ATR 4h)",
                        f"{_p(lr['min_liquidation_distance_pct'])} / {_n(lr['min_liquidation_distance_atr'], 1)}",
                    ],
                    ["worst MAE (% / R)", f"{_p(lr['worst_MAE_pct'])} / {_n(lr['worst_MAE_R'])}"],
                    ["worst single-trade account loss", _p(lr["max_account_loss_single_trade"])],
                    [
                        "max planned loss at stop (account)",
                        _p(lr["max_planned_account_loss_at_stop"]),
                    ],
                    [
                        "max loss if liquidated (margin/account)",
                        _p(lr["max_account_loss_if_liquidated"]),
                    ],
                    ["liquidations", str(lr["n_liquidations"])],
                    ["trades sized below target risk", str(lr["n_risk_capped"])],
                ],
            ),
        ]
    # 16 account
    lines += [
        "## 16. Account equity / drawdown (sequential, one position at a time, fixed research equity for sizing)",
        "",
        *_table(
            ["metric", "value"],
            [
                [
                    "initial -> final equity",
                    f"{_n(acct.get('initial_equity'), 0)} -> {_n(acct.get('final_equity'), 2)}",
                ],
                ["total return", _p(acct.get("total_return"))],
                ["CAGR", _p(acct.get("cagr"))],
                [
                    "max drawdown (trade curve / daily mark-to-market)",
                    f"{_p(acct.get('max_drawdown_frac_trade_curve'))} / {_p(acct.get('max_drawdown_frac_daily_mtm'))}",
                ],
                [
                    "Sharpe / Sortino (daily marks, annualised; most days flat)",
                    f"{_n(acct.get('sharpe_daily_annualised'), 2)} / {_n(acct.get('sortino_daily_annualised'), 2)}",
                ],
                [
                    "longest losing / winning streak (trades)",
                    f"{acct.get('longest_losing_streak', 'n/a')} / {acct.get('longest_winning_streak', 'n/a')}",
                ],
                ["overlapping trades", str(acct.get("overlapping_trade_pairs", "n/a"))],
            ],
        ),
    ]
    # 17 null
    nl = res.null
    lines += [
        "## 17. Null benchmark comparison",
        "",
        "Each real trade is matched with K random entries of the same side, stop distance (%), setup ATR and risk sizing, run through the identical exit "
        "engine with identical costs: (a) time-matched = uniform over the study window, (b) regime-matched = uniform over bars in the trade's regime at entry. "
        "A cost-free raw BTC return over the matched holding time is also shown.",
        "",
    ]
    if nl.get("n_trades", 0):
        st = nl["strategy"]
        rows = [
            [
                "strategy",
                str(nl["n_trades"]),
                _n(st["mean_R"]),
                _p(st["win_rate"], 1),
                _p(st["mean_BTC_RETURN"]),
                "",
                "",
                "",
            ]
        ]
        for v in ("time", "regime"):
            nd = nl.get(v)
            if nd:
                rows.append(
                    [
                        f"null ({v}-matched, K={nl['k']})",
                        str(nd["n_samples"]),
                        _n(nd["null_mean_R"]),
                        _p(nd["null_win_rate"], 1),
                        _p(nd["null_mean_BTC_RETURN"]),
                        _p(nd["null_mean_raw_btc_matched_hold"]),
                        _n(nd["strategy_minus_null_mean_R"]),
                        f"z={_n(nd['z_vs_null_reps'], 2)}, P(null >= strat)={_p(nd['frac_reps_with_mean_R_ge_strategy'], 0)}",
                    ]
                )
        lines += _table(
            [
                "series",
                "n",
                "mean R",
                "win rate",
                "mean BTC_RETURN",
                "raw BTC ret (matched hold)",
                "strategy - null (R)",
                "significance vs replicate means",
            ],
            rows,
        )
        for sn in seg_names:
            sd = res.segments[sn]["null"]
            if sd.get("n_trades", 0) and sd.get("time"):
                lines.append(
                    f"- {sn}: strategy mean R {_n(sd['strategy']['mean_R'])} vs time-null {_n(sd['time']['null_mean_R'])} (z={_n(sd['time']['z_vs_null_reps'], 2)}) / regime-null {_n(sd.get('regime', {}).get('null_mean_R'))} (z={_n(sd.get('regime', {}).get('z_vs_null_reps'), 2)})"
                )
        lines.append("")
    # 18 rejected / never triggered
    lines += [
        "## 18. Rejected / never-triggered setup analysis (forward labels from the detection close, signed by side)",
        "",
    ]
    if ep.height:
        rows = []
        for oc in ["TRADED", "INVALIDATED", "NEVER_TRIGGERED", "RISK_REJECTED"]:
            sub = ep.filter(pl.col("outcome_class") == oc)
            if not sub.height:
                continue
            po = sub.group_by("path_outcome").len().to_dicts()
            pod = {r["path_outcome"]: r["len"] for r in po}
            n_valid = sum(
                v for k, v in pod.items() if k in ("target_first", "invalidation_first", "neither")
            )
            rows.append(
                [
                    oc,
                    str(sub.height),
                    _p(sub["fwd_ret_4h"].mean()),
                    _p(sub["fwd_ret_24h"].mean()),
                    _p(sub["fwd_ret_72h"].mean()),
                    _p((sub["fwd_ret_24h"] > 0).cast(pl.Float64).mean(), 0),
                    _p(pod.get("target_first", 0) / n_valid if n_valid else float("nan"), 0),
                    _p(pod.get("invalidation_first", 0) / n_valid if n_valid else float("nan"), 0),
                ]
            )
        lines += _table(
            [
                "outcome class",
                "episodes",
                "mean fwd 4h",
                "mean fwd 24h",
                "mean fwd 72h",
                "P(fwd 24h > 0)",
                "target before invalidation",
                "invalidation before target",
            ],
            rows,
        )
        rows = []
        for f in sorted(ep["family"].unique().to_list()):
            sub = ep.filter(
                (pl.col("family") == f) & (pl.col("outcome_class") == "NEVER_TRIGGERED")
            )
            if sub.height:
                rows.append(
                    [
                        f,
                        str(sub.height),
                        _p(sub["fwd_ret_24h"].mean()),
                        _p(sub["fwd_ret_72h"].mean()),
                        ", ".join(
                            f"{r['end_reason']}={r['len']}"
                            for r in sub.group_by("end_reason").len().sort("end_reason").to_dicts()
                        ),
                    ]
                )
        lines += [
            "Never-triggered episodes by family (did the move happen without us?):",
            "",
            *_table(["family", "n", "mean fwd 24h", "mean fwd 72h", "end reasons"], rows),
        ]
    # 19 stability
    lines += ["## 19. 2022-23 vs 2024 stability", ""]
    rows = []
    for sn in seg_names:
        s = res.segments[sn]
        oo = s["metrics"]["overall"]
        a = s["account"]
        rows.append(
            [
                sn,
                f"{s['start']} -> {s['end']}",
                str(oo.get("n", 0)),
                _p(oo.get("win_rate"), 1),
                _n(oo.get("expectancy_R")),
                _n(oo.get("t_stat_R"), 2),
                _n(oo.get("profit_factor"), 2),
                _p(a.get("total_return")),
                _p(a.get("max_drawdown_frac_trade_curve")),
                _n(a.get("sharpe_daily_annualised"), 2),
            ]
        )
    lines += _table(
        [
            "segment",
            "period",
            "trades",
            "win rate",
            "exp. R",
            "t",
            "PF",
            "return",
            "max DD",
            "Sharpe",
        ],
        rows,
    )
    fam_names = sorted({f["family"] for sn in seg_names for f in res.segments[sn]["families"]})
    rows = []
    for fn in fam_names:
        cells = [fn]
        for sn in seg_names:
            fd = next((f for f in res.segments[sn]["families"] if f["family"] == fn), None)
            cells.append(
                f"n={fd['n']}, R={_n(fd['expectancy_R'])}, PF={_n(fd['profit_factor'], 2)}"
                if fd
                else "n=0"
            )
        rows.append(cells)
    lines += ["Per family and segment:", "", *_table(["family", *seg_names], rows)]
    rows = []
    for side in ("LONG", "SHORT"):
        cells = [side]
        for sn in seg_names:
            d = res.segments[sn]["metrics"]["by_side"].get(side)
            cells.append(
                f"n={d['n']}, R={_n(d['expectancy_R'])}, PF={_n(d['profit_factor'], 2)}"
                if d and d.get("n")
                else "n=0"
            )
        rows.append(cells)
    lines += ["Per side and segment:", "", *_table(["side", *seg_names], rows)]
    # 20 limitations
    lines += [
        "## 20. Limitations",
        "",
        "- One instrument, one venue's archive; two documented venue incidents where 5m bars are stale (listed in section 2).",
        "- Single-position sequential account with fixed research equity for sizing (`compounding: false`); the account curve is additive in USDT.",
        "- Fills: next-5m-bar open plus fixed slippage; stops fill at the stop (or the gap open) plus slippage; no order-book depth model; mark price used for liquidation only.",
        "- Funding applied at the archive rate; maintenance margin rate is a single configured tier (0.5%), not the live tier ladder.",
        "- Episode slot is single: a setup detected while another episode is open is recorded as a shadow detection, not traded; family order in the config is the priority order.",
        "- Null benchmark re-uses the trade's own stop geometry; it answers 'does timing add information', not 'is BTC long/short profitable'.",
        "- Daily Sharpe/Sortino are computed on mark-to-market equity with most days flat; they are indicative only. Trade counts per cell are small; `reliable` flags mark n >= min_cell_n.",
        "- No parameter was changed after seeing results; the Phase 2 defaults for the six new families were set before the first run on these data.",
        "",
    ]
    # 21 recommendation
    lines += ["## 21. Recommendation for the next phase (not implemented)", ""]
    lines += _recommendation(res)
    lines += [
        "",
        "## Appendix — frozen configuration",
        "",
        "```yaml",
        m["config_yaml"].strip(),
        "```",
        "",
    ]
    return "\n".join(lines)


def _recommendation(res: Phase2Result) -> list[str]:
    """Rule-based verdict. Thresholds are stated in the text so the reader can disagree with them.

    A segment counts as POSITIVE when expectancy > +0.05R and t >= 1.0, NEGATIVE when expectancy
    < -0.05R, otherwise FLAT. The strategy is 'separated from the null' in a segment when both the
    time-matched and the regime-matched z-scores are >= 1.5. 'Repeatable' requires POSITIVE and
    separated in every segment.
    """
    o = res.metrics_all["overall"]
    segs = res.segments
    lines: list[str] = []
    if not o.get("n"):
        return [
            "No trades were produced; nothing can be recommended beyond re-examining detector eligibility."
        ]

    def seg_class(sn: str) -> str:
        oo = segs[sn]["metrics"]["overall"]
        e, t = oo.get("expectancy_R") or 0.0, oo.get("t_stat_R") or 0.0
        if e > 0.05 and t >= 1.0:
            return "POSITIVE"
        if e < -0.05:
            return "NEGATIVE"
        return "FLAT"

    def seg_sep(sn: str) -> bool:
        nl = segs[sn]["null"]
        zs = [nl.get(v, {}).get("z_vs_null_reps") for v in ("time", "regime")]
        return all(z is not None and not math.isnan(z) and z >= 1.5 for z in zs)

    classes = {sn: seg_class(sn) for sn in segs}
    seps = {sn: seg_sep(sn) for sn in segs}
    repeatable = all(c == "POSITIVE" for c in classes.values()) and all(seps.values())
    c = res.costs
    lines.append(
        f"- **Verdict on the central question: {'YES — repeatable positive expectancy' if repeatable else 'NOT DEMONSTRATED'}.** "
        f"Combined expectancy {o['expectancy_R']:+.3f}R (t={o['t_stat_R']:.2f}, n={o['n']}, PF {o['profit_factor']:.2f}); "
        + "; ".join(
            f"{sn}: {classes[sn]} ({segs[sn]['metrics']['overall'].get('expectancy_R', 0):+.3f}R, t={segs[sn]['metrics']['overall'].get('t_stat_R', 0):.2f}, "
            f"{'separated from' if seps[sn] else 'not separated from'} the null)"
            for sn in segs
        )
        + "."
    )
    if c:
        lines.append(
            f"- Costs: gross expectancy before slippage {c['expectancy_R_before_costs']:+.3f}R becomes {c['expectancy_R_net']:+.3f}R net; "
            f"slippage + fees - funding take {c['cost_drag_R_per_trade']:.3f}R per trade "
            f"({100 * (1 - c['expectancy_R_net'] / c['expectancy_R_before_costs']) if c['expectancy_R_before_costs'] else float('nan'):.0f}% of the gross edge)."
        )
    # family stability across segments
    seg_names = list(segs)
    fam_cells: dict[str, dict[str, dict[str, Any]]] = {}
    for sn in seg_names:
        for f in segs[sn]["families"]:
            fam_cells.setdefault(f["family"], {})[sn] = f
    stable_pos, flips, stable_neg = [], [], []
    for fam, cells in sorted(fam_cells.items()):
        if len(cells) < len(seg_names) or any(cells[sn]["n"] < 7 for sn in seg_names):
            continue
        signs = [cells[sn]["expectancy_R"] > 0 for sn in seg_names]
        desc = (
            f"{fam} ("
            + ", ".join(
                f"{sn} {cells[sn]['expectancy_R']:+.2f}R n={cells[sn]['n']}" for sn in seg_names
            )
            + ")"
        )
        if all(signs):
            stable_pos.append(desc)
        elif not any(signs):
            stable_neg.append(desc)
        else:
            flips.append(desc)
    if stable_pos:
        lines.append(
            "- Families positive in every segment (n >= 7 each): " + "; ".join(stable_pos) + "."
        )
    if flips:
        lines.append(
            "- Families whose sign flipped between segments (no evidence of a stable edge): "
            + "; ".join(flips)
            + "."
        )
    if stable_neg:
        lines.append(
            "- Families negative in every segment (reported, not removed): "
            + "; ".join(stable_neg)
            + "."
        )
    # never-triggered evidence
    ep = res.episodes_labelled
    if ep.height and "fwd_ret_24h" in ep.columns:
        import polars as pl

        tr = _fl(ep.filter(pl.col("outcome_class") == "TRADED")["fwd_ret_24h"].mean())
        nt = _fl(ep.filter(pl.col("outcome_class") == "NEVER_TRIGGERED")["fwd_ret_24h"].mean())
        lines.append(
            f"- Plan vs entry mechanics: mean signed 24h forward return from the detection close is {tr * 100:+.2f}% for traded episodes "
            f"and {nt * 100:+.2f}% for never-triggered episodes. "
            + (
                "The plans that never triggered moved further in the intended direction than the ones we entered: the 15m/5m entry mechanics, not the setup discovery, are the first thing to examine."
                if nt > tr
                else "Traded episodes outperformed never-triggered ones, so entry mechanics are not the obvious bottleneck."
            )
        )
    if repeatable:
        lines.append(
            "- Recommended next phase: a pre-registered OUT-OF-SAMPLE confirmation on the untouched 2025-01 -> 2026-09 window with the identical frozen configuration and one pre-declared hypothesis per family (sign of expectancy). No changes before that run."
        )
    else:
        lines.append(
            "- Recommended next phase (not a parameter search): pre-register ONE structural hypothesis derived from the evidence above — the candidates, in order of evidence, are "
            "(1) the entry mechanics (confirmation/trigger) versus entering at the plan's zone, measured with the existing forward labels; "
            "(2) the exit design (mean MFE vs realised winner, counterfactual R-target table); "
            "(3) the regime eligibility table for the SHORT families. Run the single pre-registered variant on 2022-2024 once, compare against this frozen baseline on the same trades/episodes, and only then decide whether a confirmatory run on 2025+ is justified. "
            "Do not drop families or tune thresholds in that step."
        )
    lines.append(
        "- In all cases: 2025+ stays untouched until a single confirmatory run is pre-registered and approved; leverage stays at the level the liquidation table supports (section 14); no live trading."
    )
    return lines
