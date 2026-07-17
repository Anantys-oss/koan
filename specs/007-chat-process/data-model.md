# Data Model: Dedicated Chat Process

Transient runtime state only — no persistent schema, no mission-store changes.

## Chat inbox record (JSONL line)

File: `instance/<CHAT_INBOX_FILE>` (`CHAT_INBOX_FILE` defined once in `signals.py`).
Append-only; one JSON object per line; drained + truncated under `fcntl.flock`.

| Field  | Type   | Required | Meaning                                             |
|--------|--------|----------|-----------------------------------------------------|
| `text` | string | yes      | The human's raw chat message (untrusted DATA).      |
| `ts`   | number | no       | Producer wall-clock epoch seconds (diagnostics only).|

- **Ordering**: FIFO by file order.
- **Validation**: a line that fails JSON parse or lacks a non-empty `text` is skipped and
  discarded (the whole file is truncated on read regardless).
- **Lifecycle**: written by `chat_process.write_to_inbox(text)` (called from the bridge);
  read+cleared by `chat_process.read_and_clear_inbox()`.

## Mission-active liveness (read-only, existing signal)

Source: `instance/.koan-active` via `active_mission.get_execution_state(koan_root)`.

`is_mission_active(koan_root) -> bool` ≡ `state in {"working", "stalled"}`.

| state     | is_mission_active | Notes                                        |
|-----------|-------------------|----------------------------------------------|
| `working` | true              | Live provider PID, recent/unknown output.    |
| `stalled` | true              | Live PID, no output > threshold.             |
| `idle`    | false             | No provider subprocess.                      |
| `zombie`  | false             | Recorded PID dead — no real contention.      |

## Chat reply cycle (behavioral contract, not persisted)

`awake.handle_chat(text)` performs, in order:

1. Prompt-guard scan of `text` (warn-only for chat; quarantine on hit).
2. `save_conversation_message(..., "user", text)`.
3. `build_chat_prompt(text)` (fresh soul/summary; may recurse to `lite=True` on size cap).
4. Invoke CLI (`max_turns=5`, chat tools, `model=chat`/`fallback`, `cwd=KOAN_ROOT`,
   `project_context=False`) under an intra-process chat lock + typing indicator.
5. On empty/timeout → single lite-context retry with **identical** tool/scope/turn
   semantics (only context is trimmed and timeout halved).
6. Clean response, `send_telegram`, `save_conversation_message(..., "assistant", ...)`.
7. On unrecoverable failure → soft user-facing message, also saved to history.

Both the dedicated process and the inline fallback invoke this exact single
function → no behavioral drift (the invariant this feature exists to guarantee).

## Process registry entry

`pid_manager.PROCESS_NAMES` gains `"chat"`; PID file `instance/.koan-pid-chat`
(via `signals.pid_file("chat")`), exclusive `flock`, same acquire/release contract as
`run`/`awake`.
