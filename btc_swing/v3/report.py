"""Render reports/BTC_SWING_V3_ACTIVE_SWING_RESEARCH.md (26 sections + one classification)."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from btc_swing.research.phase21_report import _ms, _n, _p, _table
from btc_swing.research.phase24_report import STAT_HEADERS, _by_rows, _stat_row
from btc_swing.v3.config import V3Family
from btc_swing.v3.research import V3Research


def _row(label: str, d: dict[str, Any]) -> list[str]:
    if not d or not d.get("n"):
        return [label, "0", *[""] * 9]
    return [
        label,
        str(d["n"]),
        _n(d.get("trades_per_day"), 2),
        _p(d.get("win_rate"), 0),
        _n(d.get("gross_mean_R")),
        _n(d.get("mean_R")),
        _n(d.get("profit_factor"), 2),
        _n(d.get("sum_pnl"), 0),
        _p(d.get("max_dd")),
        _n(d.get("t_stat"), 2),
    ]


PERIOD_HEADERS = [
    "period",
    "n",
    "trades/day",
    "win",
    "gross R",
    "net R",
    "PF",
    "net P&L",
    "max DD",
    "t",
]


def _acct_rows(label: str, a: dict[str, Any]) -> list[str]:
    return [
        label,
        _n(a.get("final_equity"), 2),
        _p(a.get("total_return")),
        _p(a.get("cagr")),
        _p(a.get("max_drawdown_frac_trade_curve")),
        _p(a.get("max_drawdown_frac_daily_mtm")),
        _n(a.get("sharpe_daily_annualised"), 2),
        _n(a.get("sortino_daily_annualised"), 2),
        str(a.get("longest_losing_streak", "")),
        _n(a.get("trades_per_week"), 2),
    ]


ACCT_HEADERS = [
    "stream",
    "final equity",
    "return",
    "CAGR",
    "max DD (trades)",
    "max DD (daily)",
    "Sharpe",
    "Sortino",
    "longest losing streak",
    "trades/week",
]


def render_v3(res: V3Research) -> str:
    m, rm = res.manifest, res.manifest["raw_manifest"]
    o, cs, ac = res.overall, res.costs, res.account
    fq, hd, ch = res.freq, res.hold, res.chrono
    nl = res.null
    cfg_yaml = m["config_yaml"]
    start, end = rm["period_start_ms"], rm["period_end_ms"]
    lines: list[str] = [
        "# BTC Swing V3 — Active Multi-Timeframe Swing: research report",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · window {_ms(start)} -> {_ms(end)} UTC · config `{m['config_hash'][:12]}` · raw result hash `{rm['result_hash'][:12]}` · code `{rm['code_version']}`",
        "",
        "**Central question.** Can a newly designed multi-timeframe BTC active-swing strategy naturally generate roughly 1-3 trades per day, with typical holds of hours rather than minutes, while maintaining positive NET expectancy after realistic fees, slippage and funding across different market regimes?",
        "",
        f"**Validation constraint.** {m['validation_note']} Deterministic research (no ML), one pre-registered configuration, run once; the next genuine validation of V3 is forward paper testing after a freeze. Paper/backtest only; no live trading, no exchange keys, no real money.",
        "",
        "## 1. V3 hypothesis",
        "",
        "- A 4H-context / 1H-setup / 15m-confirmation / 5m-execution structure with four structurally distinct families (trend pullback continuation, breakout retest, liquidity sweep reversal, volatility expansion continuation), structural stops, a simple TP1/TP2/trail exit and 0.25% planned risk produces 1-3 trades per day held for hours, with positive net expectancy after costs across regimes. V1/V2 showed that micro-entry and exit tweaks and learned ranking of V1 setups did not create edge; V3 tests whether a different, more active structural generator does.",
        "",
        "## 2. Frozen design / config proof",
        "",
        *_table(
            ["check", "value"],
            [
                [
                    "design document",
                    "`docs/BTC_SWING_V3_DESIGN.md`, committed before any V3 result (git history)",
                ],
                ["config", f"`config/btc_swing_v3.yaml`, hash `{m['config_hash']}`"],
                [
                    "strategy / rule versions",
                    f"{rm['strategy_name']} {rm['strategy_version']}; "
                    + ", ".join(f"{k}={v}" for k, v in rm["rule_versions"].items()),
                ],
                ["code version", rm["code_version"]],
                ["risk per trade (default stream)", _p(rm["risk_per_trade"])],
                [
                    "runs of the strategy configuration",
                    "one raw run (plus the reporting-only overlay, 0.5% and leverage-cap streams and the determinism/truncation audits, which never changed a rule)",
                ],
                ["deterministic rerun identical", _n(m["determinism_identical"])],
            ],
        ),
        "## 3. Data coverage",
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
                    _p((d.get("coverage_pct") or math.nan) / 100.0, 2),
                ]
                for k, d in res.coverage.items()
            ],
        ),
        "## 4. PIT audit",
        "",
        f"- {res.pit['visibility_rule']}.",
        f"- Deterministic rerun: {_n(res.pit['deterministic'])}. Truncation audit: decisions (regime, position state) up to {res.pit['truncation']['cut'][:16]} identical with all later data removed: {_n(res.pit['truncation']['identical'])} ({res.pit['truncation']['rows_compared']} rows).",
        "- Resampling oracle vs native archive bars: "
        + ", ".join(
            f"{tf}: {d['bars_compared']} bars, {d['bars_differing']} differ"
            for tf, d in res.pit["resample_oracle"].items()
        )
        + ".",
        f"- Liquidation basis: {res.pit['liquidation_basis']}. `MarketView` raises on negative offsets; indicators causal; 1H swing points confirmed two bars later; the 1H ATR percentile uses previous bars only.",
        "",
        "## 5. Setup definitions (frozen)",
        "",
        "- A TREND_PULLBACK_CONTINUATION: 4H trend and 1H alignment; impulse (last confirmed 1H swing low -> high) >= 1.5 ATR; 1H close inside the 38.2-78.6% retracement; pullback extreme above the swing low; 15m EMA21 reclaim inside the zone; stop = pullback extreme - 0.3 ATR; target = the swing high. Mirror for SHORT.",
        "- B BREAKOUT_RETEST: level = 48-bar 1H high ending two bars back; 1H close > level + 0.1 ATR; within 12 h a 15m bar touches level + 0.25 ATR from above and closes above the level, up; zone [level - 0.25 ATR, level + 0.5 ATR]; stop = level - 0.5 ATR; target = level + consolidation height; cancel on a 1H close < level - 0.25 ATR.",
        "- C LIQUIDITY_SWEEP_REVERSAL: a 15m low below the last confirmed 4H or 1H swing low by >= 0.15 ATR; within 4 15m bars a close back above the level in the upper half of its range; zone [level - 0.1 ATR, level + 0.5 ATR]; stop = sweep extreme - 0.25 ATR; target = last confirmed 1H swing high; cancel on a 15m close below the sweep extreme.",
        "- D VOLATILITY_EXPANSION_CONTINUATION: 1H ATR percentile <= 0.25 within the last 12 bars; expansion bar range >= 1.8 x prior ATR, close in the top 30%, above the prior 12-bar high, volume >= 1.5 x the 20-bar mean, above EMA21, not more than 1.5 ATR above the prior high; within 4 15m bars a 15m close above the previous 15m high and <= expansion high + 0.5 ATR; zone [expansion mid, expansion high + 0.5 ATR]; stop = mid - 0.3 ATR; target = high + range.",
        "- All families: minimum reward-to-risk 1.5 at detection, stop distance 0.4-3.0 ATR(1h), execution on a 5m close inside the zone after confirmation (fill at the next 5m open), 24 h watch timeout, one net exposure, priority A > B > C > D and LONG before SHORT.",
        "",
        "## 6. Candidate counts (episodes)",
        "",
        *_table(
            ["metric", "value"],
            [
                ["5m decision bars", str(rm["n_5m_evaluations"])],
                ["episodes (setups detected, all families)", str(res.episodes["n"])],
                ["trades (raw stream)", str(o.get("n", 0))],
                [
                    "triggers blocked by an open position",
                    str(res.episodes["blocked"].get("BLOCKED_POSITION_OPEN", 0)),
                ],
                ["risk-rejected triggers", str(res.episodes["blocked"].get("RISK_REJECTED", 0))],
            ],
        ),
        "Episodes by family:",
        "",
        *_table(
            ["family", "episodes"],
            [[d["family"], str(d["len"])] for d in res.episodes["detected_by_family"]],
        ),
        "Episode end reasons:",
        "",
        *_table(
            ["end reason", "n"],
            [[str(d["end_reason"]), str(d["len"])] for d in res.episodes["by_outcome"]],
        ),
        "## 7. Trade frequency",
        "",
        *_table(
            ["metric", "value"],
            [
                ["trades / day", _n(fq.get("trades_per_day"), 3)],
                ["trades / week", _n(fq.get("trades_per_week"), 2)],
                ["trades / month", _n(fq.get("trades_per_month"), 1)],
                ["median hours between entries", _n(fq.get("median_hours_between_entries"), 1)],
                ["days with 0 / 1 / 2 / 3+ trades", _day_cell(fq.get("days_distribution", {}))],
            ],
        ),
        "By year:",
        "",
        *_table(
            ["year", "n", "trades/day", "trades/month", "days 0 / 1 / 2 / 3+"],
            [
                [
                    y,
                    str(d["n"]),
                    _n(d["trades_per_day"], 3),
                    _n(d["trades_per_month"], 1),
                    _day_cell(d["days_distribution"]),
                ]
                for y, d in fq.get("by_year", {}).items()
            ],
        ),
        "- Target band for this objective: 0.5-3 trades/day (15-90 per month). Fewer means the generator is too sparse for active swing trading; more means it is drifting towards noise.",
        "",
        "## 8. Holding periods",
        "",
        *_table(
            ["quantile", "hours"],
            [[f"p{int(q * 100)}", _n(h, 1)] for q, h in hd.get("quantiles", {}).items()],
        ),
        *_table(
            ["bucket", "share", "n"],
            [[b["bucket"], _p(b["share"], 0), str(b["n"])] for b in hd.get("buckets", [])],
        ),
        f"- Mean {_n(hd.get('mean'), 1)} h; trades under 30 minutes: {_p(hd.get('share_under_30min'), 1)}. Median hold by exit: "
        + ", ".join(f"{k} {_n(v, 1)} h" for k, v in hd.get("by_exit", {}).items())
        + ".",
        "",
        "## 9. Combined performance (raw stream, 0.25% risk)",
        "",
        *_table(
            PERIOD_HEADERS,
            [
                _row(
                    "combined",
                    {
                        **o,
                        "gross_mean_R": cs.get("expectancy_R_before_costs"),
                        "max_dd": ac.get("max_drawdown_frac_trade_curve"),
                        "trades_per_day": fq.get("trades_per_day"),
                    },
                )
            ],
        ),
        *_table(STAT_HEADERS, [_stat_row("all trades", o)]),
        f"- Net P&L {_n(cs.get('net'), 0)} USDT on 10,000 (return {_p(ac.get('total_return'))}); gross expectancy {_n(cs.get('expectancy_R_before_costs'))}R, net {_n(cs.get('expectancy_R_net'))}R, cost drag {_n(cs.get('cost_drag_R_per_trade'))}R per trade; std of R {_n(_std(o), 3)}; t = {_n(o.get('t_stat'), 2)}.",
        "",
        "## 10. Annual performance",
        "",
        *_table(PERIOD_HEADERS, [_row(y, d) for y, d in ch.get("years", {}).items()]),
        "## 11. Quarterly stability",
        "",
        *_table(PERIOD_HEADERS, [_row(q, d) for q, d in ch.get("quarters", {}).items()]),
        f"- Quarters with >= 10 trades and positive net expectancy: {sum(1 for d in ch.get('quarters', {}).values() if d.get('n', 0) >= 10 and (d.get('mean_R') or 0) > 0)} of {sum(1 for d in ch.get('quarters', {}).values() if d.get('n', 0) >= 10)}.",
        "",
        "## 12. LONG vs SHORT",
        "",
        *_table(
            [*STAT_HEADERS, "gross R", "cost drag R"],
            [
                [*_stat_row(s, d), _n(d.get("gross_mean_R")), _n(d.get("cost_drag_R"))]
                for s, d in res.sides.items()
            ],
        ),
        "## 13. Setup-family performance",
        "",
        *_table(
            [
                "family",
                "side",
                "n",
                "trades/month",
                "win",
                "net R",
                "gross R",
                "cost drag R",
                "PF",
                "MFE R",
                "MAE R",
                "median hold h",
                "DD-window P&L",
                "P&L share",
            ],
            [
                [
                    f["family"].replace("_", " "),
                    f["side"],
                    str(f.get("n", 0)),
                    _n(f.get("trades_per_month"), 2),
                    _p(f.get("win_rate"), 0),
                    _n(f.get("mean_R")),
                    _n(f.get("gross_mean_R")),
                    _n(f.get("cost_drag_R")),
                    _n(f.get("profit_factor"), 2),
                    _n(f.get("mean_MFE_R"), 2),
                    _n(f.get("mean_MAE_R"), 2),
                    _n(f.get("median_hold_h"), 1),
                    _n(f.get("dd_window_pnl"), 0),
                    _p(f.get("pnl_share"), 0),
                ]
                for f in res.families
                if f.get("n")
            ],
        ),
        "- Every family is shown; none is removed. Families with no trades do not appear in the table (count 0).",
        "",
        "## 14. Regime performance (V3 regime at the trigger bar; reported, not gated)",
        "",
        *_table(
            STAT_HEADERS,
            _by_rows(
                res.regimes,
                "regime_at_trigger"
                if res.regimes and "regime_at_trigger" in res.regimes[0]
                else "regime_at_entry",
            ),
        ),
        "Regime bar counts over the window: "
        + ", ".join(f"{k}={v}" for k, v in rm["regime_bar_counts"].items())
        + ".",
        "",
        "## 15. Derivatives context (snapshot at entry; context only)",
        "",
        *_table(
            ["feature", "n", "mean winners", "mean losers", "Spearman vs net R"],
            [
                [
                    d["feature"],
                    str(d["n"]),
                    _n(d["mean_winners"], 4),
                    _n(d["mean_losers"], 4),
                    _n(d["spearman_vs_net_R"]),
                ]
                for d in res.derivatives
            ],
        ),
        "## 16. MFE / MAE",
        "",
        *_table(
            ["metric", "value"],
            [
                [
                    k,
                    _n(v, 3)
                    if not k.startswith("hit_rate") and not k.endswith("hit_rate")
                    else _p(v, 1),
                ]
                for k, v in res.mfe.items()
                if k != "n"
            ],
        ),
        "## 17. Exits",
        "",
        *_table(STAT_HEADERS, _by_rows(res.exits, "exit_reason")),
        "- TP1 = +1R (40%, breakeven after), TP2 = structural target in [1.5R, 4R] (30%), remainder trails the 15m swing - 0.5 ATR(15m), 48 h cap. Exit levels were not optimised; the counterfactual R-hit rates in section 16 show what other targets would have reached before the initial stop.",
        "",
        "## 18. Costs (USDT, raw stream; frozen assumptions)",
        "",
        *_table(
            ["component", "value"],
            [
                ["trades", str(o.get("n", 0))],
                ["gross P&L before slippage", _n(cs.get("gross_before_slippage"), 0)],
                ["slippage", _n(cs.get("slippage"), 0)],
                ["fees", _n(cs.get("fees"), 0)],
                ["funding", _n(cs.get("funding"), 0)],
                ["net P&L", _n(cs.get("net"), 0)],
                [
                    "gross expectancy R / net expectancy R",
                    f"{_n(cs.get('expectancy_R_before_costs'))} / {_n(cs.get('expectancy_R_net'))}",
                ],
                ["cost drag per trade (R)", _n(cs.get("cost_drag_R_per_trade"))],
                ["fees as % of gross", _p((cs.get("fees_pct_of_gross") or math.nan) / 100.0, 1)],
                ["funding events", str(cs.get("n_funding_events", 0))],
            ],
        ),
        "## 19. Risk / account simulation",
        "",
        *_table(
            ACCT_HEADERS,
            [
                _acct_rows("raw, 0.25% risk (default)", ac),
                _acct_rows("safety overlay, 0.25% risk", res.account_overlay),
                _acct_rows("raw, 0.5% risk (reporting only)", res.account_half),
            ],
        ),
        "- Sequential single-slot account, fixed research equity for sizing (compounding off), max planned open risk = one position's planned risk. The 0.5% stream is for reporting; no decision uses it.",
        "",
        "## 20. Leverage / liquidation (raw stream)",
        "",
        *_table(
            ["metric", "value"],
            [
                [
                    "leverage used (trades per level)",
                    ", ".join(
                        f"{d['leverage']:g}x: {d['len']}"
                        for d in res.leverage.get("leverage_distribution", [])
                    ),
                ],
                [
                    "mean / max leverage",
                    f"{_n(res.leverage.get('mean_leverage'), 2)} / {_n(res.leverage.get('max_leverage'), 0)}",
                ],
                ["mean notional (USDT)", _n(res.leverage.get("mean_notional"), 0)],
                [
                    "mean / max margin % of equity",
                    f"{_n(res.leverage.get('mean_margin_pct_equity'), 1)} / {_n(res.leverage.get('max_margin_pct_equity'), 1)}",
                ],
                [
                    "mean / max planned account risk %",
                    f"{_n(res.leverage.get('mean_account_risk_pct'), 3)} / {_n(res.leverage.get('max_account_risk_pct'), 3)}",
                ],
                ["mean stop distance %", _n(res.leverage.get("mean_stop_distance_pct"), 2)],
                [
                    "min / median stop-to-liquidation ratio",
                    f"{_n(res.leverage.get('min_stop_to_liq_ratio'), 1)} / {_n(res.leverage.get('median_stop_to_liq_ratio'), 1)}",
                ],
                ["min liquidation distance %", _n(res.leverage.get("min_liq_distance_pct"), 2)],
                ["liquidations", str(res.leverage.get("liquidations", 0))],
                ["risk-capped trades", str(res.leverage.get("n_risk_capped", 0))],
            ],
        ),
        "Leverage caps (same rules, cap on the allowed ladder):",
        "",
        *_table(
            [
                "cap",
                "trades",
                "risk rejected",
                "mean leverage",
                "mean R",
                "return",
                "max DD",
                "min liq distance %",
                "liquidations",
            ],
            [
                [
                    f"{d['max_leverage']:g}x",
                    str(d["n_trades"]),
                    str(d["risk_rejected"]),
                    _n(d["mean_leverage"], 2),
                    _n(d["mean_R"]),
                    _p(d["total_return"]),
                    _p(d["max_dd"]),
                    _n(d["min_liq_distance_pct"], 2),
                    str(d["liquidations"]),
                ]
                for d in res.leverage_caps
            ],
        ),
        "- Leverage is not alpha: expectancy in R is the same across caps unless the cap rejects trades; the return differs only through notional. 10x must never be needed.",
        "",
        "## 21. Daily-loss safety overlay (reported separately)",
        "",
        *_table(
            ["metric", "raw", "overlay"],
            [
                [
                    "trades",
                    str(res.raw.manifest["n_trades"]),
                    str(res.overlay.manifest["n_trades"]),
                ],
                [
                    "entries blocked by the overlay",
                    "0",
                    str(res.overlay.blocked.get("BLOCKED_SAFETY", 0)),
                ],
                [
                    "net P&L",
                    _n(cs.get("net"), 0),
                    _n(
                        res.overlay.manifest["final_equity"]
                        - res.overlay.manifest["initial_equity"],
                        0,
                    ),
                ],
                [
                    "max DD (trade curve)",
                    _p(ac.get("max_drawdown_frac_trade_curve")),
                    _p(res.account_overlay.get("max_drawdown_frac_trade_curve")),
                ],
                [
                    "longest losing streak",
                    str(ac.get("longest_losing_streak")),
                    str(res.account_overlay.get("longest_losing_streak")),
                ],
            ],
        ),
        "- Rule: after 3 full-risk losses (net R <= -0.9) in a UTC day or a realised daily loss <= -0.75% of equity, no new entries that day. It is reported for operational context and is not used to classify the strategy.",
        "",
        "## 22. Null benchmarks",
        "",
        *_table(
            [
                "series",
                "n",
                "mean R",
                "win",
                "strategy - null (R)",
                "z",
                "P(null >= strategy)",
                "mean signed BTC drift",
            ],
            [
                [
                    "V3 strategy",
                    str(nl.get("n_trades", 0)),
                    _n(nl.get("strategy", {}).get("mean_R")),
                    _p(nl.get("strategy", {}).get("win_rate"), 1),
                    "",
                    "",
                    "",
                    _n(nl.get("strategy", {}).get("mean_signed_btc_move"), 4),
                ],
                *[
                    [
                        f"null ({v}-matched)",
                        str(nl[v]["n_samples"]),
                        _n(nl[v]["null_mean_R"]),
                        _p(nl[v]["null_win_rate"], 1),
                        _n(nl[v]["strategy_minus_null_R"]),
                        _n(nl[v]["z_vs_null_reps"], 2),
                        _p(nl[v]["frac_reps_ge_strategy"], 0),
                        _n(nl[v]["null_mean_drift"], 4),
                    ]
                    for v in ("time", "regime")
                    if v in nl
                ],
            ],
        ),
        "- Null entries copy each real trade's side, stop distance, ATR and sizing and run through the same V3 exits and costs (TP2 at 2.5R, the midpoint of the structural range); the drift column is the signed BTC move over the matched holding window without costs.",
        "",
        "## 23. Drawdown",
        "",
        *_table(
            ["metric", "value"],
            [
                [
                    "max drawdown (trade curve / daily mtm)",
                    f"{_p(ac.get('max_drawdown_frac_trade_curve'))} / {_p(ac.get('max_drawdown_frac_daily_mtm'))}",
                ],
                ["max drawdown (USDT)", _n(ac.get("max_drawdown_currency"), 0)],
                ["drawdown window trades", str(ac.get("drawdown_window_n_trades"))],
                [
                    "family contribution inside the window",
                    "; ".join(
                        f"{d['family']} {_n(d['pnl_in_dd_window'], 0)} (n={d['n']})"
                        for d in ac.get("drawdown_window_family_contribution", [])
                    ),
                ],
                [
                    "longest losing / winning streak",
                    f"{ac.get('longest_losing_streak')} / {ac.get('longest_winning_streak')}",
                ],
            ],
        ),
        "## 24. Outlier dependence",
        "",
        *_table(
            ["metric", "value"],
            [
                ["mean net R", _n(res.outl.get("mean_R"))],
                [
                    "without the best 3 / best 5 trades",
                    f"{_n(res.outl.get('mean_R_without_best3'))} / {_n(res.outl.get('mean_R_without_best5'))}",
                ],
                ["without the worst 5 trades", _n(res.outl.get("mean_R_without_worst5"))],
                [
                    "best 5 trades' share of gross profit",
                    _p(res.outl.get("best5_share_of_gross_profit"), 0),
                ],
                [
                    "best quarter / share of net P&L",
                    f"{res.outl.get('best_quarter')} / {_p(res.outl.get('best_quarter_share_of_pnl'), 0)}",
                ],
                [
                    "best family / share of net P&L",
                    f"{res.outl.get('best_family')} / {_p(res.outl.get('best_family_share_of_pnl'), 0)}",
                ],
            ],
        ),
        "## 25. Limitations",
        "",
        "- Development data: every year in the window was inspected in V1/V2; the pre-registration limits researcher degrees of freedom but cannot make these results out-of-sample.",
        "- One instrument, one configuration, 4.75 years; quarterly and family cells are small and noisy; chronological tables are stability checks, not proof.",
        "- Fill model: next 5m open plus fixed slippage, taker fees on every fill, archive funding; no latency, partial-fill or queue model. Liquidation on mark price where available.",
        "- The regime label, the swing definition and the ATR percentile are model outputs with frozen thresholds; a different structural vocabulary would produce a different candidate stream.",
        "- The safety overlay and the 0.5% stream are reporting views; the classification uses the raw 0.25% stream only.",
        "",
        "## 26. Recommendation and pre-declared criteria",
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
        "## Appendix — frozen V3 configuration",
        "",
        "```yaml",
        cfg_yaml.strip(),
        "```",
        "",
    ]
    return "\n".join(lines)


def _day_cell(d: dict[str, Any]) -> str:
    if not d:
        return "n/a"
    return f"{_p(d.get('share_0'), 0)} / {_p(d.get('share_1'), 0)} / {_p(d.get('share_2'), 0)} / {_p(d.get('share_3plus'), 0)} (max {d.get('max_per_day')}/day)"


def _std(o: dict[str, Any]) -> float:
    t, n = o.get("t_stat"), o.get("n", 0)
    if t is None or not n or math.isnan(float(t)) or float(t) == 0:
        return math.nan
    return float(o["mean_R"]) / float(t) * math.sqrt(n)


def _recommendation(res: V3Research) -> list[str]:
    cls = res.classification
    failed = [str(c["id"]) for c in res.criteria if not c["met"]]
    fams = [f for f in res.families if f["side"] == "ALL" and f.get("n")]
    fam_txt = "; ".join(f"{f['family']} {_n(f.get('mean_R'))}R (n={f['n']})" for f in fams)
    if cls.startswith("A"):
        return [
            f"- All eight pre-declared criteria are met. Next step (owner decision): freeze V3 exactly as configured (hash above) and start forward PAPER testing with a fixed start date; no live trading, no tuning. Family results: {fam_txt}."
        ]
    if cls.startswith("B"):
        return [
            f"- Criteria {', '.join(failed)} fail while the B conditions hold (positive net expectancy, PF > 1.05, positive in >= 3 years, >= 0.3 trades/day, robust to the 5 best trades, beats the nulls). A controlled V3.1 is justified only as a NEW pre-registration with explicit, limited changes stated in advance and the same criteria; not a threshold search. Family results: {fam_txt}."
        ]
    return [
        f"- Criteria {', '.join(failed)} fail and the B conditions do not hold. The V3 structural generator, as frozen, has no robust net edge on 2022-2026 development data. No parameter tweak, family removal or re-run is proposed. Family results: {fam_txt}."
    ]


__all__ = ["V3Family", "render_v3"]
