#!/bin/bash
# Claim authority (if not yet held by this Mac) and load the LaunchAgents. Usage (repository root):
#   bash deploy/macos/start_macos.sh "<claim note>"
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$REPO"
[ -f "$HOME/.config/btc_swing/forward.env" ] || { echo "run install_macos.sh first"; exit 1; }
set -a; . "$HOME/.config/btc_swing/forward.env"; set +a
BS="$REPO/.venv/bin/btc-swing"; D="gui/$(id -u)"
"$BS" v5 forward claim-authority --note "${1:-Mac becomes the temporary authoritative forward host}"
for t in com.btcswing.v5-forward com.btcswing.v5-forward-health; do
  launchctl enable "$D/$t"
  launchctl bootstrap "$D" "$HOME/Library/LaunchAgents/$t.plist" 2>/dev/null || launchctl kickstart -k "$D/$t"
done
sleep 20
launchctl print "$D/com.btcswing.v5-forward" | grep -E "state =|pid =|runs =|last exit" || true
pmset -g assertions | grep -i caffeinate || echo "WARNING: no caffeinate assertion found"
"$BS" v5 forward host-status
