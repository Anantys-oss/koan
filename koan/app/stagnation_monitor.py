"""Stagnation detection for long-running Claude CLI missions.

When Claude gets stuck in a loop — repeatedly calling the same tool,
regenerating the same partial output, or oscillating between two states —
the mission watchdog only kicks in after ``mission_timeout`` elapses,
burning quota with zero progress.

This module adds a lightweight daemon thread that samples the subprocess
stdout file at a configurable interval and hashes the last N lines of
output. When that hash stays identical for K consecutive samples, we
escalate: the first identical hash is just a warning; once
``abort_after_cycles`` is reached, the monitor calls the supplied abort
callback (typically ``_kill_process_group``) and flips its
``stagnated`` flag so the caller can report the outcome distinctly
from a normal failure.

Usage::

    monitor = StagnationMonitor(
        stdout_file="/tmp/claude-out-xxx",
        on_abort=lambda: _kill_process_group(proc),
    )
    monitor.start()
    try:
        proc.wait()
    finally:
        monitor.stop()
    if monitor.stagnated:
        # mark mission as stagnated
"""

from __future__ import annotations

import hashlib
import os
import sys
import threading
from typing import Callable, Optional


# Default configuration — overridable via config.yaml stagnation: section.
_DEFAULT_CHECK_INTERVAL = 60       # seconds between stdout samples
_DEFAULT_ABORT_AFTER_CYCLES = 3    # identical hashes required to abort
_DEFAULT_SAMPLE_LINES = 50         # trailing lines hashed
_DEFAULT_MIN_BYTES = 512           # ignore tiny outputs (not enough signal)


def _tail_hash(stdout_file: str, sample_lines: int) -> Optional[str]:
    """Compute a SHA-256 hash over the last *sample_lines* lines of the file.

    Returns ``None`` if the file is unreadable, empty, or smaller than
    :data:`_DEFAULT_MIN_BYTES`. A ``None`` result means "no signal yet" —
    the caller should not count it toward consecutive-identical tracking.
    """
    try:
        size = os.path.getsize(stdout_file)
    except OSError:
        return None
    if size < _DEFAULT_MIN_BYTES:
        return None

    try:
        with open(stdout_file, "rb") as f:
            # Read from the end; sample_lines * 200 bytes is a generous
            # upper bound (avg log line length) and keeps the hash cheap
            # even for multi-megabyte stdout captures.
            window = min(size, sample_lines * 200)
            f.seek(size - window)
            tail = f.read(window)
    except OSError:
        return None

    lines = tail.splitlines()
    if len(lines) > sample_lines:
        lines = lines[-sample_lines:]
    joined = b"\n".join(lines)
    return hashlib.sha256(joined).hexdigest()


class StagnationMonitor:
    """Daemon thread that aborts runaway Claude sessions stuck in a loop.

    Samples *stdout_file* every *check_interval_seconds*. When the hash of
    the last *sample_lines* lines stays identical for *abort_after_cycles*
    consecutive samples, the escalation sequence fires:

    - first duplicate hash → log a warning via *on_warn* (if supplied)
    - ``abort_after_cycles`` duplicates → invoke *on_abort* and flip
      :attr:`stagnated` to True.

    The monitor is tolerant of startup delays: ``_tail_hash`` returns
    ``None`` until enough output exists, and ``None`` samples never
    increment the identical-hash counter.

    Args:
        stdout_file: Path to the subprocess stdout capture file.
        on_abort: Callable invoked once when stagnation is confirmed.
            Should kill the subprocess (e.g. ``_kill_process_group``).
        on_warn: Optional callable invoked on the first warn-level
            detection. Receives the current consecutive count.
        check_interval_seconds: Seconds between samples. Default 60.
        abort_after_cycles: Consecutive identical hashes required to
            trigger abort. Must be >= 2. Default 3.
        sample_lines: Trailing lines to hash per sample. Default 50.
    """

    def __init__(
        self,
        stdout_file: str,
        on_abort: Callable[[], None],
        on_warn: Optional[Callable[[int], None]] = None,
        check_interval_seconds: int = _DEFAULT_CHECK_INTERVAL,
        abort_after_cycles: int = _DEFAULT_ABORT_AFTER_CYCLES,
        sample_lines: int = _DEFAULT_SAMPLE_LINES,
    ) -> None:
        if abort_after_cycles < 2:
            raise ValueError("abort_after_cycles must be >= 2")
        self._stdout_file = stdout_file
        self._on_abort = on_abort
        self._on_warn = on_warn
        self._check_interval = max(1, int(check_interval_seconds))
        self._abort_after = int(abort_after_cycles)
        self._sample_lines = max(1, int(sample_lines))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_hash: Optional[str] = None
        self._consecutive = 0
        self._warned = False
        self.stagnated: bool = False

    def start(self) -> None:
        """Launch the monitor daemon thread. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop,
            name="stagnation-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the thread to stop and wait for it to exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        # Wait one interval before the first sample — gives the subprocess
        # a chance to start producing output before we judge it stuck.
        while not self._stop_event.wait(self._check_interval):
            self._sample_once()
            if self.stagnated:
                break

    def _sample_once(self) -> None:
        """Read one sample, update counters, fire callbacks if needed."""
        current = _tail_hash(self._stdout_file, self._sample_lines)
        if current is None:
            # Not enough output yet — reset to avoid counting empty samples.
            self._last_hash = None
            self._consecutive = 0
            return

        if current == self._last_hash:
            self._consecutive += 1
        else:
            self._last_hash = current
            self._consecutive = 1
            # Fresh output means any previous "warned" state is stale.
            self._warned = False
            return

        # Warn on first duplicate (consecutive == 2) before escalating.
        if self._consecutive == 2 and not self._warned:
            self._warned = True
            if self._on_warn is not None:
                try:
                    self._on_warn(self._consecutive)
                except Exception as e:
                    # Never let a callback exception kill the monitor.
                    print(f"[stagnation_monitor] on_warn error: {e}", file=sys.stderr)

        if self._consecutive >= self._abort_after and not self.stagnated:
            self.stagnated = True
            try:
                self._on_abort()
            except Exception as e:
                print(f"[stagnation_monitor] on_abort error: {e}", file=sys.stderr)
