"""Signed Bybit V5 REST client bound to the DEMO host only.

Signature (Bybit V5, HMAC-SHA256, hex): timestamp + api_key + recv_window + (query string for GET |
compact JSON body for POST). Every request and response is journaled WITHOUT headers, with the key
and the secret redacted from anything that is written. The base URL is fixed to the Demo host and
re-checked for every request; in DISABLED mode only GET requests are allowed."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

from btc_swing.v5.demo.guard import (
    DEMO_REST_BASE,
    DemoCredentials,
    ExecutionDisabledError,
    ExecutionMode,
    assert_demo_rest_url,
)
from btc_swing.v5.demo.journal import HashChainJournal

RECV_WINDOW_MS = 5000


class Transport(Protocol):
    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: str | None
    ) -> tuple[int, dict[str, Any]]: ...


class HttpxTransport:
    """Real network transport (httpx, honours HTTPS_PROXY / SSL_CERT_FILE via trust_env)."""

    def __init__(self, timeout_s: float = 10.0) -> None:
        import httpx

        self._client = httpx.Client(timeout=timeout_s, trust_env=True)

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: str | None
    ) -> tuple[int, dict[str, Any]]:
        assert_demo_rest_url(url)
        r = self._client.request(method, url, headers=headers, content=body)
        try:
            payload = r.json()
            if not isinstance(payload, dict):
                payload = {"raw": payload}
        except ValueError:
            payload = {"raw_text": r.text[:500]}
        return r.status_code, payload


def sign(secret: str, timestamp_ms: str, api_key: str, recv_window: str, payload: str) -> str:
    msg = f"{timestamp_ms}{api_key}{recv_window}{payload}"
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


@dataclass
class ApiResult:
    method: str
    path: str
    ok: bool
    http_status: int | None
    ret_code: int | None
    ret_msg: str | None
    result: Any
    latency_ms: float | None
    error: str | None = None
    host: str = ""

    def summary(self) -> str:
        if self.error:
            return f"{self.method} {self.path}: ERROR {self.error}"
        return f"{self.method} {self.path}: HTTP {self.http_status}, retCode {self.ret_code} ({self.ret_msg})"


@dataclass
class BybitDemoClient:
    creds: DemoCredentials
    mode: ExecutionMode
    journal: HashChainJournal
    transport: Transport
    tag: str
    base: str = DEMO_REST_BASE
    recv_window_ms: int = RECV_WINDOW_MS
    clock_ms: Callable[[], int] = field(default=lambda: int(time.time() * 1000))
    hosts_contacted: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        assert_demo_rest_url(self.base)

    def _journal(self, rec: dict[str, Any]) -> None:
        self.journal.append(self.creds.redact({"tag": self.tag, "mode": self.mode.value, **rec}))

    def request(
        self, method: str, path: str, params: dict[str, Any] | None = None, auth: bool = True
    ) -> ApiResult:
        method = method.upper()
        params = dict(params or {})
        if method != "GET" and self.mode is ExecutionMode.DISABLED:
            self._journal(
                {
                    "kind": "REFUSED",
                    "method": method,
                    "path": path,
                    "reason": "mode DISABLED: only read-only GET requests are allowed",
                }
            )
            raise ExecutionDisabledError(f"{method} {path} refused: execution mode is DISABLED")
        if method == "GET":
            qs = urlencode(params)
            url = f"{self.base}{path}" + (f"?{qs}" if qs else "")
            body, payload = None, qs
        else:
            url = f"{self.base}{path}"
            body = json.dumps(params, separators=(",", ":"))
            payload = body
        host = assert_demo_rest_url(url)
        headers = {"Content-Type": "application/json"}
        if auth:
            ts = str(self.clock_ms())
            rw = str(self.recv_window_ms)
            headers |= {
                "X-BAPI-API-KEY": self.creds.api_key,
                "X-BAPI-TIMESTAMP": ts,
                "X-BAPI-RECV-WINDOW": rw,
                "X-BAPI-SIGN-TYPE": "2",
                "X-BAPI-SIGN": sign(self.creds.api_secret, ts, self.creds.api_key, rw, payload),
            }
        self.hosts_contacted.add(host)
        self._journal(
            {
                "kind": "REQUEST",
                "method": method,
                "host": host,
                "path": path,
                "params": params,
                "authenticated": auth,
            }
        )
        t0 = time.perf_counter()
        try:
            status, resp = self.transport(method, url, headers, body)
        except Exception as e:  # network / TLS / proxy failure
            res = ApiResult(
                method, path, False, None, None, None, None, None, f"{type(e).__name__}: {e}", host
            )
            self._journal(
                {
                    "kind": "RESPONSE",
                    "method": method,
                    "host": host,
                    "path": path,
                    "error": res.error,
                }
            )
            return res
        lat = (time.perf_counter() - t0) * 1000.0
        rc = resp.get("retCode")
        res = ApiResult(
            method,
            path,
            status == 200 and rc == 0,
            status,
            int(rc) if isinstance(rc, int) else None,
            resp.get("retMsg") if isinstance(resp.get("retMsg"), str) else None,
            resp.get("result", resp),
            round(lat, 1),
            None if status == 200 else f"HTTP {status}: {str(resp)[:300]}",
            host,
        )
        self._journal(
            {
                "kind": "RESPONSE",
                "method": method,
                "host": host,
                "path": path,
                "http_status": status,
                "ret_code": res.ret_code,
                "ret_msg": res.ret_msg,
                "result": res.result,
                "latency_ms": res.latency_ms,
                "error": res.error,
            }
        )
        return res

    def get(self, path: str, params: dict[str, Any] | None = None) -> ApiResult:
        return self.request("GET", path, params, auth=True)

    def post(self, path: str, params: dict[str, Any] | None = None) -> ApiResult:
        return self.request("POST", path, params, auth=True)


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()
