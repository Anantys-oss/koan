"""Provider-subprocess liveness signal (``.koan-active``).

Declarative mission state — the ``▶`` timestamp written into ``missions.md`` —
can silently diverge from real execution: a mission marked *In Progress* may
have no live provider process (a *zombie*), or a hung provider keeps aging and
reads as "running" forever. The run-loop heartbeat (``health_check.py``) only
proves ``run.py`` itself is alive, not that it is actually executing a mission.

This module records the live provider PID plus a start time, project and run
number into ``.koan-active`` when ``run_claude_task`` spawns the subprocess, and
clears it on exit. Status consumers (dashboard, ``make status``, REST
``/v1/status``) read it
via :func:`get_execution_state` to report *observed* runtime state instead of an
inferred timestamp. See issue #2086.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from app.signals import ACTIVE_FILE
from app.utils import atomic_write_json

log = logging.getLogger("koan.active_mission")

# Live PID but no provider output for this long → "stalled" rather than "working".
STALL_THRESHOLD_SECONDS = 120


def _active_path(koan_root) -> Path:
    return Path(koan_root) / ACTIVE_FILE


def write_active(
    koan_root,
    *,
    pid: int,
    project: str = "",
    run_num: int = 0,
    stdout_file: str = "",
) -> None:
    """Record the live provider subprocess as the active mission."""
    record = {
        "pid": pid,
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
    """Return the active-mission record, or None if absent/unreadable.

    An absent signal is the normal idle case (no log). A *present but
    unparseable* signal is an anomaly during an active mission, so the
    corruption case is logged before degrading to None.
    """
    path = _active_path(koan_root)
    try:
        return json.loads(path.read_text())
    except OSError:
        return None
    except ValueError as e:
        log.warning("active-mission signal %s is corrupt: %s", path, e)
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
    """Seconds since the provider last wrote stdout.

    Returns ``None`` only when no stdout file was recorded (legitimately
    unknown → callers treat as "working"). When a file *was* recorded but
    cannot be stat'd (deleted/unreadable while the provider hangs), the age is
    unknowable yet suspicious — return ``inf`` so the session is classified
    "stalled" rather than masking a stall behind the optimistic "working".
    """
    f = record.get("stdout_file")
    if not f:
        return None
    try:
        return max(0.0, time.time() - Path(f).stat().st_mtime)
    except OSError as e:
        log.warning("active-mission stdout %s unreadable: %s", f, e)
        return float("inf")


def _live_session_count(koan_root) -> int:
    """Number of parallel worktree sessions still running with a live PID.

    The single ``.koan-active`` record only covers the main run-loop provider;
    the parallel-session executor (``session_manager``) spawns its own provider
    subprocesses tracked in ``sessions.json``. Best-effort: any failure to read
    the registry counts as zero live sessions.
    """
    try:
        from app.session_manager import SessionRegistry

        instance_dir = Path(koan_root) / "instance"
        sessions = SessionRegistry(str(instance_dir)).get_active()
    except Exception as e:
        # A swallowed read failure both masks a genuinely working parallel
        # session (reported idle) and can produce a false zombie flag, since
        # live sessions are what suppress that flag — log it so a registry
        # regression surfaces instead of degrading silently to zero (#2086).
        log.warning("active-mission session registry read failed: %s", e)
        return 0
    return sum(1 for s in sessions if _pid_alive(getattr(s, "pid", None)))


def get_execution_state(koan_root) -> dict:
    """Classify real provider execution from the ``.koan-active`` signal.

    Returns a dict with keys ``state``, ``pid``, ``project``, ``run_num``,
    ``elapsed``, ``last_output_age`` and ``sessions``. ``state`` is one of:

    - ``idle``    — no provider running (no active signal, no parallel session)
    - ``working`` — live PID and recent (or unknown) output, or a live parallel
      session
    - ``stalled`` — live PID but no output for ``STALL_THRESHOLD_SECONDS``
    - ``zombie``  — signal present but the recorded PID is not alive
    """
    record = read_active(koan_root)
    sessions = _live_session_count(koan_root)
    if not record:
        # No main-loop provider signal — but parallel sessions may be running.
        return {
            "state": "working" if sessions > 0 else "idle",
            "pid": None,
            "project": "",
            "run_num": 0,
            "elapsed": 0,
            "last_output_age": None,
            "sessions": sessions,
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

    # ``inf`` (unreadable stdout) drives the "stalled" classification above but
    # must not leak into JSON output, where it serializes as invalid ``Infinity``.
    reported_age = out_age if (out_age is not None and out_age != float("inf")) else None

    return {
        "state": state,
        "pid": pid,
        "project": record.get("project", ""),
        "run_num": record.get("run_num", 0),
        "elapsed": max(0, elapsed),
        "last_output_age": reported_age,
        "sessions": sessions,
    }


def is_zombie(koan_root, *, in_progress: bool, execution: Optional[dict] = None) -> bool:
    """Reconcile a declarative *In Progress* mission against observed liveness.

    A genuine zombie (#2086) is an In Progress mission with no live provider
    backing it. The naive check — In Progress but no active signal — flaps
    ``true`` during the brief start/stop windows where the ``missions.md`` line
    and the ``.koan-active`` signal momentarily disagree (mission marked In
    Progress just before the provider spawns, or signal cleared just before the
    mission is finalized). Those windows happen while ``run.py`` is alive and
    its heartbeat fresh, so the ``idle`` case is only treated as an orphan once
    the run-loop heartbeat has gone stale.
    """
    if not in_progress:
        return False
    ex = execution if execution is not None else get_execution_state(koan_root)
    state = ex.get("state")
    if state == "zombie":
        return True  # recorded provider PID is dead — unambiguous orphan
    if state in ("working", "stalled"):
        return False  # a provider (or parallel session) is alive
    # state == "idle": no provider signal at all. Only an orphan if the run
    # loop itself is no longer healthy — otherwise this is a normal
    # between-missions window that will resolve within seconds.
    from app.health_check import check_run_heartbeat

    return not check_run_heartbeat(str(koan_root))
