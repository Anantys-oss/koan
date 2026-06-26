"""Provider-subprocess liveness signal (``.koan-active``).

Declarative mission state — the ``▶`` timestamp written into ``missions.md`` —
can silently diverge from real execution: a mission marked *In Progress* may
have no live provider process (a *zombie*), or a hung provider keeps aging and
reads as "running" forever. The run-loop heartbeat (``health_check.py``) only
proves ``run.py`` itself is alive, not that it is actually executing a mission.

This module records the live provider PID plus a start time and mission id into
``.koan-active`` when ``run_claude_task`` spawns the subprocess, and clears it on
exit. Status consumers (dashboard, ``make status``, REST ``/v1/status``) read it
via :func:`get_execution_state` to report *observed* runtime state instead of an
inferred timestamp. See issue #2086.
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

from app.signals import ACTIVE_FILE
from app.utils import atomic_write_json

# Live PID but no provider output for this long → "stalled" rather than "working".
STALL_THRESHOLD_SECONDS = 120


def _active_path(koan_root) -> Path:
    return Path(koan_root) / ACTIVE_FILE


def write_active(
    koan_root,
    *,
    pid: int,
    mission: str = "",
    project: str = "",
    run_num: int = 0,
    stdout_file: str = "",
) -> None:
    """Record the live provider subprocess as the active mission."""
    record = {
        "pid": pid,
        "mission": (mission or "").strip()[:200],
        "project": project or "",
        "run_num": run_num,
        "started_at": time.time(),
        "stdout_file": stdout_file or "",
    }
    atomic_write_json(_active_path(koan_root), record)


def clear_active(koan_root) -> None:
    """Remove the active-mission signal (provider exited)."""
    _active_path(koan_root).unlink(missing_ok=True)


def read_active(koan_root) -> Optional[dict]:
    """Return the active-mission record, or None if absent/unreadable."""
    try:
        return json.loads(_active_path(koan_root).read_text())
    except (OSError, ValueError):
        return None


def _pid_alive(pid) -> bool:
    """Best-effort check that *pid* names a live process.

    PID reuse is possible if the signal file is left stale by a hard crash of
    ``run.py`` (the ``finally`` clear is skipped). Acceptable for a best-effort
    liveness hint — output recency disambiguates the common cases.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    return True


def _last_output_age(record: dict) -> Optional[float]:
    """Seconds since the provider last wrote stdout, or None if unknown."""
    f = record.get("stdout_file")
    if not f:
        return None
    try:
        return max(0.0, time.time() - Path(f).stat().st_mtime)
    except OSError:
        return None


def get_execution_state(koan_root) -> dict:
    """Classify real provider execution from the ``.koan-active`` signal.

    Returns a dict with keys ``state``, ``pid``, ``mission``, ``project``,
    ``run_num``, ``elapsed`` and ``last_output_age``. ``state`` is one of:

    - ``idle``    — no active-mission signal (no provider running)
    - ``working`` — live PID and recent (or unknown) output
    - ``stalled`` — live PID but no output for ``STALL_THRESHOLD_SECONDS``
    - ``zombie``  — signal present but the recorded PID is not alive
    """
    record = read_active(koan_root)
    if not record:
        return {
            "state": "idle",
            "pid": None,
            "mission": "",
            "project": "",
            "run_num": 0,
            "elapsed": 0,
            "last_output_age": None,
        }

    pid = record.get("pid")
    out_age = _last_output_age(record)
    if not _pid_alive(pid):
        state = "zombie"
    elif out_age is not None and out_age > STALL_THRESHOLD_SECONDS:
        state = "stalled"
    else:
        state = "working"

    started = record.get("started_at") or 0
    elapsed = int(time.time() - started) if started else 0

    return {
        "state": state,
        "pid": pid,
        "mission": record.get("mission", ""),
        "project": record.get("project", ""),
        "run_num": record.get("run_num", 0),
        "elapsed": max(0, elapsed),
        "last_output_age": out_age,
    }
