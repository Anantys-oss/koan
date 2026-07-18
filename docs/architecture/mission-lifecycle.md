---
type: doc
title: "Mission Lifecycle"
description: "Explains the mission queue format and lifecycle (Pending/In Progress/Done/Failed), org-wide missions, branch prep, direct skill dispatch, scheduling, recovery/retries, and missions.md integrity/size-bound safeguards."
tags: [architecture]
created: 2026-05-28
updated: 2026-07-10
---

# Mission Lifecycle

> **Mission state lives in an authoritative SQLite store** (`instance/missions.db`)
> behind the `MissionStore` port; **`instance/missions.md` is a generated read-only
> export** (edits ignored after the one-time sync). Writes round-trip through the
> store (`utils._locked_missions_rw`: render → `missions.py` transform →
> `reconcile_all` → export). The Markdown format below describes the export/ingest
> text form. See `specs/004-mission-store/` and `specs/components/core.md`.

`koan/app/missions.py` provides the parsing and `content -> content` lifecycle
transforms applied inside the store's write chokepoint (it is no longer a
separate source of truth). See `specs/components/core.md` for the mission-queue
contract (store authority, sanctioned exits from In Progress) and
`specs/components/agent-loop.md` for how the agent loop picks up and executes
a mission end to end.

## Queue Format

Missions are stored (and exported to `missions.md`) in Markdown sections. The
canonical lifecycle is:

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

### In-turn completion before finalization

Finalization runs the instant the one-shot CLI turn ends. There is no event loop
that re-invokes the model afterward, so any command whose result the mission owes
**must finish and be read within the same turn** — otherwise the mission is
finalized Done without it. See `docs/architecture/daemon.md` →
"One-shot execution model".

### Pre-mission branch preparation

Before a mission runs, `git_prep.prepare_project_branch()` fetches refs,
**self-heals an interrupted merge/rebase left by a previously-killed mission**,
stashes dirty state, checks out the project's base branch, and fast-forwards it to the
remote — so each mission starts from a clean, up-to-date base.

**Self-heal:** if a prior mission was killed mid-`merge`/`rebase`/`cherry-pick`
(restart, OOM, stagnation-kill, deploy), the checkout is left with unmerged
paths that git refuses to stash. Prep detects the in-progress operation, aborts
it, and clears any stale `index.lock` before stashing. This is safe because the
next step resets the branch to the remote base regardless. A conflict-free dirty
tree is still stashed, not discarded. When a stash nonetheless fails, the error
names the concrete cause (unmerged / disk full / quota / lock) with a `git
status` snippet.

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

### Verification re-queue (verify-before-completion)

After a mission exits successfully, the RARV Verify phase
(`mission_verifier.py`) checks that the work actually matches the mission title
(meaningful changes, tests added, a PR created). When verification fails,
`run_post_mission()` signals a re-queue and `_finalize_mission()` moves the
mission back to **Pending** with a `[verify-failed: <summary>]` context tag
instead of completing it. On a successful (exit 0) mission the only check that
can FAIL is `check_diff_coherence` (an empty branch) — the other checks only
PASS/WARN/SKIP — so a single failure is already a strong, unambiguous signal,
and requiring two would make the re-queue unreachable.

- The re-queue is restricted to **code missions** (`_is_code_mission()`): an
  empty branch is the *expected* outcome for an analysis / no-code mission, so
  those complete normally regardless of `check_diff_coherence`. This avoids
  re-running a no-code mission twice and emitting false failure notices.
- The cap is `verification.max_requeue` in `config.yaml` (default **2**); `0`
  disables the verify re-queue entirely.
- Re-queues use a dedicated `verify_count` sub-counter in
  `instance/.mission-retries.json`, isolated from the stagnation `count` and
  crash-recovery `crash_count`, but still feeding the shared `total_attempts`
  so the `max_total_retries` ceiling applies across all retry systems.
- The `[verify-failed: …]` tag is stripped by `canonical_mission_key()`, so the
  counter stays attached to the same logical mission across cycles, and the tag
  never stacks (a prior tag is replaced on re-queue).
- At the cap (or when `max_total_retries` is hit), the mission **completes
  normally (Done)** — the auto-merge gate (`verify_blocking`) already kept the
  draft PR out of `main`, so a human reviews the draft rather than the work
  being marked Failed. Each re-queue sends a Telegram notification.

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
