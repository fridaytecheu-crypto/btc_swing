"""Freeze record for the forward observation: V5 config hash (strategy), forward config hash
(collection/reporting), code commit, rule versions and the observation start timestamp."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from btc_swing.core.versions import code_version
from btc_swing.v5.config import (
    V5_EVENT_RULE_VERSION,
    V5_EXIT_RULE_VERSION,
    V5_FEATURE_VERSION,
    V5_VERSION,
    V5Config,
)
from btc_swing.v5.forward.config import ForwardConfig

FREEZE_PATH = Path("manifests/v5_forward_freeze.json")


def freeze_record(
    cfg: V5Config, fcfg: ForwardConfig, start_ms: int | None = None
) -> dict[str, Any]:
    now = datetime.now(UTC)
    start = start_ms if start_ms is not None else int(now.timestamp() * 1000)
    return {
        "project": "btc_swing_v5_forward_observation",
        "mode": "FORWARD OBSERVATION (no orders, no credentials, no rule changes)",
        "v5_config_hash": cfg.config_hash,
        "v5_strategy_version": V5_VERSION,
        "rule_versions": {
            "features": V5_FEATURE_VERSION,
            "events": V5_EVENT_RULE_VERSION,
            "exits": V5_EXIT_RULE_VERSION,
        },
        "forward_config_hash": fcfg.config_hash,
        "code_version": code_version(),
        "frozen_at": now.isoformat(),
        "observation_start_ms": start,
        "observation_start": datetime.fromtimestamp(start / 1000, tz=UTC).isoformat(),
        "frozen_items": [
            "V5 config (events, features, execution geometry, stop, exits, costs, risk)",
            "feature definitions v5-feat-1",
            "event definitions v5-event-1",
            "exit rules v5-exit-1",
            "paper risk 0.25% per trade on a fixed 10,000 USDT research equity",
        ],
        "v5_config_yaml": cfg.canonical_yaml(),
        "forward_config_yaml": fcfg.canonical_yaml(),
    }


def write_freeze(cfg: V5Config, fcfg: ForwardConfig, path: Path = FREEZE_PATH) -> dict[str, Any]:
    if path.exists():
        existing: dict[str, Any] = json.loads(path.read_text())
        if existing.get("v5_config_hash") != cfg.config_hash:
            raise RuntimeError(
                "freeze exists with a different V5 config hash; the strategy must not change"
            )
        return existing
    rec = freeze_record(cfg, fcfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=1, sort_keys=True))
    return rec


def load_freeze(path: Path = FREEZE_PATH) -> dict[str, Any] | None:
    return json.loads(path.read_text()) if path.exists() else None


def check_freeze(cfg: V5Config, path: Path = FREEZE_PATH) -> dict[str, Any]:
    rec = load_freeze(path)
    if rec is None:
        raise RuntimeError("no freeze record: run `btc-swing v5 forward freeze` first")
    if rec["v5_config_hash"] != cfg.config_hash:
        raise RuntimeError("V5 config hash differs from the freeze record; refusing to run")
    return rec
