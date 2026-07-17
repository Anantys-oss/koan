---
type: component-spec
title: "Component Spec — Telegram Bridge"
description: "Design contract for the Telegram bridge process that classifies human messages into chat vs. mission, dispatches commands/skills, and flushes the agent's outbox crash-safely."
tags: [bridge]
created: 2026-06-27
updated: 2026-07-17
---

# Component Spec — Telegram Bridge

**Modules:** `awake.py`, `command_handlers.py`, `bridge_state.py`, `bridge_log.py`,
`notify.py`

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
  ├─ classify message: chat → instant Claude reply
  │                    mission → queue to missions.md
  ├─ command_handlers.py: /help /stop /pause /resume /skill ... + skill dispatch
  ├─ flush outbox.md → Telegram (atomic staging via outbox-sending.md)
  └─ bridge_state.py: shared config/paths/registries (avoids circular imports)
```

## Key types & functions

| Symbol | Contract |
|---|---|
| `awake.py` main loop | Poll Telegram, classify, dispatch, flush outbox. Crash-safe outbox via `OutboxManager.recover_staged()`. |
| `command_handlers.py` | Core command handlers + skill dispatch. New hardcoded core commands must be added to `_CORE_COMMAND_HELP` for `/help` discoverability. |
| `bridge_state.py` | Module-level shared state (config, paths, registries). The seam that breaks the awake↔handlers circular import. |
| `notify.py::format_and_send` | Invokes Claude CLI to format outbound messages. **Tests must mock this** — it is the only Claude subprocess call in the bridge path. |
| `OutboxManager._format_message` | Mission-aware: while a mission is **actively executing** (`active_mission.is_mission_active()`), it skips the AI formatter and uses the instant local `fallback_format()`. Re-evaluated per flush (never sticky). |
| `handle_chat` retry policy | An **empty AI response** (exit 0, empty stdout) is retryable — the same class as a timeout — not an immediate failure. Bounded retry-with-backoff (`cli_exec.CLI_RETRY_BACKOFF` / `CLI_RETRY_MAX_ATTEMPTS`) with lighter context before any degraded message reaches the human. The whole exchange (retries included) is bounded by a wall-clock budget (`CHAT_RETRY_BUDGET`) so the single-flight chat lane (`_CHAT_LOCK`) is never held for the full retry worst case; the typing indicator wraps only each live CLI call, not the backoff sleeps. |
| `notify.py` | Flood protection on outbound Telegram. |
| `notify.py::send_telegram(dedup_window=…)` + `notify_dedup.py` | Cross-incarnation dedup for idempotent lifecycle notices. Provider flood protection is per-process (resets on restart); this persists to `instance/.notify-dedup.json` so a restart loop / repeated stop+start doesn't re-announce the same notice N times. Opt-in per call site; fail-open. |

## Invariants

- **Two-process isolation.** The bridge and the agent loop share *only* files in
  `instance/` (atomic writes). The bridge must never call agent-loop internals directly.
- **Chat is resilient to API contention (#1084).** While a mission runs, the agent loop
  and the bridge invoke the AI CLI concurrently against the same account (the default
  provider takes no cross-invocation lock), so a chat call can return an empty response or
  time out. `handle_chat` MUST treat an empty response as a **retryable** outcome (not an
  immediate "I didn't get a response" apology), retrying with backoff and lighter context
  up to a bounded number of attempts, and only show a single degraded message once all
  attempts are exhausted. Because the chat lane is **single-flight** (`_CHAT_LOCK`
  serializes chats — Claude takes a per-cwd session lock), the retry loop MUST be bounded
  by a wall-clock budget (`CHAT_RETRY_BUDGET`) so one stuck chat can't hold the lane for
  the full retry worst case and starve every subsequent chat. The typing indicator wraps
  only each live CLI call — never the backoff sleeps — so a retrying chat does not flood
  Telegram with typing pulses. The chat lane stays a **thread inside the bridge
  process** — no dedicated OS process is introduced (see `docs/architecture/daemon.md`,
  "No extra OS process is forked").
- **Outbox formatting yields to active missions (#1084).** AI outbox formatting is
  cosmetic and the lowest-value concurrent AI caller. While a mission is *actively
  executing* — determined by the authoritative provider-liveness signal via
  `active_mission.is_mission_active()` (`.koan-active`, issue #2086), never by parsing the
  human-readable `.koan-status` string — the outbox flush MUST format with the local
  `fallback_format()` and make no AI call. Normal AI formatting resumes once no mission is
  executing. The check is fail-open: an absent/corrupt/zombie signal reads as "not
  executing" so a bad signal never permanently degrades formatting.
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

## Known debt / watch-outs

- `runpy.run_module()`-based CLI tests must patch **both** `app.<module>.format_and_send`
  and `app.notify.format_and_send` — `runpy` re-executes the module and the import-level
  binding escapes the first patch.
- When `load_dotenv()` would reload `.env` and defeat `monkeypatch.delenv`, patch
  `app.notify.load_dotenv` too.

## Change protocol

Changes to message classification, command routing, or outbox flushing must update this
spec and exercise the crash-safety path (staged-then-recover) in tests.
