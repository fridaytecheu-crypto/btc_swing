"""Bybit / Binance BTCUSDT CONDITIONAL FUNDING CARRY — one bounded, frozen, bar/mark-based economic
feasibility screen (NOT execution or deployment qualification; mark-based results are never executable
profits).

Signal (00:05 UTC daily): A_v = sum of settled funding rates with settlement time strictly inside
(decision - 24h, decision) per venue; D = A_Bybit - A_Binance; projected_72h_bps = 1e4 * 3 * |D|;
conditional entry when projected >= 50 bps; D > 0 -> SHORT Bybit / LONG Binance, D < 0 mirror, D = 0 none.
Entry proxy = 00:10 UTC 5m MARK-bar OPEN on both venues; exit proxy = the mark-bar OPEN exactly 72h later;
q = 1 BTC per leg (accounting unit); one position at a time; renewal on an exit day closes first, then
opens (both charged). Funding cash flow F = sum over venues and settlements in (entry, exit] of
-s_v * q * M_v,t * r_v,t (settlement mark = mark-bar OPEN at the settlement timestamp, flagged as an
approximation; sensitivity = the preceding bar's close). Two-leg price PnL B = sum s_v * q * (P_T - P_0)
(this IS the basis PnL; nothing is added twice). Costs: four executions x q x proxy price x 8 bps (12 bps
stress). Net bps = 1e4 * (F + B - cost) / N0 with N0 = q * (P_Bybit,0 + P_Binance,0) / 2.
Explicit assumption: a settled rate is available within five minutes of its settlement (no historical
receipt timestamps exist). Inputs are the actual settled rates; nothing is reconstructed from current
metadata and no cap is applied retrospectively."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from btc_swing.research.intraday_portfolio import ORDER_CAPABLE, RESEARCH_ONLY
from btc_swing.research.intraday_portfolio.data import HOLDOUT_START
from btc_swing.research.intraday_portfolio.run import _f, _package_hashes, _write_table

MS_5M = 300_000
MS_1H = 3_600_000
MS_24H = 24 * MS_1H
MS_72H = 72 * MS_1H
DECISION_MIN = 5  # 00:05 UTC
ENTRY_MIN = 10  # 00:10 UTC
HOLD_MS = MS_72H
THRESHOLD_BPS = 50.0
Q_BTC = 1.0
COST_PRIMARY = 8e-4  # 8 bps all-in per execution
COST_STRESS = 12e-4
CLUSTER_GAP_MS = MS_72H
BOOT_N = 10_000
BOOT_SEED = 42
MOVING_BLOCK_DAYS = 30
VENUES = ("BYBIT", "BINANCE")


# ----------------------------------------------------------------------------- inputs
@dataclass
class VenueData:
    name: str
    fund_t: np.ndarray  # settlement ms (sorted)
    fund_r: np.ndarray
    interval_h: int  # funding interval implied by the settled grid
    mark_t: np.ndarray  # 5m bar open_time_ms (sorted)
    mark_open: np.ndarray
    mark_close: np.ndarray
    traded_open: np.ndarray | None = (
        None  # secondary valuation (traded 5m open), same grid as mark_t
    )
    index_open: np.ndarray | None = None  # diagnostic only
    files: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._mark_idx = {int(t): i for i, t in enumerate(self.mark_t.tolist())}

    def mark_at(self, t_ms: int) -> float | None:
        """OPEN of the 5m bar containing t. Settlement timestamps carry millisecond jitter on Binance
        (e.g. 00:00:00.012), so the lookup floors t to the 5m grid; entry / exit times are on the grid."""
        i = self._mark_idx.get(int(t_ms) // MS_5M * MS_5M)
        return float(self.mark_open[i]) if i is not None else None

    def prev_close_at(self, t_ms: int) -> float | None:
        """CLOSE of the bar preceding the one containing t (settlement-mark sensitivity)."""
        i = self._mark_idx.get(int(t_ms) // MS_5M * MS_5M - MS_5M)
        return float(self.mark_close[i]) if i is not None else None

    def traded_at(self, t_ms: int) -> float | None:
        if self.traded_open is None:
            return None
        i = self._mark_idx.get(int(t_ms))
        v = float(self.traded_open[i]) if i is not None else math.nan
        return v if math.isfinite(v) else None

    def settlements_in(self, t0_ms: int, t1_ms: int, closed_left: bool) -> list[tuple[int, float]]:
        """Settlements with t in (t0, t1] (closed_left False) or [t0, t1] (True)."""
        lo = int(np.searchsorted(self.fund_t, t0_ms, side="left" if closed_left else "right"))
        hi = int(np.searchsorted(self.fund_t, t1_ms, side="right"))
        return [(int(self.fund_t[k]), float(self.fund_r[k])) for k in range(lo, hi)]


def _files(root: Path, cap: date, by_month: bool) -> list[Path]:
    key = cap.isoformat()[:7] if by_month else cap.isoformat()
    return sorted(p for p in root.glob("*.parquet") if p.stem < key)


def _fingerprint(files: list[Path]) -> dict[str, Any]:
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode())
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return {
        "files": len(files),
        "first": files[0].stem if files else None,
        "last": files[-1].stem if files else None,
        "sha256_of_file_hashes": h.hexdigest() if files else None,
    }


def _grid(df: pl.DataFrame, tcol: str, cols: list[str]) -> tuple[np.ndarray, list[np.ndarray]]:
    df = df.sort(tcol).unique(subset=[tcol], keep="first", maintain_order=True)
    return df[tcol].to_numpy().astype(np.int64), [df[c].to_numpy().astype(np.float64) for c in cols]


def load_venues(data_dir: Path) -> tuple[dict[str, VenueData], dict[str, Any]]:
    ds = data_dir / "btc" / "datasets"
    inv: dict[str, Any] = {}
    spec: dict[str, dict[str, Any]] = {
        "BYBIT": {
            "fund": ds / "bybit_funding" / "BTCUSDT" / "na",
            "fund_month": True,
            "mark": ds / "bybit_mark_5m" / "BTCUSDT" / "5m",
            "index": ds / "bybit_index_5m" / "BTCUSDT" / "5m",
            "traded": ds / "bybit_trades_5m" / "BTCUSDT" / "5m",
            "traded_month": False,
        },
        "BINANCE": {
            "fund": ds / "funding" / "BTCUSDT" / "na",
            "fund_month": True,
            "mark": ds / "mark_price" / "BTCUSDT" / "5m",
            "index": ds / "index_klines" / "BTCUSDT" / "5m",
            "traded": ds / "perp_klines" / "BTCUSDT" / "5m",
            "traded_month": True,
        },
    }
    out: dict[str, VenueData] = {}
    missing: list[str] = []
    for name, s in spec.items():
        ff = _files(s["fund"], HOLDOUT_START, True)
        mf = _files(s["mark"], HOLDOUT_START, True)
        inv[name] = {"funding": _fingerprint(ff), "mark_5m": _fingerprint(mf)}
        if not ff or not mf:
            missing += [f"{name}:{k}" for k, v in (("funding", ff), ("mark_5m", mf)) if not v]
            continue
        fdf = pl.concat(
            [pl.read_parquet(f, columns=["time_ms", "funding_rate"]) for f in ff]
        ).drop_nulls()
        ft, (fr,) = _grid(fdf, "time_ms", ["funding_rate"])
        mdf = pl.concat([pl.read_parquet(f, columns=["open_time_ms", "open", "close"]) for f in mf])
        mt, (mo, mc) = _grid(mdf, "open_time_ms", ["open", "close"])
        gaps = np.diff(ft)
        interval_h = round(float(np.median(gaps)) / MS_1H) if len(gaps) else 8
        v = VenueData(name, ft, fr, interval_h, mt, mo, mc)
        v.files = {
            "funding": [ff[0].name, "...", ff[-1].name],
            "mark": [mf[0].name, "...", mf[-1].name],
        }
        tf = _files(s["traded"], HOLDOUT_START, s["traded_month"])
        if tf:
            tdf = pl.concat([pl.read_parquet(f, columns=["open_time_ms", "open"]) for f in tf])
            tt, (to,) = _grid(tdf, "open_time_ms", ["open"])
            pos = {int(t): i for i, t in enumerate(tt.tolist())}
            v.traded_open = np.array(
                [to[pos[int(t)]] if int(t) in pos else math.nan for t in mt.tolist()]
            )
        inf = _files(s["index"], HOLDOUT_START, True)
        if inf:
            idf = pl.concat([pl.read_parquet(f, columns=["open_time_ms", "open"]) for f in inf])
            it, (io,) = _grid(idf, "open_time_ms", ["open"])
            pos = {int(t): i for i, t in enumerate(it.tolist())}
            v.index_open = np.array(
                [io[pos[int(t)]] if int(t) in pos else math.nan for t in mt.tolist()]
            )
        inv[name].update(
            {
                "funding_settlements": len(ft),
                "funding_first_utc": datetime.fromtimestamp(int(ft[0]) / 1000, tz=UTC).isoformat(),
                "funding_last_utc": datetime.fromtimestamp(int(ft[-1]) / 1000, tz=UTC).isoformat(),
                "funding_interval_hours_observed": interval_h,
                "funding_non_grid_gaps": int(np.sum(gaps != interval_h * MS_1H)),
                "mark_rows": len(mt),
                "mark_gaps_gt_5m": int(np.sum(np.diff(mt) != MS_5M)),
                "mark_first_utc": datetime.fromtimestamp(int(mt[0]) / 1000, tz=UTC).isoformat(),
                "mark_last_utc": datetime.fromtimestamp(int(mt[-1]) / 1000, tz=UTC).isoformat(),
                "bar_timestamp_semantics": "open_time_ms = interval START (checked: all on the 5m grid)",
                "all_on_5m_grid": bool(np.all(mt % MS_5M == 0)),
                "traded_5m_available": tf != [],
                "index_5m_available": inf != [],
                "settlement_mark": "mark-bar OPEN at the settlement timestamp (APPROXIMATION; exact settlement marks absent)",
            }
        )
        out[name] = v
    inv["missing"] = missing
    return out, inv


# ----------------------------------------------------------------------------- signal
@dataclass
class Decision:
    day: str
    t_ms: int  # 00:05 UTC
    a_bybit: float | None
    a_binance: float | None
    d: float | None
    projected_72h_bps: float | None
    qualified: bool
    direction: int  # +1 = SHORT Bybit / LONG Binance (D > 0), -1 mirror, 0 none
    valid: bool
    exclusion: str | None = None


def signal(day: date, venues: dict[str, VenueData]) -> Decision:
    t = (
        int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
        + DECISION_MIN * 60_000
    )
    acc: dict[str, float] = {}
    for name in VENUES:
        v = venues[name]
        # strictly inside (t - 24h, t): searchsorted right on t-24h excludes the left edge, left on t excludes t
        lo = int(np.searchsorted(v.fund_t, t - MS_24H, side="right"))
        hi = int(np.searchsorted(v.fund_t, t, side="left"))
        expected = 24 // v.interval_h
        if hi - lo != expected:
            return Decision(
                day.isoformat(),
                t,
                None,
                None,
                None,
                None,
                False,
                0,
                False,
                f"INCOMPLETE_TRAILING_24H_FUNDING_{name}:{hi - lo}_of_{expected}",
            )
        acc[name] = float(np.sum(v.fund_r[lo:hi]))
    d = acc["BYBIT"] - acc["BINANCE"]
    proj = 1e4 * 3.0 * abs(d)
    direction = 1 if d > 0 else (-1 if d < 0 else 0)
    return Decision(
        day.isoformat(),
        t,
        acc["BYBIT"],
        acc["BINANCE"],
        d,
        proj,
        proj >= THRESHOLD_BPS and direction != 0,
        direction,
        True,
    )


# ----------------------------------------------------------------------------- position accounting
@dataclass
class Position:
    position_id: str
    strategy: str
    entry_ms: int
    exit_ms: int
    direction: int  # +1 SHORT Bybit / LONG Binance
    s_bybit: int
    s_binance: int
    renewal: bool
    decision_day: str
    projected_72h_bps: float | None
    d_signal: float | None
    p0: dict[str, float]
    p_t: dict[str, float]
    n0: float
    funding: dict[str, float]
    funding_count: dict[str, int]
    funding_h0_24: float
    funding_h24_48: float
    funding_h48_72: float
    funding_prev_close_sens: float
    price_pnl: dict[str, float]
    f_total: float
    b_total: float
    gross: float
    cost_primary: float
    cost_stress: float
    net_primary: float
    net_stress: float
    net_bps: float
    net_bps_stress: float
    gross_bps: float
    funding_bps: float
    basis_bps: float
    cost_bps: float
    basis_mae: float
    basis_dd: float
    total_dd: float
    adverse_equity: dict[str, float]
    min_equity: dict[str, float]
    max_notional_over_equity: dict[str, float]
    secondary_net_bps: float | None
    cluster_id: int = -1
    block: int = 0
    year: int = 0
    quarter: str = ""

    def as_row(self) -> dict[str, Any]:
        r: dict[str, Any] = {}
        for k, v in self.__dict__.items():
            if isinstance(v, dict):
                for kk, vv in v.items():
                    r[f"{k}_{kk.lower()}"] = vv
            else:
                r[k] = v
        return r


def _window_ok(v: VenueData, t0: int, t1: int) -> bool:
    i0, i1 = v._mark_idx.get(t0), v._mark_idx.get(t1)
    return i0 is not None and i1 is not None and (i1 - i0) == (t1 - t0) // MS_5M


def build_position(
    pid: str,
    strategy: str,
    entry_ms: int,
    direction: int,
    renewal: bool,
    dec: Decision,
    venues: dict[str, VenueData],
    cost_entry_rate: float = COST_PRIMARY,
    charge: tuple[bool, bool] = (True, True),
) -> Position | str:
    """One 72h position; returns an exclusion string when the forward valuation window is incomplete.
    `charge` = (charge the two entry executions, charge the two exit executions) for the B-roll control."""
    exit_ms = entry_ms + HOLD_MS
    for v in venues.values():
        if not _window_ok(v, entry_ms, exit_ms):
            return f"INCOMPLETE_FORWARD_72H_WINDOW_{v.name}"
    s = {"BYBIT": -direction, "BINANCE": direction}
    p0 = {n: venues[n].mark_at(entry_ms) or math.nan for n in VENUES}
    p_t = {n: venues[n].mark_at(exit_ms) or math.nan for n in VENUES}
    n0 = Q_BTC * (p0["BYBIT"] + p0["BINANCE"]) / 2
    fund: dict[str, float] = {}
    cnt: dict[str, int] = {}
    fh = [0.0, 0.0, 0.0]
    sens = 0.0
    series: dict[str, list[tuple[int, float]]] = {}
    for n in VENUES:
        v = venues[n]
        tot = 0.0
        rows = v.settlements_in(
            entry_ms, exit_ms, closed_left=False
        )  # (entry, exit]: pre-entry midnight excluded
        series[n] = []
        for t, r in rows:
            m = v.mark_at(t)
            if m is None:
                return f"SETTLEMENT_MARK_MISSING_{n}"
            cf = -s[n] * Q_BTC * m * r
            tot += cf
            series[n].append((t, cf))
            fh[min(2, (t - entry_ms - 1) // MS_24H)] += cf
            pc = v.prev_close_at(t)
            sens += -s[n] * Q_BTC * (pc if pc is not None else m) * r
        fund[n], cnt[n] = tot, len(rows)
    price = {n: s[n] * Q_BTC * (p_t[n] - p0[n]) for n in VENUES}
    f_total, b_total = sum(fund.values()), sum(price.values())
    execs = [p0["BYBIT"], p0["BINANCE"]] * charge[0] + [p_t["BYBIT"], p_t["BINANCE"]] * charge[1]
    cost_p = sum(Q_BTC * px * cost_entry_rate for px in execs)
    cost_s = sum(Q_BTC * px * COST_STRESS for px in execs)
    net_p, net_s = f_total + b_total - cost_p, f_total + b_total - cost_s
    # path on the synchronized 5m grid (bar OPENs; no intrabar highs/lows)
    i0 = venues["BYBIT"]._mark_idx[entry_ms]
    n_bars = (exit_ms - entry_ms) // MS_5M + 1
    tt = venues["BYBIT"].mark_t[i0 : i0 + n_bars]
    j0 = venues["BINANCE"]._mark_idx[entry_ms]
    legs = {
        "BYBIT": s["BYBIT"] * Q_BTC * (venues["BYBIT"].mark_open[i0 : i0 + n_bars] - p0["BYBIT"]),
        "BINANCE": s["BINANCE"]
        * Q_BTC
        * (venues["BINANCE"].mark_open[j0 : j0 + n_bars] - p0["BINANCE"]),
    }
    basis_path = legs["BYBIT"] + legs["BINANCE"]
    fund_cum = {n: np.zeros(n_bars) for n in VENUES}
    for n in VENUES:
        for t, cf in series[n]:
            fund_cum[n][tt >= t] += cf  # a settlement is earned once its timestamp is reached
    entry_cost = (
        sum(Q_BTC * px * cost_entry_rate for px in execs[: 2 * charge[0]]) if charge[0] else 0.0
    )
    total_path = basis_path + fund_cum["BYBIT"] + fund_cum["BINANCE"] - entry_cost
    total_path[-1] = net_p

    def dd(x: np.ndarray) -> float:
        peak = np.maximum.accumulate(np.r_[0.0, x])
        return float(np.min(np.r_[0.0, x] - peak))

    adverse = {n: float(np.min(legs[n] + fund_cum[n])) for n in VENUES}
    coll = {n: Q_BTC * p0[n] for n in VENUES}
    eq = {n: coll[n] + legs[n] + fund_cum[n] for n in VENUES}
    min_eq = {n: float(np.min(eq[n])) for n in VENUES}
    mark_path = {
        "BYBIT": venues["BYBIT"].mark_open[i0 : i0 + n_bars],
        "BINANCE": venues["BINANCE"].mark_open[j0 : j0 + n_bars],
    }
    ratio = {
        n: float(np.max(Q_BTC * mark_path[n] / np.where(eq[n] > 0, eq[n], np.nan)))
        if np.any(eq[n] > 0)
        else math.inf
        for n in VENUES
    }
    sec = None
    t0b, t0n, t_tb, t_tn = (
        venues["BYBIT"].traded_at(entry_ms),
        venues["BINANCE"].traded_at(entry_ms),
        venues["BYBIT"].traded_at(exit_ms),
        venues["BINANCE"].traded_at(exit_ms),
    )
    if None not in (t0b, t0n, t_tb, t_tn):
        assert t0b is not None and t0n is not None and t_tb is not None and t_tn is not None
        b2 = s["BYBIT"] * Q_BTC * (t_tb - t0b) + s["BINANCE"] * Q_BTC * (t_tn - t0n)
        ex2 = [t0b, t0n] * charge[0] + [t_tb, t_tn] * charge[1]
        c2 = sum(Q_BTC * px * cost_entry_rate for px in ex2)
        sec = 1e4 * (f_total + b2 - c2) / (Q_BTC * (t0b + t0n) / 2)
    yr = datetime.fromtimestamp(entry_ms / 1000, tz=UTC)
    return Position(
        pid,
        strategy,
        entry_ms,
        exit_ms,
        direction,
        s["BYBIT"],
        s["BINANCE"],
        renewal,
        dec.day,
        dec.projected_72h_bps,
        dec.d,
        p0,
        p_t,
        n0,
        fund,
        cnt,
        fh[0],
        fh[1],
        fh[2],
        sens,
        price,
        f_total,
        b_total,
        f_total + b_total,
        cost_p,
        cost_s,
        net_p,
        net_s,
        1e4 * net_p / n0,
        1e4 * net_s / n0,
        1e4 * (f_total + b_total) / n0,
        1e4 * f_total / n0,
        1e4 * b_total / n0,
        1e4 * cost_p / n0,
        float(np.min(basis_path)),
        dd(basis_path),
        dd(total_path),
        adverse,
        min_eq,
        ratio,
        sec,
        year=yr.year,
        quarter=f"{yr.year}Q{(yr.month - 1) // 3 + 1}",
    )


# ----------------------------------------------------------------------------- strategies
@dataclass
class RunLog:
    decisions: list[Decision] = field(default_factory=list)
    positions: list[Position] = field(default_factory=list)
    suppressed_by_active: int = 0
    excluded: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def _entry_ms(dec: Decision) -> int:
    return dec.t_ms - DECISION_MIN * 60_000 + ENTRY_MIN * 60_000


def run_conditional(days: list[date], venues: dict[str, VenueData]) -> RunLog:
    log = RunLog()
    open_exit: int | None = None
    k = 0
    for day in days:
        dec = signal(day, venues)
        log.decisions.append(dec)
        if not dec.valid:
            log.excluded[dec.exclusion or "INVALID"] += 1
            continue
        if not dec.qualified:
            continue
        ent = _entry_ms(dec)
        if open_exit is not None and ent < open_exit:
            log.suppressed_by_active += 1
            continue
        renewal = open_exit is not None and ent == open_exit  # close the old one first, then open
        k += 1
        res = build_position(f"A-{k:04d}", "A", ent, dec.direction, renewal, dec, venues)
        if isinstance(res, str):
            log.excluded[res] += 1
            continue
        log.positions.append(res)
        open_exit = res.exit_ms
    return log


def run_control_b(days: list[date], venues: dict[str, VenueData], roll: bool) -> RunLog:
    """Threshold-off 72h control: first entry on the first valid decision, renew every 72h with the
    trailing-24h direction (D = 0 -> flat for that block). `roll` keeps unchanged exposure without
    artificial close/reopen costs and charges turnover only on direction changes / closes."""
    log = RunLog()
    by_day = {d.isoformat(): d for d in days}
    cur: date | None = None
    prev_dir = 0
    k = 0
    for day in days:
        if cur is None:
            dec = signal(day, venues)
            log.decisions.append(dec)
            if not dec.valid:
                log.excluded[dec.exclusion or "INVALID"] += 1
                continue
            cur = day
        break
    while cur is not None and cur.isoformat() in by_day:
        dec = signal(cur, venues)
        if cur != days[0]:
            log.decisions.append(dec)
        nxt = cur + timedelta(days=3)
        if not dec.valid:
            log.excluded[dec.exclusion or "INVALID"] += 1
            prev_dir = 0
            cur = nxt
            continue
        ent = _entry_ms(dec)
        if dec.direction == 0:
            prev_dir = 0
            cur = nxt
            continue
        k += 1
        if roll:
            charge = (
                prev_dir != dec.direction,
                True,
            )  # exit leg charged provisionally, corrected below
        else:
            charge = (True, True)
        res = build_position(
            f"{'BROLL' if roll else 'B'}-{k:04d}",
            "B_ROLL" if roll else "B",
            ent,
            dec.direction,
            prev_dir != 0,
            dec,
            venues,
            charge=charge,
        )
        if isinstance(res, str):
            log.excluded[res] += 1
            prev_dir = 0
            cur = nxt
            continue
        log.positions.append(res)
        prev_dir = dec.direction
        cur = nxt
    if (
        roll
    ):  # the exit executions of a block are only real when the next block does not continue it
        for a, b in zip(log.positions, log.positions[1:], strict=False):
            if b.entry_ms == a.exit_ms and b.direction == a.direction:
                _uncharge_exit(a)
    return log


def _uncharge_exit(p: Position) -> None:
    exit_cost_p = sum(Q_BTC * px * COST_PRIMARY for px in (p.p_t["BYBIT"], p.p_t["BINANCE"]))
    exit_cost_s = sum(Q_BTC * px * COST_STRESS for px in (p.p_t["BYBIT"], p.p_t["BINANCE"]))
    p.cost_primary -= exit_cost_p
    p.cost_stress -= exit_cost_s
    p.net_primary += exit_cost_p
    p.net_stress += exit_cost_s
    p.net_bps = 1e4 * p.net_primary / p.n0
    p.net_bps_stress = 1e4 * p.net_stress / p.n0
    p.cost_bps = 1e4 * p.cost_primary / p.n0
    if p.secondary_net_bps is not None:
        p.secondary_net_bps += 1e4 * exit_cost_p / p.n0


# ----------------------------------------------------------------------------- statistics
def assign_clusters(ps: list[Position]) -> int:
    cid = 0
    prev: Position | None = None
    for p in ps:
        if prev is None or p.entry_ms - prev.exit_ms > CLUSTER_GAP_MS:
            cid += 1
        p.cluster_id = cid
        prev = p
    return cid


def net_pf(vals: list[float]) -> float | None:
    pos = sum(v for v in vals if v > 0)
    neg = -sum(v for v in vals if v < 0)
    if not vals:
        return None
    if neg == 0:
        return math.inf if pos > 0 else 0.0
    return pos / neg


def _dd_bps(vals: list[float]) -> float:
    c = np.cumsum(np.r_[0.0, np.asarray(vals)])
    return float(np.min(c - np.maximum.accumulate(c)))


def summarize(ps: list[Position], attr: str = "net_bps") -> dict[str, Any]:
    v = [getattr(p, attr) for p in ps]
    return {
        "count": len(ps),
        "clusters": len({p.cluster_id for p in ps}),
        "funding_bps_mean": statistics.fmean([p.funding_bps for p in ps]) if ps else None,
        "basis_bps_mean": statistics.fmean([p.basis_bps for p in ps]) if ps else None,
        "cost_bps_mean": statistics.fmean([p.cost_bps for p in ps]) if ps else None,
        "gross_bps_mean": statistics.fmean([p.gross_bps for p in ps]) if ps else None,
        "net_bps_mean": statistics.fmean(v) if v else None,
        "net_bps_median": statistics.median(v) if v else None,
        "net_pf": net_pf(v),
        "win_rate": sum(1 for x in v if x > 0) / len(v) if v else None,
        "drawdown_bps_cum": _dd_bps(v) if v else None,
        "net_usdt_per_btc": sum(
            getattr(p, "net_primary" if attr == "net_bps" else "net_stress") for p in ps
        ),
    }


def cluster_bootstrap(ps: list[Position], seed: int = BOOT_SEED, n: int = BOOT_N) -> dict[str, Any]:
    by: dict[int, list[float]] = defaultdict(list)
    for p in ps:
        by[p.cluster_id].append(p.net_bps)
    cl = list(by.values())
    if len(cl) < 2:
        return {"status": "INSUFFICIENT_CLUSTERS", "clusters": len(cl)}
    rng = np.random.default_rng(seed)
    means = np.empty(n)
    for i in range(n):
        pick = rng.integers(0, len(cl), len(cl))
        vals = [x for j in pick for x in cl[j]]
        means[i] = float(np.mean(vals))
    return {
        "status": "OK",
        "clusters": len(cl),
        "resamples": n,
        "seed": seed,
        "mean": float(np.mean([p.net_bps for p in ps])),
        "ci95_low": float(np.quantile(means, 0.025)),
        "ci95_high": float(np.quantile(means, 0.975)),
    }


def moving_block_bootstrap(
    ps: list[Position],
    first_day: date,
    last_day: date,
    seed: int = BOOT_SEED,
    n: int = BOOT_N,
    block_days: int = MOVING_BLOCK_DAYS,
) -> dict[str, Any]:
    n_days = (last_day - first_day).days + 1
    sum_d = np.zeros(n_days)
    cnt_d = np.zeros(n_days)
    for p in ps:
        k = (datetime.fromtimestamp(p.entry_ms / 1000, tz=UTC).date() - first_day).days
        sum_d[k] += p.net_bps
        cnt_d[k] += 1
    if n_days < block_days or cnt_d.sum() < 2:
        return {"status": "INSUFFICIENT_DAYS", "days": n_days}
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(n_days / block_days)
    starts_max = n_days - block_days
    out = np.empty(n)
    for i in range(n):
        st = rng.integers(0, starts_max + 1, n_blocks)
        s = sum(sum_d[a : a + block_days].sum() for a in st)
        c = sum(cnt_d[a : a + block_days].sum() for a in st)
        out[i] = s / c if c > 0 else np.nan
    out = out[np.isfinite(out)]
    return {
        "status": "OK",
        "days": n_days,
        "block_days": block_days,
        "resamples": len(out),
        "seed": seed,
        "ci95_low": float(np.quantile(out, 0.025)),
        "ci95_high": float(np.quantile(out, 0.975)),
    }


def calendar_metrics(
    ps: list[Position], first_day: date, last_day: date, label: str
) -> dict[str, Any]:
    n_days = (last_day - first_day).days + 1
    occupied = sum(p.exit_ms - p.entry_ms for p in ps) / (n_days * MS_24H) if n_days else None
    turnover = sum(
        (p.cost_primary / COST_PRIMARY) for p in ps
    )  # executed notional (USDT per q=1 BTC)
    return {
        "strategy": label,
        "positions": len(ps),
        "net_usdt_per_btc_primary": sum(p.net_primary for p in ps),
        "net_usdt_per_btc_stress": sum(p.net_stress for p in ps),
        "funding_usdt": sum(p.f_total for p in ps),
        "basis_usdt": sum(p.b_total for p in ps),
        "cost_usdt_primary": sum(p.cost_primary for p in ps),
        "occupancy_share": occupied,
        "turnover_notional_usdt": turnover,
        "mean_initial_collateral_usdt_both_venues": statistics.fmean(
            [p.p0["BYBIT"] + p.p0["BINANCE"] for p in ps]
        )
        * Q_BTC
        if ps
        else None,
        "min_equity_bybit": min((p.min_equity["BYBIT"] for p in ps), default=None),
        "min_equity_binance": min((p.min_equity["BINANCE"] for p in ps), default=None),
        "max_notional_over_equity": max(
            (max(p.max_notional_over_equity.values()) for p in ps), default=None
        ),
        "positions_per_calendar_day": len(ps) / n_days if n_days else None,
    }


def verdict(
    a: list[Position],
    blocks: dict[int, list[Position]],
    months: float,
    cb: dict[str, Any],
    mb: dict[str, Any],
    excl_cluster: dict[str, Any],
    excl_quarter: dict[str, Any],
    sens_gap_bps: float | None,
    sec_gap_bps: float | None,
) -> dict[str, Any]:
    n = len(a)
    s = summarize(a)
    s_stress = summarize(a, "net_bps_stress")
    clusters = len({p.cluster_id for p in a})
    gates = {
        "positions_ge_100": n >= 100,
        "clusters_ge_30": clusters >= 30,
        "months_ge_24": months >= 24,
        "mean_net_ge_9bps_primary": (s["net_bps_mean"] or -1) >= 9.0,
        "net_pf_ge_1_2": (s["net_pf"] or 0) >= 1.2,
        "mean_net_stress_gt_0": (s_stress["net_bps_mean"] or -1) > 0,
        "cluster_bootstrap_lb_gt_0": cb.get("ci95_low", -1) > 0,
        "moving_block_lb_gt_0": mb.get("ci95_low", -1) > 0,
        "blocks_positive_ge_3_incl_final": sum(
            1 for b in blocks.values() if b and (summarize(b)["net_bps_mean"] or 0) > 0
        )
        >= 3
        and bool(blocks.get(4))
        and (summarize(blocks[4])["net_bps_mean"] or 0) > 0,
        "positions_per_block_ge_20": all(len(b) >= 20 for b in blocks.values()),
        "positive_excluding_best_cluster": (excl_cluster.get("net_bps_mean") or 0) > 0,
        "positive_excluding_best_quarter": (excl_quarter.get("net_bps_mean") or 0) > 0,
        "no_material_valuation_discrepancy": (sens_gap_bps is None or abs(sens_gap_bps) < 2.0)
        and (sec_gap_bps is None or abs(sec_gap_bps) < 5.0),
    }
    informative = n >= 30 and clusters >= 10 and months >= 6
    if n < 10:
        v = "PARK — INSUFFICIENT EVENTS"
    elif all(gates.values()):
        v = "ELIGIBLE FOR EXECUTION-QUALIFIED REPLAY"
    elif informative and (s["net_bps_mean"] or 0) <= 0:
        v = "REJECT — ECONOMIC SCREEN"
    else:
        v = "INCONCLUSIVE"
    ci_rules_out = cb.get("ci95_high", math.inf) < 0 if cb.get("status") == "OK" else None
    fund_only = (s["funding_bps_mean"] or 0) <= 0 < (s["basis_bps_mean"] or 0) and (
        s["net_bps_mean"] or 0
    ) > 0
    return {
        "verdict": v,
        "informative_sample": informative,
        "gates": gates,
        "n": n,
        "clusters": clusters,
        "months": months,
        "ci_rules_out_positive_expectancy": ci_rules_out,
        "profit_entirely_from_basis_carry_not_established": bool(fund_only),
        "note": "a pass would authorize executable-data investigation only, never deployment; a failure closes this specification",
    }


# ----------------------------------------------------------------------------- run
def run_carry(data_dir: Path, out_root: Path, run_id: str | None = None) -> dict[str, Any]:
    assert RESEARCH_ONLY and not ORDER_CAPABLE
    venues, inv = load_venues(data_dir)
    if inv["missing"]:
        raise FileNotFoundError(
            f"carry inputs missing on this host: {inv['missing']} (run the Mac export / copy the datasets; nothing is fabricated)"
        )
    run_id = run_id or datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out = out_root / run_id
    out.mkdir(parents=True, exist_ok=True)
    # ---- common usable coverage and the frozen manifest (before any PnL)
    lo = max(int(v.mark_t[0]) for v in venues.values()) + MS_24H
    hi = min(int(v.mark_t[-1]) for v in venues.values()) - HOLD_MS
    d0 = datetime.fromtimestamp(lo / 1000, tz=UTC).date() + timedelta(days=1)
    d1 = datetime.fromtimestamp(hi / 1000, tz=UTC).date()
    days = [d0 + timedelta(days=k) for k in range((d1 - d0).days + 1)]
    valid_days = [d for d in days if signal(d, venues).valid]
    first_valid, last_valid = valid_days[0], valid_days[-1]
    span = (last_valid - first_valid).days + 1
    edges = [first_valid + timedelta(days=round(k * span / 4)) for k in range(1, 4)]
    blocks_def = {
        1: [first_valid.isoformat(), edges[0].isoformat()],
        2: [edges[0].isoformat(), edges[1].isoformat()],
        3: [edges[1].isoformat(), edges[2].isoformat()],
        4: [edges[2].isoformat(), (last_valid + timedelta(days=1)).isoformat()],
    }
    prereg = {
        "run_id": run_id,
        "status": "FROZEN_BEFORE_ANY_PNL",
        "inputs": inv,
        "common_usable_coverage": {
            "first_decision_day": days[0].isoformat(),
            "last_decision_day": days[-1].isoformat(),
            "first_valid_decision": first_valid.isoformat(),
            "last_valid_decision": last_valid.isoformat(),
            "valid_daily_decisions": len(valid_days),
            "invalid_days": len(days) - len(valid_days),
        },
        "blocks_equal_calendar_duration_from_valid_entry_dates": blocks_def,
        "rules": {
            "decision": "00:05 UTC daily; A_v = sum of settled rates with settlement strictly inside (t-24h, t); D = A_Bybit - A_Binance; projected_72h_bps = 1e4*3*|D|; entry iff >= 50 bps; D>0 SHORT Bybit / LONG Binance, D<0 mirror, D=0 none; trailing window must hold the full expected settlement count per venue (24h / observed interval) else the decision is excluded",
            "entry_exit": "00:10 UTC mark-bar OPEN both venues; exit mark-bar OPEN exactly 72h later; complete synchronized 5m grid required on both venues else excluded; q = 1 BTC per leg; one position; renewal closes first then opens (both charged, flagged)",
            "funding": "F = sum -s_v q M_v,t r_v,t over settlements in (entry, exit]; pre-entry midnight excluded, final midnight included; settlement mark = OPEN of the 5m mark bar containing the settlement timestamp (Binance settlement stamps carry ms jitter; approximation; sensitivity = preceding bar close)",
            "price": "B = sum s_v q (P_T - P_0) = the combined basis PnL (not added twice); N0 = q (P_Bybit,0 + P_Binance,0)/2 fixed",
            "costs": "four executions x q x proxy price x 8 bps (primary) / 12 bps (stress); modeled all-in, no breakdown, no extra fees",
            "valuation": "synchronized marks both venues (primary); traded 5m opens both venues (secondary, same signals / holds); index diagnostic only; path metrics on 5m bar opens, never intrabar extremes; illustrative collateral = leg entry notional per venue, no netting; liquidation / margin compliance UNQUALIFIED",
            "controls": "B threshold-off 72h schedule from the first valid decision (direction = trailing sign, D=0 -> flat block, full close/reopen costs); B-roll keeps unchanged exposure and charges only turnover; C flat",
            "dependence": "A clusters: gap previous exit -> next entry <= 72h joins the cluster regardless of direction",
            "bootstrap": {
                "seed": BOOT_SEED,
                "resamples": BOOT_N,
                "primary": "whole-cluster",
                "robustness": "30-calendar-day moving block over entry-date outcomes incl. zero-entry days",
            },
            "assumption": "settled funding available within five minutes of settlement (no historical receipt timestamps)",
            "verdicts": "PARK < 10 positions; INFORMATIVE >= 30 positions, >= 10 clusters, >= 6 months; REJECT if informative and primary mean net <= 0; ELIGIBLE only if all promotion gates hold; else INCONCLUSIVE",
        },
        "package_sha256": _package_hashes(),
    }
    prereg_sha = hashlib.sha256(
        json.dumps(prereg, sort_keys=True, default=str).encode()
    ).hexdigest()
    man_path = out / "carry_manifest.json"
    man_path.write_text(
        json.dumps(
            {"preregistration": prereg, "preregistration_sha256": prereg_sha},
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n"
    )

    # ---- strategies
    a = run_conditional(days, venues)
    b = run_control_b(days, venues, roll=False)
    broll = run_control_b(days, venues, roll=True)
    n_clusters = assign_clusters(a.positions)
    for log in (b, broll):
        assign_clusters(log.positions)

    def blk(p: Position) -> int:
        d = datetime.fromtimestamp(p.entry_ms / 1000, tz=UTC).date()
        return 1 + sum(1 for e in edges if d >= e)

    for log in (a, b, broll):
        for p in log.positions:
            p.block = blk(p)
    pos_a = a.positions
    blocks = {k: [p for p in pos_a if p.block == k] for k in (1, 2, 3, 4)}
    months = span / 30.44
    cb = cluster_bootstrap(pos_a)
    mb = moving_block_bootstrap(pos_a, first_valid, last_valid)
    by_cluster: dict[int, float] = defaultdict(float)
    by_quarter: dict[str, float] = defaultdict(float)
    for p in pos_a:
        by_cluster[p.cluster_id] += p.net_bps
        by_quarter[p.quarter] += p.net_bps
    best_cluster = max(by_cluster, key=lambda k: by_cluster[k]) if by_cluster else None
    best_quarter = max(by_quarter, key=lambda k: by_quarter[k]) if by_quarter else None
    excl_c = summarize([p for p in pos_a if p.cluster_id != best_cluster])
    excl_q = summarize([p for p in pos_a if p.quarter != best_quarter])
    sens_gap = (
        statistics.fmean([1e4 * (p.funding_prev_close_sens - p.f_total) / p.n0 for p in pos_a])
        if pos_a
        else None
    )
    secs = [p.secondary_net_bps for p in pos_a if p.secondary_net_bps is not None]
    sec_gap = (
        (
            statistics.fmean(secs)
            - statistics.fmean([p.net_bps for p in pos_a if p.secondary_net_bps is not None])
        )
        if secs
        else None
    )
    ver = verdict(pos_a, blocks, months, cb, mb, excl_c, excl_q, sens_gap, sec_gap)

    # ---- tables
    files: list[str] = ["carry_manifest.json"]
    files += _write_table(
        [p.as_row() for log in (a, b, broll) for p in log.positions], out / "carry_positions"
    )
    rows_blocks = [
        {"block": k, "from": blocks_def[k][0], "to_excl": blocks_def[k][1], **summarize(v)}
        for k, v in blocks.items()
    ]
    rows_blocks += [
        {"block": f"year_{y}", **summarize([p for p in pos_a if p.year == y])}
        for y in sorted({p.year for p in pos_a})
    ]
    rows_blocks += [
        {
            "block": f"orientation_{'SHORT_BYBIT' if d > 0 else 'SHORT_BINANCE'}",
            **summarize([p for p in pos_a if p.direction == d]),
        }
        for d in (1, -1)
    ]
    files += _write_table(rows_blocks, out / "carry_blocks", parquet=False)
    conc = [
        {
            "item": "largest_winning_position",
            "id": max(pos_a, key=lambda p: p.net_bps).position_id if pos_a else None,
            "net_bps": max((p.net_bps for p in pos_a), default=None),
        },
        {
            "item": "best_cluster",
            "id": best_cluster,
            "net_bps_sum": by_cluster.get(best_cluster) if best_cluster is not None else None,
        },
        {
            "item": "best_calendar_quarter",
            "id": best_quarter,
            "net_bps_sum": by_quarter.get(best_quarter) if best_quarter is not None else None,
        },
        {"item": "pooled_excluding_best_cluster", **excl_c},
        {"item": "pooled_excluding_best_quarter", **excl_q},
        {
            "item": "funding_bps_hours_0_24",
            "value": statistics.fmean([1e4 * p.funding_h0_24 / p.n0 for p in pos_a])
            if pos_a
            else None,
        },
        {
            "item": "funding_bps_hours_24_48",
            "value": statistics.fmean([1e4 * p.funding_h24_48 / p.n0 for p in pos_a])
            if pos_a
            else None,
        },
        {
            "item": "funding_bps_hours_48_72",
            "value": statistics.fmean([1e4 * p.funding_h48_72 / p.n0 for p in pos_a])
            if pos_a
            else None,
        },
        {"item": "settlement_mark_sensitivity_prev_close_minus_open_bps", "value": sens_gap},
        {"item": "secondary_traded_valuation_minus_mark_net_bps", "value": sec_gap, "n": len(secs)},
        {
            "item": "basis_mae_bps_mean",
            "value": statistics.fmean([1e4 * p.basis_mae / p.n0 for p in pos_a]) if pos_a else None,
        },
        {
            "item": "basis_dd_bps_mean",
            "value": statistics.fmean([1e4 * p.basis_dd / p.n0 for p in pos_a]) if pos_a else None,
        },
        {
            "item": "total_marked_dd_bps_mean",
            "value": statistics.fmean([1e4 * p.total_dd / p.n0 for p in pos_a]) if pos_a else None,
        },
        {
            "item": "adverse_equity_bybit_bps_mean",
            "value": statistics.fmean([1e4 * p.adverse_equity["BYBIT"] / p.n0 for p in pos_a])
            if pos_a
            else None,
        },
        {
            "item": "adverse_equity_binance_bps_mean",
            "value": statistics.fmean([1e4 * p.adverse_equity["BINANCE"] / p.n0 for p in pos_a])
            if pos_a
            else None,
        },
    ]
    files += _write_table(conc, out / "carry_concentration", parquet=False)
    stress = [
        {"strategy": "A", "cost_bps_per_execution": 8, **summarize(pos_a)},
        {"strategy": "A", "cost_bps_per_execution": 12, **summarize(pos_a, "net_bps_stress")},
        {"strategy": "B", "cost_bps_per_execution": 8, **summarize(b.positions)},
        {"strategy": "B", "cost_bps_per_execution": 12, **summarize(b.positions, "net_bps_stress")},
        {"strategy": "B_ROLL", "cost_bps_per_execution": 8, **summarize(broll.positions)},
        {
            "strategy": "B_ROLL",
            "cost_bps_per_execution": 12,
            **summarize(broll.positions, "net_bps_stress"),
        },
    ]
    files += _write_table(stress, out / "carry_cost_stress", parquet=False)
    ctrl = [
        calendar_metrics(pos_a, first_valid, last_valid, "A_conditional"),
        calendar_metrics(b.positions, first_valid, last_valid, "B_threshold_off_72h"),
        calendar_metrics(broll.positions, first_valid, last_valid, "B_roll"),
        calendar_metrics([], first_valid, last_valid, "C_flat"),
    ]
    files += _write_table(ctrl, out / "carry_controls", parquet=False)
    files += _write_table([d.__dict__ for d in a.decisions], out / "carry_decisions", parquet=False)
    census = {
        "valid_daily_decisions": len(valid_days),
        "invalid_decisions": dict(a.excluded),
        "threshold_qualified_decision_days": sum(1 for d in a.decisions if d.qualified),
        "entries_suppressed_by_active_position": a.suppressed_by_active,
        "completed_positions": len(pos_a),
        "renewals": sum(1 for p in pos_a if p.renewal),
        "regime_clusters": n_clusters,
        "positions_per_calendar_day": len(pos_a) / span if span else None,
        "occupancy_share": ctrl[0]["occupancy_share"],
        "months": months,
    }
    results = {
        "census": census,
        "A": summarize(pos_a),
        "A_stress": summarize(pos_a, "net_bps_stress"),
        "B": summarize(b.positions),
        "B_roll": summarize(broll.positions),
        "blocks": {k: summarize(v) for k, v in blocks.items()},
        "cluster_bootstrap": cb,
        "moving_block_bootstrap": mb,
        "concentration": conc,
        "controls_calendar": ctrl,
        "verdict": ver,
    }
    manifest = {
        "schema": "carry_manifest-1",
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
    (out / "SUMMARY.md").write_text(_summary(run_id, manifest))
    return {"run_id": run_id, "out_dir": out, "verdict": ver, "results": results}


def _summary(run_id: str, man: dict[str, Any]) -> str:
    r = man["results"]
    v = r["verdict"]
    c = r["census"]
    o = [f"# SUMMARY — Bybit/Binance conditional funding carry `{run_id}`", ""]
    o.append(
        f"**{v['verdict']}** — informative sample: {v['informative_sample']}; n {v['n']}, clusters {v['clusters']}, {v['months']:.1f} months. {v['note']}."
    )
    o.append(
        f"Bar/MARK-based economic feasibility screen; NOT executable profits. Preregistration sha256 `{man['preregistration_sha256'][:16]}…` frozen before any PnL."
    )
    o.append("")
    o.append("## Census")
    o.append("")
    o.append(
        f"- valid decisions {c['valid_daily_decisions']}; invalid/excluded {json.dumps(c['invalid_decisions'], sort_keys=True)}; qualified days {c['threshold_qualified_decision_days']}; suppressed by active position {c['entries_suppressed_by_active_position']}; completed positions {c['completed_positions']} (renewals {c['renewals']}); clusters {c['regime_clusters']}; positions/day {_f(c['positions_per_calendar_day'], 4)}; occupancy {_f(c['occupancy_share'], 3)}"
    )
    o.append("")
    o.append("## Per-position economics (bps of N0 = matched one-leg notional)")
    o.append("")
    o.append(
        "| strategy | cost/exec | n | clusters | funding | basis | cost | gross | net mean | net median | net PF | win | cum DD | net USDT per BTC |"
    )
    o.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for name, s, cost in (
        ("A", r["A"], 8),
        ("A", r["A_stress"], 12),
        ("B", r["B"], 8),
        ("B_roll", r["B_roll"], 8),
    ):
        o.append(
            f"| {name} | {cost} | {s['count']} | {s['clusters']} | {_f(s['funding_bps_mean'])} | {_f(s['basis_bps_mean'])} | {_f(s['cost_bps_mean'])} | {_f(s['gross_bps_mean'])} | {_f(s['net_bps_mean'])} | {_f(s['net_bps_median'])} | {_f(s['net_pf'])} | {_f(s['win_rate'], 3)} | {_f(s['drawdown_bps_cum'])} | {_f(s['net_usdt_per_btc'])} |"
        )
    o.append("")
    o.append("## Confidence intervals for pos_a mean net bps (seed 42)")
    o.append("")
    o.append(f"- whole-cluster bootstrap: {json.dumps(r['cluster_bootstrap'], sort_keys=True)}")
    o.append(
        f"- 30-day moving-block bootstrap: {json.dumps(r['moving_block_bootstrap'], sort_keys=True)}"
    )
    o.append("")
    o.append("## Chronological blocks (pos_a)")
    o.append("")
    o.append("| block | n | clusters | funding | basis | cost | net mean | net median | PF | DD |")
    o.append("|---|---|---|---|---|---|---|---|---|---|")
    for k, s in r["blocks"].items():
        o.append(
            f"| {k} | {s['count']} | {s['clusters']} | {_f(s['funding_bps_mean'])} | {_f(s['basis_bps_mean'])} | {_f(s['cost_bps_mean'])} | {_f(s['net_bps_mean'])} | {_f(s['net_bps_median'])} | {_f(s['net_pf'])} | {_f(s['drawdown_bps_cum'])} |"
        )
    o.append("")
    o.append("## Concentration / sensitivity")
    o.append("")
    for row in r["concentration"]:
        o.append(f"- {json.dumps(row, sort_keys=True, default=str)}")
    o.append("")
    o.append("## Calendar comparison (q = 1 BTC accounting unit)")
    o.append("")
    o.append(
        "| strategy | positions | net USDT (8) | net USDT (12) | funding | basis | cost | occupancy | turnover notional | mean initial collateral | min equity Bybit | min equity Binance | max notional/equity |"
    )
    o.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for row in r["controls_calendar"]:
        o.append(
            f"| {row['strategy']} | {row['positions']} | {_f(row['net_usdt_per_btc_primary'])} | {_f(row['net_usdt_per_btc_stress'])} | {_f(row['funding_usdt'])} | {_f(row['basis_usdt'])} | {_f(row['cost_usdt_primary'])} | {_f(row['occupancy_share'], 3)} | {_f(row['turnover_notional_usdt'])} | {_f(row['mean_initial_collateral_usdt_both_venues'])} | {_f(row['min_equity_bybit'])} | {_f(row['min_equity_binance'])} | {_f(row['max_notional_over_equity'], 3)} |"
        )
    o.append("")
    o.append("## Gates")
    o.append("")
    for k, ok in v["gates"].items():
        o.append(f"- {k}: {'PASS' if ok else 'FAIL'}")
    o.append(
        f"- CI rules out positive expectancy: {v['ci_rules_out_positive_expectancy']}; profit entirely from basis (carry persistence not established): {v['profit_entirely_from_basis_carry_not_established']}"
    )
    o.append("")
    o.append(
        "Limitations: no historical executable quotes or fee tiers (modeled 8 / 12 bps all-in); settlement marks approximated by the mark-bar open at the settlement timestamp; settled funding assumed available within five minutes; liquidation / margin compliance unqualified; mark-based results are not executable profits."
    )
    o.append("")
    return "\n".join(o)
