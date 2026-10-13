"""V10 prospective freeze (`manifests/v10_forward_freeze.json`): config hash, code commit, feature /
family / risk versions, frozen_at and observation_start (= frozen_at). Written exactly once; the
runner refuses a different config hash; no V10 signal may precede observation_start."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from btc_swing.core.versions import code_version
from btc_swing.v5.forward.host import host_identity
from btc_swing.v10.config import (
    STRATEGY,
    V10_FAMILY_VERSION,
    V10_FEATURE_VERSION,
    V10_RISK_VERSION,
    V10_VERSION,
    V10Config,
    strategy_hash_of,
)

FREEZE_PATH_V10 = Path("manifests/v10_forward_freeze.json")


def freeze_record(cfg: V10Config, shadow_report: dict[str, Any] | None) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "project": STRATEGY,
        "version": V10_VERSION,
        "mode": "FORWARD ONLY: shadow first; Bybit DEMO only after an owner activation event",
        "config_hash": cfg.config_hash,
        "strategy_hash": cfg.strategy_hash,
        "code_version": code_version(),
        "feature_version": V10_FEATURE_VERSION,
        "family_version": V10_FAMILY_VERSION,
        "risk_version": V10_RISK_VERSION,
        "frozen_at": now.isoformat(),
        "observation_start_ms": int(now.timestamp() * 1000),
        "observation_start": now.isoformat(),
        "frozen_by_host": host_identity(),
        "shadow_verification": {
            k: shadow_report.get(k) for k in ("run_id", "status", "duration_s", "generated_utc")
        }
        if shadow_report
        else None,
        "frozen_items": [
            "features v10-feat-1, families A/B/C v10-fam-1, stop/exit/risk v10-risk-1",
            "no historical V10 backtest exists; no signal before observation_start",
            "no rule is tuned during the first observation period",
        ],
        "config_yaml": cfg.canonical_yaml(),
    }


def write_freeze(
    cfg: V10Config, shadow_report: dict[str, Any] | None, path: Path = FREEZE_PATH_V10
) -> dict[str, Any]:
    if path.exists():
        existing: dict[str, Any] = json.loads(path.read_text())
        if existing.get("config_hash") != cfg.config_hash:
            raise RuntimeError("a V10 freeze exists with a different config hash; refusing")
        return existing
    rec = freeze_record(cfg, shadow_report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=1, sort_keys=True))
    return rec


def load_freeze(path: Path = FREEZE_PATH_V10) -> dict[str, Any] | None:
    return json.loads(path.read_text()) if path.exists() else None


def check_freeze(cfg: V10Config, path: Path = FREEZE_PATH_V10) -> dict[str, Any]:
    rec = load_freeze(path)
    if rec is None:
        raise RuntimeError("no V10 freeze: run `btc-swing v10 forward freeze` first")
    frozen_raw = yaml.safe_load(rec["config_yaml"])
    if strategy_hash_of(frozen_raw) != cfg.strategy_hash:
        raise RuntimeError("V10 STRATEGY sections differ from the freeze; refusing to run")
    rec["operational_sections_changed"] = rec["config_hash"] != cfg.config_hash
    return rec
