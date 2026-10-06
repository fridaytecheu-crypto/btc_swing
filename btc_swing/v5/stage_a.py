"""Stage A: event edge before execution (docs/BTC_SWING_V5_DESIGN.md §6).

Every event (every 5m bar where a family's event condition holds, both sides, independent of any
slot) records signed forward returns at the pre-registered horizons from the event bar's close,
MFE/MAE over 12 h in ATR(1h) units, calendar year/quarter, regime and strength. Reported per
family and side: count, mean, median, hit rate, t, 1000-resample bootstrap interval of the mean,
year-by-year and quarter-by-quarter means, strength terciles; plus the unconditional 5m forward
return distribution and time-/regime-matched random-bar nulls. The per-family gate is encoded in
`stage_a_gate`.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray

from btc_swing.v5.config import V5Config, V5Family
from btc_swing.v5.events import V5Detector
from btc_swing.v5.features import FeatureFrame

F = NDArray[np.float64]
SNAPSHOT_COLS = (
    "ret_1h_z",
    "imbalance_1h_z",
    "cvd_slope_1h_z",
    "oi_chg_1h_z",
    "vol_1h_z",
    "fund_z",
    "prem_z",
    "basis_z",
    "book_imb_1",
    "depth_total_z",
    "divergence",
    "flow_source",
)


def _f(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


def _nanmean(s: pl.Series) -> float:
    x = s.to_numpy().astype(float)
    return float(np.nanmean(x)) if (~np.isnan(x)).any() else math.nan


def _nanmedian(s: pl.Series) -> float:
    x = s.to_numpy().astype(float)
    return float(np.nanmedian(x)) if (~np.isnan(x)).any() else math.nan


def _t(x: F) -> float:
    x = x[~np.isnan(x)]
    if len(x) < 3 or x.std(ddof=1) == 0:
        return math.nan
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x))))


def _fwd_extreme(x: F, steps: int, kind: str) -> F:
    """max/min of x over rows k+1..k+steps (NaN when the window is not complete)."""
    s = pl.Series(x.astype(np.float64)).shift(-1).reverse()
    r = (
        s.rolling_max(steps, min_samples=steps)
        if kind == "max"
        else s.rolling_min(steps, min_samples=steps)
    )
    return r.reverse().to_numpy().astype(np.float64)


def _year_quarter(t_ms: NDArray[np.int64]) -> tuple[list[int], list[str]]:
    ys, qs = [], []
    for t in t_ms:
        d = datetime.fromtimestamp(int(t) / 1000, tz=UTC)
        ys.append(d.year)
        qs.append(f"{d.year}Q{(d.month - 1) // 3 + 1}")
    return ys, qs


def scan_events(
    ff: FeatureFrame, detectors: list[V5Detector], cfg: V5Config, start_ms: int, end_ms: int
) -> pl.DataFrame:
    hz = cfg.stage_a.horizons_hours
    steps = [round(h * 12) for h in hz]
    mm_steps = cfg.stage_a.mfe_mae_hours * 12
    c, hi, lo, atr = ff.cols["close"], ff.cols["high"], ff.cols["low"], ff.cols["atr"]
    n = len(c)
    fwd_hi, fwd_lo = _fwd_extreme(hi, mm_steps, "max"), _fwd_extreme(lo, mm_steps, "min")
    k0 = max(0, ff.idx_at(start_ms))
    k1 = ff.idx_at(end_ms - 1)
    frames: list[pl.DataFrame] = []
    for det in detectors:
        ks = np.flatnonzero(det.mask[k0 : k1 + 1]) + k0
        if len(ks) == 0:
            continue
        s = float(det.side.sign)
        c0 = c[ks]
        d: dict[str, Any] = {
            "family": [det.v5_family.value] * len(ks),
            "side": [det.side.value] * len(ks),
            "k": ks,
            "t_ms": ff.close_ms[ks],
            "regime": [ff.regime[k].value for k in ks],
            "strength": det.strength[ks],
            "first_in_cluster": np.concatenate([[True], np.diff(ks) > cfg.events.cluster_bars]),
            "close": c0,
            "atr": atr[ks],
        }
        for h, st in zip(hz, steps, strict=True):
            kk = ks + st
            ok = kk < n
            r = np.full(len(ks), np.nan)
            r[ok] = s * (c[kk[ok]] / c0[ok] - 1.0)
            d[f"fwd_{h:g}h"] = r
        fav = (fwd_hi[ks] - c0) if s > 0 else (c0 - fwd_lo[ks])
        adv = (c0 - fwd_lo[ks]) if s > 0 else (fwd_hi[ks] - c0)
        with np.errstate(invalid="ignore", divide="ignore"):
            d["mfe_atr"] = np.where(atr[ks] > 0, fav / atr[ks], np.nan)
            d["mae_atr"] = np.where(atr[ks] > 0, -adv / atr[ks], np.nan)
        for name in SNAPSHOT_COLS:
            d[name] = ff.cols[name][ks]
        ys, qs = _year_quarter(ff.close_ms[ks])
        d["year"], d["quarter"] = ys, qs
        frames.append(pl.DataFrame(d))
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="vertical_relaxed").sort("t_ms", "family", "side")


def unconditional(ff: FeatureFrame, cfg: V5Config, start_ms: int, end_ms: int) -> dict[str, Any]:
    """Raw (unsigned) forward returns of every 5m bar over the same horizons."""
    c = ff.cols["close"]
    k0, k1 = max(0, ff.idx_at(start_ms)), ff.idx_at(end_ms - 1)
    ks = np.arange(k0, k1 + 1)
    out: dict[str, Any] = {"n_bars": len(ks)}
    for h in cfg.stage_a.horizons_hours:
        st = round(h * 12)
        kk = ks + st
        ok = kk < len(c)
        r = c[kk[ok]] / c[ks[ok]] - 1.0
        out[f"fwd_{h:g}h"] = {
            "mean": float(r.mean()),
            "std": float(r.std(ddof=1)),
            "mean_abs": float(np.abs(r).mean()),
        }
    return out


def bootstrap_ci(x: F, resamples: int, seed: int) -> tuple[float, float]:
    x = x[~np.isnan(x)]
    if len(x) < 5:
        return math.nan, math.nan
    rng = np.random.RandomState(seed)
    means = np.empty(resamples)
    n = len(x)
    for b in range(resamples):
        means[b] = x[rng.randint(0, n, n)].mean()
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def horizon_table(
    ev: pl.DataFrame, cfg: V5Config, ci_horizons: list[float] | None = None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if ev.is_empty():
        return out
    ci_h = ci_horizons if ci_horizons is not None else cfg.stage_a.gate.horizons_hours
    for h in cfg.stage_a.horizons_hours:
        x = ev[f"fwd_{h:g}h"].to_numpy().astype(float)
        xx = x[~np.isnan(x)]
        row: dict[str, Any] = {
            "horizon_h": h,
            "n": len(xx),
            "mean": float(xx.mean()) if len(xx) else math.nan,
            "median": float(np.median(xx)) if len(xx) else math.nan,
            "hit_rate": float((xx > 0).mean()) if len(xx) else math.nan,
            "t": _t(x),
        }
        if h in ci_h:
            lo, hi = bootstrap_ci(x, cfg.stage_a.bootstrap_resamples, cfg.research.seed)
            row["ci_lo"], row["ci_hi"] = lo, hi
        out.append(row)
    return out


def _terciles(sub: pl.DataFrame, hz: list[float]) -> list[dict[str, Any]] | None:
    st = sub["strength"].to_numpy().astype(float)
    if sub.height < 30 or np.isnan(st).all():
        return None
    q1, q2 = np.nanquantile(st, [1 / 3, 2 / 3])
    terc = np.where(st <= q1, 1, np.where(st <= q2, 2, 3))
    tsub = sub.with_columns(pl.Series("tercile", terc))
    out = []
    for k in (1, 2, 3):
        part = tsub.filter(pl.col("tercile") == k)
        out.append(
            {
                "tercile": k,
                "n": part.height,
                "strength_min": _f(part["strength"].min()),
                "strength_max": _f(part["strength"].max()),
                **{f"fwd_{h:g}h": _f(part[f"fwd_{h:g}h"].mean()) for h in hz},
                "mfe_atr": _nanmean(part["mfe_atr"]),
                "mae_atr": _nanmean(part["mae_atr"]),
            }
        )
    return out


def _period_means(sub: pl.DataFrame, col: str, hz: list[float]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in sorted(sub[col].unique().to_list()):
        part = sub.filter(pl.col(col) == p)
        d: dict[str, Any] = {"n": part.height}
        for h in hz:
            x = part[f"fwd_{h:g}h"].to_numpy().astype(float)
            d[f"fwd_{h:g}h"] = _f(np.nanmean(x)) if (~np.isnan(x)).any() else math.nan
            d[f"t_{h:g}h"] = _t(x)
        out[str(p)] = d
    return out


def stage_a_null(
    ev: pl.DataFrame, ff: FeatureFrame, cfg: V5Config, start_ms: int, end_ms: int, reps: int = 200
) -> dict[str, Any]:
    """Random bars, same count and side mix as the family's first-in-cluster events; time-matched
    (uniform over the window) and regime-matched (same regime label as each event)."""
    if ev.is_empty():
        return {}
    c = ff.cols["close"]
    n = len(c)
    k0, k1 = max(0, ff.idx_at(start_ms)), ff.idx_at(end_ms - 1)
    hz = cfg.stage_a.gate.horizons_hours
    steps = {h: round(h * 12) for h in hz}
    rng = np.random.RandomState(cfg.research.seed)
    reg_pool: dict[int, NDArray[np.int64]] = {}
    codes = ff.regime_code
    out: dict[str, Any] = {}
    for fam in sorted(ev["family"].unique().to_list()):
        sub = ev.filter((pl.col("family") == fam) & pl.col("first_in_cluster"))
        if sub.is_empty():
            continue
        sides = np.where(sub["side"].to_numpy() == "LONG", 1.0, -1.0)
        ev_codes = codes[sub["k"].to_numpy().astype(np.int64)]
        m = len(sides)
        real = {h: _f(np.nanmean(sub[f"fwd_{h:g}h"].to_numpy().astype(float))) for h in hz}
        res: dict[str, Any] = {"n": m, "real": real}
        for variant in ("time", "regime"):
            means = {h: np.empty(reps) for h in hz}
            for r in range(reps):
                if variant == "time":
                    ks = rng.randint(k0, k1 + 1, m)
                else:
                    ks = np.empty(m, dtype=np.int64)
                    for i, code in enumerate(ev_codes):
                        pool = reg_pool.get(int(code))
                        if pool is None:
                            pool = np.flatnonzero(codes[k0 : k1 + 1] == code) + k0
                            reg_pool[int(code)] = pool
                        ks[i] = (
                            pool[rng.randint(len(pool))] if len(pool) else rng.randint(k0, k1 + 1)
                        )
                for h in hz:
                    kk = ks + steps[h]
                    ok = kk < n
                    fr = sides[ok] * (c[kk[ok]] / c[ks[ok]] - 1.0)
                    means[h][r] = fr.mean() if len(fr) else math.nan
            res[variant] = {}
            for h in hz:
                mu, sd = float(np.nanmean(means[h])), float(np.nanstd(means[h], ddof=1))
                res[variant][f"{h:g}h"] = {
                    "null_mean": mu,
                    "null_sd": sd,
                    "z": (real[h] - mu) / sd if sd > 0 else math.nan,
                    "frac_ge_real": float(np.nanmean(means[h] >= real[h])),
                }
        out[fam] = res
    return out


def stage_a_summary(ev: pl.DataFrame, cfg: V5Config, uncond: dict[str, Any]) -> dict[str, Any]:
    hz = cfg.stage_a.horizons_hours
    if ev.is_empty():
        return {"n_events": 0, "unconditional": uncond, "by_family": {}}
    fic = ev.filter(pl.col("first_in_cluster"))
    out: dict[str, Any] = {
        "n_events": ev.height,
        "n_first_in_cluster": fic.height,
        "pooled": horizon_table(ev, cfg),
        "pooled_first_in_cluster": horizon_table(fic, cfg),
        "unconditional": uncond,
        "events_per_day": ev.height
        / max((_f(ev["t_ms"].max()) - _f(ev["t_ms"].min())) / 86_400_000.0, 1.0),
        "by_family_side_counts": ev.group_by("family", "side")
        .agg(pl.len().alias("len"), pl.col("first_in_cluster").sum().alias("first_in_cluster"))
        .sort("family", "side")
        .to_dicts(),
        "by_family": {},
    }
    for fam in [f.value for f in V5Family]:
        sub = ev.filter(pl.col("family") == fam)
        subf = fic.filter(pl.col("family") == fam)
        if sub.is_empty():
            out["by_family"][fam] = {"n": 0, "n_first_in_cluster": 0}
            continue
        out["by_family"][fam] = {
            "n": sub.height,
            "n_first_in_cluster": subf.height,
            "all": horizon_table(sub, cfg),
            "first_in_cluster": horizon_table(subf, cfg),
            "by_side": {
                sd: horizon_table(subf.filter(pl.col("side") == sd), cfg)
                for sd in ("LONG", "SHORT")
            },
            "by_side_n_all": {
                sd: sub.filter(pl.col("side") == sd).height for sd in ("LONG", "SHORT")
            },
            "mfe_atr": _nanmean(subf["mfe_atr"]),
            "mae_atr": _nanmean(subf["mae_atr"]),
            "mfe_atr_median": _nanmedian(subf["mfe_atr"]),
            "mae_atr_median": _nanmedian(subf["mae_atr"]),
            "terciles": _terciles(subf, hz),
            "by_year": _period_means(subf, "year", cfg.stage_a.gate.horizons_hours),
            "by_quarter": _period_means(subf, "quarter", cfg.stage_a.gate.horizons_hours),
            "by_regime": _period_means(subf, "regime", cfg.stage_a.gate.horizons_hours),
            "strength_quantiles": {
                f"p{int(q * 100)}": _f(np.nanquantile(subf["strength"].to_numpy().astype(float), q))
                for q in (0.1, 0.5, 0.9)
            },
            "snapshot_means": {c: _f(subf[c].mean()) for c in SNAPSHOT_COLS},
        }
    out["by_year"] = _period_means(fic, "year", hz)
    out["by_regime"] = _period_means(fic, "regime", cfg.stage_a.gate.horizons_hours)
    return out


def stage_a_gate(summary: dict[str, Any], cfg: V5Config) -> dict[str, Any]:
    """Per family, first-in-cluster events: n >= min_events; mean > 0 with t >= min_t at 1 h OR
    4 h; positive mean in >= min_positive_years calendar years at that horizon; top strength
    tercile mean >= bottom tercile mean at that horizon. Encoded before any result was seen."""
    g = cfg.stage_a.gate
    out: dict[str, Any] = {
        "families": {},
        "n_passed": 0,
        "passed_families": [],
        "min_events": g.min_events,
        "min_t": g.min_t,
        "min_positive_years": g.min_positive_years,
    }
    for fam, d in summary.get("by_family", {}).items():
        n = d.get("n_first_in_cluster", 0)
        row: dict[str, Any] = {"n": n, "n_ok": n >= g.min_events, "horizons": {}, "passed": False}
        rows = {r["horizon_h"]: r for r in d.get("first_in_cluster", [])}
        terc = d.get("terciles")
        for h in g.horizons_hours:
            r = rows.get(h, {})
            mean, t = _f(r.get("mean")), _f(r.get("t"))
            years = d.get("by_year", {})
            pos_years = sum(1 for y in years.values() if _f(y.get(f"fwd_{h:g}h")) > 0)
            top = _f(terc[2][f"fwd_{h:g}h"]) if terc else math.nan
            bot = _f(terc[0][f"fwd_{h:g}h"]) if terc else math.nan
            mono = (not math.isnan(top)) and (not math.isnan(bot)) and top >= bot
            edge = (not math.isnan(mean)) and mean > 0 and (not math.isnan(t)) and t >= g.min_t
            ok = row["n_ok"] and edge and pos_years >= g.min_positive_years and mono
            row["horizons"][f"{h:g}h"] = {
                "mean": mean,
                "t": t,
                "ci_lo": r.get("ci_lo"),
                "ci_hi": r.get("ci_hi"),
                "edge": edge,
                "positive_years": pos_years,
                "n_years": len(years),
                "top_tercile": top,
                "bottom_tercile": bot,
                "monotone": mono,
                "passed": bool(ok),
            }
            row["passed"] = row["passed"] or bool(ok)
        out["families"][fam] = row
        if row["passed"]:
            out["n_passed"] += 1
            out["passed_families"].append(fam)
    out["passed"] = out["n_passed"] >= 1
    return out
