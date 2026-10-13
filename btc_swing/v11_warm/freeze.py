"""V11-WARM prospective freeze (`manifests/v11_warm_forward_freeze.json`): pins the V11 strategy hash
(identical to the V11 freeze), the bootstrap bars (content hash, range, provenance), the gap-fill
policy and the live warm-up; observation_start = frozen_at + live_warmup_min."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from btc_swing.core.versions import code_version
from btc_swing.v5.forward.host import host_identity
from btc_swing.v11.config import V11Config
from btc_swing.v11_warm.config import (
    BOOTSTRAP_MANIFEST,
    BOOTSTRAP_PARQUET,
    STRATEGY,
    V11W_VERSION,
    V11WarmConfig,
    frozen_v11_strategy_hash,
)

FREEZE_PATH_V11W = Path("manifests/v11_warm_forward_freeze.json")
MS_1M = 60_000


def bootstrap_summary(manifest_path: Path = BOOTSTRAP_MANIFEST) -> dict[str, Any]:
    m = json.loads(manifest_path.read_text())
    return {
        "source": m["source"],
        "days_requested": m["days_requested"],
        "days_fetched": m["days_fetched"],
        "days_unavailable": m["days_unavailable"],
        "bars": m["bars"],
        "first_open_ms": m["first_open_ms"],
        "last_close_ms": m["last_close_ms"],
        "first_open": datetime.fromtimestamp(m["first_open_ms"] / 1000, tz=UTC).isoformat(),
        "last_close": datetime.fromtimestamp(m["last_close_ms"] / 1000, tz=UTC).isoformat(),
        "sha256_bars": m["sha256_bars"],
        "parquet": str(BOOTSTRAP_PARQUET),
        "fields": "open/high/low/close/volume/taker buy qty/taker sell qty/trade count (exact)",
        "never_backfilled": [
            "order book",
            "replenishment/depletion",
            "live taker windows",
            "allLiquidation",
        ],
    }


def freeze_record(
    wc: V11WarmConfig, v11: V11Config, shadow: dict[str, Any] | None
) -> dict[str, Any]:
    now = datetime.now(UTC)
    frozen_ms = int(now.timestamp() * 1000)
    obs_ms = frozen_ms + wc.warm.live_warmup_min * MS_1M
    boot = bootstrap_summary()
    if int(boot["last_close_ms"]) > frozen_ms:
        raise RuntimeError("bootstrap bars close after the freeze instant: PIT violation")
    return {
        "project": STRATEGY,
        "version": V11W_VERSION,
        "mode": "SHADOW ONLY (warm-started V11): no exchange client, no Demo authority",
        "v11_strategy_hash": v11.strategy_hash,
        "v11_freeze_strategy_hash": frozen_v11_strategy_hash(),
        "v11_config_hash": v11.config_hash,
        "warm_config_hash": wc.config_hash,
        "code_version": code_version(),
        "frozen_at": now.isoformat(),
        "frozen_ms": frozen_ms,
        "live_warmup_min": wc.warm.live_warmup_min,
        "observation_start_ms": obs_ms,
        "observation_start": datetime.fromtimestamp(obs_ms / 1000, tz=UTC).isoformat(),
        "window_days": wc.warm.window_days,
        "bootstrap": boot,
        "gapfill_policy": (
            "Bybit public klines (price/volume exact; taker split NaN, trade count placeholder 1) "
            f"for at most {wc.warm.gapfill_max_hours} h between the archive end and the seed instant; "
            "effects confined to flow windows <= 20 min, inside live_warmup_min"
        ),
        "derivatives": "OI / funding warm live from the Bybit ticker (oi_chg_1h after 60 min)",
        "frozen_by_host": host_identity(),
        "shadow_verification": {k: shadow.get(k) for k in ("run_id", "status", "duration_s")}
        if shadow
        else None,
        "v11_config_yaml": v11.canonical_yaml(),
        "warm_config_yaml": wc.canonical_yaml(),
    }


def write_freeze(
    wc: V11WarmConfig,
    v11: V11Config,
    shadow: dict[str, Any] | None,
    path: Path = FREEZE_PATH_V11W,
) -> dict[str, Any]:
    if path.exists():
        existing: dict[str, Any] = json.loads(path.read_text())
        if existing["v11_strategy_hash"] != v11.strategy_hash:
            raise RuntimeError("a V11-WARM freeze exists with a different V11 strategy; refusing")
        return existing
    rec = freeze_record(wc, v11, shadow)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=1, sort_keys=True))
    return rec


def load_freeze(path: Path = FREEZE_PATH_V11W) -> dict[str, Any] | None:
    return json.loads(path.read_text()) if path.exists() else None


def check_freeze(
    wc: V11WarmConfig, v11: V11Config, path: Path = FREEZE_PATH_V11W
) -> dict[str, Any]:
    rec = load_freeze(path)
    if rec is None:
        raise RuntimeError("no V11-WARM freeze: run `btc-swing v11w forward freeze` first")
    if rec["v11_strategy_hash"] != v11.strategy_hash:
        raise RuntimeError("V11 STRATEGY sections differ from the V11-WARM freeze; refusing to run")
    if (
        rec["live_warmup_min"] != wc.warm.live_warmup_min
        or rec["window_days"] != wc.warm.window_days
    ):
        raise RuntimeError("warm-start operational parameters differ from the freeze; refusing")
    return rec
