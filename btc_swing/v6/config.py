"""V6 configuration schema (strict Pydantic, YAML, hash). Every threshold the V6 research uses is
declared here BEFORE any result is read; the 3-point grids are reported for stability only, the
pre-registered default is the value used for classification."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from btc_swing.core.config import RiskCfg
from btc_swing.core.hashing import sha256_text

V6_VERSION = "6.0.0-research"
V6_FEATURE_VERSION = "v6-feat-1"
V6_EVENT_RULE_VERSION = "v6-event-1"
V6_EXIT_RULE_VERSION = "v6-exit-1"
V6_LINK_PREFIX = "V6D-"  # reserved; no executor exists in this track


class V6Family(StrEnum):
    MOMENTUM_CONTINUATION = "MOMENTUM_CONTINUATION"
    PULLBACK_CONTINUATION = "PULLBACK_CONTINUATION"
    FAILED_BREAKOUT_REVERSAL = "FAILED_BREAKOUT_REVERSAL"
    FLOW_OI_IMPULSE = "FLOW_OI_IMPULSE"
    LIQUIDATION_FLOW_REVERSAL = "LIQUIDATION_FLOW_REVERSAL"  # forward-only (no history)
    LIQUIDATION_CONTINUATION = "LIQUIDATION_CONTINUATION"  # forward-only (no history)


HISTORICAL_FAMILIES = (
    V6Family.MOMENTUM_CONTINUATION,
    V6Family.PULLBACK_CONTINUATION,
    V6Family.FAILED_BREAKOUT_REVERSAL,
    V6Family.FLOW_OI_IMPULSE,
)
FORWARD_ONLY_FAMILIES = (V6Family.LIQUIDATION_FLOW_REVERSAL, V6Family.LIQUIDATION_CONTINUATION)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InstrumentCfg(_Strict):
    symbol: str
    quote: str = "USDT"
    research_venue: str = "binance_um_perp"
    forward_venue: str = "bybit_linear_perp"


class FeaturesCfg(_Strict):
    atr_period: int = Field(ge=2)
    ema_fast: int = Field(ge=2)
    ema_slow: int = Field(ge=2)
    swing_k: int = Field(ge=1, le=5)
    rel_vol_days: int = Field(ge=5)
    min_baseline_days: int = Field(ge=3)
    rv_rank_days: int = Field(ge=5)
    big_trade_qty_btc: float = Field(gt=0)
    prem_z_window: int = Field(ge=100)
    prem_z_min_periods: int = Field(ge=50)


class MomentumCfg(_Strict):
    enabled: bool
    break_window_bars: int = Field(ge=12)
    rel_vol_15m_min: float = Field(gt=0)
    rel_vol_grid: list[float]
    max_extension_atr5: float = Field(gt=0)
    extension_grid: list[float]
    zone_atr5: float = Field(gt=0)
    invalidation_atr5: float = Field(gt=0)


class PullbackCfg(_Strict):
    enabled: bool
    impulse_bars: int = Field(ge=3)
    impulse_min_atr1h: float = Field(gt=0)
    impulse_grid: list[float]
    retrace_min: float = Field(gt=0, lt=1)
    retrace_max: float = Field(gt=0, lt=1)
    retrace_invalid: float = Field(gt=0, le=1)
    pullback_max_rel_vol: float = Field(gt=0)

    @model_validator(mode="after")
    def _order(self) -> PullbackCfg:
        if not self.retrace_min < self.retrace_max < self.retrace_invalid:
            raise ValueError("retrace_min < retrace_max < retrace_invalid required")
        return self


class FailedBreakoutCfg(_Strict):
    enabled: bool
    break_window_bars: int = Field(ge=12)
    rel_vol_5m_min: float = Field(gt=0)
    rel_vol_grid: list[float]
    imbalance_5m_min: float = Field(ge=0, lt=1)
    reentry_bars: int = Field(ge=1, le=6)


class FlowOiCfg(_Strict):
    enabled: bool
    ret_15m_min_atr15: float = Field(gt=0)
    ret_grid: list[float]
    rel_vol_15m_min: float = Field(gt=0)
    imbalance_15m_min: float = Field(ge=0, lt=1)
    oi_rel_min: float = Field(gt=0)
    max_crowding_prem_z: float = Field(gt=0)
    pause_max_bars: int = Field(ge=1)
    pause_wait_bars: int = Field(ge=1)


class ForwardOnlyCfg(_Strict):
    enabled: bool = False
    forward_only: bool = True
    note: str = ""

    @model_validator(mode="after")
    def _never_historical(self) -> ForwardOnlyCfg:
        if self.enabled:
            raise ValueError("forward-only families cannot be enabled for historical research")
        return self


class FamiliesCfg(_Strict):
    momentum_continuation: MomentumCfg
    pullback_continuation: PullbackCfg
    failed_breakout_reversal: FailedBreakoutCfg
    flow_oi_impulse: FlowOiCfg
    liquidation_flow_reversal: ForwardOnlyCfg
    liquidation_continuation: ForwardOnlyCfg


class StopsCfg(_Strict):
    buffer_atr5: float = Field(ge=0)
    floor_atr15: float = Field(gt=0)
    max_atr15: float = Field(gt=0)


class EpisodeCfg(_Strict):
    max_age_bars: int = Field(ge=1)
    entry_window_bars: int = Field(ge=1)
    cooldown_after_close_bars: int = Field(ge=0)
    cooldown_after_invalidation_bars: int = Field(ge=0)
    cooldown_after_two_losses_bars: int = Field(ge=0)
    cluster_bars: int = Field(ge=1)
    family_priority: list[V6Family]
    one_position: bool = True
    pyramiding: bool = False
    averaging: bool = False

    @model_validator(mode="after")
    def _rules(self) -> EpisodeCfg:
        if self.pyramiding or self.averaging:
            raise ValueError("pyramiding/averaging are never allowed")
        return self


class V6RiskCfg(RiskCfg):
    risk_grid: list[float]

    @model_validator(mode="after")
    def _grid(self) -> V6RiskCfg:
        if any(r <= 0 or r > 0.02 for r in self.risk_grid):
            raise ValueError("risk_grid within (0, 2%]")
        return self


class OverlayCfg(_Strict):
    max_full_risk_losses_per_day: int = Field(ge=1)
    losses_grid: list[int]
    max_daily_loss_frac: float = Field(gt=0)
    daily_loss_grid: list[float]
    full_risk_loss_r: float = Field(lt=0)


class ExitVariantCfg(_Strict):
    name: str
    tp1_r: float | None = None
    tp1_frac: float = Field(ge=0, le=1, default=0.0)
    tp2_r: float | None = None
    tp2_frac: float = Field(ge=0, le=1, default=0.0)
    trail_from_entry: bool = False
    description: str = ""

    @model_validator(mode="after")
    def _fracs(self) -> ExitVariantCfg:
        if self.tp1_frac + self.tp2_frac > 1.0 + 1e-12:
            raise ValueError("tp fractions exceed 1")
        if self.tp2_r is not None and self.tp1_r is not None and self.tp2_r <= self.tp1_r:
            raise ValueError("tp2_r must exceed tp1_r")
        return self


class ExitsCfg(_Strict):
    variants: dict[str, ExitVariantCfg]
    default_variant: str
    breakeven_after_tp1: bool
    trail_atr15_buffer: float = Field(ge=0)
    max_hold_hours: float = Field(gt=0, le=24)
    evaluate_r_levels: list[float]

    @model_validator(mode="after")
    def _default(self) -> ExitsCfg:
        if self.default_variant not in self.variants:
            raise ValueError("default_variant not in variants")
        return self


class CostsCfg(_Strict):
    taker_fee_bps: float = Field(ge=0)
    maker_fee_bps: float = Field(ge=0)
    entry_slippage_bps: float = Field(ge=0)
    stop_slippage_bps: float = Field(ge=0)
    spread_ticks: float = Field(ge=0)
    tick_size: float = Field(gt=0)
    apply_funding: bool
    max_cost_share_of_stop: float = Field(gt=0, lt=1)


class WindowCfg(_Strict):
    start: str
    end_exclusive: str


class ActiveDayCfg(_Strict):
    range_median_days: int = Field(ge=10)
    description: str


class FamilyCriteriaCfg(_Strict):
    min_net_expectancy_r: float
    min_bootstrap_lower_r: float
    min_profit_factor: float
    min_trades_per_day: float
    max_cost_share_of_gross: float
    min_positive_folds: int
    folds: int = Field(ge=2)


class CombinedCriteriaCfg(_Strict):
    min_trades_per_calendar_day: float
    target_trades_per_day: list[float]
    min_net_expectancy_r: float
    min_profit_factor_hard: float
    min_profit_factor_robust: float
    max_drawdown_frac: float
    max_year_profit_share: float
    max_regime_profit_share: float
    median_hold_minutes: list[float]


class ResearchCfg(_Strict):
    development: WindowCfg
    validation: WindowCfg
    oos_once_only: WindowCfg
    warmup_days: int = Field(ge=30)
    stability_folds: int = Field(ge=2)
    null_k: int = Field(ge=1)
    bootstrap_resamples: int = Field(ge=100)
    seed: int
    active_day: ActiveDayCfg
    family_criteria: FamilyCriteriaCfg
    combined_criteria: CombinedCriteriaCfg


class V6Config(_Strict):
    strategy_name: str
    instrument: InstrumentCfg
    features: FeaturesCfg
    families: FamiliesCfg
    stops: StopsCfg
    episode: EpisodeCfg
    risk: V6RiskCfg
    overlay: OverlayCfg
    exits: ExitsCfg
    costs: CostsCfg
    research: ResearchCfg

    def canonical_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=True)

    @property
    def config_hash(self) -> str:
        return sha256_text(self.canonical_yaml())

    def enabled_families(self) -> list[V6Family]:
        f = self.families
        out: list[V6Family] = []
        if f.momentum_continuation.enabled:
            out.append(V6Family.MOMENTUM_CONTINUATION)
        if f.pullback_continuation.enabled:
            out.append(V6Family.PULLBACK_CONTINUATION)
        if f.failed_breakout_reversal.enabled:
            out.append(V6Family.FAILED_BREAKOUT_REVERSAL)
        if f.flow_oi_impulse.enabled:
            out.append(V6Family.FLOW_OI_IMPULSE)
        return [fam for fam in self.episode.family_priority if fam in out]


DEFAULT_V6_CONFIG_PATH = Path("config/btc_swing_v6.yaml")


def load_v6_config(
    path: str | Path | None = None, overrides: dict[str, Any] | None = None
) -> V6Config:
    p = Path(path) if path else DEFAULT_V6_CONFIG_PATH
    with p.open() as f:
        raw = yaml.safe_load(f)
    if overrides:
        raw = _merge(raw, overrides)
    return V6Config.model_validate(raw)


def _merge(base: dict[str, Any], upd: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in upd.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
