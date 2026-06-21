# Interactive launcher (`make koan`)

`make koan` is a TTY-gated front door for starting Kōan. It complements — and
does not replace — `make start`, which remains the non-interactive launcher
used by launchd/systemd services, CI, and scripts.

## What it does

In a terminal, `make koan`:

1. Clears the screen for a clean slate.
2. Runs/resumes the CLI onboarding wizard first if no `instance/` exists or
   `.koan-onboarding.json` is present.
3. Starts the stack (agent + bridge) via `start_all(show_banner=False)`.
4. Opens the terminal dashboard directly — **no mode prompt after setup**.

Quitting the dashboard with `q` tears the stack down cleanly
(`stop_processes`). When stdin is not a TTY (services, CI, pipes) `make koan`
delegates to the headless `start_all` path with no prompt, identical to
`make start`. If `textual` is missing, Kōan stays running and the launcher
points you at `make logs`.

## Terminal dashboard

A [textual](https://textual.textualize.io/) TUI over Kōan's shared files
(`logs/*.log`, `instance/config.yaml`, `instance/usage.md`, mission/pause
signal files). Four tabs:

| Tab | Contents |
|-----|----------|
| **Status** (home) | Hero banner + live flags: run state, in-progress mission titles, Telegram/bridge status, usage bars, and single-tap toggles for the web dashboard and keep-awake |
| **Logs** | Live tail of `run.log` + `awake.log` (ANSI preserved, auto-scrolling) |
| **Config** | Collapsible tree of `config.yaml` with inline editing (comment-preserving); booleans toggle in place |
| **Usage** | Session/weekly progress bars, autonomous mode, burn rate |

### Toggles (accent dot: `◉` on / `○` off)

- **`w` — web dashboard**: start/stop the Flask web UI process and open the
  browser at `localhost:5001` on start. Backed by `start_dashboard` /
  `stop_process`.
- **`k` — keep awake**: runs `caffeinate -s` (macOS) or `systemd-inhibit`
  (Linux) so the machine doesn't sleep while Kōan works. **On by default**;
  tap `k` to turn it off. The process is reaped on exit. No-op where neither
  tool exists.

### Keys

- `1`/`2`/`3`/`4` (or aliases `s`/`l`/`u`/`c`) — switch to
  Status/Logs/Usage/Config. These work even while the config tree holds focus.
- `m` — queue a new mission via the mission store (modal input; supports
  `[project:name]` tags).
- Logs tab: Up/Down scroll one line; Page Up/Page Down scroll one page.
- Arrow keys browse the focused config tree; Enter (or click) edits the
  selected scalar; `t` toggles a boolean in place (Enter also flips booleans).
- `w` web dashboard, `k` keep-awake, `p` pause, `r` reload.
- `d` — **detach**: close the dashboard but leave Kōan running.
- `q` — **quit**: stop Kōan (with a confirmation prompt).

State-mutating actions are limited to: pause (`.koan-pause`, same signal the
bridge uses), config edits (`instance/config.yaml`, comments preserved),
queueing a mission (via `insert_pending_mission`, which mutates the store under lock and regenerates `missions.md`),
and the two toggles.

## Theme

The Anantys palette and helpers live in `koan/app/banners/theme.py` (truecolor
with a 16-color fallback, `NO_COLOR` honoured). The KŌAN hero art is in
`koan/app/banners/koan_hero.txt`. Reused by the launcher and the dashboard's
Status tab. No emojis — plain glyphs and box-drawing only.

See also: [dashboard.md](dashboard.md) for the web dashboard.
