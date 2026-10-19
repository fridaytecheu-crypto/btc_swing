"""N1 / N2 / N4 preregistered screens (frozen rules; fixed-horizon gross outcomes; no stops, no exits,
no parameter choice after the fact). Every rule constant here is part of the preregistration manifest.

Common conventions
- A signal is evaluated on COMPLETED bars only; the decision time is the signal bar's close.
- Embargo: the decision cannot act for EMBARGO_MIN minute(s); the benchmark entry is the OPEN of the
  first Bybit 1m bar starting at decision + EMBARGO_MIN (never the signal bar's own close).
- Outcome: side-adjusted gross return from that entry to the CLOSE of the 1m bar ending exactly
  `horizon` minutes later; the whole 1m path must exist (missing minutes -> not eligible).
- Lagged thresholds: a quantile over the 30 most recent COMPLETE UTC days strictly BEFORE the signal
  day. The signal day (and therefore the signal bar) never contributes to its own threshold.
- Risk unit for R: the ATR20 of the completed 5m bars at the setup, in bps (recorded as `stop_bps`).
- Episode rule (conservative, documented): a same-side flag within COOLDOWN_MIN minutes of the parent's
  first setup is a repeated trigger of that parent, never a new opportunity; the opposite side has its
  own state. N4 has at most one parent per anchor per local day by construction."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from btc_swing.research.intraday_portfolio.contract import MS_1M, MS_5M, EventTimes
from btc_swing.research.intraday_portfolio.episodes import Exit, ParentEpisode, r_multiple
from btc_swing.research.intraday_portfolio.ledger import BASE_COST_BPS, benchmark_net_bps
from btc_swing.research.intraday_portfolio.primitives import Bars, regime

MS_15M = 15 * MS_1M
EMBARGO_MIN = 1
HISTORY_DAYS = 30
COOLDOWN_MIN = 60
N1_PCT = 0.95
N1_RANGE_BARS = 6  # the preceding completed 30 minutes = six 5m bars, excluding the signal bar
N1_HORIZON_MIN = 30
N1_DIAG_HORIZON_MIN = 60
N2_PCT = 0.90  # top decile of |15m signed flow|
N2_HORIZON_MIN = 30
N4_HOLD_MIN = 45
N4_FIRST_WINDOW_BARS = 3  # the first completed 15 minutes after the anchor
N4_SESSIONS: tuple[tuple[str, str, int, int], ...] = (
    ("TOKYO", "Asia/Tokyo", 9, 0),
    ("LONDON", "Europe/London", 8, 0),
    ("NEWYORK", "America/New_York", 9, 30),
)
BARS_PER_DAY_5M = 288
BOUNDARIES_PER_DAY_15M = 96


# ----------------------------------------------------------------------------- shared helpers
def day_of(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date().isoformat()


def days_array(ms: np.ndarray) -> np.ndarray:
    return np.array([day_of(int(x)) for x in ms])


def complete_days(days: np.ndarray, expected: int) -> set[str]:
    u, c = np.unique(days, return_counts=True)
    return {str(d) for d, n in zip(u, c, strict=True) if int(n) == expected}


def lagged_quantile_by_day(
    values: np.ndarray,
    days: np.ndarray,
    complete: set[str],
    q: float,
    min_days: int = HISTORY_DAYS,
) -> dict[str, float]:
    """day -> quantile of `values` over the `min_days` most recent complete days strictly before it.
    Days with fewer complete predecessors get no entry (fail closed). NaN values are ignored."""
    per_day: dict[str, list[float]] = {}
    for v, d in zip(values.tolist(), days.tolist(), strict=True):
        if math.isfinite(v):
            per_day.setdefault(str(d), []).append(float(v))
    out: dict[str, float] = {}
    comp = sorted(d for d in complete if d in per_day)
    for d in sorted(set(days.tolist())):
        hist = [x for x in comp if x < d][-min_days:]
        if len(hist) < min_days:
            continue
        pool = np.concatenate([np.asarray(per_day[x]) for x in hist])
        if len(pool):
            out[str(d)] = float(np.quantile(pool, q))
    return out


@dataclass
class Outcome:
    entry_ms: int
    entry_px: float
    exit_ms: int
    exit_px: float
    mfe_bps: float
    mae_bps: float


def horizon_outcome(b1: Bars, entry_ms: int, horizon_min: int, side: int) -> Outcome | None:
    """Entry = OPEN of the 1m bar opening at `entry_ms`; exit = CLOSE of the bar ending `horizon_min`
    minutes later; every minute in between must exist."""
    j = int(np.searchsorted(b1.open_ms, entry_ms))
    k = j + horizon_min - 1
    if j >= len(b1) or int(b1.open_ms[j]) != entry_ms or k >= len(b1) or not b1.contiguous(j, k):
        return None
    e = float(b1.open[j])
    hi, lo = float(np.max(b1.high[j : k + 1])), float(np.min(b1.low[j : k + 1]))
    fav = (hi - e) if side > 0 else (e - lo)
    adv = (lo - e) if side > 0 else (e - hi)
    return Outcome(
        entry_ms, e, int(b1.close_ms[k]), float(b1.close[k]), fav / e * 1e4, adv / e * 1e4
    )


def close_to_close_bps(b1: Bars, t0_close_ms: int, horizon_min: int, side: int) -> float | None:
    """Benchmark WITHOUT embargo: from the signal close itself to the close `horizon_min` later
    (diagnostic only: shows how much of the effect lives inside the embargo minute)."""
    i = int(np.searchsorted(b1.close_ms, t0_close_ms))
    k = i + horizon_min
    if (
        i >= len(b1)
        or int(b1.close_ms[i]) != t0_close_ms
        or k >= len(b1)
        or not b1.contiguous(i, k)
    ):
        return None
    return side * (float(b1.close[k]) / float(b1.close[i]) - 1) * 1e4


@dataclass
class NContext:
    b5: Bars
    b1: Bars
    atr: np.ndarray  # ATR20 per 5m bar (price units)
    er: np.ndarray
    days5: np.ndarray = field(default_factory=lambda: np.zeros(0))
    flow_spot: dict[int, tuple[float, float]] = field(default_factory=dict)  # open_ms -> (q, tbq)
    flow_perp: dict[int, tuple[float, float]] = field(default_factory=dict)
    oi: dict[int, float] = field(default_factory=dict)  # time_ms -> open interest (Binance, proxy)
    cost_bps: float = BASE_COST_BPS

    def __post_init__(self) -> None:
        if len(self.days5) != len(self.b5):
            self.days5 = days_array(self.b5.open_ms)


def _new_parent(
    ctx: NContext,
    engine: str,
    side: str,
    i5: int,
    decision_ms: int,
    horizon_min: int,
    labels: list[str],
    levels: dict[str, float],
) -> ParentEpisode:
    s = 1 if side == "LONG" else -1
    atr_i = float(ctx.atr[i5])
    px = float(ctx.b5.close[i5])
    atr_bps = atr_i / px * 1e4 if math.isfinite(atr_i) and px > 0 else math.nan
    er = float(ctx.er[i5])
    entry_ms = decision_ms + EMBARGO_MIN * MS_1M
    p = ParentEpisode(
        parent_id=f"{engine}-{side[0]}-{decision_ms}",
        engine=engine,
        labels=[engine, side, regime(er), *labels],
        side=side,
        first_setup_time_ms=decision_ms,
        receipt_time_ms=None,
        times=EventTimes(decision_ms, None, None, decision_ms, entry_ms, None),
        frozen_levels={"atr_setup": atr_i, "atr_bps": atr_bps, **levels},
        regime=regime(er),
        er=er,
    )
    p.notes["embargo_done_ms"] = entry_ms
    p.notes["horizon_min"] = horizon_min
    p.notes["risk_unit"] = "ATR20_bps"
    p.notes["utc_hour"] = datetime.fromtimestamp(decision_ms / 1000, tz=UTC).hour
    p.stop_bps = atr_bps
    if not math.isfinite(atr_bps) or atr_bps <= 0:
        p.mark_rejected("ATR_UNAVAILABLE")
        return p
    oc = horizon_outcome(ctx.b1, entry_ms, horizon_min, s)
    if oc is None:
        p.mark_rejected("ENTRY_OR_HORIZON_PATH_UNAVAILABLE")
        return p
    p.eligibility_state = "ELIGIBLE"
    p.entry_benchmark = oc.entry_px
    p.exit = Exit("HORIZON", oc.exit_ms, oc.exit_px)
    p.gross_bps = s * (oc.exit_px / oc.entry_px - 1) * 1e4
    p.cost_bps = ctx.cost_bps
    p.net_bps = benchmark_net_bps(p.gross_bps, ctx.cost_bps, 0.0)
    p.net_r = r_multiple(p.net_bps, atr_bps)
    p.mfe_bps, p.mae_bps, p.hold_min = oc.mfe_bps, oc.mae_bps, float(horizon_min)
    p.notes["gross_from_close_no_embargo_bps"] = close_to_close_bps(
        ctx.b1, decision_ms, horizon_min, s
    )
    p.eligibility_state = "CLOSED"
    return p


class _SideState:
    """Same-side episode bookkeeping: a flag within COOLDOWN_MIN of the parent's first setup repeats."""

    def __init__(self) -> None:
        self.last: dict[str, ParentEpisode | None] = {"LONG": None, "SHORT": None}

    def claim(self, side: str, t_ms: int) -> ParentEpisode | None:
        p = self.last[side]
        if p is not None and t_ms - p.first_setup_time_ms < COOLDOWN_MIN * MS_1M:
            p.add_repeat(t_ms)
            return p
        return None

    def set(self, side: str, p: ParentEpisode) -> None:
        self.last[side] = p


@dataclass
class ScreenResult:
    engine: str
    parents: list[ParentEpisode]
    raw_flags: int
    evaluated_bars: int
    threshold_days: int
    skipped_no_threshold: int
    extra: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------------- N1
def run_n1(ctx: NContext) -> ScreenResult:
    b5 = ctx.b5
    n = len(b5)
    ret = np.full(n, np.nan)
    for i in range(1, n):
        if b5.contiguous(i - 1, i):
            ret[i] = float(b5.close[i]) / float(b5.close[i - 1]) - 1
    comp = complete_days(ctx.days5, BARS_PER_DAY_5M)
    thr = lagged_quantile_by_day(np.abs(ret), ctx.days5, comp, N1_PCT)
    vol_med = lagged_quantile_by_day(b5.volume, ctx.days5, comp, 0.5)
    st = _SideState()
    out: list[ParentEpisode] = []
    raw = evaluated = skipped = 0
    for i in range(N1_RANGE_BARS, n):
        if not b5.contiguous(i - N1_RANGE_BARS, i) or not math.isfinite(ret[i]):
            continue
        d = str(ctx.days5[i])
        t = thr.get(d)
        if t is None:
            skipped += 1
            continue
        evaluated += 1
        r = float(ret[i])
        if abs(r) < t:
            continue
        hi_prev = float(np.max(b5.high[i - N1_RANGE_BARS : i]))
        lo_prev = float(np.min(b5.low[i - N1_RANGE_BARS : i]))
        c = float(b5.close[i])
        if r > 0 and c > hi_prev:
            side = "LONG"
        elif r < 0 and c < lo_prev:
            side = "SHORT"
        else:
            continue
        raw += 1
        t_close = int(b5.close_ms[i])
        if st.claim(side, t_close) is not None:
            continue
        p = _new_parent(
            ctx,
            "N1",
            side,
            i,
            t_close,
            N1_HORIZON_MIN,
            [],
            {"prior_30m_high": hi_prev, "prior_30m_low": lo_prev, "threshold_abs_ret": t},
        )
        # recorded, never gated: impulse size, volume ratio, VWAP displacement, OI change, volatility
        p.notes["impulse_bps"] = r * 1e4
        vm = vol_med.get(d)
        p.notes["volume_ratio_vs_30d_median"] = (
            float(b5.volume[i]) / vm if vm is not None and vm > 0 else math.nan
        )
        vsum = float(np.sum(b5.volume[i - N1_RANGE_BARS : i]))
        vw30 = (
            float(np.sum(b5.vwap[i - N1_RANGE_BARS : i] * b5.volume[i - N1_RANGE_BARS : i])) / vsum
            if vsum > 0
            else math.nan
        )
        p.notes["vwap30_displacement_bps"] = (
            (c - vw30) / c * 1e4 if math.isfinite(vw30) else math.nan
        )
        oi_now = ctx.oi.get(int(b5.close_ms[i]))
        oi_prev = ctx.oi.get(int(b5.open_ms[i]))
        p.notes["oi_change_pct_proxy"] = (
            (oi_now / oi_prev - 1) * 100 if oi_now and oi_prev else math.nan
        )
        if p.traded:
            s = 1 if side == "LONG" else -1
            oc60 = horizon_outcome(
                ctx.b1, p.times.benchmark_entry_time or 0, N1_DIAG_HORIZON_MIN, s
            )
            p.notes["gross_60m_diag_bps"] = (
                s * (oc60.exit_px / oc60.entry_px - 1) * 1e4 if oc60 else math.nan
            )
        out.append(p)
        st.set(side, p)
    return ScreenResult("N1", out, raw, evaluated, len(thr), skipped)


# ----------------------------------------------------------------------------- N2
def signed_quote_volume(taker_buy_quote: float, total_quote: float) -> float:
    """2 * taker_buy_quote - total_quote = taker buy - taker sell quote volume."""
    return 2.0 * taker_buy_quote - total_quote


def run_n2(ctx: NContext, source: str) -> ScreenResult:
    """`source` = "SPOT" (Binance spot klines) or "PERP" (Binance USDT-perp klines); the outcome is always
    the Bybit perp. Signal at completed 15m boundaries from the three constituent completed 5m bars."""
    flows = ctx.flow_spot if source == "SPOT" else ctx.flow_perp
    b5 = ctx.b5
    n = len(b5)
    engine = f"N2{source[0]}"  # N2S / N2P
    # per-boundary 15m signed flow (NaN when any constituent is missing)
    is_b = np.zeros(n, dtype=bool)
    flow15 = np.full(n, np.nan)
    signs_agree = np.zeros(n, dtype=bool)
    missing = 0
    for i in range(2, n):
        if int(b5.close_ms[i]) % MS_15M != 0 or not b5.contiguous(i - 2, i):
            continue
        is_b[i] = True
        parts = [flows.get(int(b5.open_ms[k])) for k in (i - 2, i - 1, i)]
        if any(x is None for x in parts):
            missing += 1
            continue
        sv = [signed_quote_volume(x[1], x[0]) for x in parts if x is not None]
        if not all(math.isfinite(v) for v in sv):
            missing += 1
            continue
        flow15[i] = sum(sv)
        signs_agree[i] = all(v > 0 for v in sv) or all(v < 0 for v in sv)
    days_b = ctx.days5[is_b]
    comp = complete_days(days_b[np.isfinite(flow15[is_b])], BOUNDARIES_PER_DAY_15M)
    thr = lagged_quantile_by_day(np.abs(flow15[is_b]), days_b, comp, N2_PCT)
    st = _SideState()
    out: list[ParentEpisode] = []
    raw = evaluated = skipped = 0
    for i in np.where(is_b)[0].tolist():
        f = float(flow15[i])
        if not math.isfinite(f):
            continue
        t = thr.get(str(ctx.days5[i]))
        if t is None:
            skipped += 1
            continue
        evaluated += 1
        if abs(f) < t or not signs_agree[i]:
            continue
        side = "LONG" if f > 0 else "SHORT"
        raw += 1
        t_close = int(b5.close_ms[i])
        if st.claim(side, t_close) is not None:
            continue
        prior_ret = (
            (float(b5.close[i]) / float(b5.close[i - 3]) - 1) * 1e4
            if i >= 3 and b5.contiguous(i - 3, i)
            else math.nan
        )
        p = _new_parent(
            ctx,
            engine,
            side,
            i,
            t_close,
            N2_HORIZON_MIN,
            [source],
            {"flow15_signed_quote": f, "threshold_abs_flow": t},
        )
        p.notes["flow_source"] = source
        p.notes["prior_15m_ret_bps"] = prior_ret
        p.notes["quote_volume_15m"] = sum(flows[int(b5.open_ms[k])][0] for k in (i - 2, i - 1, i))
        out.append(p)
        st.set(side, p)
    return ScreenResult(
        engine,
        out,
        raw,
        evaluated,
        len(thr),
        skipped,
        {"boundaries": int(is_b.sum()), "boundaries_missing_flow_fields": missing},
    )


# ----------------------------------------------------------------------------- N4
def session_anchor_utc_ms(d: date, tz: str, hour: int, minute: int) -> int | None:
    """UTC ms of the local wall-clock anchor on local date `d` (DST handled by zoneinfo); None when the
    local time does not exist (spring-forward gap) or the local day is a weekend."""
    if d.weekday() >= 5:
        return None
    z = ZoneInfo(tz)
    local = datetime(d.year, d.month, d.day, hour, minute, tzinfo=z)
    # a non-existent local time (DST gap) round-trips to a different wall clock -> refuse
    if local.utcoffset() is None or local.astimezone(UTC).astimezone(z).replace(
        tzinfo=None
    ) != datetime(d.year, d.month, d.day, hour, minute):
        return None
    return int(local.timestamp() * 1000)


def run_n4(ctx: NContext) -> ScreenResult:
    b5 = ctx.b5
    idx_by_open = {int(b5.open_ms[i]): i for i in range(len(b5))}
    d0 = datetime.fromtimestamp(int(b5.open_ms[0]) / 1000, tz=UTC).date() - timedelta(days=1)
    d1 = datetime.fromtimestamp(int(b5.open_ms[-1]) / 1000, tz=UTC).date() + timedelta(days=1)
    out: list[ParentEpisode] = []
    raw = evaluated = skipped = 0
    per_session: dict[str, dict[str, int]] = {}
    for name, tz, hh, mm in N4_SESSIONS:
        hist_vol: list[float] = []
        stats = {"sessions": 0, "flags": 0, "parents": 0}
        d = d0
        while d <= d1:
            anchor = session_anchor_utc_ms(d, tz, hh, mm)
            d += timedelta(days=1)
            if anchor is None:
                continue
            i = idx_by_open.get(anchor)
            if i is None or not b5.contiguous(i, i + N4_FIRST_WINDOW_BARS - 1):
                continue
            k = i + N4_FIRST_WINDOW_BARS - 1
            stats["sessions"] += 1
            r15 = float(b5.close[k]) / float(b5.open[i]) - 1
            vol15 = float(np.sum(b5.volume[i : k + 1]))
            hist = hist_vol[-HISTORY_DAYS:]
            hist_vol.append(vol15)  # the current session joins the history only for LATER sessions
            if len(hist) < HISTORY_DAYS:
                skipped += 1
                continue
            evaluated += 1
            med = statistics.median(hist)
            if r15 == 0:
                continue
            stats["flags"] += 1
            raw += 1
            if not vol15 > med:
                continue
            side = "LONG" if r15 > 0 else "SHORT"
            t_close = int(b5.close_ms[k])
            off = datetime.fromtimestamp(anchor / 1000, tz=ZoneInfo(tz)).utcoffset()
            p = _new_parent(
                ctx,
                "N4",
                side,
                k,
                t_close,
                N4_HOLD_MIN,
                [name],
                {
                    "opening_volume": vol15,
                    "median_opening_volume_30": med,
                    "anchor_ms": float(anchor),
                },
            )
            p.notes["session"] = name
            p.notes["first_15m_ret_bps"] = r15 * 1e4
            p.notes["utc_offset_min"] = off.total_seconds() / 60 if off is not None else math.nan
            stats["parents"] += 1
            out.append(p)
        per_session[name] = stats
    out.sort(key=lambda p: (p.first_setup_time_ms, p.parent_id))
    return ScreenResult("N4", out, raw, evaluated, 0, skipped, {"sessions": per_session})


# ----------------------------------------------------------------------------- flow dictionaries
def flow_dict(
    open_ms: np.ndarray, quote: np.ndarray, taker_buy_quote: np.ndarray
) -> dict[int, tuple[float, float]]:
    return {
        int(t): (float(q), float(tb))
        for t, q, tb in zip(open_ms.tolist(), quote.tolist(), taker_buy_quote.tolist(), strict=True)
    }


def bars5_to_15m_boundaries(b5: Bars) -> list[int]:
    return [
        i
        for i in range(2, len(b5))
        if int(b5.close_ms[i]) % MS_15M == 0 and b5.contiguous(i - 2, i)
    ]


_ = MS_5M  # re-exported constant used by callers / tests
