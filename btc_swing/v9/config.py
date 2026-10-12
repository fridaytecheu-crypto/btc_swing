"""V9 configuration (strict Pydantic, YAML, hash). Every rule threshold lives here."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from btc_swing.core.config import RiskCfg
from btc_swing.core.hashing import sha256_text

V9_VERSION = "9.0.0-simple-trend-volume-pullback"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WindowCfg(_Strict):
    start: str
    end_exclusive: str


class WindowsCfg(_Strict):
    train: WindowCfg
    validation_1: WindowCfg
    recent_validation: WindowCfg
    locked_holdout: WindowCfg
    warmup_days: int = Field(ge=30)


class TrendCfg(_Strict):
    ema_fast: int = Field(ge=2)
    ema_slow: int = Field(ge=3)


class ImpulseCfg(_Strict):
    atr_period: int = Field(ge=2)
    max_leg_bars: int = Field(ge=2, le=24)
    size_atr: float = Field(gt=0)
    size_atr_grid: list[float]
    raw_size_atr: float = Field(gt=0)
    rvol_min: float = Field(gt=0)
    rvol_grid: list[float]
    rvol_baseline_days: int = Field(ge=5)
    rvol_min_days: int = Field(ge=3)


class PullbackCfg(_Strict):
    min_bars: int = Field(ge=1)
    max_bars: int = Field(ge=2, le=24)
    retrace_min: float = Field(gt=0, lt=1)
    retrace_max: float = Field(gt=0, lt=1)
    retrace_grid: list[list[float]]
    volume_ratio_max: float = Field(gt=0)
    volume_ratio_grid: list[float]


class TriggerCfg(_Strict):
    lookback_min: int = Field(ge=2)
    rvol_min: float = Field(gt=0)
    rvol_grid: list[float]
    rvol_baseline_min: int = Field(ge=5)
    imbalance_min: float = Field(ge=0, lt=1)
    imbalance_grid: list[float]
    delta_mode: str = Field(pattern="^(sign|median)$")
    delta_median_window_min: int = Field(ge=10)
    accel_baseline_min: int = Field(ge=2)
    accel_gate_candidate: float = Field(gt=0)


class StopCfg(_Strict):
    buffer_atr: float = Field(ge=0)
    min_distance_pct: float = Field(gt=0)
    min_distance_grid: list[float]


class ExitsCfg(_Strict):
    primary: str = Field(pattern="^[ABC]$")
    exit_a_r: float = Field(gt=0)
    exit_b_r: float = Field(gt=0)
    split_tp1_r: float = Field(gt=0)
    split_tp2_r: float = Field(gt=0)
    time_stop_min: int = Field(ge=10)
    time_stop_grid: list[int]
    progress_min_r: float = Field(ge=0)
    hard_max_multiple: int = Field(ge=1)


class CostsCfg(_Strict):
    taker_fee_bps: float = Field(ge=0)
    half_spread_bps: float = Field(ge=0)
    entry_slippage_bps: float = Field(ge=0)
    exit_slippage_bps: float = Field(ge=0)
    stop_slippage_bps: float = Field(ge=0)
    apply_funding: bool
    stress_multiplier: float = Field(ge=1)


class V9RiskCfg(RiskCfg):
    risk_grid: list[float]


class SelectionCfg(_Strict):
    min_folds_positive: int = Field(ge=1)
    min_trades_per_day: float = Field(ge=0)
    accel_min_gain_r: float = Field(ge=0)
    frequency_target: list[float]
    max_drawdown_pct: float = Field(gt=0)


class V9Config(_Strict):
    strategy_name: str
    windows: WindowsCfg
    trend: TrendCfg
    impulse: ImpulseCfg
    pullback: PullbackCfg
    trigger: TriggerCfg
    stop: StopCfg
    exits: ExitsCfg
    costs: CostsCfg
    risk: V9RiskCfg
    selection: SelectionCfg

    def canonical_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=True)

    @property
    def config_hash(self) -> str:
        return sha256_text(self.canonical_yaml())


DEFAULT_V9_CONFIG_PATH = Path("config/btc_swing_v9.yaml")


def load_v9_config(path: str | Path | None = None) -> V9Config:
    p = Path(path) if path else DEFAULT_V9_CONFIG_PATH
    with p.open() as f:
        return V9Config.model_validate(yaml.safe_load(f))
