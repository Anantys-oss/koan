"""Handler for /restart command.

Restarts both agent and bridge processes without pulling new code.

``/restart`` is polite: the runner finishes the mission it is on before
exiting. ``/restart --force`` is not — it marks the request forced and sends
SIGUSR2 so the runner kills the in-flight mission and restarts immediately.
Use it when the run loop is wedged. The killed mission is re-queued by crash
recovery on the next startup, unless it has already used up its crash retries
(``max_crash_retries``) — recovery then escalates it to Failed.

SIGUSR2 is only sent to a runner that advertises the ``sigusr2`` capability
(``.koan-run-caps``). A runner from a pre-upgrade image has no handler for it,
so the signal's default disposition would terminate it outright — leaving its
provider subprocess orphaned in its own session.

The capability is therefore resolved **before** the markers are written, so the
on-disk request always matches the reply. A runner nothing vouches for (no caps
marker, or one naming another process) very likely has no forced-marker poll
either, so that case degrades to a genuinely *polite* request. A runner whose
marker exists but cannot be verified (unreadable caps file, a host that cannot
report process start times) keeps the forced marker — only the signal is
withheld.

The marker fallback is weaker than the signal and the replies say so: it is
polled by ``run_claude_task``'s mission wait loop, but a skill-dispatch mission
(``/review``, ``/fix``, ``/implement``) blocks on its child's stdout with no
tick to read a marker from, and notices the restart only once it finishes.
"""

import signal as sig_mod

from app.skills import SkillContext

_FORCE_FLAGS = {"--force", "-f", "force"}

_MARKER_FALLBACK = (
    "falling back to the forced restart marker: the agent loop picks it up at "
    "its next mission poll (up to 30 s) — a skill mission (/review, /fix, "
    "/implement) only when it finishes, since it has no poll of its own."
)

_FORCED = (
    "🔄 Force restart requested. Any in-flight mission is killed now and "
    "re-queued on startup — unless it has exhausted its crash retries, in "
    "which case recovery moves it to Failed."
)

_NO_RUNNER = (
    "🔄 Force restart requested. Could not locate the agent loop (no PID, or "
    "an unreadable pidfile) — if it is running, " + _MARKER_FALLBACK +
    " If it is not, it starts clean."
)

_NOT_CAPABLE = (
    "🔄 Force restart requested, but nothing vouches for the running agent "
    "loop's SIGUSR2 handler — it predates forced restart, or its capability "
    "marker names another process. Signalling it would kill it outright and "
    "orphan its provider session, so this was downgraded to a polite restart "
    "on disk too: it exits once the current mission finishes."
)

_UNVERIFIABLE = (
    "🔄 Force restart requested. The agent loop advertises forced restart but "
    "its identity could not be verified (unreadable capability marker, or a "
    "host that cannot report process start times), so the signal was "
    "withheld — " + _MARKER_FALLBACK
)

_SIGNAL_LOST = (
    "🔄 Force restart requested. Could not signal the agent loop — "
    + _MARKER_FALLBACK
)


def handle(ctx: SkillContext) -> str:
    """Request a restart of both processes."""
    from app.restart_manager import request_restart

    force = any(arg in _FORCE_FLAGS for arg in ctx.args.lower().split())
    if not force:
        request_restart(str(ctx.koan_root))
        return "🔄 Restart requested. Both processes will restart shortly."
    return _force_restart_runner(ctx)


def _force_restart_runner(ctx: SkillContext) -> str:
    """Write the restart request the runner can actually honour, and say so.

    Resolves the SIGUSR2 capability first: the forced marker is only written
    for a runner that either advertises the capability or cannot be ruled out,
    so the reply never promises a forced kill the on-disk state contradicts.
    """
    from app.pid_manager import check_pidfile, signal_daemon
    from app.restart_manager import force_signal_support, request_restart
    from app.run_log import log

    koan_root = str(ctx.koan_root)
    pid = check_pidfile(ctx.koan_root, "run")
    if not pid:
        # check_pidfile returns None both for "no runner" and for "cannot
        # tell" (unreadable/truncated pidfile while the runner is alive), so
        # keep the forced marker — a live runner still honours it — and log
        # the degraded branch rather than asserting a state we did not verify.
        request_restart(koan_root, force=True)
        log(
            "warning",
            "Force restart: no usable runner PID — falling back to the forced "
            "restart marker",
        )
        return _NO_RUNNER

    support = force_signal_support(koan_root, pid)
    if support == "no":
        # Nothing vouches for this process, so it probably cannot read the
        # force line either. Write a polite request so the marker on disk says
        # what the operator is told.
        request_restart(koan_root, force=False)
        log(
            "warning",
            f"Force restart: nothing vouches for runner PID {pid}'s SIGUSR2 "
            "handler — degrading to a polite restart",
        )
        return _NOT_CAPABLE

    request_restart(koan_root, force=True)
    if support == "unknown":
        log(
            "warning",
            f"Force restart: cannot verify runner PID {pid} advertises "
            "SIGUSR2 — withholding the signal, falling back to the forced "
            "restart marker",
        )
        return _UNVERIFIABLE

    # signal_daemon re-verifies the PID still belongs to run.py before
    # signalling, so a recycled PID is never hit.
    if not signal_daemon(ctx.koan_root, "run", sig_mod.SIGUSR2):
        log(
            "warning",
            "Force restart: SIGUSR2 not delivered to the runner — falling "
            "back to the forced restart marker",
        )
        return _SIGNAL_LOST

    return _FORCED
