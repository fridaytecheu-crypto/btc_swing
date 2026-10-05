"""Chronological 5-minute backtest with persistent setup episodes.

Per completed 5m bar i (decision time T = close_time[i]):
  1. fill the entry triggered at bar i-1 at open[i] (+ adverse slippage, taker fee)
  2. evaluate exits on bar i's path: liquidation, then stop (gap-aware), then TP1/TP2, time cap;
     stop-first when a bar touches both stop and target
  3. apply funding events in (close[i-1], close[i]] to the open position
  4. decision at T from the PIT view (bars with close_time <= T only): trailing-stop update for an
     open position, otherwise advance the setup episode / open a new one; size a TRIGGERED entry
  5. mark equity at UTC midnight for the daily account-return series
Nothing in step 4 can see bar i+1; the fill in step 1 is the first price after the decision.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl

from btc_swing.backtest.ledger import PartialExit, Position
from btc_swing.core.config import BtcStrategyConfig
from btc_swing.core.enums import EpisodeState, ExitReason, Regime, Timeframe
from btc_swing.core.hashing import round_floats, stable_hash
from btc_swing.core.versions import (
    BACKTEST_VERSION,
    COST_MODEL_VERSION,
    EPISODE_RULE_VERSION,
    FEATURE_SET_VERSION,
    REGIME_RULE_VERSION,
    RISK_RULE_VERSION,
    SETUP_RULE_VERSION,
    STRATEGY_NAME,
    STRATEGY_VERSION,
    code_version,
)
from btc_swing.episodes.state_machine import Episode, EpisodeManager
from btc_swing.execution.costs import CostModel, FundingSchedule
from btc_swing.features.context import AuxSeries
from btc_swing.features.view import MultiTfSeries
from btc_swing.regime.classifier import classify_regime
from btc_swing.risk.sizing import Sizing, size_position
from btc_swing.setups.registry import build_detectors

log = logging.getLogger(__name__)
DAY_MS = 86_400_000


@dataclass
class BacktestResult:
    manifest: dict[str, Any]
    trades: pl.DataFrame
    episodes: pl.DataFrame
    decisions: pl.DataFrame
    daily_equity: pl.DataFrame
    result_hash: str
    shadow: pl.DataFrame = field(default_factory=pl.DataFrame)


@dataclass
class _Pending:
    episode: Episode
    sizing: Sizing
    atr_setup: float
    features: dict[str, float]
    mark_ref: float


@dataclass
class _Journal:
    t_ms: list[int] = field(default_factory=list)
    regime: list[str] = field(default_factory=list)
    episode_id: list[int | None] = field(default_factory=list)
    episode_state: list[str] = field(default_factory=list)
    family: list[str | None] = field(default_factory=list)
    close: list[float] = field(default_factory=list)
    position_open: list[bool] = field(default_factory=list)
    equity_mtm: list[float] = field(default_factory=list)


class BacktestEngine:
    def __init__(
        self,
        cfg: BtcStrategyConfig,
        bars_5m: pl.DataFrame,
        funding: pl.DataFrame | None,
        data_hashes: dict[str, str] | None = None,
        aux: AuxSeries | None = None,
        series: MultiTfSeries | None = None,
    ) -> None:
        self.cfg = cfg
        self.bars = bars_5m.sort("open_time_ms")
        self.series = series or MultiTfSeries(
            self.bars, cfg.indicators, cfg.regime.trend_slope_bars
        )
        self.funding = FundingSchedule(funding if cfg.costs.apply_funding else None)
        self.costs = CostModel(cfg.costs)
        self.data_hashes = data_hashes or {}
        self.aux = aux or AuxSeries.build(funding, None, None, None, cfg.data.latency_minutes)
        self.mark_o, self.mark_h, self.mark_l, self.mark_c = self.aux.mark_aligned(
            self.series.base.close_ms
        )
        self.has_mark = bool(np.isfinite(self.mark_c).any())

    # ------------------------------------------------------------------ run
    def run(
        self, start_ms: int, end_ms: int, notes: dict[str, Any] | None = None
    ) -> BacktestResult:
        cfg = self.cfg
        base = self.series.base
        n = len(base)
        episodes = EpisodeManager(cfg.episode, build_detectors(cfg))
        journal = _Journal()
        trades: list[Position] = []
        daily: list[tuple[int, float, float]] = []
        equity = cfg.risk.initial_equity
        position: Position | None = None
        pending: _Pending | None = None
        next_trade_id = 1
        i0 = int(np.searchsorted(base.close_ms, start_ms, side="left"))
        i1 = int(np.searchsorted(base.close_ms, end_ms, side="right"))
        started_at = datetime.now(UTC)
        regime_counts: dict[str, int] = {r.value: 0 for r in Regime}
        last_day = int(base.close_ms[i0]) // DAY_MS if i0 < n else 0

        for i in range(i0, min(i1, n)):
            t = int(base.close_ms[i])
            o, h, lo, c = (
                float(base.open[i]),
                float(base.high[i]),
                float(base.low[i]),
                float(base.close[i]),
            )
            # 1. pending entry -> fill at this bar's open
            if pending is not None and position is None:
                position = self._open_position(
                    pending, next_trade_id, i, int(base.open_ms[i]), o, equity
                )
                episodes.mark_active(pending.episode, i, t, position.trade_id)
                next_trade_id += 1
                pending = None
            # 2. exits on this bar's path
            if position is not None:
                closed = self._process_bar(position, i, t, o, h, lo, c)
                # 3. funding
                if position.is_open:
                    prev_t = int(base.close_ms[i - 1]) if i > 0 else t - 300_000
                    for _ft, rate in self.funding.events_between(prev_t, t):
                        position.funding += FundingSchedule.payment(
                            rate, position.qty_open, c, position.side
                        )
                        position.funding_events += 1
                if closed or not position.is_open:
                    equity += position.net_pnl
                    trades.append(position)
                    ep = episodes.current
                    if ep is not None and ep.trade_id == position.trade_id:
                        episodes.mark_closed(ep, i, t, position.exits[-1].reason.value)
                    position = None
                elif position.pending_stop is not None:
                    position.stop = position.pending_stop
                    position.stop_moved = True
                    position.pending_stop = None
            # 4. decision at T
            view = self.series.view_at(t)
            reg = classify_regime(view, cfg.regime)
            regime_counts[reg.regime.value] += 1
            if position is not None:
                self._update_trail(position, view, c)
            else:
                action = episodes.step(view, reg.regime, i)
                ep_now = action.episode
                if ep_now is not None and ep_now.opened_bar == i and not ep_now.features:
                    ep_now.features = self.aux.snapshot(t, view, self.series)
                if action.kind == "ENTER" and action.episode is not None:
                    pending = self._size(action.episode, view, c, equity, i, t, episodes)
            ep = episodes.current
            unreal = position.unrealised(c) if position is not None else 0.0
            journal.t_ms.append(t)
            journal.regime.append(reg.regime.value)
            journal.episode_id.append(ep.episode_id if ep else None)
            journal.episode_state.append(ep.state.value if ep else EpisodeState.NO_SETUP.value)
            journal.family.append(ep.plan.family.value if ep else None)
            journal.close.append(c)
            journal.position_open.append(position is not None)
            journal.equity_mtm.append(equity + unreal)
            # 5. daily mark at UTC midnight
            day = t // DAY_MS
            if day > last_day:
                daily.append((t, equity + unreal, equity))
                last_day = day

        # end of data: close any open position at the last close
        last_i = min(i1, n) - 1
        if position is not None and last_i >= 0:
            t = int(base.close_ms[last_i])
            self._exit(
                position,
                last_i,
                t,
                float(base.close[last_i]),
                position.qty_open,
                ExitReason.END_OF_DATA,
                stop_like=True,
            )
            equity += position.net_pnl
            trades.append(position)
            ep = episodes.current
            if ep is not None:
                episodes.mark_closed(ep, last_i, t, ExitReason.END_OF_DATA.value)
        if episodes.current is not None:
            episodes.current.set_state(
                EpisodeState.INVALIDATED, last_i, int(base.close_ms[last_i]), "END_OF_DATA"
            )
            episodes.closed.append(episodes.current)
            episodes.current = None

        trades_df = pl.DataFrame([p.to_row() for p in trades]) if trades else pl.DataFrame()
        episodes_df = (
            pl.DataFrame([e.as_row() for e in episodes.closed])
            if episodes.closed
            else pl.DataFrame()
        )
        decisions_df = pl.DataFrame(
            {
                "t_ms": journal.t_ms,
                "regime": journal.regime,
                "episode_id": journal.episode_id,
                "episode_state": journal.episode_state,
                "family": journal.family,
                "close": journal.close,
                "position_open": journal.position_open,
                "equity_mtm": journal.equity_mtm,
            },
            schema_overrides={"episode_id": pl.Int64, "family": pl.Utf8},
        )
        daily_df = pl.DataFrame(
            {
                "t_ms": [d[0] for d in daily],
                "equity_mtm": [d[1] for d in daily],
                "equity_realised": [d[2] for d in daily],
            },
            schema={"t_ms": pl.Int64, "equity_mtm": pl.Float64, "equity_realised": pl.Float64},
        )
        result_hash = stable_hash(
            {
                "trades": round_floats(trades_df.to_dicts(), 6),
                "episodes": round_floats(episodes_df.to_dicts(), 6),
                "n_decisions": decisions_df.height,
                "final_equity": round(equity, 6),
            }
        )
        n_eval = min(i1, n) - i0
        manifest = {
            "strategy_name": STRATEGY_NAME,
            "strategy_version": STRATEGY_VERSION,
            "config_hash": cfg.config_hash,
            "code_version": code_version(),
            "rule_versions": {
                "features": FEATURE_SET_VERSION,
                "regime": REGIME_RULE_VERSION,
                "setups": SETUP_RULE_VERSION,
                "episodes": EPISODE_RULE_VERSION,
                "risk": RISK_RULE_VERSION,
                "costs": COST_MODEL_VERSION,
                "backtest": BACKTEST_VERSION,
            },
            "information_mode": cfg.backtest.information_mode,
            "provider": cfg.data.provider,
            "latency_minutes": cfg.data.latency_minutes,
            "data_hashes": dict(self.data_hashes),
            "period_start_ms": start_ms,
            "period_end_ms": end_ms,
            "n_5m_evaluations": n_eval,
            "n_days": n_eval / 288.0,
            "regime_bar_counts": regime_counts,
            "initial_equity": cfg.risk.initial_equity,
            "final_equity": equity,
            "n_trades": len(trades),
            "n_episodes": len(episodes.closed),
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "result_hash": result_hash,
            "notes": notes or {},
        }
        shadow_df = pl.DataFrame(episodes.shadow) if episodes.shadow else pl.DataFrame()
        manifest["n_shadow_detections"] = shadow_df.height
        return BacktestResult(
            manifest, trades_df, episodes_df, decisions_df, daily_df, result_hash, shadow_df
        )

    # ------------------------------------------------------------------ sizing / entry
    def _size(
        self,
        ep: Episode,
        view: Any,
        close: float,
        equity: float,
        bar: int,
        t: int,
        episodes: EpisodeManager,
    ) -> _Pending | None:
        cfg = self.cfg
        atr_liq = (
            view.ind(cfg.risk.liquidation_atr_tf, "atr")
            if view.warm(cfg.risk.liquidation_atr_tf)
            else math.nan
        )
        sizing_equity = equity if cfg.risk.compounding else cfg.risk.initial_equity
        sizing = size_position(
            sizing_equity, close, ep.plan.stop_price, ep.plan.side, atr_liq, cfg.risk
        )
        if not sizing.accepted:
            episodes.mark_risk_rejected(ep, bar, t, sizing.reason)
            return None
        feats = self.aux.snapshot(t, view, self.series)
        return _Pending(ep, sizing, ep.plan.atr_setup_tf, feats, feats.get("mark_price", math.nan))

    def _open_position(
        self, p: _Pending, trade_id: int, bar: int, open_ms: int, open_price: float, equity: float
    ) -> Position:
        cfg = self.cfg
        plan = p.episode.plan
        side = plan.side
        fill = self.costs.entry_fill(open_price, side)
        qty = p.sizing.qty
        stop = plan.stop_price
        dist = side.sign * (fill - stop)
        if (
            dist <= 0
        ):  # the open gapped through the stop; the trade is stopped immediately by _process_bar
            dist = max(p.sizing.stop_distance, 1e-9)
        tp1 = fill + side.sign * cfg.exits.tp1_r * dist
        tp2 = fill + side.sign * cfg.exits.tp2_r * dist
        from btc_swing.risk.sizing import liquidation_price

        liq = liquidation_price(fill, side, p.sizing.leverage, cfg.risk.maintenance_margin_rate)
        pos = Position(
            trade_id=trade_id,
            episode_id=p.episode.episode_id,
            family=plan.family,
            side=side,
            regime_at_entry=p.episode.regime_at_detection,
            entry_bar=bar,
            entry_ms=open_ms,
            entry_price=fill,
            sizing=p.sizing,
            qty_initial=qty,
            qty_open=qty,
            stop=stop,
            initial_stop=stop,
            stop_reason=plan.stop_reason,
            stop_distance=dist,
            stop_distance_atr=dist / p.atr_setup if p.atr_setup > 0 else math.nan,
            tp1=tp1,
            tp2=tp2,
            structural_target=plan.structural_target,
            liq_price=liq,
            equity_at_entry=equity,
            r_levels=list(cfg.exits.evaluate_r_levels),
            entry_ref_price=open_price,
            mark_price_at_decision=p.mark_ref,
            liquidation_basis="mark" if self.has_mark else "traded",
            features=p.features,
        )
        pos.entry_fee = self.costs.fee(qty * fill)
        return pos

    # ------------------------------------------------------------------ exits
    def _exit(
        self,
        pos: Position,
        bar: int,
        t: int,
        price: float,
        qty: float,
        reason: ExitReason,
        stop_like: bool,
    ) -> None:
        fill = self.costs.stop_fill(price, pos.side) if stop_like else self.costs.target_fill(price)
        fee = self.costs.fee(qty * fill, maker=(not stop_like) and self.costs.target_is_maker())
        pos.exits.append(PartialExit(bar, t, fill, qty, reason, fee, ref_price=price))
        pos.qty_open -= qty
        if pos.qty_open < 1e-12:
            pos.qty_open = 0.0

    def _process_bar(
        self, pos: Position, i: int, t: int, o: float, h: float, lo: float, c: float
    ) -> bool:
        """Apply this bar's path to the position. Returns True when the position is fully closed."""
        cfg = self.cfg
        s = pos.side.sign
        pos.update_path(i, h, lo)
        adverse_extreme = lo if s > 0 else h
        favourable_extreme = h if s > 0 else lo
        # liquidation on MARK price when available (exchange rule), else on traded extremes
        mark_adverse = (self.mark_l[i] if s > 0 else self.mark_h[i]) if self.has_mark else math.nan
        liq_adverse = mark_adverse if not math.isnan(mark_adverse) else adverse_extreme
        if s * (liq_adverse - pos.liq_price) <= 0:
            pos.exits.append(
                PartialExit(
                    i, t, pos.liq_price, pos.qty_open, ExitReason.LIQUIDATION, 0.0, pos.liq_price
                )
            )
            pos.qty_open = 0.0
            # isolated margin: the loss is capped at the margin posted
            lost = pos.sizing.margin
            pos.exits[-1].price = (
                pos.entry_price - s * (lost - pos.fees + pos.funding) / pos.qty_initial
            )
            return True
        # stop (gap-aware, evaluated before targets)
        if s * (adverse_extreme - pos.stop) <= 0:
            px = o if s * (o - pos.stop) <= 0 else pos.stop
            reason = ExitReason.TRAIL if pos.stop_moved else ExitReason.STOP
            self._exit(pos, i, t, px, pos.qty_open, reason, stop_like=True)
            return True
        # targets
        if not pos.tp1_done and s * (favourable_extreme - pos.tp1) >= 0:
            q = min(pos.qty_open, pos.qty_initial * cfg.exits.tp1_frac)
            pos.tp1_done = True
            if q > 0:
                self._exit(pos, i, t, pos.tp1, q, ExitReason.TP1, stop_like=False)
            if cfg.exits.breakeven_after_tp1:
                be = pos.entry_price
                if s * (be - pos.stop) > 0:
                    pos.pending_stop = be
            pos.trail_active = True
            if not pos.is_open:
                return True
        if pos.tp1_done and not pos.tp2_done and s * (favourable_extreme - pos.tp2) >= 0:
            q = min(pos.qty_open, pos.qty_initial * cfg.exits.tp2_frac)
            pos.tp2_done = True
            if q > 0:
                self._exit(pos, i, t, pos.tp2, q, ExitReason.TP2, stop_like=False)
            if not pos.is_open:
                return True
        # time cap
        if t - pos.entry_ms >= cfg.exits.max_hold_hours * 3_600_000:
            self._exit(pos, i, t, c, pos.qty_open, ExitReason.TIME_LIMIT, stop_like=True)
            return True
        return False

    def _update_trail(self, pos: Position, view: Any, close: float) -> None:
        cfg = self.cfg
        if not pos.trail_active or cfg.exits.trail_method == "none":
            return
        tf: Timeframe = cfg.exits.trail_tf
        if not view.warm(tf):
            return
        s = pos.side.sign
        swing = view.ind(tf, "swing_low" if s > 0 else "swing_high")
        a = view.ind(tf, "atr")
        if math.isnan(swing) or math.isnan(a):
            return
        cand = swing - s * cfg.exits.trail_atr_buffer * a
        if s * (cand - pos.stop) > 0 and s * (close - cand) > 0:
            pos.pending_stop = cand
