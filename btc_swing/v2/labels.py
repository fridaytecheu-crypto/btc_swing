"""Trade-quality labels from the FROZEN V1 execution engine.

Every risk-accepted candidate is simulated independently (no slot, fixed research equity) from
the next 5m open: V1 entry fill and slippage, V1 stop / TP1 / TP2 / breakeven / structural trail /
time cap, mark-price liquidation, V1 fees and archive funding. The engine's own bar-path methods
are reused (the same ones the V1 null benchmark reuses); nothing is re-implemented.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import polars as pl

from btc_swing.backtest.engine import BacktestEngine
from btc_swing.backtest.ledger import Position
from btc_swing.core.enums import ExitReason
from btc_swing.execution.costs import FundingSchedule
from btc_swing.risk.sizing import liquidation_price
from btc_swing.v2.candidates import Candidate

log = logging.getLogger(__name__)

LABEL_COLUMNS = [
    "trade_id",
    "entry_ms",
    "exit_ms",
    "holding_hours",
    "entry_price",
    "qty",
    "notional",
    "leverage",
    "margin",
    "initial_stop",
    "stop_distance",
    "stop_distance_pct",
    "stop_distance_atr",
    "liquidation_distance_pct",
    "stop_to_liquidation_ratio",
    "risk_amount",
    "risk_frac",
    "equity_at_entry",
    "fees",
    "slippage",
    "funding",
    "funding_events",
    "gross_pnl",
    "POSITION_PNL",
    "BTC_RETURN",
    "RETURN_ON_MARGIN",
    "ACCOUNT_RETURN",
    "R_MULTIPLE",
    "R_MULTIPLE_GROSS",
    "MFE_R",
    "MAE_R",
    "MFE_PCT",
    "MAE_PCT",
    "exit_reason",
    "tp1_hit",
    "tp2_hit",
    "stop_moved",
    "structural_target_r",
    "cf_hit_1R",
    "cf_hit_1.5R",
    "cf_hit_2R",
    "cf_hit_3R",
    "regime_at_entry",
    "side",
    "family",
]


def simulate_candidate(eng: BacktestEngine, cand: Candidate) -> Position | None:
    """Open the candidate's V1 position at the next 5m open and run the V1 exit engine to the end."""
    cfg = eng.cfg
    base = eng.series.base
    n = len(base)
    bar = cand.trigger_bar + 1
    if bar >= n or not cand.sizing.accepted:
        return None
    plan = cand.plan
    side = plan.side
    o = float(base.open[bar])
    fill = eng.costs.entry_fill(o, side)
    qty = cand.sizing.qty
    stop = plan.stop_price
    dist = side.sign * (fill - stop)
    if dist <= 0:
        dist = max(cand.sizing.stop_distance, 1e-9)
    pos = Position(
        trade_id=cand.candidate_id,
        episode_id=cand.episode_id,
        family=plan.family,
        side=side,
        regime_at_entry=cand.regime_at_detection,
        entry_bar=bar,
        entry_ms=int(base.open_ms[bar]),
        entry_price=fill,
        sizing=cand.sizing,
        qty_initial=qty,
        qty_open=qty,
        stop=stop,
        initial_stop=stop,
        stop_reason=plan.stop_reason,
        stop_distance=dist,
        stop_distance_atr=dist / plan.atr_setup_tf if plan.atr_setup_tf > 0 else math.nan,
        tp1=fill + side.sign * cfg.exits.tp1_r * dist,
        tp2=fill + side.sign * cfg.exits.tp2_r * dist,
        structural_target=plan.structural_target,
        liq_price=liquidation_price(
            fill, side, cand.sizing.leverage, cfg.risk.maintenance_margin_rate
        ),
        equity_at_entry=cfg.risk.initial_equity,
        r_levels=list(cfg.exits.evaluate_r_levels),
        entry_ref_price=o,
        mark_price_at_decision=cand.aux.get("mark_price", math.nan),
        liquidation_basis="mark" if eng.has_mark else "traded",
        regime_at_trigger=cand.regime_at_trigger.value,
    )
    pos.entry_fee = eng.costs.fee(qty * fill)
    for i in range(bar, n):
        t = int(base.close_ms[i])
        c = float(base.close[i])
        closed = eng._process_bar(
            pos, i, t, float(base.open[i]), float(base.high[i]), float(base.low[i]), c
        )
        if pos.is_open:
            prev_t = int(base.close_ms[i - 1]) if i > 0 else t - 300_000
            for _ft, rate in eng.funding.events_between(prev_t, t):
                pos.funding += FundingSchedule.payment(rate, pos.qty_open, c, side)
                pos.funding_events += 1
        if closed or not pos.is_open:
            return pos
        if pos.pending_stop is not None:
            pos.stop = pos.pending_stop
            pos.stop_moved = True
            pos.stop_source = pos.pending_stop_source or pos.stop_source
            pos.pending_stop = None
            pos.pending_stop_source = None
        eng._update_trail(pos, eng.series.view_at(t), c)
    last = n - 1
    eng._exit(
        pos,
        last,
        int(base.close_ms[last]),
        float(base.close[last]),
        pos.qty_open,
        ExitReason.END_OF_DATA,
        stop_like=True,
    )
    return pos


def label_candidates(eng: BacktestEngine, cands: list[Candidate]) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for cand in cands:
        pos = simulate_candidate(eng, cand)
        if pos is None:
            continue
        r = pos.to_row()
        row = {k: r.get(k) for k in LABEL_COLUMNS}
        row["candidate_id"] = cand.candidate_id
        row["label_complete"] = r["exit_reason"] != ExitReason.END_OF_DATA.value
        row["target_before_stop"] = bool(r["tp1_hit"])
        row["stop_before_target"] = (not bool(r["tp1_hit"])) and r["exit_reason"] in (
            ExitReason.STOP.value,
            ExitReason.LIQUIDATION.value,
        )
        rows.append(row)
    log.info("labels: %d of %d candidates simulated", len(rows), len(cands))
    return pl.DataFrame(rows) if rows else pl.DataFrame()
