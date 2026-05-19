"""Track last-checked timestamps for /check skill.

Stores a simple JSON mapping of GitHub resource URLs to the `updated_at`
timestamp we last observed.  This lets /check skip resources that haven't
changed since the previous run — no GitHub noise, no wasted API calls.

File location: ``instance/.check-tracker.json``
"""

import json
from pathlib import Path


def _tracker_path(instance_dir):
    """Return path to the tracker file."""
    return Path(instance_dir) / ".check-tracker.json"


def _load(instance_dir):
    """Load the tracker data from disk.

    Returns:
        dict mapping URL strings to ``{"updated_at": str, "checked_at": str}``.
    """
    path = _tracker_path(instance_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(instance_dir, data):
    """Persist tracker data to disk (atomic write)."""
    from app.utils import atomic_write

    path = _tracker_path(instance_dir)
    atomic_write(path, json.dumps(data, indent=2) + "\n")


def get_last_checked(instance_dir, url):
    """Return the ``updated_at`` value we last recorded for *url*, or None."""
    data = _load(instance_dir)
    entry = data.get(url)
    if entry:
        return entry.get("updated_at")
    return None


def mark_checked(instance_dir, url, updated_at):
    """Record that we just checked *url* whose ``updated_at`` is *updated_at*.

    Args:
        instance_dir: Path to the instance directory.
        url: Canonical GitHub URL (PR or issue).
        updated_at: ISO-8601 timestamp from the GitHub API.
    """
    from datetime import datetime, timezone
    from app.locked_file import locked_json_modify

    def _update(data):
        data[url] = {
            "updated_at": updated_at,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    locked_json_modify(_tracker_path(instance_dir), _update, indent=2)


def has_changed(instance_dir, url, current_updated_at):
    """Return True if the resource has been updated since we last checked.

    Also returns True if we've never checked this URL before.
    """
    last = get_last_checked(instance_dir, url)
    if last is None:
        return True
    return current_updated_at != last
