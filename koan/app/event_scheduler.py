#!/usr/bin/env python3
"""
Kōan -- One-shot datetime-scheduled mission triggers

Reads ``instance/events/*.json`` each iteration.  Any event whose ``run_at``
timestamp has passed is inserted into the pending mission queue and then moved
to ``instance/events/archive/`` for audit purposes.

Event file format::

    {
        "type": "once",
        "run_at": "2026-04-25T09:00:00",
        "mission": "Check CI status on koan/..."
    }

Only ``type: "once"`` is supported.  Additional types may be added later.
"""

import json
import os
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from app.utils import insert_pending_mission

# Regex for relative time specs like "30m", "2h", "1h30m", "90s"
_RELATIVE_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
# Regex for HH:MM
_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def tick(instance_dir: str) -> List[str]:
    """Process overdue one-shot events and insert their missions.

    Scans ``instance_dir/events/*.json`` (excluding the ``archive/``
    subdirectory), inserts missions whose ``run_at`` has passed, and
    moves processed files to ``instance_dir/events/archive/``.

    Args:
        instance_dir: Path to the Kōan instance directory.

    Returns:
        List of mission texts that were enqueued.
    """
    instance = Path(instance_dir)
    events_dir = instance / "events"
    if not events_dir.exists():
        return []

    missions_path = instance / "missions.md"
    archive_dir = events_dir / "archive"
    now = datetime.now()
    enqueued: List[str] = []

    for event_file in sorted(events_dir.glob("*.json")):
        try:
            data = json.loads(event_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        mission = data.get("mission", "").strip()
        run_at_str = data.get("run_at", "")
        if not mission or not run_at_str:
            continue

        try:
            run_at = datetime.fromisoformat(run_at_str)
        except ValueError:
            continue

        if run_at > now:
            continue

        insert_pending_mission(missions_path, mission)
        enqueued.append(mission)

        archive_dir.mkdir(parents=True, exist_ok=True)
        dest = archive_dir / event_file.name
        # Avoid clobbering an existing archive entry with the same name.
        if dest.exists():
            stem = event_file.stem
            suffix = event_file.suffix
            ts = int(time.time())
            dest = archive_dir / f"{stem}_{ts}{suffix}"
        shutil.move(str(event_file), str(dest))

    return enqueued


def parse_at_arg(arg: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """Parse a time argument for the ``/at`` Telegram command.

    Supported formats:

    * ``HH:MM`` — today at that time; rolls over to tomorrow if already past
    * ``2026-04-25T09:00:00`` — ISO 8601 datetime
    * ``30m`` / ``2h`` / ``1h30m`` — relative offset from now

    Returns ``None`` for unrecognised input.
    """
    if now is None:
        now = datetime.now()
    arg = arg.strip()
    if not arg:
        return None

    # ISO datetime
    try:
        return datetime.fromisoformat(arg)
    except ValueError:
        pass

    # HH:MM
    m = _HHMM_RE.match(arg)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if hour > 23 or minute > 59:
            return None
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    # Relative: 30m / 2h / 1h30m / 90s
    m = _RELATIVE_RE.match(arg)
    if m and any(m.groups()):
        hours = int(m.group(1) or 0)
        minutes = int(m.group(2) or 0)
        seconds = int(m.group(3) or 0)
        delta = timedelta(hours=hours, minutes=minutes, seconds=seconds)
        if delta.total_seconds() > 0:
            return now + delta

    return None


def write_event_file(events_dir: Path, run_at: datetime, mission: str) -> Path:
    """Write a one-shot event JSON file to ``events_dir``.

    Creates ``events_dir`` if it doesn't exist.  Filenames are based on the
    epoch timestamp to ensure uniqueness across rapid successive calls.

    Args:
        events_dir: Directory to write the event file into.
        run_at: Scheduled datetime.
        mission: Mission text to enqueue when the trigger fires.

    Returns:
        Path to the created file.
    """
    events_dir.mkdir(parents=True, exist_ok=True)
    ts = int(run_at.timestamp() * 1000)  # millisecond precision for uniqueness
    payload = {
        "type": "once",
        "run_at": run_at.strftime("%Y-%m-%dT%H:%M:%S"),
        "mission": mission,
    }
    content = json.dumps(payload, indent=2, ensure_ascii=False)
    # Use O_CREAT|O_EXCL for atomic create — avoids TOCTOU race vs exists() loop
    for counter in range(100):
        suffix = f"_{counter}" if counter else ""
        candidate = events_dir / f"event_{ts}{suffix}.json"
        try:
            fd = os.open(str(candidate), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        return candidate
    raise RuntimeError(f"Failed to create unique event file after 100 attempts: {ts}")
