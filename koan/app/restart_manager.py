"""Restart signal management for Kōan processes.

Provides file-based restart signaling between bridge and run loop.

Two consumers (bridge and runner) each get their own marker so a fast
wrapper-restart of the runner can no longer wipe the signal before the
bridge's polling tick sees it.  The legacy single-file marker is also
written so a pre-upgrade incarnation polling ``.koan-restart`` can still
detect the request and re-exec into the new code.

The restart flow:
1. ``request_restart`` writes ``.koan-restart-bridge``,
   ``.koan-restart-run`` and (for backward compat) ``.koan-restart``.
2. Bridge's main loop notices ``.koan-restart-bridge`` and re-execs via
   ``os.execv`` (same PID, fresh interpreter).
3. Runner's main loop notices ``.koan-restart-run`` and exits with
   ``RESTART_EXIT_CODE``; its wrapper relaunches it.
4. Each process clears only its own marker on startup, so neither can
   silence the signal for the other.

Exit code 42 is the restart sentinel — any other exit is a real stop.
"""

import contextlib
import os
import sys
import time
from pathlib import Path
from typing import Optional

from app.signals import RESTART_FILE
RESTART_EXIT_CODE = 42

# Per-consumer marker files. The legacy ``RESTART_FILE`` (``.koan-restart``)
# is kept for backward compatibility: a pre-upgrade bridge that is still
# polling the old single-file marker can pick up the first post-upgrade
# request and re-exec into the new code.
RESTART_BRIDGE_FILE = ".koan-restart-bridge"
RESTART_RUN_FILE = ".koan-restart-run"

_TARGET_FILES = {
    "bridge": RESTART_BRIDGE_FILE,
    "run": RESTART_RUN_FILE,
    None: RESTART_FILE,
}


def _marker_path(koan_root: str, target: Optional[str]) -> str:
    try:
        fname = _TARGET_FILES[target]
    except KeyError as exc:
        raise ValueError(
            f"Unknown restart target {target!r}; "
            f"expected one of {sorted(k for k in _TARGET_FILES if k)!r} or None"
        ) from exc
    return os.path.join(koan_root, fname)


def request_restart(koan_root: str) -> None:
    """Create restart signal files for both consumers (and the legacy file).

    Writes three markers so each consumer can clear its own without
    silencing the other, and so a pre-upgrade incarnation still polling
    the legacy ``.koan-restart`` will also wake up and re-exec.
    """
    from app.utils import atomic_write

    body = f"restart requested at {time.strftime('%H:%M:%S')}\n"
    for fname in _TARGET_FILES.values():
        atomic_write(Path(koan_root) / fname, body)


def check_restart(
    koan_root: str,
    since: float = 0,
    target: Optional[str] = None,
) -> bool:
    """Check if a restart has been requested for ``target``.

    Args:
        koan_root: Root path for the koan installation.
        since: If > 0, only return True if the marker was modified after
            this timestamp.  Used to ignore stale restart signals left
            over from a previous process incarnation (prevents restart
            loops when Telegram re-delivers the /restart message).
        target: ``"bridge"`` or ``"run"`` to check the per-consumer
            marker.  ``None`` (default) checks the legacy single marker
            for backward compatibility.
    """
    restart_file = _marker_path(koan_root, target)
    if not os.path.isfile(restart_file):
        return False
    try:
        if since > 0 and os.path.getmtime(restart_file) <= since:
            return False
    except OSError:
        return False
    return True


def clear_restart(koan_root: str, target: Optional[str] = None) -> None:
    """Remove the restart signal file for ``target``.

    A consumer should only clear its own marker so the other consumer
    can still observe the request on its next poll tick.
    """
    path = _marker_path(koan_root, target)
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)


def reexec_bridge() -> None:
    """Re-exec the current Python process (bridge self-restart).

    Uses os.execv() to replace the current process with a fresh one.
    Same PID, same terminal, same file descriptors — clean restart.
    """
    python = sys.executable
    args = [python] + sys.argv
    os.execv(python, args)
