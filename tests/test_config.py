from __future__ import annotations

import pytest
from pydantic import ValidationError

from btc_swing.core.config import load_btc_config
from tests.conftest import ROOT

CFG = ROOT / "config" / "btc_swing.default.yaml"


def test_default_config_loads_and_hash_is_stable() -> None:
    a, b = load_btc_config(CFG), load_btc_config(CFG)
    assert a.config_hash == b.config_hash
    assert a.risk.max_leverage <= 10
    assert 0 < a.risk.risk_per_trade <= 0.02


def test_override_changes_hash() -> None:
    a = load_btc_config(CFG)
    b = load_btc_config(CFG, overrides={"risk": {"risk_per_trade": 0.0025}})
    assert a.config_hash != b.config_hash
    assert b.risk.risk_per_trade == 0.0025


@pytest.mark.parametrize(
    "overrides",
    [
        {"risk": {"max_leverage": 20}},
        {"risk": {"allowed_leverage": [1, 2, 25], "max_leverage": 10}},
        {"risk": {"risk_per_trade": 0.05}},
        {"exits": {"tp1_frac": 0.7, "tp2_frac": 0.5}},
        {"regime": {"low_vol_atr_pct": 0.1}},
        {"unknown_section": {"x": 1}},
    ],
)
def test_hard_limits_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        load_btc_config(CFG, overrides=overrides)
