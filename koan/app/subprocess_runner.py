"""Subprocess execution primitives — kill, watchdog, liveness.

Consolidates the duplicated timeout/capture/teardown logic that was
spread across ``run.py``, ``cli_exec.py``, and ``provider/__init__.py``.
"""

import contextlib
import os
import signal
import subprocess
import sys
import threading
from typing import Callable, Optional


def kill_process_group(
    proc: Optional[subprocess.Popen],
    graceful_timeout: float = 3,
    force_timeout: float = 5,
) -> None:
    """Terminate a subprocess and its entire process group.

    Sends SIGTERM to the process group, waits *graceful_timeout* seconds,
    then escalates to SIGKILL if the process is still alive.  Requires the
    subprocess to have been started with ``start_new_session=True``.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=graceful_timeout)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
            try:
                proc.wait(timeout=force_timeout)
            except subprocess.TimeoutExpired:
                print(
                    f"[subprocess_runner] warning: pid {proc.pid} "
                    f"did not exit after SIGKILL",
                    file=sys.stderr,
                )
    except (ProcessLookupError, PermissionError, OSError):
        pass


def force_kill_process_group(proc: Optional[subprocess.Popen]) -> None:
    """SIGKILL a process group immediately, with single-process fallback.

    Used by watchdog timers where graceful shutdown is not worth the delay.
    No poll() guard — the exception handler catches already-dead processes.
    """
    if proc is None:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        with contextlib.suppress(OSError, ProcessLookupError):
            proc.kill()


class ProcessWatchdog:
    """Watchdog timer that kills a process group after *timeout* seconds.

    A ``completed`` flag with a lock closes the race between the stream loop
    finishing and the Timer firing — preventing spurious kills on clean exits.
    """

    def __init__(
        self,
        proc: subprocess.Popen,
        timeout: float,
        on_timeout: Optional[Callable[[], None]] = None,
        graceful: bool = True,
    ):
        self._proc = proc
        self._timeout = timeout
        self._on_timeout = on_timeout
        self._graceful = graceful
        self._fired = False
        self._completed = False
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None

    def start(self) -> "ProcessWatchdog":
        self._timer = threading.Timer(self._timeout, self._fire)
        self._timer.daemon = True
        self._timer.start()
        return self

    def cancel(self) -> None:
        if self._timer is not None:
            self._timer.cancel()

    def mark_completed(self) -> None:
        with self._lock:
            self._completed = True

    @property
    def fired(self) -> bool:
        return self._fired

    def _fire(self) -> None:
        with self._lock:
            if self._completed:
                return
            self._fired = True

        if self._on_timeout:
            self._on_timeout()

        if self._graceful:
            kill_process_group(self._proc)
        else:
            force_kill_process_group(self._proc)


class LivenessWatchdog:
    """Watchdog that resets on each heartbeat, kills on inactivity.

    Each call to :meth:`heartbeat` restarts the countdown.  If no heartbeat
    arrives within *timeout* seconds the process group is killed.
    """

    def __init__(
        self,
        proc: subprocess.Popen,
        timeout: float,
        on_timeout: Optional[Callable[[], None]] = None,
    ):
        self._proc = proc
        self._timeout = timeout
        self._on_timeout = on_timeout
        self._fired = False
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def start(self) -> "LivenessWatchdog":
        self._schedule()
        return self

    def heartbeat(self) -> None:
        with self._lock:
            if self._fired:
                return
            if self._timer is not None:
                self._timer.cancel()
            self._schedule_locked()

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()

    @property
    def fired(self) -> bool:
        return self._fired

    def _schedule(self) -> None:
        with self._lock:
            self._schedule_locked()

    def _schedule_locked(self) -> None:
        self._timer = threading.Timer(self._timeout, self._fire)
        self._timer.daemon = True
        self._timer.start()

    def _fire(self) -> None:
        self._fired = True

        if self._on_timeout:
            self._on_timeout()

        kill_process_group(self._proc)
