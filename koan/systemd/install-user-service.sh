#!/usr/bin/env bash
# Install Kōan systemd --user services (no root).
# Usage: ./install-user-service.sh <koan_root> <python_path>
#
# Counterpart to install-service.sh, but installs per-user units under
# ~/.config/systemd/user and drives them with `systemctl --user` (no sudo).
# Enables lingering so the services start at boot without an active login.
set -euo pipefail

KOAN_ROOT="${1:?Usage: $0 <koan_root> <python_path>}"
PYTHON="${2:?Usage: $0 <koan_root> <python_path>}"

# --- Validations ---
if [ "$(uname -s)" != "Linux" ]; then
    echo "Error: systemd services are only supported on Linux." >&2
    exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
    echo "Error: systemctl not found. systemd is required." >&2
    exit 1
fi
if [ "$(id -u)" -eq 0 ]; then
    echo "Error: run as your normal user, not root (these are --user units)." >&2
    exit 1
fi

# Resolve to absolute paths
KOAN_ROOT="$(cd "$KOAN_ROOT" && pwd)"
PYTHON="$(cd "$(dirname "$PYTHON")" && pwd)/$(basename "$PYTHON")"

if [ ! -f "$KOAN_ROOT/koan/app/run.py" ]; then
    echo "Error: $KOAN_ROOT does not look like a Kōan installation." >&2
    exit 1
fi
if [ ! -x "$PYTHON" ]; then
    echo "Error: Python binary not found or not executable: $PYTHON" >&2
    exit 1
fi

# Service PATH. Unlike the system installer's sanitizer (app/systemd_service.py),
# KEEP the user bin dirs: ~/.npm-global/bin (claude CLI) and ~/.local/bin (ocgo)
# are required by the oc-claude provider wrapper — without them the provider call
# fails with 'ocgo/claude not found'.
SVC_PATH="$HOME/.npm-global/bin:$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
mkdir -p "$KOAN_ROOT/logs"

# write_unit <filename> <exec-arg> <description> <logfile> <extra-unit-lines>
write_unit() {
    local file="$UNIT_DIR/$1"
    cat > "$file" <<UNIT
[Unit]
Description=$3
$5

[Service]
Type=simple
WorkingDirectory=$KOAN_ROOT/koan
EnvironmentFile=$KOAN_ROOT/.env
Environment=KOAN_ROOT=$KOAN_ROOT
Environment=PYTHONPATH=$KOAN_ROOT/koan
Environment=PATH=$SVC_PATH
Environment=SSH_AUTH_SOCK=$KOAN_ROOT/.ssh-agent-sock
ExecStart=$PYTHON $2
Restart=on-failure
RestartSec=10
StandardOutput=append:$KOAN_ROOT/logs/$4
StandardError=append:$KOAN_ROOT/logs/$4

[Install]
WantedBy=default.target
UNIT
    echo "  wrote $file"
}

write_unit "koan-awake.service" "app/awake.py" "Kōan Communication Bridge (user)" "awake.log" \
    "PartOf=koan.service"
write_unit "koan.service" "app/run.py" "Kōan Agent Loop (user)" "run.log" \
    "Requires=koan-awake.service
BindsTo=koan-awake.service
After=koan-awake.service"

# Enable lingering so the user manager (and these services) start at boot
# without an active login session.
if ! loginctl show-user "$(id -un)" 2>/dev/null | grep -q '^Linger=yes'; then
    if loginctl enable-linger "$(id -un)" 2>/dev/null; then
        echo "→ Enabled lingering for $(id -un)"
    else
        echo "⚠ Could not enable lingering (needs privileges). Run once:" >&2
        echo "    sudo loginctl enable-linger $(id -un)" >&2
    fi
fi

# Reach the user bus even when invoked without a login session (e.g. sudo -niu).
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"

# On a fresh no-session host (sudo -niu, linger just enabled), logind spins up
# user@<uid>.service asynchronously — enable-linger returns before the user
# manager's bus socket is listening. Wait for it so the first `--user` call
# below doesn't fail with "Failed to connect to bus" and abort under set -e.
for _ in $(seq 1 40); do
    if [ -S "$XDG_RUNTIME_DIR/bus" ] && systemctl --user show-environment >/dev/null 2>&1; then
        break
    fi
    sleep 0.25
done

systemctl --user daemon-reload
systemctl --user enable koan.service koan-awake.service

echo "✓ Kōan systemd --user services installed and enabled."
echo "  Start with: make start   (requires KOAN_SERVICE_MANAGER=systemd-user in .env)"
echo "  Or directly: systemctl --user start koan.service"
