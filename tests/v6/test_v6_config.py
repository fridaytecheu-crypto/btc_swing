from __future__ import annotations

import pytest
from pydantic import ValidationError

from btc_swing.v5.demo.ids import STRATEGY_PREFIX, V51_STRATEGY_PREFIX
from btc_swing.v6.config import (
    FORWARD_ONLY_FAMILIES,
    HISTORICAL_FAMILIES,
    V6_LINK_PREFIX,
    V6Config,
    V6Family,
)
from tests.v6.helpers import cfg


def test_config_loads_and_is_isolated() -> None:
    c = cfg()
    assert c.strategy_name == "btc_swing_v6_active_intraday"
    assert len(c.config_hash) == 64
    assert set(c.enabled_families()) == set(HISTORICAL_FAMILIES)
    assert c.enabled_families() == [
        f for f in c.episode.family_priority if f in HISTORICAL_FAMILIES
    ]
    assert V6_LINK_PREFIX == "V6D-" and V6_LINK_PREFIX not in (STRATEGY_PREFIX, V51_STRATEGY_PREFIX)


def test_forward_only_families_cannot_be_enabled() -> None:
    raw = cfg().model_dump(mode="json")
    raw["families"]["liquidation_flow_reversal"]["enabled"] = True
    with pytest.raises(ValidationError):
        V6Config.model_validate(raw)
    assert set(FORWARD_ONLY_FAMILIES) == {
        V6Family.LIQUIDATION_FLOW_REVERSAL,
        V6Family.LIQUIDATION_CONTINUATION,
    }


@pytest.mark.parametrize(
    "path,value",
    [
        (("risk", "risk_per_trade"), 0.03),
        (("risk", "max_leverage"), 20),
        (("risk", "risk_grid"), [0.001, 0.05]),
        (("episode", "pyramiding"), True),
        (("episode", "averaging"), True),
        (("exits", "max_hold_hours"), 48),
        (("exits", "default_variant"), "Z"),
    ],
)
def test_hard_limits(path: tuple[str, str], value: object) -> None:
    raw = cfg().model_dump(mode="json")
    raw[path[0]][path[1]] = value
    with pytest.raises(ValidationError):
        V6Config.model_validate(raw)


def test_frozen_generations_untouched() -> None:
    """V6 work never changes V5 / V5.1 configuration (pinned hashes)."""
    from btc_swing.v5.config import load_v5_config
    from btc_swing.v51.config import (
        EXPECTED_V51_CONFIG_HASH,
        PARENT_V5_CONFIG_HASH,
        load_v51_config,
    )

    assert load_v5_config("config/btc_swing_v5.yaml").config_hash == PARENT_V5_CONFIG_HASH
    assert load_v51_config("config/btc_swing_v5_1.yaml").config_hash == EXPECTED_V51_CONFIG_HASH


def test_v6_has_no_exchange_code_path() -> None:
    """No V6 module contains exchange calls or imports (docstrings/comments excluded)."""
    import io
    import pathlib
    import tokenize

    forbidden = (
        "create_order",
        "set_leverage",
        "trading_stop",
        "BYBIT_",
        "api-demo",
        "api.bybit",
        "httpx",
        "websockets",
        "btc_swing.v5.demo",
        "btc_swing.v51",
    )
    for p in pathlib.Path("btc_swing/v6").glob("*.py"):
        toks = tokenize.generate_tokens(io.StringIO(p.read_text()).readline)
        code = " ".join(t.string for t in toks if t.type not in (tokenize.STRING, tokenize.COMMENT))
        for word in forbidden:
            assert word not in code, f"{p}: {word}"
