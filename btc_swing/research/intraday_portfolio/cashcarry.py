"""CASH-FUNDED SPOT / SHORT-PERP FUNDING CARRY — one bounded economic feasibility screen (Binance, chosen
before any result because matching spot and perpetual traded histories exist). Not execution
qualification; no real-money activation; nothing is tuned.

Frozen policy: at 00:05 UTC on Jan 1 / Apr 1 / Jul 1 / Oct 1 decide (no signal, no filter: negative
funding stays in); at 00:10 UTC buy q = 1 BTC spot and sell exactly q BTC of the linear perpetual at the
synchronized 5m traded-bar OPENs; committed capital C0 = spot purchase cost + collateral equal to the short's
entry notional + both entry transaction costs; scheduled exit exactly 90 calendar days later at the 00:10
traded-bar OPEN; capital protection: if the short account's marked equity (posted collateral + short unrealised
PnL on the mark + cumulative funding - incurred perpetual costs) reaches <= 25 % of the current short
notional, close BOTH legs at the first traded-bar open at least five minutes after the breach is observable,
no collateral injection, no re-entry inside the window; the window return is measured over the full 90 days
(proceeds idle in non-yielding USDT). Funding F = sum q * M_t * r_t over settlements in (entry, exit]; the
settlement mark is the OPEN of the 5m mark bar containing the stamp (flagged proxy; the archive carries no
settlement mark), else the perpetual traded open (flagged), else the window is UNRESOLVED. Net = q(ST - S0) +
q(P0 - PT) + F - costs; the two price terms ARE the basis PnL. Costs per execution: spot 12 bps / perp 8 bps
(stress 20 / 12), charged on each execution's own proxy notional. USD translation with Coinbase USDT-USD daily
opens; cash benchmark = SOFR compounded act/360 over the same window (opportunity cost only)."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from btc_swing.research.intraday_portfolio import ORDER_CAPABLE, RESEARCH_ONLY
from btc_swing.research.intraday_portfolio.carry import MS_5M, VenueData, load_venues
from btc_swing.research.intraday_portfolio.data import HOLDOUT_START
from btc_swing.research.intraday_portfolio.extdata import (
    fingerprint,
    load_sofr,
    load_usdtusd,
    sofr_path,
    usdtusd_path,
)
from btc_swing.research.intraday_portfolio.run import _f, _package_hashes, _write_table
from btc_swing.research.intraday_portfolio.run_n import load_perp_5m
from btc_swing.research.intraday_portfolio.spot import load_spot_5m

MS_D = 86_400_000
Q_BTC = 1.0
HOLD_DAYS = 90
DECISION_MIN, ENTRY_MIN = 5, 10
PROTECT_RATIO = 0.25
PROTECT_DELAY_MS = 5 * 60_000
COST_SPOT, COST_PERP = 12e-4, 8e-4
COST_SPOT_STRESS, COST_PERP_STRESS = 20e-4, 12e-4
FIRST_DECISION = date(2022, 1, 1)
QUARTER_MONTHS = (1, 4, 7, 10)
MIN_WINDOWS, MIN_SPAN_YEARS = 12, 3.0
BOOT_N, BOOT_SEED, BOOT_BLOCK = 10_000, 42, 2
HURDLE_ANNUAL_EXCESS = 0.03
GAP_NEAR_THRESHOLD_RATIO = 0.35


# ----------------------------------------------------------------------------- inputs
@dataclass
class Inputs:
    spot_open: dict[int, float]
    perp_open: dict[int, float]
    venue: VenueData  # Binance mark 5m + funding
    sofr: dict[str, float]
    usdtusd: dict[int, float]
    spot_span: tuple[int, int]
    perp_span: tuple[int, int]


def load_inputs(data_dir: Path) -> tuple[Inputs, dict[str, Any]]:
    spot = load_spot_5m(data_dir)
    perp = load_perp_5m(data_dir)
    venues, inv = load_venues(data_dir)
    sofr, usd = load_sofr(data_dir), load_usdtusd(data_dir)
    missing = [
        k
        for k, v in (
            ("binance_spot_5m", spot),
            ("binance_perp_5m", perp),
            ("binance_mark_funding", venues.get("BINANCE")),
            ("sofr", sofr),
            ("usdtusd", usd),
        )
        if v is None
    ]
    if missing:
        raise FileNotFoundError(f"cash-carry inputs missing: {missing}")
    assert spot is not None and perp is not None and sofr is not None and usd is not None
    so = {
        int(t): float(o)
        for t, o in zip(spot["open_time_ms"].to_list(), spot["open"].to_list(), strict=True)
    }
    po = {
        int(t): float(o)
        for t, o in zip(perp["open_time_ms"].to_list(), perp["open"].to_list(), strict=True)
    }
    v = venues["BINANCE"]
    fp = {
        "binance_spot_5m": {
            "rows": spot.height,
            "first_ms": int(spot["open_time_ms"][0]),
            "last_ms": int(spot["open_time_ms"][-1]),
            "source": "data.binance.vision spot monthly klines (sha256 manifest manifests/binance_spot_klines_5m_manifest.jsonl)",
        },
        "binance_perp_5m": {
            "rows": perp.height,
            "first_ms": int(perp["open_time_ms"][0]),
            "last_ms": int(perp["open_time_ms"][-1]),
            "source": "data.binance.vision USD-M perp monthly klines (store manifests)",
        },
        "binance_mark_5m_and_funding": {
            k: v_ for k, v_ in inv["BINANCE"].items() if k not in ("funding",)
        },
        "sofr": fingerprint(sofr_path(data_dir)),
        "usdtusd_coinbase": fingerprint(usdtusd_path(data_dir)),
        "holdout_never_read_from": HOLDOUT_START.isoformat(),
    }
    return Inputs(
        so,
        po,
        v,
        sofr,
        usd,
        (int(spot["open_time_ms"][0]), int(spot["open_time_ms"][-1])),
        (int(perp["open_time_ms"][0]), int(perp["open_time_ms"][-1])),
    ), fp


# ----------------------------------------------------------------------------- helpers
def _ms(d: date, minute: int = 0) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000) + minute * 60_000


def _day(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date()


def cash_growth(sofr: dict[str, float], start: date, end: date) -> tuple[float, int]:
    """Compounded SOFR act/360 over [start, end): each calendar day accrues the last published rate on or
    before it (weekends / holidays carry the previous business day's fixing). Returns (growth, days without
    any prior fixing)."""
    keys = sorted(sofr)
    g, missing = 1.0, 0
    d = start
    while d < end:
        k = d.isoformat()
        idx = int(np.searchsorted(np.array(keys), k, side="right")) - 1
        if idx < 0:
            missing += 1
        else:
            g *= 1 + sofr[keys[idx]] / 100 / 360
        d += timedelta(days=1)
    return g, missing


def settlement_mark(inp: Inputs, t_ms: int) -> tuple[float | None, str]:
    m = inp.venue.mark_at(t_ms)
    if m is not None:
        return m, "MARK_BAR_OPEN"
    p = inp.perp_open.get(int(t_ms) // MS_5M * MS_5M)
    if p is not None:
        return p, "PERP_TRADED_OPEN_FALLBACK"
    return None, "UNRESOLVED"


def first_traded_bar_at_or_after(inp: Inputs, t_ms: int, limit_ms: int) -> int | None:
    t = (int(t_ms) + MS_5M - 1) // MS_5M * MS_5M
    while t <= limit_ms:
        if t in inp.spot_open and t in inp.perp_open:
            return t
        t += MS_5M
    return None


# ----------------------------------------------------------------------------- allocation
@dataclass
class Allocation:
    window_id: str
    decision_day: str
    entry_ms: int
    exit_sched_ms: int
    exit_ms: int
    early_exit: bool
    protection_breach_ms: int | None
    valid: bool
    issues: list[str]
    s0: float
    p0: float
    st: float
    pt: float
    c0: float
    n0: float
    funding: float
    funding_count: int
    funding_negative_count: int
    settlement_mark_fallbacks: int
    spot_pnl: float
    perp_pnl: float
    basis_pnl: float
    cost_primary: float
    cost_stress: float
    net_primary: float
    net_stress: float
    net_ex_funding_primary: float
    net_bps_n0: float
    ret_capital: float
    ret_capital_stress: float
    ret_usd: float
    ret_usd_stress: float
    cash_ret: float
    excess_usd: float
    excess_usd_stress: float
    excess_annualized: float
    usdtusd_entry: float
    usdtusd_exit: float
    min_equity: float
    min_equity_ratio: float
    max_notional_over_equity: float
    basis_path_min: float
    mark_gap_bars: int
    gap_near_threshold: bool
    year: int = 0

    def as_row(self) -> dict[str, Any]:
        r = dict(self.__dict__)
        r["issues"] = "|".join(self.issues)
        return r


def _costs(s0: float, p0: float, st: float, pt: float, cs: float, cp: float) -> float:
    return Q_BTC * (s0 * cs + st * cs + p0 * cp + pt * cp)


def run_window(inp: Inputs, decision: date, wid: str) -> Allocation:
    entry = _ms(decision, ENTRY_MIN)
    exit_sched = entry + HOLD_DAYS * MS_D
    issues: list[str] = []
    s0, p0 = inp.spot_open.get(entry), inp.perp_open.get(entry)
    if s0 is None or p0 is None:
        issues.append("ENTRY_BARS_MISSING")
    if exit_sched not in inp.spot_open or exit_sched not in inp.perp_open:
        issues.append("SCHEDULED_EXIT_BARS_MISSING")
    x0 = inp.usdtusd.get(_ms(decision))
    x_t = inp.usdtusd.get(_ms(_day(exit_sched)))
    if x0 is None or x_t is None:
        issues.append("USDTUSD_MISSING")
    growth, miss = cash_growth(inp.sofr, decision, _day(exit_sched))
    if miss:
        issues.append(f"SOFR_MISSING_{miss}_DAYS")
    nan = math.nan
    if issues:
        return Allocation(
            wid,
            decision.isoformat(),
            entry,
            exit_sched,
            exit_sched,
            False,
            None,
            False,
            issues,
            s0 or nan,
            p0 or nan,
            nan,
            nan,
            nan,
            nan,
            nan,
            0,
            0,
            0,
            nan,
            nan,
            nan,
            nan,
            nan,
            nan,
            nan,
            nan,
            nan,
            nan,
            nan,
            nan,
            nan,
            growth - 1,
            nan,
            nan,
            nan,
            x0 or nan,
            x_t or nan,
            nan,
            nan,
            nan,
            nan,
            0,
            False,
            decision.year,
        )
    assert s0 is not None and p0 is not None and x0 is not None and x_t is not None
    coll = Q_BTC * p0
    perp_entry_cost = Q_BTC * p0 * COST_PERP
    # funding settlements in (entry, exit_sched] with their marks (flagged proxies)
    sets = inp.venue.settlements_in(entry, exit_sched, closed_left=False)
    marks: list[tuple[int, float, float, str]] = []
    for t, r in sets:
        m, src = settlement_mark(inp, t)
        if m is None:
            issues.append("UNRESOLVED_SETTLEMENT_MARK")
            continue
        marks.append((t, r, m, src))
    # protection scan on the 5m mark grid (bar opens), funding credited once its stamp is reached
    v = inp.venue
    i0 = v._mark_idx.get(entry)
    breach: int | None = None
    min_eq, min_ratio, max_ratio_ne = math.inf, math.inf, 0.0
    gaps = 0
    ratio_near_gap = False
    k = 0
    f_cum = 0.0
    t = entry
    while t <= exit_sched and breach is None:
        while k < len(marks) and marks[k][0] <= t:
            f_cum += Q_BTC * marks[k][2] * marks[k][1]
            k += 1
        i = v._mark_idx.get(t)
        if i is None:
            gaps += 1
            if min_ratio < GAP_NEAR_THRESHOLD_RATIO:
                ratio_near_gap = True
        else:
            m = float(v.mark_open[i])
            eq = coll + Q_BTC * (p0 - m) + f_cum - perp_entry_cost
            ratio = eq / (Q_BTC * m)
            min_eq, min_ratio = min(min_eq, eq), min(min_ratio, ratio)
            if eq > 0:
                max_ratio_ne = max(max_ratio_ne, Q_BTC * m / eq)
            else:
                max_ratio_ne = math.inf
            if ratio <= PROTECT_RATIO:
                breach = t
        t += MS_5M
    _ = i0
    exit_ms = exit_sched
    early = False
    if breach is not None:
        ex = first_traded_bar_at_or_after(inp, breach + PROTECT_DELAY_MS, exit_sched)
        if ex is None:
            issues.append("PROTECTION_EXIT_BAR_MISSING")
            exit_ms = exit_sched
        else:
            exit_ms, early = ex, True
    st, pt = inp.spot_open[exit_ms], inp.perp_open[exit_ms]
    f_total = sum(Q_BTC * m * r for (tt, r, m, _s) in marks if tt <= exit_ms)
    n_set = sum(1 for (tt, _r, _m, _s) in marks if tt <= exit_ms)
    n_neg = sum(1 for (tt, r, _m, _s) in marks if tt <= exit_ms and r < 0)
    n_fb = sum(1 for (tt, _r, _m, s) in marks if tt <= exit_ms and s != "MARK_BAR_OPEN")
    spot_pnl = Q_BTC * (st - s0)
    perp_pnl = Q_BTC * (p0 - pt)
    basis = spot_pnl + perp_pnl
    cost_p = _costs(s0, p0, st, pt, COST_SPOT, COST_PERP)
    cost_s = _costs(s0, p0, st, pt, COST_SPOT_STRESS, COST_PERP_STRESS)
    c0 = Q_BTC * s0 + coll + Q_BTC * s0 * COST_SPOT + perp_entry_cost
    c0_s = Q_BTC * s0 + coll + Q_BTC * s0 * COST_SPOT_STRESS + Q_BTC * p0 * COST_PERP_STRESS
    net_p, net_s = basis + f_total - cost_p, basis + f_total - cost_s
    n0 = Q_BTC * (s0 + p0) / 2
    ret_c, ret_cs = net_p / c0, net_s / c0_s
    ret_usd = (c0 + net_p) * x_t / (c0 * x0) - 1
    ret_usd_s = (c0_s + net_s) * x_t / (c0_s * x0) - 1
    cash = growth - 1
    # basis path on traded opens (same venue, both traded)
    bp = math.inf
    tt = entry
    while tt <= exit_ms:
        s_, p_ = inp.spot_open.get(tt), inp.perp_open.get(tt)
        if s_ is not None and p_ is not None:
            bp = min(bp, Q_BTC * (s_ - s0) + Q_BTC * (p0 - p_))
        tt += MS_5M
    return Allocation(
        wid,
        decision.isoformat(),
        entry,
        exit_sched,
        exit_ms,
        early,
        breach,
        "UNRESOLVED_SETTLEMENT_MARK" not in issues and "PROTECTION_EXIT_BAR_MISSING" not in issues,
        issues,
        s0,
        p0,
        st,
        pt,
        c0,
        n0,
        f_total,
        n_set,
        n_neg,
        n_fb,
        spot_pnl,
        perp_pnl,
        basis,
        cost_p,
        cost_s,
        net_p,
        net_s,
        net_p - f_total,
        1e4 * net_p / n0,
        ret_c,
        ret_cs,
        ret_usd,
        ret_usd_s,
        cash,
        ret_usd - cash,
        ret_usd_s - cash,
        (ret_usd - cash) * 365 / HOLD_DAYS,
        x0,
        x_t,
        min_eq,
        min_ratio,
        max_ratio_ne,
        bp,
        gaps,
        ratio_near_gap,
        decision.year,
    )


def decision_days(inp: Inputs) -> list[date]:
    last_bar = min(inp.spot_span[1], inp.perp_span[1])
    out: list[date] = []
    d = FIRST_DECISION
    while _ms(d, ENTRY_MIN) + HOLD_DAYS * MS_D <= last_bar:
        out.append(d)
        m = d.month + 3
        d = date(d.year + (m > 12), m - 12 if m > 12 else m, 1)
    return out


# ----------------------------------------------------------------------------- continuous diagnostic
@dataclass
class ContinuousResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def run_continuous(inp: Inputs, days: list[date]) -> ContinuousResult:
    """Lifecycle (frozen before computing): enter once at the first window's entry with q = 1 and C0 from
    that entry; hold across boundaries (no close / reopen, no collateral reset, no subscription); the
    protection rule applies to the single collateral ledger; after a protection exit stay flat until the next
    quarterly boundary and re-enter with q = 1 only if the accumulated wealth covers spot + collateral +
    entry costs (else flat to the end, reported); close at the last window's scheduled exit. Charged
    executions are the real ones only. Per-window rows are mark-to-market attributions at the boundaries
    (traded opens), labelled MTM."""
    res = ContinuousResult()
    if not days:
        return res
    end_ms = _ms(days[-1], ENTRY_MIN) + HOLD_DAYS * MS_D
    bounds = [_ms(d, ENTRY_MIN) for d in days] + [end_ms]
    wealth = math.nan
    c0 = math.nan
    pos: dict[str, Any] | None = None
    executions: list[tuple[str, int, float]] = []
    realised = 0.0
    f_cum_all = 0.0

    def open_pos(t: int) -> bool:
        nonlocal wealth, c0, pos
        s0, p0 = inp.spot_open.get(t), inp.perp_open.get(t)
        if s0 is None or p0 is None:
            return False
        need = Q_BTC * s0 + Q_BTC * p0 + Q_BTC * s0 * COST_SPOT + Q_BTC * p0 * COST_PERP
        if math.isnan(wealth):
            wealth = c0 = need
        elif wealth < need:
            return False
        pos = {
            "t0": t,
            "s0": s0,
            "p0": p0,
            "coll": Q_BTC * p0,
            "f": 0.0,
            "k": 0,
            "perp_cost": Q_BTC * p0 * COST_PERP,
        }
        executions.extend(
            [
                ("SPOT_BUY", t, Q_BTC * s0 * COST_SPOT),
                ("PERP_SELL", t, Q_BTC * p0 * COST_PERP),
            ]
        )
        return True

    def close_pos(t: int) -> None:
        nonlocal wealth, pos, realised
        assert pos is not None
        st, pt = inp.spot_open[t], inp.perp_open[t]
        cost = Q_BTC * st * COST_SPOT + Q_BTC * pt * COST_PERP
        pnl = (
            Q_BTC * (st - pos["s0"])
            + Q_BTC * (pos["p0"] - pt)
            + pos["f"]
            - pos["perp_cost"]
            - Q_BTC * pos["s0"] * COST_SPOT
            - cost
        )
        wealth += pnl
        realised += pnl
        executions.extend(
            [
                ("SPOT_SELL", t, Q_BTC * st * COST_SPOT),
                ("PERP_BUY", t, Q_BTC * pt * COST_PERP),
            ]
        )
        pos = None

    v = inp.venue
    marks = [
        (t, r, *settlement_mark(inp, t))
        for t, r in v.settlements_in(bounds[0], end_ms, closed_left=False)
    ]
    unresolved = sum(1 for m in marks if m[2] is None)
    marks = [m for m in marks if m[2] is not None]
    mi = 0
    if not open_pos(bounds[0]):
        res.summary = {"status": "FIRST_ENTRY_BARS_MISSING"}
        return res
    flat_since: int | None = None
    exits = 0
    t = bounds[0]
    bi = 1
    attribution_start_wealth = wealth
    while t <= end_ms:
        while mi < len(marks) and marks[mi][0] <= t:
            if pos is not None:
                pos["f"] += Q_BTC * float(marks[mi][2] or 0.0) * marks[mi][1]
                f_cum_all += Q_BTC * float(marks[mi][2] or 0.0) * marks[mi][1]
            mi += 1
        if pos is not None:
            i = v._mark_idx.get(t)
            if i is not None:
                m = float(v.mark_open[i])
                eq = pos["coll"] + Q_BTC * (pos["p0"] - m) + pos["f"] - pos["perp_cost"]
                if eq / (Q_BTC * m) <= PROTECT_RATIO:
                    ex = first_traded_bar_at_or_after(inp, t + PROTECT_DELAY_MS, end_ms)
                    if ex is not None:
                        # advance to the exit bar, crediting settlements in between
                        while mi < len(marks) and marks[mi][0] <= ex:
                            pos["f"] += Q_BTC * float(marks[mi][2] or 0.0) * marks[mi][1]
                            mi += 1
                        close_pos(ex)
                        exits += 1
                        flat_since = ex
                        t = ex
        if bi < len(bounds) and t >= bounds[bi]:
            # boundary: MTM attribution row (traded opens), re-entry if flat
            s_, p_ = inp.spot_open.get(t), inp.perp_open.get(t)
            mtm = wealth
            if pos is not None and s_ is not None and p_ is not None:
                mtm = (
                    wealth
                    + Q_BTC * (s_ - pos["s0"])
                    + Q_BTC * (pos["p0"] - p_)
                    + pos["f"]
                    - pos["perp_cost"]
                    - Q_BTC * pos["s0"] * COST_SPOT
                )
            res.rows.append(
                {
                    "boundary_ms": t,
                    "boundary_day": _day(t).isoformat(),
                    "in_position": pos is not None,
                    "mtm_wealth_usdt": mtm,
                    "mtm_window_return": mtm / attribution_start_wealth - 1
                    if attribution_start_wealth
                    else math.nan,
                    "protection_exits_so_far": exits,
                    "label": "MTM_NO_BOUNDARY_COSTS",
                }
            )
            attribution_start_wealth = mtm
            if pos is None and t < end_ms and flat_since is not None and not open_pos(t):
                res.rows[-1]["note"] = "FLAT_WEALTH_BELOW_REENTRY_REQUIREMENT"
            bi += 1
        t += MS_5M
    if pos is not None:
        while mi < len(marks) and marks[mi][0] <= end_ms:
            pos["f"] += Q_BTC * float(marks[mi][2] or 0.0) * marks[mi][1]
            mi += 1
        close_pos(end_ms)
    x0, x_t = inp.usdtusd.get(_ms(days[0])), inp.usdtusd.get(_ms(_day(end_ms)))
    growth, _m = cash_growth(inp.sofr, days[0], _day(end_ms))
    ret = wealth / c0 - 1
    ret_usd = (wealth * (x_t or math.nan)) / (c0 * (x0 or math.nan)) - 1
    years = (end_ms - bounds[0]) / MS_D / 365
    res.summary = {
        "status": "OK",
        "entry_day": days[0].isoformat(),
        "exit_day": _day(end_ms).isoformat(),
        "c0_usdt": c0,
        "terminal_wealth_usdt": wealth,
        "net_usdt": wealth - c0,
        "funding_usdt": f_cum_all,
        "return_on_capital_usdt": ret,
        "return_usd": ret_usd,
        "cash_return": growth - 1,
        "excess_usd": ret_usd - (growth - 1),
        "excess_annualized": ((1 + ret_usd) / growth) ** (1 / years) - 1 if years > 0 else math.nan,
        "executions": len(executions),
        "execution_costs_usdt": sum(c for _n, _t, c in executions),
        "protection_exits": exits,
        "unresolved_settlement_marks": unresolved,
        "years": years,
    }
    return res


# ----------------------------------------------------------------------------- statistics / verdict
def block_bootstrap_mean(
    x: list[float], block: int = BOOT_BLOCK, n: int = BOOT_N, seed: int = BOOT_SEED
) -> dict[str, Any]:
    """Circular moving-block bootstrap (block = 2 adjacent windows) of the mean: indicative only on ~14
    dependent windows; documented before running."""
    if len(x) < 4:
        return {"status": "INSUFFICIENT"}
    arr = np.asarray(x)
    n_win = len(arr)
    rng = np.random.default_rng(seed)
    nb = math.ceil(n_win / block)
    out = np.empty(n)
    for i in range(n):
        st = rng.integers(0, n_win, nb)
        idx = np.concatenate([(s + np.arange(block)) % n_win for s in st])[:n_win]
        out[i] = arr[idx].mean()
    return {
        "status": "OK",
        "block": block,
        "resamples": n,
        "seed": seed,
        "mean": float(arr.mean()),
        "ci95_low": float(np.quantile(out, 0.025)),
        "ci95_high": float(np.quantile(out, 0.975)),
        "share_positive": float((arr > 0).mean()),
    }


def summarize(ws: list[Allocation], label: str) -> dict[str, Any]:
    v = [w for w in ws if w.valid]
    if not v:
        return {"subset": label, "n": 0}
    return {
        "subset": label,
        "n": len(v),
        "net_usdt_sum": sum(w.net_primary for w in v),
        "funding_usdt_sum": sum(w.funding for w in v),
        "basis_usdt_sum": sum(w.basis_pnl for w in v),
        "cost_usdt_sum": sum(w.cost_primary for w in v),
        "net_bps_n0_mean": statistics.fmean(w.net_bps_n0 for w in v),
        "ret_capital_mean": statistics.fmean(w.ret_capital for w in v),
        "ret_capital_stress_mean": statistics.fmean(w.ret_capital_stress for w in v),
        "ret_usd_mean": statistics.fmean(w.ret_usd for w in v),
        "cash_ret_mean": statistics.fmean(w.cash_ret for w in v),
        "excess_usd_mean": statistics.fmean(w.excess_usd for w in v),
        "excess_usd_stress_mean": statistics.fmean(w.excess_usd_stress for w in v),
        "excess_annualized_mean": statistics.fmean(w.excess_annualized for w in v),
        "excess_positive_windows": sum(1 for w in v if w.excess_usd > 0),
        "funding_positive_windows": sum(1 for w in v if w.funding > 0),
        "funding_share_of_gross": (
            sum(w.funding for w in v) / (sum(w.funding for w in v) + sum(w.basis_pnl for w in v))
        )
        if (sum(w.funding for w in v) + sum(w.basis_pnl for w in v)) != 0
        else None,
        "net_ex_funding_usdt_sum": sum(w.net_ex_funding_primary for w in v),
        "protection_exits": sum(1 for w in v if w.early_exit),
        "min_equity_ratio": min(w.min_equity_ratio for w in v),
        "max_notional_over_equity": max(w.max_notional_over_equity for w in v),
        "mark_gap_bars_total": sum(w.mark_gap_bars for w in v),
    }


def verdict(ws: list[Allocation], boot: dict[str, Any]) -> dict[str, Any]:
    v = [w for w in ws if w.valid]
    n = len(v)
    span_years = (
        ((max(w.entry_ms for w in v) - min(w.entry_ms for w in v)) / MS_D / 365 + HOLD_DAYS / 365)
        if v
        else 0.0
    )
    informative = n >= MIN_WINDOWS and span_years >= MIN_SPAN_YEARS
    full = summarize(ws, "full")
    last4 = summarize(sorted(v, key=lambda w: w.entry_ms)[-4:], "latest_4")
    best = max(v, key=lambda w: w.excess_usd) if v else None
    excl = summarize(
        [w for w in v if best is None or w.window_id != best.window_id], "excluding_best"
    )
    unresolved = [w.window_id for w in ws if not w.valid] + [
        w.window_id for w in v if w.gap_near_threshold
    ]
    gates = {
        "informative_sample_ge_12_windows_3_years": informative,
        "excess_over_cash_positive_full_sample": (full.get("excess_usd_mean") or -1) > 0,
        "excess_over_cash_positive_latest_4": (last4.get("excess_usd_mean") or -1) > 0,
        "excess_positive_under_stress_costs": (full.get("excess_usd_stress_mean") or -1) > 0,
        "excess_positive_excluding_best_allocation": (excl.get("excess_usd_mean") or -1) > 0,
        "funding_supplies_the_return": bool(v)
        and full["funding_usdt_sum"] > 0
        and full["funding_usdt_sum"] > full["basis_usdt_sum"]
        and full["funding_positive_windows"] >= math.ceil(0.75 * n),
        "no_unresolved_accounting_or_capital_problem": not unresolved,
        "annualized_excess_ge_3pp": (full.get("excess_annualized_mean") or -1)
        >= HURDLE_ANNUAL_EXCESS,
        "bootstrap_ci_low_gt_0_indicative": boot.get("ci95_low", -1) > 0,
    }
    net_pos = (full.get("ret_capital_mean") or -1) > 0
    if not informative:
        verdict_ = "INSUFFICIENT EVIDENCE"
    elif not net_pos:
        verdict_ = (
            "REJECT — primary economic hypothesis (net return after primary costs nonpositive)"
        )
    elif all(
        gates[k]
        for k in (
            "excess_over_cash_positive_full_sample",
            "excess_over_cash_positive_latest_4",
            "excess_positive_under_stress_costs",
            "excess_positive_excluding_best_allocation",
            "funding_supplies_the_return",
            "no_unresolved_accounting_or_capital_problem",
            "annualized_excess_ge_3pp",
        )
    ):
        verdict_ = "INITIAL GO TO CONFIRMATION"
    elif (full.get("excess_usd_mean") or -1) <= 0:
        verdict_ = "PARK — unattractive capital allocation (positive but does not exceed cash)"
    else:
        verdict_ = "INCONCLUSIVE"
    return {
        "verdict": verdict_,
        "informative": informative,
        "n_valid": n,
        "span_years": span_years,
        "gates": gates,
        "unresolved_windows": unresolved,
        "best_allocation": best.window_id if best else None,
        "note": "a GO only authorises the identical frozen policy on July 2025 - September 2026 data; never trading",
    }


# ----------------------------------------------------------------------------- run
def run_cashcarry(data_dir: Path, out_root: Path, run_id: str | None = None) -> dict[str, Any]:
    assert RESEARCH_ONLY and not ORDER_CAPABLE
    inp, fp = load_inputs(data_dir)
    run_id = run_id or datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out = out_root / run_id
    out.mkdir(parents=True, exist_ok=True)
    days = decision_days(inp)
    prereg = {
        "run_id": run_id,
        "status": "FROZEN_BEFORE_ANY_PNL",
        "venue": "BINANCE (chosen before results: matching spot and perp traded 5m histories exist)",
        "inputs": fp,
        "scheduled_decisions": [d.isoformat() for d in days],
        "rules": {
            "decision": "00:05 UTC on Jan 1 / Apr 1 / Jul 1 / Oct 1; no signal, no filter; negative funding retained",
            "entry": "00:10 UTC synchronized 5m traded-bar OPENs; LONG q=1 BTC spot, SHORT exactly q BTC linear perp; fixed quantities",
            "capital": "C0 = spot cost + collateral = short entry notional + both entry costs; no borrowing, leverage, cross-margin or collateral yield",
            "hold": "exit at the 00:10 traded-bar open exactly 90 calendar days later; window return measured over the full 90 days even after an early exit (proceeds idle in USDT)",
            "protection": "short-account marked equity = collateral + q(P0 - M_t) + cumulative funding - perp entry cost; breach when <= 25% of the current short notional (5m mark-bar opens); close both legs at the first traded-bar open >= breach + 5 min; no injection, no re-entry in the window; mark gaps are counted, and a gap while the observed ratio was < 0.35 marks the window UNRESOLVED (never assumed safe)",
            "funding": "F = sum q M_t r_t over settlements in (entry, exit]; settlement mark = OPEN of the 5m mark bar containing the stamp (flagged proxy), else perp traded open (flagged), else UNRESOLVED",
            "pnl": "net = q(ST - S0) + q(P0 - PT) + F - costs; the two price terms are the basis PnL (not added again)",
            "costs_per_execution": {
                "spot_bps": [12, 20],
                "perp_bps": [8, 12],
                "charged": "entry and exit on both instruments on each execution's proxy notional; scenario assumptions, not verified fees",
            },
            "denominators": {"one_leg": "N0 = q(S0 + P0)/2", "capital": "C0"},
            "usd": "Coinbase USDT-USD daily OPEN on the entry day and on the scheduled exit day; conversion / withdrawal costs unmodeled",
            "cash_benchmark": "SOFR compounded act/360 over the window, each calendar day at the last fixing on or before it (opportunity cost only)",
            "continuous_diagnostic": run_continuous.__doc__,
            "uncertainty": f"circular moving-block bootstrap of the window excess mean, block {BOOT_BLOCK}, {BOOT_N} resamples, seed {BOOT_SEED}; indicative only (dependent windows)",
            "decision_rules": {
                "informative": f">= {MIN_WINDOWS} valid windows spanning >= {MIN_SPAN_YEARS} years",
                "reject": "informative and mean net return on capital after primary costs <= 0",
                "park": "positive but mean excess over cash <= 0",
                "go": "excess > 0 full sample and latest four, under stress, excluding the best allocation; funding supplies the return (F > 0, F > basis, F > 0 in >= 75% of windows); no unresolved window; annualized excess >= 3 pp",
                "else": "INCONCLUSIVE",
            },
            "holdout": "post-June-2025 outcomes not read in this initial study",
        },
        "package_sha256": _package_hashes(),
    }
    prereg_sha = hashlib.sha256(
        json.dumps(prereg, sort_keys=True, default=str).encode()
    ).hexdigest()
    man_path = out / "cashcarry_manifest.json"
    man_path.write_text(
        json.dumps(
            {"preregistration": prereg, "preregistration_sha256": prereg_sha},
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n"
    )

    ws = [run_window(inp, d, f"W{k + 1:02d}_{d.isoformat()}") for k, d in enumerate(days)]
    valid = [w for w in ws if w.valid]
    boot = block_bootstrap_mean([w.excess_usd for w in valid])
    cont = run_continuous(inp, days)
    years = sorted({w.year for w in valid})
    tables = [
        summarize(ws, "full"),
        summarize(sorted(valid, key=lambda w: w.entry_ms)[-4:], "latest_4"),
    ]
    best = max(valid, key=lambda w: w.excess_usd) if valid else None
    tables.append(
        summarize(
            [w for w in valid if best is None or w.window_id != best.window_id], "excluding_best"
        )
    )
    tables += [summarize([w for w in valid if w.year == y], f"year_{y}") for y in years]
    ver = verdict(ws, boot)
    files = ["cashcarry_manifest.json"]
    files += _write_table([w.as_row() for w in ws], out / "cashcarry_allocations")
    files += _write_table(tables, out / "cashcarry_summary", parquet=False)
    files += _write_table(cont.rows, out / "cashcarry_continuous", parquet=False)
    results = {
        "windows": len(ws),
        "valid_windows": len(valid),
        "invalid": {w.window_id: w.issues for w in ws if not w.valid},
        "summary": tables,
        "bootstrap_excess_mean": boot,
        "continuous": cont.summary,
        "verdict": ver,
    }
    manifest = {
        "schema": "cashcarry_manifest-1",
        "run_id": run_id,
        "generated_at_utc": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "research_only": RESEARCH_ONLY,
        "order_capable": ORDER_CAPABLE,
        "preregistration": prereg,
        "preregistration_sha256": prereg_sha,
        "results": results,
        "files": [*sorted(set(files)), "SUMMARY.md"],
    }
    man_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")
    (out / "SUMMARY.md").write_text(_summary(run_id, manifest, ws))
    return {"run_id": run_id, "out_dir": out, "verdict": ver, "results": results}


def _summary(run_id: str, man: dict[str, Any], ws: list[Allocation]) -> str:
    r = man["results"]
    v = r["verdict"]
    o = [f"# SUMMARY — cash-funded spot / short-perp funding carry (Binance) `{run_id}`", ""]
    o.append(
        f"**{v['verdict']}** — valid windows {v['n_valid']} of {r['windows']}, span {v['span_years']:.2f} years, informative {v['informative']}. Economic feasibility screen on traded / mark bars; not execution qualification; no activation. Preregistration sha256 `{man['preregistration_sha256'][:16]}…`."
    )
    o.append("")
    o.append("## Allocations (q = 1 BTC; USDT unless stated)")
    o.append("")
    o.append(
        "| window | entry | exit | early | funding | #set (neg) | spot PnL | perp PnL | basis | cost (8/12) | net | net bps N0 | ret capital | ret USD | cash | excess | excess (stress) | min eq ratio | mark gaps | issues |"
    )
    o.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for w in ws:
        o.append(
            f"| {w.window_id} | {_day(w.entry_ms)} | {_day(w.exit_ms)} | {w.early_exit} | {_f(w.funding)} | {w.funding_count} ({w.funding_negative_count}) | {_f(w.spot_pnl)} | {_f(w.perp_pnl)} | {_f(w.basis_pnl)} | {_f(w.cost_primary)} | {_f(w.net_primary)} | {_f(w.net_bps_n0)} | {_f(w.ret_capital * 100, 3)}% | {_f(w.ret_usd * 100, 3)}% | {_f(w.cash_ret * 100, 3)}% | {_f(w.excess_usd * 100, 3)}% | {_f(w.excess_usd_stress * 100, 3)}% | {_f(w.min_equity_ratio, 3)} | {w.mark_gap_bars} | {'|'.join(w.issues)} |"
        )
    o.append("")
    o.append("## Summaries (means per window unless 'sum')")
    o.append("")
    o.append(
        "| subset | n | net sum | funding sum | basis sum | cost sum | net bps N0 | ret capital | ret capital stress | ret USD | cash | excess | excess stress | excess ann. | excess>0 | funding>0 | funding share of gross | net ex-funding sum | protection exits | min eq ratio |"
    )
    o.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for s in r["summary"]:
        if s.get("n", 0) == 0:
            o.append(f"| {s['subset']} | 0 | | | | | | | | | | | | | | | | | | |")
            continue
        o.append(
            f"| {s['subset']} | {s['n']} | {_f(s['net_usdt_sum'])} | {_f(s['funding_usdt_sum'])} | {_f(s['basis_usdt_sum'])} | {_f(s['cost_usdt_sum'])} | {_f(s['net_bps_n0_mean'])} | {_f(s['ret_capital_mean'] * 100, 3)}% | {_f(s['ret_capital_stress_mean'] * 100, 3)}% | {_f(s['ret_usd_mean'] * 100, 3)}% | {_f(s['cash_ret_mean'] * 100, 3)}% | {_f(s['excess_usd_mean'] * 100, 3)}% | {_f(s['excess_usd_stress_mean'] * 100, 3)}% | {_f(s['excess_annualized_mean'] * 100, 2)}% | {s['excess_positive_windows']} | {s['funding_positive_windows']} | {_f(s['funding_share_of_gross'], 3)} | {_f(s['net_ex_funding_usdt_sum'])} | {s['protection_exits']} | {_f(s['min_equity_ratio'], 3)} |"
        )
    o.append("")
    o.append(
        f"- Window-excess bootstrap (block 2, seed 42, indicative): {json.dumps(r['bootstrap_excess_mean'], sort_keys=True)}"
    )
    o.append(
        f"- Continuous-holding diagnostic (one ledger, actual turnover only): {json.dumps({k: (round(x, 6) if isinstance(x, float) else x) for k, x in r['continuous'].items()}, sort_keys=True)}"
    )
    o.append("")
    o.append("## Gates")
    o.append("")
    for k, ok in v["gates"].items():
        o.append(f"- {k}: {'PASS' if ok else 'FAIL'}")
    o.append(
        f"- unresolved windows: {v['unresolved_windows']}; best allocation: {v['best_allocation']}"
    )
    o.append("")
    o.append(
        "Limitations: scenario costs (12/8 bps, 20/12 stress) are not verified fees or fills; settlement marks are the containing 5m mark-bar open (no settlement mark in the archive); SOFR is an opportunity-cost benchmark only; USDT/USD conversion and withdrawal costs are unmodeled; historical exchange margin / liquidation rules are not reconstructed (the 25% rule is a frozen research limit); results are bar / mark based, not executable."
    )
    o.append("")
    return "\n".join(o)
