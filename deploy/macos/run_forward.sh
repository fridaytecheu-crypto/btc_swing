#!/bin/bash
# launchd entry point for the BTC V5 forward runner on macOS (see docs/V5_MAC_AUTHORITATIVE_HOST.md).
# - loads ~/.config/btc_swing/forward.env and, if present, ~/.config/btc_swing/demo.env (must be 600)
# - holds a caffeinate assertion (no idle/system sleep on AC power, no disk sleep) for exactly the
#   lifetime of the runner process; nothing in macOS settings is changed permanently
# - execs the runner, so launchd's SIGTERM reaches it directly (graceful shutdown)
set -euo pipefail
REPO="${BTC_SWING_REPO:?BTC_SWING_REPO not set}"
CONF="$HOME/.config/btc_swing"
[ -f "$CONF/forward.env" ] && { set -a; . "$CONF/forward.env"; set +a; }
if [ -f "$CONF/demo.env" ]; then
  perm="$(stat -f %Lp "$CONF/demo.env")"
  if [ "$perm" != "600" ]; then
    echo "$(date -u +%FT%TZ) refusing: $CONF/demo.env has mode $perm (must be 600)" >&2
    exit 78
  fi
  set -a; . "$CONF/demo.env"; set +a
fi
unset BYBIT_EXECUTION_MODE   # STRATEGY_DEMO is switched only by the STRATEGY_DEMO_ACTIVATED event
cd "$REPO"
/usr/bin/caffeinate -i -m -s -w $$ &
exec "$REPO/.venv/bin/btc-swing" v5 forward run
