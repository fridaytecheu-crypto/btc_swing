"""BTC_V5_1_DATA_QUALITY_FIX configuration: the frozen V5 strategy sections, unchanged, plus the
`data_quality` block. `V51Config` is a strict superset of `V5Config`; `assert_strategy_identical`
proves that every frozen V5 section (events, features, execution, episode, risk, exits, costs,
stage_a, safety overlay, indicators, instrument, data, collector, research) is identical, so the
only difference between V5 and V5.1 is how synthetic rows and warm-up history are handled.

V5 itself (`btc_swing/v5/`) is not modified: V5.1 imports its frozen detectors and engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from btc_swing.core.hashing import sha256_text
from btc_swing.v5.config import (
    V5_EVENT_RULE_VERSION,
    V5_EXIT_RULE_VERSION,
    V5_FEATURE_VERSION,
    V5Config,
)

V51_VERSION = "5.1.0-forward"
V51_FEATURE_VERSION = "v5.1-feat-1"  # == v5-feat-1 on continuous clean data (tests prove it)
V51_DATA_QUALITY_VERSION = "v5.1-dq-1"
V51_STRATEGY_NAME = "btc_swing_v5_1_data_quality_fix"
V5_STRATEGY_NAME = "btc_swing_v5_microstructure"
# Frozen parent: the V5 forward observation (manifests/v5_forward_freeze.json)
PARENT_V5_CONFIG_HASH = "d18ebf19bd0cde67be1c27683d3e7ad0385d2456a6edaa1fe55146f013096177"
# Pinned V5.1 config hash (config/btc_swing_v5_1.yaml); tests/v51 verify the file still hashes to it.
EXPECTED_V51_CONFIG_HASH = "144f7d58bb7150c7f9084fcbd2b5547537ec677c5f92fa4628fd025c502d60d7"

STRATEGY_SECTIONS = (
    "instrument",
    "data",
    "indicators",
    "features",
    "events",
    "stage_a",
    "execution",
    "episode",
    "risk",
    "exits",
    "costs",
    "cost_sensitivity",
    "safety_overlay",
    "collector",
    "research",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OiSeedCfg(_Strict):
    enabled: bool
    interval: str
    days_before_v5_start: int = Field(ge=10, le=60)
    max_rel_diff_vs_live: float = Field(gt=0, le=0.05)


class PremiumSeedCfg(_Strict):
    enabled: bool
    interval: str
    days_before_v5_start: int = Field(ge=10, le=60)
    max_rel_diff_vs_live: float = Field(gt=0, le=0.01)


class FundingSeedCfg(_Strict):
    enabled: bool
    days_before_v5_start: int = Field(ge=15, le=90)


class DataQualityCfg(_Strict):
    version: str
    gap_rows_are_not_observations: bool
    z_windows_exclude_invalid_observations: bool
    feature_windows_must_be_clean: bool
    atr_from_clean_1h_bars_only: bool
    entry_geometry_requires_clean_struct_window: bool
    oi_seed: OiSeedCfg
    premium_seed: PremiumSeedCfg
    funding_seed: FundingSeedCfg

    @field_validator(
        "gap_rows_are_not_observations",
        "z_windows_exclude_invalid_observations",
        "feature_windows_must_be_clean",
        "atr_from_clean_1h_bars_only",
        "entry_geometry_requires_clean_struct_window",
    )
    @classmethod
    def _must_be_true(cls, v: bool) -> bool:
        if not v:
            raise ValueError("every V5.1 data-quality rule is mandatory (true)")
        return v

    @field_validator("version")
    @classmethod
    def _version(cls, v: str) -> str:
        if v != V51_DATA_QUALITY_VERSION:
            raise ValueError(f"data_quality.version must be {V51_DATA_QUALITY_VERSION}")
        return v


class V51Config(V5Config):
    data_quality: DataQualityCfg

    @field_validator("strategy_name")
    @classmethod
    def _name(cls, v: str) -> str:
        if v != V51_STRATEGY_NAME:
            raise ValueError(f"strategy_name must be {V51_STRATEGY_NAME}")
        return v


DEFAULT_V51_CONFIG_PATH = Path("config/btc_swing_v5_1.yaml")
DEFAULT_V5_CONFIG_PATH = Path("config/btc_swing_v5.yaml")


def load_v51_config(path: str | Path | None = None) -> V51Config:
    p = Path(path) if path else DEFAULT_V51_CONFIG_PATH
    with p.open() as f:
        raw: dict[str, Any] = yaml.safe_load(f)
    return V51Config.model_validate(raw)


def strategy_sections(cfg: V5Config) -> dict[str, Any]:
    d = cfg.model_dump(mode="json")
    return {k: d[k] for k in STRATEGY_SECTIONS}


def assert_strategy_identical(v51: V51Config, v5: V5Config) -> dict[str, Any]:
    """Raise unless every frozen V5 strategy section is identical in V5.1; return the sections."""
    a, b = strategy_sections(v51), strategy_sections(v5)
    diff = [k for k in STRATEGY_SECTIONS if a[k] != b[k]]
    if diff:
        raise RuntimeError(f"V5.1 differs from frozen V5 in strategy section(s): {diff}")
    if v5.config_hash != PARENT_V5_CONFIG_HASH:
        raise RuntimeError("the V5 config is not the frozen parent (hash mismatch)")
    return {
        "parent_v5_config_hash": v5.config_hash,
        "v51_config_hash": v51.config_hash,
        "strategy_sections_sha256": sha256_text(
            yaml.safe_dump(a, sort_keys=True)
        ),  # identical for V5 and V5.1
        "rule_versions": {
            "features": V51_FEATURE_VERSION,
            "features_parent": V5_FEATURE_VERSION,
            "events": V5_EVENT_RULE_VERSION,
            "exits": V5_EXIT_RULE_VERSION,
            "data_quality": V51_DATA_QUALITY_VERSION,
        },
    }
