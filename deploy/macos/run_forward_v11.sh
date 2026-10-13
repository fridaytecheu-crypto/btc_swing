#!/bin/bash
# launchd entry point for the BTC V11 SHADOW runner on macOS (separate from V5/V5.1 and V10 agents).
# No demo.env is loaded: V11 has no exchange client and cannot place orders.
set -euo pipefail
REPO="${BTC_SWING_REPO:?BTC_SWING_REPO not set}"
CONF="$HOME/.config/btc_swing"
[ -f "$CONF/forward.env" ] && { set -a; . "$CONF/forward.env"; set +a; }
unset BYBIT_DEMO_API_KEY BYBIT_DEMO_API_SECRET BYBIT_EXECUTION_MODE
cd "$REPO"
/usr/bin/caffeinate -i -m -s -w $$ &
exec "$REPO/.venv/bin/btc-swing" v11 forward run
