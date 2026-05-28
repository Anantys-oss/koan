# Shared State

Koan intentionally uses local files instead of a database. This keeps setup
simple and makes state inspectable by humans and agents.

## Instance Directory

`instance/` is gitignored runtime state. Important files and directories include:

- `missions.md` - mission queue and lifecycle sections.
- `outbox.md` - pending outbound messages for the bridge.
- `config.yaml` - instance behavior and integration configuration.
- `memory/` - global and per-project memory files.
- `journal/` - daily logs and reflections.
- `events/` - scheduled mission JSON files.
- `hooks/` - user-defined lifecycle hooks.
- hidden tracker files for pause, focus, passive mode, usage, CI dispatch,
  review dispatch, burn rate, and similar daemon state.

`instance.example/` documents the expected shape of a fresh instance.

## Locking And Atomic Writes

Shared files must be written with existing helpers such as `atomic_write()` and
file-locking utilities from `utils.py` or dedicated state modules. Avoid direct
read-modify-write cycles on `instance/` files unless the code already owns the
appropriate lock.

The bridge and runner are separate processes, so bugs that are harmless in a
single process can corrupt state when both daemons are active.

## Configuration Sources

- Project configuration primarily comes from `projects.yaml` at `KOAN_ROOT`.
- Environment configuration comes from `.env` and `KOAN_*` variables.
- Instance behavior comes from `instance/config.yaml`.
- Provider selection uses `KOAN_CLI_PROVIDER`, with legacy fallback support.

Prefer existing config helper modules over reading environment variables or YAML
directly from new code.
