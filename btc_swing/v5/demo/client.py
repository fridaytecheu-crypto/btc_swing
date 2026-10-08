"""Bybit V5 DEMO REST client (https://api-demo.bybit.com only).

Safety properties (each covered by tests):
- refuses to exist in mode DISABLED (no authenticated request can happen);
- every request URL is re-checked against the demo host before it is sent;
- credentials come only from environment variables (fail closed when missing);
- request headers (which carry the key and signature) are never journaled; every journaled
  payload is scrubbed of the key and secret strings;
- requests and responses are journaled to the caller's hash-chained journal.

Signing (Bybit V5): sign = HMAC_SHA256(secret, timestamp + api_key + recv_window + payload) where
payload is the exact query string (GET) or the exact JSON body (POST)."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

import httpx

from btc_swing.v5.demo.config import DemoExecConfig, ExecutionMode, assert_demo_url
from btc_swing.v5.demo.credentials import DemoCredentials, load_demo_credentials
from btc_swing.v5.demo.journal import HashChainJournal

log = logging.getLogger(__name__)
REDACTED = "***REDACTED***"
# retCodes that mean "already in the requested state" (idempotent success)
IDEMPOTENT_OK = {
    "/v5/position/set-leverage": {110043},  # leverage not modified
    "/v5/position/trading-stop": {34040},  # not modified
}
RET_DUPLICATE_LINK_ID = 110072
RET_ORDER_NOT_EXISTS = {110001, 170213}


class ExecutionDisabledError(RuntimeError):
    """The execution mode does not allow authenticated requests."""


class DemoApiError(RuntimeError):
    def __init__(self, path: str, ret_code: int, ret_msg: str) -> None:
        super().__init__(f"{path}: retCode {ret_code} {ret_msg}")
        self.path, self.ret_code, self.ret_msg = path, ret_code, ret_msg


class DemoTransportError(RuntimeError):
    def __init__(
        self, path: str, msg: str, status: int | None = None, geo_blocked: bool = False
    ) -> None:
        super().__init__(f"{path}: {msg}")
        self.path, self.status, self.geo_blocked = path, status, geo_blocked


def sign(secret: str, timestamp: int, api_key: str, recv_window: int, payload: str) -> str:
    return hmac.new(
        secret.encode(), f"{timestamp}{api_key}{recv_window}{payload}".encode(), hashlib.sha256
    ).hexdigest()


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


class BybitDemoClient:
    def __init__(
        self,
        cfg: DemoExecConfig,
        mode: ExecutionMode,
        journal: HashChainJournal,
        tag: str,
        credentials: DemoCredentials | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
        read_only: bool = False,
    ) -> None:
        """`read_only=True` (preflight): only GET requests can be sent; allowed while DISABLED.
        Otherwise DISABLED refuses to construct a client at all."""
        if mode is ExecutionMode.DISABLED and not read_only:
            raise ExecutionDisabledError(
                "execution mode is DISABLED: no state-changing request is allowed"
            )
        self.read_only = read_only
        assert_demo_url(cfg.rest_base)
        self.cfg, self.mode, self.journal, self.tag = cfg, mode, journal, tag
        self.creds = credentials if credentials is not None else load_demo_credentials(cfg)
        self._secrets = [s for s in self.creds.secrets() if s]
        self._clock = clock
        self.time_offset_ms = 0
        self.last_ok_ms: int | None = None
        self.calls = 0
        self._http = httpx.Client(
            base_url=cfg.rest_base,
            timeout=cfg.http_timeout_s,
            transport=transport,
            follow_redirects=False,
        )

    # ------------------------------------------------------------------ plumbing
    def close(self) -> None:
        self._http.close()

    def now_ms(self) -> int:
        return int(self._clock() * 1000) + self.time_offset_ms

    def _scrub(self, obj: Any) -> Any:
        if isinstance(obj, str):
            for s in self._secrets:
                if s and s in obj:
                    obj = obj.replace(s, REDACTED)
            return obj
        if isinstance(obj, dict):
            return {k: self._scrub(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._scrub(v) for v in obj]
        return obj

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        if self.read_only and method != "GET":
            raise ExecutionDisabledError(f"read-only client: {method} {path} refused")
        qs = urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = path + (f"?{qs}" if qs else "")
        assert_demo_url(str(self._http.base_url.join(url)))
        payload = qs if method == "GET" else json.dumps(body or {}, separators=(",", ":"))
        headers = {"Content-Type": "application/json"}
        if auth:
            ts = self.now_ms()
            rw = self.cfg.recv_window_ms
            headers.update(
                {
                    "X-BAPI-API-KEY": self.creds.api_key,
                    "X-BAPI-TIMESTAMP": str(ts),
                    "X-BAPI-RECV-WINDOW": str(rw),
                    "X-BAPI-SIGN-TYPE": "2",
                    "X-BAPI-SIGN": sign(self.creds.api_secret, ts, self.creds.api_key, rw, payload),
                }
            )
        req_rec = {
            "host": self._http.base_url.host,
            "method": method,
            "path": path,
            "params": params or None,
            "body": body if method == "POST" else None,
            "auth": auth,
        }
        t0 = time.time()
        self.calls += 1
        try:
            if method == "GET":
                resp = self._http.get(url, headers=headers)
            else:
                resp = self._http.post(url, headers=headers, content=payload)
        except httpx.HTTPError as e:
            self.journal.append(
                "api_error",
                self._scrub(
                    {
                        "request": req_rec,
                        "error": f"{type(e).__name__}: {e}"[:300],
                        "latency_ms": (time.time() - t0) * 1000,
                    }
                ),
                self.tag,
            )
            raise DemoTransportError(path, f"{type(e).__name__}: {e}"[:200]) from None
        latency = (time.time() - t0) * 1000
        text = resp.text
        if resp.status_code != 200:
            geo = resp.status_code == 403 and (
                "country" in text.lower() or "cloudfront" in text.lower()
            )
            self.journal.append(
                "api_error",
                self._scrub(
                    {
                        "request": req_rec,
                        "http_status": resp.status_code,
                        "body": text[:300],
                        "latency_ms": latency,
                        "geo_blocked": geo,
                    }
                ),
                self.tag,
            )
            raise DemoTransportError(
                path, f"HTTP {resp.status_code}: {text[:160]}", resp.status_code, geo
            )
        try:
            data: dict[str, Any] = resp.json()
        except ValueError:
            self.journal.append(
                "api_error",
                self._scrub(
                    {
                        "request": req_rec,
                        "http_status": 200,
                        "body": text[:300],
                        "latency_ms": latency,
                    }
                ),
                self.tag,
            )
            raise DemoTransportError(path, "non-JSON response") from None
        ret = int(data.get("retCode", -1))
        self.journal.append(
            "api",
            self._scrub({"request": req_rec, "response": data, "latency_ms": latency}),
            self.tag,
        )
        if ret != 0 and ret not in IDEMPOTENT_OK.get(path, set()):
            raise DemoApiError(path, ret, str(data.get("retMsg", "")))
        self.last_ok_ms = int(time.time() * 1000)
        return data

    # ------------------------------------------------------------------ public / account
    def sync_time(self) -> dict[str, Any]:
        local = int(self._clock() * 1000)
        d = self._request("GET", "/v5/market/time", auth=False)
        server = int(d.get("time") or int(d["result"]["timeNano"]) // 1_000_000)
        self.time_offset_ms = server - local
        return {"server_ms": server, "offset_ms": self.time_offset_ms}

    def account_info(self) -> dict[str, Any]:
        return dict(self._request("GET", "/v5/account/info")["result"])

    def api_key_info(self) -> dict[str, Any]:
        """GET /v5/user/query-api, reduced to non-secret fields (the key string itself is dropped)."""
        r = self._request("GET", "/v5/user/query-api")["result"]
        keep = (
            "readOnly",
            "permissions",
            "type",
            "isMaster",
            "uta",
            "unified",
            "vipLevel",
            "expiredAt",
            "createdAt",
            "deadlineDay",
            "kycLevel",
            "kycRegion",
        )
        return {k: r.get(k) for k in keep if k in r}

    def wallet_balance(self) -> dict[str, Any]:
        r = self._request(
            "GET", "/v5/account/wallet-balance", {"accountType": self.cfg.account_type}
        )["result"]
        lst = r.get("list") or [{}]
        acct = lst[0]
        usdt: dict[str, Any] = next(
            (c for c in acct.get("coin", []) if c.get("coin") == "USDT"), {}
        )
        return {
            "total_equity": _f(acct.get("totalEquity")),
            "available": _f(acct.get("totalAvailableBalance")),
            "usdt_wallet": _f(usdt.get("walletBalance")),
            "usdt_equity": _f(usdt.get("equity")),
        }

    def instrument(self) -> dict[str, Any]:
        r = self._request(
            "GET",
            "/v5/market/instruments-info",
            {"category": self.cfg.category, "symbol": self.cfg.symbol},
            auth=False,
        )["result"]
        it = (r.get("list") or [{}])[0]
        lot, pf, lev = (
            it.get("lotSizeFilter", {}),
            it.get("priceFilter", {}),
            it.get("leverageFilter", {}),
        )
        return {
            "symbol": it.get("symbol"),
            "status": it.get("status"),
            "contract_type": it.get("contractType"),
            "min_qty": _f(lot.get("minOrderQty")),
            "qty_step": _f(lot.get("qtyStep")),
            "max_qty": _f(lot.get("maxOrderQty")),
            "max_mkt_qty": _f(lot.get("maxMktOrderQty")),
            "min_notional": _f(lot.get("minNotionalValue")),
            "tick": _f(pf.get("tickSize")),
            "min_leverage": _f(lev.get("minLeverage")),
            "max_leverage": _f(lev.get("maxLeverage")),
            "leverage_step": _f(lev.get("leverageStep")),
        }

    def ticker(self) -> dict[str, Any]:
        r = self._request(
            "GET",
            "/v5/market/tickers",
            {"category": self.cfg.category, "symbol": self.cfg.symbol},
            auth=False,
        )["result"]
        t = (r.get("list") or [{}])[0]
        return {
            "last": _f(t.get("lastPrice")),
            "mark": _f(t.get("markPrice")),
            "bid": _f(t.get("bid1Price")),
            "ask": _f(t.get("ask1Price")),
        }

    # ------------------------------------------------------------------ position / orders
    def position(self) -> dict[str, Any]:
        r = self._request(
            "GET", "/v5/position/list", {"category": self.cfg.category, "symbol": self.cfg.symbol}
        )["result"]
        p = (r.get("list") or [{}])[0]
        size = _f(p.get("size")) if p.get("size") not in (None, "") else 0.0
        return {
            "size": size,
            "side": p.get("side") or "",
            "avg_price": _f(p.get("avgPrice")),
            "leverage": _f(p.get("leverage")),
            "stop_loss": _f(p.get("stopLoss"))
            if p.get("stopLoss") not in (None, "", "0")
            else None,
            "take_profit": _f(p.get("takeProfit"))
            if p.get("takeProfit") not in (None, "", "0")
            else None,
            "unrealised_pnl": _f(p.get("unrealisedPnl")),
            "liq_price": _f(p.get("liqPrice")),
            "mark_price": _f(p.get("markPrice")),
            "updated_ms": p.get("updatedTime"),
        }

    def set_leverage(self, lev: float) -> dict[str, Any]:
        v = f"{lev:g}"
        d = self._request(
            "POST",
            "/v5/position/set-leverage",
            body={
                "category": self.cfg.category,
                "symbol": self.cfg.symbol,
                "buyLeverage": v,
                "sellLeverage": v,
            },
        )
        return {"ret_code": d.get("retCode"), "leverage": v}

    def create_order(
        self,
        side: str,
        qty: str,
        order_type: str,
        link_id: str,
        price: str | None = None,
        reduce_only: bool = False,
        tif: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "category": self.cfg.category,
            "symbol": self.cfg.symbol,
            "side": side,
            "orderType": order_type,
            "qty": qty,
            "orderLinkId": link_id,
            "positionIdx": 0,
            "reduceOnly": reduce_only,
        }
        if price is not None:
            body["price"] = price
        body["timeInForce"] = tif or ("GTC" if order_type == "Limit" else "IOC")
        return dict(self._request("POST", "/v5/order/create", body=body)["result"])

    def cancel_order(self, link_id: str) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                "/v5/order/cancel",
                body={
                    "category": self.cfg.category,
                    "symbol": self.cfg.symbol,
                    "orderLinkId": link_id,
                },
            )["result"]
        )

    def order(self, link_id: str) -> dict[str, Any] | None:
        """Order by orderLinkId: open/recent first, then history. None when unknown to Bybit."""
        for path, extra in (("/v5/order/realtime", {"openOnly": 0}), ("/v5/order/history", {})):
            r = self._request(
                "GET",
                path,
                {
                    "category": self.cfg.category,
                    "symbol": self.cfg.symbol,
                    "orderLinkId": link_id,
                    **extra,
                },
            )["result"]
            lst = r.get("list") or []
            if lst:
                return dict(lst[0])
        return None

    def open_orders(self) -> list[dict[str, Any]]:
        r = self._request(
            "GET",
            "/v5/order/realtime",
            {"category": self.cfg.category, "symbol": self.cfg.symbol, "openOnly": 0},
        )["result"]
        return [dict(o) for o in (r.get("list") or [])]

    def executions(
        self, link_id: str | None = None, start_ms: int | None = None
    ) -> list[dict[str, Any]]:
        r = self._request(
            "GET",
            "/v5/execution/list",
            {
                "category": self.cfg.category,
                "symbol": self.cfg.symbol,
                "orderLinkId": link_id,
                "startTime": start_ms,
                "limit": 100,
            },
        )["result"]
        return [dict(x) for x in (r.get("list") or [])]

    def trading_stop(
        self, stop_loss: str | None = None, take_profit: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "category": self.cfg.category,
            "symbol": self.cfg.symbol,
            "tpslMode": "Full",
            "positionIdx": 0,
        }
        if stop_loss is not None:
            body.update({"stopLoss": stop_loss, "slTriggerBy": self.cfg.stop_trigger_by})
        if take_profit is not None:
            body.update({"takeProfit": take_profit, "tpTriggerBy": self.cfg.stop_trigger_by})
        d = self._request("POST", "/v5/position/trading-stop", body=body)
        return {"ret_code": d.get("retCode")}

    def closed_pnl(self, start_ms: int | None = None) -> list[dict[str, Any]]:
        r = self._request(
            "GET",
            "/v5/position/closed-pnl",
            {
                "category": self.cfg.category,
                "symbol": self.cfg.symbol,
                "startTime": start_ms,
                "limit": 50,
            },
        )["result"]
        return [dict(x) for x in (r.get("list") or [])]

    def funding(self, start_ms: int) -> list[dict[str, Any]]:
        r = self._request(
            "GET",
            "/v5/account/transaction-log",
            {
                "accountType": self.cfg.account_type,
                "category": self.cfg.category,
                "currency": "USDT",
                "type": "SETTLEMENT",
                "startTime": start_ms,
                "limit": 50,
            },
        )["result"]
        return [
            dict(x) for x in (r.get("list") or []) if x.get("symbol") in (None, "", self.cfg.symbol)
        ]


def _decimals(step: float) -> int:
    t = f"{step:.10f}".rstrip("0")
    return len(t.split(".")[1]) if "." in t else 0


def fmt_qty(qty: float, step: float) -> str:
    """Floor to the exchange quantity step (never rounds up: rounding up would raise the risk)."""
    n = math.floor(qty / step + 1e-9)
    return f"{n * step:.{_decimals(step)}f}"


def fmt_price(price: float, tick: float, side_sign: float = 0.0) -> str:
    """Round to the tick: down for sign < 0, up for sign > 0, nearest for 0."""
    k = price / tick
    n = (
        math.floor(k + 1e-9)
        if side_sign < 0
        else (math.ceil(k - 1e-9) if side_sign > 0 else round(k))
    )
    return f"{n * tick:.{_decimals(tick)}f}"
