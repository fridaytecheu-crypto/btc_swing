#!/bin/bash
# Render and load the V10 LaunchAgent (no sudo). Does NOT freeze, does NOT activate Demo.
#   bash deploy/macos/install_v10_macos.sh            # uses BTC_DATA_DIR from ~/.config/btc_swing/forward.env
set -euo pipefail
[ "$(uname -s)" = "Darwin" ] || { echo "macOS only"; exit 1; }
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
[ -f "$HOME/.config/btc_swing/forward.env" ] || { echo "run deploy/macos/install_macos.sh first"; exit 1; }
set -a; . "$HOME/.config/btc_swing/forward.env"; set +a
DATA="${BTC_DATA_DIR:?}"
mkdir -p "$DATA/btc/forward_v10/logs" "$HOME/Library/LaunchAgents"
t=com.btcswing.v10-forward
sed -e "s#__REPO__#$REPO#g" -e "s#__DATA__#$DATA#g" "$REPO/deploy/macos/$t.plist.template" > "$HOME/Library/LaunchAgents/$t.plist"
plutil -lint "$HOME/Library/LaunchAgents/$t.plist" >/dev/null
chmod +x "$REPO/deploy/macos/run_forward_v10.sh"
D="gui/$(id -u)"
launchctl enable "$D/$t"
launchctl bootstrap "$D" "$HOME/Library/LaunchAgents/$t.plist" 2>/dev/null || launchctl kickstart -k "$D/$t"
sleep 15
launchctl print "$D/$t" | grep -E "state =|pid =|last exit" || true
"$REPO/.venv/bin/btc-swing" v10 forward status
