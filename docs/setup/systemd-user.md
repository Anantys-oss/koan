# Running as a systemd `--user` Service (Linux, no root)

Kōan can run as a per-user **systemd** service on Linux — automatic restart on
failure, boot-time startup, and **no `sudo`** required for day-to-day operation.
This is the recommended Linux deployment when you don't want (or can't have) a
root-owned system service.

## Service manager modes

`make start/stop/status` delegate to a service manager selected by the
`KOAN_SERVICE_MANAGER` variable (read from `.env`):

| `KOAN_SERVICE_MANAGER` | Where units live | Privileges | Platform |
|------------------------|------------------|-----------|----------|
| _(unset, default)_ | — (foreground PID manager) | none | any |
| `systemd` | `/etc/systemd/system` | needs `sudo` | Linux |
| `systemd-user` | `~/.config/systemd/user` | no root (uses linger) | Linux |
| `launchd` | `~/Library/LaunchAgents` | no root | macOS |

To opt in, add to your `.env`:

```bash
KOAN_SERVICE_MANAGER=systemd-user
```

## Quick Setup

```bash
make install-user-service   # One-time: render units + enable linger + enable services
make start                  # Start via systemctl --user
```

Or simply run `make start` with `KOAN_SERVICE_MANAGER=systemd-user` set — it
auto-installs the user service on first run.

## What It Does

The install (`koan/systemd/install-user-service.sh`) creates two units in
`~/.config/systemd/user/`:

| Unit | Process | Description |
|------|---------|-------------|
| `koan.service` | `run.py` | Agent loop (missions, execution, reflection) |
| `koan-awake.service` | `awake.py` | Messaging bridge (Telegram/Slack) |

Both are `Restart=on-failure` with `RestartSec=10` and `WantedBy=default.target`
so they start when the user manager comes up. `koan.service` `Requires`/`BindsTo`
`koan-awake.service`, so the bridge and loop start and stop together.

### Linger (boot persistence)

A user manager normally exists only while you have an active login session. To
keep Kōan running after you log out — and to start it at boot with **no login at
all** — the installer enables **lingering**:

```bash
loginctl enable-linger <user>
```

If that fails (it can require privileges on locked-down hosts), the installer
prints the exact `sudo loginctl enable-linger <user>` command to run once.

### PATH preservation (important)

Unlike the system installer (`app/systemd_service.py`), the `--user` installer
**keeps** `~/.npm-global/bin` and `~/.local/bin` on the service `PATH`. These hold
the `claude` CLI and the `ocgo` helper used by the provider wrapper; without them
provider calls fail with `ocgo/claude not found`. This is intentional — do not
sanitize the user-service PATH the way the system service does.

## How `make start/stop/status` Work

With `KOAN_SERVICE_MANAGER=systemd-user`, the Makefile drives `systemctl --user`
through a bus-safe prefix that works both from a normal login **and** via
`sudo -niu <user>` (where `XDG_RUNTIME_DIR` / `DBUS_SESSION_BUS_ADDRESS` are
unset). Linger keeps `/run/user/<uid>` alive so the bus is reachable.

| Command | Action |
|---------|--------|
| `make start` | `systemctl --user start koan.service` (auto-installs on first run) |
| `make stop` | `systemctl --user stop koan.service koan-awake.service` |
| `make status` | `systemctl --user status koan.service koan-awake.service` |
| `make restart` | `make stop` + `make start` |

## Viewing Logs

Units write to `logs/run.log` and `logs/awake.log` (via `StandardOutput`/
`StandardError=append:`):

```bash
make logs            # watch live
tail -f logs/run.log
```

`journalctl --user -u koan.service` also works.

## SSH Agent Forwarding

If you use SSH git remotes, `make start` forwards your SSH agent socket so the
managed processes can reach it (`SSH_AUTH_SOCK=<koan_root>/.ssh-agent-sock` in the
unit). See [ssh-setup.md](ssh-setup.md) for details.

## Uninstalling

```bash
make uninstall-user-service                  # stop, disable, remove units
make uninstall-user-service disable-linger=1 # also run loginctl disable-linger
```

This stops and disables both units, removes them from `~/.config/systemd/user/`,
and reloads the user daemon. Lingering is left in place unless you pass
`disable-linger=1`. After uninstalling, unset `KOAN_SERVICE_MANAGER` (or set it
back to the default) and `make start` uses the Python PID manager again.

## Troubleshooting

### `Failed to connect to bus: No such file or directory`

The user bus socket (`/run/user/<uid>/bus`) isn't up yet. On a fresh host where
linger was just enabled, logind starts `user@<uid>.service` asynchronously — the
installer already waits for the socket before its first `--user` call. If you hit
this manually, ensure linger is enabled (`loginctl show-user <user>` →
`Linger=yes`) and that `/run/user/<uid>` exists, then retry.

### Services don't start at boot

Confirm linger is on:

```bash
loginctl show-user "$(id -un)" | grep Linger   # expect Linger=yes
```

If it shows `Linger=no`, run `sudo loginctl enable-linger "$(id -un)"`.

### Provider call fails with `ocgo/claude not found`

The service `PATH` is missing the user bin dirs. Confirm `~/.npm-global/bin` and
`~/.local/bin` are present in the `Environment=PATH=` line of
`~/.config/systemd/user/koan.service`, then `systemctl --user daemon-reload` and
restart.
