"""Restart signal management for Kōan processes.

Provides file-based restart signaling between bridge and run loop.

Two consumers (bridge and runner) each get their own marker so a fast
wrapper-restart of the runner can no longer wipe the signal before the
bridge's polling tick sees it.

The restart flow:
1. ``request_restart`` writes ``.koan-restart-bridge`` and ``.koan-restart-run``.
2. Bridge's main loop notices ``.koan-restart-bridge`` and re-execs via
   ``os.execv`` (same PID, fresh interpreter).
3. Runner's main loop notices ``.koan-restart-run`` and exits with
   ``RESTART_EXIT_CODE``; its wrapper relaunches it.
4. Each process clears only its own marker on startup, so neither can
   silence the signal for the other.

Forced restart (``/restart --force``): the markers additionally carry a
``force`` line and the runner is sent SIGUSR2. The runner then kills the
in-flight mission and exits with ``RESTART_EXIT_CODE`` immediately instead
of waiting for the mission to finish; crash recovery re-queues the killed
mission on the next startup (or fails it, if it has already used up
``max_crash_retries``). The bridge needs nothing extra — it re-execs
on its next poll tick either way.

SIGUSR2 is **capability-gated**, never optimistic. Its default disposition is
*terminate*, so sending it to a runner that never installed ``_on_sigusr2``
hard-kills it: the mission's ``finally`` blocks never run and the provider
subprocess (spawned with ``start_new_session=True``) survives as an orphan,
burning quota and mutating the worktree while the relaunched runner starts a
new mission in the same repo. That is a live window, not a theoretical one:
``/update`` re-execs the bridge immediately but lets the runner finish its
mission first, so a new bridge routinely drives an older runner. The runner
therefore publishes ``.koan-run-caps`` (its PID, that process's start time, and
one line per capability) *after* installing the handler and removes it on exit;
``/restart --force`` only signals when :func:`force_signal_support` confirms the
live process — PID *and* start time — advertises ``sigusr2``. When nothing
vouches for that process at all it degrades to the polite restart the old runner
does understand, marker included, so the on-disk request matches what the
operator is told; when a marker exists but cannot be verified it withholds only
the signal and leaves the forced marker for the wait-loop poll. The
start time is what makes the marker safe against its writer being SIGKILLed:
that runner never reaches ``clear_runner_caps``, so a PID-only marker would
vouch for whatever later reused the PID.

Legacy ``.koan-restart`` (DEPRECATED): the single combined marker is no
longer *written* by Kōan. It is read by nothing in-tree (both consumers poll
their own per-process marker), so writing it was a no-op that lingered on disk.
``check_restart``/``clear_restart`` still accept ``target=None`` → ``.koan-restart``
purely so any out-of-tree script polling the old path keeps working; remove that
mapping once you are certain no external consumer depends on it. All in-tree
restart triggers (run loop, bridge, auto-update, REST API, dashboard) now go
through ``request_restart`` so both consumer markers are written and the restart
actually fires.

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
# is DEPRECATED: no longer written by ``request_restart``. The ``None``
# entry below is retained only so ``check_restart``/``clear_restart`` keep
# honouring ``target=None`` for any out-of-tree caller still polling the old
# path; nothing in-tree reads or writes it.
RESTART_BRIDGE_FILE = ".koan-restart-bridge"
RESTART_RUN_FILE = ".koan-restart-run"

# Body line that marks a request as forced (``/restart --force``).
FORCE_MARKER = "force"

# Capability marker published by the live runner — see the module docstring.
RUN_CAPS_FILE = ".koan-run-caps"

# Capability name meaning "I have a SIGUSR2 handler installed; signalling me
# triggers a forced restart instead of killing me".
FORCE_SIGNAL_CAP = "sigusr2"

# The caps body also records the declaring process's start time, because a PID
# alone does not identify a process: a SIGKILL/OOM-killed runner never reaches
# its ``clear_runner_caps``, so the file outlives it, and a later runner that
# happens to reuse that PID — including one rolled back to an image with no
# ``_on_sigusr2`` — would otherwise be vouched for by the corpse's marker.
# Tolerance is generous only against coarse ``ps etime`` granularity and clock
# nudges; declare-to-write latency is milliseconds. Mirrors
# ``mission_scope._record_still_names_its_process``.
CAPS_START_TOLERANCE = 30.0

# Throttle for the unreadable-marker log: the mission loop reads the marker
# every poll tick, but a *persistent* read failure disables the forced-restart
# fallback for the whole process lifetime, so it must keep saying so rather
# than latching silent after the first tick.
_force_read_error_logged_at: Optional[float] = None
FORCE_READ_ERROR_LOG_INTERVAL = 300.0

# Files written by request_restart() — the two live per-consumer markers only.
_WRITE_TARGETS = (RESTART_BRIDGE_FILE, RESTART_RUN_FILE)

_TARGET_FILES = {
    "bridge": RESTART_BRIDGE_FILE,
    "run": RESTART_RUN_FILE,
    None: RESTART_FILE,  # deprecated, read-only compat (see module docstring)
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


def request_restart(koan_root: str, force: bool = False) -> None:
    """Create restart signal files for both consumers.

    Writes the two per-consumer markers (``.koan-restart-bridge`` and
    ``.koan-restart-run``) so each consumer can clear its own without
    silencing the other. The deprecated legacy ``.koan-restart`` is no
    longer written — nothing reads it.

    Args:
        koan_root: Root path for the koan installation.
        force: Mark the request as forced (``/restart --force``). A forced
            marker tells the runner to kill an in-flight mission instead of
            waiting for it to finish — see :func:`is_force_restart`.
    """
    from app.utils import atomic_write

    body = f"restart requested at {time.strftime('%H:%M:%S')}\n"
    if force:
        body += f"{FORCE_MARKER}\n"
    for fname in _WRITE_TARGETS:
        atomic_write(Path(koan_root) / fname, body)


def is_force_restart(koan_root: str, target: str, since: float = 0) -> bool:
    """Return True when the pending restart request for ``target`` is forced.

    Durability fallback for the SIGUSR2 fast path: if the signal never
    reached the runner (stale PID file, runner mid-restart), the in-mission
    poll loop still sees the forced marker and kills the mission.

    Args:
        koan_root: Root path for the koan installation.
        target: ``"bridge"`` or ``"run"`` — required, mirroring
            :func:`check_restart`'s per-consumer markers. The deprecated
            legacy marker is not a forced-restart carrier.
        since: If > 0, ignore markers not modified after this timestamp, so a
            marker left over from a previous incarnation cannot force a
            restart. Also short-circuits the read on every poll tick.

    A missing marker is the normal case. Any *other* read failure (EACCES on
    a marker written by a differently-privileged path, EIO on the mount)
    disables this fallback, so it is logged — throttled to one line per
    ``FORCE_READ_ERROR_LOG_INTERVAL``, since the mission loop calls this every
    poll tick. Throttled rather than latched one-shot on purpose: a
    persistently unreadable marker keeps the fallback dead for every later
    ``/restart --force`` too, and a single line at the start of a long process
    life is not something an operator will still see then.
    """
    global _force_read_error_logged_at
    path = _marker_path(koan_root, target)
    try:
        if since > 0 and os.path.getmtime(path) <= since:
            return False
        with open(path, encoding="utf-8") as fh:
            return any(line.strip() == FORCE_MARKER for line in fh)
    except FileNotFoundError:
        return False
    except OSError as exc:
        now = time.monotonic()
        last = _force_read_error_logged_at
        if last is None or now - last >= FORCE_READ_ERROR_LOG_INTERVAL:
            _force_read_error_logged_at = now
            from app.run_log import log
            log("error", f"Cannot read restart marker for forced restart: {exc}")
        return False


def _runner_start_time(pid: int) -> Optional[float]:
    """Epoch seconds when *pid* started, or None when it cannot be determined.

    Thin seam over ``mission_scope._process_start_time`` (``/proc/<pid>/stat``
    on Linux, ``ps -o etime=`` elsewhere) so the caps protocol and the mission
    scope registry identify a process the same way. Imported lazily: this
    module is imported by the bridge and by skill handlers, which have no other
    reason to pull in the mission-scope machinery.
    """
    try:
        from app.mission_scope import _process_start_time
    except ImportError as exc:
        # Not just "module absent": any failing transitive import inside
        # mission_scope lands here. Silently returning None would publish a
        # caps marker with no ``start=`` on every runner, permanently
        # disabling /restart --force under declare_runner_caps' warning —
        # which blames the host for a read it never attempted. Log the cause.
        from app.run_log import log_safe
        log_safe(
            "error",
            f"Cannot import mission_scope._process_start_time ({exc}); "
            "runner start times are unavailable",
        )
        return None
    return _process_start_time(pid)


def declare_runner_caps(koan_root: str, pid: int) -> None:
    """Publish what the runner incarnation owning *pid* can be signalled with.

    Called once the SIGUSR2 handler is installed. The body carries the PID
    *and* that process's start time, so a marker that outlived its writer (a
    SIGKILLed or OOM-killed runner never runs ``clear_runner_caps``) cannot
    vouch for a later process that merely inherited the PID — including a
    rollback to a runner image that predates this protocol.

    A host where the start time cannot be read publishes the marker without
    one; :func:`force_signal_support` then reports ``"unknown"`` and
    ``/restart --force`` withholds the signal but still relies on the forced
    marker the mission wait loop polls.

    A write failure is logged, never raised: this marker is an *optional*
    capability advertisement whose absence has a defined degradation, whereas
    an escaping ``OSError`` (ENOSPC/EDQUOT on the KOAN_ROOT mount) would
    propagate out of ``main_loop`` and turn a missing marker into a startup
    crash-loop.
    """
    from app.utils import atomic_write

    body = f"pid={pid}\n"
    started_at = _runner_start_time(pid)
    if started_at is None:
        from app.run_log import log
        log(
            "warning",
            f"Cannot read start time for runner PID {pid}; /restart --force "
            "cannot verify this runner and will not signal it",
        )
    else:
        body += f"start={started_at}\n"
    try:
        atomic_write(Path(koan_root) / RUN_CAPS_FILE, body + f"{FORCE_SIGNAL_CAP}\n")
    except OSError as exc:
        from app.run_log import log
        log("error", f"Could not publish runner caps marker: {exc}")


def clear_runner_caps(koan_root: str) -> None:
    """Withdraw the runner capability marker (runner exit).

    Tolerates *any* removal failure, not just a missing file. This runs from
    the runner's shutdown ``finally``, where an escaping ``OSError`` (read-only
    root, EIO on an NFS mount) would skip the rest of the cleanup — notably
    ``release_pidfile`` — and, worse, *replace* the in-flight
    ``SystemExit(RESTART_EXIT_CODE)``, turning a forced restart into a crash.
    A marker left behind is harmless: :func:`force_signal_support`
    re-verifies the live process before trusting it.
    """
    try:
        os.remove(os.path.join(koan_root, RUN_CAPS_FILE))
    except FileNotFoundError:
        pass
    except OSError as exc:
        from app.run_log import log
        log("error", f"Could not withdraw runner caps marker: {exc}")


def force_signal_support(koan_root: str, pid: int) -> str:
    """Classify the runner at *pid* for the SIGUSR2 forced-restart capability.

    Verifies the *process*, not just the PID: the marker's recorded start time
    must still match the live one, so a marker orphaned by a killed runner
    cannot vouch for whatever later reused its PID.

    Three outcomes, because the two negative ones call for different operator
    behaviour and a different on-disk restart request:

    * ``"yes"`` — the live process advertises ``sigusr2``: signal it.
    * ``"no"`` — nothing vouches for it (no marker, or one that names another
      process/incarnation). That is a pre-upgrade runner or a stale record, so
      it very likely has no forced-marker poll either: the caller MUST fall
      back to a *polite* restart request, not just withhold the signal.
    * ``"unknown"`` — a marker exists for this PID and advertises the cap, but
      the identity cannot be confirmed (unreadable caps file, no start time in
      the body or unreadable from the live process). Withhold the signal, but
      the forced marker is still worth writing: this is overwhelmingly a
      capable runner on a host that cannot report start times.

    Signalling therefore happens only on ``"yes"`` — every uncertainty fails
    closed, so a runner with no SIGUSR2 handler is never hard-killed.
    """
    try:
        with open(os.path.join(koan_root, RUN_CAPS_FILE), encoding="utf-8") as fh:
            lines = {line.strip() for line in fh}
    except FileNotFoundError:
        return "no"
    except OSError:
        # EACCES/EIO: a capable runner may well have published a marker we
        # simply cannot read. Do not claim it predates forced restart.
        return "unknown"
    if f"pid={pid}" not in lines or FORCE_SIGNAL_CAP not in lines:
        return "no"
    declared = next(
        (line[len("start="):] for line in lines if line.startswith("start=")), None,
    )
    if declared is None:
        return "unknown"
    live = _runner_start_time(pid)
    if live is None:
        return "unknown"
    try:
        matches = abs(live - float(declared)) <= CAPS_START_TOLERANCE
    except ValueError:
        return "unknown"
    # A mismatch means the record names a different incarnation than the live
    # process — the PID-reuse case the start time exists to catch.
    return "yes" if matches else "no"



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
