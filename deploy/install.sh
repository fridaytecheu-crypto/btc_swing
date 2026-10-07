#!/usr/bin/env bash
# Install the BTC V5 forward observation runner on a persistent Linux host (systemd, Debian/Ubuntu/RHEL).
# Usage: sudo bash deploy/install.sh [repo_dir=/opt/btc_swing] [data_dir=/var/lib/btc_swing]
# Idempotent. Does NOT start the service and does NOT create a freeze: migrate the state first
# (docs/V5_FORWARD_DEPLOYMENT.md), verify, then `systemctl start btc-v5-forward`.
set -euo pipefail
REPO="${1:-/opt/btc_swing}"
DATA="${2:-/var/lib/btc_swing}"
PY_MIN="3.12"

echo "== system packages"
if command -v apt-get >/dev/null; then
  apt-get update -qq && apt-get install -y -qq git curl ca-certificates logrotate python3 >/dev/null
elif command -v dnf >/dev/null; then
  dnf install -y -q git curl ca-certificates logrotate python3 >/dev/null
fi

echo "== service user and directories"
id -u btcswing >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin btcswing
mkdir -p "$DATA/btc/forward" "$REPO/reports/forward"
chown -R btcswing:btcswing "$DATA"

echo "== uv (Python package manager) for the service user"
if ! sudo -u btcswing -H sh -c 'command -v uv || test -x ~/.local/bin/uv' >/dev/null 2>&1; then
  sudo -u btcswing -H sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' >/dev/null
fi
UV="$(sudo -u btcswing -H sh -c 'command -v uv || echo ~/.local/bin/uv')"

echo "== repository at $REPO (branch main)"
if [ ! -d "$REPO/.git" ]; then
  git clone https://github.com/fridaytecheu-crypto/btc_swing "$REPO"
fi
chown -R btcswing:btcswing "$REPO"
cd "$REPO"
sudo -u btcswing -H "$UV" python install "$PY_MIN" >/dev/null 2>&1 || true   # managed CPython >= 3.12
sudo -u btcswing -H "$UV" sync --all-extras --frozen
sudo -u btcswing -H "$REPO/.venv/bin/python" -c 'import sys; assert sys.version_info >= (3, 12), sys.version'

echo "== systemd units, env, logrotate, journald"
cp deploy/btc-v5-forward.service deploy/btc-v5-forward-health.service deploy/btc-v5-forward-health.timer deploy/btc-v5-forward-alert.service /etc/systemd/system/
[ -f /etc/btc-v5-forward.env ] || { cp deploy/btc-v5-forward.env.example /etc/btc-v5-forward.env; sed -i "s#^BTC_DATA_DIR=.*#BTC_DATA_DIR=$DATA#" /etc/btc-v5-forward.env; }
cp deploy/btc-v5-forward.logrotate /etc/logrotate.d/btc-v5-forward
sed -i "s#/var/lib/btc_swing#$DATA#g" /etc/logrotate.d/btc-v5-forward
mkdir -p /etc/systemd/journald.conf.d && cp deploy/journald-btc-v5-forward.conf /etc/systemd/journald.conf.d/btc-v5-forward.conf
if [ "$REPO" != "/opt/btc_swing" ] || [ "$DATA" != "/var/lib/btc_swing" ]; then
  sed -i "s#/opt/btc_swing#$REPO#g; s#/var/lib/btc_swing#$DATA#g" /etc/systemd/system/btc-v5-forward*.service
fi
systemctl daemon-reload
systemctl restart systemd-journald
systemctl enable btc-v5-forward.service btc-v5-forward-health.timer >/dev/null
echo "== installed. Next: migrate the state (docs/V5_FORWARD_DEPLOYMENT.md section 5), verify, then:"
echo "   systemctl start btc-v5-forward && systemctl start btc-v5-forward-health.timer"
