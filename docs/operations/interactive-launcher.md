# Interactive launcher (`make koan`)

`make koan` is a TTY-gated, themed front door for starting Kōan. It complements
— and does not replace — `make start`, which remains the non-interactive
launcher used by launchd/systemd services, CI, and scripts.

## What it does

1. Renders the Anantys-themed startup banner (midnight + mint, pixel gradient).
2. Surfaces config drift between `instance/config.yaml` and
   `instance.example/config.yaml` — **display only**, it never modifies your
   config. Missing keys are shown with `+`, extras with `~`. Run
   `/config_check` for the full report.
3. Prompts for a supervision mode via an arrow-key selector:

   | Mode | Behaviour | On exit |
   |------|-----------|---------|
   | Web dashboard | Starts the stack + web UI, opens the browser, stays foreground | Ctrl-C → `stop_processes` |
   | Terminal view | Starts the stack + terminal dashboard (textual) | `q` → `stop_processes` |
   | Headless | Starts the stack and returns (same as `make start`) | Kōan keeps running |

## Backward compatibility

- `make start` is untouched — same `pid_manager start-all` path, same banner.
- When stdin is not a TTY, `make koan` delegates to the headless `start_all`
  path with no prompt, so it is safe to call from non-interactive contexts.
- `start_all(show_banner=...)` lets the launcher suppress the duplicate banner
  it has already drawn.

## Terminal dashboard

The terminal view is a read-only [textual](https://textual.textualize.io/) TUI
over Kōan's shared files (`logs/run.log`, `logs/awake.log`, `instance/config.yaml`,
`instance/usage.md`). Tabs: Logs / Config / Usage. The only state-mutating
action is `p` (pause), which writes `.koan-pause` through the same helper the
bridge uses. `textual` is installed by `make setup`; if absent, the launcher
falls back to `make logs`.

Keys: `1`/`2`/`3` switch to Logs/Config/Usage (these work even while the
config tree holds keyboard focus), arrow keys browse the focused config tree,
Enter (or click) edits the selected scalar, `t` toggles a boolean in place
(Enter also flips booleans, no typing), `p` pauses, `r` reloads, `q` quits.
The Config tab edits `instance/config.yaml` in place, preserving comments.

## Theme

The Anantys palette and helpers live in `koan/app/banners/theme.py` (truecolor
with a 16-color fallback, `NO_COLOR` honoured). They are reused by the startup
banner, the launcher, and the terminal dashboard. No emojis — plain glyphs and
box-drawing only.

See also: [dashboard.md](dashboard.md) for the web dashboard.
