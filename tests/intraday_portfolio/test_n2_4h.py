"""N2 4h re-horizon: frozen events are reused verbatim (no re-detection), horizons 120/240/480 from the
unchanged post-embargo entry, funding proxy sign-correct and never silently zero, 4h occupancy recount,
decision rule, determinism."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import polars as pl

from btc_swing.research.intraday_portfolio import n2_4h
from btc_swing.research.intraday_portfolio.contract import MS_1M
from btc_swing.research.intraday_portfolio.primitives import Bars
from tests.intraday_portfolio.test_n_engines import T0, make_1m
from tests.intraday_portfolio.test_run import _write_day


def _frozen(path: Path, rows: list[tuple[str, str, int]]) -> None:
    pl.DataFrame(
        {
            "parent_id": [r[0] for r in rows],
            "side": [r[1] for r in rows],
            "t_decision_time": [r[2] for r in rows],
            "t_benchmark_entry_time": [r[2] + MS_1M for r in rows],
            "eligibility_state": ["CLOSED"] * len(rows),
            "lvl_atr_bps": [40.0] * len(rows),
            "lvl_atr_setup": [20.0] * len(rows),
            "er": [0.1] * len(rows),
            "regime": ["RANGE"] * len(rows),
            "lvl_flow15_signed_quote": [240.0] * len(rows),
            "lvl_threshold_abs_flow": [100.0] * len(rows),
            "note_prior_15m_ret_bps": [1.0] * len(rows),
        }
    ).write_parquet(path)


def test_frozen_events_loaded_verbatim_and_entry_convention_checked(tmp_path: Path) -> None:
    p = tmp_path / "n2.parquet"
    t1 = T0 + 1000 * MS_1M
    _frozen(p, [("N2S-L-b", "LONG", t1 + 60 * MS_1M), ("N2S-S-a", "SHORT", t1)])
    evs, meta = n2_4h.load_frozen_events(p)
    assert [e.parent_id for e in evs] == ["N2S-S-a", "N2S-L-b"] and meta["rows"] == 2
    assert evs[0].entry_ms == t1 + MS_1M and evs[0].side == "SHORT"
    # a source whose entry is not decision + 1 min is refused (the causal convention is frozen)
    bad = pl.read_parquet(p).with_columns(pl.col("t_benchmark_entry_time") + MS_1M)
    bad.write_parquet(tmp_path / "bad.parquet")
    try:
        n2_4h.load_frozen_events(tmp_path / "bad.parquet")
    except ValueError as e:
        assert "embargo" in str(e)
    else:
        raise AssertionError("broken entry convention accepted")


def test_replay_horizons_funding_sign_and_nan(tmp_path: Path) -> None:
    b1 = make_1m(3, noise=0.0, impulses={1200: 50.0})  # +50 bps at minute 1200 (inside 2h..4h)
    t_dec = T0 + 1000 * MS_1M
    ev = n2_4h.FrozenEvent(
        "X", "LONG", t_dec, t_dec + MS_1M, 40.0, 20.0, 0.1, "RANGE", "CLOSED", 1.0, 1.0, 0.0
    )
    fund_t = np.array([T0 + 1200 * MS_1M], dtype=np.int64)  # one settlement inside the 4h window
    fund_r = np.array([0.0001])  # +1 bp: a LONG pays it
    p = n2_4h.replay_event(ev, b1, fund_t, fund_r)
    assert p.traded and p.exit is not None and p.exit.time_ms == t_dec + MS_1M + 240 * MS_1M
    assert p.parent_id == "X" and p.times.benchmark_entry_time == t_dec + MS_1M
    assert math.isclose(p.notes["gross_120m_bps"], 0.0, abs_tol=1e-6)  # impulse after 2h
    assert p.gross_bps is not None and 49 < p.gross_bps < 51 and 49 < p.notes["gross_480m_bps"] < 51
    assert p.notes["funding_120m_bps"] == 0.0 and p.notes["funding_240m_bps"] == 1.0
    assert math.isclose(p.net_bps or 0, p.gross_bps - 16.0 - 1.0)
    assert math.isclose(p.net_r or 0, (p.net_bps or 0) / 40.0)
    # SHORT receives the same settlement; absent funding data -> NaN, never 0
    q = n2_4h.replay_event(
        n2_4h.FrozenEvent(
            "Y", "SHORT", t_dec, t_dec + MS_1M, 40.0, 20.0, 0.1, "RANGE", "CLOSED", -1.0, 1.0, 0.0
        ),
        b1,
        fund_t,
        fund_r,
    )
    assert q.notes["funding_240m_bps"] == -1.0
    z = n2_4h.replay_event(ev, b1, np.zeros(0, dtype=np.int64), np.zeros(0))
    assert math.isnan(z.notes["funding_240m_bps"]) and math.isclose(
        z.net_bps or 0, (z.gross_bps or 0) - 16.0
    )
    # a rejected frozen event stays rejected; a path that runs off the data is not eligible
    r = n2_4h.replay_event(
        n2_4h.FrozenEvent(
            "R", "LONG", t_dec, t_dec + MS_1M, 40.0, 20.0, 0.1, "RANGE", "REJECTED", 1.0, 1.0, 0.0
        ),
        b1,
        fund_t,
        fund_r,
    )
    assert not r.traded and "FROZEN_EVENT_WAS_REJECTED" in r.rejection_reasons
    late = n2_4h.replay_event(
        n2_4h.FrozenEvent(
            "L",
            "LONG",
            T0 + 4200 * MS_1M,
            T0 + 4201 * MS_1M,
            40.0,
            20.0,
            0.1,
            "RANGE",
            "CLOSED",
            1.0,
            1.0,
            0.0,
        ),
        b1,
        fund_t,
        fund_r,
    )
    assert not late.traded and "ENTRY_OR_HORIZON_PATH_UNAVAILABLE_240M" in late.rejection_reasons


def _b1_days(b1: Bars, root: Path, days: list[str]) -> None:
    for k, d in enumerate(days):
        sl = slice(k * 1440, (k + 1) * 1440)
        part = Bars(
            b1.open_ms[sl],
            b1.close_ms[sl],
            b1.open[sl],
            b1.high[sl],
            b1.low[sl],
            b1.close[sl],
            b1.volume[sl],
            b1.vwap[sl],
            b1.buy[sl],
            b1.sell[sl],
            b1.step_ms,
        )
        _write_day(root, d, part, "1m")


def test_run_occupancy_recount_decision_and_determinism(tmp_path: Path) -> None:
    b1 = make_1m(2, noise=0.0)
    _b1_days(b1, tmp_path / "data", ["2024-01-01", "2024-01-02"])
    src = tmp_path / "n2.parquet"
    t1 = T0 + 100 * MS_1M
    # three LONG events 60 min apart: under a 240-min hold the 2nd and 3rd are occupied; a 4th 5h later is free
    _frozen(
        src,
        [
            ("A", "LONG", t1),
            ("B", "LONG", t1 + 60 * MS_1M),
            ("C", "SHORT", t1 + 120 * MS_1M),
            ("D", "LONG", t1 + 300 * MS_1M),
        ],
    )
    r1 = n2_4h.run_n2_4h(tmp_path / "data", tmp_path / "out1", src, run_id="t")
    n2_4h.run_n2_4h(tmp_path / "data", tmp_path / "out2", src, run_id="t")
    c = r1["results"]["cadence"]
    assert c["frozen_independent_episodes"] == 4 and c["eligible_4h_paths"] == 4
    assert c["available_after_4h_occupancy"] == 2 and c["occupied_by_open_n2_position"] == 2
    assert c["same_direction_occupied"] == 1 and c["opposite_direction_occupied"] == 1
    assert r1["decision"]["verdict"] == "REJECT N2 AS DIRECTIONAL ALPHA"  # flat tape: gross 0
    for name in (
        "n2_4h_parents.csv",
        "n2_4h_markouts.csv",
        "n2_4h_cost_screen.csv",
        "n2_4h_occupancy.csv",
        "SUMMARY.md",
    ):
        assert (tmp_path / "out1" / "t" / name).read_bytes() == (
            tmp_path / "out2" / "t" / name
        ).read_bytes()
    m1 = json.loads((tmp_path / "out1" / "t" / "experiment_manifest_n2_4h.json").read_text())
    m2 = json.loads((tmp_path / "out2" / "t" / "experiment_manifest_n2_4h.json").read_text())
    assert m1["preregistration_sha256"] == m2["preregistration_sha256"]
    assert m1["preregistration"]["frozen_source"]["rows"] == 4 and m1["order_capable"] is False
    ids = set(pl.read_csv(tmp_path / "out1" / "t" / "n2_4h_parents.csv")["parent_id"].to_list())
    assert ids == {"A", "B", "C", "D"}  # exactly the frozen events, nothing re-detected
