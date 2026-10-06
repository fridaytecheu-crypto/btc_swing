"""Render reports/BTC_SWING_V4_EVENT_POSITIONING_RESEARCH.md (32 sections + one classification)."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from btc_swing.research.phase21_report import _ms, _n, _p, _table
from btc_swing.research.phase24_report import STAT_HEADERS, _by_rows, _stat_row
from btc_swing.v3.report import ACCT_HEADERS, PERIOD_HEADERS, _acct_rows, _day_cell, _row
from btc_swing.v4.research import V4Research


def _hz_rows(rows: list[dict[str, Any]]) -> list[list[str]]:
    return [
        [
            f"{d['horizon_h']:g} h",
            str(d["n"]),
            _p(d["mean"], 3),
            _p(d["median"], 3),
            _p(d["hit_rate"], 1),
            _n(d["t"], 2),
        ]
        for d in rows
    ]


HZ_HEADERS = ["horizon", "n", "mean signed return", "median", "hit rate", "t"]


def render_v4(res: V4Research) -> str:
    m, rm = res.manifest, res.manifest["raw_manifest"]
    o, cs, ac = res.overall, res.costs, res.account
    sa, gate = res.stage_a, res.gate
    fq, hd, ch = res.freq, res.hold, res.chrono
    g, nl = res.geometry, res.null
    start, end = rm["period_start_ms"], rm["period_end_ms"]
    unc = sa.get("unconditional", {})
    lines: list[str] = [
        "# BTC Swing V4 — Event & Positioning Driven Active Swing: research report",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · window {_ms(start)} -> {_ms(end)} UTC · config `{m['config_hash'][:12]}` · design freeze commit `{m['design_freeze_commit'][:12]}` · raw result hash `{rm['result_hash'][:12]}` · code `{rm['code_version']}`",
        "",
        "**Central question.** Do BTC positioning and participation events — deleveraging, positioning resets and participation-confirmed breakouts — provide a repeatable gross directional edge large enough to survive realistic execution costs while still supporting an active swing style of roughly 1-3 trades per day?",
        "",
        f"**Validation constraint.** {m['validation_note']} One frozen configuration, deterministic research, no ML; the next genuine validation is forward paper testing after a freeze. Paper/backtest only; no live or paper trading engine, no exchange keys, no real money.",
        "",
        "## 1. V1-V3 lessons",
        "",
        "- V1 (structural setups, frozen defaults): small positive gross edge erased by costs; untouched 2025-26 holdout net negative. V2 (learned ranking of V1 candidates): no usable ranking signal. V3 (active 4H/1H/15m/5m price-structure generator): hit the activity profile (1.5 trades/day, 3 h median hold) but gross expectancy was already slightly negative and 0.66% stops made costs 0.34R per trade; nulls with the same geometry lost almost as much.",
        "- V4 therefore (a) requires an observable positioning/participation event behind every candidate, (b) measures event edge before any execution (Stage A), (c) floors the stop at 1.5 ATR(1h) so costs are a small fraction of 1R, and (d) treats gross expectancy as the gate.",
        "",
        "## 2. V4 hypothesis",
        "",
        "- Deleveraging flushes (price impulse + OI contraction + participation + aggressive one-sided flow) that fail to continue, positioning resets inside a 4H trend (OI contraction plus funding/premium cooling or opposite flow, structure intact) and participation-confirmed 1H breakouts (volume, OI and taker-flow expansion without crowding) carry a repeatable signed forward edge at 4-8 h horizons; with a simple 15m confirmation, a deterministic 5m execution rule, volatility-floored structural stops and a generic exit, that edge survives realistic costs at 1-3 trades/day.",
        "",
        "## 3. Frozen design proof",
        "",
        *_table(
            ["check", "value"],
            [
                [
                    "design document",
                    f"`docs/BTC_SWING_V4_DESIGN.md` and `config/btc_swing_v4.yaml`, committed at `{m['design_freeze_commit']}` before any V4 code or result",
                ],
                ["config hash", f"`{m['config_hash']}`"],
                [
                    "strategy / rule versions",
                    f"{rm['strategy_name']} {rm['strategy_version']}; "
                    + ", ".join(f"{k}={v}" for k, v in rm["rule_versions"].items()),
                ],
                ["code version at run", rm["code_version"]],
                [
                    "runs of the frozen configuration",
                    "one raw run; the overlay, 0.5%, cost-sensitivity and leverage-cap streams and the audits never changed a rule",
                ],
                ["deterministic rerun identical", _n(m["determinism_identical"])],
            ],
        ),
        "## 4. Data coverage",
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
        f"- 1H feature rows in the window: {res.feature_quality.get('rows_in_window')}; missing share of key features: "
        + ", ".join(
            f"{k} {_p(res.feature_quality[k]['missing_share'], 1)}"
            for k in ("oi_chg_4h_z", "fund_z", "taker_4h_z", "prem_z", "vol_z")
            if k in res.feature_quality
        )
        + ".",
        "",
        "## 5. PIT audit",
        "",
        f"- {res.pit['visibility_rule']}.",
        f"- Deterministic rerun: {_n(m['determinism_identical'])}. Truncation audit with all data after {res.pit['truncation']['cut'][:16]} removed: decisions identical {_n(res.pit['truncation']['decisions_identical'])} ({res.pit['truncation']['rows_compared']} rows); events identical {_n(res.pit['truncation']['events_identical'])} ({res.pit['truncation']['events_compared']} events up to two days before the cut).",
        "- Resampling oracle vs native archive bars: "
        + ", ".join(
            f"{tf}: {d['bars_compared']} bars, {d['bars_differing']} differ"
            for tf, d in res.pit["resample_oracle"].items()
        )
        + f". Liquidation basis: {res.pit['liquidation_basis']}.",
        "",
        "## 6. Event definitions (frozen; z = rolling z-score over the previous 30 days of 1H rows)",
        "",
        "- A DELEVERAGING_REVERSAL (LONG): 4h return z <= -2.0, OI 4h-change z <= -2.0, 4h volume z >= +1.0, 4h taker-imbalance z <= -1.0; thesis window 12 h; 15m EMA21 reclaim with the 5m close at or above the flush extreme; zone [flush extreme, +1.5 ATR]; structural stop flush extreme - 0.25 ATR. SHORT mirrored.",
        "- B POSITIONING_RESET_CONTINUATION (LONG): 4H trend UP; 1H close below EMA21 with 4h return z <= -0.5 and the close above the last 4H swing low; OI 24h-change z <= -1.0 plus at least one of funding cooled (max z over 48 h minus z now >= 1.0), premium cooled (same), 4h taker-imbalance z <= -1.0; window 24 h; 15m EMA21 reclaim with 1h taker buy ratio > 0.5; zone [24-bar low, +1.5 ATR]; structural stop 4H swing low - 0.25 ATR. SHORT mirrored.",
        "- C PARTICIPATION_BREAKOUT (LONG): 1H close above the previous 48-bar high; 1h volume z >= +2.0; OI 4h-change z >= +1.0; 1h taker-imbalance z >= +1.0; funding z and premium z <= +2.0; window 12 h; acceptance = two consecutive 15m closes above the level after the event bar, latest <= level + 1.5 ATR; zone [level - 0.25 ATR, level + 1.5 ATR]; structural stop level - 0.5 ATR. SHORT mirrored.",
        "- All: the stop finalised at the trigger bar is the farther of the structural stop and a 1.5 ATR(1h) volatility floor from the trigger-bar close, capped at 4 ATR; execution = a completed 5m close inside the zone after confirmation (fill next open); one net exposure; same anchor not re-armed within 24 h.",
        "",
        "## 7. Event frequency (Stage A, every 1H bar, both sides, no slot)",
        "",
        *_table(
            ["family", "side", "events"],
            [[d["family"], d["side"], str(d["len"])] for d in sa.get("by_family_side_counts", [])],
        ),
        f"- Events: {sa.get('n_events', 0)} ({sa.get('n_first_in_cluster', 0)} first-in-cluster, i.e. no same-family/side event in the previous 4 hours); {_n(sa.get('events_per_day'), 2)} events/day over the window vs {_n(fq.get('trades_per_day'), 2)} trades/day after confirmation, execution and the single slot.",
        "",
        "## 8. Event forward-return analysis (signed in the event direction, from the event bar close)",
        "",
        "Pooled, all events:",
        "",
        *_table(HZ_HEADERS, _hz_rows(sa.get("pooled", []))),
        "Pooled, first-in-cluster events only:",
        "",
        *_table(HZ_HEADERS, _hz_rows(sa.get("pooled_first_in_cluster", []))),
        "Unconditional BTC forward return of every 1H bar over the same horizons (unsigned drift and dispersion):",
        "",
        *_table(
            ["horizon", "mean", "std", "mean |return|"],
            [
                [
                    f"{h:g} h",
                    _p(unc.get(f"fwd_{h:g}h", {}).get("mean"), 3),
                    _p(unc.get(f"fwd_{h:g}h", {}).get("std"), 2),
                    _p(unc.get(f"fwd_{h:g}h", {}).get("mean_abs"), 2),
                ]
                for h in [0.5, 1, 2, 4, 8, 12, 24]
            ],
        ),
    ]
    for fam, d in sa.get("by_family", {}).items():
        lines += [
            f"{fam} (n={d['n']}; mean MFE {_n(d['mfe_atr'], 2)} ATR, mean MAE {_n(d['mae_atr'], 2)} ATR over 24 h):",
            "",
            *_table(HZ_HEADERS, _hz_rows(d["all"])),
        ]
        for sd in ("LONG", "SHORT"):
            lines += [f"{fam} {sd}:", "", *_table(HZ_HEADERS, _hz_rows(d["by_side"][sd]))]
        if d.get("terciles"):
            lines += [
                f"{fam} by strength tercile (1 = weakest):",
                "",
                *_table(
                    ["tercile", "n", "1h", "4h", "8h", "24h"],
                    [
                        [
                            str(t_["tercile"]),
                            str(t_["n"]),
                            _p(t_["fwd_1h"], 3),
                            _p(t_["fwd_4h"], 3),
                            _p(t_["fwd_8h"], 3),
                            _p(t_["fwd_24h"], 3),
                        ]
                        for t_ in d["terciles"]
                    ],
                ),
            ]
    lines += ["By year (pooled events):", ""]
    for y, rows in sa.get("by_year", {}).items():
        r4: dict[str, Any] = next((r for r in rows if r["horizon_h"] == 4), {})
        r8: dict[str, Any] = next((r for r in rows if r["horizon_h"] == 8), {})
        lines.append(
            f"- {y}: n={r4.get('n', 0)}, 4h {_p(r4.get('mean'), 3)} (t {_n(r4.get('t'), 2)}), 8h {_p(r8.get('mean'), 3)} (t {_n(r8.get('t'), 2)})"
        )
    lines += [
        "",
        f"- Stage A gate (pre-declared): 4 h mean {_p(gate.get('mean_4h'), 3)} (t {_n(gate.get('t_4h'), 2)}), 8 h mean {_p(gate.get('mean_8h'), 3)} (t {_n(gate.get('t_8h'), 2)}), top vs bottom strength tercile at 8 h {_p(gate.get('top_tercile_8h'), 3)} vs {_p(gate.get('bottom_tercile_8h'), 3)} -> passed: {_n(gate.get('passed'))}.",
        "",
    ]
    ep = res.episodes
    lines += [
        "## 9. Candidate / trade conversion",
        "",
        *_table(
            ["stage", "count"],
            [
                ["events (Stage A)", str(ep.get("events_total"))],
                [
                    "episodes opened by the lifecycle (after anchor de-duplication)",
                    str(ep.get("n")),
                ],
                [
                    "triggers blocked by an open position",
                    str(ep.get("blocked", {}).get("BLOCKED_POSITION_OPEN", 0)),
                ],
                ["risk-rejected", str(ep.get("blocked", {}).get("RISK_REJECTED", 0))],
                ["trades", str(o.get("n", 0))],
            ],
        ),
        "Episode end reasons:",
        "",
        *_table(
            ["end reason", "n"],
            [[str(d["end_reason"]), str(d["len"])] for d in ep.get("by_outcome", [])],
        ),
        "## 10. Trade frequency",
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
        "## 11. Holding period",
        "",
        *_table(
            ["quantile", "hours"],
            [[f"p{int(q * 100)}", _n(h, 1)] for q, h in hd.get("quantiles", {}).items()],
        ),
        *_table(
            ["bucket", "share", "n"],
            [[b["bucket"], _p(b["share"], 0), str(b["n"])] for b in hd.get("buckets", [])],
        ),
        "## 12. Stop geometry and cost-to-risk",
        "",
        *_table(
            ["metric", "value"],
            [
                [
                    "median / mean stop (% of price)",
                    f"{_n(g.get('median_stop_pct'), 2)} / {_n(g.get('mean_stop_pct'), 2)} (V3: 0.66)",
                ],
                [
                    "p10 / p90 stop %",
                    f"{_n(g.get('p10_stop_pct'), 2)} / {_n(g.get('p90_stop_pct'), 2)}",
                ],
                ["median stop in ATR(1h)", _n(g.get("median_stop_atr"), 2)],
                ["share of stops set by the volatility floor", _p(g.get("share_vol_floor"), 0)],
                ["round-trip cost assumed (% of notional)", _n(g.get("round_trip_cost_pct"), 3)],
                [
                    "median / p90 cost as % of stop",
                    f"{_p(g.get('median_cost_pct_of_stop'), 1)} / {_p(g.get('p90_cost_pct_of_stop'), 1)}",
                ],
                [
                    "share of trades with cost > 25% of stop",
                    _p(g.get("share_cost_over_25pct_of_stop"), 1),
                ],
                [
                    "median / mean cost drag (R)",
                    f"{_n(g.get('median_cost_drag_R'))} / {_n(g.get('mean_cost_drag_R'))}",
                ],
            ],
        ),
        *_table(
            ["family", "n", "median stop %"],
            [
                [d["family"], str(d["n"]), _n(d["median_stop_pct"], 2)]
                for d in g.get("by_family", [])
            ],
        ),
        "## 13. Gross expectancy (before fees, slippage and funding)",
        "",
        *_table(
            ["population", "n", "gross R", "net R", "PF (net)", "win"],
            [
                [
                    "all trades",
                    str(o.get("n", 0)),
                    _n(cs.get("expectancy_R_before_costs")),
                    _n(cs.get("expectancy_R_net")),
                    _n(o.get("profit_factor"), 2),
                    _p(o.get("win_rate"), 0),
                ],
                *[
                    [
                        f"{f['family']} {f['side']}",
                        str(f.get("n", 0)),
                        _n(f.get("gross_mean_R")),
                        _n(f.get("mean_R")),
                        _n(f.get("profit_factor"), 2),
                        _p(f.get("win_rate"), 0),
                    ]
                    for f in res.families
                    if f.get("n")
                ],
            ],
        ),
        "- Benchmark for economic interest (not a tuning target): gross expectancy >= +0.15R.",
        "",
        "## 14. Costs",
        "",
        *_table(
            [
                "component",
                "primary (frozen)",
                "Bybit-style sensitivity (maker targets), NOT for classification",
            ],
            [
                ["trades", str(o.get("n", 0)), str(res.bybit.manifest["n_trades"])],
                [
                    "gross P&L before slippage",
                    _n(cs.get("gross_before_slippage"), 0),
                    _n(res.costs_bybit.get("gross_before_slippage"), 0),
                ],
                ["slippage", _n(cs.get("slippage"), 0), _n(res.costs_bybit.get("slippage"), 0)],
                ["fees", _n(cs.get("fees"), 0), _n(res.costs_bybit.get("fees"), 0)],
                ["funding", _n(cs.get("funding"), 0), _n(res.costs_bybit.get("funding"), 0)],
                ["net P&L", _n(cs.get("net"), 0), _n(res.costs_bybit.get("net"), 0)],
                [
                    "gross R / net R",
                    f"{_n(cs.get('expectancy_R_before_costs'))} / {_n(cs.get('expectancy_R_net'))}",
                    f"{_n(res.costs_bybit.get('expectancy_R_before_costs'))} / {_n(res.costs_bybit.get('expectancy_R_net'))}",
                ],
                [
                    "cost drag per trade (R)",
                    _n(cs.get("cost_drag_R_per_trade")),
                    _n(res.costs_bybit.get("cost_drag_R_per_trade")),
                ],
                [
                    "funding events",
                    str(cs.get("n_funding_events", 0)),
                    str(res.costs_bybit.get("n_funding_events", 0)),
                ],
            ],
        ),
        "## 15. Net expectancy (raw stream, 0.25% risk)",
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
        "## 16. Yearly results",
        "",
        *_table(PERIOD_HEADERS, [_row(y, d) for y, d in ch.get("years", {}).items()]),
        "## 17. Quarterly stability",
        "",
        *_table(PERIOD_HEADERS, [_row(q, d) for q, d in ch.get("quarters", {}).items()]),
        "## 18. LONG vs SHORT",
        "",
        *_table(
            [*STAT_HEADERS, "gross R", "cost drag R"],
            [
                [*_stat_row(s, d), _n(d.get("gross_mean_R")), _n(d.get("cost_drag_R"))]
                for s, d in res.sides.items()
            ],
        ),
        "## 19. Family results",
        "",
        *_table(
            [
                "family",
                "side",
                "events",
                "trades",
                "trades/month",
                "win",
                "gross R",
                "net R",
                "PF",
                "MFE R",
                "MAE R",
                "median hold h",
                "median stop %",
                "cost drag R",
                "DD-window P&L",
                "P&L share",
            ],
            [
                [
                    f["family"].replace("_", " "),
                    f["side"],
                    str(f.get("events", 0)),
                    str(f.get("n", 0)),
                    _n(f.get("trades_per_month"), 2),
                    _p(f.get("win_rate"), 0),
                    _n(f.get("gross_mean_R")),
                    _n(f.get("mean_R")),
                    _n(f.get("profit_factor"), 2),
                    _n(f.get("mean_MFE_R"), 2),
                    _n(f.get("mean_MAE_R"), 2),
                    _n(f.get("median_hold_h"), 1),
                    _n(f.get("median_stop_pct"), 2),
                    _n(f.get("cost_drag_R")),
                    _n(f.get("dd_window_pnl"), 0),
                    _p(f.get("pnl_share"), 0),
                ]
                for f in res.families
            ],
        ),
        "- Every family and side is shown (zero rows included); none is hidden.",
        "",
        "## 20. Event-strength diagnostics (trades, by event-strength tercile within family)",
        "",
    ]
    for d in res.strength:
        if d.get("terciles"):
            lines += [
                f"{d['family']} (n={d['n']}):",
                "",
                *_table(
                    ["tercile", "n", "gross R", "net R"],
                    [
                        [str(t_["tercile"]), str(t_["n"]), _n(t_["mean_R_gross"]), _n(t_["mean_R"])]
                        for t_ in d["terciles"]
                    ],
                ),
            ]
        else:
            lines += [f"- {d['family']}: n={d.get('n', 0)} (too few trades for terciles).", ""]
    lines += [
        "## 21. OI / funding / taker / basis analysis (snapshot at entry; means for winners vs losers; univariate Spearman vs net R)",
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
        "## 22. Regime analysis (4H trend state x 1H ATR percentile state at the trigger bar; reported, not gated)",
        "",
        *_table(STAT_HEADERS, _by_rows(res.regimes, "regime_at_trigger")),
        "Stage A by regime (4 h / 8 h signed mean, n): "
        + "; ".join(
            f"{r}: {_p(next((x['mean'] for x in rows if x['horizon_h'] == 4), math.nan), 3)} / {_p(next((x['mean'] for x in rows if x['horizon_h'] == 8), math.nan), 3)} (n={next((x['n'] for x in rows if x['horizon_h'] == 8), 0)})"
            for r, rows in sa.get("by_regime", {}).items()
        )
        + ".",
        "",
        "## 23. MFE / MAE",
        "",
        *_table(
            ["metric", "value"],
            [
                [k, _p(v, 1) if ("hit_rate" in k) else _n(v, 3)]
                for k, v in res.mfe.items()
                if k != "n"
            ],
        ),
        "## 24. Exit outcomes",
        "",
        *_table(STAT_HEADERS, _by_rows(res.exits, "exit_reason")),
        "- TP1 +1R (40%, breakeven after), TP2 +2R (30%), remainder trails the 1H swing - 0.5 ATR(1h), 48 h cap; not optimised.",
        "",
        "## 25. Account simulation",
        "",
        *_table(
            ACCT_HEADERS,
            [
                _acct_rows("raw, 0.25% risk (default)", ac),
                _acct_rows("safety overlay, 0.25% risk", res.account_overlay),
                _acct_rows("raw, 0.5% risk (reporting only)", res.account_half),
            ],
        ),
        "- Sequential single-slot account; fixed research equity for sizing; returns beyond -100% denote ruin of the research account.",
        "",
        "## 26. Drawdown",
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
        "## 27. Daily safety overlay (reported separately)",
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
            ],
        ),
        "## 28. Leverage / liquidation",
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
                ["mean stop distance %", _n(res.leverage.get("mean_stop_distance_pct"), 2)],
                [
                    "min / median stop-to-liquidation ratio",
                    f"{_n(res.leverage.get('min_stop_to_liq_ratio'), 1)} / {_n(res.leverage.get('median_stop_to_liq_ratio'), 1)}",
                ],
                ["min liquidation distance %", _n(res.leverage.get("min_liq_distance_pct"), 2)],
                ["liquidations", str(res.leverage.get("liquidations", 0))],
            ],
        ),
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
        "## 29. Null comparison",
        "",
        *_table(
            [
                "series",
                "n",
                "mean net R",
                "mean gross R",
                "win",
                "strategy - null (R)",
                "z",
                "P(null >= strategy)",
                "signed BTC drift",
            ],
            [
                [
                    "V4 strategy",
                    str(nl.get("n_trades", 0)),
                    _n(nl.get("strategy", {}).get("mean_R")),
                    _n(nl.get("strategy", {}).get("mean_R_gross")),
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
                        _n(nl[v]["null_mean_R_gross"]),
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
        "## 30. Outlier dependence",
        "",
        *_table(
            ["metric", "value"],
            [
                ["mean net R", _n(res.outl.get("mean_R"))],
                [
                    "without the best 3 / best 5",
                    f"{_n(res.outl.get('mean_R_without_best3'))} / {_n(res.outl.get('mean_R_without_best5'))}",
                ],
                ["without the worst 5", _n(res.outl.get("mean_R_without_worst5"))],
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
        "## 31. Limitations",
        "",
        "- Development data (2022-2026 inspected by V1-V3); pre-registration limits researcher degrees of freedom but cannot make results out-of-sample.",
        "- Metrics (OI, long/short, taker ratios) are 5-minute archive rows with observation time as published; funding every 8 h; one instrument; z-scores against a 30-day trailing window are a modelling choice frozen in advance.",
        "- Fill model: next 5m open plus fixed slippage; taker fees on every fill in the primary model; no latency or partial-fill model; liquidation on mark price.",
        "- Event clusters (consecutive hours of the same flush) are auto-correlated; first-in-cluster tables and t-statistics should be read together.",
        "- The overlay, the 0.5% and the Bybit-style streams are reporting views; the classification uses the raw 0.25% stream with the frozen primary costs.",
        "",
        "## 32. Recommendation and pre-declared criteria",
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
        "## Appendix — frozen V4 configuration",
        "",
        "```yaml",
        m["config_yaml"].strip(),
        "```",
        "",
    ]
    return "\n".join(lines)


def _recommendation(res: V4Research) -> list[str]:
    cls = res.classification
    failed = [str(c["id"]) for c in res.criteria if not c["met"]]
    fams = [f for f in res.families if f["side"] == "ALL" and f.get("n")]
    fam_txt = "; ".join(
        f"{f['family']} gross {_n(f.get('gross_mean_R'))}R / net {_n(f.get('mean_R'))}R (n={f['n']})"
        for f in fams
    )
    if cls.startswith("A"):
        return [
            f"- Stage A gate and all nine trade criteria met. Next step (owner decision): freeze V4 as configured and start forward PAPER testing with a fixed start date; no live trading, no tuning. Families: {fam_txt}."
        ]
    if cls.startswith("B"):
        return [
            f"- The event populations carry gross predictive signal (Stage A gate passed, gross trade expectancy >= +0.10R, beats the nulls) but criteria {', '.join(failed)} fail, so selection and cost efficiency need work. A controlled V4.1 selection study is justified only as a NEW pre-registration (fixed feature space, fixed criteria); not implemented here. Families: {fam_txt}."
        ]
    return [
        f"- Criteria {', '.join(failed)} fail and the B conditions do not hold: the events, as frozen, do not produce a reliable predictive edge after costs. No parameter tweak, family removal or re-run is proposed. Families: {fam_txt}."
    ]
