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

Both survive process restarts and use the same TTL/cap/locking pattern.
"""

import json
import time
from pathlib import Path


_TRACKER_FILE = ".koan-github-processed.json"
_TRACKER_FILE_THREADS = ".koan-github-processed-threads.json"
_TTL_SECONDS = 7 * 86400  # 7 days
_MAX_ENTRIES = 5000


def _tracker_path(instance_dir: str) -> Path:
    return Path(instance_dir) / _TRACKER_FILE


def _load(instance_dir: str) -> dict:
    """Load tracker data, pruning expired entries."""
    path = _tracker_path(instance_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return {}
    except (json.JSONDecodeError, OSError):
        return {}
    # Prune expired
    now = time.time()
    return {k: v for k, v in data.items() if now - v < _TTL_SECONDS}


def _threads_path(instance_dir: str) -> Path:
    return Path(instance_dir) / _TRACKER_FILE_THREADS


def _load_threads(instance_dir: str) -> dict:
    """Load thread-tracker data, pruning expired entries."""
    path = _threads_path(instance_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return {}
    except (json.JSONDecodeError, OSError):
        return {}
    now = time.time()
    return {k: v for k, v in data.items() if now - v < _TTL_SECONDS}


def is_comment_tracked(instance_dir: str, comment_id: str) -> bool:
    """Check if a comment ID has been persistently recorded."""
    if not comment_id:
        return False
    data = _load(instance_dir)
    return comment_id in data


def _prune_expired(data: dict) -> None:
    """Remove expired entries (in-place)."""
    now = time.time()
    expired = [k for k, v in data.items() if now - v >= _TTL_SECONDS]
    for k in expired:
        del data[k]


def _cap_entries(data: dict) -> None:
    """Evict oldest entries beyond _MAX_ENTRIES (in-place)."""
    if len(data) > _MAX_ENTRIES:
        sorted_items = sorted(data.items(), key=lambda x: x[1])
        data.clear()
        data.update(dict(sorted_items[-_MAX_ENTRIES:]))


def track_comment(instance_dir: str, comment_id: str) -> None:
    """Record a comment ID as processed (with file lock for thread safety)."""
    if not comment_id:
        return
    try:
        from app.locked_file import locked_json_modify

        def _update(data):
            _prune_expired(data)
            data[comment_id] = time.time()
            _cap_entries(data)

        locked_json_modify(_tracker_path(instance_dir), _update)
    except OSError:
        pass  # Best-effort — don't break notification processing


def is_thread_tracked(instance_dir: str, thread_key: str) -> bool:
    """Check if an assignment-notification thread key has been recorded.

    ``thread_key`` is a composite ``"<notification_id>:<updated_at>"``.
    Bumping ``updated_at`` (e.g. a re-requested review or a new commit
    pushed to the PR) yields a fresh key so the next notification cycle
    is not deduped — a renewed request still queues a new mission.
    """
    if not thread_key:
        return False
    data = _load_threads(instance_dir)
    return thread_key in data


def track_thread(instance_dir: str, thread_key: str) -> None:
    """Record an assignment-notification thread key as processed.

    Uses an exclusive file lock for thread/process safety.
    Best-effort: file errors are swallowed rather than breaking the
    notification pipeline.
    """
    if not thread_key:
        return
    try:
        from app.locked_file import locked_json_modify

        def _update(data):
            _prune_expired(data)
            data[thread_key] = time.time()
            _cap_entries(data)

        locked_json_modify(_threads_path(instance_dir), _update)
    except OSError:
        pass  # Best-effort — don't break notification processing
