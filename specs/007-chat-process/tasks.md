# Tasks: Dedicated Chat Process

**Feature**: `specs/007-chat-process/` | **Branch**: `koan.atoomic/chat-process-1084`
**Input**: [spec.md](./spec.md), [plan.md](./plan.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/chat-process.md](./contracts/chat-process.md)

Tests are included: this is a fresh reimplementation whose entire value is *behavioral
parity + no divergence*, so regression-lock tests are first-class deliverables. Commit
after every task (one commit per task).

Conventions: `KOAN_ROOT=/tmp/test-koan .venv/bin/pytest ...`; never call Claude (mock
`run_cli` / `format_and_send`); `make lint` must pass.

---

## Phase 1: Setup

- [ ] T001 Add `CHAT_INBOX_FILE = "chat-inbox.jsonl"` constant (single source, FR-007) to `koan/app/signals.py`, in a new "Inter-process queues" section with a docstring note that it lives under `instance/` (not a `.koan-` signal).

---

## Phase 2: Foundational (blocking prerequisites for all stories)

- [ ] T002 [P] Add `is_mission_active(koan_root) -> bool` to `koan/app/active_mission.py` (returns `get_execution_state(koan_root)["state"] in {"working", "stalled"}`; single source of truth, FR-007) and cover it in `koan/tests/test_active_mission.py` (true for working/stalled, false for idle/zombie/absent).
- [ ] T003 Create `koan/app/chat_context.py` with `build_chat_prompt(text, *, lite=False)` by moving `awake._build_chat_prompt` verbatim (reading soul/summary via `bridge_state.get_soul()/get_summary()` so they stay fresh, FR-004); make `awake._build_chat_prompt` a thin delegate; add `koan/tests/test_chat_context.py` asserting prompt contents + the 12k-char → lite recursion + fresh soul/summary via mtime change. Keep all existing `test_awake` chat-prompt expectations green.
- [ ] T004 Create `koan/app/chat_engine.py` with `respond(text)` — the full chat reply cycle (guard scan → save user → `build_chat_prompt` → CLI invoke with `max_turns=5`, chat tools, `cwd=KOAN_ROOT`, `project_context=False` → lite retry with identical semantics → clean → send → save assistant), moved out of `awake.handle_chat`; make `awake.handle_chat` delegate to `chat_engine.respond`. Own `_CHAT_LOCK`, `_get_last_message_id` (use `except Exception`, not `SystemExit`), `_clean_chat_response` here. Add `koan/tests/test_chat_engine.py` locking the parity invariants (guard runs, BOTH history writes happen, retry keeps `max_turns=5`/`project_context=False`/`cwd=KOAN_ROOT`).

**Checkpoint**: shared engine + fresh-context + liveness helper exist and are tested; the
inline chat path already routes through `chat_engine.respond` with unchanged behavior.

---

## Phase 3: User Story 1 — Chat stays responsive while a mission runs (P1) 🎯 MVP

**Goal**: A running mission can no longer starve chat of a reply; chat has its own
process and one competing caller (outbox formatting) is removed during missions.

**Independent test**: start a mission, send several chats → all answered in order, no
"I didn't get a response," mission unaffected.

- [ ] T005 [US1] In `koan/app/outbox_manager.py`, add an explicit `koan_root` to `OutboxManager.__init__` (default `instance_dir.parent`) and make `_format_message` return `fallback_format(raw_content)` immediately when `is_mission_active(self._koan_root)` (FR-006); update `awake._make_outbox_mgr` to pass `KOAN_ROOT`; extend `koan/tests/test_outbox_manager.py` to assert Claude formatting is skipped when a mission is active and used when idle.
- [ ] T006 [US1] Create `koan/app/chat_process.py` inbox helpers: `write_to_inbox(text)` (append one JSONL record `{"text","ts"}` under `fcntl.flock`, returns bool), `read_and_clear_inbox()` (flock, read all lines, **unconditionally truncate**, return parsed records skipping malformed — FR-009), `has_pending_requests()`. Add `koan/tests/test_chat_process.py` covering round-trip, FIFO order, and unconditional truncation on malformed-only input.
- [ ] T007 [US1] Add the `chat_process.main()` loop to `koan/app/chat_process.py`: acquire the `"chat"` PID file, install a SIGTERM handler that finishes the in-flight reply then exits (FR-011), poll the inbox every interval, drain FIFO calling `chat_engine.respond(entry["text"])`; guard the module with `if __name__ == "__main__": main()`. Extend `test_chat_process.py` to assert drain order and clean SIGTERM shutdown (in-flight reply completes before exit).
- [ ] T008 [US1] Register the chat process in `koan/app/pid_manager.py`: add `"chat"` to `PROCESS_NAMES`, add `start_chat(koan_root, ...)` mirroring `start_awake`, and include it in `start_all()` / `stop_processes()` / status wiring. Extend the pid_manager tests to assert `start_chat` launches `app/chat_process.py` and `chat` is covered by start/stop/status.
- [ ] T009 [US1] Wire routing in `koan/app/awake.py`: add `_is_chat_process_running()` and `_route_to_chat_process(text)` (write to inbox, **re-check** liveness for TOCTOU, never reject as "busy"); in `handle_message` free-form branch, route to the process and fall back to `_run_in_worker(chat_engine.respond, text, lane="chat")` when it returns False. Extend `koan/tests/test_awake.py`: routes when up, falls back when down.
- [ ] T010 [US1] Update `Makefile`: add a `chat` target running `app/chat_process.py`; add `chat` to `.PHONY`; include `logs/chat.log` in `make logs`; launch chat in the `make start` path.

**Checkpoint**: US1 fully functional and independently testable — the MVP.

---

## Phase 4: User Story 2 — Personality edits without restart (P2)

**Goal**: soul/summary edits reflected on the next reply, no restart.

**Independent test**: chat, edit `soul.md`, chat again → second reply reflects the edit.

- [ ] T011 [P] [US2] Add a regression test in `koan/tests/test_chat_context.py` (or `test_chat_engine.py`) proving `build_chat_prompt` reflects a `soul.md`/`summary.md` change on the next call without any restart (mutate the file, bust the mtime cache, assert new content appears) — locks FR-004 against the PR #1088 stale-context defect.

**Checkpoint**: fresh-personality guarantee is proven by test (behavior delivered in T003).

---

## Phase 5: User Story 3 — Graceful degradation & operability (P3)

**Goal**: chat still works when the process is down; the process is visible to operator
tooling; no message is lost or answered twice.

**Independent test**: with the process stopped, chat is still answered; status/logs
include the chat process.

- [ ] T012 [P] [US3] Extend `koan/tests/test_awake.py`: (a) when `_route_to_chat_process` writes but the process then dies, `handle_message` falls back to the inline worker (no orphaned message); (b) a given message is answered by exactly one path (no double answer); (c) no "busy" rejection when a prior request is pending (FIFO, FR-005).
- [ ] T013 [P] [US3] Add/extend a pid_manager status test asserting the standard status output includes the `chat` process alongside `run`/`awake`, so operators see it (FR-008).

**Checkpoint**: resilience + operability proven.

---

## Phase 6: Polish & Cross-Cutting (docs, specs, final gates)

- [ ] T014 Update the durable contract `specs/components/bridge.md` **contract-first**: add the dedicated chat process to the architecture/invariants, note that outbox formatting skips Claude while a mission is active, and that chat routing prefers the process with inline fallback. (Declared as an architectural change in the PR.)
- [ ] T015 [P] Update `docs/architecture/daemon.md` (three-process model + chat lane) and add `docs/architecture/chat-process.md` (the dedicated chat path: inbox protocol, fallback, lifecycle, contention rationale).
- [ ] T016 [P] Update architecture prose in `CLAUDE.md` and `koan/app/CLAUDE.md` to describe three processes (run + awake + chat), keeping the change minimal and focused.
- [ ] T017 Run `/brain sync` (frontmatter/`updated:`/index refresh for the touched docs/specs pages), then `make lint` and the full `make test`; fix any failures.

---

## Dependencies & Execution Order

- **Setup (T001)** → **Foundational (T002–T004)** must complete before user-story phases.
- **US1 (T005–T010)** depends on Foundational; T006→T007 (helpers before loop),
  T007+T008 before T009 (routing needs the process + PID registration). T005 is
  independent within US1.
- **US2 (T011)** depends only on T003.
- **US3 (T012)** depends on T009; **T013** depends on T008.
- **Polish (T014–T017)** last; T017 is the final gate.

## Parallel Opportunities

- T002 is `[P]` (isolated module) alongside starting T003.
- T011, T012, T013 are `[P]` (independent test files/areas).
- T015, T016 are `[P]` (independent doc files).

## Implementation Strategy

**MVP = Phase 1 + Phase 2 + Phase 3 (US1)** — resolves issue #1084's reported symptom on
its own. US2 and US3 harden correctness/operability; Polish lands the declared durable
contract and docs.
