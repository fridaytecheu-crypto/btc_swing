"""Render reports/BTC_SWING_V5_MICROSTRUCTURE_RESEARCH.md (36 sections + one classification)."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from btc_swing.research.phase21_report import _ms, _n, _p, _table
from btc_swing.research.phase24_report import STAT_HEADERS, _by_rows, _stat_row
from btc_swing.v3.report import ACCT_HEADERS, PERIOD_HEADERS, _acct_rows, _day_cell, _row
from btc_swing.v5.config import V5Family
from btc_swing.v5.research import V5Research

HZ_HEADERS = ["horizon", "n", "mean signed return", "median", "hit rate", "t", "95% bootstrap CI"]
FAMILY_TITLES = {
    V5Family.LIQUIDATION_CONTINUATION.value: "A. LIQUIDATION_CONTINUATION (historical OI-flush proxy)",
    V5Family.ABSORPTION_REVERSAL.value: "B. ABSORPTION_REVERSAL",
    V5Family.FLOW_OI_CONTINUATION.value: "C. FLOW_OI_CONTINUATION",
    V5Family.FLOW_DIVERGENCE_REVERSAL.value: "D. FLOW_DIVERGENCE_REVERSAL",
}


def _ci(d: dict[str, Any]) -> str:
    if (
        "ci_lo" not in d
        or d.get("ci_lo") is None
        or (isinstance(d.get("ci_lo"), float) and math.isnan(d["ci_lo"]))
    ):
        return ""
    return f"[{_p(d['ci_lo'], 3)}, {_p(d['ci_hi'], 3)}]"


def _hz_rows(rows: list[dict[str, Any]]) -> list[list[str]]:
    return [
        [
            f"{d['horizon_h']:g} h",
            str(d["n"]),
            _p(d["mean"], 3),
            _p(d["median"], 3),
            _p(d["hit_rate"], 1),
            _n(d["t"], 2),
            _ci(d),
        ]
        for d in rows
    ]


def _hz_or_empty(rows: list[dict[str, Any]] | None) -> list[str]:
    return _table(HZ_HEADERS, _hz_rows(rows)) if rows else ["- no events.", ""]


def _period_rows(d: dict[str, dict[str, Any]], hz: list[float]) -> list[list[str]]:
    out = []
    for p, v in d.items():
        row = [p, str(v.get("n", 0))]
        for h in hz:
            row += [_p(v.get(f"fwd_{h:g}h"), 3), _n(v.get(f"t_{h:g}h"), 2)]
        out.append(row)
    return out


def _period_headers(hz: list[float]) -> list[str]:
    h = ["period", "n"]
    for x in hz:
        h += [f"{x:g} h mean", "t"]
    return h


def _family_stage_a(res: V5Research, fam: str) -> list[str]:
    sa = res.stage_a
    d = sa.get("by_family", {}).get(fam, {})
    g = res.gate.get("families", {}).get(fam, {})
    nl = res.stage_a_null.get(fam, {})
    gate_h = [float(h.rstrip("h")) for h in g.get("horizons", {})]
    lines: list[str] = []
    if not d or not d.get("n"):
        return ["- No events of this family in the window.", ""]
    lines += [
        f"- Events: {d['n']} ({d['n_first_in_cluster']} first-in-cluster: no same family/side event in the previous 12 bars); LONG {d.get('by_side_n_all', {}).get('LONG', 0)} / SHORT {d.get('by_side_n_all', {}).get('SHORT', 0)} (all events). Mean MFE {_n(d.get('mfe_atr'), 2)} ATR / mean MAE {_n(d.get('mae_atr'), 2)} ATR over 12 h (medians {_n(d.get('mfe_atr_median'), 2)} / {_n(d.get('mae_atr_median'), 2)}); strength p10/p50/p90 {_n(d.get('strength_quantiles', {}).get('p10'), 2)} / {_n(d.get('strength_quantiles', {}).get('p50'), 2)} / {_n(d.get('strength_quantiles', {}).get('p90'), 2)}.",
        "",
        "First-in-cluster events, both sides (the gate population):",
        "",
        *_hz_or_empty(d.get("first_in_cluster")),
        "All events (clusters included):",
        "",
        *_hz_or_empty(d.get("all")),
    ]
    for sd in ("LONG", "SHORT"):
        lines += [f"{sd} (first-in-cluster):", "", *_hz_or_empty(d.get("by_side", {}).get(sd))]
    if nl:
        lines += [
            "Stage A null (random bars, same count and side mix, 200 replications):",
            "",
            *_table(
                [
                    "horizon",
                    "events mean",
                    "time-matched null mean (sd)",
                    "z",
                    "P(null >= events)",
                    "regime-matched null mean (sd)",
                    "z",
                    "P(null >= events)",
                ],
                [
                    [
                        f"{h:g} h",
                        _p(nl["real"].get(h), 3),
                        f"{_p(nl['time'][f'{h:g}h']['null_mean'], 3)} ({_p(nl['time'][f'{h:g}h']['null_sd'], 3)})",
                        _n(nl["time"][f"{h:g}h"]["z"], 2),
                        _p(nl["time"][f"{h:g}h"]["frac_ge_real"], 0),
                        f"{_p(nl['regime'][f'{h:g}h']['null_mean'], 3)} ({_p(nl['regime'][f'{h:g}h']['null_sd'], 3)})",
                        _n(nl["regime"][f"{h:g}h"]["z"], 2),
                        _p(nl["regime"][f"{h:g}h"]["frac_ge_real"], 0),
                    ]
                    for h in gate_h
                ],
            ),
        ]
    lines += [
        "By regime (first-in-cluster, 4H trend x 24h realised-volatility state):",
        "",
        *_table(_period_headers(gate_h), _period_rows(d.get("by_regime", {}), gate_h)),
        "Gate evaluation (pre-declared): "
        + "; ".join(
            f"{h}: mean {_p(v['mean'], 3)} (t {_n(v['t'], 2)}, CI {_ci(v)}), positive in {v['positive_years']}/{v['n_years']} years, top tercile {_p(v['top_tercile'], 3)} vs bottom {_p(v['bottom_tercile'], 3)} -> {'PASS' if v['passed'] else 'fail'}"
            for h, v in g.get("horizons", {}).items()
        )
        + f"; n >= {res.gate.get('min_events')}: {'yes' if g.get('n_ok') else 'no'} (n={g.get('n', 0)}) -> family {'PASSED' if g.get('passed') else 'FAILED'} the gate.",
        "",
    ]
    return lines


def render_v5(res: V5Research) -> str:
    m, rm = res.manifest, res.manifest["raw_manifest"]
    o, cs, ac = res.overall, res.costs, res.account
    sa = res.stage_a
    fq, hd, ch, fun = res.freq, res.hold, res.chrono, res.funnel
    g, nl, dl, av = res.geometry, res.null, res.delay, res.availability
    col = res.collector
    start, end = rm["period_start_ms"], rm["period_end_ms"]
    unc = sa.get("unconditional", {})
    gate_h = [1.0, 4.0]
    lines: list[str] = [
        "# BTC Swing V5 — Microstructure & Liquidation Driven Active Swing: research report",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · window {_ms(start)} -> {_ms(end)} UTC · config `{m['config_hash'][:12]}` · design freeze commit `{m['design_freeze_commit']}` · raw result hash `{rm['result_hash'][:12]}` · code `{rm['code_version']}`",
        "",
        "**Central question.** Do observable microstructure events — liquidation-type flushes (historically an OI-flush proxy), absorption of aggressive flow, flow-plus-open-interest continuation and price/flow divergence at range extremes — carry a repeatable signed forward edge, and does that edge survive a simple deterministic execution rule and realistic costs at roughly 1-3 trades per day?",
        "",
        f"**Validation constraint.** {m['validation_note']} One frozen configuration, one Stage B run, no ML, no parameter search; the next genuine validation is forward paper testing after a freeze. Paper/backtest only; no live or paper trading engine, no exchange keys, no order placement, no real money.",
        "",
        "## 1. V1-V4 lessons",
        "",
        "- V1 (structural setups, frozen defaults): small positive gross edge erased by costs; untouched 2025-26 holdout net negative. V2 (learned ranking of V1 candidates): no usable ranking signal. V3 (active 4H/1H/15m/5m price-structure generator): activity profile met (1.5 trades/day, 3 h median hold) but gross expectancy slightly negative and 0.66% stops made costs 0.34R per trade. V4 (event and positioning driven, 1H events): Stage A gate failed; only one family/side showed a small-sample signal; stops at 1.54% kept cost drag low but there was no gross edge to protect.",
        "- V5 therefore moves the event clock to 5 minutes and to the flow itself (aggressor side from every trade, cumulative volume delta, open-interest change, basis/premium/funding), measures event edge at 15m-12h horizons BEFORE any execution (Stage A, with a per-family gate, bootstrap intervals, strength monotonicity and year stability), keeps one generic execution rule for all families, and floors the stop at 1.25 ATR(1h) so costs stay a small fraction of 1R.",
        "",
        "## 2. V5 hypothesis",
        "",
        "- Four pre-registered microstructure event families (A liquidation/OI-flush continuation, B absorption reversal, C flow+OI continuation, D flow divergence reversal), defined on z-scores of trailing 30-day distributions of trade-flow, open-interest and price features at 5-minute granularity, carry a positive signed forward return over 1-4 h that is visible in first-in-cluster events, monotone in event strength and stable across years; with a single 15m confirmation, a 5m zone entry, a volatility-floored structural stop and a generic TP1/TP2/trail exit, the edge survives taker fees, slippage and funding at 0.5-3 trades per day.",
        "",
        "## 3. Design freeze proof",
        "",
        *_table(
            ["check", "value"],
            [
                [
                    "design document",
                    f"`docs/BTC_SWING_V5_DESIGN.md` and `config/btc_swing_v5.yaml`, committed at `{m['design_freeze_commit']}` before any feature, event, Stage A or Stage B result existed",
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
                    "one Stage A scan; one raw Stage B run; the overlay, 0.5%, cost-sensitivity and leverage-cap streams and the audits never changed a rule",
                ],
                ["deterministic rerun identical", _n(m["determinism_identical"])],
            ],
        ),
        "## 4. Historical data availability (audit before any result; nothing fabricated, nothing backfilled)",
        "",
        *_table(
            ["field", "source", "available", "used in V5 history", "note"],
            [
                [
                    "aggTrades (every trade, aggressor flag)",
                    "Binance Vision futures/um/daily/aggTrades",
                    "yes, 2019-12-31 -> present",
                    "yes: 5m aggregates (buy/sell qty, counts, big trades >= 1 BTC, vwap)",
                    av.get("aggtrades_flow", {}).get("note", ""),
                ],
                [
                    "order-book depth",
                    "Binance Vision futures/um/daily/bookDepth",
                    "yes, 2023-01-01 -> present (+-1..5% snapshots ~30 s)",
                    "diagnostics only (no event uses it)",
                    av.get("book_depth", {}).get("note", ""),
                ],
                [
                    "best bid/ask (bookTicker)",
                    "Binance Vision",
                    "partial (2023-05-16 -> 2024-03-30 only)",
                    "no",
                    "too short and discontinuous; not ingested",
                ],
                [
                    "liquidations",
                    "Binance Vision liquidationSnapshot",
                    "NOT available for BTCUSDT UM (404)",
                    "no",
                    av.get("liquidations", {}).get("note", ""),
                ],
                [
                    "open interest",
                    "Binance Vision metrics (5m)",
                    "yes",
                    "yes: level, 5m/15m/1h change, acceleration, z",
                    av.get("open_interest", {}).get("note", ""),
                ],
                [
                    "mark / index price",
                    "Binance Vision markPriceKlines / indexPriceKlines",
                    "yes",
                    "yes: liquidation on mark; basis = perp/index - 1",
                    av.get("index_klines", {}).get("note", ""),
                ],
                [
                    "premium index",
                    "Binance Vision premiumIndexKlines",
                    "yes",
                    "yes: 1h mean, z",
                    "",
                ],
                [
                    "funding",
                    "Binance Vision fundingRate",
                    "yes",
                    "yes: level, change, z (90 obs)",
                    "",
                ],
                [
                    "taker buy/sell ratios",
                    "Binance Vision metrics + klines taker-buy volume",
                    "yes",
                    "kline taker-buy volume is the fallback for a missing aggTrades day",
                    "",
                ],
                [
                    "volume delta / CVD",
                    "derived from aggTrades aggregates",
                    "yes",
                    "yes",
                    "delta = taker buy - taker sell quantity per 5m bar",
                ],
                [
                    "Bybit historical trades",
                    "public.bybit.com/trading",
                    "reachable",
                    "no",
                    "Binance is the historical instrument; Bybit is the forward collector venue",
                ],
            ],
        ),
        "Coverage inside the study window:",
        "",
        *_table(
            ["dataset", "rows", "first", "last", "share of 5m bars covered"],
            [
                [
                    k,
                    str(d.get("rows", "")),
                    str(d.get("first", "")),
                    str(d.get("last", "")),
                    _p(d.get("share_of_bars_in_window"), 1),
                ]
                for k, d in av.items()
                if isinstance(d, dict) and "share_of_bars_in_window" in d
            ],
        ),
        f"- aggTrades aggregates cover {_p(av.get('aggtrades_flow', {}).get('share_of_bars_in_window'), 1)} of the 5m bars in the window; the kline taker-buy fallback covers {_p(av.get('aggtrades_flow', {}).get('share_kline_fallback'), 1)}. Order-book features exist only from 2023-01-01 and are never an event input.",
        "",
        *_table(
            ["archive dataset", "rows", "first", "last", "rows in window", "expected", "coverage"],
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
        "## 5. Forward-data availability (Bybit public WebSocket, no authentication)",
        "",
        "- Collector (`btc_swing/v5/collector.py`): `wss://stream.bybit.com/v5/public/linear`, topics publicTrade, orderbook.50, tickers (mark, index, funding, open interest, best bid/ask) and allLiquidation for BTCUSDT; every message is stored verbatim as an immutable JSONL row (ts_received_ms, ts_exchange_ms, symbol, channel, type, schema_version, sha256 of the payload, raw payload) in hourly files plus a state file for resumption; duplicate suppression on topic + exchange ts + update id; order-book sequence-gap detection (`u` must increase by one between deltas, snapshot resets); ping heartbeat and a 30 s stale timeout; reconnect with 1-30 s backoff; periodic flush/state save. Verification results are in section 33.",
        "- The liquidation stream therefore exists ONLY forward; the historical family A is the labelled OI-flush proxy and the forward collector makes the liquidation-burst template measurable later without any re-fitting.",
        "",
        "## 6. PIT audit",
        "",
        f"- {res.pit['visibility_rule']}.",
        f"- Deterministic rerun: {_n(m['determinism_identical'])}. Truncation audit with all data after {res.pit['truncation']['cut'][:16]} removed: decisions identical {_n(res.pit['truncation']['decisions_identical'])} ({res.pit['truncation']['rows_compared']} rows); events identical {_n(res.pit['truncation']['events_identical'])} ({res.pit['truncation']['events_compared']} events up to one day before the cut).",
        "- Resampling oracle vs native archive bars: "
        + ", ".join(
            f"{tf}: {d['bars_compared']} bars, {d['bars_differing']} differ"
            for tf, d in res.pit["resample_oracle"].items()
        )
        + f". Liquidation basis: {res.pit['liquidation_basis']}.",
        "- Forward labels (Stage A) are computed strictly from bars after the event bar; nothing in the feature frame reads a later row (rolling statistics exclude the current row). No order book is reconstructed from candles; no liquidation history is approximated.",
        "",
        "## 7. Microstructure feature definitions (frozen `v5-feat-1`; z = rolling z-score over the previous 30 days of 5m rows, excluding the current row, min 10 days)",
        "",
        "- Trade flow: taker buy/sell quantity per 5m bar (aggTrades `is_buyer_maker == false` = taker buy; kline taker-buy volume as fallback), delta = buy - sell, delta over 15m / 1h, imbalance_1h = delta_1h / (buy+sell)_1h, CVD = cumulative delta, CVD slope 1h/4h (= delta over the window per bar), flow acceleration = delta_15m minus the previous 15m delta, big-trade imbalance over 1h (trades >= 1 BTC); z-scores of imbalance_1h, cvd_slope_1h/4h, flow acceleration and big-trade imbalance; divergence = z(ret_1h) - z(cvd_slope_1h).",
        "- Order book (2023-01-01 onwards, last snapshot <= T, stale after 1 h): bid/ask depth within 1% and 5%, imbalance_1 and imbalance_5, 1h change of imbalance_1, z-scores of bid1, ask1 and total depth. Diagnostics only.",
        "- Open interest (metrics 5m, observation time + latency <= T): level; 5m/15m/1h change; acceleration (15m change minus the previous one); z of the 1h and 15m change.",
        "- Derivatives: funding level/change/z (90 observations); premium-index 1h mean and z; basis = perp close / index close - 1, 1h change, z.",
        "- Price/volume: returns 5m/15m/1h/4h with z of ret_15m and ret_1h; realised volatility 1h/24h and its z; ATR14 from the 1H series; 5m and 1h volume z; range position over 48 h; new 24h high/low flags (previous 288 bars); 4H trend state (EMA21/EMA50/close) and 1H alignment (close vs EMA21). Regime label = 4H trend x 24h-volatility z state.",
        f"- Feature rows in the window: {res.feature_quality.get('rows_in_window')}; missing share: "
        + ", ".join(
            f"{k} {_p(res.feature_quality[k]['missing_share'], 1)}"
            for k in (
                "imbalance_1h_z",
                "cvd_slope_1h_z",
                "oi_chg_1h_z",
                "fund_z",
                "prem_z",
                "basis_z",
                "book_imb_1",
                "vol_1h_z",
            )
            if k in res.feature_quality
        )
        + ".",
        "",
        "## 8. Liquidation events (family A, historical OI-flush PROXY)",
        "",
        "- Definition (LONG, SHORT mirrored): signed 1h-return z >= +2.0, OI 1h-change z <= -1.5, 1h volume z >= +1.5, signed 1h taker-imbalance z >= +1.0; strength = vol z - OI-change z; direction = with the impulse. Forward-only template: the same with the liquidation-stream burst z replacing the OI flush.",
        "",
        *_family_stage_a(res, V5Family.LIQUIDATION_CONTINUATION.value),
        "## 9. Absorption events (family B)",
        "",
        "- Definition (LONG, SHORT mirrored): signed 1h taker-imbalance z <= -2.0 (heavy aggressive selling), 1h volume z >= +1.0, signed 1h-return z >= -0.5 (impact absorbed); strength = (-signed imbalance z) - |return z|.",
        "",
        *_family_stage_a(res, V5Family.ABSORPTION_REVERSAL.value),
        "## 10. Flow / OI continuation events (family C)",
        "",
        "- Definition (LONG, SHORT mirrored): signed 1h-return z >= +1.0, signed imbalance z >= +1.0, signed CVD-slope z >= +1.0, OI 1h-change z >= +1.0, not crowded (signed funding z and premium z <= +2.0); strength = mean of the three signed flow/OI z-scores.",
        "",
        *_family_stage_a(res, V5Family.FLOW_OI_CONTINUATION.value),
        "## 11. Divergence events (family D)",
        "",
        "- Definition: SHORT when the 5m high exceeds the previous 288-bar high with CVD-slope z <= 0 and OI 1h-change z <= 0 (strength = -(CVD z + OI z)/2); LONG mirror at a new 24h low with CVD-slope z >= 0 and OI 1h-change z <= 0 (OI term unsigned: no new positioning).",
        "",
        *_family_stage_a(res, V5Family.FLOW_DIVERGENCE_REVERSAL.value),
        "## 12. Stage A forward returns (pooled; signed in the event direction from the event bar close)",
        "",
        "Pooled, all events:",
        "",
        *_hz_or_empty(sa.get("pooled")),
        "Pooled, first-in-cluster events only:",
        "",
        *_hz_or_empty(sa.get("pooled_first_in_cluster")),
        "Unconditional BTC forward return of every 5m bar over the same horizons (unsigned drift and dispersion):",
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
                for h in [0.25, 0.5, 1, 2, 4, 8, 12]
            ],
        ),
        "- Pooled numbers mix families with different directions and strengths; the gate is evaluated per family (sections 8-11).",
        "",
        "## 13. Event-strength monotonicity (first-in-cluster events, within-family terciles; 1 = weakest)",
        "",
    ]
    for fam in [f.value for f in V5Family]:
        d = sa.get("by_family", {}).get(fam, {})
        terc = d.get("terciles")
        if terc:
            lines += [
                f"{fam}:",
                "",
                *_table(
                    [
                        "tercile",
                        "n",
                        "strength range",
                        "15m",
                        "1h",
                        "2h",
                        "4h",
                        "8h",
                        "12h",
                        "MFE ATR",
                        "MAE ATR",
                    ],
                    [
                        [
                            str(t_["tercile"]),
                            str(t_["n"]),
                            f"{_n(t_['strength_min'], 2)} .. {_n(t_['strength_max'], 2)}",
                            _p(t_["fwd_0.25h"], 3),
                            _p(t_["fwd_1h"], 3),
                            _p(t_["fwd_2h"], 3),
                            _p(t_["fwd_4h"], 3),
                            _p(t_["fwd_8h"], 3),
                            _p(t_["fwd_12h"], 3),
                            _n(t_["mfe_atr"], 2),
                            _n(t_["mae_atr"], 2),
                        ]
                        for t_ in terc
                    ],
                ),
            ]
        else:
            lines += [
                f"- {fam}: n={d.get('n_first_in_cluster', 0)} first-in-cluster events (fewer than 30: no terciles).",
                "",
            ]
    lines += [
        "## 14. Event frequency",
        "",
        *_table(
            ["family", "side", "events", "first-in-cluster"],
            [
                [d["family"], d["side"], str(d["len"]), str(d["first_in_cluster"])]
                for d in sa.get("by_family_side_counts", [])
            ],
        ),
        *_table(
            ["stage", "count", "per day"],
            [
                [
                    "events (every 5m bar, both sides)",
                    str(fun.get("events")),
                    _n(fun.get("events_per_day"), 2),
                ],
                [
                    "first-in-cluster events",
                    str(fun.get("first_in_cluster")),
                    _n(fun.get("first_in_cluster_per_day"), 2),
                ],
                [
                    "episodes opened by the lifecycle (slot, cooldown and 24 h anchor de-duplication)",
                    str(fun.get("episodes")),
                    _n(fun.get("episodes_per_day"), 2),
                ],
                [
                    "qualified setups (15m confirmation inside the 2 h window)",
                    str(fun.get("confirmed")),
                    _n(fun.get("confirmed_per_day"), 2),
                ],
                ["trades", str(fun.get("trades")), _n(fun.get("trades_per_day"), 3)],
            ],
        ),
        "## 15. Year stability (Stage A, first-in-cluster events; mean signed return and t at 1 h / 4 h)",
        "",
        "Pooled by year:",
        "",
        *_table(
            _period_headers(gate_h),
            _period_rows({y: {**v} for y, v in sa.get("by_year", {}).items()}, gate_h),
        ),
    ]
    for fam in [f.value for f in V5Family]:
        d = sa.get("by_family", {}).get(fam, {})
        if d.get("by_year"):
            lines += [
                f"{fam} by year:",
                "",
                *_table(_period_headers(gate_h), _period_rows(d["by_year"], gate_h)),
            ]
            lines += [
                f"{fam} by quarter:",
                "",
                *_table(_period_headers(gate_h), _period_rows(d["by_quarter"], gate_h)),
            ]
    ep = res.episodes
    lines += [
        "## 16. Stage B execution (generic, identical for all families)",
        "",
        "- Context 4H/1H from the feature frame (reported, not gated); confirmation = the first completed 15m bar after the event whose close is beyond the previous 15m close in the trade direction, inside a 2 h thesis window; entry zone [event close - 0.5 ATR, event close + 1.0 ATR] (LONG, mirrored); execution = a completed 5m close inside the zone within 6 bars of the confirmation, fill at the next 5m open plus slippage; one net position; family priority A > B > C > D only when two detectors fire on the same bar; same anchor not re-armed within 24 h.",
        "",
        *_table(
            ["stage", "count"],
            [
                ["events (Stage A)", str(ep.get("events_total"))],
                ["episodes opened", str(ep.get("n"))],
                ["confirmed (qualified setups)", str(fun.get("confirmed"))],
                [
                    "reached ENTRY_READY (confirmed and 5m close in zone)",
                    str(fun.get("entry_ready")),
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
        "## 17. Entry delay and movement missed",
        "",
        *_table(
            ["metric", "mean", "p10", "p50", "p90", "max"],
            [
                [
                    k,
                    _n(v.get("mean"), 1),
                    _n(v.get("p10"), 1),
                    _n(v.get("p50"), 1),
                    _n(v.get("p90"), 1),
                    _n(v.get("max"), 1),
                ]
                for k, v in (
                    ("confirmation delay after the event (min)", dl.get("confirm_delay_min", {})),
                    ("entry (fill) delay after the event (min)", dl.get("entry_delay_min", {})),
                    ("movement missed, signed (ATR)", dl.get("missed_move_atr", {})),
                    ("movement missed, signed (%)", dl.get("missed_move_pct", {})),
                )
            ],
        ),
        f"- Share of trades that entered more than 0.5 ATR beyond the event close: {_p(dl.get('share_missed_over_half_atr'), 0)}; share that entered at a better price than the event close: {_p(dl.get('share_entered_below_event_close'), 0)}.",
        "",
        *_table(
            [
                "family",
                "n",
                "median entry delay (min)",
                "median confirmation delay (min)",
                "median missed (ATR)",
                "mean missed (ATR)",
            ],
            [
                [
                    d["family"],
                    str(d["n"]),
                    _n(d["median_entry_delay_min"], 0),
                    _n(d["median_confirm_delay_min"], 0),
                    _n(d["median_missed_atr"], 2),
                    _n(d["mean_missed_atr"], 2),
                ]
                for d in dl.get("by_family", [])
            ],
        ),
        "## 18. Stop geometry and cost-to-risk",
        "",
        *_table(
            ["metric", "value"],
            [
                [
                    "median / mean stop (% of price)",
                    f"{_n(g.get('median_stop_pct'), 2)} / {_n(g.get('mean_stop_pct'), 2)} (V3: 0.66, V4: 1.54)",
                ],
                [
                    "p10 / p90 stop %",
                    f"{_n(g.get('p10_stop_pct'), 2)} / {_n(g.get('p90_stop_pct'), 2)}",
                ],
                [
                    "median / p10 / p90 stop in ATR(1h)",
                    f"{_n(g.get('median_stop_atr'), 2)} / {_n(g.get('p10_stop_atr'), 2)} / {_n(g.get('p90_stop_atr'), 2)}",
                ],
                [
                    "share of stops set by the 1.25 ATR volatility floor / capped at 3 ATR",
                    f"{_p(g.get('share_vol_floor'), 0)} / {_p(g.get('share_capped'), 0)}",
                ],
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
            ["family", "n", "median stop %", "median stop ATR", "share vol floor"],
            [
                [
                    d["family"],
                    str(d["n"]),
                    _n(d["median_stop_pct"], 2),
                    _n(d["median_stop_atr"], 2),
                    _p(d["share_vol_floor"], 0),
                ]
                for d in g.get("by_family", [])
            ],
        ),
        "## 19. Gross expectancy (before fees, slippage and funding)",
        "",
        *_table(
            ["population", "n", "gross R", "net R", "PF (net)", "win", "median R"],
            [
                [
                    "all trades",
                    str(o.get("n", 0)),
                    _n(cs.get("expectancy_R_before_costs")),
                    _n(cs.get("expectancy_R_net")),
                    _n(o.get("profit_factor"), 2),
                    _p(o.get("win_rate"), 0),
                    _n(o.get("median_R")),
                ],
                *[
                    [
                        f"{f['family']} {f['side']}",
                        str(f.get("n", 0)),
                        _n(f.get("gross_mean_R")),
                        _n(f.get("mean_R")),
                        _n(f.get("profit_factor"), 2),
                        _p(f.get("win_rate"), 0),
                        _n(f.get("median_R")),
                    ]
                    for f in res.families
                    if f.get("n")
                ],
            ],
        ),
        "- Benchmark for economic interest (pre-declared, not a tuning target): gross expectancy >= +0.15R.",
        "",
        "## 20. Costs",
        "",
        *_table(
            [
                "component",
                "primary (frozen)",
                "Bybit-style sensitivity (taker 5.5 bps, maker 2 bps on targets), NOT for classification",
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
        "## 21. Net expectancy (raw stream, 0.25% risk) and chronology",
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
        "By year (2026 = YTD to the end of the window):",
        "",
        *_table(PERIOD_HEADERS, [_row(y, d) for y, d in ch.get("years", {}).items()]),
        "By quarter:",
        "",
        *_table(PERIOD_HEADERS, [_row(q, d) for q, d in ch.get("quarters", {}).items()]),
        "## 22. LONG vs SHORT",
        "",
        *_table(
            [*STAT_HEADERS, "gross R", "cost drag R", "median hold h"],
            [
                [
                    *_stat_row(s, d),
                    _n(d.get("gross_mean_R")),
                    _n(d.get("cost_drag_R")),
                    _n(d.get("median_hold_h"), 1),
                ]
                for s, d in res.sides.items()
            ],
        ),
        "## 23. Event-family trade performance",
        "",
        *_table(
            [
                "family",
                "side",
                "events",
                "first-in-cluster",
                "episodes",
                "qualified",
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
                "median delay min",
                "median missed ATR",
                "cost drag R",
                "DD-window P&L",
                "P&L share",
            ],
            [
                [
                    f["family"].replace("_", " "),
                    f["side"],
                    str(f.get("events", 0)),
                    str(f.get("events_first_in_cluster", 0)),
                    str(f.get("episodes", 0)),
                    str(f.get("qualified", 0)),
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
                    _n(f.get("median_entry_delay_min"), 0),
                    _n(f.get("median_missed_atr"), 2),
                    _n(f.get("cost_drag_R")),
                    _n(f.get("dd_window_pnl"), 0),
                    _p(f.get("pnl_share"), 0),
                ]
                for f in res.families
            ],
        ),
        "- Every family and side is shown (zero rows included); none is hidden. Trade-level strength terciles (within family):",
        "",
    ]
    for d in res.strength:
        if d.get("terciles"):
            lines += [
                f"{d['family']} (n={d['n']}):",
                "",
                *_table(
                    ["tercile", "n", "gross R", "net R", "win"],
                    [
                        [
                            str(t_["tercile"]),
                            str(t_["n"]),
                            _n(t_["mean_R_gross"]),
                            _n(t_["mean_R"]),
                            _p(t_["win_rate"], 0),
                        ]
                        for t_ in d["terciles"]
                    ],
                ),
            ]
        else:
            lines += [f"- {d['family']}: n={d.get('n', 0)} (too few trades for terciles).", ""]
    lines += [
        "Regime at trigger (4H trend x 24h-volatility state; reported, not gated):",
        "",
        *_table(STAT_HEADERS, _by_rows(res.regimes, "regime_at_trigger")),
        "Derivatives/flow snapshot at entry (means for winners vs losers; univariate Spearman vs net R):",
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
        "## 24. Holding periods",
        "",
        *_table(
            ["quantile", "hours"],
            [[f"p{int(q * 100)}", _n(h, 1)] for q, h in hd.get("quantiles", {}).items()],
        ),
        *_table(
            ["bucket", "share", "n"],
            [[b["bucket"], _p(b["share"], 0), str(b["n"])] for b in hd.get("buckets", [])],
        ),
        "## 25. Frequency (events/day, qualified setups/day, trades/day)",
        "",
        *_table(
            ["metric", "value"],
            [
                ["events / day", _n(fun.get("events_per_day"), 2)],
                ["first-in-cluster events / day", _n(fun.get("first_in_cluster_per_day"), 2)],
                ["qualified setups / day", _n(fun.get("confirmed_per_day"), 2)],
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
        "## 26. MFE / MAE",
        "",
        *_table(
            ["metric", "value"],
            [
                [k, _p(v, 1) if ("hit_rate" in k) else _n(v, 3)]
                for k, v in res.mfe.items()
                if k != "n"
            ],
        ),
        "## 27. Exits",
        "",
        *_table(STAT_HEADERS, _by_rows(res.exits, "exit_reason")),
        "- TP1 +1R (40%, stop to breakeven), TP2 +2R (30%), remainder trails the 1H swing - 0.5 ATR(1h) after TP1 (applied from the next bar), 24 h cap, stop-first; counterfactual 1R / 1.5R / 2R / 3R-before-stop recorded per trade (`cf_hit_*` columns of the trade frame; 1.5R share above). Not optimised.",
        "",
        "## 28. Daily safety overlay (reported separately)",
        "",
        *_table(
            [
                "metric",
                "raw",
                "overlay (3 full-risk losses or -0.75% realised in a UTC day -> no new entries)",
            ],
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
        "## 29. Account simulation",
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
        *_table(
            ["drawdown metric", "value"],
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
        "## 30. Leverage / liquidation",
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
        "## 31. Null benchmarks (time-matched and regime-matched random entries with the same side, stop %, ATR, sizing, exits and costs; K = 10 per trade)",
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
                    "V5 strategy",
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
        "- The Stage A nulls per family (random bars, no execution) are in sections 8-11.",
        "",
        "## 32. Outlier robustness",
        "",
        *_table(
            ["metric", "value"],
            [
                [
                    "mean net R / median net R",
                    f"{_n(res.outl.get('mean_R'))} / {_n(res.outl.get('median_R'))}",
                ],
                [
                    "without the best 1 / 3 / 5 trades",
                    f"{_n(res.outl.get('mean_R_without_best1'))} / {_n(res.outl.get('mean_R_without_best3'))} / {_n(res.outl.get('mean_R_without_best5'))}",
                ],
                [
                    "without the worst 1 / 3 / 5 trades",
                    f"{_n(res.outl.get('mean_R_without_worst1'))} / {_n(res.outl.get('mean_R_without_worst3'))} / {_n(res.outl.get('mean_R_without_worst5'))}",
                ],
                ["without the best 5 and worst 5", _n(res.outl.get("mean_R_without_best5_worst5"))],
                [
                    "gross R without the best 1 / 3 / 5",
                    f"{_n(res.outl.get('mean_R_gross_without_best1'))} / {_n(res.outl.get('mean_R_gross_without_best3'))} / {_n(res.outl.get('mean_R_gross_without_best5'))}",
                ],
                [
                    "best / worst trade (R)",
                    f"{_n(res.outl.get('best_trade_R'), 2)} / {_n(res.outl.get('worst_trade_R'), 2)}",
                ],
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
        "## 33. Bybit collector verification (public data, no authentication, no orders)",
        "",
    ]
    if col:
        lat = col.get("latency_ms", {})
        st = col.get("storage", {})
        lines += [
            *_table(
                ["check", "result"],
                [
                    [
                        "uptime test",
                        f"{_n(col.get('uptime_seconds'), 0)} s bounded run ({'with' if col.get('forced_reconnect_requested') else 'without'} a forced mid-run reconnect)",
                    ],
                    [
                        "messages received",
                        f"{col.get('messages')} ({_n(col.get('messages_per_second'), 1)}/s): "
                        + ", ".join(f"{k} {v}" for k, v in col.get("by_topic", {}).items()),
                    ],
                    [
                        "order-book deltas / sequence gaps / gap rate",
                        f"{col.get('orderbook_deltas')} / {col.get('sequence_gaps')} / {_p(col.get('sequence_gap_rate'), 3)}",
                    ],
                    ["duplicates suppressed", str(col.get("duplicates"))],
                    [
                        "reconnect test",
                        f"{col.get('reconnects')} reconnect(s), {col.get('stale_timeouts')} stale timeout(s), {col.get('errors')} transport error(s); subscription re-established and storage continued",
                    ],
                    [
                        "raw storage verification",
                        f"{st.get('files')} hourly file(s), {st.get('rows')} rows re-read, {st.get('hash_mismatches')} payload hash mismatches, {_n((col.get('bytes_written') or 0) / 1e6, 1)} MB; per channel "
                        + ", ".join(f"{k} {v}" for k, v in st.get("by_channel", {}).items()),
                    ],
                    [
                        "timestamp latency (received - exchange ts, ms)",
                        f"p50 {_n(lat.get('p50'), 0)}, p90 {_n(lat.get('p90'), 0)}, p99 {_n(lat.get('p99'), 0)}, max {_n(lat.get('max'), 0)} over {_n(lat.get('n'), 0)} messages",
                    ],
                    ["resumed from a previous state file", _n(col.get("resumed_from_state"))],
                    [
                        "liquidation messages",
                        str(col.get("by_topic", {}).get("allLiquidation.BTCUSDT", 0))
                        + " (the stream carries only actual liquidations; a short run can legitimately see none)",
                    ],
                ],
            ),
        ]
        extra_runs = col.get("additional_runs", [])
        for r in extra_runs:
            lines += [
                f"- Additional run ({r.get('label', '')}): {_n(r.get('uptime_seconds'), 0)} s, {r.get('messages')} messages, {r.get('sequence_gaps')} gaps, {r.get('reconnects')} reconnects, resumed from state {_n(r.get('resumed_from_state'))}, storage rows {r.get('storage', {}).get('rows')} with {r.get('storage', {}).get('hash_mismatches')} mismatches."
            ]
        lines += [""]
    else:
        lines += ["- No collector run was attached to this research run.", ""]
    lines += [
        "- Data persisted under `data/btc/forward/bybit/BTCUSDT/<date>/<hour>.jsonl` (git-ignored); a restart continues from the state file; nothing is modified after it is written.",
        "",
        "## 34. Bybit Demo architecture (designed, NOT activated)",
        "",
        "- `btc_swing/v5/execution.py`: `ExecutionAdapter` protocol (place_order, cancel_order, position_state, set_stop, set_take_profit, account_balance, fills, funding) with typed request/response records (`OrderRequest`, `OrderAck`, `PositionState`, `Fill`, `FundingEvent`, `Balance`); `DryRunAdapter` records intents in memory and never fills; `BybitDemoAdapter` holds the endpoint map (`api-demo.bybit.com`: /v5/order/create, /v5/order/cancel, /v5/position/list, /v5/position/trading-stop, /v5/account/wallet-balance, /v5/execution/list, /v5/account/transaction-log) and raises `NotActivatedError` on every method; constructing it with `activated=True` also raises. No credentials are read, required or stored anywhere in V5; no order was placed.",
        "- Activation would be a separate owner-approved change outside any research phase: authenticated transport (API key, timestamp, recv_window, HMAC signature), idempotent client order ids, position/fill reconciliation against the public collector, and a kill switch; none of it exists in this repository.",
        "",
        "## 35. Limitations",
        "",
        "- Development data (2022-2026 inspected by V1-V4); pre-registration limits researcher degrees of freedom but cannot make results out-of-sample. 2025-01..2026-09 was V1's spent holdout and is development data here.",
        "- Liquidation history does not exist in the archive; family A is an OI-flush proxy and is labelled as such. Order-book history starts 2023-01-01 and is diagnostic only. aggTrades aggregates are 5-minute sums; intra-bar sequencing (sweeps, icebergs) is not observable.",
        "- Metrics (OI) are 5-minute archive rows with observation time as published; funding every 8 h; one instrument (Binance USDT-M perp); the forward venue is Bybit, whose flow, book and liquidation microstructure differ.",
        "- Fill model: next 5m open plus fixed slippage; taker fees on every fill in the primary model; no latency, queue or partial-fill model; liquidation on mark price.",
        "- Event clusters are auto-correlated; first-in-cluster tables, bootstrap intervals and the per-family nulls should be read together. Stage A returns are gross of costs.",
        "- The overlay, the 0.5% and the Bybit-style streams are reporting views; the classification uses the raw 0.25% stream with the frozen primary costs.",
        "",
        "## 36. Recommendation and pre-declared criteria",
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
        "## Appendix — frozen V5 configuration",
        "",
        "```yaml",
        m["config_yaml"].strip(),
        "```",
        "",
    ]
    return "\n".join(lines)


def _recommendation(res: V5Research) -> list[str]:
    cls = res.classification
    failed = [str(c["id"]) for c in res.criteria if not c["met"]]
    fams = [f for f in res.families if f["side"] == "ALL" and f.get("n")]
    fam_txt = "; ".join(
        f"{f['family']} gross {_n(f.get('gross_mean_R'))}R / net {_n(f.get('mean_R'))}R (n={f['n']})"
        for f in fams
    )
    gate_txt = ", ".join(res.gate.get("passed_families", [])) or "none"
    if cls.startswith("A"):
        return [
            f"- Stage A gate passed ({gate_txt}) and all ten criteria met. Next step (owner decision): freeze V5 as configured and start a forward PAPER test on the Bybit public feed with a fixed start date, using the collector output and the dry-run adapter; no live trading, no tuning. Families: {fam_txt}."
        ]
    if cls.startswith("B"):
        return [
            f"- Microstructure events carry gross predictive signal (gate passed by {gate_txt}; pooled gross expectancy >= +0.10R; beats both nulls) but criteria {', '.join(failed)} fail. A controlled V5.1 study is justified only as a NEW owner pre-registration (fixed feature space, fixed criteria, new window); nothing is implemented here. Families: {fam_txt}."
        ]
    return [
        f"- Criteria {', '.join(failed)} fail and the B conditions do not hold (gate passed by: {gate_txt}): the frozen microstructure events do not produce a robust, cost-surviving edge. No threshold, stop, window, exit or family is changed and no re-run is proposed. Families: {fam_txt}."
    ]
