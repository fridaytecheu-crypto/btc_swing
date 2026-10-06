"""V5 configuration schema (strict Pydantic, YAML, hash). Generic V1 schema pieces reused."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from btc_swing.core.config import (
    CostsCfg,
    DataCfg,
    EpisodeCfg,
    IndicatorCfg,
    InstrumentCfg,
    RiskCfg,
)
from btc_swing.core.enums import Timeframe
from btc_swing.core.hashing import sha256_text

V5_VERSION = "5.0.0-research"
V5_FEATURE_VERSION = "v5-feat-1"
V5_EVENT_RULE_VERSION = "v5-event-1"
V5_EXIT_RULE_VERSION = "v5-exit-1"
COLLECTOR_SCHEMA_VERSION = "bybit-public-raw-1"


class V5Family(StrEnum):
    LIQUIDATION_CONTINUATION = "LIQUIDATION_CONTINUATION"
    ABSORPTION_REVERSAL = "ABSORPTION_REVERSAL"
    FLOW_OI_CONTINUATION = "FLOW_OI_CONTINUATION"
    FLOW_DIVERGENCE_REVERSAL = "FLOW_DIVERGENCE_REVERSAL"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FeaturesCfg(_Strict):
    z_window_bars: int = Field(ge=100)
    z_min_periods: int = Field(ge=50)
    funding_z_window: int = Field(ge=10)
    big_trade_qty_btc: float = Field(gt=0)
    range_window_bars: int = Field(ge=12)
    break_window_bars: int = Field(ge=12)


class LiqContCfg(_Strict):
    impulse_ret_1h_z: float
    oi_chg_1h_z: float
    vol_1h_z: float
    imbalance_1h_z: float


class AbsorptionCfg(_Strict):
    imbalance_1h_z: float
    vol_1h_z: float
    max_adverse_ret_1h_z: float


class FlowOiCfg(_Strict):
    ret_1h_z: float
    imbalance_1h_z: float
    cvd_slope_1h_z: float
    oi_chg_1h_z: float
    max_crowding_z: float


class DivergenceCfg(_Strict):
    max_cvd_slope_z: float
    max_oi_chg_z: float


class EventsCfg(_Strict):
    liquidation_continuation: LiqContCfg
    absorption_reversal: AbsorptionCfg
    flow_oi_continuation: FlowOiCfg
    flow_divergence_reversal: DivergenceCfg
    cluster_bars: int = Field(ge=1)


class GateCfg(_Strict):
    min_events: int
    min_t: float
    horizons_hours: list[float]
    min_positive_years: int


class StageACfg(_Strict):
    horizons_hours: list[float]
    mfe_mae_hours: int = Field(ge=1)
    bootstrap_resamples: int = Field(ge=100)
    gate: GateCfg


class ExecutionCfg(_Strict):
    confirm_window_bars: int = Field(ge=1)
    zone_below_atr: float = Field(ge=0)
    zone_above_atr: float = Field(ge=0)
    struct_lookback_bars: int = Field(ge=1)
    struct_buffer_atr: float = Field(ge=0)
    vol_floor_atr: float = Field(gt=0)
    max_stop_atr: float = Field(gt=0)


class V5EpisodeCfg(EpisodeCfg):
    family_priority: list[V5Family]


class V5RiskCfg(RiskCfg):
    report_risk_per_trade: float = Field(gt=0, le=0.02)


class V5ExitsCfg(_Strict):
    tp1_r: float = Field(gt=0)
    tp1_frac: float = Field(gt=0, lt=1)
    tp2_r: float = Field(gt=0)
    tp2_frac: float = Field(gt=0, lt=1)
    breakeven_after_tp1: bool
    trail_tf: Timeframe
    trail_atr_buffer: float = Field(ge=0)
    max_hold_hours: int = Field(ge=1)
    evaluate_r_levels: list[float]


class SafetyOverlayCfg(_Strict):
    max_full_risk_losses_per_day: int = Field(ge=1)
    full_risk_loss_r: float = Field(lt=0)
    max_daily_loss_frac: float = Field(gt=0)


class CollectorCfg(_Strict):
    url: str
    symbol: str
    topics: list[str]
    stale_seconds: float = Field(gt=0)
    ping_seconds: float = Field(gt=0)
    reconnect_backoff_seconds: list[float]
    dedupe_cache_size: int = Field(ge=100)


class V5CriteriaCfg(_Strict):
    min_gross_expectancy_r: float
    min_net_expectancy_r: float
    min_profit_factor: float
    min_positive_years: int
    min_positive_quarter_share: float
    min_quarter_trades: int
    max_cost_drag_share: float
    max_drawdown: float
    min_trades_per_day: float
    max_trades_per_day: float
    median_hold_hours: list[float]
    min_expectancy_without_best5_r: float
    max_family_pnl_share: float
    b_min_gross_expectancy_r: float


class V5ResearchCfg(_Strict):
    start: str
    end_exclusive: str
    null_k: int = Field(ge=1)
    seed: int
    leverage_caps: list[float]
    min_cell_n: int = Field(ge=1)
    criteria: V5CriteriaCfg


class V5Config(_Strict):
    strategy_name: str
    instrument: InstrumentCfg
    data: DataCfg
    indicators: IndicatorCfg
    features: FeaturesCfg
    events: EventsCfg
    stage_a: StageACfg
    execution: ExecutionCfg
    episode: V5EpisodeCfg
    risk: V5RiskCfg
    exits: V5ExitsCfg
    costs: CostsCfg
    cost_sensitivity: CostsCfg
    safety_overlay: SafetyOverlayCfg
    collector: CollectorCfg
    research: V5ResearchCfg

    def canonical_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=True)

    @property
    def config_hash(self) -> str:
        return sha256_text(self.canonical_yaml())


DEFAULT_V5_CONFIG_PATH = Path("config/btc_swing_v5.yaml")


def load_v5_config(
    path: str | Path | None = None, overrides: dict[str, Any] | None = None
) -> V5Config:
    p = Path(path) if path else DEFAULT_V5_CONFIG_PATH
    with p.open() as f:
        raw = yaml.safe_load(f)
    if overrides:
        raw = _merge(raw, overrides)
    return V5Config.model_validate(raw)


def _merge(base: dict[str, Any], upd: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in upd.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
