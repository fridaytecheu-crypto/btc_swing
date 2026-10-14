"""Render reports/BTC_V11_2_COUNTERFACTUAL_GATING_STUDY.md from study.json (pure)."""

from __future__ import annotations

from typing import Any

from btc_swing.v11.report import _v

COLS = (
    ("trades/day", "trades_per_day", 2),
    ("trades", "n", 0),
    ("gross R", "gross_r", 3),
    ("cost R", "cost_r", 3),
    ("net R", "net_r", 3),
    ("PF", "profit_factor", 2),
    ("win", "win_rate", 3),
    ("max DD %", "max_dd_pct", 1),
    ("med hold", "median_hold_min", 0),
)


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def _row(name: str, st: dict[str, Any], base: dict[str, Any]) -> str:
    cells = [_v(st.get(k), nd) for _, k, nd in COLS]
    d_tpd = (_f(st.get("trades_per_day")) or 0) - (_f(base.get("trades_per_day")) or 0)
    d_net = (_f(st.get("net_r")) or 0) - (_f(base.get("net_r")) or 0)
    d_gross = (_f(st.get("gross_r")) or 0) - (_f(base.get("gross_r")) or 0)
    fam = st.get("by_family_n") or {}
    side = f"{st.get('n_long', 0)}/{st.get('n_short', 0)}"
    return (
        f"| {name} | "
        + " | ".join(cells)
        + f" | {fam.get('A', 0)}/{fam.get('B', 0)}/{fam.get('C', 0)} | {side} | "
        f"{d_tpd:+.2f} | {d_gross:+.3f} | {d_net:+.3f} |"
    )


HEAD = (
    "| variant | "
    + " | ".join(c for c, _, _ in COLS)
    + " | A/B/C | L/S | Δ trades/day | Δ gross R | Δ net R |\n"
    "|---|" + "---|" * (len(COLS) + 5)
)


def _fam_table(res: dict[str, Any], names: list[str], win: str) -> list[str]:
    out = [
        f"**{win.upper()} — net R / trade by family (n)**",
        "",
        "| variant | A | B | C | LONG | SHORT |",
        "|---|---|---|---|---|---|",
    ]
    for n in names:
        bd = res["results"][n][win]["breakdown"]
        fam, side = bd["family"], bd["side"]

        def cell(d: dict[str, Any]) -> str:
            return f"{_v(d.get('net_r'))} ({d.get('n', 0)})" if d.get("n") else "- (0)"

        out.append(
            f"| {n} | {cell(fam['A'])} | {cell(fam['B'])} | {cell(fam['C'])} | {cell(side['LONG'])} | {cell(side['SHORT'])} |"
        )
    out.append("")
    return out


def selection(res: dict[str, Any]) -> list[dict[str, Any]]:
    """Owner's selection rule, evaluated mechanically on both windows."""
    rows = []
    for v in res["variants"]:
        n = v["name"]
        tr, va = res["results"][n]["train"]["stats"], res["results"][n]["validation"]["stats"]

        def ok(st: dict[str, Any]) -> dict[str, bool]:
            tpd, g, net, pf = (
                _f(st.get(k)) for k in ("trades_per_day", "gross_r", "net_r", "profit_factor")
            )
            return {
                "3-8/day": tpd is not None and 3.0 <= tpd <= 8.0,
                "gross>0": g is not None and g > 0,
                "net>0": net is not None and net > 0,
                "PF>1": pf is not None and pf > 1,
            }

        rows.append({"variant": n, "train": ok(tr), "validation": ok(va)})
    return rows


def render_study(res: dict[str, Any]) -> str:
    m = res["manifest"]
    names = [v["name"] for v in res["variants"]]
    base = res["results"]["V11_BASE"]
    out = ["# BTC V11.2 — counterfactual family-gating study (RESEARCH ONLY)\n"]
    out.append(
        f"Generated {m['generated_utc']} · V11 strategy `{m['v11_strategy_hash'][:12]}` · code `{m['code_version']}` · "
        f"Bybit 1m dataset `{m['dataset_hash_1m'][:12]}` · TRAIN {m['windows']['train']['start']} -> {m['windows']['train']['end_exclusive']}, "
        f"VALIDATION {m['windows']['validation']['start']} -> {m['windows']['validation']['end_exclusive']} · locked holdout: {m['locked_holdout']}.\n"
    )
    out.append(
        "Each variant changes ONE family gate of the frozen V11 engine; features, score model, thresholds, risk, exits and "
        "primary costs are identical. V11_BASE is the current frozen V11 engine (commit 98445d6+): it matches the committed V11 backtest trade for trade on entries and gross R, while its net is ~0.008R lower because that report ran before the post-TP1 stop-leg label fix (those exits were charged 1 bp exit slippage instead of the 3 bp stop slippage). **Selection bias:** this is a "
        "multi-variant search on consumed data, so the best variant's numbers are optimistic; nothing here is frozen, "
        "and no shadow runner exists for V11.2.\n"
    )
    groups = [
        ("A / B / C independent variants", [n for n in names if "+" not in n]),
        ("Combinations", [n for n in names if "+" in n]),
    ]
    for win in ("train", "validation"):
        out.append(f"## {win.upper()}\n")
        for title, ns in groups:
            if not ns:
                continue
            out.append(f"**{title}**\n")
            out.append(HEAD)
            for n in ns:
                out.append(_row(n, res["results"][n][win]["stats"], base[win]["stats"]))
            out.append("")
        out += _fam_table(res, names, win)
    out.append("## Family C structural conflict (score model vs RSI exhaustion)\n")
    for win in ("train", "validation"):
        cc = res["c_conflict"][win]
        out.append(f"**{win.upper()}**\n")
        out.append(
            "| side | C-structure minutes | share of minutes | score p50 / p90 at C structure | share ≥ armed 70 | share ≥ entry 75 | "
            "momentum sub-score at C / all minutes | RSI7 exhausted when score ≥ 65 |"
        )
        out.append("|---|---|---|---|---|---|---|---|")
        for side in ("LONG", "SHORT"):
            d = cc[side]
            p = d["score_at_c_structure_p50_p90"]
            out.append(
                f"| {side} | {d['c_structure_minutes']} | {_v(d['c_structure_share'], 4)} | {_v(p[0], 1)} / {_v(p[1], 1)} | "
                f"{_v(d['share_c_structure_with_score_ge_armed'], 4)} | {_v(d['share_c_structure_with_score_ge_entry'], 4)} | "
                f"{_v(d['momentum_subscore_at_c_structure_mean'], 3)} / {_v(d['momentum_subscore_all_minutes_mean'], 3)} | "
                f"{_v(d['rsi7_exhausted_share_when_score_ge_candidate'], 4)} |"
            )
        out.append("")
    out.append("## Owner selection rule (mechanical)\n")
    out.append(
        "| variant | TRAIN 3-8/day | TRAIN gross>0 | TRAIN net>0 | TRAIN PF>1 | VAL 3-8/day | VAL gross>0 | VAL net>0 | VAL PF>1 |"
    )
    out.append("|---|---|---|---|---|---|---|---|---|")
    for r in selection(res):
        t, v = r["train"], r["validation"]
        out.append(
            f"| {r['variant']} | "
            + " | ".join("yes" if t[k] else "no" for k in t)
            + " | "
            + " | ".join("yes" if v[k] else "no" for k in v)
            + " |"
        )
    out.append("")
    return "\n".join(out) + "\n"
