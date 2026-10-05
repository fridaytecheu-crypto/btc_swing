"""Position and trade records. Four returns are always kept apart:

  BTC_RETURN        signed move of the underlying between entry and (qty-weighted) exit
  POSITION_PNL      USDT P&L of the position net of fees and funding
  RETURN_ON_MARGIN  POSITION_PNL / isolated margin posted
  ACCOUNT_RETURN    POSITION_PNL / account equity at entry
plus R_MULTIPLE = POSITION_PNL / planned risk amount, MFE/MAE in price and R units, and the
counterfactual target-hit flags (would price have reached k*R before the INITIAL stop?).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from btc_swing.core.enums import ExitReason, Regime, SetupFamily, Side
from btc_swing.risk.sizing import Sizing


@dataclass
class PartialExit:
    bar: int
    t_ms: int
    price: float
    qty: float
    reason: ExitReason
    fee: float
    ref_price: float = float("nan")  # price before slippage (open / stop level / target / close)


@dataclass
class Position:
    trade_id: int
    episode_id: int
    family: SetupFamily
    side: Side
    regime_at_entry: Regime
    entry_bar: int
    entry_ms: int
    entry_price: float
    sizing: Sizing
    qty_initial: float
    qty_open: float
    stop: float
    initial_stop: float
    stop_reason: str
    stop_distance: float  # realised |entry - initial stop|
    stop_distance_atr: float
    tp1: float
    tp2: float
    structural_target: float | None
    liq_price: float
    equity_at_entry: float
    r_levels: list[float]
    entry_ref_price: float = float("nan")  # bar open before slippage
    mark_price_at_decision: float = float("nan")
    liquidation_basis: str = "traded"
    features: dict[str, float] = field(default_factory=dict)
    entry_fee: float = 0.0
    funding: float = 0.0
    funding_events: int = 0
    # Phase 2.2 confirmation monitoring (None/False when the entry mode does not use it)
    awaiting_confirmation: bool = False
    confirm_deadline_bar: int | None = None
    confirmed_at_entry: bool = False
    confirmed_bar: int | None = None
    confirmed_ms: int | None = None
    mfe_before_confirm: float = float("nan")
    mae_before_confirm: float = float("nan")
    pending_early_exit: ExitReason | None = None
    exits: list[PartialExit] = field(default_factory=list)
    tp1_done: bool = False
    tp2_done: bool = False
    trail_active: bool = False
    stop_moved: bool = False
    pending_stop: float | None = None
    mfe_price: float = 0.0
    mae_price: float = 0.0
    cf_stopped: bool = False
    cf_hits: dict[float, bool] = field(default_factory=dict)
    cf_bars_to_hit: dict[float, int | None] = field(default_factory=dict)
    max_unrealised_loss: float = 0.0

    def __post_init__(self) -> None:
        self.mfe_price = self.entry_price
        self.mae_price = self.entry_price
        for r in self.r_levels:
            self.cf_hits[r] = False
            self.cf_bars_to_hit[r] = None

    # ------------------------------------------------------------------ path tracking
    def update_path(self, bar: int, high: float, low: float) -> None:
        s = self.side.sign
        fav = high if s > 0 else low
        adv = low if s > 0 else high
        if self.awaiting_confirmation:
            if math.isnan(self.mfe_before_confirm) or s * (fav - self.mfe_before_confirm) > 0:
                self.mfe_before_confirm = fav
            if math.isnan(self.mae_before_confirm) or s * (adv - self.mae_before_confirm) < 0:
                self.mae_before_confirm = adv
        if s * (fav - self.mfe_price) > 0:
            self.mfe_price = fav
        if s * (adv - self.mae_price) < 0:
            self.mae_price = adv
        loss = s * (adv - self.entry_price) * self.qty_initial
        self.max_unrealised_loss = min(self.max_unrealised_loss, loss)
        if not self.cf_stopped:
            if s * (adv - self.initial_stop) <= 0:
                self.cf_stopped = True  # stop first (conservative)
            else:
                for r in self.r_levels:
                    if (
                        not self.cf_hits[r]
                        and s * (fav - (self.entry_price + s * r * self.stop_distance)) >= 0
                    ):
                        self.cf_hits[r] = True
                        self.cf_bars_to_hit[r] = bar - self.entry_bar

    # ------------------------------------------------------------------ accounting
    @property
    def is_open(self) -> bool:
        return self.qty_open > 1e-12

    @property
    def gross_pnl(self) -> float:
        s = self.side.sign
        return sum(s * (e.price - self.entry_price) * e.qty for e in self.exits)

    @property
    def fees(self) -> float:
        return self.entry_fee + sum(e.fee for e in self.exits)

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.fees + self.funding

    @property
    def slippage_cost(self) -> float:
        """USDT lost to slippage versus the reference prices (entry open, stop level, close)."""
        cost = 0.0
        if not math.isnan(self.entry_ref_price):
            cost += abs(self.entry_price - self.entry_ref_price) * self.qty_initial
        for e in self.exits:
            if not math.isnan(e.ref_price):
                cost += abs(e.price - e.ref_price) * e.qty
        return cost

    def unrealised(self, price: float) -> float:
        return self.side.sign * (price - self.entry_price) * self.qty_open

    def avg_exit_price(self) -> float:
        q = sum(e.qty for e in self.exits)
        return sum(e.price * e.qty for e in self.exits) / q if q > 0 else float("nan")

    def to_row(self) -> dict[str, Any]:
        s = self.side.sign
        sz = self.sizing
        risk = self.qty_initial * self.stop_distance
        exit_ms = self.exits[-1].t_ms if self.exits else self.entry_ms
        row: dict[str, Any] = {
            "trade_id": self.trade_id,
            "episode_id": self.episode_id,
            "family": self.family.value,
            "side": self.side.value,
            "regime_at_entry": self.regime_at_entry.value,
            "entry_ms": self.entry_ms,
            "exit_ms": exit_ms,
            "holding_hours": (exit_ms - self.entry_ms) / 3_600_000.0,
            "entry_price": self.entry_price,
            "entry_ref_price": self.entry_ref_price,
            "mark_price_at_decision": self.mark_price_at_decision,
            "liquidation_basis": self.liquidation_basis,
            "tp1_price": self.tp1,
            "tp2_price": self.tp2,
            "avg_exit_price": self.avg_exit_price(),
            "qty": self.qty_initial,
            "notional": self.qty_initial * self.entry_price,
            "leverage": sz.leverage,
            "margin": sz.margin,
            "initial_stop": self.initial_stop,
            "final_stop": self.stop,
            "stop_reason": self.stop_reason,
            "stop_distance": self.stop_distance,
            "stop_distance_pct": self.stop_distance / self.entry_price,
            "stop_distance_atr": self.stop_distance_atr,
            "liquidation_price": self.liq_price,
            "liquidation_distance": s * (self.entry_price - self.liq_price),
            "liquidation_distance_pct": s * (self.entry_price - self.liq_price) / self.entry_price,
            "stop_to_liquidation_ratio": sz.stop_to_liquidation_ratio,
            "stop_to_liquidation_buffer_pct": (
                s * (self.entry_price - self.liq_price) / self.entry_price
                - self.stop_distance / self.entry_price
            ),
            "liquidation_distance_atr": sz.liquidation_distance_atr,
            "risk_amount": risk,
            "risk_frac": risk / self.equity_at_entry,
            "risk_capped": sz.risk_capped,
            "equity_at_entry": self.equity_at_entry,
            "fees": self.fees,
            "slippage": self.slippage_cost,
            "funding": self.funding,
            "funding_events": self.funding_events,
            "gross_pnl": self.gross_pnl,
            "POSITION_PNL": self.net_pnl,
            "BTC_RETURN": s * (self.avg_exit_price() - self.entry_price) / self.entry_price,
            "RETURN_ON_MARGIN": self.net_pnl / sz.margin if sz.margin else float("nan"),
            "ACCOUNT_RETURN": self.net_pnl / self.equity_at_entry,
            "R_MULTIPLE": self.net_pnl / risk if risk else float("nan"),
            "R_MULTIPLE_GROSS": self.gross_pnl / risk if risk else float("nan"),
            "mfe_price": self.mfe_price,
            "mae_price": self.mae_price,
            "MFE_R": s * (self.mfe_price - self.entry_price) / self.stop_distance,
            "MAE_R": s * (self.mae_price - self.entry_price) / self.stop_distance,
            "MFE_PCT": s * (self.mfe_price - self.entry_price) / self.entry_price,
            "MAE_PCT": s * (self.mae_price - self.entry_price) / self.entry_price,
            "max_unrealised_loss": self.max_unrealised_loss,
            "max_account_loss_at_stop": sz.max_account_loss_at_stop,
            "max_account_loss_at_liquidation": sz.max_account_loss_at_liquidation,
            "exit_reason": self.exits[-1].reason.value if self.exits else None,
            "n_partial_exits": len(self.exits),
            "tp1_hit": self.tp1_done,
            "tp2_hit": self.tp2_done,
            "stop_moved": self.stop_moved,
            "structural_target": self.structural_target,
            "structural_target_r": (
                s * (self.structural_target - self.entry_price) / self.stop_distance
                if self.structural_target is not None
                else None
            ),
        }
        conf = self.confirmed_bar is not None or self.confirmed_at_entry
        row["confirmed_after_entry"] = conf
        row["confirmed_at_entry"] = self.confirmed_at_entry
        row["bars_to_confirmation"] = (
            (self.confirmed_bar - self.entry_bar) if self.confirmed_bar is not None else None
        )
        row["hours_to_confirmation"] = (
            (self.confirmed_ms - self.entry_ms) / 3_600_000.0
            if self.confirmed_ms is not None
            else None
        )
        row["mfe_before_confirm_R"] = (
            s * (self.mfe_before_confirm - self.entry_price) / self.stop_distance
            if not math.isnan(self.mfe_before_confirm)
            else None
        )
        row["mae_before_confirm_R"] = (
            s * (self.mae_before_confirm - self.entry_price) / self.stop_distance
            if not math.isnan(self.mae_before_confirm)
            else None
        )
        row["early_exit_reason"] = (
            self.exits[-1].reason.value
            if self.exits
            and self.exits[-1].reason
            in (ExitReason.EARLY_EXIT_NO_CONFIRMATION, ExitReason.EARLY_EXIT_INVALIDATION)
            else None
        )
        for r in self.r_levels:
            row[f"cf_hit_{r:g}R"] = self.cf_hits[r]
            row[f"cf_bars_to_{r:g}R"] = self.cf_bars_to_hit[r]
        for k, v in self.features.items():
            row[f"f_{k}"] = v
        return row
