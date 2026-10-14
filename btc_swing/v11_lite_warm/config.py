"""V11-LITE-WARM configuration: the frozen V11 strategy object (hash-checked against the V11 freeze) +
the V11-WARM warm-start section + the `lite` section that names the ONE strategy delta (family structure
as labels only) and the read-only telemetry / checkpoint definitions. Shadow only."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from btc_swing.v11.config import V11Config, load_v11_config, strategy_hash_of
from btc_swing.v11_warm.config import WarmCfg

V11LW_VERSION = "11.0.0-lite-warm"
STRATEGY = "BTC_V11_LITE_WARM_SHADOW"
STRATEGY_DELTA = (
    "V11Engine.structure(fam, side, i) no longer gates arming: it returns True whenever close and ATR5 "
    "are finite (data validity only); the frozen A/B/C structure conditions are computed by the "
    "unchanged V11Engine.structure and journaled as context labels. Everything else (features, score, "
    "contributions, live microstructure, candidate / armed / entry thresholds, family triggers, stop "
    "geometry, TP1 / TP2 / runner / break-even / stall / hard stop, risk, costs, warm-start, seam healer, "
    "derivatives inputs) is V11's own frozen code and config."
)
PREFIX_SIGNAL = "V11LWS-"
DEFAULT_CONFIG = Path("config/btc_swing_v11_lite_warm.yaml")
V11_FREEZE_PATH = Path("manifests/v11_forward_freeze.json")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CheckpointCfg(_Strict):
    name: str
    reached_when_any: dict[str, float]
    require: list[str]


class LiteCfg(_Strict):
    family_structure: Literal["labels_only"]
    outcome_horizons_min: list[int] = Field(min_length=1)
    fib_ratios: list[float] = Field(min_length=1)
    fib_swing_tf_min: int = Field(ge=1, le=60)
    fib_swing_lookback_min: int = Field(ge=60, le=10080)
    fib_bucket_edges_atr: list[float] = Field(min_length=1)
    range_minutes: list[int] = Field(min_length=1)
    preliminary_min_n: int = Field(ge=1)
    checkpoints: list[CheckpointCfg]


class V11LiteWarmConfig(_Strict):
    strategy_name: str
    v11_config: str
    warm: WarmCfg
    lite: LiteCfg

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


def lite_strategy_hash(v11: V11Config, lc: V11LiteWarmConfig) -> str:
    """sha256 over the V11 strategy sections + the lite delta name: pins 'V11 strategy X with the
    family structure as labels'. Telemetry / checkpoint settings are NOT part of it."""
    payload = json.dumps(
        {
            "v11_strategy_hash": v11.strategy_hash,
            "family_structure": lc.lite.family_structure,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def load_v11lw_config(path: str | Path | None = None) -> tuple[V11LiteWarmConfig, V11Config]:
    """Returns (lite config, the V11 strategy config with the LITE outcome horizons). Refuses any
    strategy drift from the V11 freeze. `outcomes` is not a V11 strategy section, so replacing the
    horizons leaves the V11 strategy hash unchanged (asserted)."""
    p = Path(path) if path is not None else DEFAULT_CONFIG
    raw: dict[str, Any] = yaml.safe_load(p.read_text())
    lc = V11LiteWarmConfig.model_validate(raw)
    if lc.strategy_name != STRATEGY:
        raise ValueError(f"V11-LITE-WARM config must be named {STRATEGY}")
    v11_base = load_v11_config(lc.v11_config)
    frozen = frozen_v11_strategy_hash()
    if frozen is not None and frozen != v11_base.strategy_hash:
        raise RuntimeError("V11 strategy sections differ from manifests/v11_forward_freeze.json")
    if lc.warm.window_days <= v11_base.features.rvol_baseline_days + 1:
        raise ValueError("window_days must exceed the RVOL baseline by at least two days")
    if lc.warm.bootstrap_days < v11_base.features.rvol_baseline_days + 1:
        raise ValueError("bootstrap_days must cover the RVOL baseline plus one day")
    v11 = v11_base.model_copy(
        update={
            "outcomes": v11_base.outcomes.model_copy(
                update={"horizons_min": list(lc.lite.outcome_horizons_min)}
            )
        }
    )
    if v11.strategy_hash != v11_base.strategy_hash:
        raise RuntimeError("outcome horizons changed the V11 strategy hash; refusing")
    return lc, v11
