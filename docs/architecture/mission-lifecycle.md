# Mission Lifecycle

## Data/view split

`instance/missions.json` is the canonical mission store. `instance/missions.md`
is a **generated view** — it is regenerated from JSON on every save and must
never be written directly by code.

All queue mutations go through `mission_store.locked_store(instance_dir)`, which
holds a thread+file lock across the full load→mutate→save cycle, then calls
`MissionStore.save()` to atomically write the JSON and regenerate the Markdown
view. Human edits to `missions.md` are detected by a sha256 hash stored in the
JSON sidecar; when the hash diverges the store reconciles the edits back into
the structured records before the next save.

`koan/app/missions.py` provides the Markdown parser (`parse_sections()`,
`extract_project_tag()`) and `canonical_mission_key()` — the single source of
truth for stable mission identity. Legacy string-transform functions
(`start_mission()`, `complete_mission()`, `fail_mission()`) remain for any
callers not yet migrated; prefer the store mutators for new code.

## Queue Format

Missions are stored in four lifecycle sections. The canonical order is:

- In Progress
- Pending
- Done
- Failed

French section names are also accepted for compatibility. Missions can include
project tags such as `[project:name]`.

### Org-wide missions (`[project:all]`)

A mission tagged `[project:all]` (or a recurring entry with `"project": "all"`)
is an **org-wide** mission: it targets every repository in the workspace
instead of a single project. The engine resolves it to the workspace root
(`<KOAN_ROOT>/workspace`) as its working directory and launches it **once** —
the mission's own instructions are responsible for iterating over each repo
(e.g. enumerating `workspace/*/` and operating on each, optionally via
sub-agents). Engine-level git branch preparation and auto-merge are skipped for
org-wide missions, because there is no single repo to branch; each repo's git
work (branches, PRs) is handled inside the mission.

`all` is a reserved sentinel resolved in
`iteration_manager._resolve_project_path`. A real project literally named `all`
still takes precedence over the sentinel. Missions with **no** project tag keep
their previous behaviour (they default to the first configured project), so
single-project setups are unaffected. To scope which repos an org-wide mission
touches, exclude repos at the workspace-sync layer (they simply never get cloned
into `workspace/`).

## Normal Execution

1. The bridge, a command handler, a scheduler, or a GitHub/Jira notification
   appends a pending mission via `utils.insert_pending_mission()` (which calls
   `locked_store()` internally) or directly via `locked_store()` + `store.add()`.
2. The agent loop picks a mission during an iteration.
3. `store.start()` (inside `locked_store()`) moves it from Pending to In Progress
   and flushes any stale in-progress records to Failed with `[flushed]`.
4. `mission_runner.py` resolves direct skill dispatch or provider execution.
5. The mission is completed (`store.complete()`), failed (`store.fail()`),
   requeued (`store.requeue()`), or retried based on the result and configured guards.
6. Post-mission reflection, journal writing, PR creation, security review,
   auto-merge checks, and autoreview queuing run only when their conditions apply.

## Direct Skill Missions

`skill_dispatch.py` detects slash-command missions that can run without a full
LLM agent session. These runners handle commands such as planning, rebasing,
recreating, checking, and CLAUDE.md refresh flows. Prompt-only or unsupported
missions continue through the configured provider.

## Scheduled And Recurring Work

- One-shot scheduled missions live under `instance/events/` and are consumed by
  `event_scheduler.py`.
- Recurring work is injected by the iteration path through recurring scheduler
  helpers.
- Suggestion generation can propose automation but should not silently enable it.

## Recovery And Retries

Crash recovery moves stale In Progress work back to a safe state. Stagnation
retries are tracked separately so a stuck provider session can be retried a
limited number of times before regular failure handling and user notification.
