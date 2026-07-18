---
type: component-spec
title: "Component Spec — Telegram Bridge"
description: "Design contract for the Telegram bridge process that classifies human messages into chat vs. mission, dispatches commands/skills, flushes the agent's outbox crash-safely, and routes chat to a dedicated process to survive mission-time API contention."
tags: [bridge]
created: 2026-06-27
updated: 2026-07-17
---

# Component Spec — Telegram Bridge

**Modules:** `awake.py`, `command_handlers.py`, `bridge_state.py`, `bridge_log.py`,
`notify.py`, `chat_context.py`, `chat_process.py`

## Purpose

The human ↔ agent interface. A process independent of the agent loop that polls
Telegram, classifies each message as *chat* (answer now) or *mission* (queue it),
and flushes the agent's outbox back to Telegram. It is the realtime channel; the
agent loop is asynchronous and never talks to Telegram directly except via `outbox.md`.

See `docs/architecture/daemon.md`'s Bridge Loop section for the operational rundown
of polling, the chat/bg worker lanes, and outbox draining.

## Architecture

```
awake.py (loop, ~3s poll)
  ├─ classify message: chat → dedicated chat process (fallback: inline reply)
  │                    mission → queue to missions.md
  ├─ command_handlers.py: /help /stop /pause /resume /skill ... + skill dispatch
  ├─ flush outbox.md → Telegram (atomic staging; AI formatting skipped while a
  │                    mission is active — see invariant below)
  └─ bridge_state.py: shared config/paths/registries (avoids circular imports)

chat_process.py (dedicated chat process — issue #1084)
  ├─ drains instance/chat-inbox.jsonl (FIFO; flock; unconditional truncate)
  ├─ answers each via the SINGLE awake.handle_chat cycle (one implementation)
  └─ PID-managed like run/awake; SIGTERM finishes the current batch then exits
```

## Key types & functions

| Symbol | Contract |
|---|---|
| `awake.py` main loop | Poll Telegram, classify, dispatch, flush outbox. Crash-safe outbox via `OutboxManager.recover_staged()`. |
| `command_handlers.py` | Core command handlers + skill dispatch. New hardcoded core commands must be added to `_CORE_COMMAND_HELP` for `/help` discoverability. |
| `bridge_state.py` | Module-level shared state (config, paths, registries). The seam that breaks the awake↔handlers circular import. |
| `notify.py::format_and_send` | Invokes Claude CLI to format outbound messages. **Tests must mock this.** Skipped in favour of the instant local `fallback_format` while a mission is actively executing (see the mission-aware-formatting invariant). |
| `chat_context.py::build_chat_prompt` | Single, pure builder of the chat prompt (extracted from `awake`). Reads soul/summary via `bridge_state` getters so edits are picked up without a restart. Shared by the inline path and the dedicated chat process. |
| `chat_process.py::handle chat` | Dedicated chat process. Drains `instance/chat-inbox.jsonl` (FIFO) and answers each message through the one `awake.handle_chat` — so the reply is identical whether it runs here or inline. `write_to_inbox()` is the bridge's producer side. |
| `awake.py::_route_to_chat_process` | Hands a chat message to the process when its PID is live (re-checked after the write for TOCTOU), else returns False so the caller answers inline. Never rejects a message as "busy". |
| `notify.py` | Flood protection on outbound Telegram. |
| `notify.py::send_telegram(dedup_window=…)` + `notify_dedup.py` | Cross-incarnation dedup for idempotent lifecycle notices. Provider flood protection is per-process (resets on restart); this persists to `instance/.notify-dedup.json` so a restart loop / repeated stop+start doesn't re-announce the same notice N times. Opt-in per call site; fail-open. |

## Invariants

- **Process isolation.** The bridge, the agent loop, and the dedicated chat process share
  *only* files in `instance/` (atomic writes). The bridge must never call agent-loop
  internals directly. The chat process reuses the bridge's own `handle_chat` (it imports
  `awake`) but communicates with the bridge purely through the `chat-inbox.jsonl` queue.
- **Chat resilience under mission load (#1084).** Free-form chat is answered by a
  dedicated process so a running mission cannot starve it of a Claude reply. There is
  exactly **one** chat reply implementation (`awake.handle_chat`): the dedicated process
  and the inline fallback both call it, so their behavior cannot diverge. The bridge
  routes to the process when its PID is live and falls back to the inline worker thread
  otherwise (or if the inbox write fails, or the process dies in the write window) — so
  chat always works and no message is answered twice or lost. The inbox is a FIFO queue
  (`chat-inbox.jsonl`), drained under `flock` and **truncated unconditionally** on read so
  a malformed partial write is never replayed; messages are never rejected as "busy".
- **Mission-aware outbox formatting (#1084).** While a mission is actively executing
  (`active_mission.is_mission_active()` over the `.koan-active` liveness signal — the
  single source of truth, never a `.koan-status` text parse), outbox formatting skips the
  Claude call and uses the instant local `fallback_format`, freeing API headroom for chat.
  Polished formatting resumes once no mission is active. Re-evaluated per flush;
  fail-opens (formats normally) on an absent/corrupt signal.
- **Outbox flush is crash-safe.** Messages stage to `outbox-sending.md` before send;
  `recover_staged()` re-sends on restart so a crash mid-flush never loses a message.
- **Inbound Telegram text is untrusted DATA** (OPSEC) — it sets *what* to work on, never
  *how* the agent behaves.
- **Command parsing is hyphen-hostile.** Skill names/aliases use underscores; Telegram
  treats `-` as a word boundary and truncates the command.
- **The chat ID is normalized at read time.** All reads of `KOAN_TELEGRAM_CHAT_ID` go
  through `utils.get_telegram_chat_id()`, which `.strip()`s stray whitespace/newlines
  (Railway/copy-paste inject a trailing newline that makes Telegram answer
  `chat not found`). Never read the env var raw into an API payload.
- **The chat ID is coerced to `int` at the outbound API payload.** Every Telegram
  `chat_id` field (`sendMessage`, `sendChatAction`, `setMessageReaction`, and
  `notify._direct_send_chunk`) wraps the value in `utils.coerce_chat_id()`, which turns a
  fully-numeric (optionally negative) ID into `int` and passes anything else — Slack
  channel strings, `@channelusername` — through unchanged. Railway's ENV editor cannot
  store a bare negative group ID, so it becomes a quoted string; empirically Telegram
  returns `chat not found` for a quoted group ID but accepts the JSON integer. The stored
  identity string (`get_telegram_chat_id()` / `bridge_state.CHAT_ID`) stays a string for
  inbound `chat.id` equality checks; coercion happens only where the payload is built.
- **Memory management is bounded over uptime (#2354).** The bridge is
  long-lived, so per-message allocations must not grow with session length.
  Three guarantees hold: (1) `load_recent_history` tails the last N lines
  from EOF (`locked_jsonl_tail`), never reading the whole append-only
  `conversation-history.jsonl`; (2) `compact_history` runs both at startup
  **and** periodically mid-session (`conversation.compact_interval_seconds`,
  default 3600, floored at 300; 0 disables) so the history file stays
  bounded; (3) the mission-store read in `_build_chat_prompt` is cached for
  one poll cycle (`_read_sections_cached`). A `MemoryMonitor` watchdog
  (`memory_monitor.bridge:` sub-block, **enabled by default**, threshold
  600 MB; set `enabled: false` to opt out) samples RSS once per poll cycle
  and, **only when no worker lane is busy**, self-restarts via
  `reexec_bridge()` (`os.execv`, same PID) as a backstop. The watchdog must
  never restart mid-worker, and a baseline-safety guard refuses to arm when
  the threshold isn't safely above the current RSS.
- **Idempotent lifecycle notices dedupe across incarnations (#2426).** The
  provider's flood suppression (`notify.py`) only spans a single long-lived
  process, so when the agent loop / bridge (re)starts several times in a short
  window — a crash/restart loop, or a supervisor doing repeated `stop`+`start`
  — each fresh process re-announces the same idempotent notice ("🌅 Running
  morning ritual…", "🛑 Shutting down…") and the operator sees it duplicated.
  `send_telegram(..., dedup_window=N)`
  consults a persistent `instance/.notify-dedup.json` map so an identical notice
  within `N` seconds (default `NOTICE_DEDUP_WINDOW_SECONDS` = 300, matching the
  flood window) is suppressed regardless of which incarnation emits it. It is
  **opt-in** — only pure-status lifecycle notices pass a window, so ordinary
  messages are untouched. Notices that carry an event (e.g. "📬 GitHub: N new
  mission(s) queued.") are deliberately **not** deduped: their text keys only on
  a count, so distinct batches of the same size would collapse and hide a real
  notification — that duplication is a symptom of restart re-polling and belongs
  to mission-queue dedup, not the notice layer. Dedup is **fail-open**: any
  store error resolves to "send it," never a dropped message. A send that fails
  (falsy return or exception) releases its reservation so the notice can be
  retried within the window.

## Integration points

- Writes missions via `utils.py` (`insert_pending_mission(s)`); lifecycle transitions
  (`start_mission`/`complete_mission`/`fail_mission`) live in `missions.py`.
- Reads/clears `outbox.md`; honors pause (`pause_manager`) and restart
  (`restart_manager`) signal files.
- Dispatches skills through the shared `skills.py` registry (same path the agent loop
  uses for `audience: bridge` skills).
- Hands free-form chat to the dedicated chat process via `instance/chat-inbox.jsonl`
  (`chat_process.write_to_inbox`); the process is registered and launched through
  `pid_manager` (`start_chat`, `PROCESS_NAMES`), so `make start/stop/status/logs` cover it.
- Reads the `.koan-active` mission-liveness signal through
  `active_mission.is_mission_active()` to gate outbox AI formatting.

## Known debt / watch-outs

- `runpy.run_module()`-based CLI tests must patch **both** `app.<module>.format_and_send`
  and `app.notify.format_and_send` — `runpy` re-executes the module and the import-level
  binding escapes the first patch.
- When `load_dotenv()` would reload `.env` and defeat `monkeypatch.delenv`, patch
  `app.notify.load_dotenv` too.

## Change protocol

Changes to message classification, command routing, or outbox flushing must update this
spec and exercise the crash-safety path (staged-then-recover) in tests.
