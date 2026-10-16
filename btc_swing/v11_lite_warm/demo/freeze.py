"""V11-LITE Demo EXECUTION manifest (`manifests/v11_lite_warm_demo_execution_freeze.json`). Pins the
Demo execution layer (config hash, risk, leverage, safety caps, code) next to the UNCHANGED V11-LITE
strategy (lite strategy hash, bookkeeping version, V11 strategy hash). It is not a strategy freeze: the
shadow strategy freeze stays `manifests/v11_lite_warm_forward_freeze.json`."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from btc_swing.core.versions import code_version
from btc_swing.v11.config import V11Config
from btc_swing.v11_lite_warm.config import V11LiteWarmConfig, lite_strategy_hash
from btc_swing.v11_lite_warm.demo.config import LiteDemoConfig
from btc_swing.v11_lite_warm.engine import BOOKKEEPING_VERSION

DEMO_FREEZE_PATH = Path("manifests/v11_lite_warm_demo_execution_freeze.json")


def demo_freeze_record(lc: V11LiteWarmConfig, v11: V11Config, dc: LiteDemoConfig) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "project": "BTC_V11_LITE_WARM_DEMO_EXECUTION",
        "statement": "execution layer only: the V11-LITE strategy (decisions, score, thresholds, stops, TPs, "
        "exits, shadow sizing) is UNCHANGED; the shadow remains the source of truth and records every "
        "eligible trade; Demo consumes the same decisions and may be blocked by its own safety / conflict gates",
        "frozen_at": now.isoformat(),
        "code_version": code_version(),
        "v11_strategy_hash": v11.strategy_hash,
        "lite_strategy_hash": lite_strategy_hash(v11, lc),
        "bookkeeping_version": BOOKKEEPING_VERSION,
        "demo_config_hash": dc.config_hash,
        "account_mode_default": dc.account_mode,
        "risk": json.loads(dc.risk.model_dump_json()) | {"risk_usdt": dc.risk.risk_usdt},
        "safety": json.loads(dc.safety.model_dump_json()),
        "order_prefix": dc.order_prefix,
        "demo_config_yaml": dc.canonical_yaml(),
    }


def write_demo_freeze(
    lc: V11LiteWarmConfig, v11: V11Config, dc: LiteDemoConfig, path: Path = DEMO_FREEZE_PATH
) -> dict[str, Any]:
    if path.exists():
        cur: dict[str, Any] = json.loads(path.read_text())
        if cur["demo_config_hash"] != dc.config_hash:
            raise RuntimeError(
                "a Demo execution manifest with another Demo config exists; refusing"
            )
        return cur
    rec = demo_freeze_record(lc, v11, dc)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=1, sort_keys=True))
    return rec


def check_demo_freeze(
    lc: V11LiteWarmConfig, v11: V11Config, dc: LiteDemoConfig, path: Path = DEMO_FREEZE_PATH
) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError("no V11-LITE Demo execution manifest: run `btc-swing v11lw demo freeze`")
    rec: dict[str, Any] = json.loads(path.read_text())
    if rec["demo_config_hash"] != dc.config_hash:
        raise RuntimeError("Demo config differs from the execution manifest")
    if (
        rec["lite_strategy_hash"] != lite_strategy_hash(v11, lc)
        or rec["v11_strategy_hash"] != v11.strategy_hash
    ):
        raise RuntimeError("V11-LITE strategy differs from the Demo execution manifest")
    if rec["bookkeeping_version"] != BOOKKEEPING_VERSION:
        raise RuntimeError("bookkeeping version differs from the Demo execution manifest")
    return rec
