"""Version constants for the BTC swing engine. Bump when rules change."""

from __future__ import annotations

import subprocess

STRATEGY_NAME = "btc_leveraged_swing_v1"
STRATEGY_VERSION = "1.0.0-foundation"
FEATURE_SET_VERSION = "btc-fs-1"
REGIME_RULE_VERSION = "btc-regime-1"
SETUP_RULE_VERSION = "btc-setup-1"
EPISODE_RULE_VERSION = "btc-episode-1"
RISK_RULE_VERSION = "btc-risk-1"
COST_MODEL_VERSION = "btc-cost-1"
BACKTEST_VERSION = "btc-bt-1"


def code_version() -> str:
    """Short git sha of HEAD, suffixed with -dirty when tracked files are modified."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        sha = out.stdout.strip() or "unknown"
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return f"{sha}-dirty" if status.stdout.strip() else sha
    except Exception:
        return "unknown"
