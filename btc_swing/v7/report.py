"""Markdown rendering of the V7 edge-discovery results (the ten questions of design section 6)."""

from __future__ import annotations

import math
from typing import Any

from btc_swing.v7.config import V7Config
from btc_swing.v7.discovery import V7Discovery


def _n(x: Any, d: int = 3) -> str:
    if x is None:
        return "-"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        return "-" if math.isnan(x) or math.isinf(x) else f"{x:.{d}f}"
    return str(x)


def _ci(ci: Any, d: int = 3) -> str:
    if not ci or ci[0] is None or (isinstance(ci[0], float) and math.isnan(ci[0])):
        return "-"
    return f"[{ci[0]:.{d}f}, {ci[1]:.{d}f}]"


def _pct(x: Any) -> str:
    return (
        "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{100 * float(x):.1f}%"
    )


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join(v if isinstance(v, str) else _n(v) for v in r) + " |")
    return "\n".join(out)


def _pv(param: str, value: Any) -> str:
    return f"{param}{value}" if param.endswith(("=", ">", "<")) else f"{param}: {value}"


def _label(d: dict[str, Any]) -> str:
    return f"{d['name']} ({_pv(d['param'], d['value'])})"


def _horizon_table(r: dict[str, Any]) -> str:
    h = r.get("all", {}).get("horizons", {})
    rows = []
    for k in sorted(h, key=int):
        x = h[k]
        rows.append(
            [
                f"{k} min",
                x["n_event"],
                _n(x["event_ret_pct"], 3) + "% / " + _n(x["control_ret_pct"], 3) + "%",
                _n(x["event_ret_atr"], 3) + " / " + _n(x["control_ret_atr"], 3),
                _n(x["diff_atr"], 3),
                _ci(x["diff_ci"]),
                _n(x["effect_size"], 2),
                _n(x["event_mfe_atr"], 2) + " / " + _n(x["control_mfe_atr"], 2),
                _n(x["event_mae_atr"], 2) + " / " + _n(x["control_mae_atr"], 2),
                _pct(x["event_hit_rate"]) + " / " + _pct(x["control_hit_rate"]),
            ]
        )
    return _table(
        [
            "horizon",
            "n events",
            "ret % ev / ctl",
            "ret ATR ev / ctl",
            "Δ ATR",
            "95% CI",
            "effect",
            "MFE ATR ev / ctl",
            "MAE ATR ev / ctl",
            "P(ret>0) ev / ctl",
        ],
        rows,
    )


def _before_table(r: dict[str, Any]) -> str:
    b = r.get("all", {}).get("before", {})
    rows = [
        [
            k,
            _pct(v["event_p"]),
            _pct(v["control_p"]),
            _n(100 * v["diff"], 1) + " pp"
            if v["diff"] is not None and not math.isnan(v["diff"])
            else "-",
            _ci(
                [
                    100 * c if c is not None and not math.isnan(c) else math.nan
                    for c in v["diff_ci"]
                ],
                1,
            ),
            v["event_resolved"],
        ]
        for k, v in b.items()
    ]
    return _table(
        [
            "+X before -Y (within 180 min)",
            "events",
            "controls",
            "Δ",
            "95% CI (pp)",
            "resolved events",
        ],
        rows,
    )


def render_discovery(res: V7Discovery, cfg: V7Config) -> str:
    m, s = res.manifest, res.summary
    rows = s["rows"]
    out: list[str] = []
    out.append("# BTC V7 MICROSTRUCTURE SPECIALIST — Phase 1: edge discovery report\n")
    out.append(
        f"**Repeatable effects on DISCOVERY (declared standard: Δ60 >= {cfg.discovery.decision.min_effect_atr_60} ATR5m vs matched controls, CI > 0, same sign in >= {cfg.discovery.decision.min_consistent_half_years} of 5 half-years): {s['n_repeatable_discovery']} of {len(rows)} definitions**"
        + (f" — {', '.join(s['repeatable'])}" if s["repeatable"] else "")
        + f". **Surviving 2025 (recent validation, anchors only): {len(s['survive_2025'])}**"
        + (f" — {', '.join(s['survive_2025'])}" if s["survive_2025"] else "")
        + ".\n"
    )
    out.append(
        f"Config `{cfg.strategy_name}` hash `{m['config_hash'][:16]}…`, code `{m['code_version']}`, Bybit 1m dataset hash `{m['data_hash_bybit_1m'][:16]}…`, research hash `{m['research_hash'][:16]}…`, run {m['ran_at']}. "
        f"Frame: {m['frame_meta']['rows']} 1m rows, {m['frame_meta']['valid_rows']} valid. Windows: discovery {m['windows']['discovery']['start']} -> {m['windows']['discovery']['end_exclusive']}; validation 1 {m['windows']['validation_1']['start']} -> {m['windows']['validation_1']['end_exclusive']}; recent {m['windows']['recent_validation']['start']} -> {m['windows']['recent_validation']['end_exclusive']}; locked holdout not read. "
        "Paper research only; nothing traded, nothing activated; V5/V5.1/V6/V6.1 untouched.\n"
    )
    out.append(
        "Method: events are first-in-cluster 1m rows (15-minute cluster) where the state holds; outcomes are signed by the hypothesised direction; "
        f"every event is matched with {cfg.discovery.controls_per_event} control minutes of the same hour, recent-volatility tercile, 15m-return tercile, 1h regime and 4h-range position tercile, at least {cfg.discovery.control_exclusion_min} min away from any event of the same definition. "
        "Δ = event minus control mean forward return in ATR5m units (bootstrap 95% CI); 'before' = probability of +X before -Y within 180 min.\n"
    )
    # overview table
    out.append("## 1-2. Which features predict displacement, and how much (discovery window)\n")
    out.append(
        _table(
            [
                "definition",
                "anchor",
                "events/day",
                "first-in-cluster/day",
                "n",
                "Δ60 ATR",
                "95% CI (day-cluster bootstrap)",
                "Δ60 bps",
                "event ret 60 bps",
                "effect",
                "best horizon",
                "Δ best",
                "P(+0.5% before -0.5%) ev / ctl",
                "half-years +",
                "repeatable",
            ],
            [
                [
                    f"{r['name']} {_pv(r['param'], r['value'])}",
                    _n(r["anchor"]),
                    _n(r["events_per_day"], 1),
                    _n(r["fic_per_day"], 2),
                    r["n_fic"],
                    _n(r["diff_atr_60"], 3),
                    _ci(r["ci_60"]),
                    _n(r.get("diff_bps_60"), 1),
                    _n(r.get("event_ret_bps_60"), 1),
                    _n(r["effect_60"], 2),
                    f"{r['best_horizon']} min" if r["best_horizon"] else "-",
                    _n(r["diff_best"], 3),
                    _pct(r["p50_before_50_event"]) + " / " + _pct(r["p50_before_50_control"]),
                    f"{r['half_years_pos']}/{r['half_years_n']}" if r["half_years_n"] else "-",
                    _n(r["repeatable_discovery"]),
                ]
                for r in rows
            ],
        )
    )
    out.append(
        "\nScale: Δ60 in ATR5m units and in basis points of price; the Bybit round trip costs about 15 bps (taker 5.5 x 2 + slippage), so every effect must be read against that before any engine is considered.\n"
    )
    out.append(
        "\nScale: Δ60 in ATR5m units and in basis points of price; the Bybit round trip costs about 15 bps "
        "(taker 5.5 x 2 + slippage), so every effect must be read against that before any engine is considered.\n"
    )
    # long/short
    out.append("\n## 3. Long versus short (Δ60 ATR, discovery)\n")
    out.append(
        _table(
            ["definition", "long Δ60", "short Δ60"],
            [
                [
                    f"{r['name']} {_pv(r['param'], r['value'])}",
                    _n(r["long_diff_60"], 3),
                    _n(r["short_diff_60"], 3),
                ]
                for r in rows
            ],
        )
    )
    # per-definition detail for anchors
    out.append("\n## 4. Horizon profile and +X-before-Y (anchor definitions, discovery)\n")
    for r in res.discovery:
        if not r["definition"]["anchor"] or "all" not in r:
            continue
        out.append(
            f"\n### {_label(r['definition'])} — direction: {r['definition']['direction_rule']}; {r['n_event_rows']} event rows, {r['n_first_in_cluster']} first-in-cluster ({_n(r['first_in_cluster_per_day'], 2)}/day), {r.get('n_controls', 0)} controls\n"
        )
        out.append(_horizon_table(r))
        out.append("\n" + _before_table(r))
        reg = r.get("by_regime", {})
        hb = r.get("by_hour_block", {})
        hy = r.get("half_years", [])
        out.append(
            "\n"
            + _table(
                ["split", "n", "Δ60 ATR", "P(+0.5 before -0.5) ev / ctl"],
                [
                    [
                        k,
                        v.get("n"),
                        _n(v.get("diff_atr_60"), 3),
                        _pct(v.get("event_p50_before_50"))
                        + " / "
                        + _pct(v.get("control_p50_before_50")),
                    ]
                    for k, v in list(reg.items()) + list(hb.items())
                ]
                + [[q["label"], q.get("n"), _n(q.get("diff_atr_60"), 3), "-"] for q in hy],
            )
        )
    # survival
    out.append("\n## 5. Survival on validation 1 (2024 H2) and recent 2025 data (anchors only)\n")
    out.append(
        _table(
            [
                "definition",
                "disc Δ60",
                "disc CI",
                "val1 Δ60",
                "val1 CI",
                "val1 fic/day",
                "2025 Δ60",
                "2025 CI",
                "2025 fic/day",
                "survives 2025",
            ],
            [
                [
                    f"{r['name']} {_pv(r['param'], r['value'])}",
                    _n(r["diff_atr_60"], 3),
                    _ci(r["ci_60"]),
                    _n(r["v1_diff_atr_60"], 3),
                    _ci(r["v1_ci_60"]),
                    _n(r["v1_fic_per_day"], 2),
                    _n(r["recent_diff_atr_60"], 3),
                    _ci(r["recent_ci_60"]),
                    _n(r["recent_fic_per_day"], 2),
                    _n(r["survives_2025"]),
                ]
                for r in rows
                if r["anchor"]
            ],
        )
    )

    # answers 6-10
    def find(name: str, value: Any = None) -> dict[str, Any] | None:
        for r in rows:
            if r["name"] == name and (value is None or str(r["value"]) == str(value)):
                return r
        return None

    ab, eff = find("ABSORPTION"), find("EFFICIENT_FLOW")
    ib = find("INTENSITY_BURST", cfg.discovery.anchors.intensity_burst_tc_z.anchor)
    imb = find("IMBALANCE", cfg.discovery.anchors.imbalance_abs.anchor)
    lt = find(
        f"LARGE_TRADE_IMBALANCE_{cfg.features.large_trade_anchor}",
        cfg.discovery.anchors.large_trade_imbalance_abs.anchor,
    )
    e1, e1lt, e2, e2x = (
        find("E1_TREND_PULLBACK_RELEASE"),
        find("E1_TREND_PULLBACK_RELEASE+LT"),
        find("E2_FAILED_AUCTION_ABSORPTION"),
        find("E2_SWEEP_WITH_EFFICIENT_FLOW"),
    )
    e3 = find("E3_COMPRESSION_EXPANSION", cfg.discovery.anchors.anti_chase_atr.anchor)

    def line(r: dict[str, Any] | None) -> str:
        if r is None:
            return "not evaluated"
        return f"Δ60 {_n(r['diff_atr_60'], 3)} ATR {_ci(r['ci_60'])}, effect {_n(r['effect_60'], 2)}, {_n(r['fic_per_day'], 2)} first-in-cluster/day, half-years + {r['half_years_pos']}/{r['half_years_n']}, 2025 Δ60 {_n(r['recent_diff_atr_60'], 3)} {_ci(r['recent_ci_60'])}"

    out.append("\n## 6-10. Answers\n")
    out.append(
        f"- **6. Is absorption detectable?** ABSORPTION (strong flow, weak response, traded AGAINST the flow): {line(ab)}. Contrast EFFICIENT_FLOW (strong flow, strong response, WITH the flow): {line(eff)}. Absorption is 'detectable' only if the reversal Δ is positive with a CI above zero and the efficient-flow continuation Δ is also positive: "
        + (
            "YES"
            if ab and eff and ab["repeatable_discovery"] and eff["repeatable_discovery"]
            else "PARTIAL"
            if (ab and ab["repeatable_discovery"]) or (eff and eff["repeatable_discovery"])
            else "NO"
        )
        + "."
    )
    out.append(
        f"- **7. Do trade-intensity bursts have continuation edge?** INTENSITY_BURST at the anchor: {line(ib)}; neighbourhood rows in section 1. Verdict: "
        + ("YES" if ib and ib["repeatable_discovery"] else "NO")
        + "."
    )
    out.append(
        f"- **8. Do large-trade clusters add information beyond plain imbalance?** plain IMBALANCE anchor: {line(imb)}; LARGE_TRADE_IMBALANCE anchor: {line(lt)}. Incremental: "
        + (
            "YES"
            if lt
            and imb
            and not math.isnan(lt["diff_atr_60"])
            and not math.isnan(imb["diff_atr_60"])
            and lt["diff_atr_60"] > imb["diff_atr_60"] + 0.02
            and lt["ci_60"][0] > 0
            else "NO"
        )
        + " (threshold: large-trade Δ60 exceeds plain-imbalance Δ60 by 0.02 ATR with CI > 0)."
    )
    out.append(
        f"- **9. Do combinations improve materially over single features?** E1 TREND_PULLBACK_RELEASE: {line(e1)}; E1 + large trades: {line(e1lt)}; E2 FAILED_AUCTION_ABSORPTION: {line(e2)}; E2 contrast (sweep with efficient flow, traded against): {line(e2x)}; E3 COMPRESSION_EXPANSION (anchor anti-chase): {line(e3)}. Material improvement = a combination Δ60 at least 0.05 ATR above the best single feature with CI > 0: "
        + (
            "YES"
            if any(
                r
                and not math.isnan(r["diff_atr_60"])
                and r["ci_60"][0] > 0
                and r["diff_atr_60"]
                >= max(
                    [
                        x["diff_atr_60"]
                        for x in rows
                        if x["name"]
                        in (
                            "INTENSITY_BURST",
                            "IMBALANCE",
                            "ABSORPTION",
                            "EFFICIENT_FLOW",
                            "DELTA_ACCEL",
                            "COMPRESSION_BREAK",
                            "FAILED_BREAK",
                        )
                        or x["name"].startswith("LARGE_TRADE")
                    ]
                    + [-9]
                )
                + 0.05
                for r in (e1, e1lt, e2, e3)
            )
            else "NO"
        )
        + "."
    )
    out.append(
        "- **10. Natural event frequency per day (discovery; all rows / first-in-cluster):**"
    )
    out.append(
        _table(
            ["definition", "events/day", "first-in-cluster/day", "share long"],
            [
                [
                    f"{r['name']} {_pv(r['param'], r['value'])}",
                    _n(r["events_per_day"], 1),
                    _n(r["fic_per_day"], 2),
                    _pct(r.get("share_long")),
                ]
                for r in rows
            ],
        )
    )
    out.append("\n## Reading\n")
    rep = [r for r in rows if r["repeatable_discovery"]]
    surv = [r for r in rows if r["survives_2025"]]
    if not rep:
        out.append(
            "No single feature or declared combination meets the pre-registered standard for a repeatable effect on the discovery window: the microstructure states studied do not distinguish meaningful future displacement from matched non-events once hour, volatility, recent return, regime and structure position are held fixed. Phase 2 engines built on these states would inherit this. The report stops here as the design requires."
        )
    else:
        out.append(
            f"{len(rep)} definitions meet the discovery standard ({', '.join(r['name'] + ' ' + _pv(r['param'], r['value']) for r in rep)}); {len(surv)} of the anchors also survive 2025 ({', '.join(r['name'] + ' ' + _pv(r['param'], r['value']) for r in surv) or 'none'}). Only these are candidates for the Phase 2 engines; effect sizes above are in ATR5m per trade before any cost — a Phase 2 engine must convert them into stops of at least ~75 bps to keep the 15 bps round trip under 20% of risk, which constrains the achievable frequency (see the first-in-cluster rates)."
        )
    return "\n".join(out) + "\n"
