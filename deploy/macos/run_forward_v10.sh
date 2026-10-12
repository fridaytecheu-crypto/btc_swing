#!/bin/bash
# launchd entry point for the BTC V10 forward runner on macOS (separate from the V5/V5.1 agent).
# Loads ~/.config/btc_swing/forward.env and, if present, demo.env (must be 600); never changes V5 state.
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
unset BYBIT_EXECUTION_MODE   # V10 Demo is switched only by the STRATEGY_DEMO_V10_ACTIVATED event
cd "$REPO"
/usr/bin/caffeinate -i -m -s -w $$ &
exec "$REPO/.venv/bin/btc-swing" v10 forward run
