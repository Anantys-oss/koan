"""Persistent CI check queue.

Decouples CI monitoring from the rebase workflow. After a rebase push,
the PR is enqueued here instead of blocking for 10-30 minutes.  The
iteration loop drains one entry per cycle via ``ci_queue_runner.drain_one()``.

File location: ``instance/.ci-queue.json``

Queue entries::

    {
        "pr_url": "https://github.com/owner/repo/pull/123",
        "branch": "koan/feature",
        "full_repo": "owner/repo",
        "pr_number": "123",
        "project_path": "/path/to/project",
        "queued_at": "2026-03-26T10:30:00+00:00"
    }

Thread-safe and process-safe via fcntl file locking, following the
same pattern as ``check_tracker.py``.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


# Entries older than this are expired and removed automatically.
_MAX_AGE_HOURS = 24


def _default_instance_dir() -> Path:
    from app.utils import KOAN_ROOT
    return KOAN_ROOT / "instance"


def _queue_path() -> Path:
    return _default_instance_dir() / ".ci-queue.json"


def _load() -> List[dict]:
    """Load queue from disk. Returns list of entries."""
    path = _queue_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(entries: List[dict]):
    """Persist queue to disk (atomic write)."""
    from app.utils import atomic_write

    path = _queue_path()
    atomic_write(path, json.dumps(entries, indent=2) + "\n")


def _is_expired(entry: dict) -> bool:
    """Check if a queue entry has exceeded the max age."""
    queued_at = entry.get("queued_at")
    if not queued_at:
        return True
    try:
        ts = datetime.fromisoformat(queued_at)
        age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        return age_hours > _MAX_AGE_HOURS
    except (ValueError, TypeError):
        return True


def enqueue(pr_url: str, branch: str, full_repo: str,
            pr_number: str, project_path: str) -> bool:
    """Add a CI check to the queue. Returns True if added, False if duplicate.

    Deduplicates by pr_url — if a check for the same PR is already queued,
    the entry is updated (timestamp refreshed) rather than duplicated.
    """
    from app.locked_file import locked_json_modify

    def _update(entries):
        # Dedup: update existing entry for the same PR
        for i, entry in enumerate(entries):
            if entry.get("pr_url") == pr_url:
                entries[i] = {
                    "pr_url": pr_url,
                    "branch": branch,
                    "full_repo": full_repo,
                    "pr_number": pr_number,
                    "project_path": project_path,
                    "queued_at": datetime.now(timezone.utc).isoformat(),
                }
                return False  # Updated, not added

        entries.append({
            "pr_url": pr_url,
            "branch": branch,
            "full_repo": full_repo,
            "pr_number": pr_number,
            "project_path": project_path,
            "queued_at": datetime.now(timezone.utc).isoformat(),
        })
        return True

    return locked_json_modify(
        _queue_path(), _update,
        default_factory=list, indent=2,
    )


def remove(pr_url: str) -> bool:
    """Remove a CI check from the queue by PR URL. Returns True if found."""
    from app.locked_file import locked_json_modify

    def _update(entries):
        original_len = len(entries)
        entries[:] = [e for e in entries if e.get("pr_url") != pr_url]
        return len(entries) < original_len

    return locked_json_modify(
        _queue_path(), _update,
        default_factory=list, indent=2,
    )


def peek() -> Optional[dict]:
    """Return the oldest non-expired entry without removing it, or None."""
    entries = _load()
    # Prune expired entries
    valid = [e for e in entries if not _is_expired(e)]
    if len(valid) != len(entries):
        # Clean up expired entries under lock
        from app.locked_file import locked_json_modify

        def _prune(entries):
            entries[:] = [e for e in entries if not _is_expired(e)]

        locked_json_modify(
            _queue_path(), _prune,
            default_factory=list, indent=2,
        )
        # Re-read pruned result
        valid = [e for e in _load() if not _is_expired(e)]
    return valid[0] if valid else None


def list_entries() -> List[dict]:
    """Return all non-expired entries."""
    entries = _load()
    return [e for e in entries if not _is_expired(e)]


def size() -> int:
    """Return the number of non-expired entries in the queue."""
    return len(list_entries())


# ---------------------------------------------------------------------------
# CI monitoring queue (replaces ## CI section in missions.md)
#
# Each entry tracks one open PR being actively monitored with attempt counters:
#   {
#     "pr_url":       "https://github.com/org/repo/pull/42",
#     "branch":       "koan/fix-bug",
#     "full_repo":    "org/repo",
#     "pr_number":    "42",
#     "project_name": "myapp",
#     "queued":       "2026-06-14T10:00",
#     "attempt":      0,
#     "max_attempts": 5
#   }
# ---------------------------------------------------------------------------

import fcntl  # noqa: E402 — after existing imports


def _monitor_path() -> Path:
    return _default_instance_dir() / ".ci-monitor.json"


def _monitor_lock_path() -> Path:
    return _default_instance_dir() / ".ci-monitor.lock"


def _monitor_load() -> list:
    p = _monitor_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _monitor_save(items: list) -> None:
    from app.utils import atomic_write
    p = _monitor_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps(items, indent=2) + "\n")


def monitor_get_items() -> list:
    """Return all CI monitoring entries in insertion order."""
    return _monitor_load()


def monitor_add_item(
    project_name: str,
    pr_url: str,
    pr_number: str,
    branch: str,
    full_repo: str,
    max_attempts: int,
) -> None:
    """Add or reset a CI monitoring entry (deduped by *pr_url*)."""
    lp = _monitor_lock_path()
    Path(lp).parent.mkdir(parents=True, exist_ok=True)
    with open(lp, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            items = _monitor_load()
            items = [i for i in items if i.get("pr_url") != pr_url]
            items.append({
                "pr_url": pr_url,
                "branch": branch,
                "full_repo": full_repo,
                "pr_number": str(pr_number),
                "project_name": project_name,
                "queued": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
                "attempt": 0,
                "max_attempts": max_attempts,
            })
            _monitor_save(items)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def monitor_remove_item(pr_url: str) -> None:
    """Remove the CI monitoring entry for *pr_url* (no-op if not found)."""
    lp = _monitor_lock_path()
    Path(lp).parent.mkdir(parents=True, exist_ok=True)
    with open(lp, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            items = [i for i in _monitor_load() if i.get("pr_url") != pr_url]
            _monitor_save(items)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def monitor_update_attempt(pr_url: str) -> None:
    """Increment the attempt counter for the entry matching *pr_url*."""
    lp = _monitor_lock_path()
    Path(lp).parent.mkdir(parents=True, exist_ok=True)
    with open(lp, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            items = _monitor_load()
            for item in items:
                if item.get("pr_url") == pr_url:
                    item["attempt"] = item.get("attempt", 0) + 1
                    break
            _monitor_save(items)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def monitor_migrate_from_missions_md() -> int:
    """One-time migration from ``## CI`` section in missions.md → .ci-monitor.json.

    Called at startup when the JSON file is absent but missions.md has a
    ``## CI`` section.  Returns the count of items migrated.
    """
    if _monitor_path().exists():
        return 0

    instance_dir = _default_instance_dir()
    md_path = instance_dir / "missions.md"
    if not md_path.exists():
        return 0

    try:
        content = md_path.read_text(encoding="utf-8")
    except OSError:
        return 0

    if "## CI" not in content:
        return 0

    from app.missions import get_ci_items
    items_from_md = get_ci_items(content)
    if not items_from_md:
        return 0

    json_items = [
        {
            "pr_url": item.get("pr_url", ""),
            "branch": item.get("branch", ""),
            "full_repo": item.get("full_repo", ""),
            "pr_number": str(item.get("pr_number", "")),
            "project_name": item.get("project", ""),
            "queued": item.get("queued", ""),
            "attempt": item.get("attempt", 0),
            "max_attempts": item.get("max_attempts", 5),
        }
        for item in items_from_md
        if item.get("pr_url")
    ]

    if json_items:
        _monitor_save(json_items)

    return len(json_items)
