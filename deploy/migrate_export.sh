#!/usr/bin/env bash
# SOURCE host: cold export of the running observation. Stops the runner gracefully, takes an
# integrity snapshot, exports the state tarball + sha256 manifest. Usage: bash deploy/migrate_export.sh [out_dir]
set -euo pipefail
cd "$(dirname "$0")/.."
export BTC_DATA_DIR="${BTC_DATA_DIR:-./data}"
OUT="${1:-$BTC_DATA_DIR/btc/forward_export}"
mkdir -p "$OUT"
PIDF="$BTC_DATA_DIR/btc/forward/forward_run.pid"
if systemctl is-active --quiet btc-v5-forward 2>/dev/null; then
  echo "stopping systemd service"; sudo systemctl stop btc-v5-forward
elif [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
  P="$(cat "$PIDF")"; echo "stopping runner pid $P"; kill -TERM "$P"
  for i in $(seq 1 90); do kill -0 "$P" 2>/dev/null || break; sleep 1; done
  kill -0 "$P" 2>/dev/null && { echo "runner did not stop"; exit 1; }
fi
sudo systemctl disable btc-v5-forward 2>/dev/null || true
# release the authority lease so the target can claim it (the release is part of the export)
uv run btc-swing v5 forward release-authority --note "cold migration export" \
  || echo "no authority lease held by this host (pre-lease state): nothing to release"
uv run btc-swing v5 forward integrity --out "$OUT/integrity_before.json" >/dev/null
uv run btc-swing v5 forward export --out "$OUT/v5_forward_state.tar.gz"
(cd "$OUT" && sha256sum v5_forward_state.tar.gz > v5_forward_state.tar.gz.sha256)
echo "export ready in $OUT: v5_forward_state.tar.gz, .manifest.json, .sha256, integrity_before.json"
echo "copy all four files to the target host (e.g. rsync -av $OUT/ target:/var/lib/btc_swing/import/)"
