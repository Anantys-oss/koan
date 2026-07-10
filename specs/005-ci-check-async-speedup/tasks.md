---
description: "Task list for 005-ci-check-async-speedup"
---

# Tasks: `/ci_check` async speedup — stop starving the mission queue

**Input**: Design documents from `specs/005-ci-check-async-speedup/`

**Prerequisites**: plan.md, spec.md

**Organization**: Tasks grouped by user story. One commit per task (project convention); skip
empty commits for no-op tasks.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 (non-urgent injection), US2 (per-mission single attempt), US3 (bounded step)

## Path Conventions

Single Python package: `koan/app/`, `koan/tests/`.

---

## Phase 1: Foundational config (shared by US2 + US3)

**Purpose**: Config accessors + validator registration that the behavior changes read from.

- [ ] T001 Add `get_ci_check_step_timeout()` (config `ci_check.timeout`, default 3600) and
  `get_ci_check_max_fix_attempts()` (config `ci_check.max_fix_attempts_per_mission`, default 1,
  floor 1) to `koan/app/config.py`, mirroring `is_ci_check_enabled()`'s defensive `ci_check`
  bool/dict parsing.
- [ ] T002 [P] Register `ci_check.timeout` and `ci_check.max_fix_attempts_per_mission` (both
  `"int"`) in `koan/app/config_validator.py`.
- [ ] T003 [P] Add a `test_ci_check_speedup.py` unit test asserting the two new accessors return
  documented defaults and honor overrides (dict form) and tolerate `ci_check: false` / missing.

---

## Phase 2: US1 — Non-urgent injection (P1)

**Goal**: CI-fix missions no longer queue-jump; genuine backlog runs.

- [ ] T004 [US1] In `koan/app/ci_queue_runner.py::_inject_ci_fix_mission`, change
  `insert_pending_mission(..., urgent=True)` → `urgent=False`, with a comment explaining that CI
  fixes must not starve the serial queue (ref spec FR-001).
- [ ] T005 [US1] Add/extend a test in `koan/tests/test_ci_queue_runner.py` proving the injected
  `/ci_check` mission lands **after** pre-existing Pending missions (non-urgent), and that dedup
  still prevents duplicate injection.

---

## Phase 3: US2 — Per-mission single attempt (P1)

**Goal**: Each fix mission = one Claude attempt, then yield; `## CI` budget governs interleaved
retries.

- [ ] T006 [US2] In `koan/app/ci_queue_runner.py::run_ci_check_and_fix`, use
  `get_ci_check_max_fix_attempts()` for the per-mission internal loop instead of the `## CI` item's
  `max_attempts`. Leave the `## CI` `max_attempts` budget (used by `drain_one`) untouched. Update
  the docstring to describe the decoupling.
- [ ] T007 [US2] Add a test asserting `run_ci_check_and_fix` invokes `run_ci_fix_loop` with
  `max_attempts == get_ci_check_max_fix_attempts()` (default 1) even when the `## CI` budget is 5,
  and that `drain_one` still enforces the total budget of 5 (existing behavior preserved).

---

## Phase 4: US3 — Bounded fix step (P2)

**Goal**: No single fix step can hold the queue for 2 hours.

- [ ] T008 [US3] In `koan/app/ci_queue_runner.py::_attempt_ci_fixes`, pass a bounded `step_runner`
  to `run_ci_fix_loop` that calls `run_claude_step` with `timeout=get_ci_check_step_timeout()` and
  `idle_timeout=get_first_output_timeout()` (instead of the default 7200s `skill_timeout` runner).
- [ ] T009 [US3] Add a test asserting the CI-fix step runs under the bounded timeout + idle guard
  (mock `run_claude_step`, assert the `timeout`/`idle_timeout` kwargs), not `get_skill_timeout()`.

---

## Phase 5: Specs, docs, and verification (cross-cutting)

- [ ] T010 Update `specs/skills/ci_check.md`: document non-urgent injection, per-mission single
  attempt vs. `## CI` total budget, and the bounded step timeout in Outputs/Invariants/Known debt.
- [ ] T011 [P] Update `docs/users/skills.md` and `docs/users/user-manual.md` `/ci_check` entries to
  note it is a non-blocking, bounded, interleaved check-and-fix; and `docs/architecture/daemon.md`
  CI-check flow description.
- [ ] T012 Run `make lint` and the CI-check test subset
  (`KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_ci_queue_runner.py
  koan/tests/test_ci_check_speedup.py -q`); fix failures.

---

## Dependencies

- T001 blocks T006 (max attempts), T008 (timeout), T003.
- T002, T003 parallel to each other after/with T001.
- US1 (T004–T005) is independent of US2/US3 and can ship alone as the MVP.
- T010–T012 after code tasks.
