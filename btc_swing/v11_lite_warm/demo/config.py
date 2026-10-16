"""V11-LITE Demo execution config. Hard guarantees: the REST host is the Bybit DEMO host (inherited
from the shared DemoExecConfig validator); risk is exactly 0.10 % of 5000 USDT; leverage <= 2; the safety
caps are the owner's; the file itself never enables execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from btc_swing.v5.demo.config import (
    CredentialsEnvCfg,
    DemoExecConfig,
    ExecutionMode,
    load_demo_config,
)

DEFAULT_DEMO_CONFIG = Path("config/btc_swing_v11_lite_warm_demo.yaml")
TAG = "STRATEGY_DEMO_V11LW"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CredSets(_Strict):
    isolated: CredentialsEnvCfg
    shared: CredentialsEnvCfg


class RiskCfg(_Strict):
    reference_equity_usdt: float = Field(gt=0)
    risk_per_trade: float = Field(gt=0, le=0.02)
    leverage: int = Field(ge=1, le=2)

    @property
    def risk_usdt(self) -> float:
        return self.reference_equity_usdt * self.risk_per_trade


class SafetyCfg(_Strict):
    max_trades_rolling_24h: int = Field(ge=1, le=10)
    daily_stop_usdt: float = Field(gt=0)
    max_consecutive_full_losses: int = Field(ge=1)
    full_loss_r: float = Field(lt=0)
    entry_max_delay_s: float = Field(gt=0, le=600)


class FailsafeCfg(_Strict):
    fill_timeout_s: float = Field(gt=0)
    exec_confirm_timeout_s: float = Field(gt=0, le=120)


class LiteDemoConfig(_Strict):
    demo_exec_config: str
    account_mode: Literal["isolated", "shared"]
    credentials_env: CredSets
    v10_credentials_env: CredentialsEnvCfg
    order_prefix: str
    v10_order_prefix: str
    risk: RiskCfg
    safety: SafetyCfg
    failsafe: FailsafeCfg

    @model_validator(mode="after")
    def _owner_spec(self) -> LiteDemoConfig:
        if (
            abs(self.risk.reference_equity_usdt - 5000.0) > 1e-9
            or abs(self.risk.risk_per_trade - 0.001) > 1e-12
        ):
            raise ValueError("V11-LITE Demo risk is the owner's 0.10 % of 5000 USDT (5 USDT)")
        if self.order_prefix == self.v10_order_prefix or not self.order_prefix.startswith("V11LWD"):
            raise ValueError("V11-LITE Demo order prefix must be V11LWD- and differ from V10's")
        if self.credentials_env.isolated == self.v10_credentials_env:
            raise ValueError("isolated credentials must not be V10's environment variables")
        return self

    def canonical_yaml(self) -> str:
        return yaml.safe_dump(json.loads(self.model_dump_json()), sort_keys=True)

    @property
    def config_hash(self) -> str:
        return hashlib.sha256(self.canonical_yaml().encode()).hexdigest()

    @property
    def creds(self) -> CredentialsEnvCfg:
        return (
            self.credentials_env.isolated
            if self.account_mode == "isolated"
            else self.credentials_env.shared
        )

    @property
    def shares_v10_account(self) -> bool:
        return self.account_mode == "shared"


def load_lite_demo_config(path: str | Path | None = None) -> LiteDemoConfig:
    p = Path(path) if path is not None else DEFAULT_DEMO_CONFIG
    raw: dict[str, Any] = yaml.safe_load(p.read_text())
    return LiteDemoConfig.model_validate(raw)


def exec_config(lc: LiteDemoConfig, mode: ExecutionMode) -> DemoExecConfig:
    """The shared demo-only client config with V11-LITE's credentials and risk (the base file's
    mode stays DISABLED on disk; `mode` applies to this process only)."""
    base = load_demo_config(lc.demo_exec_config, mode)
    return base.model_copy(
        update={"credentials_env": lc.creds, "risk_per_trade": lc.risk.risk_per_trade}
    )


def v10_exec_config(lc: LiteDemoConfig) -> DemoExecConfig:
    base = load_demo_config(lc.demo_exec_config, ExecutionMode.DISABLED)
    return base.model_copy(update={"credentials_env": lc.v10_credentials_env})
