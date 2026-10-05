"""Leverage never determines risk. The chain is:

  account equity -> allowed account risk (risk_per_trade)
                 -> stop distance (structural, from the plan)
                 -> position size  (qty = risk / stop distance)
                 -> required leverage (smallest allowed leverage whose isolated margin fits the
                    margin cap), subject to liquidation-buffer checks.

Isolated-margin liquidation estimate (fees and funding ignored, maintenance margin rate `mmr`):
  long : liq = entry * (1 - 1/L + mmr)
  short: liq = entry * (1 + 1/L - mmr)
A configuration is rejected when the distance to liquidation is less than
`min_stop_to_liquidation_ratio` stop distances or less than `min_liquidation_distance_atr` ATRs
of the configured timeframe. If the full-risk notional does not fit under the margin cap at any
liquidation-safe leverage, the position is scaled down (RISK_CAPPED) rather than levered up.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from btc_swing.core.config import RiskCfg
from btc_swing.core.enums import Side


@dataclass(frozen=True)
class Sizing:
    accepted: bool
    reason: str
    equity: float
    risk_amount: float
    risk_frac: float
    entry_ref: float
    stop_price: float
    stop_distance: float
    qty: float
    notional: float
    leverage: float
    margin: float
    liquidation_price: float
    liquidation_distance: float
    stop_to_liquidation_ratio: float
    liquidation_distance_atr: float
    risk_capped: bool
    max_account_loss_at_stop: float  # risk_amount expressed as a fraction of equity
    max_account_loss_at_liquidation: float  # margin / equity

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def liquidation_price(entry: float, side: Side, leverage: float, mmr: float) -> float:
    if side is Side.LONG:
        return entry * (1.0 - 1.0 / leverage + mmr)
    return entry * (1.0 + 1.0 / leverage - mmr)


def _reject(reason: str, equity: float, entry: float, stop: float, cfg: RiskCfg) -> Sizing:
    return Sizing(
        False,
        reason,
        equity,
        equity * cfg.risk_per_trade,
        cfg.risk_per_trade,
        entry,
        stop,
        abs(entry - stop),
        0.0,
        0.0,
        0.0,
        0.0,
        math.nan,
        math.nan,
        math.nan,
        math.nan,
        False,
        0.0,
        0.0,
    )


def size_position(
    equity: float, entry_ref: float, stop_price: float, side: Side, atr_liq_tf: float, cfg: RiskCfg
) -> Sizing:
    stop_distance = side.sign * (entry_ref - stop_price)
    if stop_distance <= 0 or math.isnan(stop_distance):
        return _reject("STOP_ON_WRONG_SIDE", equity, entry_ref, stop_price, cfg)
    if equity <= 0:
        return _reject("NO_EQUITY", equity, entry_ref, stop_price, cfg)
    risk_amount = equity * cfg.risk_per_trade
    full_qty = risk_amount / stop_distance
    full_notional = full_qty * entry_ref
    margin_cap = equity * cfg.margin_cap_frac
    best_scaled: tuple[float, float] | None = None  # (leverage, notional)
    for lev in cfg.allowed_leverage:
        if lev > cfg.max_leverage:
            continue
        liq = liquidation_price(entry_ref, side, lev, cfg.maintenance_margin_rate)
        liq_dist = side.sign * (entry_ref - liq)
        if liq_dist < cfg.min_stop_to_liquidation_ratio * stop_distance:
            continue
        if not math.isnan(atr_liq_tf) and liq_dist < cfg.min_liquidation_distance_atr * atr_liq_tf:
            continue
        max_notional = lev * margin_cap
        if full_notional <= max_notional:
            return _build(
                True,
                "OK",
                equity,
                risk_amount,
                entry_ref,
                stop_price,
                stop_distance,
                full_qty,
                lev,
                liq,
                liq_dist,
                atr_liq_tf,
                False,
                cfg,
            )
        if best_scaled is None or max_notional > best_scaled[1]:
            best_scaled = (lev, max_notional)
    if best_scaled is None:
        return _reject(
            "LIQUIDATION_BUFFER_INSUFFICIENT_AT_ALL_LEVERAGE", equity, entry_ref, stop_price, cfg
        )
    lev, notional = best_scaled
    liq = liquidation_price(entry_ref, side, lev, cfg.maintenance_margin_rate)
    qty = notional / entry_ref
    return _build(
        True,
        "RISK_CAPPED_BY_MARGIN_CAP",
        equity,
        qty * stop_distance,
        entry_ref,
        stop_price,
        stop_distance,
        qty,
        lev,
        liq,
        side.sign * (entry_ref - liq),
        atr_liq_tf,
        True,
        cfg,
    )


def _build(
    accepted: bool,
    reason: str,
    equity: float,
    risk_amount: float,
    entry: float,
    stop: float,
    stop_distance: float,
    qty: float,
    lev: float,
    liq: float,
    liq_dist: float,
    atr_liq: float,
    capped: bool,
    cfg: RiskCfg,
) -> Sizing:
    notional = qty * entry
    margin = notional / lev
    return Sizing(
        accepted=accepted,
        reason=reason,
        equity=equity,
        risk_amount=risk_amount,
        risk_frac=risk_amount / equity,
        entry_ref=entry,
        stop_price=stop,
        stop_distance=stop_distance,
        qty=qty,
        notional=notional,
        leverage=lev,
        margin=margin,
        liquidation_price=liq,
        liquidation_distance=liq_dist,
        stop_to_liquidation_ratio=liq_dist / stop_distance,
        liquidation_distance_atr=(liq_dist / atr_liq)
        if atr_liq and not math.isnan(atr_liq)
        else math.nan,
        risk_capped=capped,
        max_account_loss_at_stop=risk_amount / equity,
        max_account_loss_at_liquidation=margin / equity,
    )
