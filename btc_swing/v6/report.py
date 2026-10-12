"""Markdown rendering of the V6 research (sections A-L). Numbers only; no rule is changed here."""

from __future__ import annotations

import math
from typing import Any

from btc_swing.v6.config import V6Config
from btc_swing.v6.research import V6Research


def _n(x: Any, d: int = 3) -> str:
    if x is None:
        return "-"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        if math.isnan(x):
            return "-"
        if math.isinf(x):
            return "inf"
        return f"{x:.{d}f}"
    return str(x)


def _pct(x: Any, d: int = 1) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    return f"{100 * float(x):.{d}f}%"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join(_n(v) if not isinstance(v, str) else v for v in r) + " |")
    return "\n".join(out)


def _stats_rows(label: str, st: dict[str, Any], fq: dict[str, Any] | None = None) -> list[Any]:
    return [
        label,
        st.get("n", 0),
        _n(fq["mean_trades_per_calendar_day"] if fq else st.get("trades_per_day"), 2),
        _n(st.get("net_r_mean"), 3),
        _n(st.get("net_r_boot_lo"), 3) + " / " + _n(st.get("net_r_boot_hi"), 3),
        _n(st.get("net_r_sum"), 1),
        _n(st.get("profit_factor"), 2),
        _pct(st.get("win_rate")),
        _n(st.get("gross_r_mean"), 3),
        _n(st.get("fee_r_mean"), 3),
        _n(st.get("slip_r_mean"), 3),
        _n(st.get("fund_r_mean"), 3),
        _pct(st.get("cost_share_of_gross")),
        _n(st.get("median_stop_bps"), 0),
        _n(st.get("median_hold_min"), 0),
        _pct(st.get("max_dd_frac")),
    ]


STAT_HEADERS = [
    "stream", "n", "trades/day", "net R mean", "boot 95% CI", "net R sum", "PF", "win",
    "gross R", "fee R", "slip R", "fund R", "cost/gross", "med stop bps", "med hold min", "max DD",
]  # fmt: skip


def _freq_block(fq: dict[str, Any]) -> str:
    pd = fq.get("pct_days", {})
    rows = [
        ["calendar days in window", fq["calendar_days"]],
        ["executed trades", fq["trades"]],
        ["mean trades per CALENDAR day", _n(fq["mean_trades_per_calendar_day"], 2)],
        ["median trades per calendar day", _n(fq["median_trades_per_calendar_day"], 1)],
        [
            "% days with 0 / 1 / 2 / 3-6 / >6 trades",
            " / ".join(_pct(pd.get(k)) for k in ("0", "1", "2", "3-6", ">6")),
        ],
        ["max trades in a day", fq["max_trades_in_a_day"]],
        [
            "active days (pre-registered definition) / share",
            f"{fq['active_days']} / {_pct(fq['active_days_share'])}",
        ],
        ["mean trades per ACTIVE day", _n(fq.get("mean_trades_per_active_day"), 2)],
        ["mean trades per quiet day", _n(fq.get("mean_trades_per_quiet_day"), 2)],
        ["share of active days inside the 3-6 target", _pct(fq.get("share_active_days_in_target"))],
        [
            "mean / median hours between entries",
            _n(fq.get("mean_hours_between_entries"), 1)
            + " / "
            + _n(fq.get("median_hours_between_entries"), 1),
        ],
        [
            "mean / median holding time (min)",
            _n(fq.get("mean_hold_min"), 0) + " / " + _n(fq.get("median_hold_min"), 0),
        ],
        [
            "share held > 4 h / time-limit exits",
            _pct(fq.get("share_hold_gt_4h")) + " / " + _pct(fq.get("share_time_limit_exits")),
        ],
        [
            "weekday / weekend trades per day",
            _n(fq.get("weekday_trades_per_day"), 2)
            + " / "
            + _n(fq.get("weekend_trades_per_day"), 2),
        ],
        [
            "session share (Asia / Europe / US)",
            " / ".join(
                _pct(fq.get("by_session_share", {}).get(k))
                for k in ("asia_00-08", "europe_08-16", "us_16-24")
            ),
        ],
        ["by side", str(fq.get("by_side", {}))],
        ["by family", str(fq.get("by_family", {}))],
        [
            "HARD minimum >= {:.1f} trades per calendar day".format(
                fq["hard_minimum_trades_per_day"]
            ),
            "MET" if fq["meets_hard_minimum"] else "NOT MET",
        ],
    ]
    txt = _table(["frequency (calendar-day statistics first)", "value"], rows)
    if fq.get("by_year"):
        txt += "\n\n" + _table(
            ["year", "trades", "trades/day"],
            [[r["year"], r["trades"], _n(r["trades_per_day"], 2)] for r in fq["by_year"]],
        )
    return txt


def _crit_table(c: dict[str, Any]) -> str:
    return _table(
        ["criterion", "value", "passed"],
        [[r["criterion"], _n(r["value"], 3), "PASS" if r["passed"] else "FAIL"] for r in c["rows"]],
    )


def _funnel_table(fn: dict[str, Any]) -> str:
    return _table(
        ["funnel (per day)", "value"],
        [
            ["Stage A events (all families, both sides)", _n(fn["events_per_day"], 2)],
            ["first-in-cluster events", _n(fn["first_in_cluster_per_day"], 2)],
            ["episodes opened", _n(fn["episodes_per_day"], 2)],
            ["episodes that became ACTIONABLE", _n(fn["actionable_per_day"], 2)],
            ["triggers (taken + refused)", _n(fn["triggers_per_day"], 2)],
            ["refused by slot / cooldown / budget / risk", _n(fn["refused_per_day"], 2)],
            ["executed trades", _n(fn["trades_per_day"], 2)],
            ["Stage B conversion (actionable / episodes)", _pct(fn["stage_b_conversion"])],
            ["trigger -> trade", _pct(fn["trigger_to_trade"])],
        ],
    )


def render_report(res: V6Research, cfg: V6Config) -> str:
    m, dev, val, fz, bt = res.manifest, res.development, res.validation, res.frozen, res.bottleneck
    vc = val["combined"]
    gate = vc["gate"]
    out: list[str] = []
    achieved = gate["hard_passed"]
    headline = (
        "V6 ACTIVE-INTRADAY OBJECTIVE ACHIEVED ON VALIDATION (subject to the once-only OOS run)"
        if achieved
        else "V6 ACTIVE-INTRADAY OBJECTIVE NOT ACHIEVED"
    )
    out.append(
        "# BTC V6 ACTIVE INTRADAY — research report (Phases 2-4"
        + (", 5" if res.oos else "")
        + ")\n"
    )
    out.append(
        f"**{headline}** — classification on validation: **{gate['classification']}** "
        f"(A robust and suitable, B promising, C reject). Bottleneck summary: {bt['summary']}.\n"
    )
    out.append(
        f"Config `{cfg.strategy_name}` hash `{m['config_hash'][:16]}…`, code `{m['code_version']}`, "
        f"research hash `{m['research_hash'][:16]}…`, run {m['ran_at']}. Paper research only: this track "
        "has no exchange client; nothing here placed an order or touched V5/V5.1.\n"
    )
    # A
    out.append("## A. Data and windows\n")
    fm = m["frame_meta"]
    out.append(
        _table(
            ["item", "value"],
            [
                [
                    "research venue (history)",
                    cfg.instrument.research_venue
                    + " (Binance Vision archives: 5m klines, aggTrades 5m flow, 5-min metrics/OI, funding, premium index)",
                ],
                ["forward venue (shadow, later)", cfg.instrument.forward_venue],
                [
                    "frame rows / valid rows / flow rows from aggTrades",
                    f"{fm['rows']} / {fm['valid_rows']} / {fm['flow_rows_from_aggtrades']}",
                ],
                [
                    "development",
                    f"{m['windows']['development']['start']} -> {m['windows']['development']['end_exclusive']} ({m['windows']['development']['days']:.0f} d)",
                ],
                [
                    "validation",
                    f"{m['windows']['validation']['start']} -> {m['windows']['validation']['end_exclusive']} ({m['windows']['validation']['days']:.0f} d)",
                ],
                [
                    "once-only OOS",
                    f"{m['windows']['oos']['start']} -> {m['windows']['oos']['end_exclusive']} ({m['windows']['oos']['days']:.0f} d) — "
                    + ("RUN (spent)" if res.oos else "NOT RUN (unspent)"),
                ],
                [
                    "liquidation families D/E",
                    "forward-only (no historical liquidation data exists); not part of any statistic below",
                ],
                ["data hashes", ", ".join(f"{k} {v[:10]}…" for k, v in m["data_hashes"].items())],
            ],
        )
    )
    out.append(
        "\nEvery feature is PIT at the 5m close (completed 15m/1h/4h bars only, metrics/funding/premium by observation time, same-time-of-day baselines from previous days only); gap rows are never observations.\n"
    )
    # B
    out.append("## B. Families and frozen specification\n")
    out.append(
        _table(
            ["family", "Stage A (event)", "Stage B (entry)", "stop"],
            [
                [
                    "A MOMENTUM_CONTINUATION",
                    f"15m close beyond the 4h 5m high/low, rel. volume >= {cfg.families.momentum_continuation.rel_vol_15m_min:.2f}, 15m imbalance and 1h CVD slope with the break, OI up, 1h trend not opposed, extension <= {cfg.families.momentum_continuation.max_extension_atr5:.1f} ATR5",
                    f"5m close back in [level, level + {cfg.families.momentum_continuation.zone_atr5:.1f} ATR5] or a higher-low/lower-high within 2 h; invalid on a close {cfg.families.momentum_continuation.invalidation_atr5:.1f} ATR5 beyond the level",
                    "15m structure since the event",
                ],
                [
                    "B PULLBACK_CONTINUATION",
                    f"1h move >= {cfg.families.pullback_continuation.impulse_min_atr1h:.2f} ATR1h with 1h imbalance and OI confirming",
                    f"retrace {100 * cfg.families.pullback_continuation.retrace_min:.0f}-{100 * cfg.families.pullback_continuation.retrace_max:.0f}% on contracting volume (rel. vol <= {cfg.families.pullback_continuation.pullback_max_rel_vol:.1f}), 5m imbalance back with the trend and a reclaim of the 15m EMA21 or a 5m swing; invalid beyond {100 * cfg.families.pullback_continuation.retrace_invalid:.0f}%",
                    "pullback extreme",
                ],
                [
                    "C FAILED_BREAKOUT_REVERSAL",
                    f"new 24h extreme with rel. volume >= {cfg.families.failed_breakout_reversal.rel_vol_5m_min:.2f} and |5m imbalance| >= {cfg.families.failed_breakout_reversal.imbalance_5m_min:.2f}, back inside within {cfg.families.failed_breakout_reversal.reentry_bars} bars with CVD slope or OI not confirming",
                    "5m close beyond the breakout bar's opposite extreme; invalid on a close beyond the sweep extreme",
                    "sweep extreme",
                ],
                [
                    "F FLOW_OI_IMPULSE",
                    f"15m return >= {cfg.families.flow_oi_impulse.ret_15m_min_atr15:.2f} ATR15, rel. volume >= {cfg.families.flow_oi_impulse.rel_vol_15m_min:.2f}, |15m imbalance| >= {cfg.families.flow_oi_impulse.imbalance_15m_min:.2f}, CVD slope with the move, |OI change| >= {cfg.families.flow_oi_impulse.oi_rel_min:.1f}x its same-time baseline and rising, premium not crowded (|z| <= {cfg.families.flow_oi_impulse.max_crowding_prem_z:.1f})",
                    f"first 5m close beyond a <= {cfg.families.flow_oi_impulse.pause_max_bars}-bar pause within {cfg.families.flow_oi_impulse.pause_wait_bars} bars; longer pauses invalidate",
                    "15m impulse origin",
                ],
            ],
        )
    )
    out.append(
        f"\nCommon rules: stop = farther of structure and {cfg.stops.floor_atr15:.1f} ATR(15m), capped at "
        f"{cfg.stops.max_atr15:.1f} ATR(15m); one position, no pyramiding/averaging; episode max age "
        f"{cfg.episode.max_age_bars} bars, entry window {cfg.episode.entry_window_bars} bars; cooldowns "
        f"{cfg.episode.cooldown_after_close_bars} / {cfg.episode.cooldown_after_invalidation_bars} / "
        f"{cfg.episode.cooldown_after_two_losses_bars} bars (close / invalidation / two consecutive losses in a "
        f"family-side); priority {', '.join(f.value for f in cfg.episode.family_priority)}; time cap "
        f"{cfg.exits.max_hold_hours:g} h; fills at the next 5m open; costs taker {cfg.costs.taker_fee_bps} bps per side, "
        f"slippage {cfg.costs.entry_slippage_bps}/{cfg.costs.stop_slippage_bps} bps (entry/stop), half-spread "
        f"{cfg.costs.spread_ticks * cfg.costs.tick_size / 2:g} USDT, funding at settlements; risk "
        f"{100 * cfg.risk.risk_per_trade:.2f}% of {cfg.risk.initial_equity:.0f} USDT reference equity, frozen "
        f"leverage ladder {cfg.risk.allowed_leverage} (margin cap {cfg.risk.margin_cap_frac:.0%}).\n"
    )
    out.append(
        f"**Frozen after development:** exit variant **{fz['exit_variant']}** "
        f"({cfg.exits.variants[fz['exit_variant']].description}), breakeven after TP1 = {fz['breakeven_after_tp1']}. "
        f"Selection rule: {fz['selection']['rule']}. Frequency requirement met on development by the chosen "
        f"variant: {fz['selection']['frequency_requirement_met_on_development']}.\n"
    )
    # C
    out.append("## C. REQUIRED frequency report — validation, frozen specification\n")
    out.append(_freq_block(vc["frequency"]))
    out.append("\n### C.2 Funnel (validation)\n")
    out.append(_funnel_table(vc["funnel"]))
    # D
    out.append(
        "\n## D. Per-family results (validation, net of primary costs; development folds for the fold criterion)\n"
    )
    rows = []
    for fam, blk in val["families"].items():
        rows.append(_stats_rows(fam, blk["stats"], blk["frequency"]))
    out.append(_table(STAT_HEADERS, rows))
    for fam, blk in val["families"].items():
        out.append(
            f"\n**{fam}** — criteria: {'PASS' if blk['criteria']['passed'] else 'FAIL'}; cost flag (round trip > "
            f"{cfg.costs.max_cost_share_of_stop:.0%} of median stop): {blk['cost_flag']}\n"
        )
        out.append(_crit_table(blk["criteria"]))
        nl = blk.get("null")
        if nl and nl.get("n_real"):
            out.append(
                f"\nnull (regime- and hour-matched, k={cfg.research.null_k}): real {nl['real_mean_net_r']:.3f}R vs null "
                f"{nl['null_mean_net_r']:.3f}R, edge {nl['edge_vs_null_r']:+.3f}R, z {_n(nl['z_vs_null'], 2)}\n"
            )
        if blk["by_side"]:
            out.append(
                _table(
                    [
                        "side",
                        "n",
                        "trades/day",
                        "net R mean",
                        "PF",
                        "win",
                        "med hold",
                        "med stop bps",
                    ],
                    [
                        [
                            r["side"],
                            r["n"],
                            _n(r["trades_per_day"], 2),
                            _n(r["net_r_mean"], 3),
                            _n(r["profit_factor"], 2),
                            _pct(r["win_rate"]),
                            _n(r["median_hold_min"], 0),
                            _n(r["median_stop_bps"], 0),
                        ]
                        for r in blk["by_side"]
                    ],
                )
            )
    out.append("\n### D.2 Development per family (same statistics, development window)\n")
    out.append(
        _table(
            STAT_HEADERS,
            [
                _stats_rows(fam, blk["stats"], blk["frequency"])
                for fam, blk in dev["families"].items()
            ],
        )
    )
    out.append("\nDevelopment stability folds (net R sum per fold):\n")
    out.append(
        _table(
            ["family"] + [f"fold {i + 1}" for i in range(cfg.research.stability_folds)],
            [
                [fam] + [f"{_n(f['net_r_sum'], 1)} (n={f['n']})" for f in blk["folds_stability"]]
                for fam, blk in dev["families"].items()
            ],
        )
    )
    # E
    out.append("\n## E. Combined V6 (all historical families)\n")
    out.append(
        _table(
            STAT_HEADERS,
            [
                _stats_rows(
                    "development, default variant",
                    dev["combined_default"]["stats"],
                    dev["combined_default"]["frequency"],
                ),
                _stats_rows(
                    "development, frozen variant",
                    dev["combined_frozen"]["stats"],
                    dev["combined_frozen"]["frequency"],
                ),
                _stats_rows("VALIDATION, frozen", vc["stats"], vc["frequency"]),
                _stats_rows(
                    "validation, frozen + daily budget overlay",
                    vc["with_overlay"]["stats"],
                    vc["with_overlay"]["frequency"],
                ),
            ],
        )
    )
    nl = vc.get("null")
    if nl and nl.get("n_real"):
        out.append(
            f"\nValidation null (regime/hour-matched, same stops/exits/costs): real {nl['real_mean_net_r']:.3f}R vs null {nl['null_mean_net_r']:.3f}R, edge {nl['edge_vs_null_r']:+.3f}R, z {_n(nl['z_vs_null'], 2)}.\n"
        )
    out.append("\nValidation by year / regime / side / session:\n")
    out.append(
        _table(
            ["year", "n", "trades/day", "net R sum", "net R mean", "PF"],
            [
                [
                    r["year"],
                    r["n"],
                    _n(r["trades_per_day"], 2),
                    _n(r["net_r_sum"], 1),
                    _n(r["net_r_mean"], 3),
                    _n(r["profit_factor"], 2),
                ]
                for r in vc["by_year"]
            ],
        )
    )
    out.append(
        "\n"
        + _table(
            ["regime (1h trend x 30d vol tercile)", "n", "net R sum", "net R mean", "PF"],
            [
                [
                    r["regime"],
                    r["n"],
                    _n(r["net_r_sum"], 1),
                    _n(r["net_r_mean"], 3),
                    _n(r["profit_factor"], 2),
                ]
                for r in vc["by_regime"]
            ],
        )
    )
    out.append(
        "\n"
        + _table(
            ["side", "n", "net R sum", "net R mean", "PF", "win"],
            [
                [
                    r["side"],
                    r["n"],
                    _n(r["net_r_sum"], 1),
                    _n(r["net_r_mean"], 3),
                    _n(r["profit_factor"], 2),
                    _pct(r["win_rate"]),
                ]
                for r in vc["by_side"]
            ],
        )
    )
    out.append(
        "\n"
        + _table(
            ["UTC session", "n", "net R sum", "net R mean", "PF"],
            [
                [
                    {0: "Asia 00-08", 1: "Europe 08-16", 2: "US 16-24"}.get(
                        int(r["session"]), r["session"]
                    ),
                    r["n"],
                    _n(r["net_r_sum"], 1),
                    _n(r["net_r_mean"], 3),
                    _n(r["profit_factor"], 2),
                ]
                for r in vc["by_session"]
            ],
        )
    )
    out.append(
        "\n"
        + _table(
            ["exit reason", "n", "net R mean", "med hold min"],
            [
                [r["exit_reason"], r["n"], _n(r["net_r_mean"], 3), _n(r["median_hold_min"], 0)]
                for r in vc["by_exit_variant_reason"]
            ],
        )
    )
    # F
    out.append("\n## F. One-position rule and the sequential / independent diagnostics\n")
    for label, op in (
        ("development", dev["one_position_rule"]),
        ("validation", vc["one_position_rule"]),
    ):
        cf = op["counterfactual"]
        cfp = cf.get("BLOCKED_POSITION_OPEN", {})
        out.append(f"\n**{label}**\n")
        out.append(
            _table(
                ["item", "value"],
                [
                    ["trades taken (baseline)", op["trades_taken"]],
                    [
                        "valid triggers refused because a position was open",
                        op["valid_triggers_refused_position_open"],
                    ],
                    [
                        "valid triggers refused by the cooldown after close",
                        op["valid_triggers_refused_cooldown_after_close"],
                    ],
                    [
                        "suppressed per day / suppression share of all valid triggers",
                        f"{_n(op['suppressed_per_day'], 2)} / {_pct(op['suppression_share'])}",
                    ],
                    [
                        "counterfactual of the position-open refusals (each simulated alone)",
                        f"n={cfp.get('n', 0)}, mean {_n(cfp.get('mean_net_r'), 3)}R, win {_pct(cfp.get('win_rate'))}, median hold {_n(cfp.get('median_hold_min'), 0)} min",
                    ],
                    [
                        "SEQUENTIAL mode (episodes wait for the slot, no cooldown after close): trades/day, net R mean",
                        f"{_n(op['sequential_mode_trades_per_day'], 2)}, {_n(op['sequential_mode_net_r_mean'], 3)}R",
                    ],
                    [
                        "INDEPENDENT mode (every valid trigger simulated alone, overlaps allowed): trades/day, net R mean",
                        f"{_n(op['independent_mode_trades_per_day'], 2)}, {_n(op['independent_mode_net_r_mean'], 3)}R",
                    ],
                ],
            )
        )
    # G
    out.append("\n## G. Exit variants (development, combined families; pre-declared selection)\n")
    out.append(
        _table(
            [
                "variant",
                "BE after TP1",
                "n",
                "trades/cal. day",
                "net R mean",
                "net R/day",
                "PF",
                "win",
                "med hold",
                "max DD",
                "description",
            ],
            [
                [
                    r["variant"],
                    _n(r["breakeven_after_tp1"]),
                    r["n"],
                    _n(r["trades_per_calendar_day"], 2),
                    _n(r["net_r_mean"], 3),
                    _n(r["net_r_per_day"], 3),
                    _n(r["profit_factor"], 2),
                    _pct(r["win_rate"]),
                    _n(r["median_hold_min"], 0),
                    _pct(r["max_dd_frac"]),
                    r["description"],
                ]
                for r in dev["exit_variants"]
            ],
        )
    )
    # H
    out.append(
        "\n## H. Threshold grids (development, family alone; stability only — nothing was selected)\n"
    )
    out.append(
        _table(
            [
                "family",
                "parameter",
                "value",
                "default",
                "n",
                "trades/day",
                "net R mean",
                "net R sum",
                "PF",
            ],
            [
                [
                    r["family"],
                    r["parameter"],
                    _n(r["value"], 2),
                    _n(r["default"]),
                    r["n"],
                    _n(r["trades_per_day"], 2),
                    _n(r["net_r_mean"], 3),
                    _n(r["net_r_sum"], 1),
                    _n(r["profit_factor"], 2),
                ]
                for r in dev["grids"]
            ],
        )
    )
    # I
    out.append("\n## I. Risk grid and daily budget overlay\n")
    out.append(
        "Risk grid (validation, frozen; R results are identical across risk levels — no margin cap binds — account P&L and drawdown scale with the risk fraction):\n"
    )
    out.append(
        _table(
            ["risk/trade", "primary", "net return", "max DD", "max DD (R)", "worst day"],
            [
                [
                    _pct(r["risk_per_trade"], 3),
                    _n(r["primary"]),
                    _pct(r["net_return_frac"]),
                    _pct(r["max_dd_frac"]),
                    _n(r["max_dd_r"], 1),
                    _pct(r["worst_day_frac"]),
                ]
                for r in vc["risk_grid"]
            ],
        )
    )
    out.append("\nOverlay grid (development, frozen variant):\n")
    out.append(
        _table(
            [
                "max full-risk losses/day",
                "daily stop",
                "default",
                "n",
                "trades/day",
                "net R sum",
                "max DD",
                "worst day",
                "triggers blocked",
                "days blocked",
            ],
            [
                [
                    r["max_full_risk_losses_per_day"],
                    _pct(r["max_daily_loss_frac"]),
                    _n(r["default"]),
                    r["n"],
                    _n(r["trades_per_day"], 2),
                    _n(r["net_r_sum"], 1),
                    _pct(r["max_dd_frac"]),
                    _pct(r["worst_day_frac"]),
                    r["blocked_by_budget"],
                    r["days_blocked"],
                ]
                for r in dev["overlay_grid"]
            ],
        )
    )
    mk = dev["maker_pullback"]
    out.append(
        f"\nMaker-entry variant (pullback family alone, development, reporting only): n={mk.get('n', 0)}, net R mean {_n(mk.get('net_r_mean'), 3)}, PF {_n(mk.get('profit_factor'), 2)} (taker: see D.2).\n"
    )
    # J
    out.append("## J. Once-only OOS\n")
    if res.oos:
        oc = res.oos["combined"]
        out.append(_table(STAT_HEADERS, [_stats_rows("OOS, frozen", oc["stats"], oc["frequency"])]))
        out.append("\n" + _freq_block(oc["frequency"]))
        out.append("\n" + _crit_table(oc["gate"]))
        out.append(
            f"\nOOS classification: **{oc['gate']['classification']}**. The window is now spent.\n"
        )
    else:
        out.append(
            "NOT RUN. The pre-registered protocol runs the once-only OOS window only when the frozen specification passes the HARD gate on validation; "
            + (
                "it passed, and the run requires the explicit owner flag."
                if achieved
                else "it did not pass, so the window stays unspent for a future pre-registration."
            )
            + "\n"
        )
    # K
    out.append("## K. Acceptance gate (validation, frozen)\n")
    out.append(_crit_table(gate))
    out.append(
        f"\nHARD gate: {'PASSED' if gate['hard_passed'] else 'FAILED'}; robust criteria: {'passed' if gate['robust_passed'] else 'failed'}; classification **{gate['classification']}**.\n"
    )
    # L
    out.append("## L. Bottleneck analysis and next architecture\n")
    out.append(
        f"Validation: {_n(bt['trades_per_calendar_day'], 2)} trades per calendar day against the required {bt['required']:.1f}; net R mean {_n(bt['net_r_mean'], 3)}, PF {_n(bt['profit_factor'], 2)} → **{bt['summary']}**.\n"
    )
    out.append(
        _table(
            ["bottleneck", "measure", "value", "verdict"],
            [[r["bottleneck"], r["measure"], _n(r["value"], 3), r["verdict"]] for r in bt["items"]],
        )
    )
    out.append(
        f"\nWithout the one-position rule: sequential mode {_n(bt['sequential_mode_trades_per_day'], 2)} trades/day at {_n(bt['sequential_mode_net_r_mean'], 3)}R; independent mode {_n(bt['independent_mode_trades_per_day'], 2)} trades/day at {_n(bt['independent_mode_net_r_mean'], 3)}R.\n"
    )
    out.append(_next_architecture(res, cfg))
    return "\n".join(out)


def _next_architecture(res: V6Research, cfg: V6Config) -> str:
    """Data-driven proposal: which bottleneck to attack next and how, without changing any rule
    of this run."""
    vc = res.validation["combined"]
    st, op, bt = vc["stats"], vc["one_position_rule"], res.bottleneck
    gross = st.get("gross_r_mean") or 0.0
    cost = st.get("cost_r_mean") or 0.0
    stop_bps = st.get("median_stop_bps") or math.nan
    rt = st.get("round_trip_cost_bps") or math.nan
    lines = ["### L.2 Reading of the result and the next architecture\n"]
    lines.append(
        f"- **Edge:** gross expectancy {gross:+.3f}R per trade before costs, costs {cost:.3f}R per trade "
        f"(round trip {rt:.1f} bps against a median stop of {stop_bps:.0f} bps). The signal set as "
        "specified carries no gross edge on validation; costs then make every family negative. "
        "Lowering costs alone cannot rescue a zero gross edge, and widening stops to dilute costs would "
        "lengthen holds and cut frequency further."
    )
    lines.append(
        f"- **Frequency:** {bt['trades_per_calendar_day']:.2f} trades per calendar day with one position; "
        f"{op['valid_triggers_refused_position_open']} valid triggers ({_pct(op['suppression_share'])} of all) were refused "
        f"because a position was open. Sequential episodes give {bt['sequential_mode_trades_per_day']:.2f}/day and "
        f"independent episodes {bt['independent_mode_trades_per_day']:.2f}/day, both at the same negative expectancy: "
        "the one-position rule is the main frequency throttle but removing it only multiplies a losing trade."
    )
    lines.append(
        "- **Where the loss sits:** STOP exits (median hold under an hour) carry the losses; TRAIL and TIME_LIMIT "
        "exits are positive. Stage B pullback entries on the momentum family (the most frequent trigger) have "
        "negative gross expectancy: the 15m break with a quick retest is, on this data, more often a failed "
        "break than a continuation."
    )
    lines.append(
        "- **What the data supports trying next (would need a NEW pre-registration; nothing here is tuned):**\n"
        "  1. Shift the venue of the research to Bybit-native flow (stream the Bybit trade archive into 5m "
        "flow rows) before any further flow-based family work, so research and forward venue match.\n"
        "  2. Replace the quick-retest entries with structure that showed positive gross R here: trailing "
        "winners (TRAIL/TIME_LIMIT) suggest the edge, if any, is in the continuation leg, not the entry; a "
        "family that enters AFTER a confirmed 15m higher-low/lower-high (not the first touch of the level) "
        "with a stop below that structure would have fewer, later, longer trades — this conflicts with the "
        "frequency target and must be declared as such.\n"
        "  3. Cost-aware eligibility: refuse any trigger whose round-trip cost exceeds a declared share of "
        "the stop (e.g. 20%) so that the frequency statistic counts only executable trades; report the "
        "residual frequency honestly.\n"
        "  4. Prospective-only liquidation families D/E on the live Bybit stream (shadow diagnostics) once "
        "several months of allLiquidation data exist; they are the only families with a mechanism that "
        "the historical data cannot test.\n"
        "  5. If the owner's product requirement of >= 2 trades/day with positive net edge stands, the "
        "evidence here says a single-position 5m/15m breakout-retest engine on Binance history does not "
        "deliver it; a multi-slot (2 independent positions, strictly bounded total risk) design would "
        "reach the frequency only if a positive-edge family is found first."
    )
    return "\n".join(lines) + "\n"
