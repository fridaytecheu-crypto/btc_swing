"""N1 / N2 / N4 screens: lagged-percentile PIT safety, current bar excluded from its threshold, prior
range excludes the signal bar, 1-minute embargo, no same-bar fills, session calendar / DST, N2 signed
volume and three-sign agreement, parent dedup, determinism, zero-day frequency stats, overlap not
double-counted, future rows cannot alter prior signals."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

import numpy as np

from btc_swing.research.intraday_portfolio import ncensus as nc
from btc_swing.research.intraday_portfolio import nengines as ne
from btc_swing.research.intraday_portfolio.contract import MS_1M, EventTimes
from btc_swing.research.intraday_portfolio.episodes import Exit, ParentEpisode
from btc_swing.research.intraday_portfolio.primitives import (
    Bars,
    bars_1m_to_5m,
    completed_bars,
    efficiency_ratio,
    wilder_atr,
)

T0 = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)  # a Monday, UTC midnight


def make_1m(
    n_days: int,
    seed: int = 3,
    impulses: dict[int, float] | None = None,
    vol: float = 1.0,
    noise: float = 2e-4,
) -> Bars:
    """Seeded random-walk 1m bars (`noise` per minute; 0 = flat tape) with optional single-minute
    impulses (bps). A flat tape makes the impulse the ONLY N1 flag, so dedup can be asserted exactly."""
    rng = np.random.default_rng(seed)
    n = n_days * 1440
    steps = rng.normal(0, noise, n) if noise > 0 else np.zeros(n)
    for k, bps in (impulses or {}).items():
        steps[k] += bps / 1e4
    close = 50_000.0 * np.cumprod(1 + steps)
    open_ = np.r_[50_000.0, close[:-1]]
    high = np.maximum(open_, close) * (1 + 1e-5)
    low = np.minimum(open_, close) * (1 - 1e-5)
    v = np.full(n, vol)
    open_ms = (T0 + np.arange(n, dtype=np.int64) * MS_1M).astype(np.int64)
    return completed_bars(open_ms, open_, high, low, close, v, close, v / 2, v / 2, MS_1M)


def ctx_for(b1: Bars, **kw: object) -> ne.NContext:
    b5 = bars_1m_to_5m(b1)
    return ne.NContext(b5, b1, wilder_atr(b5), efficiency_ratio(b5), **kw)  # type: ignore[arg-type]


def _slice(b: Bars, n: int) -> Bars:
    return Bars(
        b.open_ms[:n], b.close_ms[:n], b.open[:n], b.high[:n], b.low[:n], b.close[:n],
        b.volume[:n], b.vwap[:n], b.buy[:n], b.sell[:n], b.step_ms,
    )  # fmt: skip


# ----------------------------------------------------------------------------- thresholds (PIT)
def test_lagged_quantile_uses_only_prior_complete_days_and_excludes_the_signal_day() -> None:
    days = np.array(
        [(date(2024, 1, 1) + timedelta(days=d)).isoformat() for d in range(40) for _ in range(4)]
    )
    vals = np.ones(len(days))
    thr = ne.lagged_quantile_by_day(vals, days, set(days), 0.95, min_days=30)
    assert "2024-01-30" not in thr and "2024-01-31" in thr  # needs 30 complete prior days
    vals2 = vals.copy()
    vals2[days == "2024-01-31"] = 1000.0  # the signal day's own values
    vals2[days >= "2024-02-01"] = 1000.0  # and every later day
    thr2 = ne.lagged_quantile_by_day(vals2, days, set(days), 0.95, min_days=30)
    assert thr2["2024-01-31"] == thr["2024-01-31"] == 1.0
    assert thr2["2024-02-05"] > 1.0  # days 31..35 enter the history of later days
    # an incomplete day never counts as history
    comp = set(days) - {"2024-01-05"}
    thr3 = ne.lagged_quantile_by_day(vals, days, comp, 0.95, min_days=30)
    assert "2024-01-31" not in thr3 and "2024-02-01" in thr3


# ----------------------------------------------------------------------------- N1
def test_n1_signal_embargo_prior_range_and_dedup() -> None:
    k_imp = 33 * 1440 + 600 + 3  # day 34, minute 603 (inside the 5m bar opening at minute 600)
    b1 = make_1m(36, impulses={k_imp: 80.0, k_imp + 20: 80.0}, noise=0.0)  # 2nd flag 20 min later
    ctx = ctx_for(b1)
    res = ne.run_n1(ctx)
    i = int(np.searchsorted(ctx.b5.open_ms, T0 + (33 * 1440 + 600) * MS_1M))
    # on a flat tape the two impulses are the only flags: one parent, one repeat
    assert len(res.parents) == 1 and res.raw_flags == 2
    p = res.parents[0]
    assert p.side == "LONG" and p.first_setup_time_ms == int(ctx.b5.close_ms[i])
    # prior 30m range = the six bars BEFORE the signal bar (the signal bar's own high is excluded)
    assert p.frozen_levels["prior_30m_high"] == float(np.max(ctx.b5.high[i - 6 : i]))
    assert p.frozen_levels["prior_30m_high"] < float(ctx.b5.high[i])
    # the current bar did not shape its own threshold: |impulse| far above a noise-only p95
    assert p.frozen_levels["threshold_abs_ret"] < 0.003 < abs(p.notes["impulse_bps"]) / 1e4
    # embargo: entry = OPEN of the minute after decision + 1 min; never the signal close
    assert p.times.benchmark_entry_time == p.first_setup_time_ms + MS_1M
    j = int(np.searchsorted(b1.open_ms, p.times.benchmark_entry_time))
    assert (
        p.entry_benchmark == float(b1.open[j])
        and p.times.benchmark_entry_time > p.first_setup_time_ms
    )
    assert p.exit is not None and p.exit.time_ms == p.times.benchmark_entry_time + 30 * MS_1M
    # the second impulse (20 min later, same side) is a repeat of the parent, not a new opportunity
    assert int(ctx.b5.close_ms[i + 4]) in p.repeated_trigger_times_ms
    assert p.repeated_triggers == 1
    assert p.traded and math.isfinite(p.notes["gross_60m_diag_bps"])
    assert p.stop_bps == p.frozen_levels["atr_bps"] and p.notes["risk_unit"] == "ATR20_bps"


def test_n1_future_rows_cannot_alter_prior_signals_and_results_are_deterministic() -> None:
    k_imp = 32 * 1440 + 300 + 2
    b1 = make_1m(36, impulses={k_imp: 70.0, 35 * 1440 + 100: 70.0}, noise=0.0)
    full = ne.run_n1(ctx_for(b1)).parents
    again = ne.run_n1(ctx_for(b1)).parents
    assert [p.as_row() for p in full] == [p.as_row() for p in again]
    cut = k_imp + 120  # keep two hours after the first impulse, drop the later impulse
    part = ne.run_n1(ctx_for(_slice(b1, cut))).parents
    first_full = [p.as_row() for p in full if p.first_setup_time_ms < T0 + cut * MS_1M]
    first_part = [p.as_row() for p in part]
    assert first_part == first_full and len(first_part) >= 1


def test_horizon_outcome_refuses_missing_entry_or_path() -> None:
    b1 = make_1m(2)
    keep = [k for k in range(len(b1)) if k != 700]
    gap = completed_bars(
        b1.open_ms[keep], b1.open[keep], b1.high[keep], b1.low[keep], b1.close[keep],
        b1.volume[keep], b1.vwap[keep], b1.buy[keep], b1.sell[keep], MS_1M,
    )  # fmt: skip
    assert ne.horizon_outcome(gap, T0 + 700 * MS_1M, 30, 1) is None  # entry minute absent
    assert ne.horizon_outcome(gap, T0 + 690 * MS_1M, 30, 1) is None  # gap inside the path
    oc = ne.horizon_outcome(gap, T0 + 100 * MS_1M, 30, 1)
    assert oc is not None and oc.exit_ms == T0 + 130 * MS_1M and oc.entry_px == float(b1.open[100])


# ----------------------------------------------------------------------------- N2
def test_n2_signed_volume_formula_and_three_sign_agreement_and_dedup() -> None:
    assert ne.signed_quote_volume(60.0, 100.0) == 20.0  # buy 60 - sell 40
    assert ne.signed_quote_volume(40.0, 100.0) == -20.0
    b1 = make_1m(36)
    b5 = bars_1m_to_5m(b1)
    flows: dict[int, tuple[float, float]] = {}
    for t in b5.open_ms.tolist():
        flows[int(t)] = (
            100.0,
            50.0,
        )  # balanced: signed flow 0, never a candidate (signs never agree)
    i = int(np.searchsorted(b5.close_ms, T0 + (33 * 1440 + 600) * MS_1M))  # a 15m boundary close
    assert int(b5.close_ms[i]) % ne.MS_15M == 0
    agree = dict(flows)
    for k in (i - 2, i - 1, i):
        agree[int(b5.open_ms[k])] = (100.0, 90.0)  # +80 each -> +240
    for k in (i + 4, i + 5, i + 6):  # the next boundary 30 min later, same side -> repeat
        agree[int(b5.open_ms[k])] = (100.0, 90.0)
    ctx = ctx_for(b1, flow_spot=agree)
    res = ne.run_n2(ctx, "SPOT")
    ps = [p for p in res.parents if p.side == "LONG"]
    assert len(ps) == 1 and ps[0].first_setup_time_ms == int(b5.close_ms[i])
    assert ps[0].repeated_triggers == 1 and ps[0].frozen_levels["flow15_signed_quote"] == 240.0
    assert ps[0].times.benchmark_entry_time == int(b5.close_ms[i]) + MS_1M
    disagree = dict(flows)
    for k in (i - 2, i - 1):
        disagree[int(b5.open_ms[k])] = (100.0, 95.0)  # +90, +90
    disagree[int(b5.open_ms[i])] = (100.0, 10.0)  # -80 -> sum +100 (top decile) but signs disagree
    res2 = ne.run_n2(ctx_for(b1, flow_spot=disagree), "SPOT")
    assert not any(p.first_setup_time_ms == int(b5.close_ms[i]) for p in res2.parents)
    missing = dict(agree)
    del missing[int(b5.open_ms[i - 1])]  # a missing constituent -> no candidate, counted as missing
    res3 = ne.run_n2(ctx_for(b1, flow_spot=missing), "SPOT")
    assert not any(p.first_setup_time_ms == int(b5.close_ms[i]) for p in res3.parents)
    assert res3.extra["boundaries_missing_flow_fields"] >= 1


# ----------------------------------------------------------------------------- N4
def test_session_anchor_dst_weekend_and_nonexistent_time() -> None:
    def utc(ms: int | None) -> str | None:
        return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%H:%M") if ms else None

    assert utc(ne.session_anchor_utc_ms(date(2024, 1, 15), "Europe/London", 8, 0)) == "08:00"  # GMT
    assert utc(ne.session_anchor_utc_ms(date(2024, 7, 15), "Europe/London", 8, 0)) == "07:00"  # BST
    assert utc(ne.session_anchor_utc_ms(date(2024, 1, 15), "America/New_York", 9, 30)) == "14:30"
    assert utc(ne.session_anchor_utc_ms(date(2024, 7, 15), "America/New_York", 9, 30)) == "13:30"
    # US DST starts 2024-03-10 (London still GMT until 03-31): the offsets diverge for three weeks
    assert utc(ne.session_anchor_utc_ms(date(2024, 3, 11), "America/New_York", 9, 30)) == "13:30"
    assert utc(ne.session_anchor_utc_ms(date(2024, 3, 11), "Europe/London", 8, 0)) == "08:00"
    assert utc(ne.session_anchor_utc_ms(date(2024, 1, 15), "Asia/Tokyo", 9, 0)) == "00:00"
    assert ne.session_anchor_utc_ms(date(2024, 1, 13), "Asia/Tokyo", 9, 0) is None  # Saturday
    assert ne.session_anchor_utc_ms(date(2024, 3, 10), "America/New_York", 2, 30) is None  # gap


def test_n4_volume_rule_sessions_and_one_parent_per_anchor_day() -> None:
    b1 = make_1m(50, vol=1.0)  # 50 days from Monday 2024-01-01 -> 36 weekdays
    ctx = ctx_for(b1)
    assert ne.run_n4(ctx).parents == []  # constant volume is never > its median
    v = b1.volume.copy()
    # raise the opening 15m volume of the Tokyo session on 2024-02-13 (a Tuesday)
    a = ne.session_anchor_utc_ms(date(2024, 2, 13), "Asia/Tokyo", 9, 0)
    assert a is not None
    k = int((a - T0) // MS_1M)
    v[k : k + 15] = 5.0
    b1b = completed_bars(
        b1.open_ms, b1.open, b1.high, b1.low, b1.close, v, b1.vwap, v / 2, v / 2, MS_1M
    )
    res = ne.run_n4(ctx_for(b1b))
    assert len(res.parents) == 1
    p = res.parents[0]
    assert p.notes["session"] == "TOKYO" and p.frozen_levels["anchor_ms"] == float(a)
    assert (
        p.first_setup_time_ms == a + 15 * MS_1M and p.times.benchmark_entry_time == a + 16 * MS_1M
    )
    assert p.notes["utc_offset_min"] == 540 and p.exit is not None
    assert p.exit.time_ms == a + 16 * MS_1M + 45 * MS_1M
    assert res.extra["sessions"]["TOKYO"]["sessions"] >= 31


# ----------------------------------------------------------------------------- census / overlap
def test_stage_stats_include_zero_event_days() -> None:
    t = [T0 + 3 * 86_400_000, T0 + 3 * 86_400_000 + 5000, T0 + 7 * 86_400_000]
    s = nc.stage_stats(t, ["LONG", "SHORT", "LONG"], "2024-01-01", "2024-01-10")
    assert s["calendar_days"] == 10 and s["zero_event_days"] == 8
    assert s["per_calendar_day"] == 0.3 and s["daily_p10"] == 0 and s["daily_p50"] == 0
    assert s["daily_p90"] == 2 and s["long"] == 2 and s["short"] == 1 and s["months"] == 1


def _p(pid: str, engine: str, side: str, entry_ms: int, hold: int, net: float) -> ParentEpisode:
    p = ParentEpisode(
        parent_id=pid, engine=engine, labels=[engine], side=side, first_setup_time_ms=entry_ms - MS_1M,
        receipt_time_ms=None, times=EventTimes(entry_ms - MS_1M, None, None, entry_ms - MS_1M, entry_ms, None),
        frozen_levels={}, regime="RANGE", er=0.1,
    )  # fmt: skip
    p.entry_benchmark, p.stop_bps = 100.0, 40.0
    p.exit = Exit("HORIZON", entry_ms + hold * MS_1M, 100.0)
    p.gross_bps, p.cost_bps, p.net_bps, p.net_r, p.hold_min = (
        net + 16,
        16.0,
        net,
        net / 40,
        float(hold),
    )
    p.eligibility_state = "CLOSED"
    return p


def test_overlap_replay_does_not_double_count() -> None:
    a = _p("A", "N1", "LONG", T0 + 10 * MS_1M, 30, 10.0)
    b = _p("B", "N2S", "LONG", T0 + 20 * MS_1M, 30, 5.0)  # inside A -> blocked, same direction
    c = _p("C", "N4", "SHORT", T0 + 30 * MS_1M, 45, 5.0)  # inside A -> blocked, opposite
    d = _p("D", "N2S", "SHORT", T0 + 60 * MS_1M, 30, -3.0)  # free
    rows = nc.overlap_replay({"N1": [a], "N2S": [b, d], "N4": [c]})
    st = {r.parent_id: (r.available, r.blocked_by_engine, r.same_direction) for r in rows}
    assert st == {
        "A": (True, None, None),
        "B": (False, "N1", True),
        "C": (False, "N1", False),
        "D": (True, None, None),
    }
    summ = {
        r["engine"]: r
        for r in nc.overlap_summary(
            {"N1": [a], "N2S": [b, d], "N4": [c]}, "2024-01-01", "2024-01-02"
        )
    }
    assert (
        summ["UNION"]["available_in_union"]
        == 2
        == sum(summ[e]["available_in_union"] for e in ("N1", "N2S", "N4"))
    )
    assert (
        summ["N1"]["incremental_available_vs_others"] == 0
    )  # without N1, B takes A's slot: union stays 2
    assert (
        summ["N2S"]["incremental_available_vs_others"] == 1
        and summ["N4"]["incremental_available_vs_others"] == 0
    )
    assert (
        summ["N2S"]["same_direction_overlap"] == 1
        and summ["N4"]["opposite_direction_conflict"] == 1
    )
    assert (
        summ["N1"]["profitable_available_at_16bps"] == 1
        and summ["N2S"]["profitable_available_at_16bps"] == 0
    )


def test_cadence_verdict_cuts_are_fixed() -> None:
    assert nc.cadence_verdict(1.0, 0.5, 12)["verdict"] == "PLAUSIBLE CORE CADENCE"
    assert nc.cadence_verdict(0.5, 0.1, 12)["verdict"] == "SATELLITE ONLY"
    assert nc.cadence_verdict(0.1, 0.0, 12)["verdict"] == "TOO LOW"
    assert nc.cadence_verdict(2.0, 1.0, 3)["verdict"] == "INCONCLUSIVE"
