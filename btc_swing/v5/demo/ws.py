"""Private Bybit DEMO WebSocket authentication check (wss://stream-demo.bybit.com/v5/private).

Auth message: {"op": "auth", "args": [api_key, expires_ms, HMAC_SHA256(secret, "GET/realtime" + expires)]}.
The auth frame is never logged or journaled; only the server's reply (success flag, message,
connection id) and the latency are returned."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
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
