#!/usr/bin/env bash
# TARGET host: import the exported state WITHOUT resetting the observation, verify every hash,
# run one cycle, compare integrity with the source snapshot. Usage (as root):
#   bash deploy/migrate_import.sh /var/lib/btc_swing/import [repo=/opt/btc_swing] [data=/var/lib/btc_swing]
set -euo pipefail
IMP="$1"; REPO="${2:-/opt/btc_swing}"; DATA="${3:-/var/lib/btc_swing}"
cd "$REPO"
export BTC_DATA_DIR="$DATA"
systemctl is-active --quiet btc-v5-forward && { echo "service is running: stop it first (systemctl stop btc-v5-forward)"; exit 1; }
[ -d "$DATA/btc/forward/signals" ] && [ -s "$DATA/btc/forward/signals/signals.jsonl" ] && { echo "target already holds forward state; refusing to overwrite"; exit 1; }
echo "== archive checksum"; (cd "$IMP" && sha256sum -c v5_forward_state.tar.gz.sha256)
echo "== extract"; mkdir -p "$DATA/btc"; tmp="$(mktemp -d)"; tar -xzf "$IMP/v5_forward_state.tar.gz" -C "$tmp"
rm -rf "$DATA/btc/forward"; mv "$tmp/forward" "$DATA/btc/forward"
cmp -s "$tmp/manifests/v5_forward_freeze.json" manifests/v5_forward_freeze.json || { echo "freeze manifest differs from the repository checkout: use the same commit"; exit 1; }
cmp -s "$tmp/config/btc_swing_v5.yaml" config/btc_swing_v5.yaml || { echo "frozen V5 config differs from the repository checkout"; exit 1; }
cp "$IMP/v5_forward_state.manifest.json" "$DATA/btc/forward/"; rm -rf "$tmp"
chown -R btcswing:btcswing "$DATA/btc/forward"
echo "== verify hashes"; sudo -u btcswing -E "$REPO/.venv/bin/btc-swing" v5 forward verify --manifest "$IMP/v5_forward_state.manifest.json"
echo "== one cycle (resumes from the migrated processor offsets)"; sudo -u btcswing -E "$REPO/.venv/bin/btc-swing" v5 forward cycle >/dev/null
echo "== integrity comparison vs the source snapshot"; sudo -u btcswing -E "$REPO/.venv/bin/btc-swing" v5 forward integrity --out "$DATA/btc/forward/integrity_after_import.json" --compare "$IMP/integrity_before.json"
echo "== claim authority (refused if another host still holds the lease)"
sudo -u btcswing -E "$REPO/.venv/bin/btc-swing" v5 forward claim-authority --note "cold migration import on $(hostname)"
echo "== start"; systemctl enable btc-v5-forward; systemctl start btc-v5-forward; systemctl start btc-v5-forward-health.timer; sleep 20
sudo -u btcswing -E "$REPO/.venv/bin/btc-swing" v5 forward status-text
