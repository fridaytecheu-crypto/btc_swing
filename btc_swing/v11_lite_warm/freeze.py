"""V11-LITE-WARM prospective freeze (`manifests/v11_lite_warm_forward_freeze.json`): pins the V11
strategy hash (identical to the V11 / V11-WARM freezes), the lite strategy hash (V11 strategy + the
labels-only delta), the lite config hash, the bootstrap bars, the warm-start parameters, the
pre-registered checkpoints and observation_start = frozen_at + live_warmup_min."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from btc_swing.core.versions import code_version
from btc_swing.v5.forward.host import host_identity
from btc_swing.v11.config import V11Config
from btc_swing.v11_lite_warm.config import (
    STRATEGY,
    STRATEGY_DELTA,
    V11LW_VERSION,
    V11LiteWarmConfig,
    frozen_v11_strategy_hash,
    lite_strategy_hash,
)
from btc_swing.v11_warm.freeze import bootstrap_summary

FREEZE_PATH_V11LW = Path("manifests/v11_lite_warm_forward_freeze.json")
MS_1M = 60_000


def freeze_record(
    lc: V11LiteWarmConfig, v11: V11Config, shadow: dict[str, Any] | None
) -> dict[str, Any]:
    now = datetime.now(UTC)
    frozen_ms = int(now.timestamp() * 1000)
    obs_ms = frozen_ms + lc.warm.live_warmup_min * MS_1M
    boot = bootstrap_summary()
    if int(boot["last_close_ms"]) > frozen_ms:
        raise RuntimeError("bootstrap bars close after the freeze instant: PIT violation")
    return {
        "project": STRATEGY,
        "version": V11LW_VERSION,
        "mode": "SHADOW ONLY (warm-started V11, family structure as labels): no exchange client, "
        "no Demo authority, no order placement capability",
        "strategy_delta": STRATEGY_DELTA,
        "v11_strategy_hash": v11.strategy_hash,
        "v11_freeze_strategy_hash": frozen_v11_strategy_hash(),
        "lite_strategy_hash": lite_strategy_hash(v11, lc),
        "v11_config_hash": v11.config_hash,
        "lite_config_hash": lc.config_hash,
        "code_version": code_version(),
        "frozen_at": now.isoformat(),
        "frozen_ms": frozen_ms,
        "live_warmup_min": lc.warm.live_warmup_min,
        "observation_start_ms": obs_ms,
        "observation_start": datetime.fromtimestamp(obs_ms / 1000, tz=UTC).isoformat(),
        "window_days": lc.warm.window_days,
        "bootstrap": boot,
        "outcome_horizons_min": list(lc.lite.outcome_horizons_min),
        "checkpoints": [json.loads(c.model_dump_json()) for c in lc.lite.checkpoints],
        "telemetry": "Fibonacci / VWAP / range / swing / location / flow / microstructure context is "
        "read-only; no decision reads any context field; no forward tuning",
        "control": "V11-WARM (strict, frozen) runs simultaneously as the control; an in-process strict "
        "V11 twin attributes every LITE decision (incremental cohort)",
        "frozen_by_host": host_identity(),
        "shadow_verification": {k: shadow.get(k) for k in ("run_id", "status", "duration_s")}
        if shadow
        else None,
        "v11_config_yaml": v11.canonical_yaml(),
        "lite_config_yaml": lc.canonical_yaml(),
    }


def write_freeze(
    lc: V11LiteWarmConfig,
    v11: V11Config,
    shadow: dict[str, Any] | None,
    path: Path = FREEZE_PATH_V11LW,
) -> dict[str, Any]:
    if path.exists():
        existing: dict[str, Any] = json.loads(path.read_text())
        if existing["lite_strategy_hash"] != lite_strategy_hash(v11, lc):
            raise RuntimeError("a V11-LITE-WARM freeze exists with a different strategy; refusing")
        return existing
    rec = freeze_record(lc, v11, shadow)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=1, sort_keys=True))
    return rec


def load_freeze(path: Path = FREEZE_PATH_V11LW) -> dict[str, Any] | None:
    return json.loads(path.read_text()) if path.exists() else None


def check_freeze(
    lc: V11LiteWarmConfig, v11: V11Config, path: Path = FREEZE_PATH_V11LW
) -> dict[str, Any]:
    rec = load_freeze(path)
    if rec is None:
        raise RuntimeError("no V11-LITE-WARM freeze: run `btc-swing v11lw forward freeze` first")
    if rec["v11_strategy_hash"] != v11.strategy_hash:
        raise RuntimeError("V11 STRATEGY sections differ from the V11-LITE-WARM freeze; refusing")
    if rec["lite_strategy_hash"] != lite_strategy_hash(v11, lc):
        raise RuntimeError("the lite strategy delta differs from the freeze; refusing to run")
    if (
        rec["live_warmup_min"] != lc.warm.live_warmup_min
        or rec["window_days"] != lc.warm.window_days
        or rec["outcome_horizons_min"] != list(lc.lite.outcome_horizons_min)
    ):
        raise RuntimeError("operational parameters differ from the freeze; refusing")
    return rec
