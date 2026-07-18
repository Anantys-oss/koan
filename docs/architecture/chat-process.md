---
type: doc
title: "Dedicated Chat Process"
description: "How Kōan answers Telegram chat during missions: a dedicated chat process that drains a FIFO inbox and reuses the single awake.handle_chat, plus mission-aware outbox formatting — the fix for API contention (issue #1084)."
tags: [architecture, messaging]
created: 2026-07-17
updated: 2026-07-17
---

# Dedicated Chat Process

## Why it exists

When a mission runs, up to three callers hit the single Claude quota at once: the
mission provider (`run.py`), the chat reply (`awake.handle_chat`), and the cosmetic
formatting of outbox notifications (`OutboxManager._format_message`). Chat — the smallest
and most time-sensitive call — is the one that loses, returning empty or timing out, so
the human sees **"⚠️ I didn't get a response — please try again."** (issue #1084).

Two independent changes remove the contention:

1. **A dedicated chat process** (`chat_process.py`) answers chat on its own execution
   path, so a busy mission can't starve it.
2. **Mission-aware outbox formatting** drops the lowest-value competing caller while a
   mission runs.

Both are optional-safe: if the process isn't running, chat still works inline; the outbox
change only affects formatting *style* during missions, never delivery.

## The process

`chat_process.py` is a long-lived process launched alongside `run` and `awake` by
`pid_manager.start_all()`, PID-managed identically (`start_chat`, `PROCESS_NAMES`,
`stop_processes`, status/logs) and runnable standalone with `make chat`.

**Transport.** The bridge hands a chat message to the process by appending one JSON
object per line to `instance/chat-inbox.jsonl` (`write_to_inbox`), under `fcntl.flock`.
The process polls that file (~0.5s), and on each read drains it FIFO and **truncates it
unconditionally** — even if no line parsed — so a malformed partial write is dropped, not
replayed on every poll. The filename is single-sourced as `signals.CHAT_INBOX_FILE`.

**One reply implementation.** The process answers each message through the *same*
`awake.handle_chat` the inline fallback uses. There is exactly one chat reply cycle
(prompt-guard scan → save user message → build prompt → CLI invoke with the chat tools /
`max_turns` / `cwd=KOAN_ROOT` / `project_context=False` → lite-context retry → clean →
send → save assistant message), so the reply cannot differ between the two paths. Prompt
building lives in `chat_context.build_chat_prompt`, which reads soul/summary through the
bridge's mtime-cached getters — so personality edits take effect on the next reply with
no restart.

**Shutdown.** SIGTERM/SIGINT ask the loop to stop; it finishes the current batch (so a
message already read from the inbox is never dropped), releases its PID file, and exits.

## Routing and fallback

`awake.handle_message` sends free-form chat through `_route_to_chat_process(text)`:

- if the chat PID is live, it writes to the inbox, then **re-checks** liveness (closing
  the TOCTOU window where the process dies between the check and the write);
- it returns `True` only when the message was queued to a still-live process;
- otherwise the bridge falls back to `_run_in_worker(handle_chat, lane="chat")`.

So exactly one path answers any message — never both (no double answer) and never neither
(no lost message). The inbox is a FIFO queue: a new message is always accepted even while
a previous one is still being answered — no "busy" rejection.

## Mission-aware outbox formatting

`OutboxManager._format_message` checks `active_mission.is_mission_active(koan_root)` and,
while a mission is actively executing, returns the instant local `fallback_format`
instead of calling Claude. `is_mission_active` is the single source of truth for "a
mission is running now": it reads the authoritative `.koan-active` provider-liveness
signal (`working`/`stalled` ⇒ active; `idle`/`zombie`/absent ⇒ not), never a
human-readable `.koan-status` string. It is re-evaluated per flush and fail-opens
(formats normally) on an absent or corrupt signal, so polished formatting resumes the
moment the mission ends.

## Design notes

- **Single implementation over "two that match".** An earlier exploration (PR #1088)
  duplicated the reply/retry path inside the chat process, where it drifted from the
  inline one (`max_turns` changed, `cwd` changed) and skipped the prompt guard and
  history writes. Reusing one `handle_chat` makes that class of divergence impossible.
- **Reuse over new mechanisms.** File-based `flock` IPC (like `outbox.md`), PID
  management, the mtime-cached getters, and the `.koan-active` signal are all existing
  primitives.

See `specs/components/bridge.md` for the durable contract and `specs/007-chat-process/`
for the design record.
