"""Markdown rendering of the V6.1 research: the 13 deliverables of the design (section 16)."""

from __future__ import annotations

import math
from typing import Any

from btc_swing.v61.config import V61Config
from btc_swing.v61.research import V61Research


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
        out.append("| " + " | ".join(v if isinstance(v, str) else _n(v) for v in r) + " |")
    return "\n".join(out)


HEAD = ["stream", "n", "trades/cal.day", "net R mean", "boot 95% CI", "net R sum", "PF", "win", "gross R", "fee R", "slip R", "cost/gross", "med stop bps", "med hold min", "max DD"]  # fmt: skip


def _srow(label: str, s: dict[str, Any]) -> list[Any]:
    st, fq = s["stats"], s["frequency"]
    return [
        label,
        st.get("n", 0),
        _n(fq["mean_trades_per_calendar_day"], 2),
        _n(st.get("net_r_mean"), 3),
        _n(st.get("net_r_boot_lo"), 3) + " / " + _n(st.get("net_r_boot_hi"), 3),
        _n(st.get("net_r_sum"), 1),
        _n(st.get("profit_factor"), 2),
        _pct(st.get("win_rate")),
        _n(st.get("gross_r_mean"), 3),
        _n(st.get("fee_r_mean"), 3),
        _n(st.get("slip_r_mean"), 3),
        _pct(st.get("cost_share_of_gross")),
        _n(st.get("median_stop_bps"), 0),
        _n(st.get("median_hold_min"), 0),
        _pct(st.get("max_dd_frac")),
    ]


def _freq(fq: dict[str, Any]) -> str:
    pd = fq.get("pct_days_v61", {})
    rows = [
        ["calendar days / executed trades", f"{fq['calendar_days']} / {fq['trades']}"],
        [
            "mean / median trades per CALENDAR day",
            f"{_n(fq['mean_trades_per_calendar_day'], 2)} / {_n(fq['median_trades_per_calendar_day'], 1)}",
        ],
        [
            "% days 0 / 1 / 2 / 3-4 / 5-6 / >6",
            " / ".join(_pct(pd.get(k)) for k in ("0", "1", "2", "3-4", "5-6", ">6")),
        ],
        ["max trades in a day", fq["max_trades_in_a_day"]],
        [
            "mean / median hours between entries",
            f"{_n(fq.get('mean_hours_between_entries'), 1)} / {_n(fq.get('median_hours_between_entries'), 1)}",
        ],
        [
            "mean / median holding time (min)",
            f"{_n(fq.get('mean_hold_min'), 0)} / {_n(fq.get('median_hold_min'), 0)}",
        ],
        [
            "weekday / weekend trades per day",
            f"{_n(fq.get('weekday_trades_per_day'), 2)} / {_n(fq.get('weekend_trades_per_day'), 2)}",
        ],
        [
            "session share Asia / Europe / US",
            " / ".join(
                _pct(fq.get("by_session_share", {}).get(k))
                for k in ("asia_00-08", "europe_08-16", "us_16-24")
            ),
        ],
        ["by side / by family", f"{fq.get('by_side', {})} / {fq.get('by_family', {})}"],
        [
            f">= {fq['hard_minimum_trades_per_day']:.1f} trades per calendar day",
            "PASS" if fq["meets_hard_minimum"] else "FAIL",
        ],
    ]
    return _table(["frequency (calendar-day statistics; active days are NOT used)", "value"], rows)


def _hold(h: dict[str, Any]) -> str:
    if not h.get("n"):
        return "no trades"
    b = h["buckets"]
    return _table(
        ["hold-time distribution", "value"],
        [
            [
                "median / mean / p10 / p90 (min)",
                f"{_n(h['median_min'], 0)} / {_n(h['mean_min'], 0)} / {_n(h['p10_min'], 0)} / {_n(h['p90_min'], 0)}",
            ],
            [
                "<30m / 30-60m / 1-2h / 2-4h / 4-6h / 6-8h / >8h",
                " / ".join(
                    _pct(b[k]) for k in ("<30m", "30-60m", "1-2h", "2-4h", "4-6h", "6-8h", ">8h")
                ),
            ],
            [
                "time-stop exits / time-limit exits",
                f"{_pct(h['share_time_stop'])} / {_pct(h['share_time_limit'])}",
            ],
        ],
    )


def _costs(c: dict[str, Any]) -> str:
    rows = []
    for name in ("all_triggers", "executed_trades"):
        d = c.get(name, {})
        if not d.get("n"):
            rows.append([name, 0, "-", "-", "-", "-"])
            continue

        def q(col: str, d: dict[str, Any] = d) -> str:
            v = d.get(col, {})
            return (
                f"{_n(v.get('p25'), 1)} / {_n(v.get('p50'), 1)} / {_n(v.get('p75'), 1)}"
                if v
                else "-"
            )

        rows.append(
            [
                name,
                d["n"],
                q("cost_bps"),
                q("stop_bps"),
                q("cost_share_stop"),
                q("cost_share_target"),
            ]
        )
    return _table(
        [
            "population",
            "n",
            "cost bps p25/p50/p75",
            "stop bps p25/p50/p75",
            "cost/stop p25/p50/p75",
            "cost/target p25/p50/p75",
        ],
        rows,
    )


def render_report_v61(res: V61Research, cfg: V61Config) -> str:
    m, dev, val, fz, fin, vd = (
        res.manifest,
        res.development,
        res.validation,
        res.frozen,
        res.final,
        res.verdict,
    )
    runs, spec = fin["runs"], fin["spec"]
    bv = runs["bybit_validation"]
    out: list[str] = []
    out.append(
        "# BTC V6.1 STRUCTURE + FLOW INTRADAY — research report (development + validation; holdout NOT run)\n"
    )
    out.append(
        f"**>= 2 trades/day: {'PASS' if vd['frequency_pass'] else 'FAIL'}. Net edge: {'PASS' if vd['net_edge_pass'] else 'FAIL'}. Recommendation: {vd['recommendation']}.**\n"
    )
    out.append(
        f"Config `{cfg.strategy_name}` hash `{m['config_hash'][:16]}…`, code `{m['code_version']}`, research hash `{m['research_hash'][:16]}…`, run {m['ran_at']}. "
        "Paper research only: no exchange client exists in this track; V5/V5.1/V6 untouched; the locked holdout 2025-07 -> 2026-09 was neither downloaded nor evaluated.\n"
    )
    # data
    out.append("## 0. Data, venues, availability\n")
    av = m["availability"]
    rows = []
    for venue in ("bybit", "binance"):
        a = av[venue]
        rows.append(
            [
                venue,
                a["first_row"],
                a["last_close"],
                a["rows_in_window"],
                _pct(a["valid_share"], 2),
                a["days_with_invalid_rows"],
                _pct(a["oi_share"], 1),
            ]
        )
    out.append(
        _table(
            [
                "frame",
                "first row",
                "last close",
                "rows (dev+val)",
                "valid rows",
                "days with invalid rows",
                "OI coverage",
            ],
            rows,
        )
    )
    out.append(
        f"\nVenue mismatch: {av['venue_mismatch']}. Primary = Bybit-native price/volume/aggressor flow from the Bybit public trade archive "
        f"(manifest hash `{m['data_hashes'].get('bybit_trades_5m', '')[:12]}…`); Binance frame = robustness comparison. "
        f"Regime share (Bybit frame): {av['bybit']['meta'].get('regime_share')}.\n"
    )
    # 1. families
    out.append(
        "## 1. Family results independently (validation, frozen exits/entry; development below)\n"
    )
    for wn, blk in (("validation", val), ("development", dev)):
        rows = []
        for fam, pair in blk["families"].items():
            for lab in ("structure_only", "structure_flow"):
                rows.append(_srow(f"{fam} [{lab}]", pair[lab]))
        out.append(f"\n**{wn}**\n\n" + _table(HEAD, rows))
    # 2. structure vs flow
    out.append(
        "\n## 2. Structure-only vs structure + flow (declared rule: flow kept only if it improves net R on BOTH windows)\n"
    )
    rows = []
    for fam in dev["families"]:
        d, v_ = dev["families"][fam], val["families"][fam]
        rows.append(
            [
                fam,
                _n(d["flow_delta_net_r"], 3),
                _n(d["flow_trades_ratio"], 2),
                _n(v_["flow_delta_net_r"], 3),
                _n(v_["flow_trades_ratio"], 2),
                "KEEP FLOW" if spec["flow_final"][fam] else "STRUCTURE ONLY",
            ]
        )
    out.append(
        _table(
            [
                "family",
                "dev Δ net R (flow - structure)",
                "dev trades ratio",
                "val Δ net R",
                "val trades ratio",
                "final",
            ],
            rows,
        )
    )
    # 3. entry modes
    out.append("\n## 3. Maker / taker entry comparison (combined families, frozen exits)\n")
    for wn, rows_ in (("development", dev["entry_rows"]), ("validation", val["entry_rows"])):
        out.append(
            f"\n**{wn}**\n\n"
            + _table(
                [
                    "entry mode",
                    "n",
                    "trades/day",
                    "maker fills",
                    "unfilled",
                    "setup gone",
                    "gross R",
                    "fee R",
                    "slip R",
                    "net R",
                    "PF",
                    "cost/stop med",
                ],
                [
                    [
                        r["entry_mode"],
                        r["n"],
                        _n(r["trades_per_day"], 2),
                        _pct(r["maker_fills_share"]),
                        r["unfilled"],
                        r["setup_gone_after_timeout"],
                        _n(r["gross_r_mean"], 3),
                        _n(r["fee_r_mean"], 3),
                        _n(r["slip_r_mean"], 3),
                        _n(r["net_r_mean"], 3),
                        _n(r["profit_factor"], 2),
                        _n(r["cost_share_stop_median"], 2),
                    ]
                    for r in rows_
                ],
            )
        )
    out.append(
        f"\nEntry mode after development: {fz['entry_mode']} ({fz['entry_selection']['rule']}); final: {spec['entry_mode_final']}.\n"
    )
    # 4. stops / costs
    out.append("## 4. Stop-distance and cost analysis\n")
    out.append(
        f"Cost-share grid (development, combined; primary θ = {cfg.costs.cost_share_stop_max:.2f}; θ = 0 means no filter):\n"
    )
    out.append(
        _table(
            [
                "θ (cost/stop max)",
                "primary",
                "n",
                "trades/day",
                "net R mean",
                "net R sum",
                "PF",
                "med stop bps",
                "cost/gross",
                "triggers refused",
            ],
            [
                [
                    _n(r["theta"], 2),
                    _n(r["primary"]),
                    r["n"],
                    _n(r["trades_per_day"], 2),
                    _n(r["net_r_mean"], 3),
                    _n(r["net_r_sum"], 1),
                    _n(r["profit_factor"], 2),
                    _n(r["median_stop_bps"], 0),
                    _pct(r["cost_share_of_gross"]),
                    r["refused_cost_share"],
                ]
                for r in dev["grids"]["cost_share"]
            ],
        )
    )
    out.append("\nMinimum-stop grid (development, combined):\n")
    out.append(
        _table(
            [
                "min stop % (0 = structural)",
                "n",
                "trades/day",
                "net R mean",
                "PF",
                "med stop bps",
                "cost/med stop",
                "med hold",
                "max DD",
            ],
            [
                [
                    _n(r["min_stop_pct"], 2),
                    r["n"],
                    _n(r["trades_per_day"], 2),
                    _n(r["net_r_mean"], 3),
                    _n(r["profit_factor"], 2),
                    _n(r["median_stop_bps"], 0),
                    _pct(r["cost_share_of_median_stop"]),
                    _n(r["median_hold_min"], 0),
                    _pct(r["max_dd_frac"]),
                ]
                for r in dev["grids"]["stop_floor"]
            ],
        )
    )
    out.append("\nNatural stop-distance buckets (final specification, Bybit validation):\n")
    out.append(
        _table(
            [
                "bucket",
                "n",
                "net R mean",
                "PF",
                "med stop bps",
                "cost/stop med",
                "fee+slip R",
                "med hold",
            ],
            [
                [
                    r["bucket"],
                    r["n"],
                    _n(r.get("net_r_mean"), 3),
                    _n(r.get("profit_factor"), 2),
                    _n(r.get("median_stop_bps"), 0),
                    _n(r.get("cost_share_stop_median"), 2),
                    _n(r.get("fee_slip_r_mean"), 3),
                    _n(r.get("median_hold_min"), 0),
                ]
                for r in bv["stop_buckets"]
            ],
        )
    )
    out.append("\nCost distributions (final specification, Bybit validation):\n")
    out.append(_costs(bv["costs"]))
    # 5. hold
    out.append("\n## 5. Hold-time distribution (final specification, Bybit validation)\n")
    out.append(_hold(bv["hold"]))
    out.append("\nExit variant x time-stop grid (development; declared selection):\n")
    out.append(
        _table(
            [
                "variant",
                "time stop h",
                "n",
                "trades/cal.day",
                "net R mean",
                "net R/day",
                "PF",
                "win",
                "med hold",
                "max DD",
                "positive folds",
                "time-stop exits",
            ],
            [
                [
                    r["variant"],
                    _n(r["time_stop_h"], 0),
                    r["n"],
                    _n(r["trades_per_calendar_day"], 2),
                    _n(r["net_r_mean"], 3),
                    _n(r["net_r_per_day"], 3),
                    _n(r["profit_factor"], 2),
                    _pct(r["win_rate"]),
                    _n(r["median_hold_min"], 0),
                    _pct(r["max_dd_frac"]),
                    r["positive_folds"],
                    _pct(r["share_time_stop"]),
                ]
                for r in dev["exit_rows"]
            ],
        )
    )
    out.append(
        f"\nChosen on development: variant **{fz['exit_variant']}**, time stop **{fz['time_stop_h']:g} h** ({fz['exit_selection']['rule']}); frequency+fold requirement met on development: {fz['exit_selection']['frequency_and_fold_requirement_met_on_development']}.\n"
    )
    # 6. frequency
    out.append("## 6. Frequency distribution (final specification, Bybit validation)\n")
    out.append(_freq(bv["frequency"]))
    op = bv["one_position"]
    out.append(
        "\n"
        + _table(
            ["one-position rule", "value"],
            [
                [
                    "valid triggers refused: position open / cooldown / cost share",
                    f"{op['refused_position_open']} / {op['refused_cooldown']} / {op['refused_cost_share']}",
                ],
                [
                    "suppressed per day / suppression share",
                    f"{_n(op['suppressed_per_day'], 2)} / {_pct(op['suppression_share'])}",
                ],
                [
                    "counterfactual of position-open refusals: mean net R / median hold",
                    f"{_n(op['counterfactual_mean_net_r'], 3)} / {_n(op['counterfactual_median_hold_min'], 0)} min",
                ],
                [
                    "sequential mode: trades/day, net R mean",
                    f"{_n(bv['mode_sequential']['trades_per_day'], 2)}, {_n(bv['mode_sequential']['net_r_mean'], 3)}",
                ],
                [
                    "independent mode: trades/day, net R mean",
                    f"{_n(bv['mode_independent']['trades_per_day'], 2)}, {_n(bv['mode_independent']['net_r_mean'], 3)}",
                ],
            ],
        )
    )
    # 7. expectancy
    out.append("\n## 7. Net expectancy and profit factor (final specification)\n")
    out.append(_table(HEAD, [_srow(k, s) for k, s in runs.items()]))
    nl = bv.get("null")
    if nl and nl.get("n_real"):
        out.append(
            f"\nValidation null (regime/hour-matched, same stops/exits/costs): real {nl['real_mean_net_r']:.3f}R vs null {nl['null_mean_net_r']:.3f}R, edge {nl['edge_vs_null_r']:+.3f}R, z {_n(nl['z_vs_null'], 2)}.\n"
        )
    out.append("\nBy family / side / 1h regime / exit reason (Bybit validation):\n")
    out.append(
        _table(
            ["family", "n", "trades/day", "net R mean", "net R sum", "PF", "win", "med hold"],
            [
                [
                    r["family"],
                    r["n"],
                    _n(r["trades_per_day"], 2),
                    _n(r["net_r_mean"], 3),
                    _n(r["net_r_sum"], 1),
                    _n(r["profit_factor"], 2),
                    _pct(r["win_rate"]),
                    _n(r["median_hold_min"], 0),
                ]
                for r in bv["by_family"]
            ],
        )
    )
    out.append(
        "\n"
        + _table(
            ["side", "n", "net R mean", "PF", "win"],
            [
                [
                    r["side"],
                    r["n"],
                    _n(r["net_r_mean"], 3),
                    _n(r["profit_factor"], 2),
                    _pct(r["win_rate"]),
                ]
                for r in bv["by_side"]
            ],
        )
    )
    out.append(
        "\n"
        + _table(
            ["1h regime (-1 down / 0 range / 1 up)", "n", "net R mean", "PF"],
            [
                [_n(r["regime_1h"], 0), r["n"], _n(r["net_r_mean"], 3), _n(r["profit_factor"], 2)]
                for r in bv["by_regime_1h"]
            ],
        )
    )
    out.append(
        "\n"
        + _table(
            ["exit reason", "n", "net R mean", "med hold"],
            [
                [r["exit_reason"], r["n"], _n(r["net_r_mean"], 3), _n(r["median_hold_min"], 0)]
                for r in bv["by_exit_reason"]
            ],
        )
    )
    out.append("\nRisk grid (Bybit validation; R results identical across risk levels):\n")
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
                for r in bv["risk_grid"]
            ],
        )
    )
    ov = bv.get("with_overlay", {})
    out.append(
        f"\nWith the daily budget overlay: n={ov.get('n', 0)}, net R mean {_n(ov.get('net_r_mean'), 3)}, max DD {_pct(ov.get('max_dd_frac'))}.\n"
    )
    # 8. walk-forward
    out.append("## 8. Walk-forward stability\n")
    for key in (
        "bybit_development",
        "bybit_validation",
        "binance_development",
        "binance_validation",
    ):
        f = runs[key]["folds"]
        out.append(
            f"\n{key}: "
            + " | ".join(
                f"fold {x['fold']}: {_n(x['net_r_sum'], 1)}R (n={x['n']}, {_n(x['trades_per_day'], 2)}/d)"
                for x in f
            )
        )
    out.append("\n\nFamily threshold grids (development, family alone; stability only):\n")
    out.append(
        _table(
            ["family", "parameter", "value", "default", "n", "trades/day", "net R mean", "PF"],
            [
                [
                    r["family"],
                    r["parameter"],
                    _n(r["value"], 2),
                    _n(r["default"]),
                    r["n"],
                    _n(r["trades_per_day"], 2),
                    _n(r["net_r_mean"], 3),
                    _n(r["profit_factor"], 2),
                ]
                for r in dev["grids"]["family_thresholds"]
            ],
        )
    )
    # 9. venues
    out.append("\n## 9. Bybit-native vs Binance (identical final specification)\n")
    out.append(
        _table(
            HEAD,
            [
                _srow(k, runs[k])
                for k in (
                    "bybit_development",
                    "binance_development",
                    "bybit_validation",
                    "binance_validation",
                )
            ],
        )
    )
    # 10. candidate
    out.append("\n## 10. Exact combined candidate\n")
    out.append(
        _table(
            ["element", "value"],
            [
                ["families", ", ".join(spec["families"])],
                ["flow confirmation per family", str(spec["flow_final"])],
                [
                    "exit variant / time stop",
                    f"{spec['exit_variant']} ({cfg.exits.variants[spec['exit_variant']].description}) / {spec['time_stop_h']:g} h (MFE < {cfg.exits.time_stop_min_mfe_r}R), hard cap {cfg.exits.max_hold_hours:g} h",
                ],
                ["entry mode", spec["entry_mode_final"]],
                [
                    "cost-share eligibility",
                    f"round-trip cost / stop <= {spec['cost_share_stop_max']}",
                ],
                [
                    "stops",
                    f"structural, floor {cfg.stops.floor_atr15} ATR15, cap {cfg.stops.max_atr15} ATR15",
                ],
                [
                    "risk / overlay",
                    f"{100 * spec['risk_per_trade']:.2f}% of {cfg.risk.initial_equity:.0f} USDT; {cfg.overlay.max_full_risk_losses_per_day} full-risk losses or {100 * cfg.overlay.max_daily_loss_frac:.2f}% daily stop",
                ],
                [
                    "one position / cooldowns",
                    f"yes / {cfg.episode.cooldown_after_close_bars}-{cfg.episode.cooldown_after_invalidation_bars}-{cfg.episode.cooldown_after_two_losses_bars} bars",
                ],
            ],
        )
    )
    # 11-13
    out.append("\n## 11-13. Gate and recommendation (Bybit validation, final specification)\n")
    out.append(
        _table(
            ["criterion", "value", "passed"],
            [
                [r["criterion"], _n(r["value"], 3), "PASS" if r["passed"] else "FAIL"]
                for r in vd["rows"]
            ],
        )
    )
    out.append(
        f"\n**11. >= 2 trades/calendar day: {'PASS' if vd['frequency_pass'] else 'FAIL'}.**  **12. Net edge after costs: {'PASS' if vd['net_edge_pass'] else 'FAIL'}.**  "
        f"**13. Locked holdout: {vd['recommendation']}.** The harness stopped before the holdout; spending it needs the owner's explicit decision and a separate run.\n"
    )
    return "\n".join(out)
