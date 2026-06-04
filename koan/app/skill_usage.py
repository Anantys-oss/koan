"""Track skill usage and hint display history.

Two JSON files in instance/:
- ``.skill-usage.json`` — records dates when each skill was invoked (90-day window)
- ``.hint-history.json`` — records when each skill was last hinted (7-day filtering)
"""

import fcntl
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Set

from app.utils import atomic_write

_USAGE_FILE = ".skill-usage.json"
_HINT_HISTORY_FILE = ".hint-history.json"

_USAGE_RETENTION_DAYS = 90
_HINT_RETENTION_DAYS = 7


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            data = json.load(f)
            fcntl.flock(f, fcntl.LOCK_UN)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError as e:
        print(f"[skill_usage] Failed to load {path.name}: {e}", file=sys.stderr)
        try:
            path.rename(path.with_suffix(path.suffix + ".bak"))
        except OSError:
            pass
        return {}
    except OSError as e:
        print(f"[skill_usage] Failed to load {path.name}: {e}", file=sys.stderr)
        return {}


def _save_json(path: Path, data: dict) -> None:
    """Best-effort persist — analytics data loss is acceptable, blocking callers is not."""
    try:
        atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
    except OSError as e:
        print(f"[skill_usage] Failed to save {path.name}: {e}", file=sys.stderr)


def _prune_dates(dates: list, cutoff: str) -> list:
    return [d for d in dates if d >= cutoff]


def record_usage(instance_dir: str, skill_name: str) -> None:
    """Record that a skill was invoked today."""
    path = Path(instance_dir) / _USAGE_FILE
    data = _load_json(path)

    today = datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.now() - timedelta(days=_USAGE_RETENTION_DAYS)).strftime("%Y-%m-%d")

    entries = data.get(skill_name, [])
    if today not in entries:
        entries.append(today)
    data[skill_name] = _prune_dates(entries, cutoff)

    _save_json(path, data)


def get_used_skills(instance_dir: str, days: int = 90) -> Set[str]:
    """Return set of skill names used within the window."""
    path = Path(instance_dir) / _USAGE_FILE
    data = _load_json(path)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    used = set()
    for skill_name, dates in data.items():
        if any(d >= cutoff for d in dates):
            used.add(skill_name)
    return used


def get_usage_counts(instance_dir: str, days: int = 90) -> Dict[str, int]:
    """Return dict of skill name → invocation count within the window."""
    path = Path(instance_dir) / _USAGE_FILE
    data = _load_json(path)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    counts = {}
    for skill_name, dates in data.items():
        count = sum(1 for d in dates if d >= cutoff)
        if count > 0:
            counts[skill_name] = count
    return counts


def record_hint_shown(instance_dir: str, skill_name: str) -> None:
    """Record that a hint was shown for this skill today."""
    path = Path(instance_dir) / _HINT_HISTORY_FILE
    data = _load_json(path)
    data[skill_name] = datetime.now().strftime("%Y-%m-%d")

    cutoff = (datetime.now() - timedelta(days=_HINT_RETENTION_DAYS * 2)).strftime("%Y-%m-%d")
    data = {k: v for k, v in data.items() if v >= cutoff}

    _save_json(path, data)


def get_recently_hinted(instance_dir: str, days: int = 7) -> Set[str]:
    """Return set of skills hinted within the window."""
    path = Path(instance_dir) / _HINT_HISTORY_FILE
    data = _load_json(path)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    return {k for k, v in data.items() if v >= cutoff}
