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

## Normal Execution

1. The bridge, a command handler, a scheduler, or a GitHub/Jira notification
   appends a pending mission.
2. The agent loop picks a mission during an iteration.
3. `start_mission()` moves it from Pending to In Progress and applies sanity
   checks for stale in-progress work.
4. `mission_runner.py` resolves direct skill dispatch or provider execution.
5. The mission is completed, failed, archived, retried, or requeued based on the
   result and configured guards.
6. Post-mission reflection, journal writing, PR creation, security review, and
   auto-merge checks run only when their conditions apply.

## Worktree Isolation

When `worktree_isolation: true` in `config.yaml`, each mission runs in an
isolated git worktree instead of the main project directory. This prevents
dirty-state conflicts from prior missions or external processes.

Flow: `create_worktree()` → run CLI in worktree → cleanup in `finally` block.
Branches created in the worktree are visible from the main repo because
worktrees share the git object store. The feature falls back gracefully to the
main project directory if worktree creation fails.

At startup, `_cleanup_orphaned_worktrees()` in `startup_manager.py` removes
any `.worktrees/` entries left by crashed sessions.

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
