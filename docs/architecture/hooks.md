---
type: doc
title: "Lifecycle Hooks & Automation Rules"
description: "Documents the lifecycle-event system (session_start/session_end/pre_mission/post_mission): instance-wide and skill-bound Python hooks via `HookRegistry`, plus the declarative automation-rules layer (notify/create_mission/pause/resume/auto_merge) with its per-rule loop guard."
tags: [architecture]
created: 2026-07-08
updated: 2026-07-08
---

# Lifecycle Hooks & Automation Rules

Kōan's agent loop fires named lifecycle events at fixed points; two independent
mechanisms subscribe to them: **hooks** (arbitrary user-written Python) and
**automation rules** (declarative YAML mapped to a fixed action set). Both are
implemented in `koan/app/hooks.py`.

## Events

| Event | When | Key context keys |
|---|---|---|
| `session_start` | After startup completes | `instance_dir`, `koan_root` |
| `session_end` | On shutdown (`finally` block) | `instance_dir`, `total_runs` |
| `pre_mission` | Before Claude CLI execution | `instance_dir`, `project_name`, `project_path`, `mission_title`, `autonomous_mode`, `run_num` |
| `post_mission` | After the post-mission pipeline completes | `instance_dir`, `project_name`, `project_path`, `exit_code`, `mission_title`, `duration_minutes`, `result`, `result_text` |

`result_text` is the truncated Claude stdout summary (up to 4000 chars) — useful
for parsing JIRA keys, PR URLs, or `RESULT:` lines without re-reading the full
stdout capture file. `result` is a snapshot copy; mutating it inside a handler
has no effect.

Fired from `startup_manager.py` (`session_start`), `run.py` (`session_end`),
`mission_executor.py` (`pre_mission`), and `mission_runner.py` (`post_mission`).

## Hooks

`HookRegistry` discovers hook modules once at startup (`init_hooks()`) from two
locations, in this order:

1. **Instance-wide hooks** — any `.py` file directly under `instance/hooks/`
   exporting a `HOOKS` dict (`{event_name: callable}`). Run for every event,
   across all projects and skills.

   ```python
   def on_post_mission(ctx):
       print(f"Mission done: {ctx['mission_title']}")

   HOOKS = {"post_mission": on_post_mission}
   ```

2. **Skill-bound hooks** — `instance/skills/<scope>/<name>/<event>.py`, where
   the *filename* is the event name (e.g. `post_mission.py`) and the module
   exports a `run(ctx)` function instead of a `HOOKS` dict. Lets a custom
   skill own its lifecycle behavior next to its `handler.py` without touching
   Kōan core. These run *after* all instance-wide hooks for the same event.
   Only the four event filenames above are recognized; any other `.py` in a
   skill directory (`handler.py`, `helpers.py`, ...) is ignored for hook
   discovery.

   A skill-bound hook fires on **every** matching event, not only missions
   dispatched by its own skill — gate explicitly inside `run()` if
   skill-scoped behavior is needed (e.g. check `"/myfix" in ctx["mission_title"]`).

Restart to pick up new or changed hook files — discovery happens once at
startup, not on every fire.

### Execution model

- `fire(event, **kwargs)` calls every registered handler for that event in
  registration order, wrapping each call in its own try/except.
- **Fire-and-forget**: a handler that raises logs a traceback to stderr but
  never blocks the agent loop or subsequent handlers.
- Files/directories starting with `_` or `.` are skipped by discovery; use a
  `.py.example` suffix for templates that should not be auto-loaded.
- **Trust model**: hooks run with the agent process's full privileges.
  `instance/skills/` is effectively trusted code — a third-party skill cloned
  from a Git remote can do anything the agent process can do.

## Automation rules

After user hook modules run, `fire()` also evaluates declarative rules loaded
from `instance/automation_rules.yaml` (`app/automation_rules.py`). Each rule
maps one of the same four events to a fixed action:

```yaml
- id: "abc123"
  event: "post_mission"
  action: "notify"
  params:
    message: "Mission completed!"
  enabled: true
  created: "2026-01-01T12:00:00"
```

Supported actions: `notify` (append a line to `instance/outbox.md`),
`create_mission` (insert into the Pending section of `instance/missions.md`),
`pause` / `resume` (drive `pause_manager.py`, going through `create_pause()` so
the standard 5h auto-resume cooldown applies rather than writing a malformed
pause file directly), and `auto_merge` (invoke
`git_auto_merge.auto_merge_branch()` for the mission's project/branch).

- **Loop guard**: each rule tracks its own in-memory fire timestamps over a
  60s window; once a rule exceeds `automation_rules.max_fires_per_minute`
  (config default 5) further fires that minute are skipped and logged.
- Every successful fire appends a `[automation_rule]`-tagged line to
  `instance/journal/<date>/automation.md`.
- Rules are CRUD-managed from the dashboard's `/rules` page (see
  [Dashboard](../operations/dashboard.md)); there is no Telegram skill for
  them today.

## When to reach for which

- **Hook** — arbitrary logic, external HTTP calls, custom parsing of
  `result_text`. Ship code, own your own error handling (keep it fast; use
  threading internally for slow I/O since hooks execute inline in the
  triggering process).
- **Automation rule** — one of the five built-in actions, configured without
  writing Python, editable at runtime via the dashboard.

See `instance.example/hooks/README.md` for the full worked examples,
including the convention for shipping tests alongside a skill-bound hook
(`instance/skills/<scope>/<name>/tests/`, discovered by `make test-skills`).
