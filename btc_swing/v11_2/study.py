"""V11.2 COUNTERFACTUAL GATING STUDY (research only; owner request 2026-10-14).

Runs the frozen V11 engine on the consumed TRAIN and VALIDATION windows with ONE family-gating rule
changed per variant (subclass override of `structure`), everything else identical: features, score
model, thresholds, risk, exits, costs. The frozen V11 module, config, freeze and every forward
artefact are imported read-only and never modified. Loads are capped at 2025-07-01 by
`btc_swing.v11.data.load_inputs`; the locked holdout is never read.

This is a multi-variant search on consumed data: the best variant's numbers are selection-biased.
Nothing here is a strategy freeze; no shadow runner exists for V11.2."""

from __future__ import annotations

import gc
import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from btc_swing.core.versions import code_version
from btc_swing.v7.ingest import dataset_hash_1m
from btc_swing.v11.config import V11Config
from btc_swing.v11.data import DAY_MS, load_inputs, ms
from btc_swing.v11.engine import MS_1M, V11Engine
from btc_swing.v11.features import V11Frame, build_frame_v11
from btc_swing.v11.research import _clean, breakdowns, trade_stats
from btc_swing.v11.scoring import score_frame

log = logging.getLogger(__name__)
STUDY = "BTC_V11_2_COUNTERFACTUAL_GATING_STUDY"
STUDY_VERSION = "11.2.0-counterfactual"


@dataclass(frozen=True)
class Variant:
    """One gating change versus frozen V11 (all defaults = frozen V11)."""

    name: str
    group: str  # BASE / A / B / C / COMBO
    description: str
    a_struct_mode: str = "exact"  # exact | neutral_ok | within2 | within3
    b_near_atr: float | None = None  # None = frozen 0.30
    families: tuple[str, ...] = ("A", "B", "C")
    ignore_score_gates: bool = False  # C diagnostic only: arm/entry without the score thresholds


VARIANTS: tuple[Variant, ...] = (
    Variant("V11_BASE", "BASE", "frozen V11 (must reproduce the committed V11 backtest)"),
    Variant(
        "A1_NEUTRAL_OK",
        "A",
        "family A: struct15 == side OR neutral (0)",
        a_struct_mode="neutral_ok",
    ),
    Variant(
        "A2_WITHIN2",
        "A",
        "family A: struct15 == side on the current or previous completed 15m bar",
        a_struct_mode="within2",
    ),
    Variant(
        "A3_WITHIN3",
        "A",
        "family A: struct15 == side on any of the last 3 completed 15m bars",
        a_struct_mode="within3",
    ),
    Variant(
        "B1_NEAR_040",
        "B",
        "family B: near boundary <= 0.40 ATR5 (compression kept)",
        b_near_atr=0.40,
    ),
    Variant(
        "B2_NEAR_050",
        "B",
        "family B: near boundary <= 0.50 ATR5 (compression kept)",
        b_near_atr=0.50,
    ),
    Variant(
        "B3_NEAR_070",
        "B",
        "family B: near boundary <= 0.70 ATR5 (compression kept)",
        b_near_atr=0.70,
    ),
    Variant("C0_C_ONLY", "C", "family C alone, frozen gating (A and B disabled)", families=("C",)),
    Variant(
        "C1_C_ONLY_NO_SCORE",
        "C",
        "DIAGNOSTIC: family C alone, structure + trigger only (arm / entry score gates ignored)",
        families=("C",),
        ignore_score_gates=True,
    ),
)


class GatingEngine(V11Engine):
    """Frozen V11 engine with one family gate replaced (research only)."""

    def __init__(
        self, cfg: V11Config, fr: V11Frame, scores: dict[str, Any], v: Variant, *a: Any
    ) -> None:
        if v.ignore_score_gates:
            cfg = cfg.model_copy(
                update={
                    "thresholds": cfg.thresholds.model_copy(update={"armed": -1.0, "entry": -1.0})
                }
            )
        super().__init__(cfg, fr, scores, *a)
        self.v = v

    def structure(self, fam: str, s: int, i: int) -> bool:
        if fam not in self.v.families:
            return False
        v = self.v
        if fam == "A" and v.a_struct_mode != "exact":
            X = self.X  # noqa: N806
            c, a5 = X["close"][i], X["atr5"][i]
            if not (np.isfinite(c) and np.isfinite(a5) and a5 > 0):
                return False
            ok = X["a_struct_long"][i] == 1.0 if s > 0 else X["a_struct_short"][i] == 1.0
            turn = (s * X["accel5"][i] > 0) or (s * (X["rsi7"][i] - X["rsi7_prev"][i]) > 0)
            st = X["struct15"]
            if v.a_struct_mode == "neutral_ok":
                aligned = st[i] == s or st[i] == 0.0
            else:
                k = 2 if v.a_struct_mode == "within2" else 3
                # struct15 is the last COMPLETED 15m bar's value on every 1m row: i - 15*j is the
                # j-th previous completed 15m bar (PIT-safe; never a forming bar)
                aligned = any(i - 15 * j >= 0 and st[i - 15 * j] == s for j in range(k))
            return bool(ok and aligned and turn)
        if fam == "B" and v.b_near_atr is not None:
            X = self.X  # noqa: N806
            c, a5 = X["close"][i], X["atr5"][i]
            if not (np.isfinite(c) and np.isfinite(a5) and a5 > 0):
                return False
            comp = X["squeeze_release_recent"][i] == 1.0 or X["atr_low_recent"][i] == 1.0
            lvl = X["hi60"][i] if s > 0 else X["lo60"][i]
            near = np.isfinite(lvl) and 0 <= s * (lvl - c) <= v.b_near_atr * a5
            return bool(comp and near)
        return super().structure(fam, s, i)


@dataclass
class WindowFrame:
    name: str
    start_ms: int
    end_ms: int
    days: int
    fr: V11Frame
    sc: dict[str, Any]
    fund_t: Any
    fund_r: Any
    meta: dict[str, Any] = field(default_factory=dict)


def build_window(cfg: V11Config, data_dir: Path, name: str, start: str, end: str) -> WindowFrame:
    a, b = ms(start), ms(end)
    inp = load_inputs(cfg, data_dir, a, b)  # raises for any end beyond the 2025-07-01 data cap
    fr = build_frame_v11(inp.rows, cfg, inp.metrics, inp.funding)
    sc = score_frame(fr, cfg)
    ft = inp.funding["time_ms"].to_numpy().astype(np.int64) if not inp.funding.is_empty() else None
    frr = (
        inp.funding["funding_rate"].to_numpy().astype(np.float64)
        if not inp.funding.is_empty()
        else None
    )
    return WindowFrame(name, a, b, max(1, round((b - a) / DAY_MS)), fr, sc, ft, frr, dict(fr.meta))


def c_conflict(cfg: V11Config, w: WindowFrame) -> dict[str, Any]:
    """How often family C structure coincides with the score gates (structural conflict measure)."""
    X, sc = w.fr.c, w.sc  # noqa: N806
    i0 = int(np.searchsorted(w.fr.t_open, w.start_ms))
    i1 = int(np.searchsorted(w.fr.t_open, w.end_ms))
    fc, th = cfg.families.c, cfg.thresholds
    out: dict[str, Any] = {}
    with np.errstate(invalid="ignore"):
        for s, tag in ((1, "LONG"), (-1, "SHORT")):
            r7 = X["rsi7"][i0:i1]
            sweep = X["sweep_long_recent" if s > 0 else "sweep_short_recent"][i0:i1] == 1.0
            exh = (r7 <= fc.rsi_exhaust) if s > 0 else (r7 >= 100 - fc.rsi_exhaust)
            c_struct = sweep & exh & np.isfinite(X["atr5"][i0:i1])
            score = sc["score_long" if s > 0 else "score_short"][i0:i1]
            mom = sc["long_momentum" if s > 0 else "short_momentum"][i0:i1] / cfg.weights.momentum
            n_c = int(c_struct.sum())
            hi = score >= th.candidate
            out[tag] = {
                "minutes": int(i1 - i0),
                "c_structure_minutes": n_c,
                "c_structure_share": n_c / max(1, i1 - i0),
                "score_at_c_structure_p50_p90": [
                    float(np.nanpercentile(score[c_struct], 50)) if n_c else None,
                    float(np.nanpercentile(score[c_struct], 90)) if n_c else None,
                ],
                "share_c_structure_with_score_ge_armed": float(
                    np.nanmean(score[c_struct] >= th.armed)
                )
                if n_c
                else None,
                "share_c_structure_with_score_ge_entry": float(
                    np.nanmean(score[c_struct] >= th.entry)
                )
                if n_c
                else None,
                "momentum_subscore_at_c_structure_mean": float(np.nanmean(mom[c_struct]))
                if n_c
                else None,
                "momentum_subscore_all_minutes_mean": float(np.nanmean(mom)),
                "rsi7_exhausted_share_when_score_ge_candidate": float(np.nanmean(exh[hi]))
                if hi.any()
                else None,
            }
    return out


def run_variant(cfg: V11Config, w: WindowFrame, v: Variant) -> dict[str, Any]:
    eng = GatingEngine(cfg, w.fr, w.sc, v, 1.0, w.fund_t, w.fund_r)
    eng.run(w.start_ms, w.end_ms)
    st = trade_stats(
        eng.trades, w.days, cfg.risk.initial_equity, dict(eng.counters), dict(eng.blockers)
    )
    st["by_family_funnel"] = eng.by_family
    bd = breakdowns(eng.trades)
    log.info(
        "%s %s: trades %d (%.2f/day) gross %.3fR net %.3fR pf %.2f",
        w.name,
        v.name,
        len(eng.trades),
        len(eng.trades) / w.days,
        st.get("gross_r", math.nan),
        st.get("net_r", math.nan),
        st.get("profit_factor", math.nan),
    )
    return {
        "stats": st,
        "breakdown": {k: bd[k] for k in ("family", "side", "family_side", "exit_reason")},
    }


def run_study(
    cfg: V11Config, data_dir: Path, out_dir: Path, variants: tuple[Variant, ...] = VARIANTS
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    w = cfg.windows
    results: dict[str, dict[str, Any]] = {v.name: {} for v in variants}
    conflict: dict[str, Any] = {}
    meta: dict[str, Any] = {}
    for name, (a, b) in (
        ("train", (w.train.start, w.train.end_exclusive)),
        ("validation", (w.validation.start, w.validation.end_exclusive)),
    ):
        wf = build_window(cfg, data_dir, name, a, b)
        meta[name] = {"start": a, "end_exclusive": b, "days": wf.days, **wf.meta}
        conflict[name] = c_conflict(cfg, wf)
        for v in variants:
            results[v.name][name] = run_variant(cfg, wf, v)
        del wf
        gc.collect()
    out = {
        "manifest": {
            "study": STUDY,
            "version": STUDY_VERSION,
            "generated_utc": datetime.now(tz=UTC).isoformat(),
            "v11_strategy_hash": cfg.strategy_hash,
            "code_version": code_version(),
            "dataset_hash_1m": dataset_hash_1m(data_dir),
            "windows": meta,
            "locked_holdout": f"not read (every load capped at {w.data_cap})",
            "kept_identical": "features, score model, thresholds, risk, exits, costs (primary)",
            "selection_bias": "multi-variant search on consumed data; best-variant numbers are optimistic",
        },
        "variants": [asdict(v) for v in variants],
        "results": results,
        "c_conflict": conflict,
    }
    (out_dir / "study.json").write_text(json.dumps(_clean(out), indent=1, sort_keys=True))
    return out


def merge_results(path: Path, extra: dict[str, Any]) -> dict[str, Any]:
    """Append combination results to an existing study.json (read, merge, rewrite the study file only)."""
    cur = json.loads(path.read_text())
    cur["variants"] += extra["variants"]
    cur["results"].update(extra["results"])
    cur["manifest"]["combinations_generated_utc"] = extra["manifest"]["generated_utc"]
    path.write_text(json.dumps(_clean(cur), indent=1, sort_keys=True))
    return dict(cur)


__all__ = ["MS_1M", "VARIANTS", "GatingEngine", "Variant", "run_study", "run_variant"]
