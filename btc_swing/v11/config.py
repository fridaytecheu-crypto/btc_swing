"""V11 configuration (strict Pydantic, YAML, hashes). The freeze pins the STRATEGY sections."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from btc_swing.core.config import RiskCfg
from btc_swing.core.hashing import sha256_text

V11_VERSION = "11.0.0-fast-multifactor"
V11_FEATURE_VERSION = "v11-feat-1"
V11_SCORE_VERSION = "v11-score-1"
V11_EXIT_VERSION = "v11-exit-1"
STRATEGY = "BTC_V11_FAST_MULTIFACTOR_INTRADAY"
PREFIX_SIGNAL, PREFIX_EPISODE = "V11S-", "V11E-"
GROUPS = ("trend", "momentum", "volatility", "volume", "location", "derivatives", "microstructure")
STRATEGY_SECTIONS = (
    "symbol",
    "features",
    "weights",
    "thresholds",
    "families",
    "stop",
    "exits",
    "costs",
    "risk",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WindowCfg(_Strict):
    start: str
    end_exclusive: str


class WindowsCfg(_Strict):
    train: WindowCfg
    validation: WindowCfg
    locked_holdout: WindowCfg
    warmup_days: int = Field(ge=7)
    data_cap: str


class FeaturesCfg(_Strict):
    ema_fast: int = Field(ge=2)
    ema_mid: int = Field(ge=2)
    ema_slow: int = Field(ge=2)
    atr_period: int = Field(ge=2)
    rsi_fast: int = Field(ge=2)
    rsi_slow: int = Field(ge=2)
    adx_period: int = Field(ge=2)
    bb_period: int = Field(ge=5)
    bb_k: float = Field(gt=0)
    kc_k: float = Field(gt=0)
    squeeze_lookback_bars: int = Field(ge=1)
    atr_pct_history_days: int = Field(ge=1)
    rvol_baseline_days: int = Field(ge=5)
    rvol_min_days: int = Field(ge=3)
    vol_spike_history_days: int = Field(ge=1)
    intensity_baseline_min: int = Field(ge=5)
    swing_k: int = Field(ge=1)
    range_hi_lo_min: int = Field(ge=10)
    struct_range_min: int = Field(ge=3)
    vwap_min_minutes: int = Field(ge=1)
    binance_latency_s: int = Field(ge=0)


class WeightsCfg(_Strict):
    trend: float = Field(ge=0)
    momentum: float = Field(ge=0)
    volatility: float = Field(ge=0)
    volume: float = Field(ge=0)
    location: float = Field(ge=0)
    derivatives: float = Field(ge=0)
    microstructure: float = Field(ge=0)

    def as_dict(self) -> dict[str, float]:
        return {g: float(getattr(self, g)) for g in GROUPS}


class ThresholdsCfg(_Strict):
    candidate: float = Field(ge=0, le=100)
    armed: float = Field(ge=0, le=100)
    entry: float = Field(ge=0, le=100)
    overextension_vwap_atr: float = Field(gt=0)
    rsi_exhaust_high: float = Field(gt=50, le=100)
    rsi_exhaust_low: float = Field(ge=0, lt=50)


class FamilyACfg(_Strict):
    impulse_max_leg_bars: int = Field(ge=2)
    impulse_size_atr: float = Field(gt=0)
    pullback_min_bars: int = Field(ge=1)
    pullback_max_bars: int = Field(ge=2)
    retrace_min: float = Field(gt=0, lt=1)
    retrace_max: float = Field(gt=0, lt=1)
    pullback_volume_ratio_max: float = Field(gt=0)
    armed_valid_min: int = Field(ge=1)
    trigger_lookback_1m: int = Field(ge=1)
    stop_buffer_atr: float = Field(ge=0)


class FamilyBCfg(_Strict):
    atr_low_pct: float = Field(gt=0, lt=1)
    near_boundary_atr: float = Field(gt=0)
    rvol_min: float = Field(gt=0)
    max_extension_atr: float = Field(gt=0)
    armed_valid_min: int = Field(ge=1)
    stop_buffer_atr: float = Field(ge=0)


class FamilyCCfg(_Strict):
    sweep_depth_atr: float = Field(ge=0)
    sweep_lookback_min: int = Field(ge=1)
    rsi_exhaust: float = Field(gt=0, lt=100)
    armed_valid_min: int = Field(ge=1)
    stop_buffer_atr: float = Field(ge=0)


class FamiliesCfg(_Strict):
    a: FamilyACfg
    b: FamilyBCfg
    c: FamilyCCfg


class StopCfg(_Strict):
    floor_pct: float = Field(gt=0)
    cap_pct: float = Field(gt=0)


class ExitsCfg(_Strict):
    tp1_r: float = Field(gt=0)
    tp1_frac: float = Field(gt=0, lt=1)
    tp2_r: float = Field(gt=0)
    tp2_frac: float = Field(gt=0, lt=1)
    runner_trail_bars_5m: int = Field(ge=1)
    runner_trail_buffer_atr: float = Field(ge=0)
    breakeven_after_tp1_bps: float = Field(ge=0)
    stall_review_min: int = Field(ge=1)
    stall_min_mfe_r: float = Field(ge=0)
    stall_score_drop: float = Field(ge=0)
    hard_max_min: int = Field(ge=5)


class CostsCfg(_Strict):
    taker_fee_bps: float = Field(ge=0)
    half_spread_bps: float = Field(ge=0)
    entry_slippage_bps: float = Field(ge=0)
    exit_slippage_bps: float = Field(ge=0)
    stop_slippage_bps: float = Field(ge=0)
    apply_funding: bool
    stress_multiplier: float = Field(ge=1)


class DemoSafetyCfg(_Strict):
    max_trades_rolling_24h: int = Field(ge=1)
    daily_stop_usdt: float = Field(gt=0)
    max_consecutive_losses: int = Field(ge=1)


class OutcomesCfg(_Strict):
    horizons_min: list[int]
    candidate_dedupe_min: int = Field(ge=1)


class V11Config(_Strict):
    strategy_name: str
    symbol: str
    windows: WindowsCfg
    features: FeaturesCfg
    weights: WeightsCfg
    thresholds: ThresholdsCfg
    families: FamiliesCfg
    stop: StopCfg
    exits: ExitsCfg
    costs: CostsCfg
    risk: RiskCfg
    demo_safety: DemoSafetyCfg
    outcomes: OutcomesCfg

    def canonical_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=True)

    @property
    def config_hash(self) -> str:
        return sha256_text(self.canonical_yaml())

    @property
    def strategy_hash(self) -> str:
        return strategy_hash_of(self.model_dump(mode="json"))


def strategy_hash_of(raw: dict[str, Any]) -> str:
    return sha256_text(yaml.safe_dump({k: raw.get(k) for k in STRATEGY_SECTIONS}, sort_keys=True))


DEFAULT_V11_CONFIG_PATH = Path("config/btc_swing_v11.yaml")


def load_v11_config(path: str | Path | None = None) -> V11Config:
    p = Path(path) if path else DEFAULT_V11_CONFIG_PATH
    with p.open() as f:
        cfg = V11Config.model_validate(yaml.safe_load(f))
    if cfg.strategy_name != STRATEGY:
        raise ValueError(f"strategy_name must be {STRATEGY}")
    if cfg.risk.max_leverage > 5 or max(cfg.risk.allowed_leverage) > 5:
        raise ValueError("V11 leverage is capped at 5x (owner specification)")
    if abs(cfg.risk.risk_per_trade - 0.0015) > 1e-12:
        raise ValueError("V11 risk_per_trade is pre-registered at 0.15%")
    if abs(sum(cfg.weights.as_dict().values()) - 100.0) > 1e-9:
        raise ValueError("group weights must sum to 100")
    return cfg
