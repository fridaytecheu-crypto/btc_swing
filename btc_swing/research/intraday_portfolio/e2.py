"""E2 — Failed Auction Into Established Range (frozen research specification).

Context: ER <= 0.25 (range regime). Frozen at the sweep bar: the previous completed 1h box high, low and
VWAP. LONG: (1) a 5m bar trades below the frozen box low by >= 0.25 ATR and (2) closes back inside the
box; (3) the NEXT 5m bar also closes inside and does not make a lower low (one-bar confirmation, expires at
once if it fails); (4) entry at the next executable ask (archive: first print of the next minute). Before
accepting: distance entry -> frozen VWAP >= 40 bps AND >= 1.5 x the initial stop distance. Stop = sweep
low - 0.25 ATR (SHORT mirror above the box high). Stop distance outside [20, 150] bps rejects. If the
VWAP target is already reached at the first executable print there is NO retroactive fill. Exit = earliest
of frozen box VWAP, stop, invalidation (completed 5m close outside the swept boundary again), 60 minutes.
One trade per box excursion: a new excursion needs a VWAP touch, two completed 5m closes inside the box and
>= 30 minutes from the original trigger; earlier sweeps are repeated triggers of the parent.

INTERPRETATION (recorded in the manifest, not a parameter): the new-excursion rule governs a parent that
became a position. A sweep rejected before entry ends its excursion at that time; further sweeps of the
SAME frozen box are repeats of that parent, a sweep of a later box is a new candidate."""

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

SWEEP_ATR = 0.25
VWAP_MIN_BPS = 40.0
VWAP_MIN_STOP_MULT = 1.5
MAX_HOLD_MIN = 60
NEW_EXCURSION_MIN = 30
NEW_EXCURSION_INSIDE_BARS = 2


@dataclass
class E2Inputs:
    b5: Bars
    b1: Bars
    atr: np.ndarray
    er: np.ndarray
    boxes: dict[int, HourBox]
    fund_t: np.ndarray
    fund_rate: np.ndarray
    cost_bps: float = BASE_COST_BPS


@dataclass
class _Excursion:
    parent: ParentEpisode
    box_high: float
    box_low: float
    box_vwap: float
    box_hour_ms: int
    vwap_touched: bool = False
    inside_closes: int = 0


def _box_for(inp: E2Inputs, open_ms: int) -> HourBox | None:
    bx = inp.boxes.get(int(open_ms // MS_1H * MS_1H))
    return bx if bx is not None and bx.available else None


def run_e2(inp: E2Inputs) -> list[ParentEpisode]:
    b5, b1 = inp.b5, inp.b1
    c5_by_close = {int(b5.close_ms[i]): float(b5.close[i]) for i in range(len(b5))}
    out: list[ParentEpisode] = []
    state: dict[int, _Excursion | None] = {1: None, -1: None}
    for i in range(len(b5) - 1):
        t_close = int(b5.close_ms[i])
        bx = _box_for(inp, int(b5.open_ms[i]))
        for s in (1, -1):
            ex = state[s]
            # ---- excursion bookkeeping: VWAP touch, closes inside, time since the original trigger
            if ex is not None and t_close > ex.parent.first_setup_time_ms:
                h, lo, c = float(b5.high[i]), float(b5.low[i]), float(b5.close[i])
                if (s > 0 and h >= ex.box_vwap) or (s < 0 and lo <= ex.box_vwap):
                    ex.vwap_touched = True
                if ex.box_low <= c <= ex.box_high:
                    ex.inside_closes += 1
                traded = ex.parent.eligibility_state == "CLOSED"
                if traded and (
                    ex.vwap_touched
                    and ex.inside_closes >= NEW_EXCURSION_INSIDE_BARS
                    and t_close - ex.parent.first_setup_time_ms >= NEW_EXCURSION_MIN * MS_1M
                    and (ex.parent.exit is None or t_close > int(ex.parent.exit.time_ms or 0))
                ):
                    state[s] = None
                    ex = None
                elif not traded and bx is not None and bx.hour_open_ms != ex.box_hour_ms:
                    # rejected before entry: the excursion ends with the rejection; the same frozen box
                    # cannot spawn a second parent (repeats), a later box can (INTERPRETATION)
                    state[s] = None
                    ex = None
            atr_i = float(inp.atr[i])
            if bx is None or not math.isfinite(atr_i) or atr_i <= 0:
                continue
            boundary = bx.low if s > 0 else bx.high
            swept = (
                (float(b5.low[i]) <= boundary - SWEEP_ATR * atr_i)
                if s > 0
                else (float(b5.high[i]) >= boundary + SWEEP_ATR * atr_i)
            )
            closed_inside = bx.low <= float(b5.close[i]) <= bx.high
            if not (swept and closed_inside):
                continue
            if ex is not None:
                ex.parent.add_repeat(t_close)
                continue
            er = float(inp.er[i])
            side = "LONG" if s > 0 else "SHORT"
            p = ParentEpisode(
                parent_id=f"E2-{side[0]}-{t_close}",
                engine="E2",
                labels=["E2", side, regime(er)],
                side=side,
                first_setup_time_ms=t_close,
                receipt_time_ms=None,
                times=EventTimes.historical_bar(t_close),
                frozen_levels={
                    "box_high": bx.high,
                    "box_low": bx.low,
                    "box_vwap": bx.vwap,
                    "box_hour_ms": float(bx.hour_open_ms),
                    "atr_setup": atr_i,
                    "sweep_extreme": float(b5.low[i]) if s > 0 else float(b5.high[i]),
                },
                regime=regime(er),
                er=er,
            )
            out.append(p)
            ex = _Excursion(p, bx.high, bx.low, bx.vwap, bx.hour_open_ms)
            state[s] = ex
            if not math.isfinite(er):
                p.mark_rejected("REGIME_UNAVAILABLE")
            elif er > ER_RANGE_MAX:
                p.mark_rejected("REGIME_NOT_RANGE")
            # ---- one-bar confirmation
            k = i + 1
            if not b5.contiguous(i, k):
                p.mark_rejected("CANCEL_DATA_GAP")
                continue
            ck = float(b5.close[k])
            inside_k = bx.low <= ck <= bx.high
            no_new_extreme = (
                (float(b5.low[k]) >= float(b5.low[i]))
                if s > 0
                else (float(b5.high[k]) <= float(b5.high[i]))
            )
            if not (inside_k and no_new_extreme):
                p.mark_rejected("CONFIRMATION_FAILED")
                continue
            conf_close_ms = int(b5.close_ms[k])
            ent = benchmark_entry(b1, conf_close_ms)
            if ent is None:
                p.mark_rejected("ENTRY_PRINT_UNAVAILABLE")
                continue
            sweep_ext = float(b5.low[i]) if s > 0 else float(b5.high[i])
            stop = sweep_ext - s * STOP_ATR_BUFFER * atr_i
            d_bps = stop_distance_bps(s, ent.price, stop)
            vwap_bps = s * (bx.vwap - ent.price) / ent.price * 1e4
            p.entry_benchmark, p.stop, p.stop_bps, p.target = ent.price, stop, d_bps, bx.vwap
            p.notes["vwap_distance_bps"] = vwap_bps
            p.times = EventTimes(conf_close_ms, None, None, conf_close_ms, ent.time_ms, None)
            rej = stop_reject(d_bps)
            if rej:
                p.mark_rejected(rej)
            if vwap_bps <= 0:
                p.mark_rejected("TARGET_ALREADY_REACHED_NO_RETROACTIVE_FILL")
            elif vwap_bps < VWAP_MIN_BPS:
                p.mark_rejected("VWAP_DISTANCE_LT_40BPS")
            elif math.isfinite(d_bps) and vwap_bps < VWAP_MIN_STOP_MULT * d_bps:
                p.mark_rejected("VWAP_DISTANCE_LT_1_5_STOP")
            if p.eligibility_state == "REJECTED":
                continue
            p.eligibility_state = "ELIGIBLE"
            res = replay_exit(
                b1,
                c5_by_close,
                s,
                ent,
                stop,
                bx.vwap,
                boundary,
                "below" if s > 0 else "above",
                MAX_HOLD_MIN,
            )
            if res.exit.reason == "TARGET":
                res.exit.reason = "VWAP"
            p.exit = res.exit
            p.mfe_bps, p.mae_bps, p.hold_min = res.mfe_bps, res.mae_bps, res.hold_min
            if res.exit.price is not None and res.exit.time_ms is not None:
                g = gross_bps(s, ent.price, res.exit.price)
                f = funding_bps(s, ent.time_ms, res.exit.time_ms, inp.fund_t, inp.fund_rate)
                p.gross_bps, p.cost_bps = g, inp.cost_bps
                p.notes["funding_bps_proxy"] = f
                p.net_bps = benchmark_net_bps(g, inp.cost_bps, f)
                p.net_r = r_multiple(p.net_bps, d_bps)
                p.eligibility_state = "CLOSED"
            else:
                p.mark_rejected("EXIT_UNRESOLVED")
    return out
