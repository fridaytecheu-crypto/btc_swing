"""Bybit DEMO execution configuration. Hard guarantees encoded here:
- the REST base must be exactly https://api-demo.bybit.com (no production host can be configured);
- the default mode is DISABLED;
- the demo risk per trade must equal the frozen V5 risk per trade."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

DEMO_REST_HOST = "api-demo.bybit.com"
DEMO_REST_BASE = f"https://{DEMO_REST_HOST}"
SMOKE_TAG = "EXECUTION_SMOKE"
STRATEGY_TAG = "STRATEGY_DEMO"


class ExecutionMode(StrEnum):
    DISABLED = "DISABLED"
    EXECUTION_SMOKE = "EXECUTION_SMOKE"
    STRATEGY_DEMO = "STRATEGY_DEMO"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CredentialsEnvCfg(_Strict):
    api_key: str
    api_secret: str


class SmokeCfg(_Strict):
    leverage: int = Field(ge=1, le=10)
    limit_offset_frac: float = Field(gt=0.02, lt=0.5)
    stop_offset_frac: float = Field(gt=0, lt=0.2)
    tp_offset_frac: float = Field(gt=0, lt=0.2)
    fill_timeout_s: float = Field(gt=0)
    poll_interval_s: float = Field(gt=0)


class FailsafeCfg(_Strict):
    collector_stale_s: float = Field(gt=0)
    bar_stale_s: float = Field(gt=0)
    entry_max_delay_s: float = Field(gt=0)
    gap_lookback_bars: int = Field(ge=0)
    ack_timeout_s: float = Field(gt=0)
    fill_timeout_s: float = Field(gt=0)


class DemoExecConfig(_Strict):
    mode: ExecutionMode = ExecutionMode.DISABLED
    rest_base: str = DEMO_REST_BASE
    category: str
    symbol: str
    account_type: str
    recv_window_ms: int = Field(ge=1000, le=60000)
    http_timeout_s: float = Field(gt=0)
    credentials_env: CredentialsEnvCfg
    reference_equity_usdt: float = Field(gt=0)
    risk_per_trade: float = Field(gt=0, le=0.02)
    stop_trigger_by: str
    smoke: SmokeCfg
    failsafe: FailsafeCfg

    @field_validator("rest_base")
    @classmethod
    def _demo_only(cls, v: str) -> str:
        assert_demo_url(v)
        return v.rstrip("/")


def assert_demo_url(url: str) -> None:
    """Raise unless `url` is an https URL on the Bybit DEMO REST host. Production hosts
    (api.bybit.com, api.bytick.com, regional mirrors, testnet) are all rejected."""
    u = urlparse(url)
    if (
        u.scheme != "https"
        or (u.hostname or "").lower() != DEMO_REST_HOST
        or u.port not in (None, 443)
    ):
        raise EndpointNotAllowedError(
            f"only {DEMO_REST_BASE} is allowed for authenticated requests (got {u.scheme}://{u.hostname})"
        )


class EndpointNotAllowedError(RuntimeError):
    """A request would leave the Bybit DEMO host."""


DEFAULT_DEMO_CONFIG_PATH = Path("config/btc_swing_v5_demo.yaml")


def load_demo_config(
    path: str | Path | None = None, mode_override: ExecutionMode | None = None
) -> DemoExecConfig:
    p = Path(path) if path else DEFAULT_DEMO_CONFIG_PATH
    with p.open() as f:
        raw: dict[str, Any] = yaml.safe_load(f)
    if mode_override is not None:
        raw = {**raw, "mode": mode_override.value}
    return DemoExecConfig.model_validate(raw)
