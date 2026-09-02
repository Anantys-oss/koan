#!/usr/bin/env python3
"""
Kōan -- One-shot datetime-scheduled mission triggers

Reads ``instance/events/*.json`` each iteration.  Any event whose ``run_at``
timestamp has passed is inserted into the pending mission queue and then moved
to ``instance/events/archive/`` for audit purposes.

A file that cannot be turned into a valid event -- undecodable bytes, invalid
JSON, a non-object payload, a missing/blank ``mission`` or ``run_at``, or an
unparseable ``run_at`` -- is moved to ``instance/events/quarantine/`` so it
cannot re-poison every later iteration.  An operator whose scheduled mission
never fired should look there.

Event file format::

    {
        "type": "once",
        "run_at": "2026-04-25T09:00:00",
        "mission": "Check CI status on koan/..."
    }

Only ``type: "once"`` is supported.  Additional types may be added later.
"""

import contextlib
import json
import os
import re
import shutil
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from app.run_log import log_safe
from app.utils import append_to_outbox, insert_pending_mission

# Regex for relative time specs like "30m", "2h", "1h30m", "90s"
_RELATIVE_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
# Regex for HH:MM
_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _quarantine(event_file: Path, quarantine_dir: Path) -> bool:
    """Move an unparseable event file out of the scan path.

    Returns ``True`` when the file left ``events/``.  Never raises: this runs
    from inside an ``except`` block, so a quarantine that itself fails (
    read-only directory, ENOSPC, a competing process that already moved the
    file) must degrade to "skip this file" rather than abort the caller's scan.
    """
    try:
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        dest = quarantine_dir / event_file.name
        # shutil.move() overwrites its destination silently, so probe for a
        # free name instead of trusting a single second-resolution suffix --
        # the quarantined copy is the only remaining evidence of the event.
        while dest.exists():
            dest = quarantine_dir / f"{event_file.stem}_{time.time_ns()}{event_file.suffix}"
        shutil.move(str(event_file), str(dest))
        return True
    except OSError as exc:
        log_safe("warning", f"[events] could not quarantine {event_file.name}: {exc}")
        return False


def _notify_dropped(instance: Path, filename: str, mission: str) -> None:
    """Tell the operator that a scheduled mission will never run.

    A log line is not a channel the operator watches; a well-formed-looking
    event carrying real mission text deserves an outbox message.
    """
    try:
        append_to_outbox(
            instance / "outbox.md",
            f"⚠️ Scheduled event `{filename}` was quarantined; its mission "
            f"will not run:\n{mission[:200]}\n\n",
        )
    except OSError as exc:
        log_safe("warning", f"[events] could not report dropped {filename}: {exc}")


def tick(instance_dir: str) -> List[str]:
    """Process overdue one-shot events and insert their missions.

    Scans ``instance_dir/events/*.json`` (excluding the ``archive/`` and
    ``quarantine/`` subdirectories), inserts missions whose ``run_at`` has
    passed, and moves processed files to ``instance_dir/events/archive/``.
    Files that cannot yield a valid event are moved to
    ``instance_dir/events/quarantine/`` instead of firing.

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
    quarantine_dir = events_dir / "quarantine"
    now = datetime.now()
    enqueued: List[str] = []

    for event_file in sorted(events_dir.glob("*.json")):
        try:
            # Strict, explicit-UTF-8 read: invalid bytes must be quarantined,
            # not silently enqueued as a replacement-char mission. Decode in
            # two steps so a bad byte surfaces as UnicodeDecodeError rather
            # than being masked by errors="replace" inside a JSON string value.
            text = event_file.read_bytes().decode("utf-8")
            data = json.loads(text)
        except OSError as exc:
            # A read failure is transient: the file may be mid-write, or the
            # bridge may have archived it between the glob and the read.
            # Leave it in place and retry next tick -- quarantining here would
            # destroy a still-valid pending mission.
            log_safe("warning", f"[events] could not read {event_file.name}: {exc}")
            continue
        except ValueError as exc:
            # UnicodeDecodeError and json.JSONDecodeError are both ValueError
            # subclasses, not OSError, so a bare OSError except would miss them
            # and crash the whole iteration. write_event_file() publishes the
            # body atomically, so a parse failure means genuine corruption
            # rather than an in-flight write.
            log_safe("warning", f"[events] quarantining unparseable {event_file.name}: {exc}")
            _quarantine(event_file, quarantine_dir)
            continue

        if not isinstance(data, dict):
            log_safe("warning", f"[events] quarantining structurally invalid {event_file.name}")
            _quarantine(event_file, quarantine_dir)
            continue

        mission_value = data.get("mission")
        run_at_value = data.get("run_at")
        if (
            not isinstance(mission_value, str)
            or not mission_value.strip()
            or not isinstance(run_at_value, str)
            or not run_at_value
        ):
            log_safe("warning", f"[events] quarantining structurally invalid {event_file.name}")
            _quarantine(event_file, quarantine_dir)
            continue
        mission = mission_value.strip()
        run_at_str = run_at_value

        try:
            run_at = datetime.fromisoformat(run_at_str)
        except ValueError as exc:
            log_safe("warning", f"[events] quarantining {event_file.name}: bad run_at: {exc}")
            _quarantine(event_file, quarantine_dir)
            _notify_dropped(instance, event_file.name, mission)
            continue
        # ISO strings may carry a 'Z' or offset (tz-aware); 'now' is naive-local.
        # Comparing the two raises TypeError, which would crash the tick and leave
        # the file un-archived — poisoning every subsequent iteration. Normalize
        # any aware value to the equivalent naive local wall-clock.
        if run_at.tzinfo is not None:
            run_at = run_at.astimezone().replace(tzinfo=None)

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
        dt = datetime.fromisoformat(arg)
    except ValueError:
        pass
    else:
        # Stored events are naive-local (write_event_file drops tzinfo). Convert an
        # aware input to local wall-clock so the offset is honored, not discarded.
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt

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
        Path to the created file.  The final ``*.json`` name never exists in a
        partial state: the body is written to a hidden sibling the ``*.json``
        glob cannot match, then the final name is claimed with ``os.link()``,
        which is atomic and refuses to clobber.  ``tick()`` in the other
        process therefore cannot read a half-written event and quarantine it.
    """
    events_dir.mkdir(parents=True, exist_ok=True)
    ts = int(run_at.timestamp() * 1000)  # millisecond precision for uniqueness
    payload = {
        "type": "once",
        "run_at": run_at.strftime("%Y-%m-%dT%H:%M:%S"),
        "mission": mission,
    }
    content = json.dumps(payload, indent=2, ensure_ascii=False)
    # Stage the complete body under a hidden name the "*.json" scan glob cannot
    # match; mkstemp() keeps concurrent writers (same process or not) off each
    # other's staging file.
    fd, tmp_name = tempfile.mkstemp(dir=str(events_dir), prefix=".koan-event-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        for counter in range(100):
            suffix = f"_{counter}" if counter else ""
            candidate = events_dir / f"event_{ts}{suffix}.json"
            try:
                # link() is the atomic claim: it fails instead of overwriting,
                # so it both replaces the old O_EXCL uniqueness check and
                # guarantees the final name only ever appears with the whole
                # payload already behind it.
                os.link(str(tmp), str(candidate))
            except FileExistsError:
                continue
            return candidate
        raise RuntimeError(f"Failed to create unique event file after 100 attempts: {ts}")
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
