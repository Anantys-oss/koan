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
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Callable, Optional


# Default configuration — overridable via config.yaml stagnation: section.
_DEFAULT_CHECK_INTERVAL = 60       # seconds between stdout samples
_DEFAULT_ABORT_AFTER_CYCLES = 3    # identical hashes required to abort
_DEFAULT_SAMPLE_LINES = 50         # trailing lines hashed
_DEFAULT_MIN_BYTES = 512           # ignore tiny outputs (not enough signal)
_CLASSIFY_TAIL_LINES = 100        # lines to read for pattern classification

# Filename of the per-mission stagnation retry tracker (lives under
# instance/). Persists across restarts so a stagnated mission requeued
# right before a crash doesn't lose its retry count.
_RETRY_TRACKER_FILENAME = ".stagnation-retries.json"


def _read_tail(stdout_file: str, lines: int) -> Optional[bytes]:
    """Read the last *lines* lines worth of bytes from *stdout_file*.

    Uses a byte-aligned seek window of ``lines * 200`` bytes — a generous
    upper bound for average log-line length. The seek may land mid-codepoint
    inside a multi-byte UTF-8 sequence; callers either hash the raw bytes
    (where this is fine for equality comparison) or decode with
    ``errors='replace'`` (classification).

    Returns ``None`` when the file is unreadable or smaller than
    :data:`_DEFAULT_MIN_BYTES`, signalling "not enough output yet".
    """
    try:
        size = os.path.getsize(stdout_file)
    except OSError:
        return None
    if size < _DEFAULT_MIN_BYTES:
        return None
    try:
        with open(stdout_file, "rb") as f:
            window = min(size, lines * 200)
            f.seek(size - window)
            return f.read(window)
    except OSError:
        return None


def _tail_hash(stdout_file: str, sample_lines: int) -> Optional[str]:
    """Compute a SHA-256 hash over the last *sample_lines* lines of the file.

    Returns ``None`` if the file is unreadable, empty, or smaller than
    :data:`_DEFAULT_MIN_BYTES`. A ``None`` result means "no signal yet" —
    the caller should not count it toward consecutive-identical tracking.

    Note: the seek-window is byte-aligned, not line-semantic. We open in
    binary mode, jump to ``size - window``, and split on ``\\n`` — that
    seek may land mid-codepoint inside a multi-byte UTF-8 sequence. This
    is fine for our equality use-case (the same seek position produces
    the same bytes, so identical inputs still hash identically), but the
    hash represents byte content, not logical text.
    """
    raw = _read_tail(stdout_file, sample_lines)
    if raw is None:
        return None
    blines = raw.splitlines()
    if len(blines) > sample_lines:
        blines = blines[-sample_lines:]
    return hashlib.sha256(b"\n".join(blines)).hexdigest()


# ---------------------------------------------------------------------------
# Root-cause pattern classification
# ---------------------------------------------------------------------------
#
# When the monitor aborts a session, we classify the stdout tail to give
# operators a hint about *why* Claude was stuck.  The patterns are ordered
# by specificity — first match wins.  The ``unknown`` fallback catches
# everything else.

# Compiled once at import time for performance.
_TOOL_NAME_RE = re.compile(
    r"\b(?:Bash|Read|Glob|Grep|Edit|Write|WebFetch|WebSearch|Agent)\b"
)
_ERROR_KW_RE = re.compile(
    r"\b(?:Error|Exception|Traceback|failed|retry|FAILED)\b", re.IGNORECASE,
)
_INTERACTIVE_RE = re.compile(
    r"(?:\[y/n\]|Continue\?|Enter |Press |Confirm |proceed\?)", re.IGNORECASE,
)
_QUOTA_RE = re.compile(
    r"(?:quota[_ ]exhausted|rate[_ ]limit|429|capacity|over[_ ]?limit"
    r"|usage[_ ]limit|max_tokens_exceeded)", re.IGNORECASE,
)


def _classify_from_bytes(raw: bytes, tail_lines: int) -> tuple:
    """Classify a stagnation pattern from pre-read stdout bytes.

    Extracted from :func:`classify_stagnation` so :meth:`StagnationMonitor._sample_once`
    can share a single :func:`_read_tail` call between hash computation and pattern
    classification — avoiding a second file read after stagnation is confirmed.

    Returns ``(pattern_type, excerpt)`` — same contract as :func:`classify_stagnation`.
    """
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if len(lines) > tail_lines:
        lines = lines[-tail_lines:]

    if not lines:
        return ("silent", "")

    # --- tool_loop: same tool name dominates the tail ---
    tool_counts: dict[str, int] = {}
    for line in lines:
        for m in _TOOL_NAME_RE.finditer(line):
            tool_counts[m.group()] = tool_counts.get(m.group(), 0) + 1
    if tool_counts:
        top_tool, top_count = max(tool_counts.items(), key=lambda kv: kv[1])
        if top_count >= 5:
            excerpt = _build_excerpt(lines, top_tool)
            return ("tool_loop", excerpt)

    # --- infinite_retry: error keywords repeated ---
    error_lines = [l for l in lines if _ERROR_KW_RE.search(l)]
    if len(error_lines) >= 3:
        excerpt = _build_excerpt(error_lines, None)
        return ("infinite_retry", excerpt)

    # --- interactive_wait: prompt for stdin ---
    for line in reversed(lines):
        if _INTERACTIVE_RE.search(line):
            return ("interactive_wait", line.strip()[:200])

    # --- quota_mid_session ---
    for line in reversed(lines):
        if _QUOTA_RE.search(line):
            return ("quota_mid_session", line.strip()[:200])

    return ("unknown", _build_excerpt(lines, None))


def classify_stagnation(stdout_file: str, tail_lines: int = _CLASSIFY_TAIL_LINES) -> tuple:
    """Classify the likely root cause of a stagnation event.

    Reads the last *tail_lines* lines of *stdout_file* and applies an ordered
    pattern set.  Returns ``(pattern_type, excerpt)`` where *excerpt* is at
    most 200 chars of representative text.

    Pattern types (in match order):
    - ``tool_loop``: same tool name appears in >= 5 of the sampled lines
    - ``infinite_retry``: error keywords appear in >= 3 lines
    - ``interactive_wait``: stdin prompt detected
    - ``quota_mid_session``: quota / rate-limit markers in output
    - ``silent``: file exists but has no content (or below threshold)
    - ``unknown``: none of the above
    """
    raw = _read_tail(stdout_file, tail_lines)
    if raw is None:
        return ("silent", "")
    return _classify_from_bytes(raw, tail_lines)


def _build_excerpt(lines: list, keyword: Optional[str]) -> str:
    """Build a <= 200 char excerpt from representative lines.

    If *keyword* is given, prefer lines containing it.
    """
    if keyword:
        matching = [l for l in lines if keyword in l]
        source = matching[-3:] if matching else lines[-3:]
    else:
        source = lines[-3:]
    text = " | ".join(l.strip() for l in source)
    return text[:200]


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
        self.pattern_type: str = ""
        self.pattern_excerpt: str = ""

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
        # Read the larger classification window once — it subsumes the hash
        # window.  On the abort path, the same buffer feeds both the hash
        # comparison and the pattern classifier, avoiding a second file read
        # while the subprocess is still writing.
        classify_lines = max(self._sample_lines, _CLASSIFY_TAIL_LINES)
        raw = _read_tail(self._stdout_file, classify_lines)

        # Compute hash from the last sample_lines of the buffer.
        current: Optional[str] = None
        if raw is not None:
            blines = raw.splitlines()
            if len(blines) > self._sample_lines:
                blines = blines[-self._sample_lines:]
            current = hashlib.sha256(b"\n".join(blines)).hexdigest()

        if current is None:
            # Not enough output yet — reset to avoid counting empty samples.
            self._last_hash = None
            self._consecutive = 0
            return

        if current == self._last_hash:
            self._consecutive += 1
        else:
            self._last_hash = current
            self._consecutive = 0
            # Fresh output means any previous "warned" state is stale.
            self._warned = False
            return

        # Warn on first duplicate (consecutive == 1) before escalating.
        if self._consecutive == 1 and not self._warned:
            self._warned = True
            if self._on_warn is not None:
                try:
                    self._on_warn(self._consecutive)
                except Exception as e:
                    # Never let a callback exception kill the monitor.
                    # Intentional stderr diagnostic (not debug leftover) — keeps the
                    # monitor decoupled from any project-level logging config.
                    print(f"[stagnation_monitor] on_warn error: {e}", file=sys.stderr)

        if self._consecutive >= self._abort_after and not self.stagnated:
            self.stagnated = True
            # Classify from the same buffer used for hash detection — no
            # second file read.  raw is guaranteed non-None here because
            # current is non-None (early return above handles the None case).
            try:
                self.pattern_type, self.pattern_excerpt = _classify_from_bytes(
                    raw, _CLASSIFY_TAIL_LINES
                )
            except Exception as e:
                # Intentional stderr diagnostic — keeps the monitor
                # decoupled from any project-level logging config.
                print(f"[stagnation_monitor] classify error: {e}", file=sys.stderr)
                self.pattern_type = "unknown"
                self.pattern_excerpt = ""
            try:
                self._on_abort()
            except Exception as e:
                # Intentional stderr diagnostic (not debug leftover) — keeps the
                # monitor decoupled from any project-level logging config.
                print(f"[stagnation_monitor] on_abort error: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Per-mission retry tracking
# ---------------------------------------------------------------------------
#
# When a mission stagnates, we don't want to fail it outright on the first
# detection — Claude can be unstuck by a fresh start. The tracker records
# how many times each mission has stagnated so :func:`run._finalize_mission`
# can decide between "requeue and try again" and "give up, mark Failed".
# Counters are keyed by a stable SHA-256 of the mission title so very long
# titles don't bloat the JSON, and identical titles in different instances
# are isolated by living under ``instance/``.


def _retry_tracker_path(instance_dir: str) -> Path:
    """Path to the per-instance stagnation retry counter file."""
    return Path(instance_dir) / _RETRY_TRACKER_FILENAME


def _mission_key(mission_title: str) -> str:
    """Stable, length-bounded key for a mission title."""
    return hashlib.sha256(mission_title.encode("utf-8", errors="replace")).hexdigest()


def _load_retry_tracker(instance_dir: str) -> dict:
    path = _retry_tracker_path(instance_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_retry_tracker(instance_dir: str, data: dict) -> None:
    path = _retry_tracker_path(instance_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from app.utils import atomic_write_json
        atomic_write_json(path, data)
    except OSError as e:
        # Stderr diagnostic — losing the counter just means an extra retry,
        # not a correctness bug.
        print(f"[stagnation_monitor] retry tracker save error: {e}", file=sys.stderr)


def _extract_count(raw) -> int:
    """Extract retry count from a tracker entry (int or dict with 'count')."""
    if isinstance(raw, dict):
        raw = raw.get("count", 0)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def get_retry_count(instance_dir: str, mission_title: str) -> int:
    """Return how many times *mission_title* has been stagnation-requeued."""
    data = _load_retry_tracker(instance_dir)
    return _extract_count(data.get(_mission_key(mission_title), 0))


def get_retry_info(instance_dir: str, mission_title: str) -> dict:
    """Return full retry info for *mission_title* including pattern classification.

    Returns a dict with keys ``count``, ``pattern_type``, ``sample_lines``.
    """
    data = _load_retry_tracker(instance_dir)
    raw = data.get(_mission_key(mission_title), {})
    if isinstance(raw, int):
        return {"count": max(0, raw), "pattern_type": "", "sample_lines": ""}
    if isinstance(raw, dict):
        return {
            "count": _extract_count(raw),
            "pattern_type": raw.get("pattern_type", ""),
            "sample_lines": raw.get("sample_lines", ""),
        }
    return {"count": 0, "pattern_type": "", "sample_lines": ""}


def increment_retry_count(
    instance_dir: str,
    mission_title: str,
    pattern_type: str = "",
    pattern_excerpt: str = "",
) -> int:
    """Increment and persist the stagnation retry counter for *mission_title*.

    When *pattern_type* is provided, the tracker entry is upgraded to a dict
    with ``count``, ``pattern_type``, and ``sample_lines`` fields.

    Returns the new count.
    """
    data = _load_retry_tracker(instance_dir)
    key = _mission_key(mission_title)
    current = _extract_count(data.get(key, 0))
    new_count = current + 1
    data[key] = {
        "count": new_count,
        "pattern_type": pattern_type,
        "sample_lines": pattern_excerpt[:500],
    }
    _save_retry_tracker(instance_dir, data)
    return new_count


def clear_retry_count(instance_dir: str, mission_title: str) -> None:
    """Drop the retry counter for *mission_title* (e.g. on completion)."""
    data = _load_retry_tracker(instance_dir)
    key = _mission_key(mission_title)
    if key in data:
        data.pop(key, None)
        _save_retry_tracker(instance_dir, data)
