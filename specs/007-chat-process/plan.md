# Implementation Plan: Dedicated Chat Process

**Branch**: `koan.atoomic/chat-process-1084` | **Date**: 2026-07-17 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/007-chat-process/spec.md`

## Summary

Give Telegram chat its own execution path so a running mission can no longer starve it
of a Claude reply, and remove the lowest-value competing caller (outbox formatting) while
a mission runs. The realtime chat cycle is refactored into **one** shared unit of work
(`chat_engine.respond`) called identically by the new dedicated **chat process** and by
the bridge's inline **fallback** — eliminating, by construction, the behavior-divergence,
history-bypass, and prompt-guard-bypass defects found in the earlier attempt (PR #1088).
Personality context is read through the bridge's existing mtime-cached getters, so edits
land on the next reply with no restart. The "is a mission active" question is answered by
the existing authoritative `.koan-active` liveness signal (`active_mission.py`), not a new
duplicated status-string parse.

## Context: what docs/ and specs/ say

Consulted via `/brain ask` (index-first) plus direct source reading:

- **`specs/components/bridge.md`** — the durable contract for the bridge. States the
  two-process isolation invariant, "`notify.format_and_send` is the only Claude
  subprocess call in the bridge path" (tests must mock it), the bounded-memory
  invariant, and the crash-safe outbox flush. Adding a third long-lived process that
  invokes Claude and changing chat routing is a **durable-contract change** → this plan
  updates `bridge.md` contract-first and the PR **declares** the architectural change
  (constitution Principle II, `scripts/spec_change_guard.py`).
- **`docs/architecture/daemon.md`** — operational rundown of the bridge's chat/bg worker
  lanes and outbox draining; must gain the chat-process path.
- **`koan/app/active_mission.py`** — `.koan-active` is the authoritative provider-liveness
  signal (issue #2086): `get_execution_state()` returns `working`/`stalled`/`idle`/`zombie`.
  This is the correct, already-single-sourced answer to "is a mission actively burning the
  quota right now," superior to PR #1088's `.koan-status` text parse.
- **`koan/app/bridge_state.py`** — `get_soul()`/`get_summary()` are **mtime-cached fresh
  reads**; building chat context through them fixes PR #1088's stale-personality blocker
  for free.

## Technical Context

**Language/Version**: Python 3.11+ (no 3.12+ syntax).

**Primary Dependencies**: stdlib only for the new code (`json`, `fcntl`, `signal`,
`threading`, `time`, `pathlib`). Reuses existing `cli_exec.run_cli`, `cli_provider`,
`notify`, `conversation_history`, `prompt_guard`, `pid_manager`, `active_mission`.

**Storage**: file-based IPC in `instance/` (append-only JSONL inbox), consistent with
`outbox.md`. `fcntl.flock` + read-then-truncate, matching `OutboxManager.flush`.

**Testing**: pytest, `KOAN_ROOT` set. Never call Claude — mock `run_cli` /
`format_and_send`. New: `test_chat_context.py`, `test_chat_engine.py`,
`test_chat_process.py`; extend `test_outbox_manager.py`, `test_awake.py`,
`test_active_mission.py`, `test_pid_manager*`.

**Target Platform**: Linux/macOS daemon.

**Project Type**: Single Python package (`koan/app/`) + Makefile + docs/specs.

**Performance Goals**: added chat latency from file handoff < ~1s (poll interval),
negligible vs the multi-second model call. Zero added load while idle.

**Constraints**: no import cycles; `chat_engine`/`chat_context` must not import `awake`
(so the dedicated process imports neither the bridge loop nor its heavy deps).

**Scale/Scope**: single operator, one chat channel; FIFO queue of pending chat messages
(bursts of a handful).

## Constitution Check

*GATE: must pass before Phase 0 and re-checked after design.*

- **I. Human Authority** — no auto-merge/main writes; work on `koan.atoomic/*`; draft PR
  only. ✅
- **II. Specs Are the Source of Truth** — durable-contract change to `bridge.md` done
  **contract-first** and **declared** in the PR (architectural box). ✅ (declared)
- **III. Local Files by Default; Mission State in the Store** — chat inbox is transient
  runtime state → a local `instance/` file, not the mission store. Reads mission liveness
  through the existing `.koan-active` signal path; never mutates mission state. ✅
- **IV. Provider Isolation** — chat invokes Claude only through `cli_provider` /
  `cli_exec`; no provider branching added. ✅
- **V. Untrusted Inputs, Audited Outputs** — inbound chat still runs `prompt_guard`
  (parity requirement FR-003); the dedicated path must NOT bypass it. Outbox scanning is
  unchanged. ✅
- **VI. Single Writer, Single Read Path** — `is_mission_active()` lives in exactly one
  module (`active_mission.py`); the inbox filename is one constant (`signals.py`); the
  chat cycle has one implementation (`chat_engine.respond`). ✅ (this is the core of the
  "cleaner than #1088" mandate)
- **VII. Simplicity and Honest Reporting** — prefer extending existing mechanisms
  (PID manager, flock IPC, mtime getters, `.koan-active`) over inventing new ones; the
  process is optional with graceful fallback. ✅

No violations → Complexity Tracking omitted.

## Project Structure

### Documentation (this feature)

```text
specs/007-chat-process/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions & rejected alternatives
├── data-model.md        # Phase 1 — inbox record, engine result, liveness states
├── quickstart.md        # Phase 1 — how to validate end-to-end
├── contracts/
│   └── chat-process.md  # Module/function contracts (internal interfaces)
└── checklists/requirements.md
```

### Source Code (repository root)

```text
koan/app/
├── chat_context.py      # NEW — pure build_chat_prompt(text, *, lite); fresh soul/summary
├── chat_engine.py       # NEW — respond(text): the single shared chat reply cycle
├── chat_process.py      # NEW — dedicated process: inbox poll/drain + lifecycle
├── active_mission.py    # EDIT — add is_mission_active(koan_root) (single source)
├── signals.py           # EDIT — add CHAT_INBOX_FILE constant (single source)
├── awake.py             # EDIT — handle_chat delegates to chat_engine; route to process w/ fallback
├── outbox_manager.py    # EDIT — _format_message skips Claude when mission active (Phase 1)
└── pid_manager.py       # EDIT — register "chat"; start_chat(); start_all/stop/status/logs

Makefile                 # EDIT — make chat; start/logs include chat
koan/tests/
├── test_chat_context.py # NEW
├── test_chat_engine.py  # NEW
├── test_chat_process.py # NEW
├── test_outbox_manager.py  # EDIT — mission-active skip
├── test_awake.py        # EDIT — routing + fallback
├── test_active_mission.py  # EDIT — is_mission_active
└── test_pid_manager*.py # EDIT — chat process registration

specs/components/bridge.md          # EDIT (contract-first, DECLARED architectural)
docs/architecture/daemon.md         # EDIT — three-process model, chat lane
docs/architecture/chat-process.md   # NEW — the dedicated chat path
CLAUDE.md / koan/app/CLAUDE.md      # EDIT — architecture prose (three processes)
```

**Structure Decision**: extend the existing single-package layout. Two new pure/logic
modules (`chat_context`, `chat_engine`) are shared by the process and the fallback; one
new runnable module (`chat_process`). All IPC/lifecycle reuses existing primitives.

## Design (the "cleaner than #1088" decisions)

1. **One chat cycle, not two.** PR #1088 re-extracted a *separate* `_retry_chat_lite`
   inside `chat_process.py`, which drifted (`max_turns` 5→1, wrong `cwd`) and skipped
   history/guard. Here the entire cycle moves to `chat_engine.respond(text)`; both
   `awake.handle_chat` (fallback) and `chat_process` call it. Divergence is impossible.

2. **Fresh personality by construction.** `chat_context.build_chat_prompt` reads soul and
   summary via `bridge_state.get_soul()/get_summary()` (mtime-cached), so no startup
   snapshot to go stale.

3. **Single mission-active authority.** `active_mission.is_mission_active(koan_root)`
   wraps `get_execution_state(...) in {working, stalled}`. Used by `outbox_manager` (and
   available to chat context). No duplicated `.koan-status` parse, no fragile
   `instance_dir.parent` derivation (the function takes `koan_root` explicitly).

4. **FIFO queue, no depth-1 bounce.** `handle_message` always appends to the inbox when
   the process is up; the process drains in arrival order. No "Busy" rejection when a
   prior message is in flight (fixes the #1088 regression).

5. **Robust inbox.** `read_and_clear_inbox()` truncates **unconditionally** under flock
   after reading, so malformed lines can't accumulate or replay (fixes #1088 finding).

6. **TOCTOU-aware routing.** After writing to the inbox, re-check the chat PID; if the
   process died in the window, fall back to the inline worker thread so no message is
   orphaned.

7. **Graceful shutdown.** `chat_process` installs a SIGTERM handler that sets a stop flag;
   the poll loop finishes the in-flight reply, then releases the PID file and exits.

8. **Optional & fail-safe.** If the process isn't running (dev, disabled, crashed), chat
   is handled inline exactly as today (`_run_in_worker`). `make start` launches it
   alongside awake/run; it is not mandatory for correctness.

## Complexity Tracking

Not applicable — no constitution violations.
