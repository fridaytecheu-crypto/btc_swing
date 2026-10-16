#!/bin/bash
# launchd entry point for the BTC V11-LITE-WARM SHADOW runner on macOS (separate from the V5/V5.1, V10, V11
# and V11-WARM agents). V10's demo.env is NEVER loaded here. The optional V11-LITE Bybit DEMO observer uses
# ONLY ~/.config/btc_swing/v11lw_demo.env (must be mode 600; isolated account: BYBIT_DEMO_V11LW_API_KEY /
# BYBIT_DEMO_V11LW_API_SECRET) and sends nothing unless the owner ran `btc-swing v11lw demo activate`.
set -euo pipefail
REPO="${BTC_SWING_REPO:?BTC_SWING_REPO not set}"
CONF="$HOME/.config/btc_swing"
[ -f "$CONF/forward.env" ] && { set -a; . "$CONF/forward.env"; set +a; }
unset BYBIT_DEMO_API_KEY BYBIT_DEMO_API_SECRET BYBIT_EXECUTION_MODE
if [ -f "$CONF/v11lw_demo.env" ]; then
  if [ "$(stat -f '%Lp' "$CONF/v11lw_demo.env")" != "600" ]; then
    echo "refusing: $CONF/v11lw_demo.env must be mode 600 (chmod 600)" >&2
    exit 78
  fi
  set -a; . "$CONF/v11lw_demo.env"; set +a
  unset BYBIT_EXECUTION_MODE
fi
cd "$REPO"
/usr/bin/caffeinate -i -m -s -w $$ &
exec "$REPO/.venv/bin/btc-swing" v11lw forward run
