#!/usr/bin/env bash
# Uninstall Kōan systemd --user services (no root).
# Usage: ./uninstall-user-service.sh
#
# Counterpart to uninstall-service.sh, but removes the per-user units under
# ~/.config/systemd/user and drives them with `systemctl --user` (no sudo).
# Leaves lingering in place by default; pass --disable-linger to also turn it off.
set -euo pipefail

if [ "$(uname -s)" != "Linux" ]; then
    echo "Error: systemd services are only supported on Linux." >&2
    exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
    echo "Error: systemctl not found." >&2
    exit 1
fi
if [ "$(id -u)" -eq 0 ]; then
    echo "Error: run as your normal user, not root (these are --user units)." >&2
    exit 1
fi

# Reach the user bus even when invoked without a login session (e.g. sudo -niu).
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"

UNIT_DIR="$HOME/.config/systemd/user"
SERVICES="koan.service koan-awake.service"

for svc in $SERVICES; do
    if [ -f "$UNIT_DIR/$svc" ]; then
        echo "→ Stopping $svc"
        systemctl --user stop "$svc" 2>/dev/null || true
        echo "→ Disabling $svc"
        systemctl --user disable "$svc" 2>/dev/null || true
        echo "→ Removing $svc"
        rm -f "$UNIT_DIR/$svc"
    else
        echo "  $svc not installed, skipping"
    fi
done

echo "→ Reloading user systemd daemon"
systemctl --user daemon-reload 2>/dev/null || true

if [ "${1:-}" = "--disable-linger" ]; then
    if loginctl disable-linger "$(id -un)" 2>/dev/null; then
        echo "→ Disabled lingering for $(id -un)"
    else
        echo "⚠ Could not disable lingering (needs privileges). Run once:" >&2
        echo "    sudo loginctl disable-linger $(id -un)" >&2
    fi
fi

echo "✓ Kōan systemd --user services removed."
