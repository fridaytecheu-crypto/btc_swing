"""V10 configuration (strict Pydantic, YAML, hash) and version constants."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from btc_swing.core.config import RiskCfg
from btc_swing.core.hashing import sha256_text
from btc_swing.v5.config import CollectorCfg

V10_VERSION = "10.0.0-live-sniper"
V10_FEATURE_VERSION = "v10-feat-1"
V10_FAMILY_VERSION = "v10-fam-1"
V10_RISK_VERSION = "v10-risk-1"
STRATEGY = "BTC_V10_LIVE_MICROSTRUCTURE_SNIPER"
PREFIX_ORDER, PREFIX_SIGNAL, PREFIX_EPISODE = "V10D-", "V10S-", "V10E-"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FeaturesCfg(_Strict):
    tick_seconds: float = Field(gt=0)
    flow_windows_s: list[int]
    liq_windows_s: list[int]
    history_sample_s: int = Field(ge=1)
    history_hours: float = Field(gt=0)
    history_min_samples: int = Field(ge=10)
    atr_period: int = Field(ge=2)
    swing_k: int = Field(ge=1)
    structure_range_min: int = Field(ge=2)
    local_extreme_min: int = Field(ge=5)
    book_depth_bps: float = Field(gt=0)
    book_replenish_lookback_s: int = Field(ge=1)
    book_max_age_s: float = Field(gt=0)
    trade_feed_max_age_s: float = Field(gt=0)


class FamilyACfg(_Strict):
    liq_burst_pct: float = Field(gt=0, lt=1)
    liq_imbalance: float = Field(ge=0, le=1)
    flow_imbalance: float = Field(gt=0, lt=1)
    absorption_flow_pct: float = Field(gt=0, lt=1)
    absorption_impact_pct: float = Field(gt=0, lt=1)
    reversal_imbalance_10: float = Field(ge=0, lt=1)
    turn_atr1m: float = Field(ge=0)
    invalidate_atr5m: float = Field(gt=0)
    expire_s: int = Field(ge=10)


class FamilyBCfg(_Strict):
    flow_pct: float = Field(gt=0, lt=1)
    flow_imbalance: float = Field(gt=0, lt=1)
    location_atr5m: float = Field(gt=0)
    absorption_flow_pct: float = Field(gt=0, lt=1)
    absorption_impact_pct: float = Field(gt=0, lt=1)
    bid_replenish_min: float = Field(gt=0)
    reversal_imbalance_10: float = Field(ge=0, lt=1)
    turn_atr1m: float = Field(ge=0)
    invalidate_atr5m: float = Field(gt=0)
    expire_s: int = Field(ge=10)


class FamilyCCfg(_Strict):
    flow_pct: float = Field(gt=0, lt=1)
    flow_imbalance: float = Field(gt=0, lt=1)
    efficient_impact_pct: float = Field(gt=0, lt=1)
    intensity_pct_10: float = Field(gt=0, lt=1)
    ask_deplete_max: float = Field(gt=0)
    chase_max_atr5m: float = Field(gt=0)
    arm_travel_max_atr5m: float = Field(gt=0)
    expire_s: int = Field(ge=10)


class FamiliesCfg(_Strict):
    a: FamilyACfg
    b: FamilyBCfg
    c: FamilyCCfg


class StopCfg(_Strict):
    buffer_atr5m: float = Field(ge=0)
    floor_pct: float = Field(gt=0)
    cap_pct: float = Field(gt=0)


class ExitsCfg(_Strict):
    tp1_r: float = Field(gt=0)
    tp1_frac: float = Field(gt=0, lt=1)
    tp2_r: float = Field(gt=0)
    time_stop_min: int = Field(ge=10)
    progress_min_r: float = Field(ge=0)
    hard_max_min: int = Field(ge=10)


class CostsCfg(_Strict):
    taker_fee_bps: float = Field(ge=0)
    half_spread_fallback_bps: float = Field(ge=0)
    entry_slippage_bps: float = Field(ge=0)
    exit_slippage_bps: float = Field(ge=0)
    stop_slippage_bps: float = Field(ge=0)


class SafetyCfg(_Strict):
    max_trades_rolling_24h: int = Field(ge=1)  # shadow executions
    demo_max_trades_rolling_24h: int = Field(ge=1, le=20)  # demo executions (separate counter)
    daily_stop_frac: float = Field(gt=0, lt=0.05)
    max_consecutive_losses: int = Field(ge=1)
    decision_max_delay_s: float = Field(gt=0)


class OutcomesCfg(_Strict):
    horizons_min: list[int]
    nearmiss_dedupe_s: int = Field(ge=1)


class ScheduleCfg(_Strict):
    outcomes_every_s: int = Field(ge=10)
    daily_report_utc_minute: int = Field(ge=0, le=59)


class V10Config(_Strict):
    strategy_name: str
    symbol: str
    collector: CollectorCfg
    features: FeaturesCfg
    families: FamiliesCfg
    stop: StopCfg
    exits: ExitsCfg
    costs: CostsCfg
    risk: RiskCfg
    safety: SafetyCfg
    outcomes: OutcomesCfg
    schedule: ScheduleCfg

    def canonical_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=True)

    @property
    def config_hash(self) -> str:
        return sha256_text(self.canonical_yaml())

    @property
    def strategy_hash(self) -> str:
        """Hash of the STRATEGY sections only (what the freeze pins); operational sections
        (collector, safety caps, outcomes, schedule) may change with a journaled note."""
        return strategy_hash_of(self.model_dump(mode="json"))


STRATEGY_SECTIONS = ("symbol", "features", "families", "stop", "exits", "costs", "risk")


def strategy_hash_of(raw: dict[str, Any]) -> str:
    return sha256_text(yaml.safe_dump({k: raw.get(k) for k in STRATEGY_SECTIONS}, sort_keys=True))


DEFAULT_V10_CONFIG_PATH = Path("config/btc_swing_v10.yaml")


def load_v10_config(path: str | Path | None = None) -> V10Config:
    p = Path(path) if path else DEFAULT_V10_CONFIG_PATH
    with p.open() as f:
        cfg = V10Config.model_validate(yaml.safe_load(f))
    if cfg.strategy_name != STRATEGY:
        raise ValueError(f"strategy_name must be {STRATEGY}")
    if abs(cfg.risk.risk_per_trade - 0.001) > 1e-12:
        raise ValueError("V10 risk_per_trade is frozen at 0.10% (owner specification)")
    return cfg
