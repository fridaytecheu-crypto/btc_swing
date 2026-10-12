from __future__ import annotations

import math

import numpy as np
import polars as pl

from btc_swing.v6.evaluation import combined_gate, family_criteria, frequency_report, trade_stats
from btc_swing.v6.features import build_frame
from tests.v6.helpers import START, cfg, synthetic_inputs

DAY = 86_400_000


def _trades(entries_ms: list[int], net_r: list[float]) -> pl.DataFrame:
    n = len(entries_ms)
    return pl.DataFrame(
        {
            "entry_ms": entries_ms,
            "exit_ms": [e + 3_600_000 for e in entries_ms],
            "hold_min": [60.0] * n,
            "net_r": net_r,
            "gross_r": [r + 0.2 for r in net_r],
            "fee_r": [-0.15] * n,
            "slip_r": [-0.05] * n,
            "fund_r": [0.0] * n,
            "net_pnl": [r * 7.5 for r in net_r],
            "stop_bps": [40.0] * n,
            "mfe_r": [1.0] * n,
            "mae_r": [0.5] * n,
            "stop_is_floor": [False] * n,
            "exit_reason": ["STOP"] * n,
            "regime_code": [4] * n,
            "family": ["MOMENTUM_CONTINUATION"] * n,
            "side": ["LONG"] * n,
        }
    )


def test_frequency_report_is_calendar_day_first() -> None:
    c = cfg()
    fr = build_frame(synthetic_inputs(n_days=40), c)
    start = START + 30 * DAY
    end = START + 40 * DAY  # 10 calendar days
    # day 0: 3 trades, day 1: 1 trade, day 5: 7 trades, other days: 0
    ent = (
        [start + 3_600_000 * (1 + i) for i in range(3)]
        + [start + DAY + 7_200_000]
        + [start + 5 * DAY + 600_000 * (1 + i) for i in range(7)]
    )
    t = _trades(ent, [0.5] * len(ent))
    fq = frequency_report(t, fr, start, end, c)
    assert fq["calendar_days"] == 10 and fq["trades"] == 11
    assert math.isclose(fq["mean_trades_per_calendar_day"], 1.1)
    assert fq["median_trades_per_calendar_day"] == 0.0
    assert math.isclose(fq["pct_days"]["0"], 0.7) and math.isclose(fq["pct_days"]["1"], 0.1)
    assert math.isclose(fq["pct_days"]["3-6"], 0.1) and math.isclose(fq["pct_days"][">6"], 0.1)
    assert fq["max_trades_in_a_day"] == 7
    assert fq["meets_hard_minimum"] is False
    empty = frequency_report(pl.DataFrame(), fr, start, end, c)
    assert empty["trades"] == 0 and empty["pct_days"]["0"] == 1.0


def test_stats_and_gate() -> None:
    c = cfg()
    start, end = START, START + 10 * DAY
    rng = np.random.RandomState(0)
    ent = sorted(int(x) for x in rng.randint(start, end, 40))
    good = _trades(ent, [1.0 if i % 3 else -1.0 for i in range(40)])
    st = trade_stats(good, start, end, c)
    assert st["n"] == 40 and st["trades_per_day"] == 4.0
    assert st["profit_factor"] > 1 and st["net_r_mean"] > 0
    assert st["net_r_boot_lo"] <= st["net_r_mean"] <= st["net_r_boot_hi"]
    fr = build_frame(synthetic_inputs(n_days=12), c)
    fq = frequency_report(good, fr, start, end, c)
    gate = combined_gate(st, fq, [{"year": 2024, "net_r_sum": st["net_r_sum"]}], [], c)
    assert gate["hard_passed"] is True
    bad = _trades(ent[:10], [-0.5] * 10)
    stb = trade_stats(bad, start, end, c)
    gate_b = combined_gate(stb, frequency_report(bad, fr, start, end, c), [], [], c)
    assert gate_b["hard_passed"] is False and gate_b["classification"] == "C"
    crit = family_criteria(stb, [{"n": 3, "net_r_sum": -1.0}] * 3, c)
    assert crit["passed"] is False
