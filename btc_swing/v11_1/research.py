"""V11.1 historical experiment: ONE run of the frozen specification on the V11 TRAIN and VALIDATION
windows (V11 statistics / breakdown functions reused read-only), the pre-registered classification
(design section 10) and the V11 benchmark read from the committed V11 report (read-only)."""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from btc_swing.core.versions import code_version
from btc_swing.v7.ingest import dataset_hash_1m
from btc_swing.v11.data import DAY_MS, load_inputs, ms
from btc_swing.v11.features import build_frame_v11
from btc_swing.v11.research import WindowResult, _clean, breakdowns, trade_stats
from btc_swing.v11.scoring import score_frame
from btc_swing.v11_1.config import V111_VERSION, V111Config
from btc_swing.v11_1.engine import V111Engine
from btc_swing.v11_1.evidence import evidence_frame

log = logging.getLogger(__name__)
V11_REPORT_JSON = Path("reports/BTC_V11_FAST_MULTIFACTOR_BACKTEST.json")


def run_window(cfg: V111Config, data_dir: Path, name: str, start: str, end: str) -> WindowResult:
    base = cfg.base
    a, b = ms(start), ms(end)
    inp = load_inputs(base, data_dir, a, b)
    if inp.rows.is_empty():
        raise RuntimeError("no 1m rows for the window")
    fr = build_frame_v11(inp.rows, base, inp.metrics, inp.funding)
    sc = score_frame(fr, base)
    sc.update(evidence_frame(fr, cfg))
    ft = inp.funding["time_ms"].to_numpy().astype(np.int64) if not inp.funding.is_empty() else None
    frr = (
        inp.funding["funding_rate"].to_numpy().astype(np.float64)
        if not inp.funding.is_empty()
        else None
    )
    days = max(1, round((b - a) / DAY_MS))
    i0 = int(np.searchsorted(fr.t_open, a))
    sl, ss = sc["score_long"][i0:], sc["score_short"][i0:]
    el, es = sc["evid_n_long"][i0:], sc["evid_n_short"][i0:]
    score_dist = {
        "long_p50_p90_p99": [float(x) for x in np.nanpercentile(sl, [50, 90, 99])],
        "short_p50_p90_p99": [float(x) for x in np.nanpercentile(ss, [50, 90, 99])],
        "share_ge_entry": float(
            np.mean((sl >= base.thresholds.entry) | (ss >= base.thresholds.entry))
        ),
        "share_ge_entry_and_evidence": float(
            np.mean(
                ((sl >= base.thresholds.entry) & (el >= cfg.quality.min_evidence_groups))
                | ((ss >= base.thresholds.entry) & (es >= cfg.quality.min_evidence_groups))
            )
        ),
        "evidence_n_mean_long": float(np.nanmean(el)),
        "evidence_n_mean_short": float(np.nanmean(es)),
        "available_weight": float(np.nanmedian(sc["avail_weight_long"][i0:])),
    }
    results = {}
    for tag, mult in (("primary", 1.0), ("stress", base.costs.stress_multiplier)):
        eng = V111Engine(cfg, fr, sc, mult, ft, frr)
        eng.run(a, b)
        st = trade_stats(
            eng.trades, days, base.risk.initial_equity, dict(eng.counters), dict(eng.blockers)
        )
        st["quality_rejections"] = dict(eng.quality_rejections)
        results[tag] = (eng, st)
        log.info(
            "%s %s: trades %d (%.2f/day) net %.3fR pf %.2f cost %.3fR",
            name,
            tag,
            len(eng.trades),
            len(eng.trades) / days,
            st.get("net_r", math.nan),
            st.get("profit_factor", math.nan),
            st.get("cost_r", math.nan),
        )
    eng = results["primary"][0]
    st = results["primary"][1]
    st["by_family_funnel"] = eng.by_family
    ev = [float(t.decision["evidence_n"]) for t in eng.trades if t.decision.get("evidence_n")]
    st["evidence_n_mean_executed"] = float(np.mean(ev)) if ev else math.nan
    st["evidence_group_frequency"] = {
        g: int(sum(1 for t in eng.trades if g in (t.decision.get("evidence_groups") or [])))
        for g in cfg.quality.directional_groups
    }
    return WindowResult(
        name,
        st,
        results["stress"][1],
        breakdowns(eng.trades),
        [_clean(t.as_dict()) for t in eng.trades],
        fr.meta,
        score_dist,
    )


def classify(cfg: V111Config, res: dict[str, WindowResult]) -> dict[str, Any]:
    su = cfg.success
    tr, va = res["train"].stats, res["validation"].stats

    def g(st: dict[str, Any], k: str) -> float:
        v = st.get(k)
        return float(v) if v is not None else math.nan

    def primary(st: dict[str, Any]) -> bool:
        return g(st, "net_r") > 0 and g(st, "profit_factor") > su.primary_min_pf

    val_edge = g(va, "net_r") > 0 and g(va, "profit_factor") > 1.0
    secondary = {
        "gross_r_gt_0.10": g(va, "gross_r") > su.secondary_min_gross_r,
        "cost_r_lt_0.25": g(va, "cost_r") < su.secondary_max_cost_r,
        "trades_per_day_ge_1.5": g(va, "trades_per_day") >= su.secondary_min_trades_per_day,
        "median_hold_le_45": g(va, "median_hold_min") <= su.secondary_max_median_hold_min,
    }
    fam = res["validation"].breakdown.get("family", {})
    total = g(va, "total_net_r")
    shares = {
        f: (float(d.get("total_net_r", 0.0)) / total if total > 0 else math.nan)
        for f, d in fam.items()
    }
    single_family = total > 0 and any(
        s > su.max_single_family_share for s in shares.values() if math.isfinite(s)
    )
    if not val_edge:
        cls, why = (
            "C",
            "validation net R <= 0 or PF <= 1 after primary costs: STOP, no shadow runner",
        )
    elif primary(va) and primary(tr) and all(secondary.values()) and not single_family:
        cls, why = (
            "A",
            "primary criteria on VALIDATION and TRAIN, all secondary criteria, no single-family dependence",
        )
    else:
        cls, why = (
            "B",
            "validation net positive but not every A condition holds (train primary, PF > 1.05, secondary criteria, family concentration)",
        )
    return {
        "classification": cls,
        "reason": why,
        "validation_primary": primary(va),
        "train_primary": primary(tr),
        "validation_net_positive": val_edge,
        "secondary": secondary,
        "family_share_of_validation_net": shares,
        "single_family_dependence": single_family,
        "shadow_justified": cls in ("A", "B") and val_edge,
    }


def v11_benchmark() -> dict[str, Any]:
    """The committed V11 result (read-only); falls back to the owner's quoted validation numbers."""
    if V11_REPORT_JSON.exists():
        r = json.loads(V11_REPORT_JSON.read_text())
        return {k: r["windows"][k]["stats"] for k in ("train", "validation")}
    return {
        "train": {},
        "validation": {
            "trades_per_day": 2.67,
            "gross_r": -0.051,
            "cost_r": 0.364,
            "net_r": -0.415,
            "profit_factor": 0.19,
            "median_hold_min": 20,
        },
    }


def run_research(cfg: V111Config, data_dir: Path, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    w = cfg.base.windows
    res = {
        "train": run_window(cfg, data_dir, "train", w.train.start, w.train.end_exclusive),
        "validation": run_window(
            cfg, data_dir, "validation", w.validation.start, w.validation.end_exclusive
        ),
    }
    manifest = {
        "generated_utc": datetime.now(tz=UTC).isoformat(),
        "v11_1_version": V111_VERSION,
        "config_hash": cfg.config_hash,
        "strategy_hash": cfg.strategy_hash,
        "base_v11_strategy_hash": cfg.base.strategy_hash,
        "code_version": code_version(),
        "dataset_hash_1m": dataset_hash_1m(data_dir),
        "windows": {
            "train": [w.train.start, w.train.end_exclusive],
            "validation": [w.validation.start, w.validation.end_exclusive],
        },
        "locked_holdout": "not read (every load capped at " + w.data_cap + ")",
        "quality": json.loads(cfg.quality.model_dump_json()),
        "cost_filter": json.loads(cfg.cost_filter.model_dump_json()),
        "stop": {"floor_pct": cfg.base.stop.floor_pct, "cap_pct": cfg.base.stop.cap_pct},
    }
    out = {
        "manifest": manifest,
        "windows": {
            k: {
                "stats": v.stats,
                "stress": v.stress,
                "breakdown": v.breakdown,
                "frame": v.frame_meta,
                "score_dist": v.score_dist,
            }
            for k, v in res.items()
        },
        "v11_benchmark": v11_benchmark(),
        "classification": classify(cfg, res),
    }
    (out_dir / "backtest.json").write_text(json.dumps(_clean(out), indent=1, sort_keys=True))
    for k, v in res.items():
        (out_dir / f"trades_{k}.json").write_text(json.dumps(_clean(v.trades)))
    return out
