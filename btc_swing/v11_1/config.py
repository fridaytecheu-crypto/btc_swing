"""V11.1 configuration: the V11 sections (consumed read-only by the V11 feature builder / scorer /
engine) plus the pre-registered quality, cost-filter and success sections
(docs/BTC_V11_1_COST_AWARE_INTRADAY_DESIGN.md). Research only."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from btc_swing.v11.config import STRATEGY as V11_STRATEGY
from btc_swing.v11.config import V11Config

V111_VERSION = "11.1.0-cost-aware"
STRATEGY = "BTC_V11_1_COST_AWARE_INTRADAY"
PREFIX_SIGNAL = "V111S-"
DEFAULT_CONFIG = Path("config/btc_swing_v11_1.yaml")
STRATEGY_SECTIONS = ("symbol", "base", "quality", "cost_filter", "success")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QualityCfg(_Strict):
    min_evidence_groups: int = Field(ge=1)
    evidence_min_sub: float = Field(gt=0, le=1)
    evidence_min_margin: float = Field(ge=0, le=1)
    directional_groups: list[str]
    a_max_dist_vwap_atr: float = Field(gt=0)
    a_opposing_imb15: float = Field(lt=0)
    a_min_room_r: float = Field(ge=0)
    b_rvol_min: float = Field(gt=0)
    b_min_break_atr: float = Field(ge=0)
    b_max_extension_atr: float = Field(gt=0)
    b_min_imb5: float = Field(ge=0)
    b_min_close_location: float = Field(ge=0, le=1)
    c_min_depth_atr: float = Field(ge=0)
    c_min_reclaim_frac: float = Field(ge=0, le=1)


class CostFilterCfg(_Strict):
    expected_cost_bps: float = Field(gt=0)
    max_cost_r: float = Field(gt=0)


class SuccessCfg(_Strict):
    primary_min_pf: float = Field(gt=1)
    secondary_min_gross_r: float
    secondary_max_cost_r: float = Field(gt=0)
    secondary_min_trades_per_day: float = Field(gt=0)
    secondary_max_median_hold_min: float = Field(gt=0)
    max_single_family_share: float = Field(gt=0, le=1)


class V111Config(_Strict):
    strategy_name: str
    symbol: str
    base: V11Config
    quality: QualityCfg
    cost_filter: CostFilterCfg
    success: SuccessCfg

    def canonical_yaml(self) -> str:
        return yaml.safe_dump(json.loads(self.model_dump_json()), sort_keys=True)

    @property
    def config_hash(self) -> str:
        return hashlib.sha256(self.canonical_yaml().encode()).hexdigest()

    @property
    def strategy_hash(self) -> str:
        raw = json.loads(self.model_dump_json())
        return strategy_hash_of(raw)


def strategy_hash_of(raw: dict[str, Any]) -> str:
    sub = {k: raw[k] for k in STRATEGY_SECTIONS if k in raw}
    return hashlib.sha256(yaml.safe_dump(sub, sort_keys=True).encode()).hexdigest()


def load_v111_config(path: str | Path | None = None) -> V111Config:
    p = Path(path) if path is not None else DEFAULT_CONFIG
    raw = yaml.safe_load(p.read_text())
    cfg = V111Config.model_validate(raw)
    if cfg.strategy_name != STRATEGY:
        raise ValueError(f"V11.1 config must be named {STRATEGY}")
    b = cfg.base
    if b.strategy_name != V11_STRATEGY:
        raise ValueError("base section must carry the V11 strategy identity")
    if b.risk.max_leverage > 5 or max(b.risk.allowed_leverage) > 5:
        raise ValueError("V11.1 leverage is capped at 5x")
    if abs(b.risk.risk_per_trade - 0.0015) > 1e-12:
        raise ValueError("V11.1 risk_per_trade is pre-registered at 0.15%")
    if abs(b.stop.floor_pct - 0.55) > 1e-12 or abs(b.stop.cap_pct - 1.00) > 1e-12:
        raise ValueError("V11.1 stop bounds are pre-registered at 0.55% / 1.00%")
    if abs(sum(b.weights.as_dict().values()) - 100.0) > 1e-9:
        raise ValueError("group weights must sum to 100")
    if "volatility" in cfg.quality.directional_groups:
        raise ValueError("volatility is non-directional and never counts as evidence")
    return cfg
