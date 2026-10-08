#!/bin/bash
# Install the BTC V5 forward runner as macOS LaunchAgents (no sudo). Does NOT start anything,
# does NOT create a freeze, does NOT claim authority. Usage (from the repository root):
#   bash deploy/macos/install_macos.sh [data_dir=$HOME/btc_swing_data]
set -euo pipefail
[ "$(uname -s)" = "Darwin" ] || { echo "macOS only"; exit 1; }
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
DATA="${1:-$HOME/btc_swing_data}"
case "$DATA" in
  /tmp/*|/private/tmp/*|/private/var/folders/*|/var/folders/*) echo "refusing ephemeral data dir $DATA"; exit 1;;
  "$HOME/Desktop"*|"$HOME/Documents"*|"$HOME/Library/Mobile Documents"*) echo "refusing $DATA: may be synced by iCloud / restricted for LaunchAgents"; exit 1;;
  *" "*) echo "refusing $DATA: no spaces please"; exit 1;;
esac
command -v uv >/dev/null || { echo "uv not found: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
echo "== dependencies (repo $REPO)"; (cd "$REPO" && uv sync --all-extras --frozen >/dev/null)
"$REPO/.venv/bin/python" -c 'import sys; assert sys.version_info >= (3, 12), sys.version'
echo "== data directory $DATA (persistent, outside temp)"; mkdir -p "$DATA/btc/forward/logs"
mkdir -p "$HOME/.config/btc_swing"; chmod 700 "$HOME/.config/btc_swing"
printf 'BTC_DATA_DIR=%s\nPYTHONUNBUFFERED=1\n' "$DATA" > "$HOME/.config/btc_swing/forward.env"
if [ ! -f "$HOME/.config/btc_swing/demo.env" ]; then
  echo "   NOTE: no ~/.config/btc_swing/demo.env yet (see docs/V5_MAC_AUTHORITATIVE_HOST.md step 3)"
fi
echo "== LaunchAgents (rendered, not loaded)"
mkdir -p "$HOME/Library/LaunchAgents"
for t in com.btcswing.v5-forward com.btcswing.v5-forward-health; do
  sed -e "s#__REPO__#$REPO#g" -e "s#__DATA__#$DATA#g" -e "s#__HOME__#$HOME#g" \
    "$REPO/deploy/macos/$t.plist.template" > "$HOME/Library/LaunchAgents/$t.plist"
  plutil -lint "$HOME/Library/LaunchAgents/$t.plist" >/dev/null
  echo "   $HOME/Library/LaunchAgents/$t.plist"
done
echo "== installed. Next: import or inspect state, claim authority, then deploy/macos/start_macos.sh"
