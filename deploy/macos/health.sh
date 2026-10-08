#!/bin/bash
# Every 5 minutes (launchd StartInterval 300): forward health check; alert on a problem.
set -uo pipefail
REPO="${BTC_SWING_REPO:?}"
CONF="$HOME/.config/btc_swing"
[ -f "$CONF/forward.env" ] && { set -a; . "$CONF/forward.env"; set +a; }
cd "$REPO"
LOG="$BTC_DATA_DIR/btc/forward/logs/health.log"
out="$("$REPO/.venv/bin/btc-swing" v5 forward health 2>&1)"; rc=$?
printf '%s rc=%s %s\n' "$(date -u +%FT%TZ)" "$rc" "$(printf '%s' "$out" | tr -d '\n' | cut -c1-600)" >> "$LOG"
if [ "$rc" -ne 0 ]; then
  logger -t btc-v5-forward-health "PROBLEM: $(printf '%s' "$out" | tr -d '\n' | cut -c1-300)"
  /usr/bin/osascript -e 'display notification "BTC V5 forward health check failed: see health.log" with title "btc_swing" sound name "Basso"' >/dev/null 2>&1 || true
fi
exit 0
