"""E4 — Forced Flow Response: an EVENT STUDY on the raw received Bybit public stream (liquidation
messages, aggressive trades, BBO, local receipt timestamps). Not a runner.

Receipt-time discipline: every feature at second t uses only messages RECEIVED at or before t; the
exchange timestamp is recorded but never treated as the time we knew the event.

Liquidation side convention (btc_swing/docs/BTC_V10_LIVE_MICROSTRUCTURE_DESIGN.md section 1, verified
empirically): allLiquidation `S = "Buy"` means a LONG position was liquidated, i.e. FORCED SELLING;
`S = "Sell"` means a SHORT was liquidated, i.e. FORCED BUYING.

Shock = same-direction liquidation notional over the last 60 s >= the lagged 99th percentile of that
quantity for the same UTC hour over the previous 30 COMPLETE days AND the 60-s price move >= 0.5 ATR
(20-period Wilder ATR on completed 5m bars built from received trades) in the shock direction. No
threshold exists before 30 complete days: detection fails closed. Events < 10 minutes apart belong to
the same parent (first observable crossing kept). Two mutually exclusive arms on the next two completed
1m bars (REVERSAL / CONTINUATION / ABSTAIN), stop = adverse extreme +/- 0.25 ATR (20-150 bps), target 2R,
max hold 60 min. Markouts: executable, side-correct (enter at the taking side, exit at the opposite side)
at 15 s, 30 s, 1, 5, 15, 30, 60, 120 min from the RECEIPT time plus latency sensitivities 0.25 / 1 / 3 / 10 s.
Liquidation-shock parents and aggressive-flow-only exceptional parents are separate tables."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from btc_swing.research.intraday_portfolio.contract import MS_1M
from btc_swing.research.intraday_portfolio.data import RawRow, iter_raw_rows, raw_hour_files
from btc_swing.research.intraday_portfolio.primitives import (
    Bars,
    bars_1m_to_5m,
    completed_bars,
    wilder_atr,
)

HORIZONS_S = (15, 30, 60, 300, 900, 1800, 3600, 7200)
LATENCIES_S = (0.0, 0.25, 1.0, 3.0, 10.0)
WINDOW_S = 60
PCT = 0.99
HISTORY_DAYS = 30
DEDUPE_S = 600
NEW_SHOCK_GAP_S = 1800
BBO_MAX_AGE_MS = 2000
SHOCK_ATR_MULT = 0.5


# ----------------------------------------------------------------------------- raw -> events
@dataclass
class Events:
    tr_rx: np.ndarray
    tr_ex: np.ndarray
    tr_px: np.ndarray
    tr_qty: np.ndarray
    tr_buy: np.ndarray  # aggressor buy
    lq_rx: np.ndarray
    lq_ex: np.ndarray
    lq_px: np.ndarray
    lq_qty: np.ndarray
    lq_forced_sell: np.ndarray  # S == "Buy" (a LONG was liquidated)
    bbo_rx: np.ndarray
    bid: np.ndarray
    ask: np.ndarray
    channels: dict[str, int] = field(default_factory=dict)


class _Book:
    def __init__(self) -> None:
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.valid = False
        self.last_u: int | None = None

    def apply(self, typ: str | None, data: Any) -> tuple[float, float] | None:
        d = data or {}
        u = d.get("u")
        if typ == "snapshot":
            self.bids = {float(p): float(q) for p, q in d.get("b", [])}
            self.asks = {float(p): float(q) for p, q in d.get("a", [])}
            self.valid = True
        else:
            if self.last_u is not None and u is not None and int(u) != self.last_u + 1:
                self.valid = False  # sequence gap: wait for the next snapshot
            if self.valid:
                for p, q in d.get("b", []):
                    (
                        self.bids.pop(float(p), None)
                        if float(q) == 0
                        else self.bids.__setitem__(float(p), float(q))
                    )
                for p, q in d.get("a", []):
                    (
                        self.asks.pop(float(p), None)
                        if float(q) == 0
                        else self.asks.__setitem__(float(p), float(q))
                    )
        self.last_u = int(u) if u is not None else self.last_u
        if not (self.valid and self.bids and self.asks):
            return None
        b, a = max(self.bids), min(self.asks)
        if b >= a:
            self.valid = False
            return None
        return b, a


def events_from_roots(roots: list[Path]) -> Events:
    tr: list[tuple[int, int, float, float, bool]] = []
    lq: list[tuple[int, int, float, float, bool]] = []
    bbo: list[tuple[int, float, float]] = []
    ch: dict[str, int] = defaultdict(int)
    book1, book50 = _Book(), _Book()
    for f in raw_hour_files(roots):
        for r in iter_raw_rows(f):
            ch[r.channel.split(".")[0]] += 1
            _ingest(r, tr, lq, bbo, book1, book50)
    tr.sort()
    lq.sort()
    bbo.sort()

    def col(rows: list[Any], k: int, dt: Any) -> np.ndarray:
        return np.array([x[k] for x in rows], dtype=dt)

    return Events(
        col(tr, 0, np.int64), col(tr, 1, np.int64), col(tr, 2, np.float64), col(tr, 3, np.float64), col(tr, 4, np.bool_),
        col(lq, 0, np.int64), col(lq, 1, np.int64), col(lq, 2, np.float64), col(lq, 3, np.float64), col(lq, 4, np.bool_),
        col(bbo, 0, np.int64), col(bbo, 1, np.float64), col(bbo, 2, np.float64), dict(ch),
    )  # fmt: skip


def _ingest(
    r: RawRow,
    tr: list[tuple[int, int, float, float, bool]],
    lq: list[tuple[int, int, float, float, bool]],
    bbo: list[tuple[int, float, float]],
    book1: _Book,
    book50: _Book,
) -> None:
    if r.channel.startswith("publicTrade"):
        for t in r.data or []:
            tr.append(
                (
                    r.rx_ms,
                    int(t.get("T", r.ex_ms or r.rx_ms)),
                    float(t["p"]),
                    float(t["v"]),
                    t.get("S") == "Buy",
                )
            )
    elif r.channel.startswith("allLiquidation"):
        for q in r.data or []:
            lq.append(
                (
                    r.rx_ms,
                    int(q.get("T", r.ex_ms or r.rx_ms)),
                    float(q.get("p", 0)),
                    float(q.get("v", 0)),
                    q.get("S") == "Buy",
                )
            )
    elif r.channel.startswith("orderbook.1."):
        ba = book1.apply(r.typ, r.data)
        if ba:
            bbo.append((r.rx_ms, ba[0], ba[1]))
    elif r.channel.startswith("orderbook.50."):
        ba = book50.apply(r.typ, r.data)
        if ba:
            bbo.append((r.rx_ms, ba[0], ba[1]))


# ----------------------------------------------------------------------------- 1-second receipt-time grid
@dataclass
class Grid:
    t_s: np.ndarray  # second start (ms)
    last_px: np.ndarray
    px_60s_ago: np.ndarray
    liq_sell_60: np.ndarray  # forced selling notional (USDT) over the last 60 s
    liq_buy_60: np.ndarray
    agg_buy_60: np.ndarray  # aggressive buy notional over the last 60 s
    agg_sell_60: np.ndarray
    bid: np.ndarray  # last BBO received at or before the second end (NaN when older than 2 s)
    ask: np.ndarray
    atr: np.ndarray  # ATR20 of the last COMPLETED 5m bar known at that second (NaN before 20 bars)
    seconds_with_trades: int


def _bucket_sum(t_ms: np.ndarray, v: np.ndarray, t0: int, n: int) -> np.ndarray:
    out = np.zeros(n)
    if len(t_ms) == 0:
        return out
    idx = (t_ms - t0) // 1000
    ok = (idx >= 0) & (idx < n)
    np.add.at(out, idx[ok].astype(np.int64), v[ok])
    return out


def _rolling(x: np.ndarray, w: int) -> np.ndarray:
    c = np.cumsum(np.r_[0.0, x])
    return c[w:] - c[:-w] if len(x) >= w else np.zeros(0)


def build_grid(ev: Events) -> Grid | None:
    if len(ev.tr_rx) == 0:
        return None
    t0 = int(ev.tr_rx.min() // 1000 * 1000)
    t1 = int(ev.tr_rx.max() // 1000 * 1000)
    n = (t1 - t0) // 1000 + 1
    t_s = t0 + np.arange(n, dtype=np.int64) * 1000
    # last trade price known at each second end (receipt time)
    order = np.argsort(ev.tr_rx, kind="stable")
    rx, px = ev.tr_rx[order], ev.tr_px[order]
    idx = np.searchsorted(rx, t_s + 999, side="right") - 1
    last_px = np.where(idx >= 0, px[np.maximum(idx, 0)], np.nan)
    sec_has = np.zeros(n, dtype=bool)
    sec_has[((rx - t0) // 1000).astype(np.int64)] = True
    px_60 = np.full(n, np.nan)
    px_60[WINDOW_S:] = last_px[:-WINDOW_S]
    notional = ev.tr_px * ev.tr_qty
    agg_buy = _bucket_sum(ev.tr_rx, np.where(ev.tr_buy, notional, 0.0), t0, n)
    agg_sell = _bucket_sum(ev.tr_rx, np.where(~ev.tr_buy, notional, 0.0), t0, n)
    lqn = ev.lq_px * ev.lq_qty
    liq_sell = _bucket_sum(ev.lq_rx, np.where(ev.lq_forced_sell, lqn, 0.0), t0, n)
    liq_buy = _bucket_sum(ev.lq_rx, np.where(~ev.lq_forced_sell, lqn, 0.0), t0, n)

    def roll(x: np.ndarray) -> np.ndarray:
        out = np.full(n, np.nan)
        r = _rolling(x, WINDOW_S)
        if len(r):
            out[WINDOW_S - 1 :] = r
        return out

    # BBO at second end, stale > 2 s -> NaN (never a stale quote presented as fresh)
    bid = np.full(n, np.nan)
    ask = np.full(n, np.nan)
    if len(ev.bbo_rx):
        j = np.searchsorted(ev.bbo_rx, t_s + 999, side="right") - 1
        ok = (j >= 0) & ((t_s + 999 - ev.bbo_rx[np.maximum(j, 0)]) <= BBO_MAX_AGE_MS)
        bid[ok], ask[ok] = ev.bid[j[ok]], ev.ask[j[ok]]
    # ATR of the last completed 5m bar KNOWN at each second: bars by exchange trade time, known once the
    # minute after the bar close has been received (receipt-time availability)
    atr = np.full(n, np.nan)
    b1 = _bars_1m_from_trades(ev)
    if b1 is not None and len(b1) >= 5:
        b5 = bars_1m_to_5m(b1)
        a5 = wilder_atr(b5, 20)
        known_at = b5.close_ms + MS_1M  # conservative: a bar is usable one minute after its close
        k = np.searchsorted(known_at, t_s + 999, side="right") - 1
        okk = k >= 0
        atr[okk] = a5[k[okk]]
    return Grid(
        t_s,
        last_px,
        px_60,
        roll(liq_sell),
        roll(liq_buy),
        roll(agg_buy),
        roll(agg_sell),
        bid,
        ask,
        atr,
        int(sec_has.sum()),
    )


def _bars_1m_from_trades(ev: Events) -> Bars | None:
    if len(ev.tr_ex) == 0:
        return None
    m = (ev.tr_ex // MS_1M) * MS_1M
    order = np.argsort(ev.tr_ex, kind="stable")
    m, px, q, buy = m[order], ev.tr_px[order], ev.tr_qty[order], ev.tr_buy[order]
    uniq, start = np.unique(m, return_index=True)
    end = np.r_[start[1:], len(m)]
    n = len(uniq)
    o, h, lo, c, v, vw, bu, se = (np.zeros(n) for _ in range(8))
    for k in range(n):
        s_, e_ = start[k], end[k]
        p = px[s_:e_]
        qq = q[s_:e_]
        o[k], c[k], h[k], lo[k] = p[0], p[-1], p.max(), p.min()
        v[k] = qq.sum()
        vw[k] = float(np.sum(p * qq) / v[k]) if v[k] > 0 else np.nan
        bu[k] = float(np.sum(qq[buy[s_:e_]]))
        se[k] = v[k] - bu[k]
    return completed_bars(uniq.astype(np.int64), o, h, lo, c, v, vw, bu, se, MS_1M)


# ----------------------------------------------------------------------------- thresholds (lagged, same UTC hour, 30 complete days)
def hour_of(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).hour


def day_of(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date().isoformat()


def lagged_thresholds(
    grid: Grid, column: np.ndarray, min_days: int = HISTORY_DAYS
) -> dict[tuple[str, int], float]:
    """(day, hour) -> 99th percentile of `column` over the SAME hour of the previous `min_days` COMPLETE
    days (a complete day = all 86400 seconds on the grid with trades in >= 1380 minutes). None when
    the history is incomplete: the caller treats a missing key as 'no threshold' (fail closed)."""
    days = np.array([day_of(int(t)) for t in grid.t_s])
    hours = np.array([hour_of(int(t)) for t in grid.t_s])
    uniq_days = sorted(set(days.tolist()))
    complete: list[str] = []
    for d in uniq_days:
        sel = days == d
        if sel.sum() == 86400 and (np.isfinite(grid.last_px[sel]).sum() >= 1380 * 60):
            complete.append(d)
    out: dict[tuple[str, int], float] = {}
    for d in uniq_days:
        hist = [x for x in complete if x < d][-min_days:]
        if len(hist) < min_days:
            continue
        hsel = np.isin(days, hist)
        for hr in range(24):
            vals = column[hsel & (hours == hr)]
            vals = vals[np.isfinite(vals)]
            if len(vals):
                out[(d, hr)] = float(np.quantile(vals, PCT))
    return out


# ----------------------------------------------------------------------------- shocks, arms, markouts
@dataclass
class Shock:
    parent_id: str
    kind: str  # LIQUIDATION / AGGRESSIVE_FLOW
    direction: str  # FORCED_SELL / FORCED_BUY
    t_rx_ms: int  # receipt time of the first observable crossing
    t_ex_ms: int | None
    notional_60: float
    threshold: float
    move_60_bps: float
    atr_bps: float
    arm: str = "ABSTAIN"
    arm_side: str | None = None
    entry_exec: float | None = None
    stop: float | None = None
    stop_bps: float | None = None
    target: float | None = None
    exit_reason: str | None = None
    exit_price: float | None = None
    exit_ms: int | None = None
    gross_bps: float | None = None
    repeated: int = 0
    markouts: dict[str, float] = field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        mk = d.pop("markouts")
        d.update(mk)
        return d


def detect(grid: Grid, thr: dict[tuple[str, int], float], kind: str) -> list[Shock]:
    """First observable crossings, deduplicated (< 10 min -> same parent, repeated count)."""
    sell = grid.liq_sell_60 if kind == "LIQUIDATION" else grid.agg_sell_60
    buy = grid.liq_buy_60 if kind == "LIQUIDATION" else grid.agg_buy_60
    out: list[Shock] = []
    last_by_dir: dict[str, Shock | None] = {"FORCED_SELL": None, "FORCED_BUY": None}
    for i in range(len(grid.t_s)):
        t = int(grid.t_s[i])
        key = (day_of(t), hour_of(t))
        th = thr.get(key)
        if th is None:
            continue
        px, p60, atr = grid.last_px[i], grid.px_60s_ago[i], grid.atr[i]
        if not (math.isfinite(px) and math.isfinite(p60) and math.isfinite(atr) and atr > 0):
            continue
        move = px - p60
        for direction, val, ok_dir in (
            ("FORCED_SELL", sell[i], move <= -SHOCK_ATR_MULT * atr),
            ("FORCED_BUY", buy[i], move >= SHOCK_ATR_MULT * atr),
        ):
            if not (math.isfinite(val) and val >= th and val > 0 and ok_dir):
                continue
            prev = last_by_dir[direction]
            if prev is not None and t - prev.t_rx_ms < DEDUPE_S * 1000:
                prev.repeated += 1
                prev.t_rx_ms = prev.t_rx_ms  # first crossing kept
                continue
            sh = Shock(
                f"E4-{kind[0]}-{direction[7]}-{t}",
                kind,
                direction,
                t,
                None,
                float(val),
                th,
                move / px * 1e4,
                atr / px * 1e4,
            )
            out.append(sh)
            last_by_dir[direction] = sh
    return out


def _exec_price(grid: Grid, t_ms: int, side: int) -> float | None:
    """Executable price at t: ask for a buy (+1), bid for a sell (-1); NaN -> None (stale / absent)."""
    i = int((t_ms - grid.t_s[0]) // 1000)
    if i < 0 or i >= len(grid.t_s):
        return None
    v = grid.ask[i] if side > 0 else grid.bid[i]
    return float(v) if math.isfinite(v) else None


def markouts(grid: Grid, sh: Shock) -> dict[str, float]:
    """Executable side-correct markouts for the REVERSAL direction (fade the forced flow) and the
    CONTINUATION direction, from the receipt time plus latency: enter at the taking side, exit at the
    opposite side at the horizon. NaN where a quote is absent (never a mid)."""
    out: dict[str, float] = {}
    rev = 1 if sh.direction == "FORCED_SELL" else -1
    for lat in LATENCIES_S:
        t_in = sh.t_rx_ms + int(lat * 1000)
        for arm_side, tag in ((rev, "rev"), (-rev, "cont")):
            e = _exec_price(grid, t_in, arm_side)
            for h in HORIZONS_S:
                x = _exec_price(grid, t_in + h * 1000, -arm_side)
                out[f"mk_{tag}_lat{lat:g}_{h}s"] = (
                    (arm_side * (x / e - 1) * 1e4) if (e and x) else math.nan
                )
    return out


def arms(grid: Grid, b1: Bars, sh: Shock) -> None:
    """The two mutually exclusive response arms on the next two COMPLETED 1m bars after the event
    minute; executable entry at the first fresh quote after the second bar is known (close + 1 min of
    receipt)."""
    ev_min = (sh.t_rx_ms // MS_1M) * MS_1M
    i0 = int(np.searchsorted(b1.open_ms, ev_min))
    if (
        i0 >= len(b1)
        or int(b1.open_ms[i0]) != ev_min
        or i0 + 2 >= len(b1)
        or not b1.contiguous(i0, i0 + 2)
    ):
        sh.arm = "ABSTAIN_NO_BARS"
        return
    ev_h, ev_l = float(b1.high[i0]), float(b1.low[i0])
    r1h, r1l, r1c = float(b1.high[i0 + 1]), float(b1.low[i0 + 1]), float(b1.close[i0 + 1])
    r2h, r2l, r2c = float(b1.high[i0 + 2]), float(b1.low[i0 + 2]), float(b1.close[i0 + 2])
    known_ms = int(b1.close_ms[i0 + 2]) + MS_1M
    atr_px = sh.atr_bps / 1e4 * float(b1.close[i0 + 2])
    side = 0
    if sh.direction == "FORCED_SELL":
        if r2l >= r1l and r2c > r1h:
            sh.arm, side = "REVERSAL", 1
            adverse = min(ev_l, r1l, r2l)
        elif r1c < ev_l and r2c < ev_l and r2c < r1c:
            sh.arm, side = "CONTINUATION", -1
            adverse = max(ev_h, r1h, r2h)
    else:
        if r2h <= r1h and r2c < r1l:
            sh.arm, side = "REVERSAL", -1
            adverse = max(ev_h, r1h, r2h)
        elif r1c > ev_h and r2c > ev_h and r2c > r1c:
            sh.arm, side = "CONTINUATION", 1
            adverse = min(ev_l, r1l, r2l)
    if side == 0:
        sh.arm = "ABSTAIN"
        return
    sh.arm_side = "LONG" if side > 0 else "SHORT"
    e = _exec_price(grid, known_ms, side)
    if e is None:
        sh.exit_reason = "NO_EXECUTABLE_QUOTE"
        return
    stop = adverse - side * 0.25 * atr_px
    d_bps = side * (e - stop) / e * 1e4
    sh.entry_exec, sh.stop, sh.stop_bps = e, stop, d_bps
    if not (20.0 <= d_bps <= 150.0):
        sh.exit_reason = "STOP_OUT_OF_RANGE"
        return
    sh.target = e + 2.0 * (e - stop)  # 2R in the trade direction (e - stop carries the sign)
    # exits on the received trade path: stop first, then target, then 60 min; executable exit side
    t_end = known_ms + 60 * MS_1M
    i_s = int((known_ms - grid.t_s[0]) // 1000)
    for i in range(max(i_s, 0), min(len(grid.t_s), i_s + 3600)):
        px = grid.last_px[i]
        if not math.isfinite(px):
            continue
        if side * (px - stop) <= 0:
            x = _exec_price(grid, int(grid.t_s[i]) + 999, -side)
            sh.exit_reason, sh.exit_price, sh.exit_ms = "STOP", x if x else stop, int(grid.t_s[i])
            break
        if side * (px - sh.target) >= 0:
            x = _exec_price(grid, int(grid.t_s[i]) + 999, -side)
            sh.exit_reason, sh.exit_price, sh.exit_ms = (
                "TARGET",
                x if x else sh.target,
                int(grid.t_s[i]),
            )
            break
    else:
        x = _exec_price(grid, t_end, -side)
        sh.exit_reason, sh.exit_price, sh.exit_ms = (
            ("TIME", x, t_end) if x else ("DATA_END", None, None)
        )
    if sh.exit_price is not None:
        sh.gross_bps = side * (sh.exit_price / e - 1) * 1e4


def controls(
    grid: Grid, shocks: list[Shock], per_parent: int = 5, step_s: int = 10
) -> dict[str, list[int]]:
    """Up to 5 non-shock control seconds per parent matched on UTC hour, weekday/weekend, lagged
    volatility quintile (|60-s move| over the prior 30 days of the same hour), spread quintile and prior
    5-min return sign bin, outside every shock's +/-120 min window. Deterministic (earliest matches)."""
    if not shocks:
        return {}
    t = grid.t_s
    excl = np.zeros(len(t), dtype=bool)
    for sh in shocks:
        lo = int((sh.t_rx_ms - 120 * MS_1M - t[0]) // 1000)
        hi = int((sh.t_rx_ms + 120 * MS_1M - t[0]) // 1000)
        excl[max(lo, 0) : min(hi, len(t))] = True
    mv = np.abs(grid.last_px - grid.px_60s_ago) / grid.px_60s_ago * 1e4
    spread = (grid.ask - grid.bid) / grid.bid * 1e4
    r5 = np.full(len(t), np.nan)
    r5[300:] = (grid.last_px[300:] / grid.last_px[:-300] - 1) * 1e4

    def quint(x: np.ndarray) -> np.ndarray:
        q = np.full(len(x), -1)
        ok = np.isfinite(x)
        if ok.sum() >= 5:
            edges = np.quantile(x[ok], [0.2, 0.4, 0.6, 0.8])
            q[ok] = np.searchsorted(edges, x[ok])
        return q

    qv, qs = quint(mv), quint(spread)
    rb = np.where(np.isnan(r5), -1, np.sign(r5) + 1).astype(int)
    hours = np.array([hour_of(int(x)) for x in t])
    wk = np.array([datetime.fromtimestamp(int(x) / 1000, tz=UTC).weekday() >= 5 for x in t])
    out: dict[str, list[int]] = {}
    for sh in shocks:
        i = int((sh.t_rx_ms - t[0]) // 1000)
        if i < 0 or i >= len(t):
            continue
        cand = np.where(
            (hours == hours[i])
            & (wk == wk[i])
            & (qv == qv[i])
            & (qs == qs[i])
            & (rb == rb[i])
            & ~excl
        )[0]
        cand = cand[(cand % step_s) == 0]
        out[sh.parent_id] = [int(t[c]) for c in cand[:per_parent]]
    return out


def run_e4(roots: list[Path]) -> dict[str, Any]:
    ev = events_from_roots(roots)
    grid = build_grid(ev)
    res: dict[str, Any] = {
        "channels": ev.channels,
        "n_trades": len(ev.tr_rx),
        "n_liquidations": len(ev.lq_rx),
        "n_bbo": len(ev.bbo_rx),
    }
    if grid is None:
        res.update({"status": "NO_TRADES", "shocks": [], "flow_parents": [], "controls": {}})
        return res
    res["grid_seconds"] = len(grid.t_s)
    res["seconds_with_trades"] = grid.seconds_with_trades
    res["bbo_seconds"] = int(np.isfinite(grid.bid).sum())
    res["atr_seconds"] = int(np.isfinite(grid.atr).sum())
    thr_liq = lagged_thresholds(grid, np.fmax(grid.liq_sell_60, grid.liq_buy_60))
    thr_agg = lagged_thresholds(grid, np.fmax(grid.agg_sell_60, grid.agg_buy_60))
    res["threshold_hours_available"] = {
        "liquidation": len(thr_liq),
        "aggressive_flow": len(thr_agg),
    }
    b1 = _bars_1m_from_trades(ev)
    shocks = detect(grid, thr_liq, "LIQUIDATION")
    flow = [
        s
        for s in detect(grid, thr_agg, "AGGRESSIVE_FLOW")
        if not any(abs(s.t_rx_ms - q.t_rx_ms) < DEDUPE_S * 1000 for q in shocks)
    ]
    for sh in shocks + flow:
        sh.markouts = markouts(grid, sh)
        if b1 is not None:
            arms(grid, b1, sh)
    res["shocks"] = [s.as_row() for s in shocks]
    res["flow_parents"] = [s.as_row() for s in flow]
    res["controls"] = controls(grid, shocks)
    res["status"] = "THRESHOLD_UNAVAILABLE_INSUFFICIENT_HISTORY" if not thr_liq else "OK"
    return res
