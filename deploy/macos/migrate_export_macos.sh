#!/bin/bash
# SOURCE = this Mac (cold migration to the NUC). Stops the runner gracefully, takes the integrity
# snapshot, exports and checksums the complete state, then RELEASES authority and disables the
# LaunchAgents so this Mac can no longer run as authoritative host. Usage (repository root):
#   bash deploy/macos/migrate_export_macos.sh [out_dir=$HOME/btc_swing_export]
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$REPO"
set -a; . "$HOME/.config/btc_swing/forward.env"; set +a
OUT="${1:-$HOME/btc_swing_export}"; mkdir -p "$OUT"
BS="$REPO/.venv/bin/btc-swing"; D="gui/$(id -u)"
echo "== STRATEGY_DEMO must be flat (no open strategy position) before migrating"
"$BS" v5 demo status | grep -E "open strategy position|reconciliation required"
echo "== graceful stop"; bash deploy/macos/stop_macos.sh
echo "== disable this Mac as authoritative host (LaunchAgents disabled, authority released)"
launchctl disable "$D/com.btcswing.v5-forward"; launchctl disable "$D/com.btcswing.v5-forward-health"
"$BS" v5 forward release-authority --note "cold migration Mac -> NUC"
echo "== integrity snapshot (source, after the release so the lease state is included)"
"$BS" v5 forward integrity --out "$OUT/integrity_before.json" >/dev/null
echo "== export (complete state incl. demo journals and the authority lease)"
"$BS" v5 forward export --out "$OUT/v5_forward_state.tar.gz"
(cd "$OUT" && shasum -a 256 v5_forward_state.tar.gz > v5_forward_state.tar.gz.sha256)
ls -la "$OUT"; cat "$OUT/v5_forward_state.tar.gz.sha256"
