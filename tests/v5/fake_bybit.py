"""In-process fake of the Bybit V5 DEMO REST API for offline tests (httpx.MockTransport).

Implements the endpoints the demo client uses with an in-memory account, verifies every
signature, rejects duplicate orderLinkIds (110072), fills market orders (optionally partially),
rests limit orders, triggers stops/TPs/limits on `move_price`, and can inject faults: lost
acknowledgement (order processed, client sees a timeout), API errors, and a CloudFront geo-block."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlparse

import httpx

TAKER = 0.00055
MAKER = 0.0002


@dataclass
class FakeBybitDemo:
    api_key: str = "test-key-123"
    api_secret: str = "test-secret-456"
    price: float = 60000.0
    min_qty: float = 0.001
    qty_step: float = 0.001
    tick: float = 0.1
    equity: float = 50000.0
    now_ms: int = 1_800_000_000_000
    geo_blocked: bool = False
    partial_fill_frac: float | None = None  # next market order fills only this fraction
    drop_ack_next_create: bool = False  # next create is processed but the client sees a timeout
    fail_next: dict[str, int] = field(default_factory=dict)  # path -> retCode
    # Bybit order creation is asynchronous: an order can be Filled before /v5/execution/list shows
    # its executions. New executions stay invisible for this many execution-list queries.
    exec_lag_queries: int = 0
    exec_split_next: int | None = None  # next fill is reported as this many partial executions
    duplicate_exec_rows: bool = False  # execution list repeats every row (same execId)
    closed_pnl_lag_queries: int = 0  # closed-PnL records stay invisible for this many queries
    # multi-asset demo account: other coins' equity (in USDT) drifts by this much per wallet query
    other_assets_drift_per_query: float = 0.0
    _other_assets: float = 0.0
    orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    position: dict[str, Any] = field(
        default_factory=lambda: {
            "side": "",
            "size": 0.0,
            "avg": 0.0,
            "sl": None,
            "tp": None,
            "lev": 1.0,
        }
    )
    executions: list[dict[str, Any]] = field(default_factory=list)
    closed: list[dict[str, Any]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    _oid: int = 0
    _xid: int = 0
    _realised: float = 0.0
    _fees_open: float = 0.0

    # ------------------------------------------------------------------ transport
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        u = urlparse(str(req.url))
        path = u.path
        params = dict(parse_qsl(u.query))
        body = json.loads(req.content or b"{}") if req.method == "POST" else {}
        self.requests.append(
            {
                "host": u.hostname,
                "path": path,
                "method": req.method,
                "headers": dict(req.headers),
                "params": params,
                "body": body,
            }
        )
        if self.geo_blocked:
            return httpx.Response(
                403,
                text='{"error":"The Amazon CloudFront distribution is configured to block access from your country."}',
            )
        public = path in ("/v5/market/time", "/v5/market/instruments-info", "/v5/market/tickers")
        if not public:
            h = req.headers
            payload = u.query if req.method == "GET" else (req.content or b"").decode()
            want = hmac.new(
                self.api_secret.encode(),
                f"{h.get('X-BAPI-TIMESTAMP')}{h.get('X-BAPI-API-KEY')}{h.get('X-BAPI-RECV-WINDOW')}{payload}".encode(),
                hashlib.sha256,
            ).hexdigest()
            if h.get("X-BAPI-API-KEY") != self.api_key or h.get("X-BAPI-SIGN") != want:
                return self._ok({}, 10004, "error sign!")
        if path in self.fail_next:
            code = self.fail_next.pop(path)
            return self._ok({}, code, "injected error")
        fn: Any = {
            "/v5/market/time": self._time,
            "/v5/account/info": lambda p, b: self._ok(
                {"unifiedMarginStatus": 5, "marginMode": "REGULAR_MARGIN"}
            ),
            "/v5/account/wallet-balance": self._wallet,
            "/v5/user/query-api": lambda p, b: self._ok(
                {
                    "id": "1",
                    "apiKey": self.api_key,
                    "readOnly": 0,
                    "permissions": {"ContractTrade": ["Order", "Position"]},
                    "type": 1,
                    "isMaster": True,
                    "uta": 1,
                }
            ),
            "/v5/market/instruments-info": self._instrument,
            "/v5/market/tickers": self._ticker,
            "/v5/position/list": self._position,
            "/v5/position/set-leverage": self._set_lev,
            "/v5/order/create": self._create,
            "/v5/order/cancel": self._cancel,
            "/v5/order/realtime": self._realtime,
            "/v5/order/history": self._history,
            "/v5/execution/list": self._exec_list,
            "/v5/position/trading-stop": self._trading_stop,
            "/v5/position/closed-pnl": self._closed_pnl,
            "/v5/account/transaction-log": lambda p, b: self._ok({"list": []}),
        }.get(path)
        if fn is None:
            return self._ok({}, 10001, f"unknown path {path}")
        out = fn(params, body)
        if (
            path == "/v5/order/create"
            and self.drop_ack_next_create
            and out.status_code == 200
            and json.loads(out.content)["retCode"] == 0
        ):
            self.drop_ack_next_create = False
            raise httpx.ReadTimeout("injected: acknowledgement lost", request=req)
        return out

    def _ok(self, result: dict[str, Any], code: int = 0, msg: str = "OK") -> httpx.Response:
        return httpx.Response(
            200, json={"retCode": code, "retMsg": msg, "result": result, "time": self.now_ms}
        )

    # ------------------------------------------------------------------ endpoints
    def _time(self, p: dict[str, Any], b: dict[str, Any]) -> httpx.Response:
        return self._ok(
            {"timeSecond": str(self.now_ms // 1000), "timeNano": str(self.now_ms * 1_000_000)}
        )

    def _wallet(self, p: dict[str, Any], b: dict[str, Any]) -> httpx.Response:
        eq = self.equity + self._realised
        self._other_assets += self.other_assets_drift_per_query
        return self._ok(
            {
                "list": [
                    {
                        "totalEquity": str(eq + self._other_assets),
                        "totalAvailableBalance": str(eq),
                        "coin": [{"coin": "USDT", "walletBalance": str(eq), "equity": str(eq)}],
                    }
                ]
            }
        )

    def _instrument(self, p: dict[str, Any], b: dict[str, Any]) -> httpx.Response:
        return self._ok(
            {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "status": "Trading",
                        "lotSizeFilter": {
                            "minOrderQty": str(self.min_qty),
                            "qtyStep": str(self.qty_step),
                            "maxOrderQty": "100",
                            "maxMktOrderQty": "50",
                            "minNotionalValue": "5",
                        },
                        "priceFilter": {"tickSize": str(self.tick)},
                        "leverageFilter": {
                            "minLeverage": "1",
                            "maxLeverage": "100",
                            "leverageStep": "0.01",
                        },
                    }
                ]
            }
        )

    def _ticker(self, p: dict[str, Any], b: dict[str, Any]) -> httpx.Response:
        return self._ok(
            {
                "list": [
                    {
                        "lastPrice": str(self.price),
                        "markPrice": str(self.price),
                        "bid1Price": str(self.price - self.tick),
                        "ask1Price": str(self.price),
                    }
                ]
            }
        )

    def _position(self, p: dict[str, Any], b: dict[str, Any]) -> httpx.Response:
        ps = self.position
        upnl = (
            (self.price - ps["avg"]) * ps["size"] * (1 if ps["side"] == "Buy" else -1)
            if ps["size"]
            else 0.0
        )
        return self._ok(
            {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "side": ps["side"] if ps["size"] else "",
                        "size": f"{ps['size']:.3f}",
                        "avgPrice": str(ps["avg"]),
                        "leverage": str(ps["lev"]),
                        "stopLoss": str(ps["sl"] or ""),
                        "takeProfit": str(ps["tp"] or ""),
                        "unrealisedPnl": str(upnl),
                        "liqPrice": "",
                        "markPrice": str(self.price),
                        "updatedTime": str(self.now_ms),
                    }
                ]
            }
        )

    def _set_lev(self, p: dict[str, Any], b: dict[str, Any]) -> httpx.Response:
        lev = float(b["buyLeverage"])
        if lev == self.position["lev"]:
            return self._ok({}, 110043, "leverage not modified")
        self.position["lev"] = lev
        return self._ok({})

    def _create(self, p: dict[str, Any], b: dict[str, Any]) -> httpx.Response:
        link = b["orderLinkId"]
        if link in self.orders:
            return self._ok({}, 110072, "OrderLinkedID is duplicate")
        self._oid += 1
        qty = float(b["qty"])
        if qty < self.min_qty - 1e-12:
            return self._ok({}, 10001, "qty below minimum")
        o = {
            "orderId": f"oid{self._oid}",
            "orderLinkId": link,
            "side": b["side"],
            "orderType": b["orderType"],
            "qty": qty,
            "price": float(b.get("price") or 0),
            "orderStatus": "New",
            "cumExecQty": 0.0,
            "avgPrice": 0.0,
            "cumExecFee": 0.0,
            "reduceOnly": bool(b.get("reduceOnly")),
            "timeInForce": b.get("timeInForce"),
            "createdTime": self.now_ms,
        }
        self.orders[link] = o
        if b["orderType"] == "Market":
            frac = self.partial_fill_frac
            self.partial_fill_frac = None
            fq = qty if frac is None else round(qty * frac / self.qty_step) * self.qty_step
            if o["reduceOnly"]:
                fq = min(fq, self.position["size"])
            self._fill(o, fq, self.price, TAKER)
            o["orderStatus"] = "Filled" if fq >= qty - 1e-12 else "PartiallyFilledCanceled"
        elif b.get("timeInForce") == "PostOnly" and (
            (b["side"] == "Buy" and o["price"] >= self.price)
            or (b["side"] == "Sell" and o["price"] <= self.price)
        ):
            o["orderStatus"] = "Rejected"
        return self._ok({"orderId": o["orderId"], "orderLinkId": link})

    def _cancel(self, p: dict[str, Any], b: dict[str, Any]) -> httpx.Response:
        o = self.orders.get(b.get("orderLinkId", ""))
        if o is None or o["orderStatus"] not in ("New", "PartiallyFilled", "Untriggered"):
            return self._ok({}, 110001, "order not exists or too late to cancel")
        o["orderStatus"] = "Cancelled"
        return self._ok({"orderId": o["orderId"], "orderLinkId": o["orderLinkId"]})

    def _view(self, o: dict[str, Any]) -> dict[str, Any]:
        return {
            **o,
            "qty": f"{o['qty']:.3f}",
            "price": str(o["price"]),
            "cumExecQty": f"{o['cumExecQty']:.3f}",
            "avgPrice": str(o["avgPrice"]),
            "cumExecFee": str(o["cumExecFee"]),
            "createdTime": str(o["createdTime"]),
        }

    def _realtime(self, p: dict[str, Any], b: dict[str, Any]) -> httpx.Response:
        link = p.get("orderLinkId")
        lst = [o for o in self.orders.values() if (link is None or o["orderLinkId"] == link)]
        if str(p.get("openOnly", "0")) == "0" and link is None:
            lst = [o for o in lst if o["orderStatus"] in ("New", "PartiallyFilled", "Untriggered")]
        return self._ok({"list": [self._view(o) for o in lst]})

    def _history(self, p: dict[str, Any], b: dict[str, Any]) -> httpx.Response:
        link = p.get("orderLinkId")
        return self._ok(
            {
                "list": [
                    self._view(o)
                    for o in self.orders.values()
                    if link is None or o["orderLinkId"] == link
                ]
            }
        )

    def _exec_list(self, p: dict[str, Any], b: dict[str, Any]) -> httpx.Response:
        link = p.get("orderLinkId")
        start = int(p.get("startTime") or 0)
        out = []
        for e in self.executions:
            if e["_hidden"] > 0:
                e["_hidden"] -= 1
                continue
            if (link is None or e["orderLinkId"] == link) and int(e["execTime"]) >= start:
                row = {k: v for k, v in e.items() if not k.startswith("_")}
                out += [row, dict(row)] if self.duplicate_exec_rows else [row]
        return self._ok({"list": out})

    def _trading_stop(self, p: dict[str, Any], b: dict[str, Any]) -> httpx.Response:
        if self.position["size"] <= 0:
            return self._ok({}, 10001, "can not set tp/sl/ts for zero position")
        if "stopLoss" in b:
            self.position["sl"] = float(b["stopLoss"]) if b["stopLoss"] not in ("", "0") else None
        if "takeProfit" in b:
            self.position["tp"] = (
                float(b["takeProfit"]) if b["takeProfit"] not in ("", "0") else None
            )
        return self._ok({})

    def _closed_pnl(self, p: dict[str, Any], b: dict[str, Any]) -> httpx.Response:
        start = int(p.get("startTime") or 0)
        out = []
        for c in reversed(self.closed):
            if c["_hidden"] > 0:
                c["_hidden"] -= 1
                continue
            if int(c["createdTime"]) >= start:
                out.append({k: v for k, v in c.items() if not k.startswith("_")})
        return self._ok({"list": out})

    # ------------------------------------------------------------------ matching
    def _fill(self, o: dict[str, Any], qty: float, price: float, fee_rate: float) -> None:
        if qty <= 0:
            return
        ps = self.position
        n = self.exec_split_next or 1
        self.exec_split_next = None
        lots = round(qty / self.qty_step)
        parts = [lots // n + (1 if i < lots % n else 0) for i in range(n)]
        parts = [x for x in parts if x > 0]
        fee = 0.0
        for i, lot in enumerate(parts):
            pq = lot * self.qty_step
            px = price + i * self.tick  # partial executions at successive ticks
            pf = pq * px * fee_rate
            fee += pf
            self._xid += 1
            self.executions.append(
                {
                    "execId": f"x{self._xid:06d}",
                    "orderLinkId": o["orderLinkId"],
                    "orderId": o["orderId"],
                    "execPrice": str(px),
                    "execQty": f"{pq:.3f}",
                    "execFee": str(pf),
                    "execTime": str(self.now_ms + i),
                    "execType": "Trade",
                    "side": o["side"],
                    "_hidden": self.exec_lag_queries,
                }
            )
        vwap = sum(x * self.qty_step * (price + i * self.tick) for i, x in enumerate(parts)) / qty
        o["avgPrice"] = (o["avgPrice"] * o["cumExecQty"] + vwap * qty) / (o["cumExecQty"] + qty)
        o["cumExecQty"] += qty
        o["cumExecFee"] += fee
        price = vwap
        same = ps["size"] == 0 or (ps["side"] == o["side"])
        if same and not o["reduceOnly"]:
            ps["avg"] = (ps["avg"] * ps["size"] + price * qty) / (ps["size"] + qty)
            ps["size"] += qty
            ps["side"] = o["side"]
            self._fees_open += fee
        else:
            sgn = 1 if ps["side"] == "Buy" else -1
            pnl = (price - ps["avg"]) * qty * sgn
            open_fee_part = self._fees_open * qty / ps["size"]
            self._fees_open -= open_fee_part
            self._realised += pnl - fee - open_fee_part
            ps["size"] = round(ps["size"] - qty, 9)
            self.closed.append(
                {
                    "symbol": "BTCUSDT",
                    "orderId": o["orderId"],
                    "side": o["side"],
                    "qty": f"{qty:.3f}",
                    "avgEntryPrice": str(ps["avg"]),
                    "avgExitPrice": str(price),
                    "closedPnl": str(pnl - fee - open_fee_part),
                    "createdTime": str(self.now_ms),
                    "_hidden": self.closed_pnl_lag_queries,
                }
            )
            if ps["size"] <= 1e-12:
                ps.update({"size": 0.0, "side": "", "avg": 0.0, "sl": None, "tp": None})
                for x in self.orders.values():
                    if x["reduceOnly"] and x["orderStatus"] in (
                        "New",
                        "PartiallyFilled",
                        "Untriggered",
                    ):
                        x["orderStatus"] = "Deactivated"

    def move_price(self, price: float, advance_ms: int = 60_000) -> None:
        """Move the last price; trigger position stop/TP and resting limit orders."""
        self.price = price
        self.now_ms += advance_ms
        ps = self.position
        for o in list(self.orders.values()):
            if (
                o["orderType"] == "Limit"
                and o["orderStatus"] in ("New", "PartiallyFilled")
                and (
                    (o["side"] == "Buy" and price <= o["price"])
                    or (o["side"] == "Sell" and price >= o["price"])
                )
            ):
                q = o["qty"] - o["cumExecQty"]
                if o["reduceOnly"]:
                    q = min(q, ps["size"])
                self._fill(o, q, o["price"], MAKER)
                o["orderStatus"] = (
                    "Filled" if o["cumExecQty"] >= o["qty"] - 1e-12 else o["orderStatus"]
                )
        if ps["size"] > 0:
            long = ps["side"] == "Buy"
            hit_sl = ps["sl"] is not None and (
                (long and price <= ps["sl"]) or (not long and price >= ps["sl"])
            )
            hit_tp = ps["tp"] is not None and (
                (long and price >= ps["tp"]) or (not long and price <= ps["tp"])
            )
            if hit_sl or hit_tp:
                self._oid += 1
                o = {
                    "orderId": f"oid{self._oid}",
                    "orderLinkId": "",
                    "side": "Sell" if long else "Buy",
                    "orderType": "Market",
                    "qty": ps["size"],
                    "price": 0.0,
                    "orderStatus": "Filled",
                    "cumExecQty": 0.0,
                    "avgPrice": 0.0,
                    "cumExecFee": 0.0,
                    "reduceOnly": True,
                    "timeInForce": "IOC",
                    "createdTime": self.now_ms,
                    "stopOrderType": "StopLoss" if hit_sl else "TakeProfit",
                }
                self.orders[f"_tpsl{self._oid}"] = o
                self._fill(o, ps["size"], price, TAKER)

    def hosts(self) -> set[str]:
        return {str(r["host"]) for r in self.requests}
