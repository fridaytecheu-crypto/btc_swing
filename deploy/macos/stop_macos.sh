#!/bin/bash
# Graceful stop: launchd sends SIGTERM (collector flushes, state saved), SIGKILL only after 90 s.
# Waits until the single-runner lock is released. Does NOT release authority.
set -euo pipefail
D="gui/$(id -u)"
for t in com.btcswing.v5-forward-health com.btcswing.v5-forward; do
  launchctl bootout "$D/$t" 2>/dev/null || true
done
for i in $(seq 1 100); do
  pgrep -f "btc-swing v5 forward run" >/dev/null || { echo "runner stopped"; exit 0; }
  sleep 1
done
echo "runner still alive after 100 s"; exit 1
