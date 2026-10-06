"""Stage A — event edge before execution. Every event (every 1H bar where a family's event
condition holds, both sides, independent of any slot) records signed forward returns at the
pre-registered horizons from the event bar's close, plus MFE/MAE over 24 h in ATR(1h) units."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl

from btc_swing.features.view import MultiTfSeries
from btc_swing.research.phase24 import _by
from btc_swing.v4.config import V4Config
from btc_swing.v4.events import V4Detector
from btc_swing.v4.features import FeatureFrame


def scan_events(
    ff: FeatureFrame,
    detectors: list[V4Detector],
    series: MultiTfSeries,
    cfg: V4Config,
    start_ms: int,
    end_ms: int,
) -> pl.DataFrame:
    base = series.base
    hz = cfg.stage_a.horizons_hours
    steps = [round(h * 12) for h in hz]
    mm_steps = cfg.stage_a.mfe_mae_hours * 12
    rows: list[dict[str, Any]] = []
    j0 = max(0, ff.idx_at(start_ms))
    j1 = ff.idx_at(end_ms - 1)
    last_fire: dict[tuple[str, str], int] = {}
    for j in range(j0, j1 + 1):
        t = int(ff.close_ms[j])
        k = int(np.searchsorted(base.close_ms, t, side="right")) - 1
        if k < 0:
            continue
        c0 = float(base.close[k])
        atr = ff.v("atr", j)
        for det in detectors:
            ev = det.event_at(j)
            if ev is None:
                continue
            key = (ev.family.value, ev.side.value)
            s = float(ev.side.sign)
            row: dict[str, Any] = {
                "family": ev.family.value,
                "side": ev.side.value,
                "j": j,
                "t_ms": t,
                "year": datetime.fromtimestamp(t / 1000, tz=UTC).year,
                "regime": ff.regime[j].value,
                "strength": ev.strength,
                "first_in_cluster": (j - last_fire.get(key, -(10**9))) > 4,
                "close": c0,
                "atr": atr,
            }
            last_fire[key] = j
            for h, st in zip(hz, steps, strict=True):
                kk = k + st
                row[f"fwd_{h:g}h"] = (
                    s * (float(base.close[kk]) / c0 - 1.0) if kk < len(base) else math.nan
                )
            kk = min(len(base), k + 1 + mm_steps)
            if kk > k + 1 and not math.isnan(atr) and atr > 0:
                hi = float(np.max(base.high[k + 1 : kk]))
                lo = float(np.min(base.low[k + 1 : kk]))
                fav = (hi - c0) if s > 0 else (c0 - lo)
                adv = (c0 - lo) if s > 0 else (hi - c0)
                row["mfe_atr"] = fav / atr
                row["mae_atr"] = -adv / atr
            else:
                row["mfe_atr"] = row["mae_atr"] = math.nan
            for name in (
                "ret_4h_z",
                "oi_chg_4h_z",
                "oi_chg_24h_z",
                "vol4_z",
                "vol_z",
                "taker_4h_z",
                "taker_1h_z",
                "fund_z",
                "prem_z",
            ):
                row[name] = ff.v(name, j)
            rows.append(row)
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def unconditional(
    ff: FeatureFrame, series: MultiTfSeries, cfg: V4Config, start_ms: int, end_ms: int
) -> dict[str, Any]:
    """Raw (unsigned) forward returns of every 1H bar over the same horizons: the BTC drift and its
    dispersion against which signed event returns are read."""
    base = series.base
    hz = cfg.stage_a.horizons_hours
    j0, j1 = max(0, ff.idx_at(start_ms)), ff.idx_at(end_ms - 1)
    ts = ff.close_ms[j0 : j1 + 1]
    ks = np.searchsorted(base.close_ms, ts, side="right") - 1
    c0 = base.close[ks]
    out: dict[str, Any] = {"n_bars": len(ks)}
    for h in hz:
        st = round(h * 12)
        kk = ks + st
        ok = kk < len(base)
        r = base.close[kk[ok]] / c0[ok] - 1.0
        out[f"fwd_{h:g}h"] = {
            "mean": float(r.mean()),
            "std": float(r.std(ddof=1)),
            "mean_abs": float(np.abs(r).mean()),
        }
    return out


def _f(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


def _t(x: np.ndarray) -> float:
    x = x[~np.isnan(x)]
    if len(x) < 3 or x.std(ddof=1) == 0:
        return math.nan
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x))))


def horizon_table(ev: pl.DataFrame, hz: list[float]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if ev.is_empty():
        return out
    for h in hz:
        x = ev[f"fwd_{h:g}h"].to_numpy().astype(float)
        xx = x[~np.isnan(x)]
        out.append(
            {
                "horizon_h": h,
                "n": len(xx),
                "mean": float(xx.mean()) if len(xx) else math.nan,
                "median": float(np.median(xx)) if len(xx) else math.nan,
                "hit_rate": float((xx > 0).mean()) if len(xx) else math.nan,
                "t": _t(x),
            }
        )
    return out


def stage_a_summary(ev: pl.DataFrame, cfg: V4Config, uncond: dict[str, Any]) -> dict[str, Any]:
    hz = cfg.stage_a.horizons_hours
    if ev.is_empty():
        return {"n_events": 0}
    out: dict[str, Any] = {
        "n_events": ev.height,
        "n_first_in_cluster": int(ev["first_in_cluster"].sum()),
        "pooled": horizon_table(ev, hz),
        "pooled_first_in_cluster": horizon_table(ev.filter(pl.col("first_in_cluster")), hz),
        "unconditional": uncond,
    }
    out["by_family"] = {}
    for fam in sorted(ev["family"].unique().to_list()):
        sub = ev.filter(pl.col("family") == fam)
        out["by_family"][fam] = {
            "n": sub.height,
            "all": horizon_table(sub, hz),
            "by_side": {
                sd: horizon_table(sub.filter(pl.col("side") == sd), hz) for sd in ("LONG", "SHORT")
            },
            "mfe_atr": _f(sub["mfe_atr"].mean()),
            "mae_atr": _f(sub["mae_atr"].mean()),
        }
        # strength terciles
        st = sub["strength"].to_numpy().astype(float)
        if sub.height >= 30:
            q1, q2 = np.quantile(st, [1 / 3, 2 / 3])
            terc = np.where(st <= q1, 1, np.where(st <= q2, 2, 3))
            tsub = sub.with_columns(pl.Series("tercile", terc))
            out["by_family"][fam]["terciles"] = [
                {
                    "tercile": int(k),
                    **{
                        f"fwd_{h:g}h": _f(tsub.filter(pl.col("tercile") == k)[f"fwd_{h:g}h"].mean())
                        for h in hz
                    },
                    "n": tsub.filter(pl.col("tercile") == k).height,
                }
                for k in (1, 2, 3)
            ]
    out["by_year"] = {
        str(y): horizon_table(ev.filter(pl.col("year") == y), hz)
        for y in sorted(ev["year"].unique().to_list())
    }
    out["by_regime"] = {
        str(r): horizon_table(ev.filter(pl.col("regime") == r), hz)
        for r in sorted(ev["regime"].unique().to_list())
    }
    out["events_per_day"] = ev.height / max(
        (_f(ev["t_ms"].max()) - _f(ev["t_ms"].min())) / 86_400_000.0, 1.0
    )
    out["by_family_side_counts"] = (
        ev.group_by("family", "side").len().sort("family", "side").to_dicts()
    )
    # pooled terciles across all events (strength is family-specific; pooled terciles are within family then pooled)
    out["mfe_mae_by_family"] = (
        _by(
            ev.rename({"mfe_atr": "MFE_R", "mae_atr": "MAE_R"}).with_columns(
                pl.col("fwd_8h").alias("R_MULTIPLE"),
                pl.col("fwd_8h").alias("POSITION_PNL"),
                pl.lit(0.0).alias("holding_hours"),
            ),
            "family",
        )
        if "fwd_8h" in ev.columns
        else []
    )
    return out


def stage_a_gate(summary: dict[str, Any], cfg: V4Config) -> dict[str, Any]:
    """Pre-declared: pooled 4h and 8h signed mean > 0 with t >= min_t, and the top strength tercile's
    8h mean >= the bottom tercile's (pooled over families via equal-weight average of family terciles)."""
    tmin = cfg.research.criteria.stage_a_min_t
    pooled = {d["horizon_h"]: d for d in summary.get("pooled", [])}
    p4, p8 = pooled.get(4.0, {}), pooled.get(8.0, {})
    ok4 = (p4.get("mean", math.nan) or math.nan) > 0 and (p4.get("t", math.nan) or math.nan) >= tmin
    ok8 = (p8.get("mean", math.nan) or math.nan) > 0 and (p8.get("t", math.nan) or math.nan) >= tmin
    tops, bots = [], []
    for fam in summary.get("by_family", {}).values():
        terc = fam.get("terciles")
        if terc:
            tops.append(terc[2]["fwd_8h"])
            bots.append(terc[0]["fwd_8h"])
    mono = bool(tops) and float(np.nanmean(tops)) >= float(np.nanmean(bots))
    return {
        "mean_4h": p4.get("mean"),
        "t_4h": p4.get("t"),
        "mean_8h": p8.get("mean"),
        "t_8h": p8.get("t"),
        "top_tercile_8h": float(np.nanmean(tops)) if tops else math.nan,
        "bottom_tercile_8h": float(np.nanmean(bots)) if bots else math.nan,
        "passed": bool(ok4 and ok8 and mono),
    }
