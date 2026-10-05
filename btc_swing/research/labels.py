"""Forward labels for EVERY setup episode (traded or not), computed after the run from the 5m
series. They answer "did the plan carry information regardless of whether our trigger fired?"

For each episode, from the first 5m bar closing after detection:
  fwd_ret_{4h,24h,72h}   signed (by side) close-to-close return from the detection close
  target_first           structural target touched before the invalidation level (within horizon)
  invalidation_first     invalidation level touched before the structural target
  neither                horizon ended with neither touched
Same-bar touches count as invalidation first (conservative).
"""

from __future__ import annotations

import math

import numpy as np
import polars as pl

from btc_swing.features.view import MultiTfSeries

HORIZONS_H = (4, 24, 72)


def label_episodes(
    episodes: pl.DataFrame, series: MultiTfSeries, max_horizon_hours: int
) -> pl.DataFrame:
    if episodes.is_empty():
        return episodes
    base = series.base
    n = len(base)
    rows: list[dict[str, float | bool | str | None]] = []
    for e in episodes.iter_rows(named=True):
        t0 = int(e["detected_at_ms"])
        i0 = int(np.searchsorted(base.close_ms, t0, side="right")) - 1
        out: dict[str, float | bool | str | None] = {"episode_id": e["episode_id"]}
        if i0 < 0 or i0 >= n:
            rows.append(out)
            continue
        ref = float(base.close[i0])
        sgn = 1.0 if e["side"] == "LONG" else -1.0
        for h in HORIZONS_H:
            j = i0 + h * 12
            out[f"fwd_ret_{h}h"] = sgn * (float(base.close[j]) / ref - 1.0) if j < n else None
        target = e.get("structural_target")
        inval = e.get("invalidation_level")
        res = "no_target"
        bars_to = None
        if target is not None and inval is not None and not math.isnan(float(target)):
            tgt, inv = float(target), float(inval)
            res = "neither"
            jmax = min(n, i0 + 1 + max_horizon_hours * 12)
            for j in range(i0 + 1, jmax):
                hi, lo = float(base.high[j]), float(base.low[j])
                inv_hit = (lo <= inv) if sgn > 0 else (hi >= inv)
                tgt_hit = (hi >= tgt) if sgn > 0 else (lo <= tgt)
                if inv_hit:
                    res, bars_to = "invalidation_first", j - i0
                    break
                if tgt_hit:
                    res, bars_to = "target_first", j - i0
                    break
            out["target_distance_pct"] = sgn * (tgt - ref) / ref
            out["invalidation_distance_pct"] = sgn * (ref - inv) / ref
        out["path_outcome"] = res
        out["bars_to_outcome"] = bars_to
        rows.append(out)
    lab = pl.DataFrame(rows)
    return episodes.join(lab, on="episode_id", how="left")
