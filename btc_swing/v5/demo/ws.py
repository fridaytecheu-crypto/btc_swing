"""Private Bybit DEMO WebSocket authentication check (wss://stream-demo.bybit.com/v5/private).

Auth message: {"op": "auth", "args": [api_key, expires_ms, HMAC_SHA256(secret, "GET/realtime" + expires)]}.
The auth frame is never logged or journaled; only the server's reply (success flag, message,
connection id) and the latency are returned."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import threading
import time
from collections.abc import Callable
from typing import Any

from btc_swing.v5.demo.config import DemoExecConfig, assert_demo_ws_url
from btc_swing.v5.demo.credentials import DemoCredentials


def ws_signature(secret: str, expires_ms: int) -> str:
    return hmac.new(
        secret.encode(), f"GET/realtime{expires_ms}".encode(), hashlib.sha256
    ).hexdigest()


async def _auth(
    cfg: DemoExecConfig, creds: DemoCredentials, connect: Callable[..., Any], timeout_s: float
) -> dict[str, Any]:
    assert_demo_ws_url(cfg.ws_private)
    expires = int((time.time() + 10) * 1000)
    msg = json.dumps(
        {
            "req_id": "v5demo-auth",
            "op": "auth",
            "args": [creds.api_key, expires, ws_signature(creds.api_secret, expires)],
        }
    )
    t0 = time.time()
    async with connect(cfg.ws_private, open_timeout=timeout_s, close_timeout=5) as ws:
        await ws.send(msg)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time()))
            d = json.loads(raw)
            if d.get("op") == "auth":
                return {
                    "ok": bool(d.get("success")),
                    "ret_msg": d.get("ret_msg"),
                    "conn_id": d.get("conn_id"),
                    "latency_ms": (time.time() - t0) * 1000,
                    "endpoint": cfg.ws_private,
                }
    return {
        "ok": False,
        "ret_msg": "no auth reply before timeout",
        "latency_ms": (time.time() - t0) * 1000,
        "endpoint": cfg.ws_private,
    }


def private_ws_auth(
    cfg: DemoExecConfig,
    creds: DemoCredentials,
    connect: Callable[..., Any] | None = None,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    if connect is None:
        import websockets

        connect = websockets.connect
    try:
        return asyncio.run(_auth(cfg, creds, connect, timeout_s))
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        for sct in creds.secrets():
            if sct:
                msg = msg.replace(sct, "***REDACTED***")
        return {"ok": False, "ret_msg": msg[:300], "endpoint": cfg.ws_private}


class ExecutionStream:
    """Authenticated private `execution.<category>` stream on the DEMO WebSocket, run in a
    background thread. Execution rows are buffered (never journaled here) and served to
    `fills.confirm_executions`, which prefers them over polling `/v5/execution/list`. Fail-soft:
    if the stream cannot authenticate/subscribe or drops, `available` is False and callers fall
    back to REST polling. The auth frame and the credentials are never logged or returned."""

    def __init__(
        self,
        cfg: DemoExecConfig,
        creds: DemoCredentials,
        connect: Callable[..., Any] | None = None,
        timeout_s: float = 10.0,
        ping_s: float = 20.0,
    ) -> None:
        assert_demo_ws_url(cfg.ws_private)
        self.cfg, self._creds, self._connect = cfg, creds, connect
        self.timeout_s, self.ping_s = timeout_s, ping_s
        self.topic = f"execution.{cfg.category}"
        self._rows: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.available = False
        self.status: dict[str, Any] = {"endpoint": cfg.ws_private, "topic": self.topic}

    def _redact(self, msg: str) -> str:
        for s in self._creds.secrets():
            if s:
                msg = msg.replace(s, "***REDACTED***")
        return msg[:300]

    async def _run(self) -> None:
        connect = self._connect
        if connect is None:
            import websockets

            connect = websockets.connect
        expires = int((time.time() + 10) * 1000)
        auth = json.dumps(
            {
                "req_id": "v5demo-exec-auth",
                "op": "auth",
                "args": [
                    self._creds.api_key,
                    expires,
                    ws_signature(self._creds.api_secret, expires),
                ],
            }
        )
        async with connect(self.cfg.ws_private, open_timeout=self.timeout_s, close_timeout=5) as ws:
            await ws.send(auth)
            subscribed = False
            last_ping = time.time()
            deadline = time.time() + self.timeout_s
            while not self._stop.is_set():
                if not subscribed and time.time() > deadline:
                    self.status["error"] = "no auth/subscribe confirmation before timeout"
                    return
                if time.time() - last_ping >= self.ping_s:
                    await ws.send(json.dumps({"op": "ping"}))
                    last_ping = time.time()
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                except TimeoutError:
                    continue
                d = json.loads(raw)
                op = d.get("op")
                if op == "auth":
                    if not d.get("success"):
                        self.status["error"] = f"auth failed: {d.get('ret_msg')}"
                        return
                    await ws.send(
                        json.dumps(
                            {"req_id": "v5demo-exec-sub", "op": "subscribe", "args": [self.topic]}
                        )
                    )
                elif op == "subscribe":
                    if not d.get("success"):
                        self.status["error"] = f"subscribe failed: {d.get('ret_msg')}"
                        return
                    subscribed = True
                    self.available = True
                    self.status["subscribed"] = True
                    self._ready.set()
                elif str(d.get("topic", "")).startswith("execution"):
                    with self._lock:
                        self._rows += [dict(x) for x in d.get("data") or []]

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as e:
            self.status["error"] = self._redact(f"{type(e).__name__}: {e}")
        finally:
            self.available = False
            self._ready.set()

    def start(self) -> dict[str, Any]:
        """Connect, authenticate and subscribe; returns the (secret-free) status."""
        self._thread = threading.Thread(target=self._thread_main, name="demo-exec-ws", daemon=True)
        self._thread.start()
        self._ready.wait(self.timeout_s + 1)
        return {**self.status, "available": self.available}

    def rows(self) -> list[dict[str, Any]] | None:
        """Buffered execution rows, or None when the stream is not available."""
        if not self.available:
            return None
        with self._lock:
            return list(self._rows)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.available = False
