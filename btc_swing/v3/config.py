"""V3 configuration schema (strict Pydantic, YAML, hash). Reuses the generic V1 schema pieces
(instrument, data, indicators, episode lifecycle, risk, costs) and defines the V3-specific parts.
Every value is pre-registered in `docs/BTC_SWING_V3_DESIGN.md`; nothing is searched."""

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

V3_VERSION = "3.0.0-research"
V3_SETUP_RULE_VERSION = "v3-setup-1"
V3_REGIME_RULE_VERSION = "v3-regime-1"
V3_EXIT_RULE_VERSION = "v3-exit-1"


class V3Family(StrEnum):
    TREND_PULLBACK_CONTINUATION = "TREND_PULLBACK_CONTINUATION"
    BREAKOUT_RETEST = "BREAKOUT_RETEST"
    LIQUIDITY_SWEEP_REVERSAL = "LIQUIDITY_SWEEP_REVERSAL"
    VOLATILITY_EXPANSION_CONTINUATION = "VOLATILITY_EXPANSION_CONTINUATION"


class V3Regime(StrEnum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
    VOLATILITY_COMPRESSION = "VOLATILITY_COMPRESSION"
    TRANSITION = "TRANSITION"
    UNCLEAR = "UNCLEAR"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContextCfg(_Strict):
    trend_tf: Timeframe = Timeframe.H4
    align_tf: Timeframe = Timeframe.H1
    vol_percentile_window: int = Field(ge=20, default=100)


class V3RegimeCfg(_Strict):
    vol_expansion_percentile: float = Field(gt=0, lt=1)
    vol_expansion_4h_atr_ratio: float = Field(gt=0)
    vol_compression_percentile: float = Field(gt=0, lt=1)
    d1_slope_bars: int = Field(ge=1)


class TrendPullbackV3Cfg(_Strict):
    impulse_min_atr: float = Field(gt=0)
    retrace_min: float = Field(gt=0, lt=1)
    retrace_max: float = Field(gt=0, lt=1)
    zone_pad_atr: float = Field(ge=0)
    stop_buffer_atr: float = Field(ge=0)


class BreakoutRetestCfg(_Strict):
    level_lookback_bars: int = Field(ge=4)
    break_buffer_atr: float = Field(ge=0)
    retest_window_bars: int = Field(ge=1)
    retest_touch_atr: float = Field(ge=0)
    zone_inner_atr: float = Field(ge=0)
    zone_outer_atr: float = Field(ge=0)
    stop_buffer_atr: float = Field(ge=0)
    invalidation_atr: float = Field(ge=0)


class LiquiditySweepCfg(_Strict):
    sweep_min_atr: float = Field(ge=0)
    reclaim_window_bars: int = Field(ge=1)
    zone_inner_atr: float = Field(ge=0)
    zone_outer_atr: float = Field(ge=0)
    stop_buffer_atr: float = Field(ge=0)


class VolatilityExpansionCfg(_Strict):
    compression_percentile: float = Field(gt=0, lt=1)
    compression_lookback_bars: int = Field(ge=1)
    expansion_range_atr: float = Field(gt=0)
    close_location: float = Field(gt=0, lt=1)
    breakout_lookback_bars: int = Field(ge=1)
    volume_mult: float = Field(gt=0)
    volume_lookback_bars: int = Field(ge=1)
    max_chase_atr: float = Field(gt=0)
    confirm_window_bars: int = Field(ge=1)
    confirm_max_above_atr: float = Field(ge=0)
    stop_buffer_atr: float = Field(ge=0)


class V3SetupsCfg(_Strict):
    min_rr: float = Field(gt=0)
    tp2_cap_r: float = Field(gt=0)
    min_stop_atr: float = Field(gt=0)
    max_stop_atr: float = Field(gt=0)
    trend_pullback: TrendPullbackV3Cfg
    breakout_retest: BreakoutRetestCfg
    liquidity_sweep: LiquiditySweepCfg
    volatility_expansion: VolatilityExpansionCfg


class V3EpisodeCfg(EpisodeCfg):
    family_priority: list[V3Family]


class V3RiskCfg(RiskCfg):
    report_risk_per_trade: float = Field(gt=0, le=0.02)


class V3ExitsCfg(_Strict):
    tp1_r: float = Field(gt=0)
    tp1_frac: float = Field(gt=0, lt=1)
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


class V3CriteriaCfg(_Strict):
    min_expectancy_r: float
    min_profit_factor: float
    min_positive_years: int
    min_positive_quarter_share: float
    min_quarter_trades: int
    max_cost_drag_share: float
    max_drawdown: float
    min_trades_per_day: float
    max_trades_per_day: float
    median_hold_hours: list[float]
    max_family_pnl_share: float
    min_expectancy_without_best5_r: float
    max_quarter_pnl_share: float
    b_min_profit_factor: float
    b_min_positive_years: int
    b_min_trades_per_day: float


class V3ResearchCfg(_Strict):
    start: str
    end_exclusive: str
    null_k: int = Field(ge=1)
    seed: int
    leverage_caps: list[float]
    min_cell_n: int = Field(ge=1)
    criteria: V3CriteriaCfg


class V3Config(_Strict):
    strategy_name: str
    instrument: InstrumentCfg
    data: DataCfg
    indicators: IndicatorCfg
    context: ContextCfg
    regime: V3RegimeCfg
    setups: V3SetupsCfg
    episode: V3EpisodeCfg
    risk: V3RiskCfg
    exits: V3ExitsCfg
    costs: CostsCfg
    safety_overlay: SafetyOverlayCfg
    research: V3ResearchCfg

    def canonical_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=True)

    @property
    def config_hash(self) -> str:
        return sha256_text(self.canonical_yaml())


DEFAULT_V3_CONFIG_PATH = Path("config/btc_swing_v3.yaml")


def load_v3_config(
    path: str | Path | None = None, overrides: dict[str, Any] | None = None
) -> V3Config:
    p = Path(path) if path else DEFAULT_V3_CONFIG_PATH
    with p.open() as f:
        raw = yaml.safe_load(f)
    if overrides:
        raw = _merge(raw, overrides)
    return V3Config.model_validate(raw)


def _merge(base: dict[str, Any], upd: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in upd.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
