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
`post_mission.py`, `post_review.py`.

## Available events

| Event | When | Context keys |
|-------|------|-------------|
| `session_start` | After startup completes | `instance_dir`, `koan_root` |
| `session_end` | On shutdown (finally block) | `instance_dir`, `total_runs` |
| `pre_mission` | Before Claude execution | `instance_dir`, `project_name`, `project_path`, `mission_title`, `autonomous_mode`, `run_num` |
| `post_mission` | After post-mission pipeline | `instance_dir`, `project_name`, `project_path`, `exit_code`, `mission_title`, `duration_minutes`, `result`, `result_text` |
| `post_review` | After a PR review is successfully posted | `instance_dir`, `project_name`, `project_path`, `owner`, `repo`, `pr_number`, `pr_url`, `review_summary`, `review_data`, `lgtm`, `verdict_submitted`, `closed`, `ultra` |

`result_text` is the truncated Claude stdout summary (up to 4000 chars) —
useful for parsing JIRA keys, PR URLs, or `RESULT:` lines without re-reading
the stdout capture file.

## slim_review_post.py — automatic lightweight code review

Copy `slim_review_post.py` **and** `slim_review_prompt.md` to `instance/hooks/`
(no `.example` suffix to drop — the module is inert until enabled in config).
Runs a haiku-powered review on the diff of any PR created during a mission;
findings land in the project's daily journal.

**Setup:**

1. Copy `slim_review_post.py` and `slim_review_prompt.md` to `instance/hooks/`
2. Add to `instance/config.yaml`:
   ```yaml
   slim_review_hook:
     enabled: true          # master switch (default: false)
   ```
3. Restart Koan

**Behavior:**

- Only triggers on successful missions (`exit_code == 0`) that created a PR
- Skips `/review`, `/rebase`, `/slim_review`, and `/review_rebase` missions
  (prevents review-of-a-review loops)
- Deduplicates by diff content hash — the same diff is never reviewed twice,
  but new commits pushed to the same PR re-trigger analysis
- Runs the Claude call in a daemon thread (5-10s; does not block the loop)
- Findings appear in `instance/journal/YYYY-MM-DD/{project}.md`

**Files:**

| File | Purpose |
|------|---------|
| `slim_review_post.py` | Hook module |
| `slim_review_prompt.md` | Review prompt (customizable) |
| `instance/.slim-review-tracker.json` | Dedup tracker (auto-created) |

## extract_review_lessons.py.example — post_review capture

Copy to `instance/hooks/extract_review_lessons.py` (drop `.example`) and
restart to activate. On every posted PR review it writes a raw metadata
record to `instance/reviews/<pr_number>_<timestamp>.json` (PR info, verdict,
findings; `human_reaction` is null — filled by a future reaction pipeline).
Files accumulate until a future cleanup policy is added — prune manually if
`instance/reviews/` grows large.

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
