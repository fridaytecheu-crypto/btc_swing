"""Render reports/BTC_SWING_V2_RANKING_RESEARCH.md (21 sections + one classification)."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from btc_swing.research.phase21_report import _n, _p, _table
from btc_swing.research.phase24_report import STAT_HEADERS, _stat_row
from btc_swing.v2.features import DERIVATIVES_FEATURES, FEATURE_NAMES
from btc_swing.v2.research import V2Result


def _acct_row(label: str, a: dict[str, Any]) -> list[str]:
    if not a or not a.get("n_trades"):
        return [label, "0", *[""] * 10]
    return [
        label,
        str(a["n_trades"]),
        _n(a["trades_per_month"], 2),
        _n(a["mean_R"]),
        _n(a["gross_mean_R"]),
        _n(a["profit_factor"], 2),
        _p(a["win_rate"], 0),
        _n(a["total_costs"], 0),
        _p(a["max_drawdown"]),
        _p(a["total_return"]),
        _n(a["t_stat"], 2),
    ]


ACCT_HEADERS = [
    "stream (single-slot sequential)",
    "trades",
    "trades/month",
    "net R",
    "gross R",
    "PF",
    "win",
    "total costs",
    "max DD",
    "return",
    "t",
]


def _slice_rows(slices: list[dict[str, Any]]) -> list[list[str]]:
    rows = []
    for s in slices:
        rows.append(
            [
                s["slice"],
                str(s.get("n", 0)),
                _n(s.get("mean_R")),
                _n(s.get("median_R")),
                _n(s.get("profit_factor"), 2),
                _p(s.get("win_rate"), 0),
                _n(s.get("mean_MFE_R"), 2),
                _n(s.get("mean_MAE_R"), 2),
                _n(s.get("sum_pnl"), 0),
                _n(s.get("t_stat"), 2),
            ]
        )
    return rows


SLICE_HEADERS = [
    "slice (candidates)",
    "n",
    "mean net R",
    "median R",
    "PF",
    "win",
    "MFE R",
    "MAE R",
    "sum P&L (independent)",
    "t",
]


def _decile_rows(dec: list[dict[str, Any]]) -> list[list[str]]:
    return [
        [
            str(d["decile"]),
            f"{_n(d['score_min'], 3)}..{_n(d['score_max'], 3)}",
            str(d.get("n", 0)),
            _n(d.get("mean_R")),
            _n(d.get("median_R")),
            _n(d.get("profit_factor"), 2),
            _p(d.get("win_rate"), 0),
            _n(d.get("sum_pnl"), 0),
        ]
        for d in dec
    ]


DECILE_HEADERS = [
    "decile (1 = lowest score)",
    "score range",
    "n",
    "mean net R",
    "median R",
    "PF",
    "win",
    "sum P&L",
]


def _bd_rows(rows: list[dict[str, Any]], key: str) -> list[list[str]]:
    out = []
    for d in rows:
        a, s = d["all"], d["selected"]
        out.append(
            [
                str(d[key]),
                f"n={a['n']}, R={_n(a['mean_R'])}, PF={_n(a['profit_factor'], 2)}, win={_p(a['win_rate'], 0)}",
                f"n={s.get('n', 0)}, R={_n(s.get('mean_R'))}, PF={_n(s.get('profit_factor'), 2)}, win={_p(s.get('win_rate'), 0)}"
                if s.get("n")
                else "n=0",
                _p(d["selected_share"], 0),
            ]
        )
    return out


def render_v2(res: V2Result) -> str:
    m, pop = res.manifest, res.population
    rk, sl = res.rank["M1_logistic"], res.slices["M1_logistic"]
    top_q = int(res.criteria[2]["text"].split("top-")[1].split("%")[0]) if res.criteria else 25
    top = next((s for s in sl if s["slice"] == f"top {top_q}%"), {})
    bl, nl = res.baselines, res.null
    first_oof = m["window"]["first_test_start"]
    lines: list[str] = [
        "# BTC Swing V2 — Cost-Aware Learned Opportunity Ranking: research report",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · candidates {m['window']['start']} -> {m['window']['end_exclusive']} · walk-forward predictions from {first_oof} · "
        f"V2 config `{m['v2_config_hash'][:12]}` · frozen V1 execution config `{m['v1_config_hash'][:12]}` · feature set `{m['feature_set_version']}` · code `{m['code_version']}`",
        "",
        "**Central question.** Can a PIT-safe, cost-aware learned ranking identify a substantially smaller subset of BTC swing opportunities whose realised net expectancy survives realistic fees, slippage and funding across different market regimes?",
        "",
        f"**Validation constraint.** {m['validation_note']} Every number below is chronological walk-forward research on data that has already been inspected; the next genuine test of V2 is forward paper testing after a freeze. Paper/backtest only; no live trading, no exchange keys, no real money.",
        "",
        "## 1. V1 failure summary",
        "",
        "- V1 (eight frozen setup families, confirmation trigger, 0.5% risk, V1 exits) showed positive gross expectancy in every window but a net result that did not survive costs: 2022-2024 +0.08R net (CONTROL), 2025-2026 untouched holdout -0.10R net (CONTROL) / -0.07R (the Phase 2.4 approved variant), with fees + slippage + funding of 0.16R to 0.18R per trade.",
        "- Entry-mechanics changes (Phase 2.1, 2.2) and the exit change (Phase 2.3) did not help. Blocking new SHORT entries in TREND_DOWN (Phase 2.4) helped consistently (in sample and on the holdout) but not enough. V1 is closed as failed out-of-sample as a strategy and successful as research infrastructure.",
        "- Conclusion carried into V2: the binding constraint is opportunity selection and per-trade edge strength, not leverage or exits.",
        "",
        "## 2. V2 hypothesis",
        "",
        "- V1 setups are candidate generators. A PIT-safe feature snapshot at each candidate decision point, labelled with the frozen V1 execution engine (realised NET R after fees, slippage and funding), can be ranked by a simple learned model so that higher-scored candidates have higher realised net expectancy, and a selective top slice is net positive after costs while remaining frequent enough to matter.",
        "- Models are pre-declared (logistic regression, ridge, a gated gradient-boosting model), the feature space is fixed (`v2-fs-1`, "
        f"{len(FEATURE_NAMES)} features), evaluation is chronological walk-forward, and the classification criteria were written before any model was fitted (`docs/BTC_SWING_V2_DESIGN.md`, section 11).",
        "",
        "## 3. Candidate population",
        "",
        *_table(
            ["metric", "value"],
            [
                ["5m decision bars scanned", str(pop.get("n_bars"))],
                [
                    "candidates (TRIGGERED episodes, one lifecycle per family, no slot)",
                    str(pop.get("n_candidates")),
                ],
                [
                    "risk-rejected by the frozen V1 sizing rule (no label)",
                    str(pop.get("n_risk_rejected")),
                ],
                [
                    "labelled candidates with a complete simulated exit",
                    str(pop.get("n_labelled_complete")),
                ],
                [
                    f"evaluable walk-forward rows (from {first_oof})",
                    str(pop.get("n_evaluable_walk_forward")),
                ],
                [
                    "V1 single-slot trades on the same window (baseline)",
                    str(bl["v1_single_slot_traded"].get("n_trades_full_window")),
                ],
                [
                    "episode outcomes",
                    ", ".join(
                        f"{k}={v}" for k, v in sorted(pop.get("episode_outcomes", {}).items())
                    ),
                ],
            ],
        ),
        "By family:",
        "",
        *_table(
            ["family", "candidates"],
            [[d["family"], str(d["len"])] for d in pop.get("by_family", [])],
        ),
        "By regime at the trigger bar:",
        "",
        *_table(
            ["regime", "candidates"],
            [[d["regime_at_trigger"], str(d["len"])] for d in pop.get("by_regime_at_trigger", [])],
        ),
        "Labelled candidates by year (independent simulation, no slot):",
        "",
        *_table(
            ["year", "n", "mean net R", "P(net R > 0)"],
            [
                [str(d["year"]), str(d["n"]), _n(d["mean_R"]), _p(d["pos_rate"], 0)]
                for d in pop.get("by_year", [])
            ],
        ),
        "- Candidate = the V1 episode reaching TRIGGERED (zone reached, confirm-TF confirmation, 5m trigger) with each family's lifecycle run independently and the V1 post-close cooldown after every candidate; the decision time is the trigger-bar close, the hypothetical fill the next 5m open plus slippage. This is the same opportunity definition as V1 minus the single slot.",
        "",
        "## 4. Feature definitions (`v2-fs-1`, fixed before fitting)",
        "",
        f"- {len(FEATURE_NAMES)} features in five groups; all signed-in-trade-direction momentum/distance features carry the side sign. Full list: {', '.join(FEATURE_NAMES)}.",
        "- Setup: family one-hot, side, setup age, bars since confirmation, distance from zone mid / anchor / stop (setup-TF ATR), stop distance %, zone width, structural target in R.",
        "- Price/momentum: 15m/1h/4h/24h/7d returns, EMA20/EMA50 distances and spreads on 1h/4h/1d, EMA200 distance on 1d, EMA50 slope on 1d, range position on 4h/1d, 1h retrace, volume acceleration 5m/1h, ATR% 1h/4h/1d, ATR expansion 1h/1d, 1d ATR% percentile (previous 90 bars), realised vol 24h/7d.",
        "- Regime: regime one-hot, regime age, transitions in 7 days, `short_in_trend_down` (the V1 structural finding as a feature, not a gate).",
        f"- Derivatives ({len(DERIVATIVES_FEATURES)}): {', '.join(DERIVATIVES_FEATURES)}.",
        "- Context: hour/day-of-week sine-cosine, weekend, distance from the 30-day high/low, `n_missing`.",
        "- Missing values: training-fold median imputation inside the model pipeline (the gradient-boosting model handles NaN natively).",
        "",
        "## 5. PIT audit",
        "",
        f"- {res.pit['visibility_rule']}.",
        f"- Truncation audit: candidate generation + feature computation re-run with all data after {res.pit['cut'][:10]} removed: {res.pit['candidates_full_run']} vs {res.pit['candidates_truncated_run']} candidates up to the cut, identical trigger times and features: {_n(res.pit['identical'])} (max |feature difference| {_n(res.pit['max_abs_feature_diff'], 9)}).",
        "- Labels use only bars after the decision (simulation forward from the next open); a label enters a training set only when its simulated exit time is <= the fold's test start.",
        "- Selectivity thresholds are quantiles of each fold's training-set scores; the test block never informs its own selection.",
        "",
        "## 6. Labels",
        "",
        *_table(
            STAT_HEADERS,
            [
                _stat_row(
                    "all labelled candidates (independent simulation)",
                    pop.get("label_stats_all", {}),
                )
            ],
        ),
        "- Targets: A `y_pos` = 1[net R > 0]; B net R clipped to [-2, 4]. Net R is after fees, slippage and funding with the frozen V1 sizing (0.5% of a fixed 10,000 USDT research equity).",
        "",
        "## 7. Chronological splits",
        "",
        *_table(
            ["fold", "test block", "train rows", "test rows", "fitted", f"thr top {top_q}%"],
            [
                [
                    str(f["fold"]),
                    f.get("name", ""),
                    str(f["n_train"]),
                    str(f["n_test"]),
                    _n(f["fitted"]),
                    _n(f.get(f"thr_{top_q:02d}"), 3),
                ]
                for f in m["models"]["M1_logistic"]["folds"]
            ],
        ),
        "- Expanding window; training rows are candidates with decision time before the block and simulated exit at or before the block start. No random split anywhere.",
        "",
        "## 8. Logistic baseline (M1)",
        "",
        *_table(
            ["metric", "value"],
            [
                ["walk-forward rows", str(rk.get("n", 0))],
                ["Spearman(score, net R)", _n(rk.get("spearman"))],
                ["AUC for net R > 0", _n(rk.get("auc_pos"))],
                ["base rate P(net R > 0)", _p(rk.get("base_rate_pos"), 1)],
                ["mean net R, all evaluable", _n(rk.get("mean_R"))],
            ],
        ),
        "Per fold:",
        "",
        *_table(
            ["fold", "n", "Spearman", "AUC", "mean net R"],
            [
                [str(d["fold"]), str(d["n"]), _n(d["spearman"]), _n(d["auc_pos"]), _n(d["mean_R"])]
                for d in rk.get("per_fold", [])
            ],
        ),
        "Largest standardised coefficients (mean across folds; sign consistency = share of folds with the mean's sign):",
        "",
        *_table(
            ["feature", "mean coef", "std", "sign consistency"],
            [
                [
                    d["feature"].replace("x_", ""),
                    _n(d["mean_coef"]),
                    _n(d["std_coef"]),
                    _p(d["sign_consistency"], 0),
                ]
                for d in res.coef_stability[:20]
            ],
        ),
        "## 9. Ranking monotonicity (M1 walk-forward deciles)",
        "",
        *_table(DECILE_HEADERS, _decile_rows(rk.get("deciles", []))),
        f"- Spearman between decile index and decile mean net R: {_n(rk.get('decile_monotonicity'), 2)}.",
        "",
    ]
    for yv, buckets in rk.get("deciles_by_year", {}).items():
        lines += [
            f"Quartiles within {yv}:",
            "",
            *_table(
                ["quartile", "score range", "n", "mean net R", "median R", "PF", "win", "sum P&L"],
                _decile_rows(buckets),
            ),
        ]
    lines += [
        "Top-N per month (descriptive, not PIT: picks the month's n best after the month is known):",
        "",
        *_table(STAT_HEADERS, [_stat_row(f"top {d['top_n']} per month", d) for d in res.top_n]),
    ]
    # 10 expected net R
    r2 = res.rank.get("M2_ridge", {})
    s2 = res.slices.get("M2_ridge", [])
    lines += [
        "## 10. Expected-net-R analysis (M2 ridge on clipped net R)",
        "",
        *_table(
            ["metric", "M1 logistic", "M2 ridge"],
            [
                ["Spearman(score, net R)", _n(rk.get("spearman")), _n(r2.get("spearman"))],
                [
                    "decile monotonicity",
                    _n(rk.get("decile_monotonicity"), 2),
                    _n(r2.get("decile_monotonicity"), 2),
                ],
                ["AUC for net R > 0", _n(rk.get("auc_pos")), _n(r2.get("auc_pos"))],
            ],
        ),
        *_table(DECILE_HEADERS, _decile_rows(r2.get("deciles", []))),
        "M2 PIT slices:",
        "",
        *_table(SLICE_HEADERS, _slice_rows(s2)),
    ]
    if "M3_hgb" in res.rank:
        r3, s3 = res.rank["M3_hgb"], res.slices["M3_hgb"]
        lines += [
            f"M3 gradient boosting (gate passed: M1 Spearman {_n(res.gate['spearman_M1'])}, top-quartile gain {_n(res.gate['top_quartile_gain_R'])}R > {_n(res.gate['threshold_gain_R'])}R):",
            "",
            *_table(
                ["metric", "value"],
                [
                    ["Spearman", _n(r3.get("spearman"))],
                    ["AUC", _n(r3.get("auc_pos"))],
                    ["decile monotonicity", _n(r3.get("decile_monotonicity"), 2)],
                ],
            ),
            *_table(SLICE_HEADERS, _slice_rows(s3)),
        ]
    else:
        lines += [
            f"- M3 (gradient boosting) was NOT fitted: the pre-declared gate requires M1 Spearman > 0 and a top-quartile gain over all candidates > {_n(res.gate['threshold_gain_R'])}R; observed Spearman {_n(res.gate['spearman_M1'])}, gain {_n(res.gate['top_quartile_gain_R'])}R.",
            "",
        ]
    # 11-13 breakdowns
    for sec, key, title in (
        (11, "side", "LONG / SHORT"),
        (12, "family", "Setup-family performance"),
        (13, "regime_at_trigger", "Regime performance"),
    ):
        lines += [
            f"## {sec}. {title} (all evaluable vs M1 top-{top_q}% slice)",
            "",
            *_table(
                [key, "all candidates", f"top {top_q}% slice", "share selected"],
                _bd_rows(res.breakdowns[key], key),
            ),
        ]
    # 14 derivatives
    lines += [
        "## 14. Derivatives-feature diagnostics",
        "",
        *_table(
            ["feature", "n", "Spearman vs net R (univariate)", "missing"],
            [
                [d["feature"], str(d["n"]), _n(d["spearman_vs_net_R"]), _p(d["missing_share"], 1)]
                for d in res.derivatives
            ],
        ),
    ]
    ra = res.rank.get("M1_no_derivatives", {})
    sa = res.slices.get("M1_no_derivatives", [])
    ta = next((s for s in sa if s["slice"] == f"top {top_q}%"), {})
    lines += [
        "Ablation (M1 without the derivatives block):",
        "",
        *_table(
            ["metric", "M1 full", "M1 without derivatives"],
            [
                ["Spearman", _n(rk.get("spearman")), _n(ra.get("spearman"))],
                ["AUC", _n(rk.get("auc_pos")), _n(ra.get("auc_pos"))],
                [f"top {top_q}% mean net R", _n(top.get("mean_R")), _n(ta.get("mean_R"))],
                [
                    f"top {top_q}% PF",
                    _n(top.get("profit_factor"), 2),
                    _n(ta.get("profit_factor"), 2),
                ],
            ],
        ),
        "Coefficients of the derivatives block in M1 (mean across folds):",
        "",
        *_table(
            ["feature", "mean coef", "sign consistency"],
            [
                [d["feature"].replace("x_", ""), _n(d["mean_coef"]), _p(d["sign_consistency"], 0)]
                for d in res.coef_stability
                if d["feature"].replace("x_", "") in DERIVATIVES_FEATURES
            ],
        ),
    ]
    # 15 selectivity
    lines += [
        "## 15. Selectivity vs frequency (M1, PIT thresholds)",
        "",
        "Candidate populations (every selected candidate simulated independently):",
        "",
        *_table(SLICE_HEADERS, _slice_rows(sl)),
        "Tradeable streams after single-slot sequencing (fixed research equity, as V1):",
        "",
        *_table(ACCT_HEADERS, [_acct_row(s["slice"], s["account"]) for s in sl]),
        f"- Halves of the top-{top_q}% slice: 2023-2024 n={res.halves['first_half'].get('n', 0)}, mean {_n(res.halves['first_half'].get('mean_R'))}R (all candidates {_n(res.halves['all_first_half'].get('mean_R'))}R); 2025-2026 n={res.halves['second_half'].get('n', 0)}, mean {_n(res.halves['second_half'].get('mean_R'))}R (all candidates {_n(res.halves['all_second_half'].get('mean_R'))}R).",
        "",
        f"Per fold, top-{top_q}% slice:",
        "",
        *_table(
            ["fold", "n all", "mean R all", "n selected", "mean R selected", "P&L selected"],
            [
                [
                    str(d["fold"]),
                    str(d["n_all"]),
                    _n(d["mean_R_all"]),
                    str(d["n_selected"]),
                    _n(d["mean_R_selected"]),
                    _n(d["pnl_selected"], 0),
                ]
                for d in res.per_fold_top
            ],
        ),
    ]

    # 16 costs
    def cost_cell(a: dict[str, Any]) -> list[str]:
        if not a.get("n_trades"):
            return ["0", "", "", "", "", ""]
        return [
            str(a["n_trades"]),
            _n(a["gross_mean_R"]),
            _n(a["mean_R"]),
            _n(a["fees"], 0),
            _n(a["slippage"], 0),
            _n(a["funding"], 0),
        ]

    lines += [
        "## 16. Costs (sequential streams, USDT; frozen assumptions)",
        "",
        *_table(
            ["stream", "trades", "gross R", "net R", "fees", "slippage", "funding"],
            [[s["slice"], *cost_cell(s["account"])] for s in sl],
        ),
    ]
    # 17 drawdown
    lines += [
        "## 17. Drawdown / equity (sequential streams)",
        "",
        *_table(
            ["stream", "max DD", "return", "longest losing streak", "net P&L"],
            [
                [
                    s["slice"],
                    _p(s["account"].get("max_drawdown")),
                    _p(s["account"].get("total_return")),
                    str(s["account"].get("longest_losing_streak", "")),
                    _n(s["account"].get("net_pnl"), 0),
                ]
                for s in sl
            ],
        ),
    ]
    # 18 null / baselines
    v1b = bl["v1_single_slot_traded"]
    hb, mhb = bl["hard_block_only"], bl["model_plus_hard_block"]
    nt, na = nl.get("top_slice", {}), nl.get("all_candidates", {})

    def null_cells(d: dict[str, Any]) -> list[str]:
        if not d.get("n_trades"):
            return ["0", "", "", "", ""]
        tm, rg = d.get("time", {}), d.get("regime", {})
        return [
            str(d["n_trades"]),
            _n(d["strategy"]["mean_R"]),
            _n(tm.get("null_mean_R")),
            _n(rg.get("null_mean_R")),
            f"z time {_n(tm.get('z_vs_null_reps'), 2)}, regime {_n(rg.get('z_vs_null_reps'), 2)}",
        ]

    lines += [
        "## 18. Null comparison and baselines",
        "",
        *_table(
            ["population", "n", "mean net R", "null time-matched", "null regime-matched", "z"],
            [[f"M1 top {top_q}%", *null_cells(nt)], ["all candidates", *null_cells(na)]],
        ),
        *_table(
            ["baseline", "n", "mean net R", "PF", "win", "notes"],
            [
                [
                    "all candidates (generator, unranked)",
                    str(bl["all_candidates"].get("n", 0)),
                    _n(bl["all_candidates"].get("mean_R")),
                    _n(bl["all_candidates"].get("profit_factor"), 2),
                    _p(bl["all_candidates"].get("win_rate"), 0),
                    "every TRIGGERED episode",
                ],
                [
                    "V1 single-slot traded population",
                    str(v1b.get("n", 0)),
                    _n(v1b.get("mean_R")),
                    _n(v1b.get("profit_factor"), 2),
                    _p(v1b.get("win_rate"), 0),
                    f"V1 CONTROL engine on the same window from {first_oof}; result hash `{str(v1b.get('result_hash', ''))[:12]}`",
                ],
                [
                    "simple rule: NO_NEW_SHORT_IN_TREND_DOWN (unranked)",
                    str(hb.get("n", 0)),
                    _n(hb.get("mean_R")),
                    _n(hb.get("profit_factor"), 2),
                    _p(hb.get("win_rate"), 0),
                    "V1 Phase 2.4 rule as a hard block",
                ],
                [
                    f"M1 top {top_q}% (model learns the regime effect)",
                    str(top.get("n", 0)),
                    _n(top.get("mean_R")),
                    _n(top.get("profit_factor"), 2),
                    _p(top.get("win_rate"), 0),
                    f"Spearman {_n(rk.get('spearman'))}",
                ],
                [
                    f"M1 top {top_q}% + hard block",
                    str(mhb["top_slice"].get("n", 0)),
                    _n(mhb["top_slice"].get("mean_R")),
                    _n(mhb["top_slice"].get("profit_factor"), 2),
                    _p(mhb["top_slice"].get("win_rate"), 0),
                    f"Spearman on non-blocked {_n(mhb['rank'].get('spearman'))}",
                ],
            ],
        ),
        "- The learned ranking adds information beyond the candidate generator only if the top slice beats all candidates, the V1 traded population and both geometry-matched nulls; the pre-declared criterion 4 encodes this.",
        "",
    ]
    # 19 stability
    lines += [
        "## 19. Model stability",
        "",
        f"- Folds with Spearman > 0: {sum(1 for d in rk.get('per_fold', []) if not math.isnan(d['spearman']) and d['spearman'] > 0)} of {sum(1 for d in rk.get('per_fold', []) if not math.isnan(d['spearman']))}; folds with AUC > 0.5: {sum(1 for d in rk.get('per_fold', []) if not math.isnan(d['auc_pos']) and d['auc_pos'] > 0.5)}.",
        f"- Top-{top_q}% slice positive in {sum(1 for d in res.per_fold_top if d['n_selected'] >= 5 and d['mean_R_selected'] > 0)} of {sum(1 for d in res.per_fold_top if d['n_selected'] >= 5)} folds with >= 5 selected candidates.",
        f"- Coefficient sign consistency across folds (top 20 by magnitude): mean {_p(float(sum(d['sign_consistency'] for d in res.coef_stability[:20]) / max(len(res.coef_stability[:20]), 1)), 0)}.",
        "",
        "## 20. Limitations",
        "",
        "- 2025-2026 is not untouched (V1 Phase 3 inspected it); walk-forward discipline limits leakage of *model* information but cannot restore holdout status for *research decisions* made with knowledge of that period.",
        "- One instrument, one candidate generator (V1 setups), one fixed feature space, a few hundred to a few thousand candidates; per-fold estimates are noisy and the per-fold tables should be read as a stability check, not as evidence of edge in any single quarter.",
        "- Labels are simulated independently (no slot); the sequential-account streams reintroduce the slot and are the tradeable view. Costs are the frozen V1 assumptions; no alternative cost scenario was run.",
        "- The regime label and the setup definitions are V1 model outputs with frozen thresholds; the ranking can only re-weight what the generator produces.",
        "- Top-N-per-month tables are descriptive only (not PIT).",
        "",
        "## 21. Recommendation and pre-declared criteria",
        "",
        *_table(
            ["#", "criterion", "met", "evidence"],
            [
                [str(c["id"]), c["text"], "yes" if c["met"] else "no", c["evidence"]]
                for c in res.criteria
            ],
        ),
        f"**{res.classification}**",
        "",
        *_recommendation(res),
        "",
        "## Appendix — V2 protocol configuration",
        "",
        "```yaml",
        m["v2_config_yaml"].strip(),
        "```",
        "",
    ]
    return "\n".join(lines)


def _recommendation(res: V2Result) -> list[str]:
    cls = res.classification
    failed = [str(c["id"]) for c in res.criteria if not c["met"]]
    if cls.startswith("A"):
        return [
            "- All six pre-declared criteria are met. Next step (owner decision): freeze V2 (feature set `v2-fs-1`, M1 logistic, the fixed hyper-parameters, the top-slice threshold rule) and start forward PAPER testing with a fixed start date and config hash; no live trading, no exchange keys. Nothing is tuned before or during the paper test.",
        ]
    if cls.startswith("B"):
        return [
            f"- Some ranking signal exists (criterion 1 met, the top slice beats all candidates) but criteria {', '.join(failed)} fail. V2 is not ready for a paper test. The honest next step is more CONTROLLED research with a new pre-registration: e.g. a longer candidate history or additional out-of-sample months as the archive grows, evaluated with the SAME feature space, models and criteria. Not a filter search, not threshold tuning, not family removal.",
        ]
    return [
        f"- Criteria {', '.join(failed)} fail; the learned ranking does not add usable information beyond the candidate generator under the frozen execution and cost model. No parameter tweak is proposed. The owner decides whether to stop the V2 track or to pre-register a differently defined candidate generator or label as a new research generation.",
    ]
