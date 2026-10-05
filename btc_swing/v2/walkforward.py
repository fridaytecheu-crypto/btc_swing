"""Chronological walk-forward fitting of the pre-declared models.

Folds are calendar blocks (default quarterly) from `first_test_start`. For a block starting at S
the training set is every labelled candidate with decision time < S whose simulated exit time
<= S (its label is known at S). Models, imputers and scalers are fitted on the training set only.
The selectivity thresholds for the block are quantiles of the model's scores on its own training
set (PIT); they are attached to every test row so the evaluation never uses test information to
select. Every candidate from `first_test_start` on receives exactly one prediction.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

from btc_swing.v2.config import V2Config

log = logging.getLogger(__name__)


def _ms(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp() * 1000)


def _add_months(dt: datetime, months: int) -> datetime:
    y, m = dt.year, dt.month + months
    while m > 12:
        y, m = y + 1, m - 12
    return dt.replace(year=y, month=m)


@dataclass(frozen=True)
class Fold:
    index: int
    test_start_ms: int
    test_end_ms: int

    @property
    def name(self) -> str:
        d = datetime.fromtimestamp(self.test_start_ms / 1000, tz=UTC)
        return (
            f"{d.year}-Q{(d.month - 1) // 3 + 1}"
            if d.month in (1, 4, 7, 10)
            else d.date().isoformat()
        )


def make_folds(first_test_start: str, end_exclusive: str, block_months: int) -> list[Fold]:
    start = datetime.fromisoformat(first_test_start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(end_exclusive).replace(tzinfo=UTC)
    folds: list[Fold] = []
    k = 0
    cur = start
    while cur < end:
        nxt = min(_add_months(cur, block_months), end)
        folds.append(Fold(k, int(cur.timestamp() * 1000), int(nxt.timestamp() * 1000)))
        cur = nxt
        k += 1
    return folds


@dataclass
class ModelSpec:
    name: str  # M1_logistic | M2_ridge | M3_hgb | M1_no_derivatives
    kind: str  # classifier | regressor
    features: list[str]


def _make_model(spec: ModelSpec, cfg: V2Config) -> Pipeline:
    m = cfg.models
    if spec.name.startswith("M3"):
        if spec.kind == "classifier":
            return make_pipeline(
                HistGradientBoostingClassifier(
                    max_depth=m.hgb.max_depth,
                    learning_rate=m.hgb.learning_rate,
                    max_iter=m.hgb.max_iter,
                    min_samples_leaf=m.hgb.min_samples_leaf,
                    l2_regularization=m.hgb.l2_regularization,
                    random_state=cfg.seed,
                )
            )
        return make_pipeline(
            HistGradientBoostingRegressor(
                max_depth=m.hgb.max_depth,
                learning_rate=m.hgb.learning_rate,
                max_iter=m.hgb.max_iter,
                min_samples_leaf=m.hgb.min_samples_leaf,
                l2_regularization=m.hgb.l2_regularization,
                random_state=cfg.seed,
            )
        )
    if spec.kind == "classifier":
        return make_pipeline(
            SimpleImputer(strategy="median", keep_empty_features=True),
            StandardScaler(),
            LogisticRegression(C=m.logistic_c, max_iter=5000),
        )
    return make_pipeline(
        SimpleImputer(strategy="median", keep_empty_features=True),
        StandardScaler(),
        Ridge(alpha=m.ridge_alpha),
    )


@dataclass
class WalkForwardResult:
    spec: ModelSpec
    predictions: pl.DataFrame  # candidate_id, fold, score, thr_<slice> columns
    fold_info: list[dict[str, Any]] = field(default_factory=list)
    coefficients: list[dict[str, float]] = field(default_factory=list)  # per fold (linear only)


def walk_forward(
    df: pl.DataFrame,
    spec: ModelSpec,
    cfg: V2Config,
    folds: list[Fold],
    target_col: str,
) -> WalkForwardResult:
    """df: one row per labelled candidate with `trigger_ms`, `exit_ms`, features and the target."""
    x_feat = df.select(spec.features).to_numpy().astype(np.float64)
    y_all = df[target_col].to_numpy().astype(np.float64)
    t_all = df["trigger_ms"].to_numpy().astype(np.int64)
    x_all = df["exit_ms"].to_numpy().astype(np.int64)
    ids = df["candidate_id"].to_numpy().astype(np.int64)
    out_rows: list[dict[str, Any]] = []
    info: list[dict[str, Any]] = []
    coefs: list[dict[str, float]] = []
    for fold in folds:
        tr = (t_all < fold.test_start_ms) & (x_all <= fold.test_start_ms)
        te = (t_all >= fold.test_start_ms) & (t_all < fold.test_end_ms)
        n_tr, n_te = int(tr.sum()), int(te.sum())
        rec: dict[str, Any] = {
            "fold": fold.index,
            "name": fold.name,
            "test_start": datetime.fromtimestamp(fold.test_start_ms / 1000, tz=UTC)
            .date()
            .isoformat(),
            "n_train": n_tr,
            "n_test": n_te,
            "fitted": False,
        }
        if n_tr < cfg.walkforward.min_train_rows or n_te == 0:
            info.append(rec)
            continue
        y_tr = y_all[tr]
        if spec.kind == "classifier" and len(np.unique(y_tr)) < 2:
            info.append(rec)
            continue
        model = _make_model(spec, cfg)
        model.fit(x_feat[tr], y_tr)
        score_tr = (
            model.predict_proba(x_feat[tr])[:, 1]
            if spec.kind == "classifier"
            else model.predict(x_feat[tr])
        )
        score_te = (
            model.predict_proba(x_feat[te])[:, 1]
            if spec.kind == "classifier"
            else model.predict(x_feat[te])
        )
        thr = {q: float(np.quantile(score_tr, 1.0 - q)) for q in cfg.slices}
        rec.update(
            {
                "fitted": True,
                "train_score_mean": float(np.mean(score_tr)),
                "train_target_mean": float(np.mean(y_tr)),
                **{f"thr_{int(q * 100):02d}": v for q, v in thr.items()},
            }
        )
        info.append(rec)
        est = model.steps[-1][1]
        if hasattr(est, "coef_"):
            coef = np.ravel(est.coef_)
            coefs.append(
                {
                    "fold": float(fold.index),
                    **dict(zip(spec.features, map(float, coef), strict=True)),
                }
            )
        for j, idx in enumerate(np.flatnonzero(te)):
            out_rows.append(
                {
                    "candidate_id": int(ids[idx]),
                    "fold": fold.index,
                    "score": float(score_te[j]),
                    **{f"thr_{int(q * 100):02d}": v for q, v in thr.items()},
                }
            )
    preds = pl.DataFrame(out_rows) if out_rows else pl.DataFrame()
    log.info("%s: %d predictions over %d folds", spec.name, preds.height, len(folds))
    return WalkForwardResult(spec, preds, info, coefs)


def coefficient_stability(
    coefs: list[dict[str, float]], features: list[str]
) -> list[dict[str, Any]]:
    if not coefs:
        return []
    out: list[dict[str, Any]] = []
    for fname in features:
        vals = np.array([c[fname] for c in coefs], dtype=float)
        if len(vals) == 0:
            continue
        sign_share = (
            float(np.mean(np.sign(vals) == np.sign(np.mean(vals)))) if np.mean(vals) != 0 else 0.5
        )
        out.append(
            {
                "feature": fname,
                "mean_coef": float(np.mean(vals)),
                "std_coef": float(np.std(vals, ddof=1)) if len(vals) > 1 else math.nan,
                "sign_consistency": sign_share,
                "abs_mean": float(abs(np.mean(vals))),
            }
        )
    out.sort(key=lambda d: -d["abs_mean"])
    return out
