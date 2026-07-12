# Implementation Plan: `/ci_check` async speedup — stop starving the mission queue

**Branch**: `koan.atoomic/ci-check-speedup` | **Date**: 2026-07-10 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/005-ci-check-async-speedup/spec.md`

## Summary

`/ci_check` is meant to be a quick async check that only adjusts a PR on failure and never blocks
the queue. In production it does the opposite: the auto-injected fix mission is inserted **urgent**
(queue-jumping a single-slot runner), runs an up-to-5-attempt Claude fix loop per mission, and
compounds via drain re-injection — one unfixable PR froze the main queue for ~2h13m. Each fix step
is also capped at the 2-hour `skill_timeout`.

Technical approach — three surgical, low-risk changes that preserve the existing design (async
monitor + `## CI` budget + give-up notification) while removing the blocking:

1. **Non-urgent injection** — inject the CI-fix mission FIFO, not at the queue front.
2. **Per-mission single attempt** — cap the internal `run_ci_fix_loop` at 1 attempt (configurable),
   decoupled from the `ci_fix_max_attempts` total budget that `drain_one` continues to enforce by
   interleaved re-injection.
3. **Bounded fix step** — run the CI-fix Claude step under a dedicated `ci_check.timeout`
   (default 3600s) plus an idle guard, instead of the 7200s `skill_timeout`.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: stdlib; internal `app.*` modules (`ci_queue_runner`, `claude_step`,
`config`, `config_validator`, `utils.insert_pending_mission`)

**Storage**: `instance/missions.md` (`## CI` section + Pending), `instance/config.yaml`

**Testing**: pytest (`koan/tests/test_ci_queue_runner.py`, plus a new focused test module);
`KOAN_ROOT` must be set. Mock at `run_gh`/`check_ci_status`/`run_ci_fix_loop` level, never below
`retry_with_backoff`.

**Target Platform**: Linux/macOS daemon (agent loop)

**Project Type**: Single Python package (`koan/`)

**Performance Goals**: One unfixable PR must not block the queue for more than ~1 bounded fix step
at a time; other Pending missions interleave. No single fix step > `ci_check.timeout` + idle guard.

**Constraints**: Single-slot serial mission runner (fixed). No new parallelism. Defaults must not
change behavior for operators who only set today's keys.

**Scale/Scope**: ~4 source files touched + tests + spec/docs. No schema/migration.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Human Authority** — ✅ No change to branch/merge/PR discipline; CI fixes still land on
  `koan/*` branches via the existing push path. This change only reduces how much the agent
  monopolizes its own queue.
- **II. Specs Are the Source of Truth** — ✅ `specs/skills/ci_check.md` is updated in this branch
  (Known debt / behavior section) as part of implementation; this plan + spec are the design record.
- **III. Local Files by Default** — ✅ `## CI` state stays in `missions.md`; all writes go through
  `modify_missions_file` / `insert_pending_mission` (atomic). No new database state. (The
  MissionStore amendment (004) is not yet realized; `## CI` remains a `missions.md` section — this
  change does not add or move any authority.)
- **IV. Provider Isolation** — ✅ No provider branching; the fix step still goes through
  `run_claude_step` / the provider abstraction. Only timeouts change.
- **V. Untrusted Inputs, Audited Outputs** — ✅ No change to input trust or outbox scanning.
- **VI. Single Writer, Single Read Path** — ✅ New config concerns get exactly one accessor each in
  `config.py` and are registered in `config_validator.py`. `## CI` mutations still funnel through
  the existing helpers.

No violations → Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/005-ci-check-async-speedup/
├── spec.md      # feature spec (done)
├── plan.md      # this file
└── tasks.md     # task breakdown (/speckit-tasks output)
```

Durable artifacts updated on ship: `specs/skills/ci_check.md`, `docs/users/skills.md`,
`docs/users/user-manual.md`, `docs/architecture/daemon.md` (CI-check flow).

### Source Code (repository root)

```text
koan/app/
├── ci_queue_runner.py     # inject non-urgent; per-mission attempt cap; bounded step_runner
├── claude_step.py         # (only if a shared bounded step_runner helper is added)
├── config.py              # get_ci_check_step_timeout(), get_ci_check_max_fix_attempts()
└── config_validator.py    # register new ci_check.* keys

koan/tests/
├── test_ci_queue_runner.py         # extend: non-urgent injection, per-mission cap
└── test_ci_check_speedup.py        # new: config accessors + bounded step wiring

specs/skills/ci_check.md            # update contract/invariants + known debt
docs/users/skills.md                # user-facing note
docs/users/user-manual.md           # user-facing note
docs/architecture/daemon.md         # CI-check flow description
```

**Structure Decision**: Single Python package. The change is concentrated in `ci_queue_runner.py`
(injection + per-mission attempt cap + bounded step runner), backed by two `config.py` accessors
and validator registration. `claude_step.run_ci_fix_loop` already accepts a pluggable `step_runner`
and `max_attempts`, so no change to its loop is required — we pass a bounded step runner and
`max_attempts=1` from the caller.

## Phase 0 — Research / decisions

- **Injection urgency.** `insert_pending_mission(..., urgent=True)` places the mission at the front.
  Decision: use `urgent=False`. Rationale: CI fixes are lower priority than the human/agent
  backlog; the goal explicitly requires the queue to progress. Dedup (`_inject_ci_fix_mission`
  returns False when a `/ci_check` for the PR is already pending/in-progress) prevents pile-up.
- **Per-mission attempts vs. total budget.** The `## CI` item's `max_attempts` (`ci_fix_max_attempts`,
  default 5) is currently used *both* as the drain total budget *and* passed into the per-mission
  internal loop — causing attempts×attempts. Decision: split them. `drain_one` keeps using the
  `## CI` budget (total interleaved retries); `run_ci_check_and_fix` passes
  `get_ci_check_max_fix_attempts()` (default 1) to `run_ci_fix_loop`. Fresh CI logs are re-fetched
  each mission, so evidence is preserved (in fact more accurate) across interleaved attempts.
- **Bounded step.** `_default_ci_fix_step_runner` uses `get_skill_timeout()` (7200s) with no idle
  guard. Decision: pass a ci_check-specific `step_runner` (from `_attempt_ci_fixes`) that calls
  `run_claude_step` with `timeout=get_ci_check_step_timeout()` (default 3600s) and
  `idle_timeout=get_first_output_timeout()` (600s). `run_claude_step` already enforces
  `first_output_timeout` internally; adding the idle guard + shorter overall cap closes the
  "waiting forever" hole.
- **Manual path.** Human `/ci_check` (no `## CI` entry) now also does a single bounded attempt; if
  the fix is pushed and CI goes pending it is re-enqueued for monitoring (same interleave). Fast-
  failing manual fix with no `## CI` entry reports "still failing" (one-shot) — documented.

## Phase 1 — Design

- `config.py`:
  - `get_ci_check_step_timeout() -> int` — `ci_check.timeout`, default 3600, `_safe_int`.
  - `get_ci_check_max_fix_attempts() -> int` — `ci_check.max_fix_attempts_per_mission`, default 1,
    `_safe_int`, floor at 1.
  - Both tolerate `ci_check` being a bool/dict (mirror `is_ci_check_enabled` defensive parsing).
- `config_validator.py`: register `ci_check.timeout: int`, `ci_check.max_fix_attempts_per_mission: int`.
- `ci_queue_runner.py`:
  - `_inject_ci_fix_mission`: `urgent=True` → `urgent=False` (+ comment).
  - `run_ci_check_and_fix`: replace `max_fix_attempts` (from `## CI` item) with
    `get_ci_check_max_fix_attempts()` for the per-mission internal loop; keep the `## CI` budget
    untouched.
  - `_attempt_ci_fixes`: build and pass a bounded `step_runner` to `run_ci_fix_loop`.
- No change to `run_ci_fix_loop`'s control flow (it already honors `max_attempts` and `step_runner`).

## Phase 2 — Tasks

See `tasks.md`. One commit per task, per project convention.

## Complexity Tracking

> No Constitution Check violations — section intentionally empty.
