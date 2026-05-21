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

Thread-safe and process-safe via :func:`~app.locked_file.locked_json_modify`.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from app.locked_file import locked_json_modify, locked_json_read


# Entries older than this are expired and removed automatically.
_MAX_AGE_HOURS = 24


def _queue_path(instance_dir) -> Path:
    return Path(instance_dir) / ".ci-queue.json"


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


def enqueue(instance_dir, pr_url: str, branch: str, full_repo: str,
            pr_number: str, project_path: str) -> bool:
    """Add a CI check to the queue. Returns True if added, False if duplicate.

    Deduplicates by pr_url — if a check for the same PR is already queued,
    the entry is updated (timestamp refreshed) rather than duplicated.
    """
    def _enqueue(entries):
        new_entry = {
            "pr_url": pr_url,
            "branch": branch,
            "full_repo": full_repo,
            "pr_number": pr_number,
            "project_path": project_path,
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }
        for i, entry in enumerate(entries):
            if entry.get("pr_url") == pr_url:
                entries[i] = new_entry
                return False  # Updated, not added
        entries.append(new_entry)
        return True

    return locked_json_modify(
        _queue_path(instance_dir), _enqueue,
        default_factory=list, indent=2,
    )


def remove(instance_dir, pr_url: str) -> bool:
    """Remove a CI check from the queue by PR URL. Returns True if found."""
    def _remove(entries):
        original_len = len(entries)
        entries[:] = [e for e in entries if e.get("pr_url") != pr_url]
        return len(entries) < original_len

    return locked_json_modify(
        _queue_path(instance_dir), _remove,
        default_factory=list, indent=2,
    )


def peek(instance_dir) -> Optional[dict]:
    """Return the oldest non-expired entry without removing it, or None."""
    entries = locked_json_read(
        _queue_path(instance_dir), default_factory=list,
    )
    valid = [e for e in entries if not _is_expired(e)]
    if len(valid) != len(entries):
        # Clean up expired entries under lock (re-reads to avoid races)
        def _prune(entries):
            entries[:] = [e for e in entries if not _is_expired(e)]

        locked_json_modify(
            _queue_path(instance_dir), _prune,
            default_factory=list, indent=2,
        )
    return valid[0] if valid else None


def list_entries(instance_dir) -> List[dict]:
    """Return all non-expired entries."""
    entries = locked_json_read(
        _queue_path(instance_dir), default_factory=list,
    )
    return [e for e in entries if not _is_expired(e)]


def size(instance_dir) -> int:
    """Return the number of non-expired entries in the queue."""
    return len(list_entries(instance_dir))
