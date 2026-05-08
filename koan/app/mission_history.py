"""Kōan — Mission execution history tracker.

JSON-backed tracker at instance/mission_history.json for dedup protection.
Prevents infinite re-execution of missions that fail repeatedly.
"""

import json
import time
from pathlib import Path

from app.utils import _PROJECT_TAG_STRIP_RE, atomic_write


_HISTORY_FILE = "mission_history.json"
_MAX_ENTRIES = 100


def _history_path(instance_dir: str) -> Path:
    return Path(instance_dir, _HISTORY_FILE)


def _normalize_key(mission_text: str) -> str:
    """Normalize mission text to a stable key for matching.

    Strips leading ``- ``, ``[project:X]`` / ``[projet:X]`` tags, and
    whitespace so the same mission recorded with or without a project tag
    shares one dedup counter.
    """
    line = mission_text.strip().split("\n")[0]
    line = line.removeprefix("- ").strip()
    line = _PROJECT_TAG_STRIP_RE.sub("", line).strip()
    return line


def _load_history(instance_dir: str) -> dict:
    path = _history_path(instance_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
        return {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_history(instance_dir: str, data: dict):
    path = _history_path(instance_dir)
    atomic_write(path, json.dumps(data, indent=2) + "\n")


def record_execution(
    instance_dir: str,
    mission_text: str,
    project: str = "",
    exit_code: int = 0,
):
    """Record a mission execution. Creates or increments the entry."""
    key = _normalize_key(mission_text)
    if not key:
        return

    history = _load_history(instance_dir)
    entry = history.get(key, {"count": 0, "project": project})
    entry["count"] = entry.get("count", 0) + 1
    entry["last_run"] = time.time()
    entry["last_exit_code"] = exit_code
    if project:
        entry["project"] = project
    history[key] = entry

    _save_history(instance_dir, history)


def get_execution_count(instance_dir: str, mission_text: str) -> int:
    """Return how many times this mission has been executed."""
    key = _normalize_key(mission_text)
    if not key:
        return 0
    history = _load_history(instance_dir)
    entry = history.get(key)
    if entry is None:
        return 0
    return entry.get("count", 0)


def should_skip_mission(
    instance_dir: str,
    mission_text: str,
    max_executions: int = 3,
) -> bool:
    """Return True if the mission has been executed max_executions or more times."""
    return get_execution_count(instance_dir, mission_text) >= max_executions


def cleanup_old_entries(instance_dir: str, max_age_hours: int = 48):
    """Remove entries older than max_age_hours and cap at _MAX_ENTRIES."""
    history = _load_history(instance_dir)
    if not history:
        return

    cutoff = time.time() - (max_age_hours * 3600)
    pruned = {
        k: v for k, v in history.items()
        if v.get("last_run", 0) > cutoff
    }

    # Cap at max entries (keep most recent)
    if len(pruned) > _MAX_ENTRIES:
        sorted_items = sorted(
            pruned.items(), key=lambda x: x[1].get("last_run", 0), reverse=True
        )
        pruned = dict(sorted_items[:_MAX_ENTRIES])

    _save_history(instance_dir, pruned)
