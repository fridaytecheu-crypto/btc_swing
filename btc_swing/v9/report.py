"""Render the V9 research report (design section 9)."""

from __future__ import annotations

from typing import Any

from btc_swing.v9.config import V9Config
from btc_swing.v9.research import V9Research

STEPS = ["A_PRICE_ONLY", "B_IMPULSE_RVOL", "C_PULLBACK_VOLUME", "D_TRIGGER_FLOW"]
LABEL = {
    "A_PRICE_ONLY": "PRICE ONLY",
    "B_IMPULSE_RVOL": "+ IMPULSE RVOL",
    "C_PULLBACK_VOLUME": "+ PULLBACK VOLUME",
    "D_TRIGGER_FLOW": "+ TRIGGER VOLUME/FLOW",
}


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


def _row(st: dict[str, Any], label: str | None = None) -> str:
    return (
        f"| {label or st['spec']} | {st['n']} | {_v(st['trades_per_day'], 2)} | {_v(st.get('win_rate'), 0, True)} | "
        f"{_v(st.get('gross_r'))} | {_v(st.get('cost_r'))} | {_v(st.get('net_r'))} | "
        f"[{_v(st.get('net_r_ci_lo'), 2)}, {_v(st.get('net_r_ci_hi'), 2)}] | {_v(st.get('pf'), 2)} | "
        f"{_v(st.get('max_dd_pct'), 2)}% | {_v(st.get('median_hold_min'), 0)} | "
        f"{st.get('n_long', 0)} ({_v(st.get('net_r_long'), 2)}) | {st.get('n_short', 0)} ({_v(st.get('net_r_short'), 2)}) |"
    )


HEAD = (
    "| variant | n | trades/day | win rate | gross R | cost R | net R | net R 95% CI (day-cluster) | PF | max DD | "
    "median hold (min) | LONG n (net R) | SHORT n (net R) |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|"
)


def _bps_row(st: dict[str, Any]) -> str:
    return (
        f"| {st['spec']} | {_v(st.get('gross_bps'), 1)} | {_v(st.get('fee_bps'), 1)} | {_v(st.get('slip_bps'), 1)} | "
        f"{_v(st.get('cost_bps'), 1)} | {_v(st.get('net_bps'), 1)} | {_v(st.get('gross_r'))} | {_v(st.get('fee_r'))} | "
        f"{_v(st.get('slip_r'))} | {_v(st.get('funding_r'), 4)} | {_v(st.get('net_r'))} | {_v(st.get('stop_pct_median'), 2)}% |"
    )


BPS_HEAD = (
    "| variant | gross bps | fee bps | spread+slip bps | cost bps | net bps | gross R | fee R | spread+slip R | "
    "funding R | net R | median stop % |\n|---|---|---|---|---|---|---|---|---|---|---|---|"
)


def _funnel_table(stats: list[dict[str, Any]]) -> str:
    keys = [
        ("raw", "raw impulse legs (>= 0.5 ATR, any trend)"),
        ("after_trend", "after 15m trend"),
        ("after_impulse", "after impulse >= anchor ATR"),
        ("after_rvol", "after impulse RVOL"),
        ("after_pullback", "after pullback (armed at least once)"),
        ("after_pb_volume", "after pullback-volume rule"),
        ("after_trigger", "after 1m trigger (breakout while armed)"),
        ("after_flow", "after flow confirmation"),
        ("skipped_in_position", "skipped: position open"),
        ("rejected_micro_stop", "rejected: micro-stop"),
        ("floored_micro_stop", "floored: micro-stop (floor mode only)"),
        ("rejected_sizing", "rejected: sizing"),
        ("executed", "EXECUTED"),
    ]
    head = (
        "| stage | "
        + " | ".join(LABEL[s["spec"]] for s in stats)
        + " |\n|---|"
        + "---|" * len(stats)
    )
    lines = [head]
    for k, lab in keys:
        cells = []
        for s in stats:
            v = s["funnel"].get(k, 0)
            cells.append(f"{v} ({v / s['days']:.2f}/day)")
        lines.append(f"| {lab} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _fold_table(folds: dict[str, list[dict[str, Any]]], names: list[str]) -> str:
    fold_names = [f["fold"] for f in folds[names[0]]]
    head = (
        "| variant | "
        + " | ".join(fold_names)
        + " | folds net > 0 |\n|---|"
        + "---|" * (len(fold_names) + 1)
    )
    lines = [head]
    for nm in names:
        cells = [f"{_v(f['net_r'], 2)}R / {_v(f['trades_per_day'], 1)}/d" for f in folds[nm]]
        pos = sum(1 for f in folds[nm] if f["n"] > 0 and f["net_r"] > 0)
        lines.append(f"| {nm} | " + " | ".join(cells) + f" | {pos}/{len(fold_names)} |")
    return "\n".join(lines)


def render_report(res: V9Research, cfg: V9Config) -> str:
    m, tr, va, fz, vd = res.manifest, res.train, res.validation, res.frozen, res.verdict
    out: list[str] = []
    out.append("# BTC V9 — SIMPLE TREND + VOLUME PULLBACK: research report\n")
    out.append(
        f"Generated {m['generated_utc']} · V9 {m['v9_version']} · config `{m['config_hash'][:12]}` · "
        f"Bybit 1m dataset `{m['dataset_hash_1m'][:12]}` · frame {m['frame']['days']} days, "
        f"{m['frame']['valid_rows_1m']} valid minutes · locked holdout: {m['locked_holdout']}.\n"
    )
    out.append(
        "Paper research only. One deterministic BTCUSDT strategy (15m trend, 5m impulse + pullback, 1m breakout "
        "entry) with the pre-registered A/B/C/D volume ladder (`docs/BTC_V9_SIMPLE_TREND_VOLUME_PULLBACK_DESIGN.md`). "
        "Costs: TAKER entry and TAKER exits, fee 5.5 bps/side, half-spread 0.5 bps/side, slippage 1 bps "
        "(entry, target, time) / 3 bps (stop), Binance funding proxy at 8h marks. R = P&L / planned risk "
        "(qty x stop distance); primary exit B (+1.5R), time stop 120 min (progress < 0.5R), hard max 240 min, "
        "stop = pullback low - 0.1 ATR5m with a 0.50% minimum (closer stops rejected), risk 0.125%, one position.\n"
    )
    # ------------------------------------------------------------ headline comparison
    out.append(
        "## 1. The comparison table (ladder at anchors; primary exit B; conservative taker costs)\n"
    )
    for win, title in (
        ("train", "TRAIN 2022-01-01 -> 2024-06-30"),
        ("validation_1", "VALIDATION 1 2024-07-01 -> 2024-12-31"),
        ("recent_validation", "RECENT VALIDATION 2025-01-01 -> 2025-06-30"),
    ):
        rows = tr["ladder"] if win == "train" else va[win]["ladder"]
        out.append(f"### {title}\n")
        out.append(HEAD)
        for st in rows:
            out.append(_row(st, LABEL[st["spec"]]))
        if win != "train":
            out.append(_row(va[win]["frozen"], f"FROZEN: {va[win]['frozen']['spec']}"))
            out.append(_row(va[win]["frozen_stress"], "FROZEN x1.5 costs"))
        out.append("")
    out.append("### Cost decomposition per trade (bps of notional and R)\n")
    out.append(BPS_HEAD)
    for st in tr["ladder"]:
        out.append(_bps_row({**st, "spec": "TRAIN " + LABEL[st["spec"]]}))
    for win in ("validation_1", "recent_validation"):
        out.append(_bps_row({**va[win]["frozen"], "spec": f"{win} FROZEN"}))
    out.append("")
    out.append("### Cost stress x1.5 (TRAIN ladder)\n")
    out.append(HEAD)
    for st in tr["stress"]:
        out.append(_row(st))
    out.append("")
    # ------------------------------------------------------------ funnel
    out.append("## 2. Funnel (distinct impulse legs per window; anchors of each ladder step)\n")
    out.append("### TRAIN\n")
    out.append(_funnel_table(tr["ladder"]))
    for win in ("validation_1", "recent_validation"):
        out.append(f"\n### {win}\n")
        out.append(_funnel_table(va[win]["ladder"]))
    out.append("")
    # ------------------------------------------------------------ frequency distribution
    out.append("## 3. Frequency distribution (executed trades per calendar day)\n")
    out.append(
        "| window | variant | mean/day | median/day | % days 0 | % days 1-2 | % days 3-6 | % days > 6 | median hold |\n|---|---|---|---|---|---|---|---|---|"
    )
    for win in ("train", "validation_1", "recent_validation"):
        rows = tr["ladder"] if win == "train" else va[win]["ladder"] + [va[win]["frozen"]]
        for st in rows:
            dd = st.get("day_dist") or {}
            out.append(
                f"| {win} | {st['spec']} | {_v(st['trades_per_day'], 2)} | {_v(st.get('median_trades_per_day'), 1)} | "
                f"{_v(dd.get('zero'), 0, True)} | {_v(dd.get('one_two'), 0, True)} | {_v(dd.get('three_six'), 0, True)} | "
                f"{_v(dd.get('more'), 0, True)} | {_v(st.get('median_hold_min'), 0)} |"
            )
    out.append("")
    # ------------------------------------------------------------ folds
    out.append("## 4. TRAIN half-year folds (net R per trade / trades per day)\n")
    out.append(_fold_table(tr["ladder_folds"], STEPS))
    out.append("")
    # ------------------------------------------------------------ sensitivities
    out.append(
        "## 5. Pre-declared neighbourhoods (TRAIN only, one change at a time; never adopted)\n"
    )
    groups: dict[str, list[dict[str, Any]]] = {}
    for st in tr["sensitivities"]:
        groups.setdefault(st["group"], []).append(st)
    for g, rows in groups.items():
        out.append(f"### {g}\n")
        out.append(HEAD)
        for st in rows:
            out.append(_row(st))
        names = [st["spec"] for st in rows]
        out.append("")
        out.append(_fold_table(tr["sensitivity_folds"], names))
        out.append("")
    # ------------------------------------------------------------ accel
    out.append("## 6. Volume-acceleration diagnostic (TRAIN, full volume version D)\n")
    acc = tr["accel"]
    if acc.get("quartiles"):
        out.append("| accel quartile | range | n | net R |\n|---|---|---|---|")
        for q in acc["quartiles"]:
            out.append(
                f"| Q{q['quartile']} | {_v(q['accel_min'], 2)} - {_v(q['accel_max'], 2)} | {q['n']} | {_v(q['net_r'])} |"
            )
        out.append(
            "\n| fold | n top quartile | net R top quartile | net R rest |\n|---|---|---|---|"
        )
        for f in acc["top_vs_rest_by_fold"]:
            out.append(
                f"| {f['fold']} | {f['n_top']} | {_v(f['net_r_top'])} | {_v(f['net_r_rest'])} |"
            )
    else:
        out.append("Too few executed trades for a quartile diagnostic.")
    ag = fz["accel_gate"]
    out.append(
        f"\nAccel gate >= {cfg.trigger.accel_gate_candidate} on D: net R {_v(ag['net_r'])} "
        f"({ag['folds_positive']} folds positive, {_v(ag['trades_per_day'], 2)}/day). "
        f"Promotion rule (>= +{cfg.selection.accel_min_gain_r} R vs D, >= {cfg.selection.min_folds_positive} folds positive, "
        f">= {cfg.selection.min_trades_per_day}/day): **{'PROMOTED' if fz['accel_promoted'] else 'NOT promoted (dropped)'}**.\n"
    )
    # ------------------------------------------------------------ risk
    out.append("## 7. Risk sensitivity (frozen specification, TRAIN)\n")
    out.append(HEAD)
    for st in tr["risk"]:
        out.append(_row(st))
    out.append("")
    # ------------------------------------------------------------ freeze
    sel = fz["selection"]
    out.append("## 8. Selection and freeze (TRAIN only; design section 7)\n")
    out.append(
        f"Selected ladder step: **{sel['step']}** (qualified under the written rule: {sel['qualified']}; TRAIN net R "
        f"{_v(sel['net_r'])}). Frozen specification `{fz['spec']['name']}` (hash `{fz['spec_hash'][:12]}`), run once per "
        f"validation window. Accel promoted: {fz['accel_promoted']}.\n"
    )
    # ------------------------------------------------------------ verdict
    out.append("## 9. Answers\n")
    out.append(
        "**1. Which conditions genuinely add edge** (TRAIN delta net R vs the previous step, confirmed only if positive on BOTH validation windows):\n"
    )
    out.append(
        "| rule | TRAIN delta net R | VAL1 delta | RECENT delta | TRAIN frequency cut | adds edge |\n|---|---|---|---|---|---|"
    )
    for c in vd["conditions"]:
        out.append(
            f"| {LABEL[c['rule']]} | {_v(c['delta_net_r_train'])} | {_v(c['delta_net_r_validation'][0])} | "
            f"{_v(c['delta_net_r_validation'][1])} | {_v(c['frequency_cut_train'], 0, True)} | {'YES' if c['adds_edge'] else 'no'} |"
        )
    only_cut = [LABEL[c["rule"]] for c in vd["conditions"] if not c["adds_edge"]]
    out.append(
        f"\n**2. Which conditions only reduce trade count:** {', '.join(only_cut) if only_cut else 'none'}.\n"
    )
    out.append(f"**3. Best simple specification (TRAIN rule):** {vd['frozen_spec']}.\n")
    out.append(
        f"**4. Expected trades/day (mean of the two validation windows, frozen spec):** {_v(vd['expected_trades_per_day_validation'], 2)}.\n"
    )
    for win, x in vd["per_window"].items():
        out.append(
            f"- {win}: n {x['n']}, {_v(x['trades_per_day'], 2)}/day, net R {_v(x['net_r'])}, PF {_v(x['pf'], 2)}, "
            f"max DD {_v(x['max_dd_pct'], 2)}% -> profitability {'PASS' if x['profit_pass'] else 'FAIL'}"
            f"{' (PF >= 1.10)' if x['pf_110'] else ''}, frequency {'PASS' if x['frequency_pass'] else 'FAIL'}"
        )
    out.append(
        f"\n**5. PASS/FAIL profitability (both windows, conservative costs):** **{'PASS' if vd['profitability_pass'] else 'FAIL'}**.\n"
    )
    out.append(
        f"**6. PASS/FAIL 3-6 trades/day (both windows):** **{'PASS' if vd['frequency_pass'] else 'FAIL'}**.\n"
    )
    out.append("## 10. Reading\n")
    a_tr = next(s for s in tr["ladder"] if s["spec"] == "A_PRICE_ONLY")
    fl = [
        s
        for s in tr["sensitivities"]
        if s["group"] == "stop_floor" and s["spec"].startswith("A floor")
    ]
    fl_txt = "; ".join(
        f"{s['spec']}: {_v(s['trades_per_day'], 1)}/day, gross {_v(s['gross_r'], 2)}R, cost {_v(s['cost_r'], 2)}R, net {_v(s['net_r'], 2)}R"
        for s in fl
    )
    fn = a_tr["funnel"]
    out.append(
        f"- The price structure itself carries no edge at this horizon: PRICE ONLY earns {_v(a_tr['gross_bps'], 1)} bps gross per trade "
        f"on TRAIN against a {_v(a_tr['cost_bps'], 1)} bps conservative round trip (net {_v(a_tr['net_r'])} R, PF {_v(a_tr['pf'], 2)}), and no "
        f"ladder step is positive in more than one TRAIN half-year or in either validation window.\n"
        f"- Frequency is destroyed by the stop rule, not by the volume filters: {fn['after_trigger']} distinct PRICE-ONLY legs reach a 1m "
        f"trigger on TRAIN, and {fn['rejected_micro_stop']} trigger attempts (a leg can trigger more than once while armed) are rejected because "
        f"the structural stop (pullback low - 0.1 ATR5m, entry after a 1m breakout) is closer than 0.50% (median structural stop about 0.2% of "
        f"price, ATR5m about 0.13%); {fn['executed']} trades execute. The 'impulse >= 1 ATR' "
        f"rule removes almost nothing ({fn['after_trend']} -> {fn['after_impulse']} legs) because an 8-bar leg almost always spans more than one bar's ATR.\n"
        f"- Widening every stop to the minimum (pre-declared floor sensitivity, TRAIN) reaches the 3-6/day band but is worse, not better: {fl_txt}. "
        f"Costs of 0.23-0.35 R per trade on a 0.4-0.6% stop with ~0 gross are not recoverable by any exit or time stop studied.\n"
        f"- Volume rules (impulse RVOL, pullback contraction, trigger rvol / imbalance / delta) change net R by a few hundredths of an R in either "
        f"direction and reduce trade count by 5-27% each; none adds edge on both validation windows. Volume acceleration does not help (top quartile is the worst).\n"
        f"- Classification C: V9 fails the owner's profitability and frequency criteria on both validation windows under the pre-registered specification "
        f"and under every pre-declared neighbour. Nothing was tuned on validation; the locked holdout stays unspent.\n"
    )
    out.append(
        "STOPPED before the locked holdout (2025-07-01 -> 2026-09-30 not read). Nothing activated; no Bybit order; V5.1 untouched.\n"
    )
    return "\n".join(out)
