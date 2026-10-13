"""Render reports/BTC_V11_FAST_MULTIFACTOR_BACKTEST.md from the research JSON."""

from __future__ import annotations

from typing import Any


def _v(x: Any, nd: int = 3, pct: bool = False) -> str:
    if x is None:
        return "n/a"
    try:
        f = float(x)
    except (TypeError, ValueError):
        return str(x)
    if f != f:
        return "n/a"
    if f == float("inf"):
        return "inf"
    return f"{f * 100:.1f}%" if pct else f"{f:.{nd}f}"


def _row(label: str, st: dict[str, Any]) -> str:
    return (
        f"| {label} | {st.get('n', 0)} | {_v(st.get('trades_per_day'), 2)} | {_v(st.get('win_rate'), 0, True)} | "
        f"{_v(st.get('gross_r'))} | {_v(st.get('cost_r'))} | {_v(st.get('net_r'))} | {_v(st.get('profit_factor'), 2)} | "
        f"{_v(st.get('total_net_r'), 1)} | {_v(st.get('max_dd_pct'), 2)}% | {_v(st.get('median_hold_min'), 0)} / "
        f"{_v(st.get('p75_hold_min'), 0)} / {_v(st.get('p90_hold_min'), 0)} |"
    )


HEAD = "| window | n | trades/day | win rate | gross R | cost R | NET R | PF | total net R | max DD | hold p50/p75/p90 (min) |\n|---|---|---|---|---|---|---|---|---|---|---|"


def _sub_table(title: str, d: dict[str, Any]) -> list[str]:
    out = [
        f"**{title}**",
        "",
        "| bucket | n | net R | win rate | PF | total net R | median hold |",
        "|---|---|---|---|---|---|---|",
    ]
    for k, s in d.items():
        out.append(
            f"| {k} | {s.get('n', 0)} | {_v(s.get('net_r'))} | {_v(s.get('win_rate'), 0, True)} | {_v(s.get('pf'), 2)} | {_v(s.get('total_net_r'), 1)} | {_v(s.get('median_hold_min'), 0)} |"
        )
    out.append("")
    return out


def render_backtest(res: dict[str, Any]) -> str:
    m, w, cl = res["manifest"], res["windows"], res["classification"]
    tr, va = w["train"]["stats"], w["validation"]["stats"]
    out = ["# BTC V11 — FAST MULTI-FACTOR INTRADAY: pre-registered historical experiment\n"]
    out.append(
        f"Generated {m['generated_utc']} · V11 {m['v11_version']} · config `{m['config_hash'][:12]}` (strategy `{m['strategy_hash'][:12]}`) · code `{m['code_version']}` · Bybit 1m dataset `{m['dataset_hash_1m'][:12]}` · windows TRAIN {m['windows']['train'][0]} -> {m['windows']['train'][1]}, VALIDATION {m['windows']['validation'][0]} -> {m['windows']['validation'][1]} · locked holdout: {m['locked_holdout']}.\n"
    )
    out.append(
        "ONE frozen specification (docs/BTC_V11_FAST_MULTIFACTOR_DESIGN.md), run once on each window; nothing selected, nothing tuned. Historically testable groups: "
        + ", ".join(m["historically_testable_groups"])
        + ". FORWARD-ONLY (not in this run, never backfilled): "
        + ", ".join(m["forward_only_groups"])
        + "; the score is renormalised over the available groups (available weight "
        + _v(w["train"]["score_dist"]["available_weight"], 0)
        + " of 100). Costs: taker 5.5 bps/side, half-spread 0.5 bps/side, slippage 1 bps (3 bps on stops), Binance funding at 8-h marks; NET is the headline.\n"
    )
    out.append(f"## 0. Classification: **{cl['classification']}** — {cl['reason']}\n")
    out.append("## 1. Headline (NET after primary costs)\n")
    out.append(HEAD)
    out.append(_row("TRAIN", tr))
    out.append(_row("VALIDATION", va))
    out.append(_row("TRAIN x1.5 costs", w["train"]["stress"]))
    out.append(_row("VALIDATION x1.5 costs", w["validation"]["stress"]))
    out.append("")
    out.append("## 2. Required metrics\n")
    out.append("| metric | TRAIN | VALIDATION |\n|---|---|---|")
    keys = [
        ("total trades", "n", 0),
        ("trades / calendar day", "trades_per_day", 2),
        ("trades / active day", "trades_per_active_day", 2),
        ("median trades / day", "median_trades_per_day", 1),
        ("zero-trade days", "zero_trade_days", 0),
        ("zero-trade day share", "zero_trade_day_share", 2),
        ("LONG", "n_long", 0),
        ("SHORT", "n_short", 0),
        ("win rate", "win_rate", 3),
        ("gross R / trade", "gross_r", 3),
        ("net R / trade", "net_r", 3),
        ("cost R / trade", "cost_r", 3),
        ("avg winner R", "avg_winner_r", 3),
        ("avg loser R", "avg_loser_r", 3),
        ("payoff ratio", "payoff_ratio", 2),
        ("profit factor", "profit_factor", 2),
        ("total net R", "total_net_r", 1),
        ("total net USDT", "total_net_usd", 1),
        ("max DD USDT", "max_dd_usd", 1),
        ("max DD % of 5000", "max_dd_pct", 2),
        ("max DD R", "max_dd_r", 1),
        ("median hold min", "median_hold_min", 0),
        ("p75 hold", "p75_hold_min", 0),
        ("p90 hold", "p90_hold_min", 0),
        ("mean hold", "mean_hold_min", 0),
        ("cost USDT / trade", "cost_usd_per_trade", 3),
        ("gross USDT / trade", "gross_usd_per_trade", 3),
        ("net USDT / trade", "net_usd_per_trade", 3),
        ("cost % of gross edge", "cost_pct_of_gross", 0),
        ("mean leverage", "mean_leverage", 2),
        ("mean margin USDT", "mean_margin_usd", 0),
        ("median stop %", "median_stop_pct", 2),
        ("MFE R mean", "mfe_r_mean", 2),
        ("MAE R mean", "mae_r_mean", 2),
        ("skipped while open", "skipped_while_open", 0),
    ]
    for lab, k, nd in keys:
        out.append(f"| {lab} | {_v(tr.get(k), nd)} | {_v(va.get(k), nd)} |")
    out.append(f"| family A / B / C | {tr.get('by_family_n')} | {va.get('by_family_n')} |")
    out.append(f"| exits | {tr.get('exits')} | {va.get('exits')} |")
    out.append(f"| funnel counters | {tr.get('counters')} | {va.get('counters')} |")
    out.append(f"| blockers | {tr.get('blockers')} | {va.get('blockers')} |")
    out.append(
        f"| family funnel (armed/triggers/executed) | {tr.get('by_family_funnel')} | {va.get('by_family_funnel')} |"
    )
    out.append(
        f"| score dist long p50/p90/p99 | {[round(x, 1) for x in w['train']['score_dist']['long_p50_p90_p99']]} | {[round(x, 1) for x in w['validation']['score_dist']['long_p50_p90_p99']]} |"
    )
    out.append(
        f"| share of minutes with a side >= candidate / entry | {_v(w['train']['score_dist']['share_ge_candidate'], 3)} / {_v(w['train']['score_dist']['share_ge_entry'], 3)} | {_v(w['validation']['score_dist']['share_ge_candidate'], 3)} / {_v(w['validation']['score_dist']['share_ge_entry'], 3)} |"
    )
    out.append("")
    for win in ("train", "validation"):
        out.append(f"## 3. Breakdowns — {win.upper()}\n")
        b = w[win]["breakdown"]
        for title, key in (
            ("Setup family", "family"),
            ("Side", "side"),
            ("Family x side", "family_side"),
            ("Trend regime (struct15 at decision)", "trend_regime"),
            ("Volatility regime (ATR5 tercile at decision)", "vol_regime"),
            ("Hour of day (UTC)", "hour_bucket"),
            ("Exit reason", "exit_reason"),
        ):
            out += _sub_table(title, b[key])
    out.append("## 4. The six questions\n")

    def fam_pos(win: str) -> str:
        b = w[win]["breakdown"]["family"]
        return ", ".join(f"{f}: {_v(s.get('net_r'))} R (n {s.get('n', 0)})" for f, s in b.items())

    q = [
        f"1. **Is 3-8/day reachable?** TRAIN {_v(tr.get('trades_per_day'), 2)}/day ({_v(tr.get('trades_per_active_day'), 2)} per active day, {_v(tr.get('zero_trade_day_share'), 0, True)} zero days), VALIDATION {_v(va.get('trades_per_day'), 2)}/day; skipped while a position was open: {tr.get('skipped_while_open')} / {va.get('skipped_while_open')}. {'Yes' if cl['frequency_ok'] else 'No'} under the frozen rules.",
        f"2. **Gross edge or turnover?** gross R per trade TRAIN {_v(tr.get('gross_r'))}, VALIDATION {_v(va.get('gross_r'))}; costs {_v(tr.get('cost_r'))} R per trade ({_v(tr.get('cost_pct_of_gross'), 0)}% of gross on TRAIN).",
        f"3. **Does each setup contribute?** TRAIN {fam_pos('train')}; VALIDATION {fam_pos('validation')}.",
        f"4. **Winners vs losers and costs?** avg winner {_v(tr.get('avg_winner_r'), 2)} R vs avg loser {_v(tr.get('avg_loser_r'), 2)} R (payoff {_v(tr.get('payoff_ratio'), 2)}), win rate {_v(tr.get('win_rate'), 0, True)} on TRAIN; VALIDATION payoff {_v(va.get('payoff_ratio'), 2)}, win rate {_v(va.get('win_rate'), 0, True)}.",
        f"5. **Positive in validation?** VALIDATION net R {_v(va.get('net_r'))}, PF {_v(va.get('profit_factor'), 2)}, total {_v(va.get('total_net_r'), 1)} R; TRAIN net R {_v(tr.get('net_r'))}, PF {_v(tr.get('profit_factor'), 2)}. {'Yes' if cl['validation_net_positive'] else 'No'}.",
        f"6. **Hold time 10-60 min?** median {_v(tr.get('median_hold_min'), 0)} / p75 {_v(tr.get('p75_hold_min'), 0)} / p90 {_v(tr.get('p90_hold_min'), 0)} min on TRAIN, {_v(va.get('median_hold_min'), 0)} / {_v(va.get('p75_hold_min'), 0)} / {_v(va.get('p90_hold_min'), 0)} on VALIDATION (hard cap 60). {'Yes' if cl['hold_ok'] else 'No'}.",
    ]
    out += q
    out.append(
        "\nNo threshold, length, weight, stop or target was changed after this result (design section 11). The locked holdout was not read.\n"
    )
    return "\n".join(out)
