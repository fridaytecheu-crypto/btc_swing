"""Endpoint allow-list, execution modes and credential loading (fail closed)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

DEMO_REST_BASE = "https://api-demo.bybit.com"
DEMO_WS_PRIVATE = "wss://stream-demo.bybit.com/v5/private"
ALLOWED_REST_HOSTS = frozenset({"api-demo.bybit.com"})
ALLOWED_PRIVATE_WS_HOSTS = frozenset({"stream-demo.bybit.com"})
# Production / non-demo Bybit REST hosts: authenticated requests to any of them are refused.
FORBIDDEN_REST_HOSTS = frozenset(
    {
        "api.bybit.com",
        "api.bytick.com",
        "api.bybit.nl",
        "api.byhkbit.com",
        "api.bybit.kz",
        "api.bybitgeorgia.ge",
        "api.bybit-tr.com",
        "api.bybit.eu",
        "api-testnet.bybit.com",
    }
)

ENV_API_KEY = "BYBIT_DEMO_API_KEY"
ENV_API_SECRET = "BYBIT_DEMO_API_SECRET"
ENV_MODE = "BYBIT_EXECUTION_MODE"


class ExecutionMode(StrEnum):
    DISABLED = "DISABLED"
    EXECUTION_SMOKE = "EXECUTION_SMOKE"
    STRATEGY_DEMO = "STRATEGY_DEMO"


class DemoGuardError(RuntimeError):
    """A request would leave the Bybit Demo environment or break a fail-closed rule."""


class MissingDemoCredentialsError(DemoGuardError):
    """BYBIT_DEMO_API_KEY / BYBIT_DEMO_API_SECRET are not set: nothing authenticated may run."""


class ExecutionDisabledError(DemoGuardError):
    """A state-changing request was attempted while the execution mode does not allow it."""


def mode_from_env(env: dict[str, str] | None = None) -> ExecutionMode:
    raw = (env if env is not None else dict(os.environ)).get(ENV_MODE, "").strip()
    if not raw:
        return ExecutionMode.DISABLED
    try:
        return ExecutionMode(raw)
    except ValueError as e:
        raise DemoGuardError(
            f"{ENV_MODE}={raw!r} is not one of {[m.value for m in ExecutionMode]}"
        ) from e


def assert_demo_rest_url(url: str) -> str:
    """Return the host if `url` is an https URL on the Bybit Demo REST host, else raise."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        raise DemoGuardError(f"refused non-https URL for Bybit Demo REST: scheme {parts.scheme!r}")
    if host in FORBIDDEN_REST_HOSTS:
        raise DemoGuardError(f"refused PRODUCTION / non-demo Bybit host {host!r}")
    if host not in ALLOWED_REST_HOSTS:
        raise DemoGuardError(f"refused host {host!r}: only {sorted(ALLOWED_REST_HOSTS)} is allowed")
    if parts.port not in (None, 443):
        raise DemoGuardError(f"refused port {parts.port}")
    return host


def assert_demo_private_ws_url(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "wss" or host not in ALLOWED_PRIVATE_WS_HOSTS:
        raise DemoGuardError(f"refused private stream URL host {host!r} scheme {parts.scheme!r}")
    return host


@dataclass(frozen=True)
class DemoCredentials:
    api_key: str
    api_secret: str

    def __repr__(self) -> str:  # never render the secret (or the key) in logs / tracebacks
        return "DemoCredentials(<redacted>)"

    __str__ = __repr__

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> DemoCredentials:
        e = env if env is not None else dict(os.environ)
        key, secret = e.get(ENV_API_KEY, "").strip(), e.get(ENV_API_SECRET, "").strip()
        missing = [n for n, v in ((ENV_API_KEY, key), (ENV_API_SECRET, secret)) if not v]
        if missing:
            raise MissingDemoCredentialsError(
                f"missing environment variable(s): {', '.join(missing)} — failing closed"
            )
        return cls(key, secret)

    def redact(self, obj: Any) -> Any:
        """Deep-copy `obj` with every occurrence of the key or the secret replaced."""
        if isinstance(obj, str):
            out = obj
            for s in (self.api_secret, self.api_key):
                if s and s in out:
                    out = out.replace(s, "<redacted>")
            return out
        if isinstance(obj, dict):
            return {self.redact(str(k)): self.redact(v) for k, v in obj.items()}
        if isinstance(obj, list | tuple):
            return [self.redact(v) for v in obj]
        return obj
