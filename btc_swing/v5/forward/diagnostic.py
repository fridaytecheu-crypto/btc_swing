"""READ-ONLY signal-proximity diagnostic for the frozen V5 forward observation.

Explains, for the latest completed 5m bar, why each frozen V5 family/side does or does not emit a
signal: every Stage A event condition (current value, frozen threshold, PASS/FAIL, distance, warm-up
of its rolling z-score), the Stage B lifecycle state from an in-memory replay of the frozen engine
since the observation start, cooldowns, gap suppression and the STRATEGY_DEMO / risk blockers.

ZERO side effects: it never processes raw data, never extends the seed, never appends to a journal,
never writes state, never calls an exchange. It reuses `pipeline.assemble/build` (pure reads), the
frozen feature frame and the frozen detectors; the condition table below mirrors
`btc_swing/v5/events.py` and every verdict is cross-checked against the frozen detector mask (a
mismatch is reported as LOGIC_MISMATCH instead of being hidden).
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from btc_swing.core.enums import Side, Timeframe
from btc_swing.v5.config import V5Config, V5Family
from btc_swing.v5.engine import V5Engine
from btc_swing.v5.features import FeatureFrame, rolling_sum
from btc_swing.v5.forward.config import ForwardContextLike
from btc_swing.v5.forward.pipeline import assemble, build

MS_5M = 300_000
F = Any


def _iso(ms: float | int | None) -> str | None:
    if ms is None or (isinstance(ms, float) and math.isnan(ms)):
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=UTC).isoformat()


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# ----------------------------------------------------------------------------- z warm-up
@dataclass(frozen=True)
class ZSource:
    """The raw series a frozen z-score is computed from (see features.py)."""

    z_col: str
    label: str
    raw: Callable[[FeatureFrame], Any]
    window: str = "z"  # "z": z_window_bars/z_min_periods; "funding": funding_z_window


def _log_pos(x: Any) -> Any:
    return np.log(np.where(x > 0, x, np.nan))


Z_SOURCES: dict[str, ZSource] = {
    "ret_1h_z": ZSource("ret_1h_z", "1h return", lambda ff: ff.cols["ret_1h"]),
    "vol_1h_z": ZSource(
        "vol_1h_z", "log 1h volume", lambda ff: _log_pos(rolling_sum(ff.cols["volume"], 12))
    ),
    "imbalance_1h_z": ZSource(
        "imbalance_1h_z", "1h taker imbalance", lambda ff: ff.cols["imbalance_1h"]
    ),
    "cvd_slope_1h_z": ZSource(
        "cvd_slope_1h_z", "1h CVD slope (BTC/5m)", lambda ff: ff.cols["cvd_slope_1h"]
    ),
    "oi_chg_1h_z": ZSource("oi_chg_1h_z", "1h OI change", lambda ff: ff.cols["oi_chg_1h"]),
    "prem_z": ZSource("prem_z", "1h mean premium (mark/index-1)", lambda ff: ff.cols["prem_1h"]),
    "fund_z": ZSource("fund_z", "funding rate", lambda ff: ff.cols["fund"], "funding"),
}


FUNDING_EVENTS_SEEN = {"n": 0}


def z_params(cfg: V5Config, ff: FeatureFrame, z_col: str, k: int) -> dict[str, Any]:
    """Mean/std of the PREVIOUS window (exactly as features.rolling_z) and warm-up count."""
    src = Z_SOURCES[z_col]
    if src.window == "funding":
        mp_f = max(10, cfg.features.funding_z_window // 2)
        n_f = int(FUNDING_EVENTS_SEEN["n"])
        return {
            "observations": n_f,
            "min_periods": mp_f,
            "warm": not math.isnan(ff.v(z_col, k)),
            "mean": None,
            "std": None,
            "eta_hours_if_collecting": max(0, mp_f + 1 - n_f) * 8,
            "note": "funding z uses 8-hourly funding observations (window 90, min 45); "
            "forward funding events exist only since the collector started",
        }
    win, mp = cfg.features.z_window_bars, cfg.features.z_min_periods
    x = np.asarray(src.raw(ff), dtype=np.float64)
    w = x[max(0, k - win) : k]
    w = w[~np.isnan(w)]
    n = len(w)
    out: dict[str, Any] = {
        "observations": n,
        "min_periods": mp,
        "warm": n >= mp,
        "mean": None,
        "std": None,
        "raw_now": _num(x[k]) if 0 <= k < len(x) else None,
    }
    if n >= mp:
        out["mean"], out["std"] = float(w.mean()), float(w.std(ddof=1))
    return out


# ----------------------------------------------------------------------------- data quality
def data_quality(
    cfg: V5Config, ff: FeatureFrame, rows: Any, k: int, validity: Any = None
) -> dict[str, Any]:
    """How flagged gap rows (collector outages carried forward as zero-trade rows, and the
    seed->forward gap fill) enter each frozen 30-day z window. INFORMATIONAL: the live-only z is
    NOT a V5 calculation and is never used for any decision."""
    win = cfg.features.z_window_bars
    gmap = (
        dict(
            zip(
                rows["close_time_ms"].to_list(),
                rows["gap_filled"].fill_null(0.0).to_list(),
                strict=True,
            )
        )
        if "gap_filled" in rows.columns
        else {}
    )
    g = np.array([float(gmap.get(int(t), 0.0) or 0.0) for t in ff.close_ms]) > 0
    tw = rolling_sum(g.astype(np.float64), 12)
    touched = np.where(np.isnan(tw), False, tw > 0).astype(bool)  # gap in the row's 1h window
    lo = max(0, k - win)
    last_gap = int(np.flatnonzero(g[: k + 1]).max()) if g[: k + 1].any() else None
    out: dict[str, Any] = {
        "gap_rows_in_30d_window": int(g[lo:k].sum()),
        "rows_with_gap_in_their_1h_window": int(touched[lo:k].sum()),
        "current_row_1h_window_contains_gap": bool(touched[k]) if k >= 0 else False,
        "gap_rows_in_atr_window_14h": int(g[max(0, k - 14 * 12 + 1) : k + 1].sum()),
        "last_gap_row": _iso(int(ff.close_ms[last_gap])) if last_gap is not None else None,
        "gap_rows_leave_the_window_at": _iso(int(ff.close_ms[last_gap]) + win * MS_5M)
        if last_gap is not None
        else None,
    }
    for col, src in Z_SOURCES.items():
        if src.window != "z":
            continue
        x = np.asarray(src.raw(ff), dtype=np.float64).copy()
        if validity is not None:  # V5.1: invalid observations are not observations at all
            x[~validity.valid_for(col)] = np.nan
        w_all = x[lo:k]
        keep = ~np.isnan(w_all)
        clean = keep & ~touched[lo:k]
        w, wc = w_all[keep], w_all[clean]
        d: dict[str, Any] = {
            "window_obs": int(keep.sum()),
            "window_obs_from_gap_rows": int((keep & touched[lo:k]).sum()),
        }
        if len(w) >= 2:
            d["window_mean"], d["window_std"] = float(w.mean()), float(w.std(ddof=1))
        if len(wc) >= 2:
            d["live_rows_mean"], d["live_rows_std"] = float(wc.mean()), float(wc.std(ddof=1))
            xv = x[k]
            if not math.isnan(xv) and d["live_rows_std"] > 0:
                d["live_rows_z_informational"] = (xv - d["live_rows_mean"]) / d["live_rows_std"]
        if "window_std" in d and d.get("live_rows_std"):
            ratio = d["window_std"] / d["live_rows_std"]
            d["std_inflation_vs_live_rows"] = ratio
            d["distorted"] = bool(ratio > 1.5 or ratio < 1 / 1.5)
        if col == "vol_1h_z" and len(w) and "window_std" in d:
            d["window_min_log_volume"] = float(w.min())
            d["rows_with_1h_volume_below_1_btc"] = int((w < 0).sum())
            need_b = d["window_mean"] + cfg.events.absorption_reversal.vol_1h_z * d["window_std"]
            need_a = (
                d["window_mean"] + cfg.events.liquidation_continuation.vol_1h_z * d["window_std"]
            )
            d["btc_per_hour_needed_for_vol_1h_z_1.0"] = float(np.exp(need_b))
            d["btc_per_hour_needed_for_vol_1h_z_1.5"] = float(np.exp(need_a))
            # > 100,000 BTC in one hour has never traded on Bybit BTCUSDT: unreachable in practice
            d["unreachable"] = bool(np.exp(need_b) > 100_000.0)
        out[col] = d
    return out


# ----------------------------------------------------------------------------- conditions
@dataclass(frozen=True)
class Cond:
    name: str
    col: str  # feature column (z-score or flag)
    op: str  # ">=" or "<="
    thr: float
    signed: bool  # value is multiplied by the side sign s (as in events.py)
    neg: bool = False  # value is -s * col (family D cvd term)
    nan_passes: bool = False  # crowding filter: a NaN z is "not crowded"


def family_conditions(cfg: V5Config, fam: V5Family, side: Side) -> list[Cond]:
    """Mirror of btc_swing/v5/events.py (frozen v5-event-1). LONG: s=+1, SHORT: s=-1."""
    e = cfg.events
    if fam is V5Family.LIQUIDATION_CONTINUATION:
        p = e.liquidation_continuation
        return [
            Cond("impulse: s*ret_1h_z >= thr", "ret_1h_z", ">=", p.impulse_ret_1h_z, True),
            Cond("OI flush: oi_chg_1h_z <= thr", "oi_chg_1h_z", "<=", p.oi_chg_1h_z, False),
            Cond("volume: vol_1h_z >= thr", "vol_1h_z", ">=", p.vol_1h_z, False),
            Cond(
                "aggression: s*imbalance_1h_z >= thr",
                "imbalance_1h_z",
                ">=",
                p.imbalance_1h_z,
                True,
            ),
        ]
    if fam is V5Family.ABSORPTION_REVERSAL:
        p2 = e.absorption_reversal
        return [
            Cond(
                "opposite aggression: s*imbalance_1h_z <= thr",
                "imbalance_1h_z",
                "<=",
                p2.imbalance_1h_z,
                True,
            ),
            Cond("volume: vol_1h_z >= thr", "vol_1h_z", ">=", p2.vol_1h_z, False),
            Cond(
                "impact absorbed: s*ret_1h_z >= thr",
                "ret_1h_z",
                ">=",
                p2.max_adverse_ret_1h_z,
                True,
            ),
        ]
    if fam is V5Family.FLOW_OI_CONTINUATION:
        p3 = e.flow_oi_continuation
        return [
            Cond("trend: s*ret_1h_z >= thr", "ret_1h_z", ">=", p3.ret_1h_z, True),
            Cond(
                "aggression: s*imbalance_1h_z >= thr",
                "imbalance_1h_z",
                ">=",
                p3.imbalance_1h_z,
                True,
            ),
            Cond("CVD: s*cvd_slope_1h_z >= thr", "cvd_slope_1h_z", ">=", p3.cvd_slope_1h_z, True),
            Cond("new positioning: oi_chg_1h_z >= thr", "oi_chg_1h_z", ">=", p3.oi_chg_1h_z, False),
            Cond(
                "not crowded (funding): s*fund_z <= thr (NaN passes)",
                "fund_z",
                "<=",
                p3.max_crowding_z,
                True,
                nan_passes=True,
            ),
            Cond(
                "not crowded (premium): s*prem_z <= thr (NaN passes)",
                "prem_z",
                "<=",
                p3.max_crowding_z,
                True,
                nan_passes=True,
            ),
        ]
    p4 = e.flow_divergence_reversal
    brk = "break_dn" if side is Side.LONG else "break_up"
    return [
        Cond(
            f"new 24h {'low' if side is Side.LONG else 'high'}: {brk} > 0",
            brk,
            ">=",
            1.0,
            False,
        ),
        Cond(
            "flow not confirming: -s*cvd_slope_1h_z <= thr",
            "cvd_slope_1h_z",
            "<=",
            p4.max_cvd_slope_z,
            True,
            neg=True,
        ),
        Cond("no new positioning: oi_chg_1h_z <= thr", "oi_chg_1h_z", "<=", p4.max_oi_chg_z, False),
    ]


def _eval(
    cfg: V5Config, ff: FeatureFrame, c: Cond, side: Side, k: int, fam: V5Family
) -> dict[str, Any]:
    s = float(side.sign)
    raw = ff.v(c.col, k)
    mult = (-s if c.neg else s) if c.signed else 1.0
    val = mult * raw if not math.isnan(raw) else math.nan
    nan = math.isnan(val)
    if nan:
        ok = c.nan_passes
        dist = None
    else:
        ok = val >= c.thr if c.op == ">=" else val <= c.thr
        dist = 0.0 if ok else (c.thr - val if c.op == ">=" else val - c.thr)
    row: dict[str, Any] = {
        "condition": c.name,
        "column": c.col,
        "value": _num(val),
        "column_value": _num(raw),
        "threshold": f"{c.op} {c.thr:g}",
        "pass": bool(ok),
        "distance": _num(dist),
        "distance_unit": "z" if c.col.endswith("_z") else "flag",
        "warm": None,
    }
    if c.col in Z_SOURCES:
        zp = z_params(cfg, ff, c.col, k)
        row["warm"] = zp["warm"]
        row["warm_up"] = zp
        if nan and not zp["warm"] and Z_SOURCES[c.col].window == "z":
            short = zp["min_periods"] - zp["observations"]
            row["warm_up"]["bars_missing"] = short
            row["warm_up"]["eta_hours_if_collecting"] = round(short * 5 / 60, 1)
        if not ok and not nan and zp.get("std"):
            # the raw value the frozen z threshold corresponds to right now
            need_z = c.thr / mult if mult != 0 else c.thr
            row["raw_needed"] = zp["mean"] + need_z * zp["std"]
            row["raw_now"] = zp.get("raw_now")
    elif c.col in ("break_up", "break_dn"):
        row["warm"] = not math.isnan(
            ff.v("hi_break_ref" if c.col == "break_up" else "lo_break_ref", k)
        )
        if c.col == "break_up":
            ref, ext = ff.v("hi_break_ref", k), ff.v("high", k)
            row["price_distance"] = _num(ref - ext)  # high must exceed the prior 24h high
            row["reference"] = {"prior_24h_high": _num(ref), "bar_high": _num(ext)}
        else:
            ref, ext = ff.v("lo_break_ref", k), ff.v("low", k)
            row["price_distance"] = _num(ext - ref)  # low must undercut the prior 24h low
            row["reference"] = {"prior_24h_low": _num(ref), "bar_low": _num(ext)}
        atr = ff.v("atr", k)
        if not ok and row["price_distance"] is not None and atr > 0:
            row["distance"] = row["price_distance"] / atr
            row["distance_unit"] = "ATR(1h)"
    return row


# ----------------------------------------------------------------------------- engine state
def _family_states(
    ctx: ForwardContextLike, eng: V5Engine, b: Any
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    res = eng.run(ctx.start_ms, b.last_close_ms + 1, notes={"stream": "signal_diagnostic"})
    cfg = ctx.cfg
    n_last = len(eng.series.base) - 1
    states: dict[tuple[str, str], dict[str, Any]] = {}
    eps = res.episodes.to_dicts() if res.episodes.height else []
    for fam in V5Family:
        for side in (Side.LONG, Side.SHORT):
            mine = [e for e in eps if e["family"] == fam.value and e["side"] == side.value]
            active = [e for e in mine if e.get("end_reason") == "END_OF_DATA"]
            done = [e for e in mine if e.get("end_reason") != "END_OF_DATA"]
            st: dict[str, Any] = {"episodes_since_start": len(mine)}
            if active:
                a = active[-1]
                st["state"] = (
                    "CONFIRMED (waiting for a 5m close inside the entry zone)"
                    if a.get("confirmed")
                    else "WATCH (waiting for the 15m confirmation)"
                )
                st["active_episode"] = {
                    "event_at": _iso(a.get("event_ms")),
                    "entry_zone": [a.get("entry_zone_low"), a.get("entry_zone_high")],
                    "structural_stop": a.get("stop_price"),
                }
            else:
                st["state"] = "IDLE (no open setup)"
            if done:
                last = done[-1]
                cb = int(last["closed_bar"] or 0)
                cd = (
                    cfg.episode.cooldown_bars_after_close
                    if last.get("trade_id") is not None
                    else cfg.episode.cooldown_bars_after_invalidation
                )
                st["last_episode"] = {
                    "end_reason": last.get("end_reason"),
                    "outcome": last.get("outcome_class"),
                }
                st["cooldown_bars_left"] = max(0, cb + cd - n_last)
            else:
                st["cooldown_bars_left"] = 0
            states[(fam.value, side.value)] = st
    open_trades = (
        res.trades.filter(res.trades["exit_reason"] == "END_OF_DATA").height
        if res.trades.height and "exit_reason" in res.trades.columns
        else 0
    )
    summary = {
        "paper_trades_since_start": res.trades.height,
        "paper_position_open": open_trades > 0,
        "blocked_counts": res.blocked,
        "episodes_since_start": len(eps),
    }
    return states, summary


# ----------------------------------------------------------------------------- main
def signal_diagnostic(
    ctx: ForwardContextLike,
    now_ms: int | None = None,
    builder: Callable[[Any], tuple[Any, Any, Any]] | None = None,
    feature_table: Callable[[Any, FeatureFrame, Any, int], list[dict[str, Any]]] | None = None,
    label: str = "V5 (frozen)",
) -> dict[str, Any]:
    """Read-only. Uses the latest completed 5m bar already built by the runner."""
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    cfg = ctx.cfg
    validity: Any = None
    if builder is None:
        a = assemble(ctx)  # type: ignore[arg-type]
        b = build(ctx, a)  # type: ignore[arg-type]
    else:
        a, b, validity = builder(ctx)
    ff = b.ff
    k = ff.idx_at(b.last_close_ms)
    rows = a.rows.sort("open_time_ms")
    last = rows.tail(1).to_dicts()[0] if rows.height else {}
    gap_col = rows["gap_filled"].fill_null(0.0) if "gap_filled" in rows.columns else None
    gaps_12 = int((gap_col.tail(12) > 0).sum()) if gap_col is not None else 0
    gaps_48 = int((gap_col.tail(48) > 0).sum()) if gap_col is not None else 0
    FUNDING_EVENTS_SEEN["n"] = int(
        (np.asarray(b.aux.funding_t, dtype=np.int64) <= b.last_close_ms).sum()
    )
    quality = data_quality(cfg, ff, rows, k, validity)
    ftab = feature_table(cfg, ff, validity, k) if feature_table is not None else None
    eng = V5Engine(cfg, a.bars, a.funding, None, b.aux, b.series, ff)
    states, engine_summary = _family_states(ctx, eng, b)
    v = ff.v
    s12 = rows.tail(12)
    liq = {
        c: _num(s12[c].fill_nan(None).sum())
        for c in ("liq_n_long", "liq_n_short", "liq_long_notional", "liq_short_notional")
        if c in s12.columns
    }
    view = b.series.view_at(b.last_close_ms)
    market = {
        "bar_close_time": _iso(b.last_close_ms),
        "bar_valid_live": bool(last.get("trades", 0) or 0) and not bool(last.get("gap_filled")),
        "price": {
            "close": _num(v("close", k)),
            "high": _num(v("high", k)),
            "low": _num(v("low", k)),
        },
        "context": {
            "ret_5m": _num(v("ret_5m", k)),
            "ret_15m": _num(v("ret_15m", k)),
            "ret_1h": _num(v("ret_1h", k)),
            "ret_1h_z": _num(v("ret_1h_z", k)),
            "ret_4h": _num(v("ret_4h", k)),
            "last_15m_closes (confirmation input)": [
                _num(view.close(Timeframe.M15, i)) for i in range(2)
            ],
            "close_1h": _num(v("close_1h", k)),
            "ema21_1h": _num(v("ema21_1h", k)),
            "align_1h": _num(v("align_1h", k)),
            "trend_4h (EMA21/50 on 4h)": _num(v("trend_4h", k)),
            "regime": ff.regime[k].value if k >= 0 else None,
            "rv_24h_z": _num(v("rv_24h_z", k)),
        },
        "volatility": {"atr_1h": _num(v("atr", k)), "rv_1h_annualised": _num(v("rv_1h", k))},
        "volume": {
            "volume_5m_btc": _num(v("volume", k)),
            "vol_5m_z": _num(v("vol_5m_z", k)),
            "volume_1h_btc": _num(rolling_sum(ff.cols["volume"], 12)[k]),
            "vol_1h_z (relative 1h volume)": _num(v("vol_1h_z", k)),
        },
        "aggressor_flow": {
            "source": "bybit_ws publicTrade" if v("flow_source", k) == 1.0 else "kline fallback",
            "buy_qty_5m": _num(v("buy_qty", k)),
            "sell_qty_5m": _num(v("sell_qty", k)),
            "delta_1h_btc": _num(v("delta_1h", k)),
            "imbalance_1h": _num(v("imbalance_1h", k)),
            "imbalance_1h_z": _num(v("imbalance_1h_z", k)),
            "cvd_slope_1h": _num(v("cvd_slope_1h", k)),
            "cvd_slope_1h_z": _num(v("cvd_slope_1h_z", k)),
        },
        "open_interest": {
            "oi_btc": _num(v("oi", k)),
            "oi_chg_1h": _num(v("oi_chg_1h", k)),
            "oi_chg_1h_z": _num(v("oi_chg_1h_z", k)),
        },
        "funding": {"funding_rate": _num(v("fund", k)), "fund_z": _num(v("fund_z", k))},
        "premium_basis": {
            "prem_1h (mark/index-1)": _num(v("prem_1h", k)),
            "prem_z": _num(v("prem_z", k)),
            "basis (last/index-1)": _num(v("basis", k)),
            "basis_z (not a detector input)": _num(v("basis_z", k)),
        },
        "breakout_refs (family D)": {
            "prior_24h_high": _num(v("hi_break_ref", k)),
            "prior_24h_low": _num(v("lo_break_ref", k)),
        },
        "not_used_by_any_frozen_detector": {
            "liquidations_last_1h (collected; family A is an OI-flush proxy, not liquidation-based)": liq,
            "book_imb_1": _num(v("book_imb_1", k)),
            "depth_total_z": _num(v("depth_total_z", k)),
        },
    }
    # ---- blockers (read-only)
    demo = _demo_blockers(ctx, b, rows, now, gaps_12)
    det_by = {(d.v5_family, d.side): d for d in b.detectors}
    fams: list[dict[str, Any]] = []
    for fam in cfg.episode.family_priority:
        for side in (Side.LONG, Side.SHORT):
            conds = [_eval(cfg, ff, c, side, k, fam) for c in family_conditions(cfg, fam, side)]
            base_ok = (
                not math.isnan(v("atr", k)) and v("atr", k) > 0 and not math.isnan(v("close", k))
            )
            mine = all(c["pass"] for c in conds) and base_ok
            det = det_by[(fam, side)]
            frozen = bool(det.mask[k]) if 0 <= k < len(det.mask) else False
            recent = np.flatnonzero(
                det.mask[max(0, k - cfg.execution.confirm_window_bars + 1) : k + 1]
            )
            not_warm = [c["column"] for c in conds if c["warm"] is False and not c["pass"]]
            warming_nonblocking = [c["column"] for c in conds if c["warm"] is False and c["pass"]]
            failed = [c for c in conds if not c["pass"]]
            st = states[(fam.value, side.value)]
            entry = _entry_status(st, frozen, len(recent))
            blockers: list[str] = []
            if st["cooldown_bars_left"]:
                blockers.append(f"COOLDOWN ({st['cooldown_bars_left']} bars)")
            if gaps_12:
                blockers.append("DATA_GAP_IN_FEATURE_WINDOW (demo)")
            blockers += demo["global_blockers"]
            distorted = [
                c["column"]
                for c in conds
                if not c["pass"] and quality.get(c["column"], {}).get("unreachable")
            ]
            if not_warm:
                reason = (
                    "NOT WARM: " + ", ".join(sorted(set(not_warm))) + " (z-score lacks history)"
                )
                if distorted:
                    reason += (
                        "; and UNREACHABLE: " + ", ".join(distorted) + " (gap-distorted z window)"
                    )
            elif distorted:
                reason = (
                    "UNREACHABLE: "
                    + ", ".join(distorted)
                    + " (the frozen 30-day z window is distorted by carried-forward outage rows)"
                )
            elif not frozen:
                reason = "Stage A event conditions not met: " + "; ".join(
                    c["condition"] for c in failed
                )
            elif "CONFIRMED" not in st["state"]:
                reason = "event present; waiting for the 15m confirmation"
            else:
                reason = "confirmed; waiting for a 5m close inside the entry zone"
            closest = _closest(failed)
            invalid_now = (
                sorted(
                    {
                        c["column"]
                        for c in conds
                        if validity is not None
                        and c["column"] in validity.obs_cols()
                        and not validity.valid_for(c["column"])[k]
                    }
                )
                if validity is not None
                else []
            )
            dq_label = (
                "UNREACHABLE: " + ",".join(distorted)
                if distorted
                else (
                    "INVALID NOW: " + ",".join(invalid_now)
                    if invalid_now
                    else (
                        "baseline includes gap rows: "
                        + ",".join(
                            sorted(
                                {
                                    c["column"]
                                    for c in conds
                                    if quality.get(c["column"], {}).get("window_obs_from_gap_rows")
                                }
                            )
                        )
                        if any(
                            quality.get(c["column"], {}).get("window_obs_from_gap_rows")
                            for c in conds
                        )
                        else "clean"
                    )
                )
            )
            fams.append(
                {
                    "family": fam.value,
                    "side": side.value,
                    "state": st["state"],
                    "stage_a_event_now": frozen,
                    "events_in_thesis_window_24_bars": len(recent),
                    "stage_b_entry": entry,
                    "conditions": conds,
                    "logic_check": "matches frozen detector mask"
                    if mine == frozen
                    else "LOGIC_MISMATCH",
                    "warm": not not_warm,
                    "not_warm_inputs": sorted(set(not_warm)),
                    "warming_but_not_blocking (NaN passes)": sorted(set(warming_nonblocking)),
                    "unreachable_inputs": sorted(set(distorted)),
                    "blocked_by_gap": gaps_12 > 0,
                    "blockers": blockers,
                    "cooldown_bars_left": st["cooldown_bars_left"],
                    "signal_now": frozen and not blockers and "CONFIRMED" in st["state"],
                    "reason_no_signal": reason,
                    "closest_failed": closest,
                    "data_quality": dq_label,
                    "invalid_now_inputs": invalid_now,
                    "n_failed": len(failed),
                    "rank_key": _rank_key(not_warm + distorted, failed),
                }
            )
    ranked = sorted(fams, key=lambda f: f["rank_key"])
    return {
        "generated_at": _iso(now),
        "read_only": True,
        "frozen_v5_config_hash": cfg.config_hash,
        "observation_start": _iso(ctx.start_ms),
        "latest_bar": market["bar_close_time"],
        "latest_bar_valid_live": market["bar_valid_live"],
        "gap_rows_last_12_bars": gaps_12,
        "gap_rows_last_48_bars": gaps_48,
        "variant": label,
        "feature_table": ftab,
        "market": market,
        "data_quality": quality,
        "families": fams,
        "ranking": [f"{f['family']} {f['side']}" for f in ranked],
        "engine_replay": engine_summary,
        "demo": demo,
        "signals_since_start": _count_lines(ctx.paths.signals_file),
    }


def _entry_status(st: dict[str, Any], event_now: bool, recent: int) -> str:
    if "CONFIRMED" in st["state"]:
        return "CONFIRMED: needs a 5m close inside the entry zone within 6 bars"
    if "WATCH" in st["state"]:
        return (
            "WATCH: needs the first 15m close beyond the previous 15m close in the trade direction"
        )
    if event_now or recent:
        return "event seen but no episode (cooldown/de-dup/slot)"
    return "n/a (no Stage A event: Stage B never starts)"


def _closest(failed: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not failed:
        return None
    warm = [c for c in failed if c["distance"] is not None]
    pick = min(warm, key=lambda c: c["distance"]) if warm else failed[0]
    return {
        "condition": pick["condition"],
        "current": pick["value"],
        "threshold": pick["threshold"],
        "distance": pick["distance"],
        "unit": pick["distance_unit"],
        "raw_needed": pick.get("raw_needed"),
        "raw_now": pick.get("raw_now"),
        "price_distance": pick.get("price_distance"),
    }


def _rank_key(not_warm: list[str], failed: list[dict[str, Any]]) -> tuple[int, int, float]:
    """Cannot fire (not warm, or an input is unreachable) -> last; then fewest failed conditions;
    then total shortfall in z / ATR units."""
    total = sum(c["distance"] or 0.0 for c in failed)
    return (1 if not_warm else 0, len(failed), total)


def _count_lines(p: Path) -> int:
    if not p.exists():
        return 0
    return sum(1 for ln in p.read_text().splitlines() if ln.strip())


def _demo_blockers(
    ctx: ForwardContextLike, b: Any, rows: Any, now: int, gaps_12: int
) -> dict[str, Any]:
    """STRATEGY_DEMO blockers that apply to ANY trigger right now (read-only, no API call)."""
    from btc_swing.risk.sizing import size_position
    from btc_swing.v5.demo.activation import activation_state, effective_activation
    from btc_swing.v5.demo.config import DEFAULT_DEMO_CONFIG_PATH, load_demo_config
    from btc_swing.v5.demo.runtime import _raw_age_s

    out: dict[str, Any] = {"global_blockers": []}
    st_path = ctx.paths.root / "demo" / "strategy_state.json"
    st = json.loads(st_path.read_text()) if st_path.exists() else {}
    act = effective_activation(ctx.paths.root)
    ast = activation_state(ctx.paths.root)
    out["activation"] = {
        "status": ast["status"],
        "activated_at": (ast["last"] or {}).get("activated_at"),
        "effective_on_this_host": act is not None,
    }
    out["execution_mode"] = "STRATEGY_DEMO" if act is not None else "DISABLED"
    out["open_strategy_position"] = (st.get("position") or {}).get("status")
    out["reconcile_required"] = bool(st.get("reconcile_required"))
    if act is None:
        out["global_blockers"].append("STRATEGY_DEMO not active on this host")
    if st.get("position") is not None:
        out["global_blockers"].append("POSITION_OPEN (one position maximum)")
    if st.get("reconcile_required"):
        out["global_blockers"].append("RECONCILIATION_REQUIRED")
    if not DEFAULT_DEMO_CONFIG_PATH.exists():
        return out
    dcfg = load_demo_config(DEFAULT_DEMO_CONFIG_PATH)
    fs = dcfg.failsafe
    age = _raw_age_s(ctx.paths.raw)
    if age is None or age > fs.collector_stale_s:
        out["global_blockers"].append(
            f"COLLECTOR_STALE (raw age {age if age is None else round(age)} s)"
        )
    if now - b.last_close_ms > fs.bar_stale_s * 1000:
        out["global_blockers"].append("RUNNER_STALE (last bar too old)")
    out["gap_rows_in_last_12_bars"] = gaps_12
    # risk feasibility of a trigger NOW at the volatility-floor stop (1.25 ATR) on 5000 USDT
    ff, k = b.ff, b.ff.idx_at(b.last_close_ms)
    close, atr = ff.v("close", k), ff.v("atr", k)
    if close > 0 and atr > 0:
        view = b.series.view_at(b.last_close_ms)
        tf = ctx.cfg.risk.liquidation_atr_tf
        atr_liq = view.ind(tf, "atr") if view.warm(tf) else math.nan
        stop = close - ctx.cfg.execution.vol_floor_atr * atr
        sz = size_position(
            dcfg.reference_equity_usdt, close, stop, Side.LONG, atr_liq, ctx.cfg.risk
        )
        out["risk_check_at_vol_floor_stop"] = {
            "reference_equity_usdt": dcfg.reference_equity_usdt,
            "risk_per_trade": dcfg.risk_per_trade,
            "stop_distance_usd": close - stop,
            "risk_usdt": sz.risk_amount,
            "qty_btc": sz.qty,
            "leverage": sz.leverage,
            "accepted_by_frozen_sizing": sz.accepted,
            "reason": sz.reason,
            "meets_bybit_min_qty_0.001": sz.qty >= 0.001 - 1e-12,
        }
    return out


# ----------------------------------------------------------------------------- render
def _f(x: Any, nd: int = 3) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def render_diagnostic(d: dict[str, Any]) -> str:
    m = d["market"]
    out: list[str] = [
        f"BTC {d.get('variant', 'V5')} SIGNAL DIAGNOSTIC (read-only: nothing written, no order, no API call)",
        f"generated {d['generated_at']} | latest completed 5m bar closes {d['latest_bar']} "
        f"(live trades: {d['latest_bar_valid_live']}) | frozen V5 {d['frozen_v5_config_hash'][:12]} "
        f"| observation start {d['observation_start']}",
        f"signals since start: {d['signals_since_start']} | gap rows in last 12 / 48 bars: "
        f"{d['gap_rows_last_12_bars']} / {d['gap_rows_last_48_bars']} | execution mode "
        f"{d['demo']['execution_mode']} (activation {d['demo']['activation']['status']} "
        f"{d['demo']['activation'].get('activated_at') or ''})",
        "",
        "MARKET INPUTS USED BY FROZEN V5",
    ]
    for sect in (
        "price",
        "context",
        "volatility",
        "volume",
        "aggressor_flow",
        "open_interest",
        "funding",
        "premium_basis",
        "breakout_refs (family D)",
        "not_used_by_any_frozen_detector",
    ):
        vals = m[sect]
        out.append(
            f"  {sect}: "
            + ", ".join(f"{k}={_f(v, 6) if isinstance(v, float) else v}" for k, v in vals.items())
        )
    q = d["data_quality"]
    out += ["", "DATA QUALITY OF THE FROZEN Z WINDOWS (informational; never used for decisions)"]
    out.append(
        f"  gap rows in the 30-day window: {q['gap_rows_in_30d_window']}; rows whose 1h window "
        f"touches a gap: {q['rows_with_gap_in_their_1h_window']}; current 1h window touches a gap: "
        f"{q['current_row_1h_window_contains_gap']}; gap rows in the 14h ATR(1h) window: "
        f"{q['gap_rows_in_atr_window_14h']}; last gap row {q['last_gap_row']} -> leaves "
        f"the window {q['gap_rows_leave_the_window_at']}"
    )
    for col in (
        "ret_1h_z",
        "vol_1h_z",
        "imbalance_1h_z",
        "cvd_slope_1h_z",
        "oi_chg_1h_z",
        "prem_z",
    ):
        qq = q.get(col) or {}
        out.append(
            f"  {col}: obs {qq.get('window_obs')} (from gap-touched rows "
            f"{qq.get('window_obs_from_gap_rows')}); window std {_f(qq.get('window_std'), 6)} vs "
            f"live-rows std {_f(qq.get('live_rows_std'), 6)} (x{_f(qq.get('std_inflation_vs_live_rows'), 2)}); "
            f"live-rows z {_f(qq.get('live_rows_z_informational'), 2)}"
            + (" DISTORTED" if qq.get("distorted") else "")
        )
    vq = q.get("vol_1h_z") or {}
    if vq.get("btc_per_hour_needed_for_vol_1h_z_1.0") is not None:
        out.append(
            f"  vol_1h_z: min log-volume in window {_f(vq.get('window_min_log_volume'), 1)} "
            f"({vq.get('rows_with_1h_volume_below_1_btc')} rows with < 1 BTC/h = outage rows); "
            f"1h volume needed for z>=1.0: {vq['btc_per_hour_needed_for_vol_1h_z_1.0']:,.0f} BTC, "
            f"for z>=1.5: {vq['btc_per_hour_needed_for_vol_1h_z_1.5']:,.0f} BTC"
            + (" -> UNREACHABLE" if vq.get("unreachable") else "")
        )
    if d.get("feature_table"):
        out += ["", "FEATURE | VALID OBS | WARM | CURRENT | Z | QUALITY"]
        for r in d["feature_table"]:
            out.append(
                f"{r['feature']} | {r['valid_obs']}/{r['min_periods']} | {'yes' if r['warm'] else 'NO'} | "
                f"{_f(r['current'], 6)} | {_f(r['z'])} | {r['quality']}"
            )
    out += [
        "",
        f"FAMILIES (frozen v5-event-1 conditions at the latest bar; variant {d['variant']})",
    ]
    for f in d["families"]:
        out.append(
            f"- {f['family']} {f['side']}: state {f['state']}; Stage A event now {f['stage_a_event_now']}; "
            f"events in last 24 bars {f['events_in_thesis_window_24_bars']}; Stage B {f['stage_b_entry']}; "
            f"[{f['logic_check']}]"
        )
        for c in f["conditions"]:
            w = c.get("warm_up") or {}
            warm = (
                "warm"
                if c["warm"]
                else (
                    f"NOT WARM ({w.get('observations')}/{w.get('min_periods')} obs, "
                    f"~{w.get('eta_hours_if_collecting', '?')} h of collection left)"
                    if c["warm"] is False
                    else "-"
                )
            )
            extra = ""
            if c.get("raw_needed") is not None:
                extra = f" | raw now {_f(c.get('raw_now'), 6)} needs {_f(c['raw_needed'], 6)}"
            if c.get("reference"):
                extra = f" | {c['reference']}"
            out.append(
                f"    {'PASS' if c['pass'] else 'FAIL'} {c['condition']}: value {_f(c['value'])} "
                f"threshold {c['threshold']} distance {_f(c['distance'])} {c['distance_unit']} [{warm}]{extra}"
            )
        out.append(
            f"    blockers: {', '.join(f['blockers']) or 'none'} | cooldown {f['cooldown_bars_left']} bars | "
            f"signal now {f['signal_now']} | WHY: {f['reason_no_signal']}"
        )
    out += [
        "",
        "FAMILY | SIDE | EVENT | ENTRY | CLOSEST FAILED CONDITION | CURRENT | THRESHOLD | DISTANCE | WARM | DATA QUALITY | BLOCKED BY GAP | SIGNAL NOW",
    ]
    for f in d["families"]:
        c = f["closest_failed"] or {}
        out.append(
            " | ".join(
                [
                    f["family"],
                    f["side"],
                    "YES" if f["stage_a_event_now"] else "no",
                    "n/a" if not f["stage_a_event_now"] else f["state"].split(" ")[0],
                    c.get("condition", "-"),
                    _f(c.get("current")),
                    c.get("threshold", "-"),
                    f"{_f(c.get('distance'))} {c.get('unit', '')}".strip(),
                    ("yes" if f["warm"] else "NO: " + ",".join(f["not_warm_inputs"]))
                    + (
                        "; UNREACHABLE: " + ",".join(f["unreachable_inputs"])
                        if f["unreachable_inputs"]
                        else ""
                    ),
                    f["data_quality"],
                    "yes" if f["blocked_by_gap"] else "no",
                    "YES" if f["signal_now"] else "no",
                ]
            )
        )
    out += ["", "CLOSEST TO TRIGGER: " + " > ".join(d["ranking"])]
    rc = d["demo"].get("risk_check_at_vol_floor_stop")
    if rc:
        out.append(
            f"risk check (trigger now, 1.25 ATR stop, {rc['reference_equity_usdt']:.0f} USDT, "
            f"{rc['risk_per_trade']:.2%}): qty {rc['qty_btc']:.4f} BTC, leverage {rc['leverage']}, "
            f"accepted {rc['accepted_by_frozen_sizing']}, >= Bybit min qty {rc['meets_bybit_min_qty_0.001']}"
        )
    out.append(
        f"global demo blockers: {', '.join(d['demo']['global_blockers']) or 'none'} | engine replay since start: "
        f"{d['engine_replay']}"
    )
    return "\n".join(out)
