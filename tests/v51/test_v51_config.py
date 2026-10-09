"""V5.1 config: frozen V5 strategy sections identical, pinned hash, mandatory data-quality rules."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from btc_swing.v5.config import load_v5_config
from btc_swing.v51.config import (
    EXPECTED_V51_CONFIG_HASH,
    PARENT_V5_CONFIG_HASH,
    STRATEGY_SECTIONS,
    V51Config,
    assert_strategy_identical,
    load_v51_config,
    strategy_sections,
)

ROOT = Path(__file__).resolve().parents[2]


def test_v51_strategy_sections_are_byte_identical_to_frozen_v5() -> None:
    v51 = load_v51_config(ROOT / "config" / "btc_swing_v5_1.yaml")
    v5 = load_v5_config(ROOT / "config" / "btc_swing_v5.yaml")
    info = assert_strategy_identical(v51, v5)
    assert strategy_sections(v51) == strategy_sections(v5)
    assert v5.config_hash == PARENT_V5_CONFIG_HASH
    assert info["parent_v5_config_hash"] == PARENT_V5_CONFIG_HASH
    # thresholds, event definitions, risk, stops, targets, leverage, exits: unchanged
    assert v51.events == v5.events and v51.execution == v5.execution
    assert v51.risk == v5.risk and v51.exits == v5.exits and v51.features == v5.features
    assert v51.episode == v5.episode and v51.costs == v5.costs


def test_v51_hash_is_pinned_and_differs_from_v5() -> None:
    v51 = load_v51_config(ROOT / "config" / "btc_swing_v5_1.yaml")
    assert v51.config_hash == EXPECTED_V51_CONFIG_HASH
    assert v51.config_hash != PARENT_V5_CONFIG_HASH
    assert v51.data_quality.version == "v5.1-dq-1"


def test_data_quality_rules_are_mandatory() -> None:
    raw = yaml.safe_load((ROOT / "config" / "btc_swing_v5_1.yaml").read_text())
    for flag in (
        "gap_rows_are_not_observations",
        "z_windows_exclude_invalid_observations",
        "feature_windows_must_be_clean",
        "atr_from_clean_1h_bars_only",
        "entry_geometry_requires_clean_struct_window",
    ):
        bad = {**raw, "data_quality": {**raw["data_quality"], flag: False}}
        with pytest.raises(ValidationError):
            V51Config.model_validate(bad)
    with pytest.raises(ValidationError):
        V51Config.model_validate({**raw, "strategy_name": "btc_swing_v5_microstructure"})
    # a changed threshold is detected by the identity assertion
    v5 = load_v5_config(ROOT / "config" / "btc_swing_v5.yaml")
    tweaked = V51Config.model_validate(
        {
            **raw,
            "events": {
                **raw["events"],
                "absorption_reversal": {**raw["events"]["absorption_reversal"], "vol_1h_z": 0.5},
            },
        }
    )
    with pytest.raises(RuntimeError, match="events"):
        assert_strategy_identical(tweaked, v5)
    assert len(STRATEGY_SECTIONS) == 15
