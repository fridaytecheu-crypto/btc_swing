"""V6 chronological 5-minute paper engine (`v6-exit-1`).

Decision at the close of bar i, fill at the open of bar i+1 (taker, slippage + half spread
against the trade). One V6 position at a time (no pyramiding, no averaging), a pre-declared family
priority, cooldowns, an optional daily budget overlay, four pre-declared exit variants, an 8 h time
cap, Bybit-like costs and funding, and the frozen `size_position` ladder (leverage sets margin,
never the loss at the stop). The engine records every trigger it refused and why, so the
one-position rule can be quantified; two diagnostic modes (`sequential`, `independent`) relax it.
Nothing here can place an order: there is no client and no credentials."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import polars as pl

from btc_swing.core.enums import Side
from btc_swing.core.hashing import round_floats, stable_hash
from btc_swing.core.versions import code_version
from btc_swing.risk.sizing import Sizing, liquidation_price, size_position
from btc_swing.v6.config import (
    V6_EVENT_RULE_VERSION,
    V6_EXIT_RULE_VERSION,
    V6_FEATURE_VERSION,
    V6_VERSION,
    ExitVariantCfg,
    V6Config,
    V6Family,
)
from btc_swing.v6.families import (
    Detector,
    Episode,
    EpState,
    Trigger,
    build_detectors,
    first_in_cluster,
)
from btc_swing.v6.features import V6Frame

DAY_MS = 86_400_000
MS_5M = 300_000
SNAPSHOT_COLUMNS: list[str] = [
    "rel_vol_5m", "rel_vol_15m", "imbalance_5m", "imbalance_15m", "imbalance_1h",
    "cvd_slope_15m", "cvd_slope_1h", "oi_chg_15m", "oi_chg_1h", "rel_absoi_15m",
    "prem_z", "trend_1h", "trend_4h", "vol_tercile", "rv_24h", "atr5", "atr15m",
    "atr1h", "active_day",
]  # fmt: skip
Mode = Literal["baseline", "sequential", "independent"]


# ----------------------------------------------------------------------------- costs
@dataclass(frozen=True)
class Costs:
    taker_bps: float
    maker_bps: float
    entry_slip_bps: float
    stop_slip_bps: float
    half_spread: float  # price units

    @classmethod
    def from_cfg(cls, cfg: V6Config, maker_entry: bool = False) -> Costs:
        c = cfg.costs
        return cls(
            c.taker_fee_bps,
            c.maker_fee_bps if maker_entry else c.taker_fee_bps,
            c.entry_slippage_bps,
            c.stop_slippage_bps,
            0.5 * c.spread_ticks * c.tick_size,
        )

    def entry_fill(self, ref: float, side: Side) -> float:
        return ref * (1.0 + side.sign * self.entry_slip_bps / 1e4) + side.sign * self.half_spread

    def stop_fill(self, ref: float, side: Side) -> float:
        return ref * (1.0 - side.sign * self.stop_slip_bps / 1e4) - side.sign * self.half_spread

    def target_fill(self, ref: float) -> float:
        return ref

    def fee(self, notional: float, maker: bool = False) -> float:
        return abs(notional) * (self.maker_bps if maker else self.taker_bps) / 1e4


# ----------------------------------------------------------------------------- position
@dataclass
class Fill:
    bar: int
    t_ms: int
    ref_price: float
    fill_price: float
    qty: float
    reason: str
    fee: float


@dataclass
class Position:
    trade_id: int
    episode_id: int
    family: V6Family
    side: Side
    variant: ExitVariantCfg
    entry_bar: int
    entry_ms: int
    entry_ref: float
    entry_fill: float
    qty: float
    stop: float
    initial_stop: float
    stop_dist: float
    sizing: Sizing
    liq_price: float
    tp1: float
    tp2: float
    event_ms: int
    trigger_ms: int
    trigger_reason: str
    structural_stop: float
    stop_is_floor: bool
    atr5_at_entry: float
    atr15_at_entry: float
    regime_code: int
    features: dict[str, float] = field(default_factory=dict)
    qty_open: float = 0.0
    entry_fee: float = 0.0
    exits: list[Fill] = field(default_factory=list)
    tp1_done: bool = False
    tp2_done: bool = False
    trail_active: bool = False
    stop_moved: bool = False
    pending_stop: float | None = None
    pending_source: str = ""
    stop_source: str = "INITIAL"
    funding: float = 0.0
    funding_events: int = 0
    max_fav: float = 0.0
    max_adv: float = 0.0
    mode_tag: str = ""

    @property
    def is_open(self) -> bool:
        return self.qty_open > 1e-12

    def update_path(self, h: float, lo: float) -> None:
        s = self.side.sign
        self.max_fav = max(self.max_fav, s * ((h if s > 0 else lo) - self.entry_fill))
        self.max_adv = max(self.max_adv, s * (self.entry_fill - (lo if s > 0 else h)))

    # accounting (reference prices = gross; fills = after slippage; fees and funding separate)
    def gross_pnl(self) -> float:
        s = self.side.sign
        return sum(s * (x.ref_price - self.entry_ref) * x.qty for x in self.exits)

    def pnl_at_fills(self) -> float:
        s = self.side.sign
        return sum(s * (x.fill_price - self.entry_fill) * x.qty for x in self.exits)

    def fees(self) -> float:
        return self.entry_fee + sum(x.fee for x in self.exits)

    def slippage(self) -> float:
        return self.gross_pnl() - self.pnl_at_fills()

    def net_pnl(self) -> float:
        return self.pnl_at_fills() - self.fees() + self.funding

    def row(self, close_ms: np.ndarray) -> dict[str, Any]:
        risk = max(self.sizing.risk_amount, 1e-9)
        last = self.exits[-1]
        hold_min = (last.t_ms - self.entry_ms) / 60_000.0
        gross, slip, fees, fund, net = (
            self.gross_pnl(),
            self.slippage(),
            self.fees(),
            self.funding,
            self.net_pnl(),
        )
        return {
            "trade_id": self.trade_id,
            "episode_id": self.episode_id,
            "family": self.family.value,
            "side": self.side.value,
            "exit_variant": self.variant.name,
            "mode": self.mode_tag,
            "event_ms": self.event_ms,
            "trigger_ms": self.trigger_ms,
            "trigger_reason": self.trigger_reason,
            "entry_ms": self.entry_ms,
            "exit_ms": last.t_ms,
            "entry_delay_min": (self.entry_ms - self.event_ms) / 60_000.0,
            "hold_min": hold_min,
            "entry_ref": self.entry_ref,
            "entry_fill": self.entry_fill,
            "initial_stop": self.initial_stop,
            "structural_stop": self.structural_stop,
            "stop_is_floor": self.stop_is_floor,
            "stop_bps": 1e4 * self.stop_dist / self.entry_fill,
            "stop_atr15": self.stop_dist / self.atr15_at_entry
            if self.atr15_at_entry > 0
            else math.nan,
            "stop_atr5": self.stop_dist / self.atr5_at_entry
            if self.atr5_at_entry > 0
            else math.nan,
            "qty": self.qty,
            "notional": self.qty * self.entry_fill,
            "leverage": self.sizing.leverage,
            "margin": self.sizing.margin,
            "risk_amount": self.sizing.risk_amount,
            "liq_price": self.liq_price,
            "exit_reason": last.reason,
            "exit_reasons": ",".join(x.reason for x in self.exits),
            "n_fills": len(self.exits),
            "gross_pnl": gross,
            "slippage_cost": slip,
            "fees": fees,
            "funding": fund,
            "net_pnl": net,
            "gross_r": gross / risk,
            "slip_r": -slip / risk,
            "fee_r": -fees / risk,
            "fund_r": fund / risk,
            "net_r": net / risk,
            "mfe_r": self.max_fav / self.stop_dist,
            "mae_r": self.max_adv / self.stop_dist,
            "tp1_done": self.tp1_done,
            "tp2_done": self.tp2_done,
            "stop_moved": self.stop_moved,
            "regime_code": self.regime_code,
            "funding_events": self.funding_events,
            **{f"f_{k}": v for k, v in self.features.items()},
        }


# ----------------------------------------------------------------------------- result
@dataclass
class V6Result:
    manifest: dict[str, Any]
    trades: pl.DataFrame
    episodes: pl.DataFrame
    refused: pl.DataFrame
    daily: pl.DataFrame
    result_hash: str
    events_per_detector: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass
class _Manager:
    det: Detector
    fic: np.ndarray
    current: Episode | None = None
    cooldown_until: int = -1
    consecutive_losses: int = 0
    stats: dict[str, int] = field(
        default_factory=lambda: {"events": 0, "first_in_cluster": 0, "episodes": 0}
    )

    @property
    def key(self) -> str:
        return f"{self.det.family.value}/{self.det.side.value}"


@dataclass
class _Day:
    day: int = -1
    realised: float = 0.0
    full_losses: int = 0
    blocked: bool = False


class V6Engine:
    def __init__(
        self,
        cfg: V6Config,
        fr: V6Frame,
        funding: pl.DataFrame | None,
        families: list[V6Family] | None = None,
        variant: str | None = None,
        breakeven: bool | None = None,
        mode: Mode = "baseline",
        overlay: bool = False,
        overlay_losses: int | None = None,
        overlay_daily_loss: float | None = None,
        maker_entry: bool = False,
        risk_per_trade: float | None = None,
        data_hashes: dict[str, str] | None = None,
    ) -> None:
        self.cfg = cfg
        self.fr = fr
        self.families = families if families is not None else cfg.enabled_families()
        self.variant = cfg.exits.variants[variant or cfg.exits.default_variant]
        self.breakeven = cfg.exits.breakeven_after_tp1 if breakeven is None else breakeven
        self.mode: Mode = mode
        self.overlay = overlay
        self.overlay_losses = overlay_losses or cfg.overlay.max_full_risk_losses_per_day
        self.overlay_daily_loss = overlay_daily_loss or cfg.overlay.max_daily_loss_frac
        self.costs = Costs.from_cfg(cfg, maker_entry)
        self.maker_entry = maker_entry
        self.risk_cfg = (
            cfg.risk.model_copy(update={"risk_per_trade": risk_per_trade})
            if risk_per_trade
            else cfg.risk
        )
        self.data_hashes = data_hashes or {}
        if funding is not None and not funding.is_empty() and cfg.costs.apply_funding:
            f = funding.sort("time_ms")
            self.fund_t = f["time_ms"].to_numpy().astype(np.int64)
            self.fund_r = f["funding_rate"].to_numpy().astype(np.float64)
        else:
            self.fund_t = np.zeros(0, dtype=np.int64)
            self.fund_r = np.zeros(0)
        self.detectors = build_detectors(cfg, fr, self.families)
        c = fr.cols
        self.o, self.h, self.lo, self.c = c["open"], c["high"], c["low"], c["close"]
        self.atr5, self.atr15, self.atr4h = c["atr5"], c["atr15m"], c["atr4h"]
        self.sw_lo, self.sw_hi = c["swing_low_15m"], c["swing_high_15m"]
        self.t = fr.close_ms
        self.t_open = fr.open_ms

    # ------------------------------------------------------------------ run
    def run(self, start_ms: int, end_ms: int, notes: dict[str, Any] | None = None) -> V6Result:
        cfg, fr = self.cfg, self.fr
        n = len(fr)
        i0 = max(1, int(np.searchsorted(self.t, start_ms, side="left")))
        i1 = min(n, int(np.searchsorted(self.t, end_ms, side="right")))
        managers = [
            _Manager(d, first_in_cluster(d.events, cfg.episode.cluster_bars))
            for d in self.detectors
        ]
        for m in managers:
            sl = slice(i0, i1)
            m.stats["events"] = int(m.det.events[sl].sum())
            m.stats["first_in_cluster"] = int(m.fic[sl].sum())
        trades: list[Position] = []
        closed_eps: list[dict[str, Any]] = []
        refused: list[dict[str, Any]] = []
        daily: list[dict[str, Any]] = []
        independent: list[Position] = []
        equity = cfg.risk.initial_equity
        position: Position | None = None
        pending: tuple[Episode, Trigger, Sizing, dict[str, float], bool, float] | None = None
        next_trade, next_ep = 1, 1
        no_entry_until = -1
        day = _Day(day=int(self.t[i0]) // DAY_MS)
        day_realised_r = 0.0
        day_trades = 0
        for i in range(i0, i1):
            t = int(self.t[i])
            d_now = t // DAY_MS
            if d_now != day.day:
                daily.append(
                    {
                        "day": day.day,
                        "trades": day_trades,
                        "realised_pnl": day.realised,
                        "realised_r": day_realised_r,
                        "equity": equity,
                        "budget_blocked": day.blocked,
                    }
                )
                day = _Day(day=d_now)
                day_realised_r = 0.0
                day_trades = 0
            # 1. fill the pending entry at this bar's open
            if pending is not None and position is None and math.isnan(float(self.o[i])):
                # the fill bar is a data gap: no price exists to fill at; the trigger is void
                p_ep, p_trg = pending[0], pending[1]
                refused.append(
                    {
                        "episode_id": p_ep.episode_id,
                        "family": p_ep.family.value,
                        "side": p_ep.side.value,
                        "t_ms": t,
                        "reason": "ENTRY_BAR_GAP",
                        "trigger_reason": p_trg.reason,
                        "counterfactual_net_r": math.nan,
                        "counterfactual_hold_min": math.nan,
                        "counterfactual_exit": "",
                    }
                )
                p_ep.set_state(EpState.INVALIDATED, i, "ENTRY_BAR_GAP")
                closed_eps.append(p_ep.as_row())
                for m in managers:
                    if m.current is p_ep:
                        m.current = None
                pending = None
            if pending is not None and position is None:
                p_ep, p_trg, sz, feats, is_floor, struct = pending
                position = self._open(
                    p_ep, p_trg, sz, feats, is_floor, struct, next_trade, i, equity
                )
                position.mode_tag = self.mode
                p_ep.trade_id = next_trade
                p_ep.set_state(EpState.ACTIVE, i, "FILLED")
                next_trade += 1
                pending = None
            # 2. manage the open position on this bar
            if position is not None:
                closed = self._process_bar(position, i)
                if position.is_open:
                    self._funding(position, i)
                if closed or not position.is_open:
                    equity += position.net_pnl()
                    trades.append(position)
                    day_trades += 1
                    r = position.net_pnl() / max(position.sizing.risk_amount, 1e-9)
                    day.realised += position.net_pnl()
                    day_realised_r += r
                    if r <= cfg.overlay.full_risk_loss_r:
                        day.full_losses += 1
                    if self.overlay and (
                        day.full_losses >= self.overlay_losses
                        or day.realised <= -self.overlay_daily_loss * cfg.risk.initial_equity
                    ):
                        day.blocked = True
                    self._after_close(managers, position, i, closed_eps, r)
                    no_entry_until = (
                        i + cfg.episode.cooldown_after_close_bars if self.mode == "baseline" else i
                    )
                    position = None
                else:
                    if position.pending_stop is not None:
                        position.stop = position.pending_stop
                        position.stop_moved = True
                        position.stop_source = position.pending_source
                        position.pending_stop = None
                    self._update_trail(position, i)
            # 3. episodes: open on first-in-cluster events, step Stage B, handle triggers
            slot_free = position is None and pending is None
            for m in managers:
                det = m.det
                ep = m.current
                if ep is None:
                    if i < i1 - 1 and m.fic[i] and i >= m.cooldown_until:
                        m.current = det.open_episode(i, next_ep)
                        m.stats["episodes"] += 1
                        next_ep += 1
                    continue
                trg = det.step(ep, i)
                if ep.state in (EpState.INVALIDATED, EpState.EXPIRED):
                    closed_eps.append(ep.as_row())
                    m.current = None
                    m.cooldown_until = i + cfg.episode.cooldown_after_invalidation_bars
                    continue
                if trg is None:
                    continue
                # the trigger fires: resolve against the slot, cooldowns and the budget
                if self.mode == "independent":
                    pos = self._simulate(ep, trg, i, next_trade, equity)
                    if pos is not None:
                        pos.mode_tag = "independent"
                        independent.append(pos)
                        next_trade += 1
                    ep.set_state(EpState.CLOSED, i, "INDEPENDENT_SIMULATED")
                    closed_eps.append(ep.as_row())
                    m.current = None
                    m.cooldown_until = i + cfg.episode.cooldown_after_invalidation_bars
                    continue
                reason = ""
                if not slot_free:
                    reason = "BLOCKED_POSITION_OPEN"
                elif i < no_entry_until:
                    reason = "BLOCKED_COOLDOWN_AFTER_CLOSE"
                elif self.overlay and day.blocked:
                    reason = "BLOCKED_DAILY_BUDGET"
                elif i >= i1 - 1:
                    reason = "END_OF_WINDOW"
                if reason:
                    self._refuse(m, ep, trg, i, reason, refused, closed_eps, equity, next_trade)
                    continue
                sized = self._size(ep, trg, i, equity)
                if sized is None:
                    self._refuse(
                        m, ep, trg, i, "RISK_REJECTED", refused, closed_eps, equity, next_trade
                    )
                    continue
                pending = (ep, trg, *sized)
                ep.set_state(EpState.TRIGGERED, i, trg.reason)
                slot_free = False
        # end of window: close an open position at the last close
        last_i = i1 - 1
        if position is not None and last_i >= i0:
            self._exit(
                position, last_i, float(self.c[last_i]), position.qty_open, "END_OF_DATA", True
            )
            equity += position.net_pnl()
            trades.append(position)
            self._after_close(managers, position, last_i, closed_eps, 0.0)
        for m in managers:
            if m.current is not None:
                m.current.set_state(EpState.EXPIRED, last_i, "END_OF_DATA")
                closed_eps.append(m.current.as_row())
                m.current = None
        daily.append(
            {
                "day": day.day,
                "trades": day_trades,
                "realised_pnl": day.realised,
                "realised_r": day_realised_r,
                "equity": equity,
                "budget_blocked": day.blocked,
            }
        )
        all_trades = independent if self.mode == "independent" else trades
        trades_df = (
            pl.DataFrame([p.row(self.t) for p in all_trades]) if all_trades else pl.DataFrame()
        )
        eps_df = pl.DataFrame(closed_eps) if closed_eps else pl.DataFrame()
        ref_df = pl.DataFrame(refused) if refused else pl.DataFrame()
        daily_df = pl.DataFrame(daily)
        rh = stable_hash(
            {
                "trades": round_floats(trades_df.to_dicts() if trades_df.height else [], 6),
                "refused": ref_df.height,
                "episodes": eps_df.height,
                "final_equity": round(equity, 6),
            }
        )
        n_days = (i1 - i0) / 288.0
        manifest = {
            "strategy_name": cfg.strategy_name,
            "strategy_version": V6_VERSION,
            "rule_versions": {
                "features": V6_FEATURE_VERSION,
                "events": V6_EVENT_RULE_VERSION,
                "exits": V6_EXIT_RULE_VERSION,
            },
            "config_hash": cfg.config_hash,
            "code_version": code_version(),
            "data_hashes": dict(self.data_hashes),
            "families": [f.value for f in self.families],
            "exit_variant": self.variant.name,
            "breakeven_after_tp1": self.breakeven,
            "mode": self.mode,
            "overlay": self.overlay,
            "maker_entry": self.maker_entry,
            "risk_per_trade": self.risk_cfg.risk_per_trade,
            "reference_equity": cfg.risk.initial_equity,
            "period_start_ms": start_ms,
            "period_end_ms": end_ms,
            "n_5m_evaluations": i1 - i0,
            "n_days": n_days,
            "n_trades": len(all_trades),
            "n_episodes": len(closed_eps),
            "n_refused": len(refused),
            "final_equity": equity,
            "result_hash": rh,
            "notes": notes or {},
        }
        return V6Result(
            manifest,
            trades_df,
            eps_df,
            ref_df,
            daily_df,
            rh,
            {m.key: dict(m.stats) for m in managers},
        )

    # ------------------------------------------------------------------ helpers
    def _refuse(
        self,
        m: _Manager,
        ep: Episode,
        trg: Trigger,
        i: int,
        reason: str,
        refused: list[dict[str, Any]],
        closed_eps: list[dict[str, Any]],
        equity: float,
        next_trade: int,
    ) -> None:
        if self.mode == "sequential" and reason in (
            "BLOCKED_POSITION_OPEN",
            "BLOCKED_COOLDOWN_AFTER_CLOSE",
        ):
            # the episode stays alive (within its own age/entry window) and re-triggers later
            ep.set_state(EpState.WATCH, i, "WAITING_FOR_SLOT")
            return
        # counterfactual: what the refused trade would have done on its own
        pos = self._simulate(ep, trg, i, -1, equity) if reason != "END_OF_WINDOW" else None
        refused.append(
            {
                "episode_id": ep.episode_id,
                "family": ep.family.value,
                "side": ep.side.value,
                "t_ms": int(self.t[i]),
                "reason": reason,
                "trigger_reason": trg.reason,
                "counterfactual_net_r": pos.net_pnl() / max(pos.sizing.risk_amount, 1e-9)
                if pos is not None
                else math.nan,
                "counterfactual_hold_min": (pos.exits[-1].t_ms - pos.entry_ms) / 60_000.0
                if pos is not None
                else math.nan,
                "counterfactual_exit": pos.exits[-1].reason if pos is not None else "",
            }
        )
        ep.set_state(EpState.INVALIDATED, i, reason)
        closed_eps.append(ep.as_row())
        m.current = None
        m.cooldown_until = i + self.cfg.episode.cooldown_after_invalidation_bars

    def _after_close(
        self,
        managers: list[_Manager],
        pos: Position,
        i: int,
        closed_eps: list[dict[str, Any]],
        r: float,
    ) -> None:
        for m in managers:
            ep = m.current
            if ep is not None and ep.trade_id == pos.trade_id:
                ep.set_state(EpState.CLOSED, i, pos.exits[-1].reason)
                closed_eps.append(ep.as_row())
                m.current = None
                if r < 0:
                    m.consecutive_losses += 1
                    if m.consecutive_losses >= 2:
                        m.cooldown_until = max(
                            m.cooldown_until, i + self.cfg.episode.cooldown_after_two_losses_bars
                        )
                        m.consecutive_losses = 0
                else:
                    m.consecutive_losses = 0
                return

    def final_stop(
        self, side: Side, ref: float, structural: float, atr15: float
    ) -> tuple[float, bool]:
        """Farther of the structural stop and the ATR(15m) floor, capped at max_atr15."""
        sc = self.cfg.stops
        s = side.sign
        struct_d = s * (ref - structural)
        floor_d = sc.floor_atr15 * atr15
        is_floor = struct_d < floor_d
        d = max(struct_d, floor_d)
        d = min(d, sc.max_atr15 * atr15)
        return ref - s * d, is_floor

    def _size(
        self, ep: Episode, trg: Trigger, i: int, equity: float
    ) -> tuple[Sizing, dict[str, float], bool, float] | None:
        cfg = self.cfg
        ref = trg.ref_close
        stop, is_floor = self.final_stop(ep.side, ref, trg.structural_stop, float(self.atr15[i]))
        atr_liq = float(self.atr4h[i])
        eq = equity if cfg.risk.compounding else cfg.risk.initial_equity
        sz = size_position(eq, ref, stop, ep.side, atr_liq, self.risk_cfg)
        if not sz.accepted:
            return None
        feats = self.fr.row(
            i,
            SNAPSHOT_COLUMNS,
        )
        return sz, feats, is_floor, trg.structural_stop

    def _open(
        self,
        ep: Episode,
        trg: Trigger,
        sz: Sizing,
        feats: dict[str, float],
        is_floor: bool,
        struct: float,
        trade_id: int,
        i: int,
        equity: float,
    ) -> Position:
        cfg = self.cfg
        side = ep.side
        s = side.sign
        o = float(self.o[i])
        fill = self.costs.entry_fill(o, side)
        stop = sz.stop_price
        dist = s * (fill - stop)
        if dist <= 0:
            dist = max(sz.stop_distance, 1e-9)
        v = self.variant
        liq = liquidation_price(fill, side, sz.leverage, cfg.risk.maintenance_margin_rate)
        pos = Position(
            trade_id=trade_id,
            episode_id=ep.episode_id,
            family=ep.family,
            side=side,
            variant=v,
            entry_bar=i,
            entry_ms=int(self.t_open[i]),
            entry_ref=o,
            entry_fill=fill,
            qty=sz.qty,
            stop=stop,
            initial_stop=stop,
            stop_dist=dist,
            sizing=sz,
            liq_price=liq,
            tp1=fill + s * v.tp1_r * dist if v.tp1_r else math.nan,
            tp2=fill + s * v.tp2_r * dist if v.tp2_r else math.nan,
            event_ms=ep.event_ms,
            trigger_ms=int(self.t[i - 1]),
            trigger_reason=trg.reason,
            structural_stop=struct,
            stop_is_floor=is_floor,
            atr5_at_entry=float(self.atr5[i - 1]),
            atr15_at_entry=float(self.atr15[i - 1]),
            regime_code=int(self.fr.regime_code[i - 1]),
            features=feats,
            qty_open=sz.qty,
            trail_active=v.trail_from_entry,
        )
        pos.entry_fee = self.costs.fee(sz.qty * fill, maker=self.maker_entry)
        return pos

    def _simulate(
        self, ep: Episode, trg: Trigger, i: int, trade_id: int, equity: float
    ) -> Position | None:
        """Run one trade on its own from the open of bar i+1 with the engine's exits (diagnostic)."""
        sized = self._size(ep, trg, i, equity)
        if sized is None or i + 1 >= len(self.fr) or math.isnan(float(self.o[i + 1])):
            return None
        sz, feats, is_floor, struct = sized
        pos = self._open(ep, trg, sz, feats, is_floor, struct, trade_id, i + 1, equity)
        n = len(self.fr)
        for j in range(i + 1, n):
            closed = self._process_bar(pos, j)
            if pos.is_open:
                self._funding(pos, j)
            if closed or not pos.is_open:
                return pos
            if pos.pending_stop is not None:
                pos.stop = pos.pending_stop
                pos.stop_moved = True
                pos.stop_source = pos.pending_source
                pos.pending_stop = None
            self._update_trail(pos, j)
        self._exit(pos, n - 1, float(self.c[n - 1]), pos.qty_open, "END_OF_DATA", True)
        return pos

    # ------------------------------------------------------------------ exits
    def _exit(
        self, pos: Position, i: int, ref: float, qty: float, reason: str, stop_like: bool
    ) -> None:
        fill = self.costs.stop_fill(ref, pos.side) if stop_like else self.costs.target_fill(ref)
        fee = self.costs.fee(qty * fill)
        pos.exits.append(Fill(i, int(self.t[i]), ref, fill, qty, reason, fee))
        pos.qty_open -= qty
        if pos.qty_open < 1e-12:
            pos.qty_open = 0.0

    def _process_bar(self, pos: Position, i: int) -> bool:
        s = pos.side.sign
        o, h, lo, c = float(self.o[i]), float(self.h[i]), float(self.lo[i]), float(self.c[i])
        if math.isnan(c):
            return False  # data gap: nothing can be observed on this bar
        pos.update_path(h, lo)
        adverse = lo if s > 0 else h
        favourable = h if s > 0 else lo
        if s * (adverse - pos.liq_price) <= 0:
            # isolated-margin liquidation: the margin is lost (fees/funding already separate)
            lost = pos.sizing.margin
            ref = pos.entry_ref - s * lost / pos.qty
            pos.exits.append(
                Fill(i, int(self.t[i]), ref, ref + 0.0, pos.qty_open, "LIQUIDATION", 0.0)
            )
            pos.exits[-1].fill_price = pos.entry_fill - s * lost / pos.qty
            pos.qty_open = 0.0
            return True
        if s * (adverse - pos.stop) <= 0:
            px = o if s * (o - pos.stop) <= 0 else pos.stop
            reason = (
                "STOP"
                if not pos.stop_moved
                else ("BREAKEVEN" if pos.stop_source == "BREAKEVEN" else "TRAIL")
            )
            self._exit(pos, i, px, pos.qty_open, reason, True)
            return True
        v = pos.variant
        if not pos.tp1_done and not math.isnan(pos.tp1) and s * (favourable - pos.tp1) >= 0:
            q = min(pos.qty_open, pos.qty * v.tp1_frac)
            pos.tp1_done = True
            if q > 0:
                self._exit(pos, i, pos.tp1, q, "TP1", False)
            if self.breakeven and s * (pos.entry_fill - pos.stop) > 0:
                pos.pending_stop, pos.pending_source = pos.entry_fill, "BREAKEVEN"
            pos.trail_active = True
            if not pos.is_open:
                return True
        if (
            pos.tp1_done
            and not pos.tp2_done
            and not math.isnan(pos.tp2)
            and s * (favourable - pos.tp2) >= 0
        ):
            q = min(pos.qty_open, pos.qty * v.tp2_frac)
            pos.tp2_done = True
            if q > 0:
                self._exit(pos, i, pos.tp2, q, "TP2", False)
            if not pos.is_open:
                return True
        if int(self.t[i]) - pos.entry_ms >= self.cfg.exits.max_hold_hours * 3_600_000:
            self._exit(pos, i, c, pos.qty_open, "TIME_LIMIT", True)
            return True
        return False

    def _update_trail(self, pos: Position, i: int) -> None:
        if not pos.trail_active:
            return
        s = pos.side.sign
        swing = float(self.sw_lo[i] if s > 0 else self.sw_hi[i])
        a = float(self.atr15[i])
        c = float(self.c[i])
        if math.isnan(swing) or math.isnan(a) or math.isnan(c):
            return
        cand = swing - s * self.cfg.exits.trail_atr15_buffer * a
        if s * (cand - pos.stop) > 0 and s * (c - cand) > 0:
            pos.pending_stop, pos.pending_source = cand, "TRAIL"

    def _funding(self, pos: Position, i: int) -> None:
        if len(self.fund_t) == 0:
            return
        prev_t = int(self.t[i - 1]) if i > 0 else int(self.t[i]) - MS_5M
        lo_ = int(np.searchsorted(self.fund_t, prev_t, side="right"))
        hi_ = int(np.searchsorted(self.fund_t, int(self.t[i]), side="right"))
        if hi_ <= lo_:
            return
        px = float(self.c[i])
        if math.isnan(px):  # data-gap bar: settle on the last observed close
            k = i - 1
            while k >= pos.entry_bar and math.isnan(float(self.c[k])):
                k -= 1
            px = float(self.c[k]) if k >= pos.entry_bar else pos.entry_fill
        for j in range(lo_, hi_):
            pos.funding += -pos.side.sign * float(self.fund_r[j]) * pos.qty_open * px
            pos.funding_events += 1
