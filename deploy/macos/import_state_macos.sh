#!/bin/bash
# Import a cold-exported forward state (tar.gz + manifest + .sha256 + integrity_before.json) into
# the Mac data directory WITHOUT resetting the observation. Refuses to overwrite existing forward
# state and refuses while the runner is loaded. Usage (repository root):
#   bash deploy/macos/import_state_macos.sh <import_dir> [data_dir=$HOME/btc_swing_data]
set -euo pipefail
IMP="$(cd "$1" && pwd)"; DATA="${2:-$HOME/btc_swing_data}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$REPO"
export BTC_DATA_DIR="$DATA"
BS="$REPO/.venv/bin/btc-swing"
launchctl print "gui/$(id -u)/com.btcswing.v5-forward" >/dev/null 2>&1 && { echo "runner LaunchAgent is loaded: stop it first (deploy/macos/stop_macos.sh)"; exit 1; }
for f in "$DATA/btc/forward/signals/signals.jsonl" "$DATA/btc/forward/derived/processor_state.json"; do
  [ -s "$f" ] && { echo "target already holds forward state ($f): refusing to overwrite"; exit 1; }
done
TGZ="$(ls "$IMP"/*.tar.gz | head -1)"; MAN="${TGZ%.tar.gz}.manifest.json"
echo "== archive checksum"; (cd "$IMP" && shasum -a 256 -c "$(basename "$TGZ").sha256")
echo "== extract"; mkdir -p "$DATA/btc"; tmp="$(mktemp -d "$DATA/btc/.import.XXXXXX")"
tar -xzf "$TGZ" -C "$tmp"
cmp -s "$tmp/manifests/v5_forward_freeze.json" manifests/v5_forward_freeze.json || { echo "freeze manifest differs from this checkout"; rm -rf "$tmp"; exit 1; }
cmp -s "$tmp/config/btc_swing_v5.yaml" config/btc_swing_v5.yaml || { echo "frozen V5 config differs from this checkout"; rm -rf "$tmp"; exit 1; }
if [ -d "$DATA/btc/forward" ]; then mv "$DATA/btc/forward" "$DATA/btc/forward.pre_import.$(date -u +%Y%m%dT%H%M%SZ)"; fi
mv "$tmp/forward" "$DATA/btc/forward"; cp "$MAN" "$DATA/btc/forward/"; rm -rf "$tmp"
echo "== verify every file hash"; "$BS" v5 forward verify --manifest "$MAN"
echo "== integrity vs the source snapshot (prefix identity, no duplicates)"
"$BS" v5 forward integrity --out "$DATA/btc/forward/integrity_after_import.json" --compare "$IMP/integrity_before.json"
echo "== coverage (missing periods are reported, never backfilled)"; "$BS" v5 forward coverage
