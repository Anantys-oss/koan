#!/usr/bin/env python3
"""Dedicated chat process — answers Telegram chat independently of the mission runner.

Watches ``instance/<CHAT_INBOX_FILE>`` for queued human chat messages and answers
each through the *same* ``awake.handle_chat`` cycle the bridge uses inline — so a
reply is identical whether it runs here or in the bridge fallback (one
implementation, no divergence). Running in its own process means a busy mission
provider can no longer starve chat of a Claude reply, which is the bug in issue
#1084. See specs/007-chat-process/.

Transport: an append-only JSONL queue drained FIFO under ``fcntl.flock`` and
truncated unconditionally on read (a malformed partial write is dropped, never
replayed). Lifecycle mirrors the other long-lived processes (exclusive PID file,
graceful SIGTERM). Optional: when this process is not running, the bridge falls
back to answering chat inline, so nothing here is required for correctness.
"""

import fcntl
import json
import signal
import time
from pathlib import Path
from typing import List

from app.bridge_log import log
from app.bridge_state import INSTANCE_DIR, KOAN_ROOT
from app.signals import CHAT_INBOX_FILE

# Poll cadence for the inbox. Sub-second, negligible next to the multi-second
# Claude call, and zero cost while idle (a stat + empty read).
POLL_INTERVAL = 0.5

# Set by the SIGTERM/SIGINT handler; the poll loop finishes the current batch
# (so no read-and-cleared message is lost) and then exits.
_stop = False


def _inbox_path() -> Path:
    """Resolve the inbox path from the (patchable) module-level INSTANCE_DIR."""
    return INSTANCE_DIR / CHAT_INBOX_FILE


def write_to_inbox(text: str) -> bool:
    """Append one chat message to the inbox (FIFO). Returns False on failure.

    Called by the bridge to hand a chat message to this process.
    """
    record = json.dumps({"text": text, "ts": time.time()}, ensure_ascii=False)
    try:
        with open(_inbox_path(), "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(record + "\n")
                f.flush()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return True
    except OSError as e:
        log("error", f"[chat] inbox write failed: {e}")
        return False


def read_and_clear_inbox() -> List[dict]:
    """Read all queued messages FIFO and clear the inbox.

    The file is truncated *unconditionally* after the read — whether or not any
    line parsed — so a malformed partial write can never accumulate or be
    replayed on every poll (FR-009). Malformed/empty lines are skipped.
    """
    path = _inbox_path()
    if not path.exists():
        return []
    try:
        with open(path, "r+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                raw = f.read()
                f.seek(0)
                f.truncate()
                f.flush()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except OSError as e:
        log("error", f"[chat] inbox read failed: {e}")
        return []

    entries: List[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            log("warn", f"[chat] dropping malformed inbox line: {line[:80]}")
            continue
        text = obj.get("text")
        if isinstance(text, str) and text.strip():
            entries.append(obj)
    return entries


def has_pending_requests() -> bool:
    """True if the inbox currently holds any bytes (diagnostics only).

    NOT used to reject messages — the queue is FIFO, so a message is always
    accepted even while a previous one is in flight (FR-005).
    """
    path = _inbox_path()
    try:
        return path.exists() and path.stat().st_size > 0
    except OSError:
        return False


def _request_stop(signum, frame):
    """SIGTERM/SIGINT handler — ask the loop to stop after the current batch."""
    global _stop
    _stop = True
    log("chat", "Shutdown signal received — finishing current batch then exiting.")


def _drain_once(handle_chat) -> int:
    """Read and answer one batch of queued messages FIFO. Returns the count.

    The full batch is finished even if a stop is requested mid-batch, so a
    message that was read (and cleared from the inbox) is never dropped (FR-011).
    """
    entries = read_and_clear_inbox()
    for entry in entries:
        try:
            handle_chat(entry["text"])
        except Exception as e:
            log("error", f"[chat] handle_chat failed: {e}")
    return len(entries)


def main() -> None:
    """Run the dedicated chat process until SIGTERM/SIGINT."""
    from app.awake import handle_chat
    from app.pid_manager import acquire_pidfile, release_pidfile

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    lock = acquire_pidfile(KOAN_ROOT, "chat")
    log("chat", "Dedicated chat process started — watching for chat messages.")
    try:
        while not _stop:
            _drain_once(handle_chat)
            if _stop:
                break
            time.sleep(POLL_INTERVAL)
    finally:
        release_pidfile(lock, KOAN_ROOT, "chat")
        log("chat", "Dedicated chat process stopped.")


if __name__ == "__main__":
    main()
