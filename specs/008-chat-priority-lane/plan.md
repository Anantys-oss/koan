# Implementation Plan: Chat Priority Lane

**Branch**: `koan.atoomic/chat-lane-1084` | **Date**: 2026-07-17 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/008-chat-priority-lane/spec.md`

## Summary

Keep Telegram chat responsive during missions by (1) making the bridge chat handler
**retry on empty AI responses** (the exact "I didn't get a response" symptom), not just on
timeouts, and (2) **skipping AI outbox formatting while a mission is actively executing**
so chat isn't starved of account headroom. Both changes stay inside the existing bridge
process — **no third OS process** (the approach PR #1088 took and this plan deliberately
rejects). A single authoritative `is_mission_active()` helper, backed by the existing
`.koan-active` provider-liveness signal, is the shared input.

## Context (from docs/ and specs/ — Documentation-first)

Consulted via `/brain ask` (index-first) before planning:

- **`docs/architecture/daemon.md` (Bridge Loop → Worker lanes)** — states the chat-vs-
  background split is *deliberately* realized with **threads inside the existing bridge
  process**: *"No extra OS process is forked."* This is the architectural principle PR
  #1088 violated and this plan honors.
- **`specs/components/bridge.md`** — the durable contract for the bridge. It currently
  asserts `notify.py::format_and_send` is the Claude subprocess call in the bridge path
  and lists the two-process isolation invariant. Adding mission-aware outbox formatting
  changes bridge behavior, so this contract must be updated **contract-first**
  (architectural change, declared in the PR).
- **`koan/app/active_mission.py` (issue #2086)** — `get_execution_state()` already
  classifies real provider execution (`idle`/`working`/`stalled`/`zombie`) from the
  `.koan-active` signal, covering parallel sessions and zombie PIDs. This is the correct
  source of truth for "a mission is executing" — far cleaner than parsing the free-form
  `.koan-status` string (what PR #1088 did).
- **`koan/app/cli_exec.py`** — already exposes `CLI_RETRY_BACKOFF = (2, 5, 10)` and
  `CLI_RETRY_MAX_ATTEMPTS = 3`; the chat retry reuses these rather than inventing new
  constants.

Nothing in docs/specs proposes a dedicated chat process as the sanctioned design; the
sanctioned design is the thread lanes already in place.

## Technical Context

**Language/Version**: Python 3.11+ (project-wide constraint).

**Primary Dependencies**: none new. Reuses `app.active_mission`, `app.format_outbox`
(`fallback_format`), `app.cli_exec` (`run_cli`, `CLI_RETRY_BACKOFF`), `app.bridge_state`.

**Storage**: existing signal files under `instance/` (`.koan-active`); no new files.

**Testing**: pytest (`KOAN_ROOT=/tmp/test-koan .venv/bin/pytest`), `make lint` (ruff PERF).

**Target Platform**: Linux/macOS daemon (bridge + agent-loop processes).

**Project Type**: Single Python project (`koan/`).

**Performance Goals**: chat retry adds at most `sum(backoff[:n])` seconds only on the
failure path; the happy path is unchanged. Outbox fallback formatting is instant (no CLI
call) during missions.

**Constraints**: No new OS process / PID file / IPC protocol / Makefile target
(FR-011). Behavior of delivery, history persistence, prompt-guard, and outbox
crash-safety unchanged (FR-012).

**Scale/Scope**: ~3 source files touched + 1 durable spec + 3 test files. Small, focused.

## Constitution Check

*GATE: Must pass before implementation.*

- **I. Human Authority** — PASS. Work on `koan.atoomic/*`, draft PR only, no merge to
  main. No auto-execution changes.
- **II. Specs Are the Source of Truth** — **ARCHITECTURAL CHANGE, DECLARED.** This touches
  the durable `specs/components/bridge.md` contract (outbox now conditionally skips AI
  formatting; a new bridge invariant "chat is resilient to empty responses"). Per
  Principle II this is contract-first: the bridge spec is updated to express the intended
  design as the **first** implementation task, before the code. The PR will check the
  "Architectural change" box. `scripts/spec_change_guard.py` is satisfied by the
  declaration.
- **III. Local Files by Default; Mission State in the Store** — PASS. Reads the existing
  `.koan-active` file via the existing `active_mission` reader (no raw read-modify-write,
  no new authority). No mission-state mutation.
- **IV / V / VI** — PASS. No provider-contract change, no new un-gated control, no mission
  mutation outside the store port.

No violations requiring Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/008-chat-priority-lane/
├── spec.md              # /speckit-specify output
├── plan.md              # this file
├── tasks.md             # /speckit-tasks output
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
koan/app/
├── active_mission.py     # + is_mission_active(koan_root) thin helper over get_execution_state()
├── outbox_manager.py     # _format_message(): skip AI format → fallback_format() when mission active
└── awake.py              # handle_chat(): empty response is retryable; unified bounded retry-with-backoff

koan/tests/
├── test_active_mission.py   # + is_mission_active: working/idle/zombie/corrupt
├── test_outbox_manager.py   # + mission-active → fallback_format; no-mission → AI format
└── test_awake.py            # + empty-response retry succeeds; all-empty → single degraded message

specs/components/bridge.md   # durable contract update (architectural, contract-first)
docs/architecture/daemon.md  # capture: chat resilience + mission-aware outbox note
```

**Structure Decision**: Single-project layout; changes are localized to three bridge/
agent-shared modules plus their unit tests, with the durable bridge spec updated first.

## Design detail

### 1. `is_mission_active(koan_root) -> bool` (in `active_mission.py`)

Thin wrapper: `get_execution_state(koan_root)["state"] in {"working", "stalled"}`.
`idle`/`zombie` → False (a dead PID must not degrade outbox forever). Never raises — it
delegates to `get_execution_state`, which already degrades corrupt/absent signals to
`idle`. Placed in `active_mission.py` because that module already owns the active-provider
concept — the natural DRY home (FR-009, FR-010).

### 2. Mission-aware outbox formatting (`outbox_manager.OutboxManager._format_message`)

At the top of `_format_message`, if `is_mission_active(self._instance_dir.parent)` returns
True, return `fallback_format(raw_content)` immediately — no `format_message()` AI call
(FR-006). Otherwise the existing AI path runs (FR-007). Each flush re-evaluates, so it is
not sticky (FR-008). `koan_root` is derived as `self._instance_dir.parent` (the existing
convention — `instance/` lives at `KOAN_ROOT/instance/`); a short comment documents the
coupling.

### 3. Chat resilience (`awake.handle_chat`)

Refactor the current one-shot + timeout-only-retry into a single bounded loop:

- Attempt the AI call. **Empty stdout with returncode 0** is now classified the same as a
  timeout — a retryable contention outcome (FR-001).
- On a retryable outcome, sleep `CLI_RETRY_BACKOFF[attempt]` and retry with the **lite**
  prompt and a shorter timeout (FR-002), up to `CLI_RETRY_MAX_ATTEMPTS` total (FR-003).
- First non-empty response → deliver + save history, return (FR-005).
- Exhausted → exactly one degraded message + one history entry (FR-004).

Preserves `_CHAT_LOCK`, `TypingIndicator`, prompt-guard scan, `project_context=False`, and
`max_turns=5` (do not silently change mission semantics — a review finding against #1088
was exactly such an accidental `max_turns`/`cwd` drift). No change to lane concurrency.

## Phases (implementation order)

1. **Contract-first**: update `specs/components/bridge.md` (declare the new outbox-format
   behavior + chat-resilience invariant).
2. `is_mission_active()` helper + tests.
3. Mission-aware outbox formatting + tests.
4. Chat empty-response retry + tests.
5. Docs capture (`daemon.md`) + `/brain sync`.
6. Lint + full test suite.

## Risks & mitigations

- **Fallback formatting is less polished during missions** — accepted; mission-completion
  messages (the important ones) arrive after the mission when AI formatting resumes.
- **Retry adds latency on the failure path** — bounded by `CLI_RETRY_BACKOFF`; happy path
  unchanged; lite context reduces repeat-empty likelihood.
- **`instance_dir.parent` coupling** — documented; mirrors existing conventions and the
  helper degrades safely if the path is wrong (reads absent signal → not active).
```
