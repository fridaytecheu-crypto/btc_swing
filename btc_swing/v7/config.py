"""V7 Phase 1 configuration (strict Pydantic, YAML, hash)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from btc_swing.core.hashing import sha256_text

V7_VERSION = "7.0.0-discovery"
V7_FEATURE_VERSION = "v7-feat-1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WindowCfg(_Strict):
    start: str
    end_exclusive: str


class WindowsCfg(_Strict):
    discovery: WindowCfg
    validation_1: WindowCfg
    recent_validation: WindowCfg
    locked_holdout: WindowCfg
    warmup_days: int = Field(ge=30)


class FeaturesCfg(_Strict):
    atr_period: int = Field(ge=2)
    z_window_min: int = Field(ge=60)
    z_min_periods: int = Field(ge=30)
    burst_short_min: int = Field(ge=1)
    burst_long_min: int = Field(ge=5)
    flow_window_min: int = Field(ge=1)
    response_quantile_window_min: int = Field(ge=60)
    strong_flow_pct: float = Field(gt=0, lt=1)
    weak_response_pct: float = Field(gt=0, lt=1)
    strong_response_pct: float = Field(gt=0, lt=1)
    compression_range_min: int = Field(ge=5)
    compression_history_min: int = Field(ge=60)
    rv_tercile_history_min: int = Field(ge=60)
    range_window_min: int = Field(ge=5)
    impulse_window_min: int = Field(ge=15)
    ema_fast: int = Field(ge=2)
    ema_slow: int = Field(ge=2)
    swing_k: int = Field(ge=1, le=5)
    trend_slope_bars_1h: int = Field(ge=1)
    large_trade_keys: list[str]
    large_trade_anchor: str


class AnchorCfg(_Strict):
    anchor: Any
    grid: list[Any]


class DecisionCfg(_Strict):
    min_effect_atr_60: float = Field(gt=0)
    min_consistent_half_years: int = Field(ge=1)


class AnchorsCfg(_Strict):
    intensity_burst_tc_z: AnchorCfg
    imbalance_abs: AnchorCfg
    large_trade_imbalance_abs: AnchorCfg
    delta_accel_pct: AnchorCfg
    compression_pct: AnchorCfg
    pullback_depth: AnchorCfg
    anti_chase_atr: AnchorCfg
    engine_tc_z: float
    engine_imbalance: float
    engine_large_trade: float
    engine_impulse_atr: float
    engine_volume_expansion: float


class DiscoveryCfg(_Strict):
    horizons_min: list[int]
    max_horizon_min: int = Field(ge=15)
    move_pcts: list[float]
    before_pairs: list[list[float]]
    cluster_min: int = Field(ge=1)
    controls_per_event: int = Field(ge=1)
    control_exclusion_min: int = Field(ge=1)
    bootstrap_resamples: int = Field(ge=100)
    seed: int
    decision: DecisionCfg
    anchors: AnchorsCfg


class V7Config(_Strict):
    strategy_name: str
    windows: WindowsCfg
    features: FeaturesCfg
    discovery: DiscoveryCfg

    def canonical_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=True)

    @property
    def config_hash(self) -> str:
        return sha256_text(self.canonical_yaml())


DEFAULT_V7_CONFIG_PATH = Path("config/btc_swing_v7.yaml")


def load_v7_config(path: str | Path | None = None) -> V7Config:
    p = Path(path) if path else DEFAULT_V7_CONFIG_PATH
    with p.open() as f:
        return V7Config.model_validate(yaml.safe_load(f))
