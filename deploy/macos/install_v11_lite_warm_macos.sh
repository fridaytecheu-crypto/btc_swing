#!/bin/bash
# Install ONLY the BTC V11-LITE-WARM shadow LaunchAgent (com.btcswing.v11-lite-warm-forward). The V5/V5.1, V10,
# V11 and V11-WARM agents are not touched. Seeds the V11-LITE-WARM root with the committed bootstrap bars (+ kline
# gap-fill up to now) before the agent starts; refuses to re-seed a root that already holds bars.
set -euo pipefail
[ "$(uname -s)" = "Darwin" ] || { echo "macOS only"; exit 1; }
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
[ -f "$HOME/.config/btc_swing/forward.env" ] || { echo "run deploy/macos/install_macos.sh first"; exit 1; }
set -a; . "$HOME/.config/btc_swing/forward.env"; set +a
unset BYBIT_DEMO_API_KEY BYBIT_DEMO_API_SECRET BYBIT_EXECUTION_MODE
DATA="${BTC_DATA_DIR:?}"
mkdir -p "$DATA/btc/forward_v11_lite_warm/logs" "$HOME/Library/LaunchAgents"
cd "$REPO"
if [ ! -s "$DATA/btc/forward_v11_lite_warm/derived/bars_1m.jsonl" ]; then
  "$REPO/.venv/bin/btc-swing" v11lw seed
else
  echo "V11-LITE-WARM root already seeded; leaving bars untouched"
fi
t=com.btcswing.v11-lite-warm-forward
sed -e "s#__REPO__#$REPO#g" -e "s#__DATA__#$DATA#g" "$REPO/deploy/macos/$t.plist.template" > "$HOME/Library/LaunchAgents/$t.plist"
plutil -lint "$HOME/Library/LaunchAgents/$t.plist" >/dev/null
chmod +x "$REPO/deploy/macos/run_forward_v11_lite_warm.sh"
D="gui/$(id -u)"
launchctl enable "$D/$t"
launchctl bootstrap "$D" "$HOME/Library/LaunchAgents/$t.plist" 2>/dev/null || launchctl kickstart -k "$D/$t"
sleep 20
launchctl print "$D/$t" | grep -E "state =|pid =|last exit" || true
"$REPO/.venv/bin/btc-swing" v11lw forward status
