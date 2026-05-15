# Hooks

Koan discovers lifecycle hooks from two locations at startup:

1. **Instance-wide hooks** — `.py` files in `instance/hooks/` that export a
   `HOOKS` dict. These run for every event, across all skills and projects.
2. **Skill-bound hooks** — `<event>.py` files placed next to a custom skill's
   `handler.py` (e.g. `instance/skills/<scope>/<name>/post_mission.py`).
   These run *after* instance-wide hooks and let a skill own its full
   workflow without touching Koan core.

Hooks are **fire-and-forget**: errors are logged to stderr but never block the
agent. Files starting with `_` or `.` are skipped.

## Scope & trust

Both flavors execute with the agent's full process privileges. Anything dropped
under `instance/hooks/` or `instance/skills/<scope>/<name>/<event>.py` runs:

- at **startup** (the module is imported and its top-level code executes), and
- on **every** matching lifecycle event — for every project, every mission,
  regardless of whether the skill that owns the hook was the one invoked.

A skill-bound `post_mission.py` does **not** auto-filter to missions targeting
its own skill. If you want skill-scoped behaviour, gate it explicitly inside
`run()` (see the example below). Treat the `instance/skills/` tree as trusted
code: a third-party skill cloned in from a Git remote can do anything your
agent process can do.

## Instance-wide hook format

```python
def on_post_mission(ctx):
    """Called after the post-mission pipeline completes."""
    project = ctx["project_name"]
    title = ctx["mission_title"]
    print(f"Mission done: {title} on {project}")

HOOKS = {
    "post_mission": on_post_mission,
}
```

## Skill-bound hook format

Drop a file named after the event (e.g. `post_mission.py`) inside your skill
directory and export a `run(ctx)` function. No `HOOKS` dict required — the
file name *is* the event name.

```
instance/skills/my/fix/
├── SKILL.md
├── handler.py          # runs at command receipt
└── post_mission.py     # runs after every mission — gate inside run()
```

The hook fires on every `post_mission` event, not only on missions that
invoked this skill. Filter explicitly when you want skill-scoped behaviour:

```python
# instance/skills/my/fix/post_mission.py
def run(ctx):
    # Skip missions that don't belong to this skill.
    if "/myfix" not in ctx.get("mission_title", ""):
        return
    # ... skill-owned post-mission work ...
```

Recognized filenames: `session_start.py`, `session_end.py`, `pre_mission.py`,
`post_mission.py`.

## Available events

| Event | When | Context keys |
|-------|------|-------------|
| `session_start` | After startup completes | `instance_dir`, `koan_root` |
| `session_end` | On shutdown (finally block) | `instance_dir`, `total_runs` |
| `pre_mission` | Before Claude execution | `instance_dir`, `project_name`, `project_path`, `mission_title`, `autonomous_mode`, `run_num` |
| `post_mission` | After post-mission pipeline | `instance_dir`, `project_name`, `project_path`, `exit_code`, `mission_title`, `duration_minutes`, `result`, `result_text` |

`result_text` is the truncated Claude stdout summary (up to 4000 chars) —
useful for parsing JIRA keys, PR URLs, or `RESULT:` lines without re-reading
the stdout capture file.

## Tips

- Hooks must be fast. For slow operations (HTTP calls), use threading internally.
- Hooks are discovered once at startup. Restart to pick up new hooks.
- Use `.py.example` extension for template files to prevent auto-discovery.
- The `result` dict in `post_mission` is a snapshot copy — modifying it has no effect.

## Testing skill-bound hooks

A skill that ships a hook should ship its tests alongside, so the hook and
its verification travel together (especially important when the skill lives
in a separate git repo symlinked into `instance/skills`).

Convention:

```
instance/skills/my/fix/
├── SKILL.md
├── handler.py
├── post_mission.py            # the hook
└── tests/
    ├── conftest.py            # bootstraps sys.path + KOAN_ROOT
    └── test_post_mission.py
```

The `conftest.py` injects `<koan>/koan` into `sys.path` so `app.*` imports
resolve, and sets `KOAN_ROOT` if unset. Copy it verbatim from an existing
skill (e.g. `instance/skills/<scope>/<name>/tests/conftest.py`).

Run skill-local tests:

```bash
make test-skills           # discovers and runs every instance/skills/**/tests/
make test                  # repo tests + skill tests (chained)
```

Direct invocation also works:

```bash
# From the koan workspace root:
pytest instance/skills/<scope>/<name>/tests/ -v

# From the skill's tests directory:
cd instance/skills/<scope>/<name>/tests && pytest .

# From anywhere else, point at the koan workspace:
KOAN_REPO=/path/to/koan pytest /path/to/koan/instance/skills/<scope>/<name>/tests/
```
