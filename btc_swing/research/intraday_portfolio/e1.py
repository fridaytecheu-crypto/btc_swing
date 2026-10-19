"""E1 — Accepted Range Expansion (frozen research specification; no parameter is tuned here).

Context: 4h efficiency-ratio regime (ER > 0.25 required), the previous completed 1h box (high / low / VWAP)
frozen at each UTC hour, 5m confirmation, benchmark (archive) or quote-based (live) entry.

LONG: (1) two consecutive CLOSED 5m bars close above the frozen box high (the box is frozen at the first
breakout bar and kept for the whole episode); (2) within the next three 5m bars the FIRST retest: low
touches / crosses the box high while the close stays above it; (3) entry = next executable ask after the
retest bar (archive: first print of the next minute, benchmark ledger). Cancel: a close back inside the box
before a valid retest, or no retest within 15 minutes. Stop = retest low - 0.25 ATR (SHORT: retest high
+ 0.25 ATR); stop distance d in bps must be within [20, 150]. Invalidation: a completed 5m close back
inside the original frozen box. Exit = earliest of stop, invalidation, target at 2d, 120 minutes. No
partial TP, no break-even, no trailing, no scores. Cooldown before a NEW parent on the same side: position
closed, >= 60 minutes since the first signal AND two completed 5m closes back inside the original box.
A new hourly box alone never creates a new parent; repeated breakouts are recorded on the parent.

INTERPRETATION (recorded in the manifest, not a parameter): the cooldown above governs a parent that
became a position. A setup that was cancelled or rejected before entry ends its episode at that time;
further breakouts of the SAME frozen box are repeats of that parent, a breakout of a later box is a new
candidate. Without this, one rejected breakout at a low price would block the whole side until price
returned inside that box (possibly never)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from btc_swing.research.intraday_portfolio.contract import MS_1H, MS_1M, EventTimes
from btc_swing.research.intraday_portfolio.episodes import ParentEpisode, r_multiple
from btc_swing.research.intraday_portfolio.ledger import (
    BASE_COST_BPS,
    benchmark_net_bps,
    funding_bps,
    gross_bps,
)
from btc_swing.research.intraday_portfolio.primitives import ER_RANGE_MAX, Bars, HourBox, regime
from btc_swing.research.intraday_portfolio.replay import (
    STOP_ATR_BUFFER,
    benchmark_entry,
    replay_exit,
    stop_distance_bps,
    stop_reject,
)

RETEST_BARS = 3
MAX_HOLD_MIN = 120
TARGET_R = 2.0
COOLDOWN_MIN = 60
COOLDOWN_INSIDE_BARS = 2


@dataclass
class E1Inputs:
    b5: Bars
    b1: Bars
    atr: np.ndarray  # ATR20 at each 5m bar
    er: np.ndarray  # ER at each 5m bar
    boxes: dict[int, HourBox]  # hour_open_ms -> box frozen for that hour
    fund_t: np.ndarray
    fund_rate: np.ndarray
    cost_bps: float = BASE_COST_BPS


@dataclass
class _Open:
    """Side state: the current parent and what ends its cooldown."""

    parent: ParentEpisode
    box_high: float
    box_low: float
    box_hour_ms: int  # the hour the frozen box belongs to (same box -> repeats, never a 2nd parent)
    closed_ms: int | None  # when the position closed (or the parent was rejected / cancelled)
    inside_closes: int = 0  # completed 5m closes back inside the original box since closed


def _box_for(inp: E1Inputs, open_ms: int) -> HourBox | None:
    bx = inp.boxes.get(int(open_ms // MS_1H * MS_1H))
    return bx if bx is not None and bx.available else None


def run_e1(inp: E1Inputs) -> list[ParentEpisode]:
    b5, b1 = inp.b5, inp.b1
    c5_by_close = {int(b5.close_ms[i]): float(b5.close[i]) for i in range(len(b5))}
    out: list[ParentEpisode] = []
    state: dict[int, _Open | None] = {1: None, -1: None}
    for i in range(1, len(b5)):
        t_close = int(b5.close_ms[i])
        if not b5.contiguous(i - 1, i):
            continue
        bx = _box_for(inp, int(b5.open_ms[i - 1]))
        for s in (1, -1):
            st = state[s]
            # ---- episode bookkeeping of an existing parent
            if st is not None and st.closed_ms is not None and t_close > st.closed_ms:
                if st.parent.eligibility_state == "CLOSED":
                    # a traded parent: cooldown = position closed AND >= 60 min since the first signal
                    # AND two completed 5m closes back inside the original box (a new box alone is
                    # never enough)
                    c = float(b5.close[i])
                    if st.box_low <= c <= st.box_high:
                        st.inside_closes += 1
                    if (
                        st.inside_closes >= COOLDOWN_INSIDE_BARS
                        and t_close - st.parent.first_setup_time_ms >= COOLDOWN_MIN * MS_1M
                    ):
                        state[s] = None
                        st = None
                elif bx is not None and bx.hour_open_ms != st.box_hour_ms:
                    # a setup that never became a position ended with its cancel / rejection; the same
                    # frozen box cannot spawn a second parent (later breakouts of it are repeats), a
                    # later box can (INTERPRETATION, recorded in the manifest)
                    state[s] = None
                    st = None
            # ---- breakout pattern: two consecutive closes beyond the box frozen at the first bar
            if bx is None:
                continue
            lvl = bx.high if s > 0 else bx.low
            if not (s * (float(b5.close[i - 1]) - lvl) > 0 and s * (float(b5.close[i]) - lvl) > 0):
                continue
            if st is not None:  # same underlying move: a repeated trigger, never a new parent
                st.parent.add_repeat(t_close)
                continue
            er = float(inp.er[i])
            side = "LONG" if s > 0 else "SHORT"
            p = ParentEpisode(
                parent_id=f"E1-{side[0]}-{t_close}",
                engine="E1",
                labels=["E1", side, regime(er)],
                side=side,
                first_setup_time_ms=t_close,
                receipt_time_ms=None,
                times=EventTimes.historical_bar(t_close),
                frozen_levels={
                    "box_high": bx.high,
                    "box_low": bx.low,
                    "box_vwap": bx.vwap,
                    "box_hour_ms": float(bx.hour_open_ms),
                    "atr_setup": float(inp.atr[i]),
                },
                regime=regime(er),
                er=er,
            )
            out.append(p)
            st = _Open(p, bx.high, bx.low, bx.hour_open_ms, None)
            state[s] = st
            if not math.isfinite(er):
                p.mark_rejected("REGIME_UNAVAILABLE")
            elif er <= ER_RANGE_MAX:
                p.mark_rejected("REGIME_NOT_DIRECTIONAL")
            # ---- retest search over the next three completed 5m bars (recorded even when rejected)
            k_retest: int | None = None
            cancel: str | None = None
            for k in range(i + 1, min(i + 1 + RETEST_BARS, len(b5))):
                if not b5.contiguous(k - 1, k):
                    cancel = "CANCEL_DATA_GAP"
                    break
                ck, hk, lk = float(b5.close[k]), float(b5.high[k]), float(b5.low[k])
                if s * (ck - lvl) <= 0:
                    cancel = "CANCEL_CLOSE_INSIDE_BEFORE_RETEST"
                    break
                touched = (lk <= lvl) if s > 0 else (hk >= lvl)
                if touched:
                    k_retest = k
                    break
            if k_retest is None:
                p.mark_rejected(cancel or "CANCEL_NO_RETEST_15M")
                st.closed_ms = t_close
                continue
            k = k_retest
            atr_k = float(inp.atr[k])
            retest_close_ms = int(b5.close_ms[k])
            p.notes["retest_close_ms"] = retest_close_ms
            p.frozen_levels["retest_extreme"] = float(b5.low[k]) if s > 0 else float(b5.high[k])
            if not math.isfinite(atr_k) or atr_k <= 0:
                p.mark_rejected("ATR_UNAVAILABLE")
                st.closed_ms = retest_close_ms
                continue
            ent = benchmark_entry(b1, retest_close_ms)
            if ent is None:
                p.mark_rejected("ENTRY_PRINT_UNAVAILABLE")
                st.closed_ms = retest_close_ms
                continue
            stop = (
                (float(b5.low[k]) - STOP_ATR_BUFFER * atr_k)
                if s > 0
                else (float(b5.high[k]) + STOP_ATR_BUFFER * atr_k)
            )
            d_bps = stop_distance_bps(s, ent.price, stop)
            p.entry_benchmark, p.stop, p.stop_bps = ent.price, stop, d_bps
            p.times = EventTimes(
                exchange_event_time=retest_close_ms,
                local_receive_time=None,
                processing_time=None,
                decision_time=retest_close_ms,
                benchmark_entry_time=ent.time_ms,
                executable_entry_time=None,
            )
            rej = stop_reject(d_bps)
            if rej:
                p.mark_rejected(rej)
            if p.eligibility_state == "REJECTED":
                st.closed_ms = retest_close_ms
                continue
            p.eligibility_state = "ELIGIBLE"
            dist = s * (ent.price - stop)
            target = ent.price + s * TARGET_R * dist
            p.target = target
            res = replay_exit(
                b1,
                c5_by_close,
                s,
                ent,
                stop,
                target,
                lvl,
                "below" if s > 0 else "above",
                MAX_HOLD_MIN,
            )
            p.exit = res.exit
            p.mfe_bps, p.mae_bps, p.hold_min = res.mfe_bps, res.mae_bps, res.hold_min
            if res.exit.price is not None and res.exit.time_ms is not None:
                g = gross_bps(s, ent.price, res.exit.price)
                f = funding_bps(s, ent.time_ms, res.exit.time_ms, inp.fund_t, inp.fund_rate)
                p.gross_bps = g
                p.cost_bps = inp.cost_bps
                p.notes["funding_bps_proxy"] = f
                p.net_bps = benchmark_net_bps(g, inp.cost_bps, f)
                p.net_r = r_multiple(p.net_bps, d_bps)
                p.eligibility_state = "CLOSED"
                st.closed_ms = res.exit.time_ms
            else:
                p.mark_rejected("EXIT_UNRESOLVED")
                st.closed_ms = retest_close_ms
    return out
