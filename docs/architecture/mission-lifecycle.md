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

### Pre-mission branch preparation

Before a mission runs, `git_prep.prepare_project_branch()` fetches refs, stashes
dirty state, checks out the project's base branch, and fast-forwards it to the
remote — so each mission starts from a clean, up-to-date base.

**Launching-repo exception:** when the project being prepared resolves to the
same directory as `KOAN_ROOT` (a self-hosting setup where Kōan works on the repo
that launched it) **and** that repo is currently on a custom branch, prep leaves
it untouched instead of switching to the base branch. This lets an operator
check out a development branch and test it without Kōan resetting it to `main`.
The exception applies only to the launching repo — every other managed project
still resets to its base branch before each mission.

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

## File Integrity And Size Bounds

`missions.md` is on the hot path of every loop iteration, so a malformed write
can silently degrade the whole agent. `startup_manager.prune_missions_done`
(run at startup) and `run._prune_missions_history` (run post-mission) keep the
file healthy:

- **Validation** — `validate_missions_structure()` checks the canonical
  sections are present exactly once, that no `## ` header is glued to the
  preceding item (the production corruption mode; the `# Missions` H1 title
  sitting above the first section is not flagged), and that no item lines fall
  before any section header. Content inside ` ``` ` code fences (mission bodies
  routinely contain fenced markdown with `## ` / `- ` lines) is treated as text,
  never structure.
- **Self-heal** — `repair_missions_structure()` resolves every issue validation
  classifies as serious, so re-validating its output is always clean (it
  converges, never re-alarming a stuck file): it restores missing blank lines
  around headers, merges duplicate canonical sections into their first
  occurrence, re-homes orphan items found before any header under `## Pending`,
  and appends any missing canonical sections — never dropping mission lines
  (Pending/In Progress items and non-canonical sections like Ideas are
  preserved, fenced mission-body content left verbatim). A
  merely incomplete file (e.g. a fresh install without `## Failed`) is healed
  silently; genuine corruption is first backed up to
  `instance/.missions.md.bak-<ts>` and surfaced to the operator via the outbox.
  If the backup write fails, the destructive repair is skipped entirely and the
  corrupt file is left untouched — the only copy of the data is never
  overwritten without a verified backup. Backups are capped at the 5 most
  recent.
- **Size bounds** — `enforce_size_bound()` prunes Done/Failed history to the
  configured keeps, then progressively sheds more old completed entries until
  the file is under the line cap. Pending and In Progress are never pruned.

Configurable under a `missions:` section in `config.yaml`:

```yaml
missions:
  done_keep: 50      # max Done items retained
  failed_keep: 30    # max Failed items retained
  max_lines: 500     # hard line cap (0 disables)
```
