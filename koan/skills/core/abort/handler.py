"""Kōan abort skill -- abort the current in-progress mission.

Writes ``.koan-abort`` AND sends SIGUSR1 to the run process so the
abort takes effect within milliseconds. Without the signal, the runner
would only notice the file on its next ``proc.wait`` poll (up to 30 s).
The file remains as a durability fallback: if the signal is lost (runner
restarting, PID file stale), the poll loop still picks it up.
"""

import contextlib
import os
import signal as sig_mod
from pathlib import Path

from app.skills import SkillContext


def _verify_is_runner(pid: int) -> bool:
    """Best-effort check that *pid* belongs to the koan runner.

    Mitigates the PID-reuse race between :func:`check_pidfile` and
    :func:`os.kill`: if Linux recycled the runner's PID for an unrelated
    process, SIGUSR1's default disposition would terminate it. Reads
    ``/proc/<pid>/cmdline`` and confirms it references ``run.py``.
    Returns False when ``/proc`` is unavailable (non-Linux) or unreadable
    — the file-based fallback still aborts on the next poll cycle.
    """
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return b"run.py" in cmdline


def handle(ctx: SkillContext) -> str:
    """Handle /abort command."""
    from app.pid_manager import check_pidfile
    from app.signals import ABORT_FILE
    from app.utils import atomic_write

    abort_path = ctx.koan_root / ABORT_FILE
    atomic_write(abort_path, "abort")

    # Wake the runner immediately via SIGUSR1. The runner's handler kills
    # the active Claude subprocess and clears the abort file. If the runner
    # is paused / between missions, the signal is harmless (no claude_proc).
    # _verify_is_runner guards against a recycled PID belonging to an
    # unrelated process (SIGUSR1's default disposition would kill it).
    with contextlib.suppress(OSError, ProcessLookupError, ValueError):
        run_pid = check_pidfile(ctx.koan_root, "run")
        if run_pid and _verify_is_runner(run_pid):
            os.kill(run_pid, sig_mod.SIGUSR1)

    return "⏭️ Abort requested. Current mission will be aborted and moved to Failed."
