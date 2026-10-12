"""V6.1 chronological 5-minute paper engine (`v6.1-exit-1`).

Differences from the frozen V6 engine (which is not modified): three entry modes with an explicit
maker fill model (TAKER / MAKER / MAKER_FIRST), a first-class cost-share eligibility rule, family
structural targets, a time stop on insufficient progress, a minimum-stop-floor grid, and the
structure-only / structure+flow switch handed to the detectors. One position, no pyramiding, no
averaging; fills at the next 5m open; frozen `size_position` ladder. Nothing here can place an
order: there is no client and no credentials."""

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
from btc_swing.v6.families import EpState, first_in_cluster
from btc_swing.v6.features import V6Frame
from btc_swing.v61.config import (
    V61_EVENT_RULE_VERSION,
    V61_EXIT_RULE_VERSION,
    V61_FEATURE_VERSION,
    V61_VERSION,
    EntryMode,
    ExitVariantCfg,
    V61Config,
    V61Family,
)
from btc_swing.v61.families import Detector61, Episode, Trigger61, build_detectors61

DAY_MS = 86_400_000
MS_5M = 300_000
Mode = Literal["baseline", "sequential", "independent"]
SNAPSHOT_COLUMNS: list[str] = [
    "rel_vol_5m", "rel_vol_15m", "imbalance_5m", "imbalance_15m", "delta_accel", "cvd_slope_15m",
    "oi_chg_15m", "oi_chg_1h", "prem_z", "regime_1h", "trend_1h", "vol_tercile", "atr5", "atr15m",
    "dist_vwap_atr", "dist_ema21_15m_atr", "range_pos_4h", "range_pos_24h", "compression_6",
    "avg_trade_rel", "active_day",
]  # fmt: skip


@dataclass(frozen=True)
class Costs61:
    taker_bps: float
    maker_bps: float
    entry_slip_bps: float
    stop_slip_bps: float
    half_spread: float

    @classmethod
    def from_cfg(cls, cfg: V61Config) -> Costs61:
        c = cfg.costs
        return cls(
            c.taker_fee_bps,
            c.maker_fee_bps,
            c.entry_slippage_bps,
            c.stop_slippage_bps,
            0.5 * c.spread_ticks * c.tick_size,
        )

    def taker_entry_fill(self, ref: float, side: Side) -> float:
        return ref * (1.0 + side.sign * self.entry_slip_bps / 1e4) + side.sign * self.half_spread

    def stop_fill(self, ref: float, side: Side) -> float:
        return ref * (1.0 - side.sign * self.stop_slip_bps / 1e4) - side.sign * self.half_spread

    def fee(self, notional: float, maker: bool = False) -> float:
        return abs(notional) * (self.maker_bps if maker else self.taker_bps) / 1e4

    def round_trip_bps(self, maker_entry: bool) -> float:
        """Expected cost of a stopped-out round trip in bps of price (fees + slippage + spread)."""
        entry = self.maker_bps if maker_entry else self.taker_bps + self.entry_slip_bps
        return entry + self.taker_bps + self.stop_slip_bps + 2 * (self.half_spread / 50_000.0) * 1e4


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
class Position61:
    trade_id: int
    episode_id: int
    family: V61Family
    side: Side
    variant: ExitVariantCfg
    entry_bar: int
    entry_ms: int
    entry_ref: float
    entry_fill: float
    entry_maker: bool
    qty: float
    stop: float
    initial_stop: float
    stop_dist: float
    sizing: Sizing
    liq_price: float
    tp1: float
    tp2: float
    target: float
    event_ms: int
    trigger_ms: int
    trigger_reason: str
    structural_stop: float
    stop_is_floor: bool
    atr5_at_entry: float
    atr15_at_entry: float
    regime_code: int
    regime_1h: float
    cost_share_stop: float
    cost_share_target: float
    flow_on: bool
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
    entry_mode: str = "TAKER"

    @property
    def is_open(self) -> bool:
        return self.qty_open > 1e-12

    def update_path(self, h: float, lo: float) -> None:
        s = self.side.sign
        self.max_fav = max(self.max_fav, s * ((h if s > 0 else lo) - self.entry_fill))
        self.max_adv = max(self.max_adv, s * (self.entry_fill - (lo if s > 0 else h)))

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

    def row(self) -> dict[str, Any]:
        risk = max(self.sizing.risk_amount, 1e-9)
        last = self.exits[-1]
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
            "entry_mode": self.entry_mode,
            "entry_maker": self.entry_maker,
            "flow_on": self.flow_on,
            "mode": self.mode_tag,
            "event_ms": self.event_ms,
            "trigger_ms": self.trigger_ms,
            "trigger_reason": self.trigger_reason,
            "entry_ms": self.entry_ms,
            "exit_ms": last.t_ms,
            "entry_delay_min": (self.entry_ms - self.event_ms) / 60_000.0,
            "hold_min": (last.t_ms - self.entry_ms) / 60_000.0,
            "entry_ref": self.entry_ref,
            "entry_fill": self.entry_fill,
            "initial_stop": self.initial_stop,
            "structural_stop": self.structural_stop,
            "stop_is_floor": self.stop_is_floor,
            "stop_bps": 1e4 * self.stop_dist / self.entry_fill,
            "stop_pct": 100.0 * self.stop_dist / self.entry_fill,
            "stop_atr15": self.stop_dist / self.atr15_at_entry
            if self.atr15_at_entry > 0
            else math.nan,
            "target": self.target,
            "target_bps": 1e4 * abs(self.target - self.entry_fill) / self.entry_fill
            if not math.isnan(self.target)
            else math.nan,
            "cost_share_stop": self.cost_share_stop,
            "cost_share_target": self.cost_share_target,
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
            "regime_1h": self.regime_1h,
            "funding_events": self.funding_events,
            **{f"f_{k}": v for k, v in self.features.items()},
        }


@dataclass
class V61Result:
    manifest: dict[str, Any]
    trades: pl.DataFrame
    episodes: pl.DataFrame
    refused: pl.DataFrame
    daily: pl.DataFrame
    result_hash: str
    events_per_detector: dict[str, dict[str, int]] = field(default_factory=dict)
    triggers: pl.DataFrame = field(
        default_factory=pl.DataFrame
    )  # every trigger with its cost shares


@dataclass
class _Manager:
    det: Detector61
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


@dataclass
class _Pending:
    ep: Episode
    trg: Trigger61
    sz: Sizing
    feats: dict[str, float]
    is_floor: bool
    cost_share_stop: float
    cost_share_target: float
    trigger_bar: int
    limit: float  # maker limit price (= trigger close); NaN for taker
    maker: bool  # current attempt is a maker attempt
    deadline_bar: int  # last bar on which a maker fill can happen


class V61Engine:
    def __init__(
        self,
        cfg: V61Config,
        fr: V6Frame,
        funding: pl.DataFrame | None,
        families: list[V61Family] | None = None,
        flow: dict[V61Family, bool] | bool = True,
        variant: str | None = None,
        time_stop_hours: float | None = None,
        entry_mode: EntryMode | None = None,
        cost_share_max: float | None = None,
        stop_floor_pct: float = 0.0,
        mode: Mode = "baseline",
        overlay: bool = False,
        risk_per_trade: float | None = None,
        data_hashes: dict[str, str] | None = None,
    ) -> None:
        self.cfg = cfg
        self.fr = fr
        self.families = families if families is not None else cfg.enabled_families()
        self.flow = flow
        self.variant = cfg.exits.variants[variant or cfg.exits.default_variant]
        self.time_stop_h = (
            time_stop_hours if time_stop_hours is not None else cfg.exits.default_time_stop_hours
        )
        self.entry_mode: EntryMode = entry_mode or cfg.costs.entry_mode
        self.cost_share_max = (
            cfg.costs.cost_share_stop_max if cost_share_max is None else cost_share_max
        )
        self.stop_floor_pct = stop_floor_pct
        self.mode: Mode = mode
        self.overlay = overlay
        self.costs = Costs61.from_cfg(cfg)
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
        self.detectors = build_detectors61(cfg, fr, self.families, flow)
        c = fr.cols
        self.o, self.h, self.lo, self.c = c["open"], c["high"], c["low"], c["close"]
        self.atr5, self.atr15, self.atr4h = c["atr5"], c["atr15m"], c["atr4h"]
        self.sw_lo, self.sw_hi = c["swing_low_15m"], c["swing_high_15m"]
        self.t, self.t_open = fr.close_ms, fr.open_ms
        self.tick = cfg.costs.tick_size

    # ------------------------------------------------------------------ run
    def run(self, start_ms: int, end_ms: int, notes: dict[str, Any] | None = None) -> V61Result:
        cfg, fr = self.cfg, self.fr
        n = len(fr)
        i0 = max(1, int(np.searchsorted(self.t, start_ms, side="left")))
        i1 = min(n, int(np.searchsorted(self.t, end_ms, side="right")))
        managers = [
            _Manager(d, first_in_cluster(d.events, cfg.episode.cluster_bars))
            for d in self.detectors
        ]
        for m in managers:
            m.stats["events"] = int(m.det.events[i0:i1].sum())
            m.stats["first_in_cluster"] = int(m.fic[i0:i1].sum())
        trades: list[Position61] = []
        independent: list[Position61] = []
        closed_eps: list[dict[str, Any]] = []
        refused: list[dict[str, Any]] = []
        trig_rows: list[dict[str, Any]] = []
        daily: list[dict[str, Any]] = []
        equity = cfg.risk.initial_equity
        position: Position61 | None = None
        pending: _Pending | None = None
        next_trade, next_ep = 1, 1
        no_entry_until = -1
        day = _Day(day=int(self.t[i0]) // DAY_MS)
        day_r, day_trades = 0.0, 0
        for i in range(i0, i1):
            t = int(self.t[i])
            d_now = t // DAY_MS
            if d_now != day.day:
                daily.append(
                    {
                        "day": day.day,
                        "trades": day_trades,
                        "realised_pnl": day.realised,
                        "realised_r": day_r,
                        "equity": equity,
                        "budget_blocked": day.blocked,
                    }
                )
                day = _Day(day=d_now)
                day_r, day_trades = 0.0, 0
            # 1. resolve a pending entry on this bar
            if pending is not None and position is None:
                position, pending = self._resolve_pending(
                    pending, i, next_trade, equity, refused, closed_eps, managers
                )
                if position is not None:
                    next_trade += 1
            # 2. manage the open position
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
                    day_r += r
                    if r <= cfg.overlay.full_risk_loss_r:
                        day.full_losses += 1
                    if self.overlay and (
                        day.full_losses >= cfg.overlay.max_full_risk_losses_per_day
                        or day.realised
                        <= -cfg.overlay.max_daily_loss_frac * cfg.risk.initial_equity
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
            slot_free = position is None and pending is None
            # 3. episodes
            for m in managers:
                det, ep = m.det, m.current
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
                cs_stop, cs_tgt, stop, _is_floor = self._cost_shares(ep, trg, i)
                trig_rows.append(
                    {
                        "t_ms": t,
                        "family": ep.family.value,
                        "side": ep.side.value,
                        "stop_bps": 1e4 * abs(trg.ref_close - stop) / trg.ref_close,
                        "target_bps": 1e4 * abs(trg.target - trg.ref_close) / trg.ref_close,
                        "cost_bps": self.costs.round_trip_bps(self.entry_mode != "TAKER"),
                        "cost_share_stop": cs_stop,
                        "cost_share_target": cs_tgt,
                    }
                )
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
                if self.cost_share_max > 0 and cs_stop > self.cost_share_max:
                    reason = "COST_SHARE_TOO_HIGH"
                elif not slot_free:
                    reason = "BLOCKED_POSITION_OPEN"
                elif i < no_entry_until:
                    reason = "BLOCKED_COOLDOWN_AFTER_CLOSE"
                elif self.overlay and day.blocked:
                    reason = "BLOCKED_DAILY_BUDGET"
                elif i >= i1 - 1:
                    reason = "END_OF_WINDOW"
                if reason:
                    self._refuse(m, ep, trg, i, reason, refused, closed_eps, equity)
                    continue
                sized = self._size(ep, trg, i, equity)
                if sized is None:
                    self._refuse(m, ep, trg, i, "RISK_REJECTED", refused, closed_eps, equity)
                    continue
                sz, feats, isf = sized
                maker = self.entry_mode != "TAKER"
                pending = _Pending(
                    ep,
                    trg,
                    sz,
                    feats,
                    isf,
                    cs_stop,
                    cs_tgt,
                    i,
                    trg.ref_close if maker else math.nan,
                    maker,
                    i + cfg.costs.maker_timeout_bars,
                )
                ep.set_state(EpState.TRIGGERED, i, trg.reason)
                slot_free = False
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
                "realised_r": day_r,
                "equity": equity,
                "budget_blocked": day.blocked,
            }
        )
        all_trades = independent if self.mode == "independent" else trades
        trades_df = pl.DataFrame([p.row() for p in all_trades]) if all_trades else pl.DataFrame()
        eps_df = pl.DataFrame(closed_eps) if closed_eps else pl.DataFrame()
        ref_df = pl.DataFrame(refused) if refused else pl.DataFrame()
        trig_df = pl.DataFrame(trig_rows) if trig_rows else pl.DataFrame()
        rh = stable_hash(
            {
                "trades": round_floats(trades_df.to_dicts() if trades_df.height else [], 6),
                "refused": ref_df.height,
                "episodes": eps_df.height,
                "final_equity": round(equity, 6),
            }
        )
        flow_desc = (
            self.flow if isinstance(self.flow, bool) else {k.value: v for k, v in self.flow.items()}
        )
        manifest = {
            "strategy_name": cfg.strategy_name,
            "strategy_version": V61_VERSION,
            "rule_versions": {
                "features": V61_FEATURE_VERSION,
                "events": V61_EVENT_RULE_VERSION,
                "exits": V61_EXIT_RULE_VERSION,
            },
            "config_hash": cfg.config_hash,
            "code_version": code_version(),
            "data_hashes": dict(self.data_hashes),
            "data_source": fr.meta.get("source"),
            "families": [f.value for f in self.families],
            "flow": flow_desc,
            "exit_variant": self.variant.name,
            "time_stop_hours": self.time_stop_h,
            "entry_mode": self.entry_mode,
            "cost_share_max": self.cost_share_max,
            "stop_floor_pct": self.stop_floor_pct,
            "mode": self.mode,
            "overlay": self.overlay,
            "risk_per_trade": self.risk_cfg.risk_per_trade,
            "reference_equity": cfg.risk.initial_equity,
            "period_start_ms": start_ms,
            "period_end_ms": end_ms,
            "n_5m_evaluations": i1 - i0,
            "n_days": (i1 - i0) / 288.0,
            "n_trades": len(all_trades),
            "n_episodes": len(closed_eps),
            "n_refused": len(refused),
            "final_equity": equity,
            "result_hash": rh,
            "notes": notes or {},
        }
        return V61Result(
            manifest,
            trades_df,
            eps_df,
            ref_df,
            pl.DataFrame(daily),
            rh,
            {m.key: dict(m.stats) for m in managers},
            trig_df,
        )

    # ------------------------------------------------------------------ entries
    def _resolve_pending(
        self,
        p: _Pending,
        i: int,
        trade_id: int,
        equity: float,
        refused: list[dict[str, Any]],
        closed_eps: list[dict[str, Any]],
        managers: list[_Manager],
    ) -> tuple[Position61 | None, _Pending | None]:
        """Bar i after the trigger: taker fills at the open; maker fills only if the bar trades
        through the limit; after the timeout MAKER cancels and MAKER_FIRST falls back to a taker
        fill at the next open if the setup is still valid."""
        s = p.ep.side.sign
        o = float(self.o[i])
        if math.isnan(o):
            self._void(p, i, "ENTRY_BAR_GAP", refused, closed_eps, managers)
            return None, None
        if not p.maker:
            pos = self._open(p, trade_id, i, o, False, equity)
            return pos, None
        # maker attempt on bar i (i <= deadline)
        through = (
            (float(self.lo[i]) <= p.limit - self.tick)
            if s > 0
            else (float(self.h[i]) >= p.limit + self.tick)
        )
        if through:
            pos = self._open(p, trade_id, i, p.limit, True, equity)
            return pos, None
        if i < p.deadline_bar:
            return None, p  # keep waiting
        # timeout
        if self.entry_mode == "MAKER":
            self._void(p, i, "MAKER_UNFILLED", refused, closed_eps, managers, counterfactual=True)
            return None, None
        # MAKER_FIRST: taker fallback at the NEXT open if the setup is still valid at this close
        c = float(self.c[i])
        drift_ok = abs(c - p.trg.ref_close) <= self.cfg.costs.maker_first_max_drift_atr15 * float(
            self.atr15[i]
        )
        if s * (c - p.sz.stop_price) > 0 and drift_ok:
            p.maker = False
            return None, p
        self._void(p, i, "MAKER_FIRST_SETUP_GONE", refused, closed_eps, managers)
        return None, None

    def _void(
        self,
        p: _Pending,
        i: int,
        reason: str,
        refused: list[dict[str, Any]],
        closed_eps: list[dict[str, Any]],
        managers: list[_Manager],
        counterfactual: bool = False,
    ) -> None:
        cf = (
            self._simulate(
                p.ep, p.trg, p.trigger_bar, -1, self.cfg.risk.initial_equity, force_taker=True
            )
            if counterfactual
            else None
        )
        refused.append(self._refusal_row(p.ep, p.trg, i, reason, cf))
        p.ep.set_state(EpState.INVALIDATED, i, reason)
        closed_eps.append(p.ep.as_row())
        for m in managers:
            if m.current is p.ep:
                m.current = None

    def _refusal_row(
        self, ep: Episode, trg: Trigger61, i: int, reason: str, pos: Position61 | None
    ) -> dict[str, Any]:
        return {
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

    def _refuse(
        self,
        m: _Manager,
        ep: Episode,
        trg: Trigger61,
        i: int,
        reason: str,
        refused: list[dict[str, Any]],
        closed_eps: list[dict[str, Any]],
        equity: float,
    ) -> None:
        if self.mode == "sequential" and reason in (
            "BLOCKED_POSITION_OPEN",
            "BLOCKED_COOLDOWN_AFTER_CLOSE",
        ):
            ep.set_state(EpState.WATCH, i, "WAITING_FOR_SLOT")
            return
        pos = (
            self._simulate(ep, trg, i, -1, equity, force_taker=True)
            if reason not in ("END_OF_WINDOW", "RISK_REJECTED")
            else None
        )
        refused.append(self._refusal_row(ep, trg, i, reason, pos))
        ep.set_state(EpState.INVALIDATED, i, reason)
        closed_eps.append(ep.as_row())
        m.current = None
        m.cooldown_until = i + self.cfg.episode.cooldown_after_invalidation_bars

    def _after_close(
        self,
        managers: list[_Manager],
        pos: Position61,
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
        sc = self.cfg.stops
        s = side.sign
        struct_d = s * (ref - structural)
        floor_d = max(sc.floor_atr15 * atr15, self.stop_floor_pct / 100.0 * ref)
        is_floor = struct_d < floor_d
        d = min(max(struct_d, floor_d), sc.max_atr15 * atr15)
        return ref - s * d, is_floor

    def _cost_shares(self, ep: Episode, trg: Trigger61, i: int) -> tuple[float, float, float, bool]:
        stop, is_floor = self.final_stop(
            ep.side, trg.ref_close, trg.structural_stop, float(self.atr15[i])
        )
        stop_bps = 1e4 * abs(trg.ref_close - stop) / trg.ref_close
        tgt_bps = (
            1e4 * abs(trg.target - trg.ref_close) / trg.ref_close
            if not math.isnan(trg.target)
            else math.nan
        )
        cost = self.costs.round_trip_bps(self.entry_mode != "TAKER")
        return (
            cost / stop_bps if stop_bps > 0 else math.inf,
            cost / tgt_bps if tgt_bps and tgt_bps > 0 else math.nan,
            stop,
            is_floor,
        )

    def _size(
        self, ep: Episode, trg: Trigger61, i: int, equity: float
    ) -> tuple[Sizing, dict[str, float], bool] | None:
        cfg = self.cfg
        stop, is_floor = self.final_stop(
            ep.side, trg.ref_close, trg.structural_stop, float(self.atr15[i])
        )
        eq = equity if cfg.risk.compounding else cfg.risk.initial_equity
        sz = size_position(eq, trg.ref_close, stop, ep.side, float(self.atr4h[i]), self.risk_cfg)
        if not sz.accepted:
            return None
        return sz, self.fr.row(i, SNAPSHOT_COLUMNS), is_floor

    def _open(
        self, p: _Pending, trade_id: int, i: int, ref: float, maker: bool, equity: float
    ) -> Position61:
        cfg = self.cfg
        side = p.ep.side
        s = side.sign
        fill = ref if maker else self.costs.taker_entry_fill(ref, side)
        stop = p.sz.stop_price
        dist = s * (fill - stop)
        if dist <= 0:
            dist = max(p.sz.stop_distance, 1e-9)
        v = self.variant
        det = next(d for d in self.detectors if d.family is p.ep.family)
        pos = Position61(
            trade_id=trade_id,
            episode_id=p.ep.episode_id,
            family=p.ep.family,
            side=side,
            variant=v,
            entry_bar=i,
            entry_ms=int(self.t_open[i]),
            entry_ref=ref,
            entry_fill=fill,
            entry_maker=maker,
            qty=p.sz.qty,
            stop=stop,
            initial_stop=stop,
            stop_dist=dist,
            sizing=p.sz,
            liq_price=liquidation_price(
                fill, side, p.sz.leverage, cfg.risk.maintenance_margin_rate
            ),
            tp1=fill + s * v.tp1_r * dist if v.tp1_r else math.nan,
            tp2=fill + s * v.tp2_r * dist if v.tp2_r else math.nan,
            target=p.trg.target if v.structural_target else math.nan,
            event_ms=p.ep.event_ms,
            trigger_ms=int(self.t[p.trigger_bar]),
            trigger_reason=p.trg.reason,
            structural_stop=p.trg.structural_stop,
            stop_is_floor=p.is_floor,
            atr5_at_entry=float(self.atr5[p.trigger_bar]),
            atr15_at_entry=float(self.atr15[p.trigger_bar]),
            regime_code=int(self.fr.regime_code[p.trigger_bar]),
            regime_1h=float(self.fr.cols["regime_1h"][p.trigger_bar]),
            cost_share_stop=p.cost_share_stop,
            cost_share_target=p.cost_share_target,
            flow_on=det.flow_on,
            features=p.feats,
            qty_open=p.sz.qty,
            trail_active=False,
            entry_mode=self.entry_mode,
        )
        pos.entry_fee = self.costs.fee(p.sz.qty * fill, maker=maker)
        p.ep.trade_id = trade_id
        p.ep.set_state(EpState.ACTIVE, i, "FILLED_MAKER" if maker else "FILLED_TAKER")
        pos.mode_tag = self.mode
        return pos

    def _simulate(
        self,
        ep: Episode,
        trg: Trigger61,
        i: int,
        trade_id: int,
        equity: float,
        force_taker: bool = False,
    ) -> Position61 | None:
        """One trade on its own from the open of bar i+1 (taker) with the engine's exits."""
        sized = self._size(ep, trg, i, equity)
        if sized is None or i + 1 >= len(self.fr) or math.isnan(float(self.o[i + 1])):
            return None
        sz, feats, isf = sized
        cs_stop, cs_tgt, _stop, _f = self._cost_shares(ep, trg, i)
        p = _Pending(ep, trg, sz, feats, isf, cs_stop, cs_tgt, i, math.nan, False, i)
        saved_state, saved_tid = ep.state, ep.trade_id
        pos = self._open(p, trade_id, i + 1, float(self.o[i + 1]), False, equity)
        ep.state, ep.trade_id = (
            saved_state,
            saved_tid,
        )  # a simulation must not touch the live episode
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
        self, pos: Position61, i: int, ref: float, qty: float, reason: str, stop_like: bool
    ) -> None:
        fill = self.costs.stop_fill(ref, pos.side) if stop_like else ref
        fee = self.costs.fee(qty * fill)
        pos.exits.append(Fill(i, int(self.t[i]), ref, fill, qty, reason, fee))
        pos.qty_open -= qty
        if pos.qty_open < 1e-12:
            pos.qty_open = 0.0

    def _process_bar(self, pos: Position61, i: int) -> bool:
        s = pos.side.sign
        o, h, lo, c = float(self.o[i]), float(self.h[i]), float(self.lo[i]), float(self.c[i])
        if math.isnan(c):
            return False
        pos.update_path(h, lo)
        adverse = lo if s > 0 else h
        favourable = h if s > 0 else lo
        if s * (adverse - pos.liq_price) <= 0:
            lost = pos.sizing.margin
            ref = pos.entry_ref - s * lost / pos.qty
            pos.exits.append(
                Fill(
                    i,
                    int(self.t[i]),
                    ref,
                    pos.entry_fill - s * lost / pos.qty,
                    pos.qty_open,
                    "LIQUIDATION",
                    0.0,
                )
            )
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
        if (
            v.structural_target
            and not math.isnan(pos.target)
            and s * (favourable - pos.target) >= 0
        ):
            self._exit(pos, i, pos.target, pos.qty_open, "TARGET", False)
            return True
        if not pos.tp1_done and not math.isnan(pos.tp1) and s * (favourable - pos.tp1) >= 0:
            q = min(pos.qty_open, pos.qty * v.tp1_frac)
            pos.tp1_done = True
            if q > 0:
                self._exit(pos, i, pos.tp1, q, "TP1", False)
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
        mfe_r = pos.max_fav / pos.stop_dist
        if (
            v.breakeven_after_r is not None
            and mfe_r >= v.breakeven_after_r
            and s * (pos.entry_fill - pos.stop) > 0
            and pos.pending_stop is None
            and not pos.stop_moved
        ):
            pos.pending_stop, pos.pending_source = pos.entry_fill, "BREAKEVEN"
        if v.trail_after_r is not None and mfe_r >= v.trail_after_r:
            pos.trail_active = True
        held_ms = int(self.t[i]) - pos.entry_ms
        if held_ms >= self.time_stop_h * 3_600_000 and mfe_r < self.cfg.exits.time_stop_min_mfe_r:
            self._exit(pos, i, c, pos.qty_open, "TIME_STOP", True)
            return True
        if held_ms >= self.cfg.exits.max_hold_hours * 3_600_000:
            self._exit(pos, i, c, pos.qty_open, "TIME_LIMIT", True)
            return True
        return False

    def _update_trail(self, pos: Position61, i: int) -> None:
        if not pos.trail_active:
            return
        s = pos.side.sign
        swing = float(self.sw_lo[i] if s > 0 else self.sw_hi[i])
        a, c = float(self.atr15[i]), float(self.c[i])
        if math.isnan(swing) or math.isnan(a) or math.isnan(c):
            return
        cand = swing - s * self.cfg.exits.trail_atr15_buffer * a
        if s * (cand - pos.stop) > 0 and s * (c - cand) > 0:
            pos.pending_stop, pos.pending_source = cand, "TRAIL"

    def _funding(self, pos: Position61, i: int) -> None:
        if len(self.fund_t) == 0:
            return
        prev_t = int(self.t[i - 1]) if i > 0 else int(self.t[i]) - MS_5M
        lo_ = int(np.searchsorted(self.fund_t, prev_t, side="right"))
        hi_ = int(np.searchsorted(self.fund_t, int(self.t[i]), side="right"))
        if hi_ <= lo_:
            return
        px = float(self.c[i])
        if math.isnan(px):
            k = i - 1
            while k >= pos.entry_bar and math.isnan(float(self.c[k])):
                k -= 1
            px = float(self.c[k]) if k >= pos.entry_bar else pos.entry_fill
        for j in range(lo_, hi_):
            pos.funding += -pos.side.sign * float(self.fund_r[j]) * pos.qty_open * px
            pos.funding_events += 1
