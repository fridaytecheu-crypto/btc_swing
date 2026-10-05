"""V2 research configuration (strict schema + hash). Every value is pre-declared; none is searched.

V2 never changes the V1 execution framework: it loads the frozen V1 configuration from
`v1_config_path` and records its hash. V2-specific settings only describe the research protocol
(candidate window, labels, walk-forward folds, fixed model hyper-parameters, slices, criteria).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from btc_swing.core.hashing import sha256_text

V2_VERSION = "2.0.0-research"
FEATURE_SET_VERSION = "v2-fs-1"
LABEL_VERSION = "v2-label-1"
CANDIDATE_RULE_VERSION = "v2-cand-1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CandidateCfg(_Strict):
    start: str = "2022-01-01"
    end_exclusive: str = "2026-10-01"
    # after a candidate is recorded the family observes the V1 post-close cooldown (12 bars)
    cooldown_after_candidate_bars: int = Field(ge=0, default=12)


class LabelCfg(_Strict):
    clip_low: float = -2.0
    clip_high: float = 4.0


class WalkForwardCfg(_Strict):
    first_test_start: str = "2023-01-01"
    block_months: int = Field(ge=1, le=12, default=3)
    min_train_rows: int = Field(ge=20, default=100)


class HgbCfg(_Strict):
    max_depth: int = 3
    learning_rate: float = 0.05
    max_iter: int = 200
    min_samples_leaf: int = 50
    l2_regularization: float = 1.0


class ModelCfg(_Strict):
    logistic_c: float = 1.0
    ridge_alpha: float = 10.0
    hgb: HgbCfg = HgbCfg()
    # M3 gate: M1 Spearman > 0 AND top-quartile gain over all candidates > this (R)
    gate_min_top_quartile_gain_r: float = 0.05


class CriteriaCfg(_Strict):
    top_slice: float = 0.25
    spearman_fold_share: float = 0.7
    min_candidates_per_fold_for_spearman: int = 20
    decile_monotonicity: float = 0.6
    gain_vs_all_r: float = 0.10
    min_trades_per_month: float = 2.0
    stability_fold_share: float = 0.6
    min_selected_per_fold: int = 5


class V2Config(_Strict):
    name: str = "btc_swing_v2_ranking"
    v1_config_path: str = "config/btc_swing.default.yaml"
    candidates: CandidateCfg = CandidateCfg()
    labels: LabelCfg = LabelCfg()
    walkforward: WalkForwardCfg = WalkForwardCfg()
    models: ModelCfg = ModelCfg()
    slices: list[float] = [0.5, 0.25, 0.10, 0.05]
    top_n_per_month: list[int] = [5, 10]
    null_k: int = Field(ge=1, default=5)
    seed: int = 7
    criteria: CriteriaCfg = CriteriaCfg()

    def canonical_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=True)

    @property
    def config_hash(self) -> str:
        return sha256_text(self.canonical_yaml())


DEFAULT_V2_CONFIG_PATH = Path("config/btc_swing_v2.default.yaml")


def load_v2_config(path: str | Path | None = None) -> V2Config:
    p = Path(path) if path else DEFAULT_V2_CONFIG_PATH
    raw: dict[str, Any] = {}
    if p.exists():
        with p.open() as f:
            raw = yaml.safe_load(f) or {}
    return V2Config.model_validate(raw)
