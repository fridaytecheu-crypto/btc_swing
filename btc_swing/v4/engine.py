"""V4 chronological 5-minute engine: event-driven episodes (generic lifecycle, one manager per
family/side), one net BTC exposure, volatility-floored structural stops finalised at the trigger
bar, generic V4 exits (TP1 1R / TP2 2R / 1H structural trail / 48 h cap), V1 ledger, costs,
funding, sizing and liquidation model reused unchanged."""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import numpy as np
import polars as pl

from btc_swing.backtest.ledger import PartialExit, Position
from btc_swing.core.enums import EpisodeState, ExitReason, Regime, SetupFamily, Side, Timeframe
from btc_swing.core.hashing import round_floats, stable_hash
from btc_swing.core.versions import code_version
from btc_swing.episodes.state_machine import Episode, EpisodeManager
from btc_swing.execution.costs import CostModel, FundingSchedule
from btc_swing.features.context import AuxSeries
from btc_swing.features.view import MarketView, MultiTfSeries
from btc_swing.risk.sizing import Sizing, liquidation_price, size_position
from btc_swing.v4.config import (
    V4_EVENT_RULE_VERSION,
    V4_EXIT_RULE_VERSION,
    V4_FEATURE_VERSION,
    V4_VERSION,
    V4Config,
    V4Family,
)
from btc_swing.v4.events import V4Detector, build_v4_detectors
from btc_swing.v4.features import FeatureFrame, V4Regime, build_feature_frame

DAY_MS = 86_400_000


@dataclass
class V4Result:
    manifest: dict[str, Any]
    trades: pl.DataFrame
    episodes: pl.DataFrame
    decisions: pl.DataFrame
    daily_equity: pl.DataFrame
    result_hash: str
    blocked: dict[str, int] = field(default_factory=dict)


@dataclass
class _Pending:
    episode: Episode
    sizing: Sizing
    features: dict[str, float]
    regime: V4Regime
    cost_pct_of_stop: float


@dataclass
class _DayState:
    day: int = -1
    realised: float = 0.0
    full_losses: int = 0
    blocked_today: bool = False


class V4Engine:
    def __init__(
        self,
        cfg: V4Config,
        bars_5m: pl.DataFrame,
        funding: pl.DataFrame | None,
        data_hashes: dict[str, str] | None = None,
        aux: AuxSeries | None = None,
        series: MultiTfSeries | None = None,
        ff: FeatureFrame | None = None,
        safety_overlay: bool = False,
        costs_cfg: Any | None = None,
    ) -> None:
        self.cfg = cfg
        self.bars = bars_5m.sort("open_time_ms")
        self.series = series or MultiTfSeries(self.bars, cfg.indicators, 5)
        self.funding = FundingSchedule(funding if cfg.costs.apply_funding else None)
        self.costs = CostModel(costs_cfg or cfg.costs)
        self.data_hashes = data_hashes or {}
        self.aux = aux or AuxSeries.build(funding, None, None, None, cfg.data.latency_minutes)
        self.ff = ff or build_feature_frame(self.series, self.aux, cfg)
        self.mark_o, self.mark_h, self.mark_l, self.mark_c = self.aux.mark_aligned(
            self.series.base.close_ms
        )
        self.has_mark = bool(np.isfinite(self.mark_c).any())
        self.safety_overlay = safety_overlay

    # ------------------------------------------------------------------ run
    def run(self, start_ms: int, end_ms: int, notes: dict[str, Any] | None = None) -> V4Result:
        cfg = self.cfg
        base = self.series.base
        n = len(base)
        detectors = build_v4_detectors(cfg, self.ff)
        managers: list[tuple[V4Detector, EpisodeManager]] = [
            (d, EpisodeManager(cfg.episode, [d], "CONFIRMED_TRIGGER")) for d in detectors
        ]
        trades: list[Position] = []
        closed_eps: list[dict[str, Any]] = []
        daily: list[tuple[int, float, float]] = []
        equity = cfg.risk.initial_equity
        position: Position | None = None
        pending: _Pending | None = None
        next_trade_id = 1
        next_episode_id = 1
        i0 = int(np.searchsorted(base.close_ms, start_ms, side="left"))
        i1 = int(np.searchsorted(base.close_ms, end_ms, side="right"))
        started_at = datetime.now(UTC)
        blocked = {"BLOCKED_POSITION_OPEN": 0, "BLOCKED_SAFETY": 0, "RISK_REJECTED": 0}
        j_t: list[int] = []
        j_reg: list[str] = []
        j_open: list[bool] = []
        j_eq: list[float] = []
        last_day = int(base.close_ms[i0]) // DAY_MS if i0 < n else 0
        day = _DayState(day=last_day)
        for i in range(i0, min(i1, n)):
            t = int(base.close_ms[i])
            o, h, lo, c = (
                float(base.open[i]),
                float(base.high[i]),
                float(base.low[i]),
                float(base.close[i]),
            )
            d_now = t // DAY_MS
            if d_now != day.day:
                day = _DayState(day=d_now)
            if pending is not None and position is None:
                position = self._open_position(
                    pending, next_trade_id, i, int(base.open_ms[i]), o, equity
                )
                pending.episode.trade_id = position.trade_id
                pending.episode.set_state(EpisodeState.ACTIVE, i, t, "filled")
                next_trade_id += 1
                pending = None
            closed = False
            if position is not None and position.is_open:
                closed = self._process_bar(position, i, t, o, h, lo, c)
                if position.is_open:
                    prev_t = int(base.close_ms[i - 1]) if i > 0 else t - 300_000
                    for _ft, rate in self.funding.events_between(prev_t, t):
                        position.funding += FundingSchedule.payment(
                            rate, position.qty_open, c, position.side
                        )
                        position.funding_events += 1
            if position is not None and (closed or not position.is_open):
                equity += position.net_pnl
                trades.append(position)
                r_mult = position.net_pnl / max(position.sizing.risk_amount, 1e-9)
                day.realised += position.net_pnl
                if r_mult <= cfg.safety_overlay.full_risk_loss_r:
                    day.full_losses += 1
                if (
                    day.full_losses >= cfg.safety_overlay.max_full_risk_losses_per_day
                    or day.realised
                    <= -cfg.safety_overlay.max_daily_loss_frac * cfg.risk.initial_equity
                ):
                    day.blocked_today = True
                self._close_episode(managers, position, i, t, closed_eps)
                position = None
            elif position is not None and position.pending_stop is not None:
                position.stop = position.pending_stop
                position.stop_moved = True
                position.stop_source = position.pending_stop_source or position.stop_source
                position.pending_stop = None
                position.pending_stop_source = None
            view = self.series.view_at(t)
            j = self.ff.idx_at(t)
            reg = self.ff.regime[j] if j >= 0 else V4Regime.UNCLEAR
            if position is not None:
                self._update_trail(position, view, c)
            for _det, mgr in managers:
                action = mgr.step(view, cast(Regime, reg), i)
                ep = action.episode
                if (
                    action.kind == "NONE"
                    and ep is not None
                    and ep.opened_bar == i
                    and ep.state is EpisodeState.WATCH
                    and not ep.features
                ):
                    ep.features = {"_v4_id": float(next_episode_id)}
                    next_episode_id += 1
                if action.kind == "INVALIDATED" and ep is not None:
                    closed_eps.append(self._episode_row(ep))
                    mgr.closed.clear()
                if action.kind != "ENTER" or ep is None:
                    continue
                ep.regime_at_trigger = cast(Regime, reg)
                if position is not None or pending is not None:
                    self._block(mgr, ep, i, t, "BLOCKED_POSITION_OPEN", closed_eps)
                    blocked["BLOCKED_POSITION_OPEN"] += 1
                    continue
                if self.safety_overlay and day.blocked_today:
                    self._block(mgr, ep, i, t, "BLOCKED_SAFETY", closed_eps)
                    blocked["BLOCKED_SAFETY"] += 1
                    continue
                pend = self._size(ep, view, c, equity, i, t, mgr, reg, closed_eps)
                if pend is None:
                    blocked["RISK_REJECTED"] += 1
                    continue
                pending = pend
            unreal = position.unrealised(c) if position is not None else 0.0
            j_t.append(t)
            j_reg.append(reg.value)
            j_open.append(position is not None)
            j_eq.append(equity + unreal)
            if d_now > last_day:
                daily.append((t, equity + unreal, equity))
                last_day = d_now
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
                True,
            )
            equity += position.net_pnl
            trades.append(position)
            self._close_episode(managers, position, last_i, t, closed_eps)
        for _det, mgr in managers:
            if mgr.current is not None:
                mgr.current.set_state(
                    EpisodeState.INVALIDATED, last_i, int(base.close_ms[last_i]), "END_OF_DATA"
                )
                closed_eps.append(self._episode_row(mgr.current))
                mgr.current = None
        trades_df = pl.DataFrame([self._trade_row(p) for p in trades]) if trades else pl.DataFrame()
        episodes_df = pl.DataFrame(closed_eps) if closed_eps else pl.DataFrame()
        decisions_df = pl.DataFrame(
            {"t_ms": j_t, "regime": j_reg, "position_open": j_open, "equity_mtm": j_eq}
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
                "trades": round_floats(trades_df.to_dicts() if trades_df.height else [], 6),
                "episodes": round_floats(episodes_df.to_dicts() if episodes_df.height else [], 6),
                "n_decisions": decisions_df.height,
                "final_equity": round(equity, 6),
            }
        )
        n_eval = min(i1, n) - i0
        manifest = {
            "strategy_name": cfg.strategy_name,
            "strategy_version": V4_VERSION,
            "rule_versions": {
                "features": V4_FEATURE_VERSION,
                "events": V4_EVENT_RULE_VERSION,
                "exits": V4_EXIT_RULE_VERSION,
            },
            "config_hash": cfg.config_hash,
            "code_version": code_version(),
            "data_hashes": dict(self.data_hashes),
            "period_start_ms": start_ms,
            "period_end_ms": end_ms,
            "n_5m_evaluations": n_eval,
            "n_days": n_eval / 288.0,
            "initial_equity": cfg.risk.initial_equity,
            "final_equity": equity,
            "risk_per_trade": cfg.risk.risk_per_trade,
            "max_leverage": cfg.risk.max_leverage,
            "safety_overlay": self.safety_overlay,
            "n_trades": len(trades),
            "n_episodes": len(closed_eps),
            "blocked": dict(blocked),
            "liquidation_basis": "mark" if self.has_mark else "traded",
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "result_hash": result_hash,
            "notes": notes or {},
        }
        return V4Result(
            manifest, trades_df, episodes_df, decisions_df, daily_df, result_hash, blocked
        )

    # ------------------------------------------------------------------ records
    def _trade_row(self, p: Position) -> dict[str, Any]:
        row = p.to_row()
        row["event_strength"] = p.features.get("_event_strength", math.nan)
        row["event_j"] = int(p.features.get("_event_j", -1))
        row["structural_stop"] = p.features.get("_structural_stop", math.nan)
        row["stop_source_rule"] = (
            "VOL_FLOOR" if p.features.get("_stop_is_floor", 0.0) > 0 else "STRUCTURAL"
        )
        row["cost_pct_of_stop"] = p.features.get("_cost_pct_of_stop", math.nan)
        for k in (
            "_event_strength",
            "_event_j",
            "_structural_stop",
            "_stop_is_floor",
            "_cost_pct_of_stop",
        ):
            row.pop(f"f_{k}", None)
        return row

    def _episode_row(self, ep: Episode) -> dict[str, Any]:
        row = ep.as_row()
        row["episode_id"] = int(ep.features.get("_v4_id", ep.episode_id))
        row["regime_at_detection"] = (
            ep.regime_at_detection.value if ep.regime_at_detection else None
        )
        row["regime_at_trigger"] = ep.regime_at_trigger.value if ep.regime_at_trigger else None
        row["event_strength"] = float(ep.plan.notes.get("strength", math.nan))
        row.pop("f__v4_id", None)
        return row

    def _block(
        self,
        mgr: EpisodeManager,
        ep: Episode,
        i: int,
        t: int,
        reason: str,
        closed: list[dict[str, Any]],
    ) -> None:
        ep.set_state(EpisodeState.INVALIDATED, i, t, reason)
        mgr._end(ep, i, self.cfg.episode.cooldown_bars_after_invalidation)
        mgr.closed.clear()
        closed.append(self._episode_row(ep))

    def _close_episode(
        self,
        managers: list[tuple[V4Detector, EpisodeManager]],
        pos: Position,
        i: int,
        t: int,
        closed: list[dict[str, Any]],
    ) -> None:
        for _det, mgr in managers:
            ep = mgr.current
            if ep is not None and ep.trade_id == pos.trade_id:
                mgr.mark_closed(ep, i, t, pos.exits[-1].reason.value)
                mgr.closed.clear()
                closed.append(self._episode_row(ep))
                return

    # ------------------------------------------------------------------ sizing / entry
    def _final_stop(
        self, plan_stop: float, side: Side, ref: float, atr: float
    ) -> tuple[float, bool]:
        """The farther of the structural stop and the volatility floor, capped at max_stop_atr."""
        g = self.cfg.geometry
        s = side.sign
        floor = ref - s * g.vol_floor_atr * atr
        cap = ref - s * g.max_stop_atr * atr
        structural_dist = s * (ref - plan_stop)
        floor_dist = g.vol_floor_atr * atr
        stop = plan_stop if structural_dist >= floor_dist else floor
        is_floor = structural_dist < floor_dist
        if s * (ref - stop) > g.max_stop_atr * atr:
            stop = cap
        return stop, is_floor

    def _size(
        self,
        ep: Episode,
        view: MarketView,
        close: float,
        equity: float,
        bar: int,
        t: int,
        mgr: EpisodeManager,
        reg: V4Regime,
        closed: list[dict[str, Any]],
    ) -> _Pending | None:
        cfg = self.cfg
        plan = ep.plan
        stop, is_floor = self._final_stop(plan.stop_price, plan.side, close, plan.atr_setup_tf)
        ep.plan = dataclasses.replace(plan, stop_price=stop, stop_distance_ref=abs(close - stop))
        atr_liq = (
            view.ind(cfg.risk.liquidation_atr_tf, "atr")
            if view.warm(cfg.risk.liquidation_atr_tf)
            else math.nan
        )
        sizing_equity = equity if cfg.risk.compounding else cfg.risk.initial_equity
        sizing = size_position(sizing_equity, close, stop, plan.side, atr_liq, cfg.risk)
        if not sizing.accepted:
            mgr.mark_risk_rejected(ep, bar, t, sizing.reason)
            mgr.closed.clear()
            closed.append(self._episode_row(ep))
            return None
        feats = self.aux.snapshot(t, view, self.series)
        cc = cfg.costs
        rt_cost_pct = (
            2 * cc.taker_fee_bps + cc.entry_slippage_bps + cc.stop_slippage_bps
        ) / 10_000.0
        stop_pct = abs(close - stop) / close
        feats["_event_strength"] = float(plan.notes.get("strength", math.nan))
        feats["_event_j"] = float(plan.notes.get("event_j", -1))
        feats["_structural_stop"] = float(plan.notes.get("structural_stop", math.nan))
        feats["_stop_is_floor"] = 1.0 if is_floor else 0.0
        feats["_cost_pct_of_stop"] = rt_cost_pct / stop_pct if stop_pct > 0 else math.nan
        return _Pending(ep, sizing, feats, reg, feats["_cost_pct_of_stop"])

    def _open_position(
        self, p: _Pending, trade_id: int, bar: int, open_ms: int, open_price: float, equity: float
    ) -> Position:
        cfg = self.cfg
        plan = p.episode.plan
        side = plan.side
        s = side.sign
        fill = self.costs.entry_fill(open_price, side)
        qty = p.sizing.qty
        stop = plan.stop_price
        dist = s * (fill - stop)
        if dist <= 0:
            dist = max(p.sizing.stop_distance, 1e-9)
        liq = liquidation_price(fill, side, p.sizing.leverage, cfg.risk.maintenance_margin_rate)
        pos = Position(
            trade_id=trade_id,
            episode_id=int(p.episode.features.get("_v4_id", p.episode.episode_id)),
            family=plan.family,
            side=side,
            regime_at_entry=cast(Regime, p.regime),
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
            stop_distance_atr=dist / plan.atr_setup_tf if plan.atr_setup_tf > 0 else math.nan,
            tp1=fill + s * cfg.exits.tp1_r * dist,
            tp2=fill + s * cfg.exits.tp2_r * dist,
            structural_target=plan.structural_target,
            liq_price=liq,
            equity_at_entry=equity,
            r_levels=list(cfg.exits.evaluate_r_levels),
            entry_ref_price=open_price,
            mark_price_at_decision=p.features.get("mark_price", math.nan),
            liquidation_basis="mark" if self.has_mark else "traded",
            features=p.features,
            regime_at_trigger=p.regime.value,
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
        cfg = self.cfg
        s = pos.side.sign
        pos.update_path(i, h, lo)
        adverse = lo if s > 0 else h
        favourable = h if s > 0 else lo
        mark_adverse = (self.mark_l[i] if s > 0 else self.mark_h[i]) if self.has_mark else math.nan
        liq_adverse = mark_adverse if not math.isnan(mark_adverse) else adverse
        if s * (liq_adverse - pos.liq_price) <= 0:
            pos.exits.append(
                PartialExit(
                    i, t, pos.liq_price, pos.qty_open, ExitReason.LIQUIDATION, 0.0, pos.liq_price
                )
            )
            pos.qty_open = 0.0
            lost = pos.sizing.margin
            pos.exits[-1].price = (
                pos.entry_price - s * (lost - pos.fees + pos.funding) / pos.qty_initial
            )
            return True
        if s * (adverse - pos.stop) <= 0:
            px = o if s * (o - pos.stop) <= 0 else pos.stop
            self._exit(
                pos,
                i,
                t,
                px,
                pos.qty_open,
                ExitReason.TRAIL if pos.stop_moved else ExitReason.STOP,
                True,
            )
            return True
        if not pos.tp1_done and s * (favourable - pos.tp1) >= 0:
            q = min(pos.qty_open, pos.qty_initial * cfg.exits.tp1_frac)
            pos.tp1_done = True
            pos.tp1_bar, pos.tp1_ms = i, t
            if q > 0:
                self._exit(pos, i, t, pos.tp1, q, ExitReason.TP1, False)
            if cfg.exits.breakeven_after_tp1 and s * (pos.entry_price - pos.stop) > 0:
                pos.pending_stop = pos.entry_price
                pos.pending_stop_source = "BREAKEVEN"
            pos.trail_active = True
            if not pos.is_open:
                return True
        if pos.tp1_done and not pos.tp2_done and s * (favourable - pos.tp2) >= 0:
            q = min(pos.qty_open, pos.qty_initial * cfg.exits.tp2_frac)
            pos.tp2_done = True
            if q > 0:
                self._exit(pos, i, t, pos.tp2, q, ExitReason.TP2, False)
            if not pos.is_open:
                return True
        if t - pos.entry_ms >= cfg.exits.max_hold_hours * 3_600_000:
            self._exit(pos, i, t, c, pos.qty_open, ExitReason.TIME_LIMIT, True)
            return True
        return False

    def _update_trail(self, pos: Position, view: MarketView, close: float) -> None:
        cfg = self.cfg
        if not pos.trail_active:
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
            pos.pending_stop_source = "TRAIL"


def simulate_entry(
    eng: V4Engine, bar: int, side: Side, stop_pct: float, atr_setup: float, equity: float
) -> Position | None:
    """Null benchmark helper: enter at open[bar] with the given geometry and run the V4 exits."""
    cfg = eng.cfg
    base = eng.series.base
    n = len(base)
    if bar <= 0 or bar >= n:
        return None
    ref = float(base.close[bar - 1])
    stop_ref = ref * (1.0 - side.sign * stop_pct)
    view = eng.series.view_at(int(base.close_ms[bar - 1]))
    atr_liq = (
        view.ind(cfg.risk.liquidation_atr_tf, "atr")
        if view.warm(cfg.risk.liquidation_atr_tf)
        else math.nan
    )
    sz = size_position(equity, ref, stop_ref, side, atr_liq, cfg.risk)
    if not sz.accepted:
        return None
    o = float(base.open[bar])
    fill = eng.costs.entry_fill(o, side)
    stop = fill * (1.0 - side.sign * stop_pct)
    dist = side.sign * (fill - stop)
    pos = Position(
        trade_id=-1,
        episode_id=-1,
        family=cast(SetupFamily, V4Family.DELEVERAGING_REVERSAL),
        side=side,
        regime_at_entry=cast(Regime, V4Regime.UNCLEAR),
        entry_bar=bar,
        entry_ms=int(base.open_ms[bar]),
        entry_price=fill,
        sizing=sz,
        qty_initial=sz.qty,
        qty_open=sz.qty,
        stop=stop,
        initial_stop=stop,
        stop_reason="NULL",
        stop_distance=dist,
        stop_distance_atr=dist / atr_setup if atr_setup > 0 else math.nan,
        tp1=fill + side.sign * cfg.exits.tp1_r * dist,
        tp2=fill + side.sign * cfg.exits.tp2_r * dist,
        structural_target=None,
        liq_price=liquidation_price(fill, side, sz.leverage, cfg.risk.maintenance_margin_rate),
        equity_at_entry=equity,
        r_levels=list(cfg.exits.evaluate_r_levels),
        entry_ref_price=o,
    )
    pos.entry_fee = eng.costs.fee(sz.qty * fill)
    for i in range(bar, n):
        t = int(base.close_ms[i])
        c = float(base.close[i])
        closed = eng._process_bar(
            pos, i, t, float(base.open[i]), float(base.high[i]), float(base.low[i]), c
        )
        if pos.is_open:
            for _ft, rate in eng.funding.events_between(int(base.close_ms[i - 1]), t):
                pos.funding += FundingSchedule.payment(rate, pos.qty_open, c, side)
        if closed or not pos.is_open:
            return pos
        if pos.pending_stop is not None:
            pos.stop = pos.pending_stop
            pos.stop_moved = True
            pos.pending_stop = None
        eng._update_trail(pos, eng.series.view_at(t), c)
    eng._exit(
        pos,
        n - 1,
        int(base.close_ms[n - 1]),
        float(base.close[n - 1]),
        pos.qty_open,
        ExitReason.END_OF_DATA,
        True,
    )
    return pos
