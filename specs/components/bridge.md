---
type: component-spec
title: "Component Spec — Telegram Bridge"
tags: [bridge]
created: 2026-06-27
updated: 2026-07-08
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
| `notify.py` | Flood protection on outbound Telegram. |

## Invariants

- **Two-process isolation.** The bridge and the agent loop share *only* files in
  `instance/` (atomic writes). The bridge must never call agent-loop internals directly.
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
