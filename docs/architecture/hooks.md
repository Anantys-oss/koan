---
type: doc
title: "Lifecycle Hooks & Automation Rules"
description: "Documents the lifecycle-event system (session_start/session_end/pre_mission/post_mission/post_review): instance-wide and skill-bound Python hooks via `HookRegistry`, the declarative automation-rules layer (notify/create_mission/pause/resume/auto_merge) with its per-rule loop guard, and the project-declared `hooks.<event>` skill lists read from a repo's own `.koan/config.yaml`."
tags: [architecture]
created: 2026-07-08
updated: 2026-07-17
---

# Lifecycle Hooks & Automation Rules

Kōan's agent loop fires named lifecycle events at fixed points; three
independent mechanisms subscribe to them: **hooks** (arbitrary user-written
Python), **automation rules** (declarative YAML mapped to a fixed action set),
and **project hook skills** (a reviewed repo naming its own Claude Code skills
in `.koan/config.yaml`). All three are implemented in `koan/app/hooks.py`.

The first two are the operator's; the third belongs to the repo being worked
on, which is why it is the most tightly constrained of the three.

## Events

| Event | When | Key context keys |
|---|---|---|
| `session_start` | After startup completes | `instance_dir`, `koan_root` |
| `session_end` | On shutdown (`finally` block) | `instance_dir`, `total_runs` |
| `pre_mission` | Before Claude CLI execution | `instance_dir`, `project_name`, `project_path`, `mission_title`, `autonomous_mode`, `run_num` |
| `post_mission` | After the post-mission pipeline completes | `instance_dir`, `project_name`, `project_path`, `exit_code`, `mission_title`, `duration_minutes`, `result`, `result_text` |
| `post_review` | After a PR review is successfully posted (`run_review`) | `instance_dir`, `project_name`, `project_path`, `owner`, `repo`, `pr_number`, `pr_url`, `review_summary`, `review_data`, `lgtm`, `verdict_submitted`, `closed`, `ultra` |

`result_text` is the truncated Claude stdout summary (up to 4000 chars) — useful
for parsing JIRA keys, PR URLs, or `RESULT:` lines without re-reading the full
stdout capture file. `result` is a snapshot copy; mutating it inside a handler
has no effect.

`post_review` fires only on the public-PR posting path (`run_review`), not
`run_private_review` (which posts nothing to GitHub). Human reaction is not
known at fire time — the default capture hook records `human_reaction: null`
for a later reaction-capture pipeline.

Fired from `startup_manager.py` (`session_start`), `run.py` (`session_end`),
`mission_executor.py` (`pre_mission`), `mission_runner.py` (`post_mission`),
and `review_runner.py` (`post_review`).

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
   Only the five event filenames above are recognized; any other `.py` in a
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
maps one of the same five events to a fixed action:

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

## Project hook skills (`.koan/config.yaml`)

A reviewed repo can name skills to run when an event fires, without the
operator writing any Python:

```yaml
hooks:
  post_review:
    - cp-docs-string-chain
```

Keys are the event names above; values are lists of skill names. For each name,
`_fire_project_hook_skills` queues a pending mission that runs that skill.
Read via `project_koan.get_hook_skills(project_path, event)`, so it only
applies to events whose context carries a `project_path` (`pre_mission`,
`post_mission`, `post_review`).

**Queued, not executed.** Handlers run inline in the firing process and a skill
pipeline can take minutes. Queuing also puts the work on the mission loop,
which — unlike the read-only review subprocess — loads the project's own
`.claude/skills`, can invoke the `Skill` tool, and does not have MCP stripped.
That difference is the point of the mechanism: it is how a repo wires follow-up
work that a review pass is deliberately not allowed to do.

**The repo supplies names; Kōan composes the sentence.** Names must match
`^[a-z0-9][a-z0-9-]*$`, capped at 10 per event, with anything else dropped and
a warning logged. This is a security boundary, not tidiness: whoever can open a
pull request can commit `.koan/config.yaml`, and the value reaches the prompt of
a *write-capable* agent. A name that could carry an instruction, a path or a
shell fragment is refused rather than sanitized. Contrast the operator-side
mechanisms above, which may run arbitrary code because the operator owns them.

**Idempotent per subject.** `insert_pending_mission` only de-duplicates entries
shaped like `/<command> <github-url>`, so this path does its own check against
the pending and in-progress sections, keyed on the subject (the PR URL, or the
mission title) plus a delimited `[hook-skill:<name>]` marker stamped into each
queued entry. Matching that exact marker rather than a bare substring means a
shorter name (`docs`) is never masked by an already-queued longer one
(`docs-lint`). Re-reviewing the same PR does not queue the same work twice; a
different PR queues separately.

**No self-replication.** The mission this queues carries the `[hook-skill:…]`
marker in its own title, so a repo naming a skill under `pre_mission` or
`post_mission` would otherwise re-queue it every time that mission ran, without
bound. `_fire_project_hook_skills` sees the marker in the firing context and
queues nothing — a hook-skill mission never spawns further hook skills.

Fail-safe throughout: an absent, empty, or malformed `.koan/config.yaml` is a
no-op, and a failure here never disturbs the event that fired.

## When to reach for which

- **Hook** — arbitrary logic, external HTTP calls, custom parsing of
  `result_text`. Ship code, own your own error handling (keep it fast; use
  threading internally for slow I/O since hooks execute inline in the
  triggering process).
- **Automation rule** — one of the five built-in actions, configured without
  writing Python, editable at runtime via the dashboard.
- **Project hook skill** — the *repo* decides what runs after an event on its
  own code, committed alongside it and reviewable in a pull request. Choose
  this when the behavior belongs to the project rather than the operator, and
  when naming a skill is enough. It cannot express logic or run commands.

See `instance.example/hooks/README.md` for the full worked examples,
including the convention for shipping tests alongside a skill-bound hook
(`instance/skills/<scope>/<name>/tests/`, discovered by `make test-skills`).
