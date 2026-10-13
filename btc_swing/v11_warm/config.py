"""V11-WARM configuration: the frozen V11 strategy (loaded from the V11 config file, hash-checked
against the V11 freeze) plus the operational warm-start section. NOT a new strategy: every
threshold, weight, family, stop, exit and risk rule is V11's own object."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from btc_swing.v11.config import V11Config, load_v11_config, strategy_hash_of

V11W_VERSION = "11.0.0-warm-start"
STRATEGY = "BTC_V11_WARM_SHADOW"
DEFAULT_CONFIG = Path("config/btc_swing_v11_warm.yaml")
V11_FREEZE_PATH = Path("manifests/v11_forward_freeze.json")
BOOTSTRAP_PARQUET = Path("manifests/v11_warm_bootstrap_1m.parquet")
BOOTSTRAP_MANIFEST = Path("manifests/v11_warm_bootstrap_manifest.json")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WarmCfg(_Strict):
    bootstrap_days: int = Field(ge=21, le=60)
    window_days: int = Field(ge=22, le=62)
    live_warmup_min: int = Field(ge=0, le=240)
    gapfill_max_hours: int = Field(ge=0, le=48)
    required_warm_fields: list[str]
    required_warm_groups: list[str]


class V11WarmConfig(_Strict):
    strategy_name: str
    v11_config: str
    warm: WarmCfg

    def canonical_yaml(self) -> str:
        return yaml.safe_dump(json.loads(self.model_dump_json()), sort_keys=True)

    @property
    def config_hash(self) -> str:
        return hashlib.sha256(self.canonical_yaml().encode()).hexdigest()


def frozen_v11_strategy_hash(path: Path = V11_FREEZE_PATH) -> str | None:
    if not path.exists():
        return None
    rec = json.loads(path.read_text())
    return strategy_hash_of(yaml.safe_load(rec["config_yaml"]))


def load_v11w_config(path: str | Path | None = None) -> tuple[V11WarmConfig, V11Config]:
    """Returns (warm config, the V11 strategy config). Refuses any strategy drift from the V11 freeze."""
    p = Path(path) if path is not None else DEFAULT_CONFIG
    raw: dict[str, Any] = yaml.safe_load(p.read_text())
    wc = V11WarmConfig.model_validate(raw)
    if wc.strategy_name != STRATEGY:
        raise ValueError(f"V11-WARM config must be named {STRATEGY}")
    v11 = load_v11_config(wc.v11_config)
    frozen = frozen_v11_strategy_hash()
    if frozen is not None and frozen != v11.strategy_hash:
        raise RuntimeError("V11 strategy sections differ from manifests/v11_forward_freeze.json")
    if wc.warm.window_days <= v11.features.rvol_baseline_days + 1:
        raise ValueError("window_days must exceed the RVOL baseline by at least two days")
    if wc.warm.bootstrap_days < v11.features.rvol_baseline_days + 1:
        raise ValueError("bootstrap_days must cover the RVOL baseline plus one day")
    return wc, v11
