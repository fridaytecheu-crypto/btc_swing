"""Read-only V5 signal diagnostic: exact agreement with the frozen detectors, zero side effects,
and detection of z windows distorted by carried-forward outage rows."""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from btc_swing.core.enums import Side
from btc_swing.v5.config import V5Family, load_v5_config
from btc_swing.v5.events import build_v5_detectors
from btc_swing.v5.features import FeatureFrame
from btc_swing.v5.forward import diagnostic as dg
from btc_swing.v5.forward.config import ForwardPaths, load_forward_config, load_frozen_v5
from btc_swing.v5.forward.pipeline import ForwardContext

ROOT = Path(__file__).resolve().parents[2]
CFG = load_v5_config(ROOT / "config" / "btc_swing_v5.yaml")


def _frame(n: int, seed: int = 3, nan_frac: float = 0.05) -> FeatureFrame:
    rng = np.random.RandomState(seed)
    t = (1_790_000_000_000 + np.arange(n) * 300_000).astype(np.int64)

    def z() -> np.ndarray:
        x = rng.normal(0, 1.6, n)
        x[rng.rand(n) < nan_frac] = np.nan
        return x

    close = 80_000 + np.cumsum(rng.normal(0, 30, n))
    cols = {
        "close": close,
        "high": close + 20,
        "low": close - 20,
        "atr": np.where(rng.rand(n) < 0.02, np.nan, 300.0),
        "ret_1h_z": z(),
        "oi_chg_1h_z": z(),
        "vol_1h_z": z(),
        "imbalance_1h_z": z(),
        "cvd_slope_1h_z": z(),
        "fund_z": z(),
        "prem_z": z(),
        "break_up": (rng.rand(n) < 0.3).astype(float),
        "break_dn": (rng.rand(n) < 0.3).astype(float),
        "hi_break_ref": close + 100,
        "lo_break_ref": close - 100,
        "lo_struct": close - 50,
        "hi_struct": close + 50,
        "ret_1h": rng.normal(0, 0.004, n),
        "volume": rng.uniform(50, 500, n),
        "imbalance_1h": rng.normal(0, 0.15, n),
        "cvd_slope_1h": rng.normal(0, 30, n),
        "oi_chg_1h": rng.normal(0, 0.001, n),
        "prem_1h": rng.normal(0, 1e-4, n),
        "fund": rng.normal(0, 1e-4, n),
    }
    return FeatureFrame(t, cols)


def test_condition_table_matches_the_frozen_detectors_exactly() -> None:
    ff = _frame(4000)
    dets = {(d.v5_family, d.side): d for d in build_v5_detectors(CFG, ff)}
    checked = fired = 0
    for fam in V5Family:
        for side in (Side.LONG, Side.SHORT):
            det = dets[(fam, side)]
            conds = dg.family_conditions(CFG, fam, side)
            for k in range(0, 4000, 7):
                ev = [dg._eval(CFG, ff, c, side, k, fam) for c in conds]
                base = not math.isnan(ff.v("atr", k)) and ff.v("atr", k) > 0
                mine = all(e["pass"] for e in ev) and base
                assert mine == bool(det.mask[k]), (fam, side, k, ev)
                checked += 1
                fired += int(mine)
    assert checked > 4000 and fired > 20  # both outcomes exercised


def test_thresholds_are_the_frozen_config_values() -> None:
    e = CFG.events
    got = {
        (f.value, c.col): c.thr for f in V5Family for c in dg.family_conditions(CFG, f, Side.LONG)
    }
    assert (
        got[("LIQUIDATION_CONTINUATION", "ret_1h_z")] == e.liquidation_continuation.impulse_ret_1h_z
    )
    assert got[("ABSORPTION_REVERSAL", "imbalance_1h_z")] == e.absorption_reversal.imbalance_1h_z
    assert got[("FLOW_OI_CONTINUATION", "oi_chg_1h_z")] == e.flow_oi_continuation.oi_chg_1h_z
    assert (
        got[("FLOW_DIVERGENCE_REVERSAL", "oi_chg_1h_z")] == e.flow_divergence_reversal.max_oi_chg_z
    )


def test_outage_rows_distort_the_volume_window_and_are_reported(tmp_path: Path) -> None:
    n = 9000
    ff = _frame(n, nan_frac=0.0)
    vol = ff.cols["volume"]
    gap = np.zeros(n)
    gap[5000:5400] = 1.0
    vol[5000:5400] = 0.0
    # rolling-sum float residue makes the 1h volume of outage rows tiny but positive
    ff.cols["volume"] = np.where(vol == 0, 1e-13, vol)
    rows = pl.DataFrame({"close_time_ms": ff.close_ms, "gap_filled": gap})
    q = dg.data_quality(CFG, ff, rows, n - 1)
    v = q["vol_1h_z"]
    assert q["gap_rows_in_30d_window"] == 400
    assert v["distorted"] and v["unreachable"] and v["std_inflation_vs_live_rows"] > 3
    assert v["btc_per_hour_needed_for_vol_1h_z_1.0"] > 100_000
    assert q["gap_rows_leave_the_window_at"] is not None


def _fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        st = p.stat()
        h.update(f"{p.relative_to(root)}|{p.is_dir()}|{st.st_size}|{st.st_mtime_ns}".encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def _forward_bars(paths: ForwardPaths, start: int, n: int) -> None:
    rng = np.random.RandomState(5)
    close = 80_000 + np.cumsum(rng.normal(0, 25, n))
    t = start + np.arange(n) * 300_000
    vol = rng.uniform(100, 400, n)
    buy = vol * rng.uniform(0.3, 0.7, n)
    df = pl.DataFrame(
        {
            "open_time_ms": t,
            "close_time_ms": t + 300_000,
            "open": close,
            "high": close + 15,
            "low": close - 15,
            "close": close,
            "volume": vol,
            "quote_volume": vol * close,
            "trades": np.full(n, 500, dtype=np.int64),
            "taker_buy_volume": buy,
            "taker_buy_quote_volume": buy * close,
            "buy_qty": buy,
            "sell_qty": vol - buy,
            "n_buy": np.full(n, 250, dtype=np.int64),
            "n_sell": np.full(n, 250, dtype=np.int64),
            "buy_notional": buy * close,
            "sell_notional": (vol - buy) * close,
            "big_buy_qty": buy * 0.2,
            "big_sell_qty": (vol - buy) * 0.2,
            "big_buy_notional_100k": buy * close * 0.1,
            "big_sell_notional_100k": (vol - buy) * close * 0.1,
            "max_trade_qty": np.full(n, 3.0),
            "vwap": close,
            "gap_filled": np.zeros(n),
            "oi_last": 58_000 + np.cumsum(rng.normal(0, 5, n)),
            "oi_value_last": np.full(n, 4.7e9),
            "mark_open": close,
            "mark_high": close + 15,
            "mark_low": close - 15,
            "mark_close": close,
            "index_open": close,
            "index_high": close + 15,
            "index_low": close - 15,
            "index_close": close - 5,
            "next_funding_ms": np.full(n, float(start + 8 * 3_600_000)),
            "funding_rate_last": np.full(n, 1e-4),
        }
    )
    df.write_parquet(paths.bars_dir / "2026-10-07.parquet")


def test_signal_diagnostic_has_zero_side_effects(tmp_path: Path) -> None:
    fcfg = load_forward_config(ROOT / "config" / "btc_swing_v5_forward.yaml")
    cfg = load_frozen_v5(fcfg, ROOT)
    start = int(datetime(2026, 10, 7, 15, 0, tzinfo=UTC).timestamp() * 1000)
    ctx = ForwardContext(fcfg, cfg, ForwardPaths(tmp_path, "BTCUSDT"), start)
    _forward_bars(ctx.paths, start - 600 * 300_000, 900)
    before = _fingerprint(tmp_path)
    d = dg.signal_diagnostic(ctx, now_ms=start + 300 * 300_000)
    text = dg.render_diagnostic(d)
    assert _fingerprint(tmp_path) == before  # nothing created, written or touched
    assert d["read_only"] and len(d["families"]) == 8
    assert all(f["logic_check"] == "matches frozen detector mask" for f in d["families"])
    assert "CLOSEST TO TRIGGER" in text and "FAMILY | SIDE | EVENT" in text
    assert d["signals_since_start"] == 0 and not any(f["signal_now"] for f in d["families"])
    # warm-up is reported, not assumed: 900 rows < the frozen 2880-row minimum
    assert all(not f["warm"] for f in d["families"])


@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
def test_distance_semantics(side: Side) -> None:
    ff = _frame(3000, nan_frac=0.0)
    k = 2999
    ff.cols["vol_1h_z"][k] = 0.4
    c = next(
        x
        for x in dg.family_conditions(CFG, V5Family.ABSORPTION_REVERSAL, side)
        if x.col == "vol_1h_z"
    )
    e = dg._eval(CFG, ff, c, side, k, V5Family.ABSORPTION_REVERSAL)
    assert not e["pass"] and e["distance"] == pytest.approx(0.6)
