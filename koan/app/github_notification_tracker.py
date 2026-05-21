"""Persistent trackers for processed GitHub notifications.

Two parallel trackers live here:

- **Comment tracker** (``instance/.koan-github-processed.json``):
  records comment IDs for @mention notifications. Used as a fallback when
  the reactions API fails to confirm a 👍/👀 was placed.
- **Thread tracker** (``instance/.koan-github-processed-threads.json``):
  records ``"<notification_id>:<updated_at>"`` keys for assignment
  notifications (``review_requested`` / ``assign``). These have no comment
  to react to, so without persistent tracking the same notification gets
  re-processed on every restart.

Both survive process restarts and use the same TTL/cap/locking pattern
via :func:`~app.locked_file.locked_json_modify`.
"""

import contextlib
import time
from pathlib import Path

from app.locked_file import locked_json_modify, locked_json_read


_TRACKER_FILE = ".koan-github-processed.json"
_TRACKER_FILE_THREADS = ".koan-github-processed-threads.json"
_TTL_SECONDS = 7 * 86400  # 7 days
_MAX_ENTRIES = 5000


def _tracker_path(instance_dir: str) -> Path:
    return Path(instance_dir) / _TRACKER_FILE


def _prune_expired(data: dict) -> dict:
    """Remove entries older than TTL."""
    now = time.time()
    return {k: v for k, v in data.items() if now - v < _TTL_SECONDS}


def _cap_entries(data: dict) -> None:
    """Evict oldest entries beyond MAX_ENTRIES (mutates in place)."""
    if len(data) > _MAX_ENTRIES:
        sorted_items = sorted(data.items(), key=lambda x: x[1])
        keep = dict(sorted_items[-_MAX_ENTRIES:])
        data.clear()
        data.update(keep)


def is_comment_tracked(instance_dir: str, comment_id: str) -> bool:
    """Check if a comment ID has been persistently recorded."""
    if not comment_id:
        return False
    data = _prune_expired(
        locked_json_read(_tracker_path(instance_dir))
    )
    return comment_id in data


def track_comment(instance_dir: str, comment_id: str) -> None:
    """Record a comment ID as processed (with file lock for thread safety)."""
    if not comment_id:
        return

    def _track(data):
        # Prune expired before adding
        expired = [k for k, v in data.items() if time.time() - v >= _TTL_SECONDS]
        for k in expired:
            del data[k]
        data[comment_id] = time.time()
        _cap_entries(data)

    with contextlib.suppress(OSError):
        locked_json_modify(_tracker_path(instance_dir), _track)


def _threads_path(instance_dir: str) -> Path:
    return Path(instance_dir) / _TRACKER_FILE_THREADS


def is_thread_tracked(instance_dir: str, thread_key: str) -> bool:
    """Check if an assignment-notification thread key has been recorded.

    ``thread_key`` is a composite ``"<notification_id>:<updated_at>"``.
    Bumping ``updated_at`` (e.g. a re-requested review or a new commit
    pushed to the PR) yields a fresh key so the next notification cycle
    is not deduped — a renewed request still queues a new mission.
    """
    if not thread_key:
        return False
    data = _prune_expired(
        locked_json_read(_threads_path(instance_dir))
    )
    return thread_key in data


def track_thread(instance_dir: str, thread_key: str) -> None:
    """Record an assignment-notification thread key as processed.

    Best-effort: file errors are swallowed rather than breaking the
    notification pipeline.
    """
    if not thread_key:
        return

    def _track(data):
        expired = [k for k, v in data.items() if time.time() - v >= _TTL_SECONDS]
        for k in expired:
            del data[k]
        data[thread_key] = time.time()
        _cap_entries(data)

    with contextlib.suppress(OSError):
        locked_json_modify(_threads_path(instance_dir), _track)
