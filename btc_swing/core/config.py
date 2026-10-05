"""Strategy configuration for the BTC swing engine: strict Pydantic schema, YAML, hash.

Every threshold the engine uses lives here. Values in `config/btc_swing.default.yaml` are
pre-registered research defaults; they are NOT tuned and must not be searched for the best value.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from btc_swing.core.enums import Regime, SetupFamily, Timeframe
from btc_swing.core.hashing import sha256_text

HARD_MAX_LEVERAGE = 10.0


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InstrumentCfg(_Strict):
    symbol: str
    market: Literal["binance_um_perp"]
    spot_symbol: str | None = None
    quote: str = "USDT"


class DataCfg(_Strict):
    provider: Literal["binance_vision", "synthetic"]
    base_timeframe: Literal[Timeframe.M5] = Timeframe.M5
    latency_minutes: int = Field(ge=0, le=60)
    ingest_native_timeframes: list[Timeframe]
    ingest_funding: bool
    ingest_metrics: bool
    ingest_premium_index: bool
    ingest_mark_price: bool = False
    ingest_spot: bool


class IndicatorCfg(_Strict):
    ema_fast: int = Field(gt=1)
    ema_slow: int = Field(gt=1)
    ema_trend: int = Field(gt=1)
    atr_period: int = Field(gt=1)
    swing_k: int = Field(ge=1, le=10)
    donchian_period: int = Field(gt=1)
    min_bars: dict[Timeframe, int]

    @model_validator(mode="after")
    def _order(self) -> IndicatorCfg:
        if not self.ema_fast < self.ema_slow < self.ema_trend:
            raise ValueError("ema_fast < ema_slow < ema_trend required")
        return self


class RegimeCfg(_Strict):
    high_vol_atr_pct: float = Field(gt=0)
    low_vol_atr_pct: float = Field(gt=0)
    breakout_lookback_bars: int = Field(ge=1)
    breakout_atr_expansion: float = Field(gt=0)
    range_ema_band_atr: float = Field(gt=0)
    trend_slope_bars: int = Field(ge=1)

    @model_validator(mode="after")
    def _order(self) -> RegimeCfg:
        if self.low_vol_atr_pct >= self.high_vol_atr_pct:
            raise ValueError("low_vol_atr_pct must be < high_vol_atr_pct")
        return self


class TrendPullbackCfg(_Strict):
    trend_tf: Timeframe
    setup_tf: Timeframe
    confirm_tf: Timeframe
    entry_tf: Timeframe
    min_pullback_atr: float = Field(ge=0)
    zone_upper_ema: Literal["ema_fast"] = "ema_fast"
    zone_lower_ema: Literal["ema_slow"] = "ema_slow"
    zone_pad_atr: float = Field(ge=0)
    stop_atr_buffer: float = Field(ge=0)
    min_stop_atr: float = Field(gt=0)
    max_stop_atr: float = Field(gt=0)
    structural_target_lookback: int = Field(ge=1)


class BreakoutCfg(_Strict):
    level_tf: Timeframe
    setup_tf: Timeframe
    confirm_tf: Timeframe
    entry_tf: Timeframe
    min_expansion: float = Field(gt=0)
    max_extension_atr: float = Field(gt=0)
    zone_inner_atr: float = Field(ge=0)
    zone_outer_atr: float = Field(ge=0)
    stop_atr_buffer: float = Field(ge=0)
    min_stop_atr: float = Field(gt=0)
    max_stop_atr: float = Field(gt=0)


class SupportReclaimCfg(_Strict):
    level_tf: Timeframe
    setup_tf: Timeframe
    confirm_tf: Timeframe
    entry_tf: Timeframe
    level_max_age_bars: int = Field(ge=1)
    sweep_min_atr: float = Field(ge=0)
    sweep_window_bars: int = Field(ge=1)
    zone_inner_atr: float = Field(ge=0)
    zone_outer_atr: float = Field(ge=0)
    stop_atr_buffer: float = Field(ge=0)
    min_stop_atr: float = Field(gt=0)
    max_stop_atr: float = Field(gt=0)


class MomentumContinuationCfg(_Strict):
    impulse_tf: Timeframe
    setup_tf: Timeframe
    confirm_tf: Timeframe
    entry_tf: Timeframe
    impulse_min_atr: float = Field(gt=0)
    impulse_close_location: float = Field(gt=0, lt=1)
    flag_min_atr: float = Field(ge=0)
    flag_max_atr: float = Field(gt=0)
    flag_max_age_bars: int = Field(ge=1)
    stop_atr_buffer: float = Field(ge=0)
    min_stop_atr: float = Field(gt=0)
    max_stop_atr: float = Field(gt=0)

    @model_validator(mode="after")
    def _order(self) -> MomentumContinuationCfg:
        if self.flag_min_atr >= self.flag_max_atr:
            raise ValueError("flag_min_atr must be < flag_max_atr")
        return self


class SetupsCfg(_Strict):
    enabled: list[SetupFamily]
    eligible_regimes: dict[SetupFamily, list[Regime]]
    trend_pullback: TrendPullbackCfg
    breakout: BreakoutCfg
    support_reclaim: SupportReclaimCfg
    momentum_continuation: MomentumContinuationCfg

    @model_validator(mode="after")
    def _check(self) -> SetupsCfg:
        for fam in self.enabled:
            if fam not in self.eligible_regimes:
                raise ValueError(f"eligible_regimes missing for {fam}")
        return self


class EpisodeCfg(_Strict):
    watch_timeout_bars: int = Field(ge=1)
    entry_ready_timeout_bars: int = Field(ge=1)
    cooldown_bars_after_close: int = Field(ge=0)
    cooldown_bars_after_invalidation: int = Field(ge=0)
    max_concurrent_positions: Literal[1] = 1
    dedupe_same_anchor: bool = True
    anchor_tolerance_atr: float = Field(ge=0)


class RiskCfg(_Strict):
    initial_equity: float = Field(gt=0)
    risk_per_trade: float = Field(gt=0, le=0.02)
    allowed_leverage: list[float]
    max_leverage: float = Field(gt=0, le=HARD_MAX_LEVERAGE)
    margin_cap_frac: float = Field(gt=0, le=1.0)
    maintenance_margin_rate: float = Field(gt=0, lt=0.1)
    min_stop_to_liquidation_ratio: float = Field(ge=1.0)
    min_liquidation_distance_atr: float = Field(ge=0)
    liquidation_atr_tf: Timeframe
    compounding: bool

    @model_validator(mode="after")
    def _check(self) -> RiskCfg:
        if any(lv <= 0 or lv > HARD_MAX_LEVERAGE for lv in self.allowed_leverage):
            raise ValueError(f"allowed_leverage must be within (0, {HARD_MAX_LEVERAGE}]")
        if max(self.allowed_leverage) > self.max_leverage:
            raise ValueError("allowed_leverage exceeds max_leverage")
        if sorted(self.allowed_leverage) != list(self.allowed_leverage):
            raise ValueError("allowed_leverage must be ascending")
        return self


class ExitsCfg(_Strict):
    tp1_r: float = Field(gt=0)
    tp1_frac: float = Field(ge=0, le=1)
    tp2_r: float = Field(gt=0)
    tp2_frac: float = Field(ge=0, le=1)
    breakeven_after_tp1: bool
    trail_method: Literal["structure_atr", "none"]
    trail_tf: Timeframe
    trail_atr_buffer: float = Field(ge=0)
    max_hold_hours: int = Field(ge=1)
    regime_exit: bool
    evaluate_r_levels: list[float]

    @model_validator(mode="after")
    def _check(self) -> ExitsCfg:
        if self.tp1_frac + self.tp2_frac > 1.0 + 1e-12:
            raise ValueError("tp1_frac + tp2_frac must be <= 1")
        if self.tp2_r <= self.tp1_r:
            raise ValueError("tp2_r must be > tp1_r")
        return self


class CostsCfg(_Strict):
    taker_fee_bps: float = Field(ge=0)
    maker_fee_bps: float = Field(ge=0)
    use_maker_for_targets: bool
    entry_slippage_bps: float = Field(ge=0)
    stop_slippage_bps: float = Field(ge=0)
    apply_funding: bool
    default_funding_interval_hours: int = Field(ge=1)


class BacktestCfg(_Strict):
    fill_rule: Literal["next_bar_open"]
    same_bar_stop_and_target: Literal["stop_first"]
    warmup_days: int = Field(ge=1)
    information_mode: Literal["MARKET_AS_OF", "DEPLOYMENT_AS_OF"]


class ResearchCfg(_Strict):
    min_cell_n: int = Field(ge=1)
    sharpe_periods_per_year: int = Field(ge=1)


class ExperimentCfg(_Strict):
    """Pre-registered structural experiments. Exactly one switch per phase; default = frozen baseline.

    entry_mode:
      CONFIRMED_TRIGGER  Phase 2 baseline: zone reached AND confirm-TF confirmation -> ENTRY_READY,
                         then entry-TF trigger -> TRIGGERED (fill at the next 5m open).
      ZONE_ENTRY         Phase 2.1 variant: the first entry-TF bar that reaches the pre-defined zone
                         while the plan is still valid -> TRIGGERED (fill at the next 5m open).
                         No confirmation, no trigger. Everything else identical.
    """

    entry_mode: Literal["CONFIRMED_TRIGGER", "ZONE_ENTRY"] = "CONFIRMED_TRIGGER"


class BtcStrategyConfig(_Strict):
    strategy_name: str
    instrument: InstrumentCfg
    data: DataCfg
    indicators: IndicatorCfg
    regime: RegimeCfg
    setups: SetupsCfg
    episode: EpisodeCfg
    risk: RiskCfg
    exits: ExitsCfg
    costs: CostsCfg
    backtest: BacktestCfg
    research: ResearchCfg
    experiment: ExperimentCfg = ExperimentCfg()

    def canonical_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=True)

    @property
    def config_hash(self) -> str:
        return sha256_text(self.canonical_yaml())


DEFAULT_CONFIG_PATH = Path("config/btc_swing.default.yaml")


def load_btc_config(
    path: str | Path | None = None, overrides: dict[str, Any] | None = None
) -> BtcStrategyConfig:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    with p.open() as f:
        raw = yaml.safe_load(f)
    if overrides:
        raw = _deep_merge(raw, overrides)
    return BtcStrategyConfig.model_validate(raw)


def _deep_merge(base: dict[str, Any], upd: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in upd.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
