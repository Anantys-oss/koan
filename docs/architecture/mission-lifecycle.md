# Mission Lifecycle

`koan/app/missions.py` is the source of truth for parsing and mutating
`instance/missions.md`.

## Queue Format

Missions are stored in Markdown sections. The canonical lifecycle is:

- Pending
- In Progress
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
   appends a pending mission.
2. The agent loop picks a mission during an iteration.
3. `start_mission()` moves it from Pending to In Progress and applies sanity
   checks for stale in-progress work.
4. `mission_runner.py` resolves direct skill dispatch or provider execution.
5. The mission is completed, failed, archived, retried, or requeued based on the
   result and configured guards.
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
