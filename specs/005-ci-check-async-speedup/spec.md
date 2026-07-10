# Feature Specification: `/ci_check` async speedup — stop starving the mission queue

**Feature Branch**: `koan.atoomic/ci-check-speedup` (speckit dir `005-ci-check-async-speedup`)

**Created**: 2026-07-10

**Status**: Draft

**Input**: User description: "Investigate why a `/ci_check` mission takes so much time on the
production instance — it looks like it waits forever on some state. `/ci_check` is meant to be a
quick async check that does not block the whole queue and only adjusts the PR on failures. Improve
and speed it up without holding/waiting forever, letting the queue progress while preserving the
original intent."

## Context (from investigation — production logs on this instance)

The CI-check system has two halves:

- **Async monitor** — `ci_queue_runner.drain_one()`, called every ~30s from the interruptible
  sleep loop (`loop_manager._drain_ci_queue_during_sleep`). It reads the `## CI` section of
  `missions.md`, does **one** non-blocking CI status check per PR, and on failure injects a
  `/ci_check <url>` fix mission. This half is already non-blocking and correct.
- **Fix mission** — the injected `/ci_check <url>` runs as a normal, single-slot mission via
  `ci_queue_runner.run_ci_check_and_fix()`. This half is what blocks.

Production evidence (`instance/missions.md`, `instance/journal/2026-07-06/koan.md`) shows:

- **Urgent queue-jumping.** `_inject_ci_fix_mission()` inserts the fix mission with
  `urgent=True`, so every `/ci_check` jumps to the front of the single-threaded queue. In the
  ledger every `/ci_check` has `⏳ == ▶` (queued time equals start time), while sibling
  `/rebase` / `/review` missions waited **hours** behind them (e.g. a `/rebase` queued 17:26,
  started 19:42).
- **Compounded multi-attempt fix loop.** Each fix mission runs `run_ci_fix_loop()` for up to
  `max_attempts` (the `## CI` item's budget, config `ci_fix_max_attempts`, default **5**)
  sequential Claude fix steps. When CI fails fast after each push, the loop runs all 5 internal
  steps; individual steps were observed at **119s–1575s (up to 26 min)**. drain then re-injects
  another full mission, so total Claude steps for one PR compound toward attempts×attempts.
- **Result.** An unfixable PR (koan #639) consumed **~2h13m of the main queue** across 5
  back-to-back urgent missions, all ending "CI still failing after N fix attempts", starving all
  other work. The 2-hour `skill_timeout` (`get_skill_timeout()`, default 7200s) applied to each
  fix step means one stuck step alone can hold the queue for 2 hours — the "waiting forever on
  some state" symptom.

Root cause is **not** the CI HTTP poll (`wait_for_ci`, the 600s/30s poll loop, is the *rebase*
path; `/ci_check` uses the non-blocking `check_existing_ci` path). The blocking is: (1) urgent
re-injection into a serial queue, and (2) a long, compounding multi-attempt Claude fix loop with a
2-hour per-step cap.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - CI fixing never starves genuine backlog work (Priority: P1)

As an operator, when CI fails on one of Kōan's PRs, I want the automatic CI-fix work to run
**behind** normal missions (rebases, reviews, implements), not ahead of them, so a single flaky or
unfixable PR can never freeze the whole agent for hours.

**Why this priority**: This is the reported failure — the queue "waits forever". Removing the
queue-jump is the single highest-value change and directly restores forward progress.

**Independent Test**: With a `/rebase` and a `/review` already in Pending, trigger a CI failure so
drain injects a `/ci_check`. Assert the `/ci_check` mission is appended after the existing Pending
missions (non-urgent), not inserted at the front.

**Acceptance Scenarios**:

1. **Given** Pending contains `/rebase` and `/review`, **When** `drain_one()` injects a CI-fix
   mission for a failing PR, **Then** the `/ci_check` mission is inserted **non-urgently** (after
   the existing Pending entries).
2. **Given** a CI-fix mission and other missions are queued, **When** the loop runs, **Then** the
   pre-existing missions are not indefinitely blocked behind CI-fix work.

### User Story 2 - Each CI-fix mission is bounded and yields the queue (Priority: P1)

As an operator, I want each injected `/ci_check` mission to perform **at most one** Claude fix
attempt and then return, so the queue gets a turn between attempts. Retries across attempts are
governed by the existing `## CI` attempt counter and interleaved with other work — preserving the
"eventually fix, or give up after N attempts" intent without compounding.

**Why this priority**: Removes the attempts×attempts compounding that turned one PR into 25 back-
to-back Claude steps. Combined with P1-story-1 it fully addresses "taking so much time".

**Independent Test**: Run `run_ci_check_and_fix()` for a failing PR with `ci_fix_max_attempts=5`
and assert the internal `run_ci_fix_loop` is invoked with `max_attempts=1` (per-mission cap), while
the `## CI` drain budget of 5 is unchanged.

**Acceptance Scenarios**:

1. **Given** a failing PR whose `## CI` budget is 5, **When** the fix mission runs, **Then** it
   performs a single Claude fix step, pushes if changes were produced, and returns — it does not
   loop 5 times inside one mission.
2. **Given** the single fix pushed and CI is now pending, **When** the mission finishes, **Then**
   the PR is re-enqueued for monitoring so `drain_one()` continues checking it non-blockingly.
3. **Given** the single fix pushed and CI still fails, **When** the mission finishes, **Then** the
   `## CI` attempt counter drives the next interleaved retry until the budget is exhausted, at which
   point Kōan gives up with the existing 🚦 notification.

### User Story 3 - No single fix step can hang the queue "forever" (Priority: P2)

As an operator, I want a per-step wall-clock and idle guard on the CI-fix Claude step, so a stuck
step is killed in bounded time rather than after the 2-hour `skill_timeout`.

**Why this priority**: Defense-in-depth against the literal "waiting forever on some state" — a
hung provider call or silent step. Lower priority than P1 because the attempt-decoupling already
removes most of the blocking, but a hard cap is what guarantees the queue is never frozen.

**Independent Test**: Assert the CI-fix step runs `run_claude_step` with a bounded overall timeout
(`ci_check.timeout`, default well under `skill_timeout`) and an idle timeout, not the raw
`get_skill_timeout()` value.

**Acceptance Scenarios**:

1. **Given** a CI-fix step, **When** it is launched, **Then** its overall timeout is
   `get_ci_check_step_timeout()` (default 3600s) — not 7200s — and it uses an idle/first-output
   guard so a silent step is killed early.
2. **Given** the step exceeds the bound, **When** the watchdog fires, **Then** the mission ends
   promptly and the queue continues.

### Edge Cases

- **Manual `/ci_check <url>`** (human-typed, no `## CI` entry): runs a single bounded fix attempt
  and reports the outcome. If the fix is pushed and CI goes pending, it is re-enqueued for
  monitoring (gaining the same interleaved retry path as the auto flow). If the fix fails fast with
  no `## CI` entry, it reports "still failing" — a one-shot manual attempt, which is acceptable and
  documented.
- **CI already green / PR merged / closed / blocked on maintainer approval**: unchanged — these
  return early without a fix loop.
- **`ci_check.enabled: false`**: the entire pipeline stays disabled; no behavior change.
- **Duplicate injection**: `_inject_ci_fix_mission` still dedups against a pending/in-progress
  `/ci_check` for the same PR, so non-urgent insertion cannot pile up multiple copies.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The auto-injected CI-fix mission MUST be inserted **non-urgently** (normal FIFO
  Pending order), never queue-jumped ahead of existing missions.
- **FR-002**: A single `/ci_check` fix mission MUST perform at most a configurable number of
  internal Claude fix attempts (default **1**), decoupled from the `## CI` total-attempt budget
  (`ci_fix_max_attempts`, default 5) which continues to govern interleaved retries via
  `drain_one()`.
- **FR-003**: After a fix is pushed and CI is pending, the PR MUST be re-enqueued for non-blocking
  monitoring by `drain_one()` (preserved from current behavior, now also reachable on the manual
  path).
- **FR-004**: The CI-fix Claude step MUST run under a bounded overall timeout
  (`ci_check.timeout`, default 3600s) and an idle guard, instead of the 7200s `skill_timeout`.
- **FR-005**: The async monitor (`drain_one`) MUST remain non-blocking and unchanged in cadence
  (one status check per PR per iteration; throttled to `CI_QUEUE_SLEEP_INTERVAL`).
- **FR-006**: The total-attempt budget and terminal give-up notification (🚦 after N attempts)
  MUST be preserved.
- **FR-007**: New config keys MUST be surfaced through `config.py` accessors and registered in
  `config_validator.py`, with documented defaults, and MUST default to safe values that do not
  change behavior for operators who set nothing beyond today's config.

### Key Entities

- **`## CI` item** — per-PR monitoring entry in `missions.md` with `attempt` / `max_attempts`
  (total drain budget). Unchanged shape; `max_attempts` semantics clarified as the interleaved-
  retry budget, not the per-mission internal loop count.
- **CI-fix mission** — an injected `/ci_check <url>` Pending mission; now non-urgent and
  single-attempt.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A single unfixable PR can no longer occupy the main queue for more than roughly one
  bounded fix step at a time; other Pending missions run between CI-fix attempts (no `⏳ == ▶`
  queue-jump for injected `/ci_check`).
- **SC-002**: The number of consecutive Claude fix steps attributable to one PR before other work
  runs drops from up to `ci_fix_max_attempts` (5) per mission to **1**.
- **SC-003**: No CI-fix step can hold the queue longer than `ci_check.timeout` (default 3600s)
  plus the idle guard, versus the prior 7200s ceiling.
- **SC-004**: The async monitor cadence, the total-attempt budget, and the give-up notification are
  unchanged (verified by existing `test_ci_queue_runner` behavior plus new tests).

## Assumptions

- The single-slot serial mission runner is a fixed constraint; the fix is to change *what* the
  CI-fix mission does and *where* it sits in the queue, not to add real parallelism.
- Interleaving retries across missions (one attempt each) is acceptable latency for CI fixes, which
  are lower priority than the human/agent backlog by design.
- `ci_fix_max_attempts` (default 5) remains the drain-level give-up counter per PR; only the
  per-mission internal loop is capped at 1 by default. (That counter resets on a fix that lands a
  fresh *pending* CI run — pre-existing `add_ci_item` behavior — so it bounds repeated fast-failing
  attempts rather than being a hard lifetime cap; this diff does not change that reset.)
- Bounding the fix step to 3600s + idle guard does not truncate legitimate fixes (observed real
  steps were ≤ ~1575s; the idle guard only kills genuinely silent steps).
