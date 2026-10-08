"""Demo credentials: read ONLY from environment variables, never logged, never persisted.
Missing or empty variables fail closed (no authenticated request is possible)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from btc_swing.v5.demo.config import DemoExecConfig


class DemoCredentialsMissingError(RuntimeError):
    """Raised when the demo API key or secret environment variable is missing or empty."""


@dataclass(frozen=True)
class DemoCredentials:
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)

    def __repr__(self) -> str:
        return "DemoCredentials(api_key=***, api_secret=***)"

    __str__ = __repr__

    def secrets(self) -> tuple[str, str]:
        return self.api_key, self.api_secret


def load_demo_credentials(cfg: DemoExecConfig) -> DemoCredentials:
    k = os.environ.get(cfg.credentials_env.api_key, "").strip()
    s = os.environ.get(cfg.credentials_env.api_secret, "").strip()
    missing = [
        n
        for n, v in ((cfg.credentials_env.api_key, k), (cfg.credentials_env.api_secret, s))
        if not v
    ]
    if missing:
        raise DemoCredentialsMissingError(
            f"demo credentials missing: set {', '.join(missing)} (values are never logged)"
        )
    return DemoCredentials(k, s)


def credentials_present(cfg: DemoExecConfig) -> bool:
    return all(
        os.environ.get(n, "").strip()
        for n in (cfg.credentials_env.api_key, cfg.credentials_env.api_secret)
    )
