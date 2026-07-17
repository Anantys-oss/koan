# Internal Contracts: Dedicated Chat Process

These are the internal Python interfaces the feature introduces or changes. No external
(REST/CLI) surface changes.

## `signals.py`

```python
CHAT_INBOX_FILE = "chat-inbox.jsonl"   # single source for the inbox filename (FR-007)
```

## `active_mission.py`

```python
def is_mission_active(koan_root) -> bool:
    """True when a mission provider subprocess is actively (or stalled-but-live)
    burning the Claude quota — i.e. get_execution_state(koan_root)["state"]
    in {"working", "stalled"}.  Single source of truth (FR-007)."""
```

## `chat_context.py`

```python
def build_chat_prompt(text: str, *, lite: bool = False) -> str:
    """Pure prompt builder, extracted verbatim from awake._build_chat_prompt.
    Reads soul/summary through bridge_state.get_soul()/get_summary() (mtime-cached,
    so always fresh — FR-004).  Same 12k-char cap → lite recursion behavior."""
```

## `chat_engine.py`

```python
def respond(text: str) -> None:
    """The single shared chat reply cycle (see data-model.md §Chat reply cycle).
    Called by BOTH chat_process (dedicated path) and awake.handle_chat (fallback),
    guaranteeing identical behavior (FR-003).  Side effects only: sends Telegram
    reply(s) and appends to conversation history."""
```

`awake.handle_chat(text)` remains as a thin wrapper delegating to `chat_engine.respond`
(preserves existing import/test surface).

## `chat_process.py`

```python
def write_to_inbox(text: str) -> bool:
    """Append one JSONL record to instance/<CHAT_INBOX_FILE> under flock.
    Returns True on success, False on write failure (caller falls back)."""

def read_and_clear_inbox() -> list[dict]:
    """Under flock: read all lines, UNCONDITIONALLY truncate, return parsed
    records (malformed lines skipped).  FR-009."""

def has_pending_requests() -> bool:
    """True if the inbox currently holds any bytes (diagnostics; NOT used to
    reject messages — queue is FIFO, FR-005)."""

def main() -> None:
    """Acquire the 'chat' PID file; install SIGTERM handler; poll the inbox every
    POLL_INTERVAL; drain FIFO calling chat_engine.respond(entry['text']); on
    SIGTERM finish the in-flight reply then release PID and exit (FR-011)."""
```

## `awake.py` (routing)

```python
def _is_chat_process_running() -> bool:
    """check_pidfile(KOAN_ROOT, 'chat') is not None."""

def _route_to_chat_process(text: str) -> bool:
    """If the chat process is live: write_to_inbox(text); RE-CHECK liveness
    (TOCTOU); return True only if still live after the write.  Else False so the
    caller falls back to the inline worker thread.  Never rejects as 'busy'."""

# handle_message free-form branch:
#   if not _route_to_chat_process(text):
#       _run_in_worker(chat_engine.respond, text, lane="chat")
```

## `outbox_manager.py` (Phase 1)

```python
# OutboxManager._format_message(raw_content):
#   if is_mission_active(self._koan_root):      # koan_root passed explicitly
#       return fallback_format(raw_content)     # skip Claude — cut contention (FR-006)
#   ... existing Claude formatting ...
```

`OutboxManager.__init__` gains an explicit `koan_root` (defaulting to
`instance_dir.parent` for back-compat) so the liveness path is not derived implicitly.

## `pid_manager.py`

```python
PROCESS_NAMES = ("run", "awake", "chat", "ollama", "dashboard", "api")

def start_chat(koan_root, verify_timeout=...) -> ...:
    """Launch app/chat_process.py as an exclusive PID-managed process, mirroring
    start_awake()."""
# start_all() launches chat alongside awake/run; stop_processes() and the status/
# logs wiring include it.
```

## Test contracts

- `run_cli` / `format_and_send` are mocked — no real Claude calls.
- `test_chat_engine`: guard scan runs, both user+assistant history writes happen, retry
  uses `max_turns=5` + `project_context=False` + `cwd=KOAN_ROOT` (regression lock vs #1088).
- `test_chat_process`: FIFO drain order; unconditional truncate on malformed-only input;
  SIGTERM finishes in-flight then exits.
- `test_awake`: routes to process when up; falls back when down; falls back on
  post-write death; never double-answers; no "busy" rejection.
- `test_outbox_manager`: `_format_message` skips Claude when `is_mission_active` true,
  formats normally when false.
- `test_active_mission`: `is_mission_active` true for working/stalled, false for
  idle/zombie.
