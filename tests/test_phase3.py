from __future__ import annotations

from btc_swing.core.config import load_btc_config
from btc_swing.research.phase3 import (
    PHASE24_CONTROL_CONFIG_HASH,
    PHASE24_VARIANT_CONFIG_HASH,
    THRESHOLDS,
    arm_configs,
    classify,
)
from tests.conftest import ROOT


def test_phase3_arms_are_the_frozen_phase24_configs() -> None:
    """The default YAML + the two arm overrides must hash to the Phase 2.4 CONTROL / variant
    configs: any drift in a frozen threshold breaks this test."""
    cfg = load_btc_config(ROOT / "config" / "btc_swing.default.yaml")
    cfg_c, cfg_v = arm_configs(cfg)
    assert cfg_c.config_hash == PHASE24_CONTROL_CONFIG_HASH
    assert cfg_v.config_hash == PHASE24_VARIANT_CONFIG_HASH
    assert cfg_c.experiment.block_short_in_trend_down is False
    assert cfg_v.experiment.block_short_in_trend_down is True
    assert cfg_c.experiment.entry_mode == cfg_v.experiment.entry_mode == "CONFIRMED_TRIGGER"
    assert cfg_c.exits.breakeven_after_tp1 and cfg_v.exits.breakeven_after_tp1
    assert cfg_c.risk.max_leverage <= 10 and cfg_c.risk.risk_per_trade <= 0.02


def test_phase3_classification_rule_is_pre_declared() -> None:
    def crit(met: dict[int, bool]) -> list[dict[str, object]]:
        return [{"id": i, "met": met.get(i, True), "text": "", "evidence": ""} for i in range(1, 8)]

    assert classify(crit({})).startswith("A")
    assert classify(crit({1: False})).startswith("C")
    assert classify(crit({2: False, 5: False})).startswith("C")
    assert classify(crit({3: False})).startswith("B")
    assert classify(crit({4: False, 7: False})).startswith("B")
    assert abs(THRESHOLDS["c5_drawdown_cap_frac"] - 1.5 * 0.06482147409778874) < 1e-12
