# Research: Dedicated Chat Process

Phase 0 decisions. Each resolves a design question raised by the spec, grounded in the
current codebase and the review history of PR #1088.

## D1 — Separate OS process vs. in-process priority lane

**Decision**: A separate OS process (`chat_process.py`), matching the issue's explicit
ask ("the Telegram chat handler should live in its own process").

**Rationale**: The mission provider, chat, and outbox formatting compete for one Claude
quota. A separate process cleanly owns its own subprocess/session lifecycle and cannot be
blocked by the bridge's worker-thread back-pressure or the mission runner. It is also the
shape the maintainers already reviewed favorably in #1088 ("architecture is sound").

**Alternatives considered**:
- *In-thread priority lane in awake.py* — cheaper, but chat still shares the bridge
  process and its thread pool; doesn't satisfy the issue's "own process" requirement and
  keeps chat coupled to bridge memory pressure.
- *Ollama/local-model fallback for chat during missions* (#1088 Approach B) — masks the
  problem with lower-quality replies and adds an Ollama dependency.

## D2 — Where the shared chat logic lives

**Decision**: Two new modules — `chat_context.py` (pure prompt building) and
`chat_engine.py` (the full reply cycle: guard → save user → build → invoke+retry →
send → save assistant). `awake.handle_chat` becomes a thin delegate to
`chat_engine.respond`; `chat_process` calls the same `respond`.

**Rationale**: The single worst class of bug in #1088 came from re-implementing the
retry/invoke path separately in the chat process, where it drifted from the inline one
(`max_turns` 5→1, `cwd` change) and skipped `save_conversation_message` and the prompt
guard. Collapsing to one implementation makes those regressions structurally impossible
and is the concrete meaning of "cleaner reimplementation."

**Alternatives considered**:
- *Keep `handle_chat` in awake.py and import it from chat_process* — creates an import
  cycle risk and forces the lightweight process to import the entire bridge loop and its
  heavy dependencies. Rejected.

## D3 — "Is a mission active?" source of truth

**Decision**: `active_mission.is_mission_active(koan_root)` = `get_execution_state(...)
["state"] in {"working", "stalled"}`, reading the existing `.koan-active` signal.

**Rationale**: `.koan-active` is *already* the authoritative provider-liveness signal
(issue #2086) and is written/cleared around the exact subprocess that causes contention.
PR #1088 instead parsed `.koan-status` free-text ("In Progress"), which review flagged as
duplicated (copied into two modules) and fragile (string matching + `instance_dir.parent`
path derivation). One function in the module that already owns the signal removes both
problems (constitution VI).

**Alternatives considered**:
- *Parse `.koan-status` text* — fragile, and the status string is human-facing and
  changes. Rejected.
- *Read the mission store's In Progress section* — heavier (SQLite), and "declared In
  Progress" can diverge from "provider actually running" (the zombie case `.koan-active`
  exists to disambiguate). Rejected.

## D4 — Inbox transport & robustness

**Decision**: Append-only JSONL at `instance/<CHAT_INBOX_FILE>`; producer appends one JSON
object per line under `fcntl.flock`; consumer reads all lines under flock and
**unconditionally truncates**, then processes parsed entries FIFO, skipping unparseable
lines.

**Rationale**: Mirrors the established `outbox.md` flock + read-then-truncate pattern.
Unconditional truncation (even when zero entries parsed) prevents a malformed partial
write from being replayed every poll forever (#1088 finding). The filename is one constant
in `signals.py` (single source, FR-007).

**Alternatives considered**:
- *Named pipe / socket* — more moving parts, no crash-persistence, inconsistent with the
  rest of the daemon's file-IPC. Rejected.
- *Truncate only when entries parsed* (the #1088 bug) — leaks malformed lines. Rejected.

## D5 — Queue discipline (FIFO vs. reject-when-busy)

**Decision**: Always enqueue; the process answers in arrival order. No "Busy" rejection.

**Rationale**: #1088's `has_pending_requests()` gate gave the process an effective queue
depth of 1 — a regression from the worker-thread model that at least queued. FIFO is
simpler and matches user expectation (replies arrive in order).

## D6 — Routing & fallback (no double-answer, no lost message)

**Decision**: `handle_message` free-form path: if the chat PID is live, `write_to_inbox`,
then **re-check** the PID; on either "not running" or "died after write," fall back to
`_run_in_worker(chat_engine.respond, text, lane="chat")`. Exactly one path answers.

**Rationale**: The re-check closes the TOCTOU window (process dies between PID check and
write) so a message is never left unconsumed; because the fallback only fires when the
process is confirmed down, a message is never answered twice.

## D7 — Lifecycle & operability

**Decision**: Register `"chat"` in `pid_manager.PROCESS_NAMES`; add `start_chat()`; include
it in `start_all()`, `stop_processes()`, and status/log wiring; add `make chat` and include
`logs/chat.log` in `make logs`/`make start`. SIGTERM → finish in-flight → release PID →
exit.

**Rationale**: Consistency with `run`/`awake`/`ollama`/`dashboard`; operators manage it with
the same commands. Graceful SIGTERM reuses the daemon's shutdown conventions.

## D8 — Contention reduction at the source (Phase 1)

**Decision**: `OutboxManager._format_message` returns `fallback_format(raw_content)`
immediately when `is_mission_active(koan_root)` is true, skipping the Claude formatting
call; polished formatting resumes once idle.

**Rationale**: Outbox formatting is purely cosmetic and the lowest-priority Claude caller.
Removing it during missions cuts contention independently of the chat-process change, and
is valuable even on its own. `bridge.md`'s "only Claude call in the bridge path" note is
updated to reflect the mission-active skip.
