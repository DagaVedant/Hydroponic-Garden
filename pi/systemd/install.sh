#!/usr/bin/env bash
# Install the four services and start them. Run from anywhere, as root:
#
#     sudo pi/systemd/install.sh
#
# Expects a venv at pi/.venv with the requirements installed (see pi/README.md)
# and mosquitto already running as a system service.
set -euo pipefail

PI_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"

if [ ! -x "$PI_DIR/.venv/bin/python" ]; then
    echo "no venv at $PI_DIR/.venv. make one first:" >&2
    echo "  python3 -m venv $PI_DIR/.venv" >&2
    echo "  $PI_DIR/.venv/bin/pip install -r $PI_DIR/control/requirements.txt -r $PI_DIR/ingest/requirements.txt -r $PI_DIR/alerts/requirements.txt -r $PI_DIR/web/requirements.txt" >&2
    exit 1
fi

for unit in control ingest alerts web; do
    sed -e "s|@PI_DIR@|$PI_DIR|g" -e "s|@USER@|$USER_NAME|g" \
        "$PI_DIR/systemd/hydro-$unit.service" > "/etc/systemd/system/hydro-$unit.service"
    echo "installed hydro-$unit.service for $USER_NAME in $PI_DIR"
done

systemctl daemon-reload
systemctl enable --now hydro-control hydro-ingest hydro-alerts hydro-web
systemctl --no-pager --lines=0 status hydro-control hydro-ingest hydro-alerts hydro-web || true
echo
echo "logs: journalctl -u hydro-control -f"
