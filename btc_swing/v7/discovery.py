"""V7 Phase 1: microstructure edge discovery with matched controls (design sections 4-6).

Events are 1m rows where a declared feature state holds; outcomes are forward returns, MFE, MAE and
"+X before -Y" probabilities over the following minutes; every event is compared with K controls
matched on hour, recent volatility, recent return, 1h regime and structure position. Thresholds and
neighbourhoods are evaluated on the DISCOVERY window; validation windows see only the anchors."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from btc_swing.core.hashing import stable_hash
from btc_swing.core.versions import code_version
from btc_swing.v7.config import V7Config
from btc_swing.v7.features import V7Frame

log = logging.getLogger(__name__)
DAY_MS = 86_400_000


def ms(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp() * 1000)


def iso(t: float) -> str:
    return datetime.fromtimestamp(t / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


# ----------------------------------------------------------------------------- event definitions
@dataclass(frozen=True)
class EventDef:
    name: str
    group: str  # single / combination
    param: str
    value: Any
    anchor: bool
    direction_rule: str  # text for the report


def _events_single(cfg: V7Config, fr: V7Frame) -> list[tuple[EventDef, np.ndarray, np.ndarray]]:
    """Returns (definition, mask, direction) with direction in {+1, -1} on event rows."""
    a = cfg.discovery.anchors
    c = fr.cols
    out: list[tuple[EventDef, np.ndarray, np.ndarray]] = []
    sgn_imb5 = np.sign(np.nan_to_num(c["imb_5"]))
    for v in a.intensity_burst_tc_z.grid:
        m = (c["tc_z"] >= v) & (sgn_imb5 != 0)
        out.append(
            (
                EventDef(
                    "INTENSITY_BURST",
                    "single",
                    "tc_z>=",
                    v,
                    v == a.intensity_burst_tc_z.anchor,
                    "sign of imb_5",
                ),
                m,
                sgn_imb5,
            )
        )
    for v in a.imbalance_abs.grid:
        m = np.abs(c["imb_5"]) >= v
        out.append(
            (
                EventDef(
                    "IMBALANCE",
                    "single",
                    "|imb_5|>=",
                    v,
                    v == a.imbalance_abs.anchor,
                    "sign of imb_5",
                ),
                m,
                sgn_imb5,
            )
        )
    for key in cfg.features.large_trade_keys:
        lt = c[f"lt_imb_{key}_5"]
        grid = (
            a.large_trade_imbalance_abs.grid
            if key == cfg.features.large_trade_anchor
            else [a.large_trade_imbalance_abs.anchor]
        )
        for v in grid:
            m = np.abs(lt) >= v
            out.append(
                (
                    EventDef(
                        f"LARGE_TRADE_IMBALANCE_{key}",
                        "single",
                        "|lt_imb_5|>=",
                        v,
                        v == a.large_trade_imbalance_abs.anchor
                        and key == cfg.features.large_trade_anchor,
                        "sign of large-trade imbalance",
                    ),
                    m,
                    np.sign(np.nan_to_num(lt)),
                )
            )
    da = c["delta_accel"]
    m = np.abs(da) >= c["delta_accel_p90"]
    out.append(
        (
            EventDef(
                "DELTA_ACCEL",
                "single",
                "|delta_accel| >= rolling p",
                a.delta_accel_pct.anchor,
                True,
                "sign of delta acceleration",
            ),
            m,
            np.sign(np.nan_to_num(da)),
        )
    )
    sgn_d5 = np.sign(np.nan_to_num(c["delta_5"]))
    out.append(
        (
            EventDef(
                "EFFICIENT_FLOW",
                "single",
                "strong flow & strong response",
                "efficient",
                True,
                "sign of delta_5 (continuation)",
            ),
            c["efficient"] == 1.0,
            sgn_d5,
        )
    )
    out.append(
        (
            EventDef(
                "ABSORPTION",
                "single",
                "strong flow & weak response",
                "absorption",
                True,
                "AGAINST delta_5 (reversal)",
            ),
            c["absorption"] == 1.0,
            -sgn_d5,
        )
    )
    brk = np.where(c["break_up"] == 1.0, 1.0, np.where(c["break_dn"] == 1.0, -1.0, 0.0))
    for v in a.compression_pct.grid:
        q = c[f"comp_q{int(v * 100)}"]
        comp_prev = lagged_f(c["range_30"], 1) <= lagged_f(
            q, 1
        )  # compressed BEFORE the breaking minute
        m = comp_prev & (brk != 0)
        out.append(
            (
                EventDef(
                    "COMPRESSION_BREAK",
                    "single",
                    "compression_pct<=",
                    v,
                    v == a.compression_pct.anchor,
                    "break direction",
                ),
                m,
                brk,
            )
        )
    fb = np.where(
        c["failed_break_up"] == 1.0, -1.0, np.where(c["failed_break_dn"] == 1.0, 1.0, 0.0)
    )
    out.append(
        (
            EventDef(
                "FAILED_BREAK", "single", "failed 60-min break", "failed", True, "against the break"
            ),
            fb != 0,
            fb,
        )
    )
    return out


def lagged_f(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    out[k:] = x[:-k]
    return out


def _events_engines(cfg: V7Config, fr: V7Frame) -> list[tuple[EventDef, np.ndarray, np.ndarray]]:
    a = cfg.discovery.anchors
    c = fr.cols
    out: list[tuple[EventDef, np.ndarray, np.ndarray]] = []
    # E1 TREND_PULLBACK_RELEASE: 15m trend, impulse, retrace in band, no close beyond origin, release
    lo_d, hi_d = a.pullback_depth.anchor
    tr = c["trend_15m"]
    idir = c["impulse_dir"]
    base = (
        (tr == idir)
        & (c["impulse_atr"] >= a.engine_impulse_atr)
        & (c["retrace"] >= lo_d)
        & (c["retrace"] <= hi_d)
    )
    release = (
        (c["tc_z"] >= a.engine_tc_z)
        & (idir * c["imb_5"] >= a.engine_imbalance)
        & (idir * c["impact_5"] * np.sign(c["delta_5"]) * idir > 0)
    )
    e1 = base & release & (np.sign(c["ret_5"]) == idir)
    out.append(
        (
            EventDef(
                "E1_TREND_PULLBACK_RELEASE",
                "combination",
                "trend+impulse+retrace+release",
                "anchor",
                True,
                "trend direction",
            ),
            e1,
            np.nan_to_num(idir),
        )
    )
    e1lt = e1 & (idir * c["lt_imb_5"] >= a.engine_large_trade)
    out.append(
        (
            EventDef(
                "E1_TREND_PULLBACK_RELEASE+LT",
                "combination",
                "E1 + large-trade confirmation",
                "anchor",
                True,
                "trend direction",
            ),
            e1lt,
            np.nan_to_num(idir),
        )
    )
    # E2 FAILED_AUCTION_ABSORPTION: sweep of the 60-min / prior-day extreme with flow, weak response, close back inside
    lvl_hi = np.fmax(lagged_f(c["range_hi_60"], 1), c["prev_day_hi"])
    lvl_lo = np.fmin(lagged_f(c["range_lo_60"], 1), c["prev_day_lo"])
    swept_hi = (c["high"] > lvl_hi) & (c["close"] < lvl_hi)
    swept_lo = (c["low"] < lvl_lo) & (c["close"] > lvl_lo)
    flow_up = c["imb_5"] >= a.engine_imbalance
    flow_dn = c["imb_5"] <= -a.engine_imbalance
    absorb = c["absorption"] == 1.0
    e2_short = swept_hi & flow_up & absorb
    e2_long = swept_lo & flow_dn & absorb
    e2 = e2_short | e2_long
    d2 = np.where(e2_short, -1.0, np.where(e2_long, 1.0, 0.0))
    out.append(
        (
            EventDef(
                "E2_FAILED_AUCTION_ABSORPTION",
                "combination",
                "sweep+flow+absorption+close inside",
                "anchor",
                True,
                "against the sweep",
            ),
            e2,
            d2,
        )
    )
    # same sweep WITHOUT absorption (control concept: strong flow + strong progress)
    e2x = (swept_hi & flow_up & (c["efficient"] == 1.0)) | (
        swept_lo & flow_dn & (c["efficient"] == 1.0)
    )
    d2x = np.where(swept_hi & flow_up, -1.0, np.where(swept_lo & flow_dn, 1.0, 0.0))
    out.append(
        (
            EventDef(
                "E2_SWEEP_WITH_EFFICIENT_FLOW",
                "combination",
                "sweep+flow+efficient response (contrast)",
                "anchor",
                True,
                "against the sweep",
            ),
            e2x,
            d2x,
        )
    )
    # E3 COMPRESSION_EXPANSION
    comp = lagged_f(c["range_30"], 1) <= lagged_f(
        c[f"comp_q{int(a.compression_pct.anchor * 100)}"], 1
    )
    brk = np.where(c["break_up"] == 1.0, 1.0, np.where(c["break_dn"] == 1.0, -1.0, 0.0))
    level = np.where(brk > 0, lagged_f(c["range_hi_60"], 1), lagged_f(c["range_lo_60"], 1))
    dist = np.abs(c["close"] - level) / c["atr5m"]
    for v in a.anti_chase_atr.grid:
        e3 = (
            comp
            & (brk != 0)
            & (c["tc_z"] >= a.engine_tc_z)
            & (c["vol_exp_5"] >= a.engine_volume_expansion)
            & (brk * c["imb_5"] >= a.engine_imbalance)
            & (c["efficient"] == 1.0)
            & (dist <= v)
        )
        out.append(
            (
                EventDef(
                    "E3_COMPRESSION_EXPANSION",
                    "combination",
                    "anti_chase_atr<=",
                    v,
                    v == a.anti_chase_atr.anchor,
                    "break direction",
                ),
                e3,
                brk,
            )
        )
    return out


def first_in_cluster(mask: np.ndarray, direction: np.ndarray, cluster: int) -> np.ndarray:
    """Event rows with no same-direction event in the previous `cluster` rows."""
    out = np.zeros(len(mask), dtype=bool)
    last = {1.0: -(10**9), -1.0: -(10**9)}
    for i in np.flatnonzero(mask):
        d = float(direction[i])
        if d == 0.0:
            continue
        if i - last[d] > cluster:
            out[i] = True
        last[d] = int(i)
    return out


# ----------------------------------------------------------------------------- outcomes
@dataclass
class Outcomes:
    ret: dict[int, np.ndarray]  # signed forward return (%) per horizon
    mfe: dict[int, np.ndarray]  # in ATR5m units
    mae: dict[int, np.ndarray]
    ret_atr: dict[int, np.ndarray]
    before: dict[
        tuple[float, float], np.ndarray
    ]  # 1 if +X hit before -Y within max horizon, 0 if -Y first, nan if neither


def forward_outcomes(
    fr: V7Frame, idx: np.ndarray, direction: np.ndarray, cfg: V7Config
) -> Outcomes:
    c, h, lo, atr = fr.cols["close"], fr.cols["high"], fr.cols["low"], fr.cols["atr5m"]
    n = len(fr)
    hz = cfg.discovery.horizons_min
    maxh = cfg.discovery.max_horizon_min
    m = len(idx)
    ret = {k: np.full(m, np.nan) for k in hz}
    ret_atr = {k: np.full(m, np.nan) for k in hz}
    mfe = {k: np.full(m, np.nan) for k in hz}
    mae = {k: np.full(m, np.nan) for k in hz}
    pairs = [(float(x), float(y)) for x, y in cfg.discovery.before_pairs]
    before = {p: np.full(m, np.nan) for p in pairs}
    for q, (i, d) in enumerate(zip(idx, direction, strict=True)):
        i = int(i)
        if i + maxh >= n or math.isnan(c[i]) or math.isnan(atr[i]) or atr[i] <= 0:
            continue
        p0 = c[i]
        fh, fl, fc_ = h[i + 1 : i + maxh + 1], lo[i + 1 : i + maxh + 1], c[i + 1 : i + maxh + 1]
        fav = (fh - p0) if d > 0 else (p0 - fl)  # favourable excursion path
        adv = (p0 - fl) if d > 0 else (fh - p0)
        for k in hz:
            ck = fc_[k - 1]
            if math.isnan(ck):
                continue
            r = d * (ck / p0 - 1.0)
            ret[k][q] = 100.0 * r
            ret_atr[k][q] = r * p0 / atr[i]
            mfe[k][q] = np.nanmax(fav[:k]) / atr[i]
            mae[k][q] = np.nanmax(adv[:k]) / atr[i]
        for x, y in pairs:
            up = np.flatnonzero(fav >= p0 * x / 100.0)
            dn = np.flatnonzero(adv >= p0 * y / 100.0)
            tu = up[0] if len(up) else maxh + 1
            td = dn[0] if len(dn) else maxh + 1
            if tu <= maxh or td <= maxh:
                before[(x, y)][q] = (
                    1.0 if tu < td else 0.0
                )  # ties (same minute) count as adverse first
    return Outcomes(ret, mfe, mae, ret_atr, before)


# ----------------------------------------------------------------------------- controls / stats
def match_key(fr: V7Frame, idx: np.ndarray) -> np.ndarray:
    c = fr.cols
    keys = np.stack(
        [
            c["hour"][idx],
            c["rv_tercile"][idx],
            c["ret15_tercile"][idx],
            c["regime_1h"][idx],
            c["struct_tercile"][idx],
        ],
        axis=1,
    )
    return keys


def sample_controls(
    fr: V7Frame,
    ev_idx: np.ndarray,
    all_event_rows: np.ndarray,
    i0: int,
    i1: int,
    cfg: V7Config,
    rng: np.random.RandomState,
) -> np.ndarray:
    """K controls per event with the same (hour, rv tercile, ret15 tercile, regime_1h, structure tercile),
    outside +-exclusion of ANY event row of the same definition. Returns the control row indices
    (length K x n_events, -1 where no candidate exists)."""
    k = cfg.discovery.controls_per_event
    excl = cfg.discovery.control_exclusion_min
    c = fr.cols
    cand = np.arange(i0, i1 - cfg.discovery.max_horizon_min)
    near = np.zeros(len(fr), dtype=bool)
    for i in all_event_rows:
        near[max(0, i - excl) : i + excl + 1] = True
    cand = cand[~near[cand] & fr.valid[cand]]
    ckey = np.stack(
        [
            c["hour"][cand],
            c["rv_tercile"][cand],
            c["ret15_tercile"][cand],
            c["regime_1h"][cand],
            c["struct_tercile"][cand],
        ],
        axis=1,
    )
    ok = ~np.isnan(ckey).any(axis=1)
    cand, ckey = cand[ok], ckey[ok]
    lists: dict[tuple[float, ...], list[int]] = {}
    for key, i in zip(map(tuple, ckey), cand, strict=True):
        lists.setdefault(key, []).append(int(i))
    pools: dict[tuple[float, ...], np.ndarray] = {kk: np.asarray(v) for kk, v in lists.items()}
    ekey = match_key(fr, ev_idx)
    out = np.full(len(ev_idx) * k, -1, dtype=np.int64)
    for q, key in enumerate(map(tuple, ekey)):
        if any(math.isnan(x) for x in key):
            continue
        pool = pools.get(key)
        if pool is None or len(pool) == 0:
            continue
        out[q * k : (q + 1) * k] = rng.choice(pool, size=k, replace=len(pool) < k)
    return out


def _boot_diff(
    a: np.ndarray, b: np.ndarray, n: int, rng: np.random.RandomState
) -> tuple[float, float]:
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if len(a) < 5 or len(b) < 5:
        return math.nan, math.nan
    ia = rng.randint(0, len(a), size=(n, len(a)))
    ib = rng.randint(0, len(b), size=(n, len(b)))
    d = a[ia].mean(axis=1) - b[ib].mean(axis=1)
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def compare(
    ev: Outcomes, ct: Outcomes, cfg: V7Config, rng: np.random.RandomState
) -> dict[str, Any]:
    out: dict[str, Any] = {"horizons": {}, "before": {}}
    nb = cfg.discovery.bootstrap_resamples
    for k in cfg.discovery.horizons_min:
        a, b = ev.ret_atr[k], ct.ret_atr[k]
        lo_, hi_ = _boot_diff(a, b, nb, rng)
        sd = float(np.nanstd(b)) if np.isfinite(b).any() else math.nan
        out["horizons"][k] = {
            "n_event": int(np.isfinite(a).sum()),
            "n_control": int(np.isfinite(b).sum()),
            "event_ret_pct": float(np.nanmean(ev.ret[k])),
            "control_ret_pct": float(np.nanmean(ct.ret[k])),
            "event_ret_atr": float(np.nanmean(a)),
            "control_ret_atr": float(np.nanmean(b)),
            "diff_atr": float(np.nanmean(a) - np.nanmean(b)),
            "diff_ci": [lo_, hi_],
            "effect_size": float((np.nanmean(a) - np.nanmean(b)) / sd)
            if sd and sd > 0
            else math.nan,
            "event_mfe_atr": float(np.nanmean(ev.mfe[k])),
            "control_mfe_atr": float(np.nanmean(ct.mfe[k])),
            "event_mae_atr": float(np.nanmean(ev.mae[k])),
            "control_mae_atr": float(np.nanmean(ct.mae[k])),
            "event_mfe_mae_ratio": float(np.nanmean(ev.mfe[k]) / np.nanmean(ev.mae[k]))
            if np.nanmean(ev.mae[k]) > 0
            else math.nan,
            "control_mfe_mae_ratio": float(np.nanmean(ct.mfe[k]) / np.nanmean(ct.mae[k]))
            if np.nanmean(ct.mae[k]) > 0
            else math.nan,
            "event_hit_rate": float(np.nanmean(a > 0)),
            "control_hit_rate": float(np.nanmean(b > 0)),
        }
    for pair, a in ev.before.items():
        b = ct.before[pair]
        pe, pc = (
            float(np.nanmean(a)) if np.isfinite(a).any() else math.nan,
            float(np.nanmean(b)) if np.isfinite(b).any() else math.nan,
        )
        lo_, hi_ = _boot_diff(a, b, nb, rng)
        out["before"][f"+{pair[0]:.2f}%_before_-{pair[1]:.2f}%"] = {
            "event_p": pe,
            "control_p": pc,
            "diff": pe - pc,
            "diff_ci": [lo_, hi_],
            "event_resolved": int(np.isfinite(a).sum()),
            "control_resolved": int(np.isfinite(b).sum()),
        }
    return out


def _half_years(start_ms: int, end_ms: int) -> list[tuple[int, int, str]]:
    out = []
    d = datetime.fromtimestamp(start_ms / 1000, tz=UTC)
    while True:
        a = int(d.timestamp() * 1000)
        nxt = d.replace(month=7) if d.month == 1 else d.replace(year=d.year + 1, month=1)
        b = min(int(nxt.timestamp() * 1000), end_ms)
        out.append((a, b, f"{d.year}H{1 if d.month == 1 else 2}"))
        if b >= end_ms:
            break
        d = nxt
    return out


# ----------------------------------------------------------------------------- driver
def evaluate_definition(
    fr: V7Frame,
    dfn: EventDef,
    mask: np.ndarray,
    direction: np.ndarray,
    start_ms: int,
    end_ms: int,
    cfg: V7Config,
    seed: int,
    splits: bool,
) -> dict[str, Any]:
    dcfg = cfg.discovery
    i0, i1 = fr.idx_at(start_ms) + 1, fr.idx_at(end_ms - 1)
    win = np.zeros(len(fr), dtype=bool)
    win[i0:i1] = True
    m = mask & win & fr.valid & (direction != 0)
    all_rows = np.flatnonzero(m)
    fic = first_in_cluster(m, direction, dcfg.cluster_min)
    ev_idx = np.flatnonzero(fic)
    days = (end_ms - start_ms) / DAY_MS
    res: dict[str, Any] = {
        "definition": dfn.__dict__,
        "window": {"start": iso(start_ms), "end_exclusive": iso(end_ms), "days": days},
        "n_event_rows": len(all_rows),
        "n_first_in_cluster": len(ev_idx),
        "events_per_day": len(all_rows) / days,
        "first_in_cluster_per_day": len(ev_idx) / days,
        "share_long": float(np.mean(direction[ev_idx] > 0)) if len(ev_idx) else math.nan,
    }
    if len(ev_idx) < 20:
        res["note"] = "fewer than 20 first-in-cluster events: no statistics"
        return res
    rng = np.random.RandomState(seed)
    ctrl = sample_controls(fr, ev_idx, all_rows, i0, i1, cfg, rng)
    ok = ctrl >= 0
    ctrl_dir = np.repeat(direction[ev_idx], dcfg.controls_per_event)[ok]
    ctrl = ctrl[ok]
    res["n_controls"] = len(ctrl)
    ev_out = forward_outcomes(fr, ev_idx, direction[ev_idx], cfg)
    ct_out = forward_outcomes(fr, ctrl, ctrl_dir, cfg)
    res["all"] = compare(ev_out, ct_out, cfg, rng)
    if splits:
        # long / short
        for side, s in (("long", 1.0), ("short", -1.0)):
            sel = direction[ev_idx] == s
            csel = ctrl_dir == s
            if sel.sum() >= 20 and csel.sum() >= 20:
                res[side] = compare(_sub(ev_out, sel), _sub(ct_out, csel), cfg, rng)
            else:
                res[side] = {"n": int(sel.sum())}
        # regime / hour block splits at the 60-minute horizon
        reg = fr.cols["regime_1h"]
        res["by_regime"] = {}
        for rv, name in ((1.0, "TREND_UP"), (0.0, "RANGE"), (-1.0, "TREND_DOWN")):
            sel = reg[ev_idx] == rv
            csel = reg[ctrl] == rv
            res["by_regime"][name] = _quick(ev_out, ct_out, sel, csel, cfg)
        hr = fr.cols["hour"]
        res["by_hour_block"] = {}
        for lo_h, hi_h, name in ((0, 8, "00-08"), (8, 16, "08-16"), (16, 24, "16-24")):
            sel = (hr[ev_idx] >= lo_h) & (hr[ev_idx] < hi_h)
            csel = (hr[ctrl] >= lo_h) & (hr[ctrl] < hi_h)
            res["by_hour_block"][name] = _quick(ev_out, ct_out, sel, csel, cfg)
        # half-year sign consistency at 60 minutes
        t_ev = fr.close_ms[ev_idx]
        t_ct = fr.close_ms[ctrl]
        hy = []
        for a, b, label in _half_years(start_ms, end_ms):
            sel = (t_ev >= a) & (t_ev < b)
            csel = (t_ct >= a) & (t_ct < b)
            q = _quick(ev_out, ct_out, sel, csel, cfg)
            q["label"] = label
            hy.append(q)
        res["half_years"] = hy
        pos = sum(
            1
            for q in hy
            if q.get("diff_atr_60") is not None
            and not math.isnan(q["diff_atr_60"])
            and q["diff_atr_60"] > 0
        )
        neg = sum(
            1
            for q in hy
            if q.get("diff_atr_60") is not None
            and not math.isnan(q["diff_atr_60"])
            and q["diff_atr_60"] < 0
        )
        res["half_year_consistency"] = {"positive": pos, "negative": neg, "n": len(hy)}
        d60 = res["all"]["horizons"][60]
        dec = cfg.discovery.decision
        res["repeatable_on_window"] = bool(
            d60["diff_atr"] >= dec.min_effect_atr_60
            and d60["diff_ci"][0] > 0
            and max(pos, neg) >= dec.min_consistent_half_years
            and pos >= neg
        )
    return res


def _sub(o: Outcomes, sel: np.ndarray) -> Outcomes:
    return Outcomes(
        {k: v[sel] for k, v in o.ret.items()},
        {k: v[sel] for k, v in o.mfe.items()},
        {k: v[sel] for k, v in o.mae.items()},
        {k: v[sel] for k, v in o.ret_atr.items()},
        {k: v[sel] for k, v in o.before.items()},
    )


def _quick(
    ev: Outcomes, ct: Outcomes, sel: np.ndarray, csel: np.ndarray, cfg: V7Config
) -> dict[str, Any]:
    a, b = ev.ret_atr[60][sel], ct.ret_atr[60][csel]
    if np.isfinite(a).sum() < 10 or np.isfinite(b).sum() < 10:
        return {"n": int(np.isfinite(a).sum()), "diff_atr_60": math.nan}
    pair = (0.5, 0.5)
    return {
        "n": int(np.isfinite(a).sum()),
        "event_ret_atr_60": float(np.nanmean(a)),
        "control_ret_atr_60": float(np.nanmean(b)),
        "diff_atr_60": float(np.nanmean(a) - np.nanmean(b)),
        "event_p50_before_50": float(np.nanmean(ev.before[pair][sel]))
        if np.isfinite(ev.before[pair][sel]).any()
        else math.nan,
        "control_p50_before_50": float(np.nanmean(ct.before[pair][csel]))
        if np.isfinite(ct.before[pair][csel]).any()
        else math.nan,
    }


@dataclass
class V7Discovery:
    manifest: dict[str, Any]
    discovery: list[dict[str, Any]]
    validation_1: list[dict[str, Any]]
    recent: list[dict[str, Any]]
    summary: dict[str, Any]


def run_discovery(cfg: V7Config, fr: V7Frame, out_dir: Path, data_hash: str) -> V7Discovery:
    w = cfg.windows
    dw = (ms(w.discovery.start), ms(w.discovery.end_exclusive))
    v1 = (ms(w.validation_1.start), ms(w.validation_1.end_exclusive))
    rv = (ms(w.recent_validation.start), ms(w.recent_validation.end_exclusive))
    defs = _events_single(cfg, fr) + _events_engines(cfg, fr)
    seed = cfg.discovery.seed
    disc: list[dict[str, Any]] = []
    val1: list[dict[str, Any]] = []
    rec: list[dict[str, Any]] = []
    for j, (dfn, m, d) in enumerate(defs):
        log.info("discovery: %s %s=%s", dfn.name, dfn.param, dfn.value)
        disc.append(evaluate_definition(fr, dfn, m, d, dw[0], dw[1], cfg, seed + j, True))
        if dfn.anchor:
            val1.append(
                evaluate_definition(fr, dfn, m, d, v1[0], v1[1], cfg, seed + 1000 + j, True)
            )
            rec.append(evaluate_definition(fr, dfn, m, d, rv[0], rv[1], cfg, seed + 2000 + j, True))
    summary = _summarise(cfg, disc, val1, rec)
    manifest = {
        "strategy_name": cfg.strategy_name,
        "config_hash": cfg.config_hash,
        "code_version": code_version(),
        "data_hash_bybit_1m": data_hash,
        "frame_meta": fr.meta,
        "windows": {
            "discovery": {"start": iso(dw[0]), "end_exclusive": iso(dw[1])},
            "validation_1": {"start": iso(v1[0]), "end_exclusive": iso(v1[1])},
            "recent_validation": {"start": iso(rv[0]), "end_exclusive": iso(rv[1])},
            "locked_holdout": "not read",
        },
        "ran_at": datetime.now(UTC).isoformat(),
        "n_definitions": len(defs),
    }
    manifest["research_hash"] = stable_hash({"summary": _clean(summary), "n": len(disc)})
    res = V7Discovery(manifest, disc, val1, rec, summary)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(_clean(manifest), indent=1, sort_keys=True, default=str)
    )
    (out_dir / "discovery.json").write_text(
        json.dumps(
            _clean({"discovery": disc, "validation_1": val1, "recent": rec, "summary": summary}),
            indent=1,
            sort_keys=True,
            default=str,
        )
    )
    return res


def _clean(d: Any) -> Any:
    if isinstance(d, dict):
        return {str(k): _clean(v) for k, v in d.items()}
    if isinstance(d, list | tuple):
        return [_clean(v) for v in d]
    if isinstance(d, float) and (math.isnan(d) or math.isinf(d)):
        return None
    if isinstance(d, np.generic):
        return _clean(d.item())
    return d


def _summarise(
    cfg: V7Config, disc: list[dict[str, Any]], val1: list[dict[str, Any]], rec: list[dict[str, Any]]
) -> dict[str, Any]:
    by_name_v1 = {(r["definition"]["name"], str(r["definition"]["value"])): r for r in val1}
    by_name_rec = {(r["definition"]["name"], str(r["definition"]["value"])): r for r in rec}
    rows = []
    for r in disc:
        d = r["definition"]
        key = (d["name"], str(d["value"]))
        h = r.get("all", {}).get("horizons", {})
        best_h = max(h, key=lambda k: abs(h[k]["diff_atr"])) if h else None
        row = {
            "name": d["name"],
            "param": d["param"],
            "value": d["value"],
            "anchor": d["anchor"],
            "events_per_day": r["events_per_day"],
            "fic_per_day": r["first_in_cluster_per_day"],
            "n_fic": r["n_first_in_cluster"],
            "diff_atr_60": h.get(60, {}).get("diff_atr", math.nan) if h else math.nan,
            "ci_60": h.get(60, {}).get("diff_ci", [math.nan, math.nan])
            if h
            else [math.nan, math.nan],
            "effect_60": h.get(60, {}).get("effect_size", math.nan) if h else math.nan,
            "best_horizon": best_h,
            "diff_best": h[best_h]["diff_atr"] if best_h else math.nan,
            "p50_before_50_event": r.get("all", {})
            .get("before", {})
            .get("+0.50%_before_-0.50%", {})
            .get("event_p", math.nan),
            "p50_before_50_control": r.get("all", {})
            .get("before", {})
            .get("+0.50%_before_-0.50%", {})
            .get("control_p", math.nan),
            "long_diff_60": r.get("long", {})
            .get("horizons", {})
            .get(60, {})
            .get("diff_atr", math.nan),
            "short_diff_60": r.get("short", {})
            .get("horizons", {})
            .get(60, {})
            .get("diff_atr", math.nan),
            "half_years_pos": r.get("half_year_consistency", {}).get("positive"),
            "half_years_n": r.get("half_year_consistency", {}).get("n"),
            "repeatable_discovery": r.get("repeatable_on_window"),
        }
        for lab, src in (("v1", by_name_v1), ("recent", by_name_rec)):
            rr = src.get(key)
            hh = rr.get("all", {}).get("horizons", {}) if rr else {}
            row[f"{lab}_diff_atr_60"] = hh.get(60, {}).get("diff_atr", math.nan) if hh else math.nan
            row[f"{lab}_ci_60"] = (
                hh.get(60, {}).get("diff_ci", [math.nan, math.nan]) if hh else [math.nan, math.nan]
            )
            row[f"{lab}_fic_per_day"] = rr["first_in_cluster_per_day"] if rr else math.nan
        row["survives_2025"] = (
            bool(row["repeatable_discovery"])
            and not math.isnan(row["recent_diff_atr_60"])
            and row["recent_diff_atr_60"] > 0
            and row["recent_ci_60"][1] > 0
        )
        rows.append(row)
    repeatable = [r for r in rows if r["repeatable_discovery"]]
    return {
        "rows": rows,
        "n_repeatable_discovery": len(repeatable),
        "repeatable": [f"{r['name']} {r['param']}{r['value']}" for r in repeatable],
        "survive_2025": [
            f"{r['name']} {r['param']}{r['value']}" for r in rows if r["survives_2025"]
        ],
    }
