"""Independent-evidence and trend-contradiction arrays (design section 3), vectorised over a V11 frame.
Built from the V11 group sub-scores (read-only library); one group = one unit of evidence."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from btc_swing.v11.features import V11Frame
from btc_swing.v11.scoring import group_scores
from btc_swing.v11_1.config import V111Config

F = NDArray[np.float64]


def evidence_frame(
    fr: V11Frame, cfg: V111Config, micro: dict[str, F] | None = None
) -> dict[str, F]:
    """Returns per-side arrays: evid_n_{side} (count of confirming directional groups),
    evid_{side}_{group} (1.0 confirming / 0.0 not / NaN unavailable) and contra_{side} (1.0 when the
    trend/regime contradicts the side)."""
    q = cfg.quality
    sub_l = group_scores(fr, cfg.base, 1.0, micro)
    sub_s = group_scores(fr, cfg.base, -1.0, micro)
    n = len(fr)
    out: dict[str, F] = {}
    for tag, mine, other in (("long", sub_l, sub_s), ("short", sub_s, sub_l)):
        count = np.zeros(n)
        for g in q.directional_groups:
            a, b = mine[g], other[g]
            ok = np.isfinite(a) & np.isfinite(b)
            with np.errstate(invalid="ignore"):
                conf = ok & (a >= q.evidence_min_sub) & ((a - b) >= q.evidence_min_margin)
            out[f"evid_{tag}_{g}"] = np.where(ok, conf.astype(np.float64), np.nan)
            count += conf.astype(np.float64)
        out[f"evid_n_{tag}"] = count
    X = fr.c  # noqa: N806
    with np.errstate(invalid="ignore"):
        for s, tag in ((1.0, "long"), (-1.0, "short")):
            opp_struct = X["struct15"] == -s
            opp_trend = (s * (X["ema21"] - X["ema50"]) < 0) & (s * (X["close"] - X["vwap"]) < 0)
            out[f"contra_{tag}"] = (opp_struct | opp_trend).astype(np.float64)
    return out
