"""V4 configuration schema (strict Pydantic, YAML, hash). Generic V1 schema pieces are reused;
V4-specific parts are defined here. Every value is pre-registered in docs/BTC_SWING_V4_DESIGN.md."""

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

V4_VERSION = "4.0.0-research"
V4_FEATURE_VERSION = "v4-feat-1"
V4_EVENT_RULE_VERSION = "v4-event-1"
V4_EXIT_RULE_VERSION = "v4-exit-1"


class V4Family(StrEnum):
    DELEVERAGING_REVERSAL = "DELEVERAGING_REVERSAL"
    POSITIONING_RESET_CONTINUATION = "POSITIONING_RESET_CONTINUATION"
    PARTICIPATION_BREAKOUT = "PARTICIPATION_BREAKOUT"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FeaturesCfg(_Strict):
    z_window_bars: int = Field(ge=50)
    z_min_periods: int = Field(ge=20)
    funding_z_window: int = Field(ge=10)
    cooling_lookback_hours: int = Field(ge=1)
    level_lookback_bars: int = Field(ge=4)
    rv_window_bars: int = Field(ge=12)


class DeleveragingCfg(_Strict):
    impulse_ret_z: float
    oi_change_4h_z: float
    volume_4h_z: float
    taker_4h_z: float
    flush_lookback_bars: int = Field(ge=1)
    watch_hours: int = Field(ge=1)
    struct_buffer_atr: float = Field(ge=0)


class PositioningResetCfg(_Strict):
    pullback_ret_z: float
    oi_change_24h_z: float
    cooling_z_drop: float
    taker_4h_z: float
    pullback_low_bars: int = Field(ge=1)
    watch_hours: int = Field(ge=1)
    struct_buffer_atr: float = Field(ge=0)


class ParticipationBreakoutCfg(_Strict):
    volume_1h_z: float
    oi_change_4h_z: float
    taker_1h_z: float
    max_crowding_z: float
    accept_bars_15m: int = Field(ge=1)
    max_chase_atr: float = Field(gt=0)
    watch_hours: int = Field(ge=1)
    struct_buffer_atr: float = Field(ge=0)


class EventsCfg(_Strict):
    deleveraging: DeleveragingCfg
    positioning_reset: PositioningResetCfg
    participation_breakout: ParticipationBreakoutCfg


class GeometryCfg(_Strict):
    vol_floor_atr: float = Field(gt=0)
    max_stop_atr: float = Field(gt=0)
    zone_width_atr: float = Field(gt=0)
    zone_inner_atr: float = Field(ge=0)


class V4EpisodeCfg(EpisodeCfg):
    family_priority: list[V4Family]


class V4RiskCfg(RiskCfg):
    report_risk_per_trade: float = Field(gt=0, le=0.02)


class V4ExitsCfg(_Strict):
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


class StageACfg(_Strict):
    horizons_hours: list[float]
    mfe_mae_hours: int = Field(ge=1)


class V4CriteriaCfg(_Strict):
    stage_a_min_t: float
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


class V4ResearchCfg(_Strict):
    start: str
    end_exclusive: str
    null_k: int = Field(ge=1)
    seed: int
    leverage_caps: list[float]
    min_cell_n: int = Field(ge=1)
    criteria: V4CriteriaCfg


class V4Config(_Strict):
    strategy_name: str
    instrument: InstrumentCfg
    data: DataCfg
    indicators: IndicatorCfg
    features: FeaturesCfg
    events: EventsCfg
    geometry: GeometryCfg
    episode: V4EpisodeCfg
    risk: V4RiskCfg
    exits: V4ExitsCfg
    costs: CostsCfg
    cost_sensitivity: CostsCfg
    safety_overlay: SafetyOverlayCfg
    stage_a: StageACfg
    research: V4ResearchCfg

    def canonical_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=True)

    @property
    def config_hash(self) -> str:
        return sha256_text(self.canonical_yaml())


DEFAULT_V4_CONFIG_PATH = Path("config/btc_swing_v4.yaml")


def load_v4_config(
    path: str | Path | None = None, overrides: dict[str, Any] | None = None
) -> V4Config:
    p = Path(path) if path else DEFAULT_V4_CONFIG_PATH
    with p.open() as f:
        raw = yaml.safe_load(f)
    if overrides:
        raw = _merge(raw, overrides)
    return V4Config.model_validate(raw)


def _merge(base: dict[str, Any], upd: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in upd.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
