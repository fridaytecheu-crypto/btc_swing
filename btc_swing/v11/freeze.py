"""V11 prospective shadow freeze (`manifests/v11_forward_freeze.json`); strategy sections pinned."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from btc_swing.core.versions import code_version
from btc_swing.v5.forward.host import host_identity
from btc_swing.v11.config import (
    STRATEGY,
    V11_EXIT_VERSION,
    V11_FEATURE_VERSION,
    V11_SCORE_VERSION,
    V11_VERSION,
    V11Config,
    strategy_hash_of,
)

FREEZE_PATH_V11 = Path("manifests/v11_forward_freeze.json")


def freeze_record(
    cfg: V11Config, historical: dict[str, Any] | None, shadow: dict[str, Any] | None
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "project": STRATEGY,
        "version": V11_VERSION,
        "mode": "RESEARCH + SHADOW ONLY: no exchange client, no Demo authority",
        "config_hash": cfg.config_hash,
        "strategy_hash": cfg.strategy_hash,
        "code_version": code_version(),
        "feature_version": V11_FEATURE_VERSION,
        "score_version": V11_SCORE_VERSION,
        "exit_version": V11_EXIT_VERSION,
        "frozen_at": now.isoformat(),
        "observation_start_ms": int(now.timestamp() * 1000),
        "observation_start": now.isoformat(),
        "frozen_by_host": host_identity(),
        "historical_classification": (historical or {}).get("classification"),
        "shadow_verification": {k: shadow.get(k) for k in ("run_id", "status", "duration_s")}
        if shadow
        else None,
        "config_yaml": cfg.canonical_yaml(),
    }


def write_freeze(
    cfg: V11Config,
    historical: dict[str, Any] | None,
    shadow: dict[str, Any] | None,
    path: Path = FREEZE_PATH_V11,
) -> dict[str, Any]:
    if path.exists():
        existing: dict[str, Any] = json.loads(path.read_text())
        if strategy_hash_of(yaml.safe_load(existing["config_yaml"])) != cfg.strategy_hash:
            raise RuntimeError("a V11 freeze exists with different strategy sections; refusing")
        return existing
    rec = freeze_record(cfg, historical, shadow)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=1, sort_keys=True))
    return rec


def load_freeze(path: Path = FREEZE_PATH_V11) -> dict[str, Any] | None:
    return json.loads(path.read_text()) if path.exists() else None


def check_freeze(cfg: V11Config, path: Path = FREEZE_PATH_V11) -> dict[str, Any]:
    rec = load_freeze(path)
    if rec is None:
        raise RuntimeError("no V11 freeze: run `btc-swing v11 forward freeze` first")
    if strategy_hash_of(yaml.safe_load(rec["config_yaml"])) != cfg.strategy_hash:
        raise RuntimeError("V11 STRATEGY sections differ from the freeze; refusing to run")
    return rec
