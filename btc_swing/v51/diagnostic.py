"""V5.1 read-only signal diagnostic (the generic diagnostic on the V5.1 frame) with the per-feature
quality table, and the V5-vs-V5.1 comparison that explains every difference."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from btc_swing.v5.features import FeatureFrame
from btc_swing.v5.forward.diagnostic import Z_SOURCES, render_diagnostic, signal_diagnostic
from btc_swing.v51.config import V51Config
from btc_swing.v51.features import DETECTOR_Z, Z_COLUMNS, Validity
from btc_swing.v51.forward import V51Context, assemble_v51, build_v51


def _builder(ctx: V51Context) -> tuple[Any, Any, Any]:
    a51 = assemble_v51(ctx)
    b = build_v51(ctx, a51)
    return a51.base, b, b.validity


def feature_table(
    cfg: V51Config, ff: FeatureFrame, val: Validity | None, k: int
) -> list[dict[str, Any]]:
    """FEATURE | VALID OBS (in the frozen z window) | WARM | CURRENT (raw, masked) | Z | QUALITY."""
    out: list[dict[str, Any]] = []
    if val is None:
        return out
    win, mp = cfg.features.z_window_bars, cfg.features.z_min_periods
    lo = max(0, k - win)
    for zc in (*DETECTOR_Z, "prem_z", "fund_z"):
        if zc == "fund_z":
            z = ff.v("fund_z", k)
            out.append(
                {
                    "feature": zc,
                    "valid_obs": "n/a",
                    "min_periods": "45 funding events",
                    "warm": not math.isnan(z),
                    "current": ff.v("fund", k),
                    "z": z,
                    "quality": "crowding filter only (NaN passes); 8-hourly funding observations",
                }
            )
            continue
        _raw, key = Z_COLUMNS[zc]
        src = Z_SOURCES[zc]
        x = np.asarray(src.raw(ff), dtype=np.float64)
        ok = val.obs[key]
        w = x[lo:k]
        valid_obs = int(((~np.isnan(w)) & ok[lo:k]).sum())
        cur_valid = bool(ok[k]) if 0 <= k < len(ok) else False
        z = ff.v(zc, k)
        if not cur_valid:
            q = "INVALID NOW: the current lookback window touches a gap row"
        elif valid_obs < mp:
            q = f"warming: {valid_obs}/{mp} valid observations"
        else:
            q = "clean"
        out.append(
            {
                "feature": zc,
                "valid_obs": valid_obs,
                "min_periods": mp,
                "warm": valid_obs >= mp,
                "current": float(x[k]) if cur_valid and 0 <= k < len(x) else None,
                "z": None if math.isnan(z) else z,
                "quality": q,
                "excluded_in_window": int(((~np.isnan(w)) & ~ok[lo:k]).sum()),
            }
        )
    # ATR / geometry
    out.append(
        {
            "feature": "atr_1h (clean bars)",
            "valid_obs": val.n_1h_bars_clean,
            "min_periods": cfg.indicators.atr_period,
            "warm": val.n_1h_bars_clean >= cfg.indicators.atr_period,
            "current": ff.v("atr", k),
            "z": None,
            "quality": "clean"
            if val.geometry is not None and val.geometry[k]
            else "INVALID NOW: ATR window or 12-bar structural window touches a gap",
        }
    )
    return out


def signal_diagnostic_v51(ctx: V51Context, now_ms: int | None = None) -> dict[str, Any]:
    d = signal_diagnostic(
        ctx,  # type: ignore[arg-type]
        now_ms,
        builder=_builder,
        feature_table=feature_table,
        label="V5.1 (data-quality fix)",
    )
    seeds = {}
    for kind in ("oi", "premium", "funding"):
        p = ctx.paths.seed_dir(kind) / "verification.json"
        if p.exists():
            import json

            seeds[kind] = json.loads(p.read_text())
    d["seed_verification"] = seeds
    d["v51_observation_start"] = d.get("observation_start")
    return d


def render_v51(d: dict[str, Any]) -> str:
    txt = render_diagnostic(d)
    sv = d.get("seed_verification") or {}
    if sv:
        lines = ["", "HISTORICAL SEED VERIFICATION (alignment vs live rows; warm-up only)"]
        for kind, r in sv.items():
            lines.append(
                f"  {kind}: ok={r.get('ok')} overlap={r.get('n_overlap')} "
                f"{r.get('alignment', '')} {r.get('reason', '')}"
            )
        txt += "\n" + "\n".join(lines)
    return txt


# ----------------------------------------------------------------------------- comparison
def _cause(f5: dict[str, Any], f51: dict[str, Any]) -> str:
    if f5["not_warm_inputs"] and not f51["not_warm_inputs"]:
        return "V5.1 warm thanks to the historical warm-up seed (V5 lacks pre-start history)"
    if f5.get("unreachable_inputs") and not f51.get("unreachable_inputs"):
        return "V5 baseline distorted by gap-contaminated observations (excluded in V5.1)"
    if f51.get("invalid_now_inputs"):
        return (
            "V5.1 refuses: the current window touches a gap row (V5 consumed carried-forward data)"
        )
    if f5["stage_a_event_now"] != f51["stage_a_event_now"]:
        return "event verdict differs because the z baseline differs (gap rows in the V5 window)"
    if f5["closest_failed"] != f51["closest_failed"]:
        return "same verdict; condition values differ by the z baseline (gap rows in the V5 window)"
    return "identical"


def compare_diagnostics(d5: dict[str, Any], d51: dict[str, Any]) -> dict[str, Any]:
    rows = []
    by51 = {(f["family"], f["side"]): f for f in d51["families"]}
    for f5 in d5["families"]:
        f51 = by51[(f5["family"], f5["side"])]
        rows.append(
            {
                "family": f5["family"],
                "side": f5["side"],
                "v5": {
                    "event": f5["stage_a_event_now"],
                    "warm": f5["warm"],
                    "not_warm": f5["not_warm_inputs"],
                    "unreachable": f5.get("unreachable_inputs", []),
                    "closest": f5["closest_failed"],
                    "reason": f5["reason_no_signal"],
                },
                "v51": {
                    "event": f51["stage_a_event_now"],
                    "warm": f51["warm"],
                    "not_warm": f51["not_warm_inputs"],
                    "unreachable": f51.get("unreachable_inputs", []),
                    "invalid_now": f51.get("invalid_now_inputs", []),
                    "closest": f51["closest_failed"],
                    "reason": f51["reason_no_signal"],
                    "data_quality": f51.get("data_quality"),
                },
                "cause": _cause(f5, f51),
            }
        )
    q5, q51 = d5["data_quality"], d51["data_quality"]
    feats = {}
    for col in (
        "ret_1h_z",
        "vol_1h_z",
        "imbalance_1h_z",
        "cvd_slope_1h_z",
        "oi_chg_1h_z",
        "prem_z",
    ):
        a, b = q5.get(col) or {}, q51.get(col) or {}
        feats[col] = {
            "v5_window_obs": a.get("window_obs"),
            "v5_obs_from_gap_rows": a.get("window_obs_from_gap_rows"),
            "v5_window_std": a.get("window_std"),
            "v51_window_obs": b.get("window_obs"),
            "v51_obs_from_gap_rows": b.get("window_obs_from_gap_rows"),
            "v51_window_std": b.get("window_std"),
            "v5_unreachable": a.get("unreachable"),
            "v51_unreachable": b.get("unreachable"),
        }
    return {
        "latest_bar": d5["latest_bar"],
        "v5_ranking": d5["ranking"],
        "v51_ranking": d51["ranking"],
        "families": rows,
        "features": feats,
        "vol_1h_needed_for_z1": {
            "v5_btc_per_hour": (q5.get("vol_1h_z") or {}).get(
                "btc_per_hour_needed_for_vol_1h_z_1.0"
            ),
            "v51_btc_per_hour": (q51.get("vol_1h_z") or {}).get(
                "btc_per_hour_needed_for_vol_1h_z_1.0"
            ),
        },
        "v5_signals_since_start": d5["signals_since_start"],
        "v51_signals_since_start": d51["signals_since_start"],
    }


def render_comparison(c: dict[str, Any]) -> str:
    out = [
        f"V5 vs V5.1 SIGNAL DIAGNOSTIC COMPARISON at {c['latest_bar']} (read-only)",
        "",
        "FAMILY | SIDE | V5 EVENT | V5.1 EVENT | V5 WARM | V5.1 WARM | V5 CLOSEST | V5.1 CLOSEST | CAUSE",
    ]
    for r in c["families"]:
        c5, c51 = r["v5"]["closest"] or {}, r["v51"]["closest"] or {}
        out.append(
            " | ".join(
                [
                    r["family"],
                    r["side"],
                    "YES" if r["v5"]["event"] else "no",
                    "YES" if r["v51"]["event"] else "no",
                    "yes" if r["v5"]["warm"] else "NO:" + ",".join(r["v5"]["not_warm"]),
                    "yes" if r["v51"]["warm"] else "NO:" + ",".join(r["v51"]["not_warm"]),
                    f"{c5.get('condition', '-')} {c5.get('current')}",
                    f"{c51.get('condition', '-')} {c51.get('current')}",
                    r["cause"],
                ]
            )
        )
    out += ["", "FEATURE BASELINES (frozen 30-day z window)"]
    for col, f in c["features"].items():
        out.append(
            f"  {col}: V5 obs {f['v5_window_obs']} (from gap rows {f['v5_obs_from_gap_rows']}, std "
            f"{f['v5_window_std']}) -> V5.1 obs {f['v51_window_obs']} (from gap rows "
            f"{f['v51_obs_from_gap_rows']}, std {f['v51_window_std']}); unreachable V5 "
            f"{f['v5_unreachable']} -> V5.1 {f['v51_unreachable']}"
        )
    v = c["vol_1h_needed_for_z1"]
    out.append(
        f"  1h volume needed for vol_1h_z >= 1.0: V5 {v['v5_btc_per_hour']} BTC -> V5.1 "
        f"{v['v51_btc_per_hour']} BTC"
    )
    out.append(
        f"  ranking V5: {' > '.join(c['v5_ranking'])}\n  ranking V5.1: {' > '.join(c['v51_ranking'])}"
    )
    return "\n".join(out)
