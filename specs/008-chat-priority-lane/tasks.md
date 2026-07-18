---
description: "Task list for Chat Priority Lane (#1084)"
---

# Tasks: Chat Priority Lane

**Input**: Design documents from `specs/008-chat-priority-lane/`

**Prerequisites**: plan.md, spec.md

**Tests**: Included — the spec's SC-005 explicitly requires new unit tests.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 (chat resilience), US2 (mission-aware outbox), US3 (mission-active helper)

## Path Conventions

Single Python project: source under `koan/app/`, tests under `koan/tests/`.

---

## Phase 1: Contract-first (Architectural — blocking)

**Purpose**: Per Constitution Principle II, update the durable bridge contract to express
the intended design BEFORE writing conforming code.

- [ ] T001 Update `specs/components/bridge.md`: add a **Chat resilience** invariant (empty
  AI responses are retryable, bounded retry-with-backoff before any degraded message) and
  a **Mission-aware outbox formatting** note to the `format_and_send`/outbox row and
  Invariants (outbox uses the local fallback formatter while a mission is actively
  executing, backed by the `.koan-active` signal via `active_mission`). Bump `updated:`.
  Commit: `docs(spec): declare chat-resilience + mission-aware outbox contract (#1084)`

---

## Phase 2: US3 — Authoritative mission-active signal (Priority P3, foundational)

**Purpose**: Shared input for US2 (and any future caller). Foundational because US2 depends
on it. Independently testable.

- [ ] T002 [US3] Add `is_mission_active(koan_root) -> bool` to `koan/app/active_mission.py`:
  returns True when `get_execution_state(koan_root)["state"]` is `"working"` or `"stalled"`;
  False for `idle`/`zombie`; never raises. Docstring notes it is the single source of truth
  for "a mission is executing". Commit: `feat(bridge): add is_mission_active signal helper (#1084)`
- [ ] T003 [US3] Add tests to `koan/tests/test_active_mission.py`: (a) live PID + recent
  output → True; (b) no signal / idle → False; (c) dead PID (zombie) → False; (d)
  corrupt/absent signal → False without raising. Commit: `test(bridge): cover is_mission_active (#1084)`

**Checkpoint**: helper correct in isolation.

---

## Phase 3: US2 — Mission-aware outbox formatting (Priority P2)

**Purpose**: Remove the lowest-value concurrent AI caller during missions.

- [ ] T004 [US2] In `koan/app/outbox_manager.py` `OutboxManager._format_message()`: at the
  top, if `is_mission_active(self._instance_dir.parent)` is True, return
  `fallback_format(raw_content)` immediately (no AI `format_message()` call). Otherwise run
  the existing AI path. Add a one-line comment documenting the `instance_dir.parent ==
  KOAN_ROOT` convention. Import `is_mission_active` from `app.active_mission`. Commit:
  `feat(bridge): skip AI outbox formatting during active missions (#1084)`
- [ ] T005 [US2] Add tests to `koan/tests/test_outbox_manager.py`: (a) mission active →
  `_format_message` returns fallback output and `format_message` (AI) is NOT called;
  (b) no mission → AI `format_message` IS called; (c) re-evaluated per call (not sticky).
  Mock `is_mission_active` and `format_message`. Commit: `test(bridge): cover mission-aware outbox formatting (#1084)`

**Checkpoint**: US2 independently verifiable; outbox still delivers during missions.

---

## Phase 4: US1 — Chat resilience to empty responses (Priority P1, the bug fix)

**Purpose**: The reported #1084 fix — chat retries instead of giving up on empty responses.

- [ ] T006 [US1] Refactor `koan/app/awake.py` `handle_chat()` into a single bounded
  retry loop: treat empty stdout (rc 0) the same as `TimeoutExpired` — a retryable
  outcome; on retry sleep `CLI_RETRY_BACKOFF[attempt]` and use the lite prompt + shorter
  timeout; up to `CLI_RETRY_MAX_ATTEMPTS` attempts; deliver first non-empty reply (save
  history); after exhaustion send exactly one degraded message + one history entry.
  Preserve `_CHAT_LOCK`, `TypingIndicator`, prompt-guard scan, `project_context=False`,
  `max_turns=5`, and `cwd=KOAN_ROOT` unchanged (no accidental semantic drift). Import
  `CLI_RETRY_BACKOFF`, `CLI_RETRY_MAX_ATTEMPTS` from `app.cli_exec`. Commit:
  `fix(bridge): retry chat on empty response, not just timeout (#1084)`
- [ ] T007 [US1] Add tests to `koan/tests/test_awake.py`: (a) first call empty, retry
  returns text → user gets the real reply, apology NOT sent; (b) first call times out,
  retry returns text → real reply; (c) all attempts empty/timeout → exactly one degraded
  message sent and exactly one assistant history entry saved. Mock `run_cli`,
  `send_telegram`, `save_conversation_message`, and `time.sleep`. Commit:
  `test(bridge): cover chat empty-response retry (#1084)`

**Checkpoint**: US1 — the reported bug — is fixed and covered.

---

## Phase 5: Documentation capture & polish

- [ ] T008 [P] Update `docs/architecture/daemon.md` Bridge Loop section: note chat
  resilience (bounded retry on empty/timeout) and mission-aware outbox formatting; reaffirm
  no third process. Run `/brain sync` (frontmatter/index). Commit: `docs: capture chat-priority-lane behavior (#1084)`
- [ ] T009 Run `make lint` and the full test suite (`KOAN_ROOT=/tmp/test-koan
  .venv/bin/pytest koan/tests/`); fix any failures. Commit fixes if needed.

---

## Dependencies

- T001 (contract) → precedes all code (Principle II).
- T002 (helper) → blocks T004 (outbox uses it).
- T004 → T005; T006 → T007.
- US1 (T006/T007) and US2 (T004/T005) are independent once T002 lands and can be built in
  either order; US1 is P1 so prioritized if time-boxed.
- T008/T009 last.

## Parallelizable

- After T002, the US1 (awake.py) and US2 (outbox_manager.py) tracks touch different files
  and could proceed in parallel.
- T003, T005, T007 each touch a distinct test file.
