"""Markdown report for a BTC swing backtest run. Every section states what the number is and
what it is not; a foundation run proves plumbing, never edge."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any


def _fmt(v: Any, pct: bool = False, nd: int = 3) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "n/a"
        if math.isinf(v):
            return "inf"
        return f"{v * 100:.2f}%" if pct else f"{v:.{nd}f}"
    return str(v)


def _ms(ms: int | float | None) -> str:
    if ms is None:
        return "n/a"
    return datetime.fromtimestamp(float(ms) / 1000.0, tz=UTC).strftime("%Y-%m-%d %H:%M")


def _summary_table(cells: dict[str, dict[str, Any]], title: str, min_n: int) -> list[str]:
    lines = [
        f"### {title}",
        "",
        f"| cell | n | win rate | exp. R | exp. acct % | PF | mean hold h | reliable (n>={min_n}) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for k, s in cells.items():
        if s.get("n", 0) == 0:
            lines.append(f"| {k} | 0 | | | | | | no |")
            continue
        lines.append(
            f"| {k} | {s['n']} | {_fmt(s['win_rate'], pct=True)} | {_fmt(s['expectancy_R'])} | "
            f"{_fmt(s['expectancy_account_pct'], pct=True)} | {_fmt(s['profit_factor'], nd=2)} | "
            f"{_fmt(s['mean_holding_hours'], nd=1)} | {_fmt(s['reliable'])} |"
        )
    lines.append("")
    return lines


def render_report(
    manifest: dict[str, Any], metrics: dict[str, Any], cfg_yaml: str, title: str, caveats: list[str]
) -> str:
    o = metrics["overall"]
    f = metrics["frequency"]
    a = metrics["account"]
    min_n = int(metrics.get("min_cell_n", 0))
    lines: list[str] = [f"# {title}", ""]
    lines += [
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · strategy `{manifest['strategy_name']}` "
        f"{manifest['strategy_version']} · config `{manifest['config_hash'][:12]}` · code `{manifest['code_version']}` "
        f"· result hash `{manifest['result_hash'][:12]}`",
        "",
        "## 0. What this report is",
        "",
    ]
    lines += [f"- {c}" for c in caveats]
    lines += [
        "",
        "## 1. Run",
        "",
        f"- Period: {_ms(manifest['period_start_ms'])} -> {_ms(manifest['period_end_ms'])} UTC "
        f"({manifest['n_5m_evaluations']} five-minute evaluations, {manifest['n_days']:.1f} days)",
        f"- Information mode: {manifest['information_mode']} · provider: {manifest['provider']} · latency: {manifest['latency_minutes']} min",
        "- Data hashes: "
        + ", ".join(f"{k}=`{v[:10]}`" for k, v in manifest["data_hashes"].items()),
        "- Rule versions: " + ", ".join(f"{k}={v}" for k, v in manifest["rule_versions"].items()),
        "",
        "### Regime occupancy (5m bars)",
        "",
        "| regime | bars | share |",
        "|---|---|---|",
    ]
    tot = max(sum(manifest["regime_bar_counts"].values()), 1)
    for k, v in manifest["regime_bar_counts"].items():
        lines.append(f"| {k} | {v} | {v / tot * 100:.1f}% |")
    lines += [
        "",
        "## 2. Frequency (naturally produced, no quota)",
        "",
        f"- Setup episodes: {f['setups_total']} ({_fmt(f['setups_per_day'])}/day)",
        f"- Entries: {f['entries_total']} ({_fmt(f['entries_per_day'])}/day, {_fmt(f['entries_per_week'])}/week)",
        f"- Setup -> entry conversion: {_fmt(f['setup_to_entry_conversion'], pct=True)}",
        "- Episode end reasons: "
        + ", ".join(f"{r['end_reason']}={r['len']}" for r in f["episode_end_reasons"]),
        "",
        "## 3. Trade statistics (primary metric: risk-adjusted expectancy)",
        "",
    ]
    if o.get("n", 0) == 0:
        lines += ["No trades in this run.", ""]
    else:
        lines += [
            "| metric | value |",
            "|---|---|",
            f"| trades | {o['n']} |",
            f"| win rate | {_fmt(o['win_rate'], pct=True)} |",
            f"| average win (R / account) | {_fmt(o['avg_win_R'])} / {_fmt(o['avg_win_account_pct'], pct=True)} |",
            f"| average loss (R / account) | {_fmt(o['avg_loss_R'])} / {_fmt(o['avg_loss_account_pct'], pct=True)} |",
            f"| expectancy (R / account) | **{_fmt(o['expectancy_R'])}** / {_fmt(o['expectancy_account_pct'], pct=True)} |",
            f"| t-stat of mean R | {_fmt(o['t_stat_R'], nd=2)} |",
            f"| profit factor | {_fmt(o['profit_factor'], nd=2)} |",
            f"| mean / median holding (h) | {_fmt(o['mean_holding_hours'], nd=1)} / {_fmt(o['median_holding_hours'], nd=1)} |",
            f"| mean MFE / MAE (R) | {_fmt(o['mean_MFE_R'])} / {_fmt(o['mean_MAE_R'])} |",
            f"| total fees / funding (USDT) | {_fmt(o['total_fees'], nd=2)} / {_fmt(o['total_funding'], nd=2)} |",
            "",
            "### Returns kept separate",
            "",
            "| basis | value |",
            "|---|---|",
            f"| return on underlying: sum of BTC_RETURN (signed, per trade) | {_fmt(o['sum_BTC_RETURN'], pct=True)} (mean {_fmt(o['mean_BTC_RETURN'], pct=True)}) |",
            f"| return on margin: mean RETURN_ON_MARGIN | {_fmt(o['mean_RETURN_ON_MARGIN'], pct=True)} |",
            f"| return on account: sum ACCOUNT_RETURN | {_fmt(o['sum_ACCOUNT_RETURN'], pct=True)} |",
            f"| account: {_fmt(a['initial_equity'], nd=0)} -> {_fmt(a['final_equity'], nd=2)} | {_fmt(a.get('total_account_return'), pct=True)} |",
            f"| max drawdown (daily marks) | {_fmt(a.get('max_drawdown_frac'), pct=True)} |",
            f"| Sharpe (daily, annualised) | {_fmt(a.get('sharpe_daily_annualised'), nd=2)} — {a.get('sharpe_caveat', '')} |",
            "",
        ]
        lines += _summary_table(metrics["by_side"], "LONG vs SHORT", min_n)
        lines += _summary_table(metrics["by_regime"], "By regime at entry", min_n)
        lines += _summary_table(metrics["by_family"], "By setup family", min_n)
        lines += _summary_table(metrics["by_exit_reason"], "By exit reason", min_n)
        rd = metrics["r_distribution"]
        lines += ["### R-multiple distribution", "", "| bin | count |", "|---|---|"]
        lines += [f"| {b} | {c} |" for b, c in zip(rd["bins"], rd["counts"], strict=True)]
        lines += [
            "",
            "quantiles: "
            + ", ".join(f"q{int(q * 100)}={v:.2f}" for q, v in rd["quantiles"].items()),
            "",
        ]
        te = metrics["target_evaluation"]
        lines += [
            "### Target evaluation (counterfactual: reached before the initial stop)",
            "",
            "| target | hit rate |",
            "|---|---|",
        ]
        lines += [
            f"| {k} | {_fmt(v, pct=True)} |" for k, v in te.items() if k.startswith("hit_rate_")
        ]
        lines += [
            f"| TP1 executed | {_fmt(te['tp1_hit_rate'], pct=True)} |",
            f"| TP2 executed | {_fmt(te['tp2_hit_rate'], pct=True)} |",
            f"| structural target, median distance (R) | {_fmt(te['structural_target_median_R'])} |",
            "",
        ]
        ht = metrics["holding_time"]
        lines += [
            "### Holding time",
            "",
            f"- mean {_fmt(ht['mean_hours'], nd=1)} h, median {_fmt(ht['median_hours'], nd=1)} h, p90 {_fmt(ht['p90_hours'], nd=1)} h, max {_fmt(ht['max_hours'], nd=1)} h",
            f"- < 1h: {_fmt(ht['frac_under_1h'], pct=True)} · 1h-1d: {_fmt(ht['frac_1h_to_1d'], pct=True)} · 1d-3d: {_fmt(ht['frac_1d_to_3d'], pct=True)} · > 3d: {_fmt(ht['frac_over_3d'], pct=True)}",
            "",
        ]
        lr = metrics["leverage_risk"]
        lines += [
            "## 4. Leverage and liquidation risk (modelled explicitly)",
            "",
            "| metric | value |",
            "|---|---|",
            f"| leverage used (count by level) | {', '.join(f'{d["leverage"]:g}x={d["len"]}' for d in lr['leverage_distribution'])} |",
            f"| min stop-to-liquidation ratio | {_fmt(lr['min_stop_to_liquidation_ratio'], nd=1)} |",
            f"| min liquidation distance (% / ATR) | {_fmt(lr['min_liquidation_distance_pct'], pct=True)} / {_fmt(lr['min_liquidation_distance_atr'], nd=1)} |",
            f"| worst MAE (% / R) | {_fmt(lr['worst_MAE_pct'], pct=True)} / {_fmt(lr['worst_MAE_R'])} |",
            f"| worst single-trade account loss | {_fmt(lr['max_account_loss_single_trade'], pct=True)} |",
            f"| max planned loss at stop (account) | {_fmt(lr['max_planned_account_loss_at_stop'], pct=True)} |",
            f"| max loss if liquidated (margin / account) | {_fmt(lr['max_account_loss_if_liquidated'], pct=True)} |",
            f"| liquidations | {lr['n_liquidations']} |",
            f"| trades sized below target risk (margin cap) | {lr['n_risk_capped']} |",
            "",
        ]
    lines += [
        "## 5. Configuration (pre-registered defaults, untuned)",
        "",
        "```yaml",
        cfg_yaml.strip(),
        "```",
        "",
    ]
    return "\n".join(lines)
