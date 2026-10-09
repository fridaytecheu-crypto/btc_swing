"""V5.1 freeze manifest (manifests/v5_1_forward_freeze.json): the V5.1 config hash, the frozen V5
parent hash, rule versions, the data-quality version, the forward config hash, code commit and
the V5.1 observation start (= freeze time; no retrospective V5.1 signal before it). Separate from
and never touching the V5 freeze."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from btc_swing.core.versions import code_version
from btc_swing.v5.config import V5_EVENT_RULE_VERSION, V5_EXIT_RULE_VERSION, V5Config
from btc_swing.v5.forward.config import ForwardConfig
from btc_swing.v51.config import (
    EXPECTED_V51_CONFIG_HASH,
    PARENT_V5_CONFIG_HASH,
    V51_DATA_QUALITY_VERSION,
    V51_FEATURE_VERSION,
    V51_VERSION,
    V51Config,
    assert_strategy_identical,
)

FREEZE_PATH_V51 = Path("manifests/v5_1_forward_freeze.json")


def freeze_record_v51(
    cfg: V51Config,
    v5: V5Config,
    fcfg: ForwardConfig,
    parent_freeze: dict[str, Any] | None,
    start_ms: int | None = None,
) -> dict[str, Any]:
    info = assert_strategy_identical(cfg, v5)
    now = datetime.now(UTC)
    start = start_ms if start_ms is not None else int(now.timestamp() * 1000)
    pf = parent_freeze or {}
    return {
        "project": "btc_swing_v5_1_data_quality_fix_forward_observation",
        "version": V51_VERSION,
        "mode": "FORWARD OBSERVATION + owner-activated STRATEGY_DEMO (Bybit DEMO only)",
        "v51_config_hash": cfg.config_hash,
        "parent_v5_config_hash": v5.config_hash,
        "parent_v5_freeze": {
            "observation_start": pf.get("observation_start"),
            "observation_start_ms": pf.get("observation_start_ms"),
            "frozen_at": pf.get("frozen_at"),
        },
        "strategy_sections_sha256": info["strategy_sections_sha256"],
        "rule_versions": {
            "features": V51_FEATURE_VERSION,
            "events": V5_EVENT_RULE_VERSION,
            "exits": V5_EXIT_RULE_VERSION,
            "data_quality": V51_DATA_QUALITY_VERSION,
        },
        "forward_config_hash": fcfg.config_hash,
        "code_version": code_version(),
        "frozen_at": now.isoformat(),
        "observation_start_ms": start,
        "observation_start": datetime.fromtimestamp(start / 1000, tz=UTC).isoformat(),
        "frozen_items": [
            "every V5 strategy section (identical to the frozen V5 parent)",
            "data-quality rules v5.1-dq-1 (gap rows are not observations; clean windows; clean ATR)",
            "historical warm-up seeds are feature inputs only (never signals or trades)",
        ],
        "v51_config_yaml": cfg.canonical_yaml(),
    }


def write_v51_freeze(
    cfg: V51Config,
    v5: V5Config,
    fcfg: ForwardConfig,
    parent_freeze: dict[str, Any] | None,
    path: Path = FREEZE_PATH_V51,
) -> dict[str, Any]:
    if path.exists():
        existing: dict[str, Any] = json.loads(path.read_text())
        if existing.get("v51_config_hash") != cfg.config_hash:
            raise RuntimeError("V5.1 freeze exists with a different config hash; refusing")
        return existing
    if cfg.config_hash != EXPECTED_V51_CONFIG_HASH:
        raise RuntimeError("config/btc_swing_v5_1.yaml does not hash to the pinned V5.1 hash")
    if (parent_freeze or {}).get("v5_config_hash") != PARENT_V5_CONFIG_HASH:
        raise RuntimeError("the V5 parent freeze is missing or not the frozen V5 observation")
    rec = freeze_record_v51(cfg, v5, fcfg, parent_freeze)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=1, sort_keys=True))
    return rec


def load_v51_freeze(path: Path = FREEZE_PATH_V51) -> dict[str, Any] | None:
    return json.loads(path.read_text()) if path.exists() else None


def check_v51_freeze(cfg: V51Config, path: Path = FREEZE_PATH_V51) -> dict[str, Any]:
    rec = load_v51_freeze(path)
    if rec is None:
        raise RuntimeError("no V5.1 freeze: run `btc-swing v51 forward freeze` first")
    if rec["v51_config_hash"] != cfg.config_hash:
        raise RuntimeError("V5.1 config hash differs from the freeze record; refusing to run")
    if rec["parent_v5_config_hash"] != PARENT_V5_CONFIG_HASH:
        raise RuntimeError("V5.1 freeze parent is not the frozen V5 observation")
    return rec
