"""Ranking evaluation, selectivity slices, sequential single-slot account, baselines and the
pre-declared V2 classification. Pure functions of data frames; thresholds come from the config.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

from btc_swing.research.phase24 import _by, _stats
from btc_swing.v2.config import V2Config

MONTH_MS = 30.4375 * 86_400_000


def _f(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return math.nan
    return float(spearmanr(a, b).statistic)


def auc(y: np.ndarray, s: np.ndarray) -> float:
    if len(np.unique(y)) < 2:
        return math.nan
    return float(roc_auc_score(y, s))


# --------------------------------------------------------------------------- ranking
def rank_metrics(df: pl.DataFrame, score_col: str = "score") -> dict[str, Any]:
    """df: evaluable rows with `score`, `R_MULTIPLE`, `fold`, `trigger_ms`."""
    if df.is_empty():
        return {"n": 0}
    s = df[score_col].to_numpy().astype(float)
    r = df["R_MULTIPLE"].to_numpy().astype(float)
    y = (r > 0).astype(float)
    out: dict[str, Any] = {
        "n": df.height,
        "spearman": spearman(s, r),
        "auc_pos": auc(y, s),
        "mean_R": float(r.mean()),
        "base_rate_pos": float(y.mean()),
    }
    per_fold: list[dict[str, Any]] = []
    for k in sorted(df["fold"].unique().to_list()):
        sub = df.filter(pl.col("fold") == k)
        ss, rr = sub[score_col].to_numpy().astype(float), sub["R_MULTIPLE"].to_numpy().astype(float)
        per_fold.append(
            {
                "fold": int(k),
                "n": sub.height,
                "spearman": spearman(ss, rr),
                "auc_pos": auc((rr > 0).astype(float), ss),
                "mean_R": float(rr.mean()),
            }
        )
    out["per_fold"] = per_fold
    # deciles on the pooled walk-forward scores (descriptive) and per calendar year
    out["deciles"] = _deciles(df, score_col)
    dm = out["deciles"]
    out["decile_monotonicity"] = (
        spearman(
            np.array([d["decile"] for d in dm], dtype=float),
            np.array([d["mean_R"] for d in dm], dtype=float),
        )
        if len(dm) >= 3
        else math.nan
    )
    out["deciles_by_year"] = {}
    yrs = df.with_columns(
        pl.col("trigger_ms")
        .map_elements(
            lambda x: datetime.fromtimestamp(int(x) / 1000, tz=UTC).year, return_dtype=pl.Int64
        )
        .alias("year")
    )
    for yv in sorted(yrs["year"].unique().to_list()):
        sub = yrs.filter(pl.col("year") == yv)
        if sub.height >= 30:
            out["deciles_by_year"][str(yv)] = _quantile_buckets(sub, score_col, 4)
    return out


def _deciles(df: pl.DataFrame, score_col: str) -> list[dict[str, Any]]:
    return _quantile_buckets(df, score_col, 10)


def _quantile_buckets(df: pl.DataFrame, score_col: str, n_buckets: int) -> list[dict[str, Any]]:
    s = df[score_col].to_numpy().astype(float)
    order = np.argsort(s, kind="stable")
    ranks = np.empty(len(s), dtype=float)
    ranks[order] = np.arange(len(s))
    bucket = np.minimum((ranks * n_buckets / max(len(s), 1)).astype(int), n_buckets - 1)
    out: list[dict[str, Any]] = []
    tmp = df.with_columns(pl.Series("bucket", bucket))
    for b in range(n_buckets):
        sub = tmp.filter(pl.col("bucket") == b)
        st = _stats(sub)
        out.append(
            {
                "decile": b + 1,
                "score_min": _f(sub[score_col].min()) if sub.height else math.nan,
                "score_max": _f(sub[score_col].max()) if sub.height else math.nan,
                **st,
            }
        )
    return out


def top_n_per_month(df: pl.DataFrame, n: int, score_col: str = "score") -> dict[str, Any]:
    """Descriptive slice (NOT PIT: uses the month's full candidate set to pick the top n)."""
    if df.is_empty():
        return {"n": 0}
    m = df.with_columns(
        pl.col("trigger_ms")
        .map_elements(
            lambda x: datetime.fromtimestamp(int(x) / 1000, tz=UTC).strftime("%Y-%m"),
            return_dtype=pl.Utf8,
        )
        .alias("month")
    )
    picked = (
        m.sort(["month", score_col], descending=[False, True])
        .with_columns(pl.int_range(pl.len()).over("month").alias("rank_in_month"))
        .filter(pl.col("rank_in_month") < n)
    )
    return {"top_n": n, "months": m["month"].n_unique(), **_stats(picked)}


# --------------------------------------------------------------------------- slices / account
def sequential_account(
    sel: pl.DataFrame, initial_equity: float, start_ms: int, end_ms: int
) -> dict[str, Any]:
    """Single-slot sequential account over selected candidates (fixed research equity sizing, as
    V1): a candidate is taken only if no position is open at its decision time."""
    if sel.is_empty():
        return {"n_trades": 0, "trades_per_month": 0.0}
    t = sel.sort("trigger_ms")
    taken: list[int] = []
    busy_until = -1
    for row in t.select("candidate_id", "trigger_ms", "exit_ms").iter_rows():
        if int(row[1]) >= busy_until:
            taken.append(int(row[0]))
            busy_until = int(row[2])
    tr = t.filter(pl.col("candidate_id").is_in(taken)).sort("trigger_ms")
    pnl = tr["POSITION_PNL"].to_numpy().astype(float)
    curve = np.concatenate([[initial_equity], initial_equity + np.cumsum(pnl)])
    peak = np.maximum.accumulate(curve)
    dd = (curve - peak) / peak
    months = (end_ms - start_ms) / MONTH_MS
    r = tr["R_MULTIPLE"].to_numpy().astype(float)
    gw, gl = float(pnl[pnl > 0].sum()), float(-pnl[pnl <= 0].sum())
    streak = cur = 0
    for x in pnl:
        cur = cur + 1 if x <= 0 else 0
        streak = max(streak, cur)
    return {
        "n_candidates_selected": sel.height,
        "n_trades": tr.height,
        "trades_per_month": tr.height / months if months > 0 else math.nan,
        "candidates_per_month": sel.height / months if months > 0 else math.nan,
        "mean_R": float(r.mean()) if len(r) else math.nan,
        "gross_mean_R": _f(tr["R_MULTIPLE_GROSS"].mean()) if tr.height else math.nan,
        "win_rate": float((pnl > 0).mean()) if len(pnl) else math.nan,
        "profit_factor": gw / gl if gl > 0 else math.inf,
        "net_pnl": float(pnl.sum()),
        "fees": _f(tr["fees"].sum()),
        "slippage": _f(tr["slippage"].sum()),
        "funding": _f(tr["funding"].sum()),
        "total_costs": _f(tr["fees"].sum()) + _f(tr["slippage"].sum()) - _f(tr["funding"].sum()),
        "total_return": float(curve[-1] / initial_equity - 1.0),
        "max_drawdown": float(dd.min()),
        "longest_losing_streak": streak,
        "t_stat": float(r.mean() / (r.std(ddof=1) / math.sqrt(len(r))))
        if len(r) > 2 and r.std(ddof=1) > 0
        else math.nan,
        "n_long": int((tr["side"] == "LONG").sum()),
        "n_short": int((tr["side"] == "SHORT").sum()),
    }


def slice_table(
    df: pl.DataFrame,
    cfg: V2Config,
    initial_equity: float,
    start_ms: int,
    end_ms: int,
    score_col: str = "score",
) -> list[dict[str, Any]]:
    """PIT slices: a row is selected when its score >= the fold's training-score quantile."""
    rows: list[dict[str, Any]] = []
    rows.append(
        {
            "slice": "all candidates",
            "q": 1.0,
            **_stats(df),
            "account": sequential_account(df, initial_equity, start_ms, end_ms),
        }
    )
    for q in cfg.slices:
        col = f"thr_{int(q * 100):02d}"
        sel = df.filter(pl.col(score_col) >= pl.col(col)) if col in df.columns else df.head(0)
        rows.append(
            {
                "slice": f"top {int(q * 100)}%",
                "q": q,
                **_stats(sel),
                "account": sequential_account(sel, initial_equity, start_ms, end_ms),
            }
        )
    return rows


def slice_by_half(
    df: pl.DataFrame, q: float, split_ms: int, score_col: str = "score"
) -> dict[str, Any]:
    col = f"thr_{int(q * 100):02d}"
    sel = df.filter(pl.col(score_col) >= pl.col(col))
    return {
        "first_half": _stats(sel.filter(pl.col("trigger_ms") < split_ms)),
        "second_half": _stats(sel.filter(pl.col("trigger_ms") >= split_ms)),
        "all_first_half": _stats(df.filter(pl.col("trigger_ms") < split_ms)),
        "all_second_half": _stats(df.filter(pl.col("trigger_ms") >= split_ms)),
    }


def slice_per_fold(df: pl.DataFrame, q: float, score_col: str = "score") -> list[dict[str, Any]]:
    col = f"thr_{int(q * 100):02d}"
    out: list[dict[str, Any]] = []
    for k in sorted(df["fold"].unique().to_list()):
        sub = df.filter(pl.col("fold") == k)
        sel = sub.filter(pl.col(score_col) >= pl.col(col))
        out.append(
            {
                "fold": int(k),
                "n_all": sub.height,
                "mean_R_all": _f(sub["R_MULTIPLE"].mean()),
                "n_selected": sel.height,
                "mean_R_selected": _f(sel["R_MULTIPLE"].mean()) if sel.height else math.nan,
                "pnl_selected": _f(sel["POSITION_PNL"].sum()) if sel.height else 0.0,
            }
        )
    return out


def breakdown(
    df: pl.DataFrame, q: float, col: str, score_col: str = "score"
) -> list[dict[str, Any]]:
    thr = f"thr_{int(q * 100):02d}"
    sel = df.filter(pl.col(score_col) >= pl.col(thr))
    all_rows = {d[col]: d for d in _by(df, col)}
    sel_rows = {d[col]: d for d in _by(sel, col)}
    out: list[dict[str, Any]] = []
    for k in sorted(all_rows):
        a, s = all_rows[k], sel_rows.get(k, {"n": 0})
        out.append(
            {
                col: k,
                "all": a,
                "selected": s,
                "selected_share": s.get("n", 0) / a["n"] if a["n"] else math.nan,
            }
        )
    return out


def derivatives_diagnostics(df: pl.DataFrame, features: list[str]) -> list[dict[str, Any]]:
    r = df["R_MULTIPLE"].to_numpy().astype(float)
    out: list[dict[str, Any]] = []
    for fname in features:
        col = f"x_{fname}"
        if col not in df.columns:
            continue
        x = df[col].to_numpy().astype(float)
        ok = ~np.isnan(x)
        out.append(
            {
                "feature": fname,
                "n": int(ok.sum()),
                "spearman_vs_net_R": spearman(x[ok], r[ok]) if ok.sum() > 10 else math.nan,
                "missing_share": float(1 - ok.mean()),
            }
        )
    return out


# --------------------------------------------------------------------------- criteria
def evaluate_criteria(
    rm: dict[str, Any],
    slices: list[dict[str, Any]],
    halves: dict[str, Any],
    per_fold: list[dict[str, Any]],
    null_summary: dict[str, Any],
    cfg: V2Config,
) -> list[dict[str, Any]]:
    c = cfg.criteria
    empty: dict[str, Any] = {"n": 0, "account": {}}
    top: dict[str, Any] = next((s for s in slices if abs(s["q"] - c.top_slice) < 1e-9), empty)
    allc: dict[str, Any] = next((s for s in slices if s["q"] == 1.0), empty)
    pf_folds = [
        d
        for d in rm.get("per_fold", [])
        if d["n"] >= c.min_candidates_per_fold_for_spearman and not math.isnan(d["spearman"])
    ]
    share_pos = float(np.mean([d["spearman"] > 0 for d in pf_folds])) if pf_folds else math.nan
    sp = _f(rm.get("spearman", math.nan))
    mono = _f(rm.get("decile_monotonicity", math.nan))
    top_mean, top_pf = _f(top.get("mean_R", math.nan)), _f(top.get("profit_factor", math.nan))
    all_mean = _f(allc.get("mean_R", math.nan))
    h1, h2 = (
        halves.get("first_half", {}).get("mean_R", math.nan),
        halves.get("second_half", {}).get("mean_R", math.nan),
    )
    null_time = (null_summary.get("time") or {}).get("null_mean_R", math.nan)
    null_reg = (null_summary.get("regime") or {}).get("null_mean_R", math.nan)
    tpm = top.get("account", {}).get("trades_per_month", math.nan)
    stab = [d for d in per_fold if d["n_selected"] >= c.min_selected_per_fold]
    stab_share = float(np.mean([d["mean_R_selected"] > 0 for d in stab])) if stab else math.nan

    def ok(x: float) -> bool:
        return bool(not math.isnan(x))

    return [
        {
            "id": 1,
            "text": f"Spearman(score, net R) > 0 overall and > 0 in >= {c.spearman_fold_share:.0%} of folds with >= {c.min_candidates_per_fold_for_spearman} candidates",
            "met": ok(sp) and sp > 0 and ok(share_pos) and share_pos >= c.spearman_fold_share,
            "evidence": f"overall {sp:+.3f}; positive in {share_pos:.0%} of {len(pf_folds)} folds",
        },
        {
            "id": 2,
            "text": f"decile monotonicity: Spearman(decile, decile mean net R) >= {c.decile_monotonicity}",
            "met": ok(mono) and mono >= c.decile_monotonicity,
            "evidence": f"{mono:+.2f}",
        },
        {
            "id": 3,
            "text": f"PIT top-{int(c.top_slice * 100)}% slice: net expectancy > 0 and PF > 1 overall, and net expectancy > 0 in both halves",
            "met": ok(top_mean)
            and top_mean > 0
            and top_pf > 1
            and ok(h1)
            and h1 > 0
            and ok(h2)
            and h2 > 0,
            "evidence": f"mean {top_mean:+.3f}R, PF {top_pf:.2f}, halves {h1:+.3f}R / {h2:+.3f}R (n={top.get('n', 0)})",
        },
        {
            "id": 4,
            "text": f"top slice beats all candidates by >= +{c.gain_vs_all_r:.2f}R and beats both null means",
            "met": ok(top_mean)
            and ok(all_mean)
            and top_mean - all_mean >= c.gain_vs_all_r
            and ok(null_time)
            and top_mean > null_time
            and ok(null_reg)
            and top_mean > null_reg,
            "evidence": f"top {top_mean:+.3f}R vs all {all_mean:+.3f}R (gain {top_mean - all_mean:+.3f}R); null time {null_time:+.3f}R, regime {null_reg:+.3f}R",
        },
        {
            "id": 5,
            "text": f"top slice after single-slot sequencing yields >= {c.min_trades_per_month} trades/month",
            "met": ok(tpm) and tpm >= c.min_trades_per_month,
            "evidence": f"{tpm:.2f} trades/month ({top.get('account', {}).get('n_trades', 0)} trades)",
        },
        {
            "id": 6,
            "text": f"stability: top slice positive in >= {c.stability_fold_share:.0%} of folds with >= {c.min_selected_per_fold} selected",
            "met": ok(stab_share) and stab_share >= c.stability_fold_share,
            "evidence": f"positive in {stab_share:.0%} of {len(stab)} folds",
        },
    ]


def classify(criteria: list[dict[str, Any]], top_mean: float, all_mean: float) -> str:
    met = {c["id"]: bool(c["met"]) for c in criteria}
    if all(met.values()):
        return "A — RANKING EDGE DEMONSTRATED, FREEZE FOR FORWARD PAPER TEST"
    if met[1] and not math.isnan(top_mean) and not math.isnan(all_mean) and top_mean > all_mean:
        return "B — SOME SIGNAL, MORE CONTROLLED RESEARCH REQUIRED"
    return "C — NO USEFUL RANKING EDGE"
