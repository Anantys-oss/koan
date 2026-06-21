# Shared State

Koan intentionally uses local files instead of a database. This keeps setup
simple and makes state inspectable by humans and agents.

## Instance Directory

`instance/` is gitignored runtime state. Important files and directories include:

- `missions.json` - canonical structured mission store (source of truth).
- `missions.md` - human-readable view of the queue, **generated** from
  `missions.json`. Never write it directly; mutate via
  `mission_store.locked_store()` (or `MissionStore.save()`), which regenerates
  the view atomically.
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

The mission queue is the canonical example: all mutations go through
`mission_store.locked_store()`, which holds a thread + file lock across the full
load → mutate → save cycle and regenerates `missions.md` from `missions.json`.
`missions.md` is a read-only view — no code writes it directly. New code that
needs to queue or change a mission must use the store (or the
`utils.insert_pending_mission(text, project=None, *, urgent=False)` helper, which
wraps it), never a hand-rolled text edit of `missions.md`. The `project` parameter
accepts `str | None`; `None` and `""` both mean "no project" and are normalized
internally.

The bridge and runner are separate processes, so bugs that are harmless in a
single process can corrupt state when both daemons are active.

## Scratch Files And Provider Locks

Transient scratch files (captured stdout/stderr, prompt files, generated plugin
dirs) and the provider invocation lock do **not** live in `instance/` — they live
under a per-uid temp directory returned by `utils.koan_tmp_dir()`:

- `$XDG_RUNTIME_DIR/koan` when `XDG_RUNTIME_DIR` is set (Linux/systemd), else
  `/tmp/koan-<uid>/`, created mode `0700`. Overridable with `KOAN_TMP_DIR`.
- It is per-**uid**, not per-instance: provider auth/session state is stored in
  the user's home directory, so two Kōan instances run by the same user must
  still serialize on the same provider lock.

This isolation is what lets multiple users run Kōan on one host without colliding
on shared `/tmp` paths. Code that needs a temp file MUST pass `dir=koan_tmp_dir()`
to `tempfile.*`; agent prompts that write to `/tmp` MUST use a `mktemp` pattern
rather than a fixed filename. See [Troubleshooting](../operations/troubleshooting.md).

## Configuration Sources

- Project configuration primarily comes from `projects.yaml` at `KOAN_ROOT`.
- Environment configuration comes from `.env` and `KOAN_*` variables.
- Instance behavior comes from `instance/config.yaml`.
- Provider selection uses `KOAN_CLI_PROVIDER`, with legacy fallback support.

Prefer existing config helper modules over reading environment variables or YAML
directly from new code.
