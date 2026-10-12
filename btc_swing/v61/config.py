"""V6.1 configuration schema (strict Pydantic, YAML, hash). Every threshold used by the V6.1
research is declared here before any result is read (docs/BTC_V6_1_STRUCTURE_FLOW_DESIGN.md)."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from btc_swing.core.config import RiskCfg
from btc_swing.core.hashing import sha256_text

V61_VERSION = "6.1.0-research"
V61_FEATURE_VERSION = "v6.1-feat-1"
V61_EVENT_RULE_VERSION = "v6.1-event-1"
V61_EXIT_RULE_VERSION = "v6.1-exit-1"


class V61Family(StrEnum):
    TREND_PULLBACK_RECLAIM = "TREND_PULLBACK_RECLAIM"
    BREAKOUT_RETEST_CONTINUATION = "BREAKOUT_RETEST_CONTINUATION"
    LIQUIDITY_SWEEP_REVERSAL = "LIQUIDITY_SWEEP_REVERSAL"
    RANGE_EDGE_MEAN_REVERSION = "RANGE_EDGE_MEAN_REVERSION"
    IMPULSE_CONSOLIDATION_BREAK = "IMPULSE_CONSOLIDATION_BREAK"


ALL_FAMILIES = tuple(V61Family)
EntryMode = Literal["TAKER", "MAKER", "MAKER_FIRST"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InstrumentCfg(_Strict):
    symbol: str
    quote: str = "USDT"
    primary_venue: Literal["bybit_linear_perp"] = "bybit_linear_perp"
    robustness_venue: Literal["binance_um_perp"] = "binance_um_perp"
    aux_venue_note: str


class FeaturesCfg(_Strict):
    atr_period: int = Field(ge=2)
    ema_fast: int = Field(ge=2)
    ema_slow: int = Field(ge=2)
    swing_k: int = Field(ge=1, le=5)
    rel_vol_days: int = Field(ge=5)
    min_baseline_days: int = Field(ge=3)
    rv_rank_days: int = Field(ge=5)
    prem_z_window: int = Field(ge=100)
    prem_z_min_periods: int = Field(ge=50)
    trend_slope_bars_1h: int = Field(ge=1)
    edge_touch_atr15: float = Field(gt=0)


class TrendPullbackCfg(_Strict):
    enabled: bool
    impulse_lookback_bars: int = Field(ge=6)
    impulse_min_atr15: float = Field(gt=0)
    impulse_grid: list[float]
    retrace_min: float = Field(gt=0, lt=1)
    retrace_max: float = Field(gt=0, lt=1)
    retrace_invalid: float = Field(gt=0, le=1)
    level_proximity_atr15: float = Field(gt=0)
    pullback_range_max_atr5: float = Field(gt=0)
    pullback_max_rel_vol: float = Field(gt=0)
    min_bars_after_high: int = Field(ge=1)
    flow_imbalance_5m: float = Field(ge=0)


class BreakoutRetestCfg(_Strict):
    enabled: bool
    range_min_atr15: float = Field(gt=0)
    range_max_atr15: float = Field(gt=0)
    break_min_atr15: float = Field(ge=0)
    retest_window_bars: int = Field(ge=1)
    retest_below_atr15: float = Field(ge=0)
    retest_above_atr15: float = Field(ge=0)
    hold_below_atr15: float = Field(ge=0)
    extended_atr15: float = Field(gt=0)
    max_trigger_ext_atr15: float = Field(gt=0)
    invalidation_atr15: float = Field(gt=0)
    flow_rel_vol: float = Field(gt=0)
    flow_imbalance_5m: float = Field(ge=0)
    rel_vol_grid: list[float]


class SweepReversalCfg(_Strict):
    enabled: bool
    reference_lookback_bars: int = Field(ge=12)
    reference_exclude_bars: int = Field(ge=1)
    sweep_min_atr15: float = Field(ge=0)
    sweep_grid: list[float]
    flow_sweep_imbalance: float = Field(le=0)
    flow_trigger_imbalance: float = Field(ge=0)


class RangeEdgeCfg(_Strict):
    enabled: bool
    range_min_atr15: float = Field(gt=0)
    range_max_atr15: float = Field(gt=0)
    min_edge_touches: int = Field(ge=1)
    min_opposite_distance_atr15: float = Field(gt=0)
    edge_zone: float = Field(gt=0, lt=0.5)
    trigger_zone: float = Field(gt=0, lt=0.5)
    touch_atr15: float = Field(ge=0)
    invalidation_atr15: float = Field(gt=0)
    flow_imbalance_5m: float = Field(ge=0)
    edge_zone_grid: list[float]


class ImpulseConsolidationCfg(_Strict):
    enabled: bool
    impulse_bars: int = Field(ge=3)
    impulse_min_atr15: float = Field(gt=0)
    impulse_grid: list[float]
    min_efficiency: float = Field(gt=0, le=1)
    consolidation_min_bars: int = Field(ge=1)
    consolidation_max_bars: int = Field(ge=2)
    consolidation_max_range_atr15: float = Field(gt=0)
    consolidation_max_rel_vol: float = Field(gt=0)
    hold_share: float = Field(ge=0, lt=1)
    max_extension_atr15: float = Field(gt=0)
    invalidation_atr15: float = Field(gt=0)
    flow_imbalance_5m: float = Field(ge=0)
    flow_rel_vol: float = Field(gt=0)


class FamiliesCfg(_Strict):
    trend_pullback_reclaim: TrendPullbackCfg
    breakout_retest_continuation: BreakoutRetestCfg
    liquidity_sweep_reversal: SweepReversalCfg
    range_edge_mean_reversion: RangeEdgeCfg
    impulse_consolidation_break: ImpulseConsolidationCfg


class StopsCfg(_Strict):
    buffer_atr5: float = Field(ge=0)
    floor_atr15: float = Field(gt=0)
    max_atr15: float = Field(gt=0)
    floor_pct_grid: list[float]  # 0 = structural only (primary), else minimum stop in % of price
    bucket_edges_pct: list[float]


class EpisodeCfg(_Strict):
    max_age_bars: int = Field(ge=1)
    entry_window_bars: int = Field(ge=1)
    cooldown_after_close_bars: int = Field(ge=0)
    cooldown_after_invalidation_bars: int = Field(ge=0)
    cooldown_after_two_losses_bars: int = Field(ge=0)
    cluster_bars: int = Field(ge=1)
    family_priority: list[V61Family]
    one_position: Literal[True] = True
    pyramiding: Literal[False] = False
    averaging: Literal[False] = False


class V61RiskCfg(RiskCfg):
    risk_grid: list[float]

    @model_validator(mode="after")
    def _grid(self) -> V61RiskCfg:
        if any(r <= 0 or r > 0.02 for r in self.risk_grid):
            raise ValueError("risk_grid within (0, 2%]")
        return self


class OverlayCfg(_Strict):
    max_full_risk_losses_per_day: int = Field(ge=1)
    max_daily_loss_frac: float = Field(gt=0)
    full_risk_loss_r: float = Field(lt=0)


class ExitVariantCfg(_Strict):
    name: str
    tp1_r: float | None = None
    tp1_frac: float = Field(ge=0, le=1, default=0.0)
    tp2_r: float | None = None
    tp2_frac: float = Field(ge=0, le=1, default=0.0)
    structural_target: bool = False
    breakeven_after_r: float | None = None
    trail_after_r: float | None = None  # None = trail only after TP1 (if any)
    description: str = ""

    @model_validator(mode="after")
    def _fracs(self) -> ExitVariantCfg:
        if self.tp1_frac + self.tp2_frac > 1.0 + 1e-12:
            raise ValueError("tp fractions exceed 1")
        return self


class ExitsCfg(_Strict):
    variants: dict[str, ExitVariantCfg]
    default_variant: str
    time_stop_hours_grid: list[float]
    default_time_stop_hours: float = Field(gt=0)
    time_stop_min_mfe_r: float = Field(ge=0)
    trail_atr15_buffer: float = Field(ge=0)
    max_hold_hours: float = Field(gt=0, le=24)

    @model_validator(mode="after")
    def _default(self) -> ExitsCfg:
        if self.default_variant not in self.variants:
            raise ValueError("default_variant not in variants")
        if self.default_time_stop_hours not in self.time_stop_hours_grid:
            raise ValueError("default_time_stop_hours must be in the grid")
        return self


class CostsCfg(_Strict):
    taker_fee_bps: float = Field(ge=0)
    maker_fee_bps: float = Field(ge=0)
    entry_slippage_bps: float = Field(ge=0)
    stop_slippage_bps: float = Field(ge=0)
    spread_ticks: float = Field(ge=0)
    tick_size: float = Field(gt=0)
    apply_funding: bool
    entry_mode: EntryMode
    maker_timeout_bars: int = Field(ge=1)
    maker_first_max_drift_atr15: float = Field(gt=0)
    cost_share_stop_max: float = Field(gt=0)  # primary theta
    cost_share_grid: list[float]  # 0 = no filter


class WindowCfg(_Strict):
    start: str
    end_exclusive: str


class GateCfg(_Strict):
    min_trades_per_calendar_day: float
    preferred_trades_per_day: list[float]
    min_net_expectancy_r: float
    min_profit_factor: float
    preferred_profit_factor: float
    max_drawdown_frac: float
    min_positive_dev_folds: int
    min_positive_val_halves: int
    max_single_family_share: float
    exit_selection_min_positive_folds: int


class ResearchCfg(_Strict):
    development: WindowCfg
    validation: WindowCfg
    locked_holdout: WindowCfg
    warmup_days: int = Field(ge=30)
    dev_folds: int = Field(ge=2)
    val_folds: int = Field(ge=2)
    null_k: int = Field(ge=1)
    bootstrap_resamples: int = Field(ge=100)
    seed: int
    gate: GateCfg


class V61Config(_Strict):
    strategy_name: str
    instrument: InstrumentCfg
    features: FeaturesCfg
    families: FamiliesCfg
    stops: StopsCfg
    episode: EpisodeCfg
    risk: V61RiskCfg
    overlay: OverlayCfg
    exits: ExitsCfg
    costs: CostsCfg
    research: ResearchCfg

    def canonical_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=True)

    @property
    def config_hash(self) -> str:
        return sha256_text(self.canonical_yaml())

    def enabled_families(self) -> list[V61Family]:
        f = self.families
        on = {
            V61Family.TREND_PULLBACK_RECLAIM: f.trend_pullback_reclaim.enabled,
            V61Family.BREAKOUT_RETEST_CONTINUATION: f.breakout_retest_continuation.enabled,
            V61Family.LIQUIDITY_SWEEP_REVERSAL: f.liquidity_sweep_reversal.enabled,
            V61Family.RANGE_EDGE_MEAN_REVERSION: f.range_edge_mean_reversion.enabled,
            V61Family.IMPULSE_CONSOLIDATION_BREAK: f.impulse_consolidation_break.enabled,
        }
        return [fam for fam in self.episode.family_priority if on[fam]]


DEFAULT_V61_CONFIG_PATH = Path("config/btc_swing_v6_1.yaml")


def load_v61_config(
    path: str | Path | None = None, overrides: dict[str, Any] | None = None
) -> V61Config:
    p = Path(path) if path else DEFAULT_V61_CONFIG_PATH
    with p.open() as f:
        raw = yaml.safe_load(f)
    if overrides:
        raw = _merge(raw, overrides)
    return V61Config.model_validate(raw)


def _merge(base: dict[str, Any], upd: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in upd.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
