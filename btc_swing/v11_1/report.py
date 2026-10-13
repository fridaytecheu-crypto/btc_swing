"""Render reports/BTC_V11_1_COST_AWARE_INTRADAY_BACKTEST.md from the V11.1 research JSON."""

from __future__ import annotations

from typing import Any

from btc_swing.v11.report import HEAD, _row, _sub_table, _v

REQUIRED = (
    ("total trades", "n", 0, False),
    ("trades / calendar day", "trades_per_day", 2, False),
    ("trades / active day", "trades_per_active_day", 2, False),
    ("zero-trade days", "zero_trade_days", 0, False),
    ("zero-trade day share", "zero_trade_day_share", 2, False),
    ("median hold min", "median_hold_min", 0, False),
    ("p75 hold", "p75_hold_min", 0, False),
    ("p90 hold", "p90_hold_min", 0, False),
    ("win rate", "win_rate", 3, False),
    ("avg winner R", "avg_winner_r", 3, False),
    ("avg loser R", "avg_loser_r", 3, False),
    ("gross R / trade", "gross_r", 3, False),
    ("cost R / trade", "cost_r", 3, False),
    ("net R / trade", "net_r", 3, False),
    ("profit factor", "profit_factor", 2, False),
    ("total net R", "total_net_r", 1, False),
    ("max DD USDT", "max_dd_usd", 1, False),
    ("max DD % of 5000", "max_dd_pct", 2, False),
    ("max DD R", "max_dd_r", 1, False),
    ("LONG", "n_long", 0, False),
    ("SHORT", "n_short", 0, False),
    ("cost USDT / trade", "cost_usd_per_trade", 3, False),
    ("median stop %", "median_stop_pct", 2, False),
    ("mean leverage", "mean_leverage", 2, False),
    ("mean evidence groups (executed)", "evidence_n_mean_executed", 2, False),
    ("skipped while open (one-position)", "skipped_while_open", 0, False),
)


def _cmp_row(label: str, a: dict[str, Any], b: dict[str, Any], key: str, nd: int) -> str:
    va, vb = a.get(key), b.get(key)
    try:
        delta = _v(float(vb) - float(va), nd) if va is not None and vb is not None else "n/a"
    except (TypeError, ValueError):
        delta = "n/a"
    return f"| {label} | {_v(va, nd)} | {_v(vb, nd)} | {delta} |"


def render_backtest(res: dict[str, Any]) -> str:
    m, w, cl, bm = res["manifest"], res["windows"], res["classification"], res["v11_benchmark"]
    tr, va = w["train"]["stats"], w["validation"]["stats"]
    out = [
        "# BTC V11.1 — COST-AWARE HIGH-CONVICTION INTRADAY: pre-registered historical experiment\n"
    ]
    out.append(
        f"Generated {m['generated_utc']} · V11.1 {m['v11_1_version']} · config `{m['config_hash'][:12]}` (strategy `{m['strategy_hash'][:12]}`, V11 base `{m['base_v11_strategy_hash'][:12]}`) · code `{m['code_version']}` · Bybit 1m dataset `{m['dataset_hash_1m'][:12]}` · windows TRAIN {m['windows']['train'][0]} -> {m['windows']['train'][1]}, VALIDATION {m['windows']['validation'][0]} -> {m['windows']['validation'][1]} · locked holdout: {m['locked_holdout']}.\n"
    )
    out.append(
        "ONE frozen specification (docs/BTC_V11_1_COST_AWARE_INTRADAY_DESIGN.md): V11 information groups and formulas unchanged; CHANGE #1 entry quality (>= "
        f"{m['quality']['min_evidence_groups']} independent directional groups with sub-score >= {m['quality']['evidence_min_sub']} and margin >= {m['quality']['evidence_min_margin']}, no trend contradiction, tightened A/B/C requirements, failed-breakout rejection); CHANGE #2 stop bounds {m['stop']['floor_pct']}% floor / {m['stop']['cap_pct']}% cap with the cost filter (expected {m['cost_filter']['expected_cost_bps']} bps round trip, reject above {m['cost_filter']['max_cost_r']}R). Exits, risk (7.5 USDT), leverage ladder and taker costs identical to V11. NET is the headline. Nothing was tuned after the run.\n"
    )
    out.append(f"## 0. Classification: **{cl['classification']}** — {cl['reason']}\n")
    out.append(
        f"Validation primary (net > 0 and PF > 1.05): {cl['validation_primary']} · train primary: {cl['train_primary']} · secondary: {cl['secondary']} · family share of validation net R: { {k: _v(v, 2) for k, v in cl['family_share_of_validation_net'].items()} } · shadow justified: {cl['shadow_justified']}.\n"
    )
    out.append("## 1. Headline (NET after primary costs)\n")
    out.append(HEAD)
    out.append(_row("TRAIN", tr))
    out.append(_row("VALIDATION", va))
    out.append(_row("TRAIN x1.5 costs", w["train"]["stress"]))
    out.append(_row("VALIDATION x1.5 costs", w["validation"]["stress"]))
    out.append("")
    out.append("## 2. V11 vs V11.1 (same windows, same data, same exits and costs)\n")
    for win in ("validation", "train"):
        b, s = bm.get(win, {}), w[win]["stats"]
        out.append(f"**{win.upper()}**\n")
        out.append("| metric | V11 | V11.1 | change |")
        out.append("|---|---|---|---|")
        for label, key, nd in (
            ("trades / day", "trades_per_day", 2),
            ("total trades", "n", 0),
            ("gross R / trade", "gross_r", 3),
            ("cost R / trade", "cost_r", 3),
            ("net R / trade", "net_r", 3),
            ("profit factor", "profit_factor", 2),
            ("win rate", "win_rate", 3),
            ("median hold min", "median_hold_min", 0),
            ("median stop %", "median_stop_pct", 2),
            ("cost USDT / trade", "cost_usd_per_trade", 3),
            ("max DD % of 5000", "max_dd_pct", 2),
            ("total net R", "total_net_r", 1),
        ):
            out.append(_cmp_row(label, b, s, key, nd))
        out.append("")
    out.append("## 3. Required metrics\n")
    out.append("| metric | TRAIN | VALIDATION |")
    out.append("|---|---|---|")
    for label, key, nd, pct in REQUIRED:
        out.append(f"| {label} | {_v(tr.get(key), nd, pct)} | {_v(va.get(key), nd, pct)} |")
    for label, key in (
        ("exits (STOP / TP-ladder BE / TRAIL runner / STALL / TIME)", "exits"),
        ("family A / B / C", "by_family_n"),
        ("funnel counters", "counters"),
        (
            "decision blockers (COST_TOO_HIGH, STOP_TOO_WIDE_V11_1, one-position, sizing)",
            "blockers",
        ),
        (
            "quality rejections at trigger (evidence, contradiction, family rules, failed breakout)",
            "quality_rejections",
        ),
        ("evidence group frequency among executed", "evidence_group_frequency"),
        ("family funnel (armed/triggers/executed)", "by_family_funnel"),
    ):
        out.append(f"| {label} | {tr.get(key)} | {va.get(key)} |")
    sd_t, sd_v = w["train"]["score_dist"], w["validation"]["score_dist"]
    out.append(
        f"| share of minutes with score >= entry / with evidence too | {_v(sd_t['share_ge_entry'], 3)} / {_v(sd_t['share_ge_entry_and_evidence'], 3)} | {_v(sd_v['share_ge_entry'], 3)} / {_v(sd_v['share_ge_entry_and_evidence'], 3)} |"
    )
    out.append("")
    for win in ("train", "validation"):
        out.append(f"## 4. Breakdowns — {win.upper()}\n")
        bd = w[win]["breakdown"]
        out += _sub_table("Setup family", bd["family"])
        out += _sub_table("Side", bd["side"])
        out += _sub_table("Family x side", bd["family_side"])
        out += _sub_table("Trend regime at decision", bd["trend_regime"])
        out += _sub_table("Volatility regime", bd["vol_regime"])
        out += _sub_table("Hour bucket (UTC)", bd["hour_bucket"])
        out += _sub_table("Exit reason", bd["exit_reason"])
    out.append("## 5. The six questions\n")
    bv, bt = bm.get("validation", {}), bm.get("train", {})

    def f(d: dict[str, Any], k: str) -> float:
        v = d.get(k)
        return float(v) if v is not None else float("nan")

    cost_drop_v = f(bv, "cost_r") - f(va, "cost_r")
    gross_gain_v = f(va, "gross_r") - f(bv, "gross_r")
    net_gain_v = f(va, "net_r") - f(bv, "net_r")
    out.append(
        f"1. **Did wider stops materially lower cost/R?** VALIDATION cost per trade {_v(f(bv, 'cost_r'))}R (V11, median stop {_v(f(bv, 'median_stop_pct'), 2)}%) -> {_v(f(va, 'cost_r'))}R (V11.1, median stop {_v(va.get('median_stop_pct'), 2)}%), a change of {_v(-cost_drop_v)}R; TRAIN {_v(f(bt, 'cost_r'))}R -> {_v(f(tr, 'cost_r'))}R. In USDT: {_v(f(bv, 'cost_usd_per_trade'))} -> {_v(va.get('cost_usd_per_trade'))} per trade."
    )
    out.append(
        f"2. **Did stricter entry quality improve gross R?** VALIDATION gross {_v(f(bv, 'gross_r'))}R -> {_v(f(va, 'gross_r'))}R (change {_v(gross_gain_v)}R); TRAIN {_v(f(bt, 'gross_r'))}R -> {_v(f(tr, 'gross_r'))}R. Mean confirming groups among executed trades: {_v(va.get('evidence_n_mean_executed'), 2)} (validation)."
    )
    out.append(
        f"3. **Did both improve net expectancy?** VALIDATION net {_v(f(bv, 'net_r'))}R -> {_v(f(va, 'net_r'))}R (change {_v(net_gain_v)}R), PF {_v(f(bv, 'profit_factor'), 2)} -> {_v(va.get('profit_factor'), 2)}; TRAIN net {_v(f(bt, 'net_r'))}R -> {_v(f(tr, 'net_r'))}R. Net positive on validation: {f(va, 'net_r') > 0}."
    )
    out.append(
        f"4. **How much frequency was sacrificed?** VALIDATION {_v(f(bv, 'trades_per_day'), 2)}/day -> {_v(va.get('trades_per_day'), 2)}/day ({_v(100.0 * (1 - f(va, 'trades_per_day') / f(bv, 'trades_per_day')), 0)}% fewer trades); TRAIN {_v(f(bt, 'trades_per_day'), 2)} -> {_v(tr.get('trades_per_day'), 2)}. Quality rejections at trigger: {va.get('quality_rejections')}; decision blockers: {va.get('blockers')}."
    )
    fam_v = w["validation"]["breakdown"]["family"]
    fam_t = w["train"]["breakdown"]["family"]
    out.append(
        "5. **Which family improved most?** VALIDATION net R by family (V11 -> V11.1): "
        + "; ".join(
            f"{k}: {_v(d.get('net_r'))}R on {d.get('n', 0)} trades" for k, d in fam_v.items()
        )
        + " (V11: A -0.378R / B -0.451R / C -0.570R). TRAIN: "
        + "; ".join(f"{k}: {_v(d.get('net_r'))}R on {d.get('n', 0)}" for k, d in fam_t.items())
        + " (V11: A -0.378R / B -0.387R / C -0.370R)."
    )
    robust = (
        f(va, "net_r") > 0
        and f(tr, "net_r") > 0
        and f(va, "profit_factor") > 1
        and f(tr, "profit_factor") > 1
    )
    out.append(
        f"6. **Is any apparent improvement robust across TRAIN and VALIDATION?** Net positive on both windows: {robust}. Cost/R change TRAIN {_v(f(tr, 'cost_r') - f(bt, 'cost_r'))}R vs VALIDATION {_v(-cost_drop_v)}R; gross change TRAIN {_v(f(tr, 'gross_r') - f(bt, 'gross_r'))}R vs VALIDATION {_v(gross_gain_v)}R; x1.5 costs: TRAIN net {_v(w['train']['stress'].get('net_r'))}R, VALIDATION net {_v(w['validation']['stress'].get('net_r'))}R."
    )
    out.append("")
    out.append(
        "Classification C means STOP: no V11.1 shadow runner, no V11.2, no tuning. Any further step needs a new owner pre-registration."
        if cl["classification"] == "C"
        else "Classification A/B with positive validation expectancy: a V11.1 forward SHADOW (separate namespace, never Demo) is justified under the owner's rule."
    )
    return "\n".join(out) + "\n"
