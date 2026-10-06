"""Execution abstraction for a future Bybit DEMO integration. DESIGNED, NOT ACTIVATED.

Nothing in this module talks to a network, reads credentials or places orders. `ExecutionAdapter`
is the interface the forward paper engine would use; `DryRunAdapter` records intents in memory;
`BybitDemoAdapter` holds the endpoint configuration and raises `NotActivatedError` on every network
method until an owner-approved activation adds the authenticated transport (a separate, future
change that must never be part of a research phase).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol


class NotActivatedError(RuntimeError):
    """Raised by the Bybit Demo adapter: execution is not enabled in this research phase."""


class OrderSide(StrEnum):
    BUY = "Buy"
    SELL = "Sell"


class OrderType(StrEnum):
    MARKET = "Market"
    LIMIT = "Limit"


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: OrderSide
    qty: float
    order_type: OrderType = OrderType.MARKET
    price: float | None = None
    reduce_only: bool = False
    client_order_id: str | None = None


@dataclass(frozen=True)
class OrderAck:
    order_id: str
    client_order_id: str | None
    accepted: bool
    message: str = ""


@dataclass(frozen=True)
class PositionState:
    symbol: str
    side: str  # "Buy" | "Sell" | "None"
    size: float
    entry_price: float
    mark_price: float
    liquidation_price: float | None
    leverage: float
    unrealised_pnl: float
    stop_loss: float | None
    take_profit: float | None


@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    side: OrderSide
    qty: float
    price: float
    fee: float
    ts_ms: int


@dataclass(frozen=True)
class FundingEvent:
    symbol: str
    rate: float
    ts_ms: int
    amount: float


@dataclass(frozen=True)
class Balance:
    equity: float
    available: float
    currency: str = "USDT"


class ExecutionAdapter(Protocol):
    def place_order(self, req: OrderRequest) -> OrderAck: ...
    def cancel_order(self, symbol: str, order_id: str) -> OrderAck: ...
    def position_state(self, symbol: str) -> PositionState: ...
    def set_stop(self, symbol: str, stop_price: float) -> OrderAck: ...
    def set_take_profit(self, symbol: str, tp_price: float) -> OrderAck: ...
    def account_balance(self) -> Balance: ...
    def fills(self, symbol: str, since_ms: int) -> list[Fill]: ...
    def funding(self, symbol: str, since_ms: int) -> list[FundingEvent]: ...


@dataclass
class DryRunAdapter:
    """Records every intent; never fills, never touches a network. For wiring and tests."""

    equity: float = 10_000.0
    intents: list[dict[str, object]] = field(default_factory=list)
    _n: int = 0

    def _ack(self, kind: str, **kw: object) -> OrderAck:
        self._n += 1
        self.intents.append({"kind": kind, "ts": datetime.now(UTC).isoformat(), **kw})
        return OrderAck(
            order_id=f"dry-{self._n}",
            client_order_id=str(kw.get("client_order_id") or ""),
            accepted=True,
            message="dry-run: recorded, not sent",
        )

    def place_order(self, req: OrderRequest) -> OrderAck:
        return self._ack(
            "place_order",
            symbol=req.symbol,
            side=req.side.value,
            qty=req.qty,
            order_type=req.order_type.value,
            price=req.price,
            reduce_only=req.reduce_only,
            client_order_id=req.client_order_id,
        )

    def cancel_order(self, symbol: str, order_id: str) -> OrderAck:
        return self._ack("cancel_order", symbol=symbol, order_id=order_id)

    def position_state(self, symbol: str) -> PositionState:
        return PositionState(symbol, "None", 0.0, 0.0, 0.0, None, 1.0, 0.0, None, None)

    def set_stop(self, symbol: str, stop_price: float) -> OrderAck:
        return self._ack("set_stop", symbol=symbol, stop_price=stop_price)

    def set_take_profit(self, symbol: str, tp_price: float) -> OrderAck:
        return self._ack("set_take_profit", symbol=symbol, tp_price=tp_price)

    def account_balance(self) -> Balance:
        return Balance(self.equity, self.equity)

    def fills(self, symbol: str, since_ms: int) -> list[Fill]:
        return []

    def funding(self, symbol: str, since_ms: int) -> list[FundingEvent]:
        return []


@dataclass(frozen=True)
class BybitDemoConfig:
    rest_base: str = "https://api-demo.bybit.com"
    ws_private: str = "wss://stream-demo.bybit.com/v5/private"
    category: str = "linear"
    symbol: str = "BTCUSDT"
    recv_window_ms: int = 5000
    activated: bool = False  # must stay False in every research phase


class BybitDemoAdapter:
    """Endpoint map and request shapes for Bybit v5 Demo trading; every method raises NotActivatedError.

    Planned mapping (documented, not executed): place_order -> POST /v5/order/create;
    cancel_order -> POST /v5/order/cancel; position_state -> GET /v5/position/list;
    set_stop / set_take_profit -> POST /v5/position/trading-stop; account_balance ->
    GET /v5/account/wallet-balance; fills -> GET /v5/execution/list; funding ->
    GET /v5/account/transaction-log (type FUNDING). Authentication (API key, timestamp,
    recv_window, HMAC-SHA256 signature) is intentionally absent here.
    """

    def __init__(self, cfg: BybitDemoConfig | None = None) -> None:
        self.cfg = cfg or BybitDemoConfig()
        if self.cfg.activated:
            raise NotActivatedError(
                "Bybit Demo execution cannot be activated inside the V5 research phase"
            )

    def _blocked(self, what: str) -> NotActivatedError:
        return NotActivatedError(
            f"{what}: Bybit Demo adapter is designed but not activated (no credentials, no network)"
        )

    def place_order(self, req: OrderRequest) -> OrderAck:
        raise self._blocked("place_order")

    def cancel_order(self, symbol: str, order_id: str) -> OrderAck:
        raise self._blocked("cancel_order")

    def position_state(self, symbol: str) -> PositionState:
        raise self._blocked("position_state")

    def set_stop(self, symbol: str, stop_price: float) -> OrderAck:
        raise self._blocked("set_stop")

    def set_take_profit(self, symbol: str, tp_price: float) -> OrderAck:
        raise self._blocked("set_take_profit")

    def account_balance(self) -> Balance:
        raise self._blocked("account_balance")

    def fills(self, symbol: str, since_ms: int) -> list[Fill]:
        raise self._blocked("fills")

    def funding(self, symbol: str, since_ms: int) -> list[FundingEvent]:
        raise self._blocked("funding")
