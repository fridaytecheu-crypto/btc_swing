from __future__ import annotations

import math

import polars as pl
import pytest
from pydantic import ValidationError

from btc_swing.v61.config import V61Config
from btc_swing.v61.features import build_frame_v61
from btc_swing.v61.research import Window, frequency61, hold_distribution, stop_buckets, verdict
from tests.v61.helpers import START, cfg, synthetic_inputs

DAY = 86_400_000


def _trades(entries: list[int], net_r: list[float], stop_pct: float = 0.6) -> pl.DataFrame:
    n = len(entries)
    return pl.DataFrame(
        {
            "entry_ms": entries,
            "exit_ms": [e + 3_600_000 for e in entries],
            "hold_min": [60.0] * n,
            "net_r": net_r,
            "gross_r": [r + 0.2 for r in net_r],
            "fee_r": [-0.15] * n,
            "slip_r": [-0.05] * n,
            "fund_r": [0.0] * n,
            "net_pnl": [r * 7.5 for r in net_r],
            "stop_bps": [stop_pct * 100] * n,
            "stop_pct": [stop_pct] * n,
            "cost_share_stop": [15.0 / (stop_pct * 100)] * n,
            "mfe_r": [1.0] * n,
            "mae_r": [0.5] * n,
            "stop_is_floor": [False] * n,
            "exit_reason": ["STOP"] * n,
            "regime_code": [4] * n,
            "family": ["TREND_PULLBACK_RECLAIM"] * n,
            "side": ["LONG"] * n,
        }
    )


def test_frequency61_buckets_calendar_first() -> None:
    c = cfg()
    fr = build_frame_v61(synthetic_inputs(n_days=45), c)
    w = Window("validation", START + 35 * DAY, START + 45 * DAY)
    ent = (
        [w.start_ms + 3_600_000 * (1 + i) for i in range(4)]
        + [w.start_ms + DAY + 3_600_000]
        + [w.start_ms + 5 * DAY + 600_000 * (1 + i) for i in range(6)]
    )
    fq = frequency61(_trades(ent, [0.5] * len(ent)), fr, w, c)
    pd = fq["pct_days_v61"]
    assert fq["calendar_days"] == 10 and math.isclose(fq["mean_trades_per_calendar_day"], 1.1)
    assert (
        math.isclose(pd["0"], 0.7)
        and math.isclose(pd["1"], 0.1)
        and math.isclose(pd["3-4"], 0.1)
        and math.isclose(pd["5-6"], 0.1)
    )
    assert fq["meets_hard_minimum"] is False and fq["in_preferred_band"] is False


def test_hold_and_stop_buckets() -> None:
    c = cfg()
    t = _trades([START + i * DAY for i in range(6)], [0.5, -1.0, 0.3, 0.2, -1.1, 0.4], stop_pct=0.8)
    h = hold_distribution(t)
    assert h["n"] == 6 and math.isclose(h["buckets"]["30-60m"] + h["buckets"]["1-2h"], 1.0)
    b = stop_buckets(t, c)
    assert [r["n"] for r in b] == [0, 0, 6, 0, 0]
    assert b[2]["bucket"] == "0.75-1%" and math.isclose(b[2]["cost_share_stop_median"], 15.0 / 80.0)


def test_verdict_logic() -> None:
    c = cfg()
    good_stats = {
        "net_r_mean": 0.15,
        "profit_factor": 1.3,
        "net_r_boot_lo": 0.02,
        "max_dd_frac": 0.05,
        "net_r_sum": 100.0,
    }
    base = {
        "stats": good_stats,
        "frequency": {"mean_trades_per_calendar_day": 2.4},
        "folds": [{"n": 10, "net_r_sum": 1.0}, {"n": 10, "net_r_sum": 2.0}],
        "by_family": [{"family": "A", "net_r_sum": 50.0}, {"family": "B", "net_r_sum": 50.0}],
    }
    dev = {"folds": [{"n": 10, "net_r_sum": 1.0}] * 6}
    v = verdict(c, {"bybit_validation": base, "bybit_development": dev}, {})
    assert v["frequency_pass"] and v["net_edge_pass"] and v["recommend_spending_holdout"]
    bad = dict(
        base,
        stats={**good_stats, "net_r_mean": -0.1, "profit_factor": 0.8},
        frequency={"mean_trades_per_calendar_day": 1.2},
    )
    v2 = verdict(c, {"bybit_validation": bad, "bybit_development": dev}, {})
    assert (
        not v2["frequency_pass"]
        and not v2["net_edge_pass"]
        and not v2["recommend_spending_holdout"]
    )
    one_family = dict(
        base, by_family=[{"family": "A", "net_r_sum": 95.0}, {"family": "B", "net_r_sum": 5.0}]
    )
    v3 = verdict(c, {"bybit_validation": one_family, "bybit_development": dev}, {})
    assert v3["frequency_pass"] and v3["net_edge_pass"] and not v3["recommend_spending_holdout"]


@pytest.mark.parametrize(
    "path,value",
    [
        (("risk", "risk_per_trade"), 0.03),
        (("risk", "max_leverage"), 20),
        (("episode", "pyramiding"), True),
        (("episode", "one_position"), False),
        (("exits", "max_hold_hours"), 48),
        (("exits", "default_time_stop_hours"), 3),
    ],
)
def test_hard_limits(path, value) -> None:
    raw = cfg().model_dump(mode="json")
    raw[path[0]][path[1]] = value
    with pytest.raises(ValidationError):
        V61Config.model_validate(raw)


def test_frozen_generations_untouched() -> None:
    from btc_swing.v5.config import load_v5_config
    from btc_swing.v6.config import load_v6_config
    from btc_swing.v51.config import (
        EXPECTED_V51_CONFIG_HASH,
        PARENT_V5_CONFIG_HASH,
        load_v51_config,
    )

    assert load_v5_config("config/btc_swing_v5.yaml").config_hash == PARENT_V5_CONFIG_HASH
    assert load_v51_config("config/btc_swing_v5_1.yaml").config_hash == EXPECTED_V51_CONFIG_HASH
    assert load_v6_config("config/btc_swing_v6.yaml").config_hash.startswith("9d4ef909f4a3d2e5")


def test_v61_has_no_exchange_code_path() -> None:
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
        "websockets",
        "btc_swing.v5.demo",
        "btc_swing.v51",
    )
    for p in pathlib.Path("btc_swing/v61").glob("*.py"):
        toks = tokenize.generate_tokens(io.StringIO(p.read_text()).readline)
        code = " ".join(t.string for t in toks if t.type not in (tokenize.STRING, tokenize.COMMENT))
        for word in forbidden:
            assert word not in code, f"{p}: {word}"
    # the only network use is the PUBLIC archive download in ingest.py
    ing = pathlib.Path("btc_swing/v61/ingest.py").read_text()
    assert "public.bybit.com/trading/" in ing and "api.bybit.com" not in ing
