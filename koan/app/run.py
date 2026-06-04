"""Kōan — Main agent loop.

Manages the agent loop: mission picking, Claude CLI execution,
post-mission processing, pause/resume, signal handling, and
lifecycle notifications.

Usage:
    python -m app.run              # Normal start
    python -m app.run --restart    # Re-exec after restart signal (exit 42)

Features:
- Double-tap CTRL-C protection across ALL phases (missions, rituals,
  sleep, startup, git sync). First press shows warning with current
  activity name; second press within 10s aborts.
- Automatic exception recovery with backoff (survives crashes)
- protected_phase() context manager for easy phase protection
- Restart wrapper (exit code 42 → re-exec)
- Process group isolation for Claude subprocess (SIGINT ignored)
- Colored log output with TTY detection
"""

import contextlib
import os
import json
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import List, Optional, Tuple

from app.constants import IDLE_LOOP_BREATH_SECONDS
from app.iteration_manager import plan_iteration
from app.loop_manager import check_pending_missions, interruptible_sleep
from app.pid_manager import acquire_pidfile, release_pidfile
from app.restart_manager import check_restart, clear_restart, RESTART_EXIT_CODE
from app.run_log import (  # noqa: F401 — re-exported for backward compat
    _ANSI_RESET,
    _CATEGORY_COLORS,
    _COLORS,
    _init_colors,
    _reset_terminal,
    _styled,
    bold_cyan,
    bold_green,
    log,
    suppress_logged,
)
from app.shutdown_manager import is_shutdown_requested, clear_shutdown
from app.signals import (
    CYCLE_FILE,
    PAUSE_FILE,
    PROJECT_FILE,
    RESTART_FILE,
    SHUTDOWN_FILE,
    ABORT_FILE,
    STATUS_FILE,
    STOP_FILE,
)
from app.subprocess_runner import kill_process_group
from app.utils import atomic_write


# ---------------------------------------------------------------------------
# Recovery configuration
# ---------------------------------------------------------------------------

# Maximum consecutive iteration errors before entering pause mode.
MAX_CONSECUTIVE_ERRORS = 10

# Maximum crashes in main() before giving up.
MAX_MAIN_CRASHES = 5

# Backoff parameters (in seconds).
BACKOFF_MULTIPLIER = 10
MAX_BACKOFF_MAIN = 60
MAX_BACKOFF_ITERATION = 300

# Notification throttling: notify on first error, then every N errors.
ERROR_NOTIFICATION_INTERVAL = 5


# ---------------------------------------------------------------------------
# Recovery helpers
# ---------------------------------------------------------------------------

def _calculate_backoff(attempt: int, max_backoff: int) -> int:
    """Calculate linear backoff capped at max_backoff.

    Returns: attempt * BACKOFF_MULTIPLIER, capped at max_backoff.
    """
    return min(BACKOFF_MULTIPLIER * attempt, max_backoff)


def _should_notify_error(attempt: int) -> bool:
    """Determine if error notification should be sent.

    Notifies on first error and every ERROR_NOTIFICATION_INTERVAL errors.
    """
    return attempt == 1 or attempt % ERROR_NOTIFICATION_INTERVAL == 0


def _provider_identity() -> Tuple[str, str]:
    """Return the active provider name and a human-friendly label.

    Centralizes the ``get_provider_name() + .title()`` lookup so notification
    text and quota/auth handlers stay consistent across mission, skill, and
    contemplative code paths.
    """
    from app.provider import get_provider_name

    name = get_provider_name()
    return name, name.title()


# ---------------------------------------------------------------------------
# Status file
# ---------------------------------------------------------------------------

def set_status(koan_root: str, message: str):
    """Write loop status for /status and dashboard."""
    try:
        atomic_write(Path(koan_root, STATUS_FILE), message)
    except Exception as e:
        log("error", f"Failed to write status: {e}")


def _build_startup_status(koan_root: str) -> str:
    """Build a human-readable status line for startup notification.

    Returns a status string like:
    - "✅ Active — ready to work"
    - "⏸️ Paused (quota) — resets 10am (Europe/Paris). Use /resume to unpause."
    - "⏸️ Paused (max_runs) — use /resume to unpause."
    """
    from app.pause_manager import get_pause_state

    if not Path(koan_root, PAUSE_FILE).exists():
        return "✅ Active — ready to work"

    state = get_pause_state(koan_root)
    if state and state.display:
        return f"⏸️ Paused ({state.reason}) — {state.display}. Use /resume to unpause."
    elif state:
        return f"⏸️ Paused ({state.reason}) — use /resume to unpause."
    else:
        return "⏸️ Paused — use /resume to unpause."


# ---------------------------------------------------------------------------
# Signal handling — double-tap CTRL-C
# ---------------------------------------------------------------------------

class SignalState:
    """Mutable state for SIGINT handler (double-tap pattern)."""
    task_running: bool = False
    first_ctrl_c: float = 0
    claude_proc: Optional[subprocess.Popen] = None
    timeout: int = 10
    phase: str = ""  # Human-readable description of current activity


_sig = SignalState()


class protected_phase:
    """Context manager that activates double-tap CTRL-C protection.

    Usage:
        with protected_phase("Running morning ritual"):
            subprocess.run(...)

    First CTRL-C warns with the phase name.
    Second CTRL-C within timeout raises KeyboardInterrupt.
    """

    def __init__(self, phase_name: str):
        self.phase_name = phase_name
        self.prev_phase = ""
        self.prev_task_running = False

    def __enter__(self):
        self.prev_phase = _sig.phase
        self.prev_task_running = _sig.task_running
        _sig.phase = self.phase_name
        _sig.task_running = True
        _sig.first_ctrl_c = 0
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        _sig.phase = self.prev_phase
        _sig.task_running = self.prev_task_running
        _sig.first_ctrl_c = 0
        return False  # Don't suppress exceptions


def _kill_process_group(proc):
    """Delegate to :func:`app.subprocess_runner.kill_process_group`."""
    kill_process_group(proc)


def _on_sigint(signum, frame):
    """SIGINT handler: first press warns, second press aborts."""
    if not _sig.task_running:
        raise KeyboardInterrupt

    now = time.time()
    if _sig.first_ctrl_c > 0:
        elapsed = now - _sig.first_ctrl_c
        if elapsed <= _sig.timeout:
            # Second CTRL-C within timeout — abort
            print()
            log("koan", "Confirmed. Aborting...")
            _kill_process_group(_sig.claude_proc)
            _sig.first_ctrl_c = 0
            _sig.task_running = False
            raise KeyboardInterrupt

    # First CTRL-C (or timeout expired)
    _sig.first_ctrl_c = now
    print()
    phase_hint = f" ({_sig.phase})" if _sig.phase else ""
    log("koan", f"⚠️  Press CTRL-C again within {_sig.timeout}s to abort.{phase_hint}")


def _on_sigusr1(signum, frame):
    """SIGUSR1 handler: instant /abort from the bridge.

    The /abort skill writes ``.koan-abort`` and sends SIGUSR1 so the runner
    reacts within milliseconds instead of waiting up to ``proc.wait``'s 30 s
    poll cycle. Idempotent: a no-op when no Claude subprocess is running.
    """
    global _last_mission_aborted
    proc = _sig.claude_proc
    if proc is None or proc.poll() is not None:
        return

    _last_mission_aborted = True
    koan_root_path = os.environ.get("KOAN_ROOT", "")
    if koan_root_path:
        Path(koan_root_path, ABORT_FILE).unlink(missing_ok=True)
    log("koan", "Abort signal received — killing current mission")
    _kill_process_group(proc)


def _start_stagnation_monitor(stdout_file: str, proc, project_name: str):
    """Launch a StagnationMonitor for a running Claude subprocess.

    Returns ``None`` when stagnation detection is disabled (via config
    or per-project override) or if any setup error occurs — the monitor
    is strictly a best-effort safety net and must never block mission
    execution.
    """
    try:
        from app.config import get_stagnation_config
        from app.stagnation_monitor import StagnationMonitor
    except Exception as e:
        log("error", f"stagnation monitor import failed: {e}")
        return None

    try:
        cfg = get_stagnation_config(project_name)
    except Exception as e:
        log("error", f"stagnation config error: {e}")
        return None

    if not cfg.get("enabled", True):
        return None

    def _on_warn(count: int) -> None:
        log("koan", f"⚠️  Possible stagnation detected (identical output {count}x)")

    def _on_abort() -> None:
        log("error", "Stagnation confirmed — killing stuck Claude session")
        _kill_process_group(proc)

    try:
        monitor = StagnationMonitor(
            stdout_file=stdout_file,
            on_abort=_on_abort,
            on_warn=_on_warn,
            check_interval_seconds=cfg["check_interval_seconds"],
            abort_after_cycles=cfg["abort_after_cycles"],
            sample_lines=cfg["sample_lines"],
        )
        monitor.start()
        return monitor
    except Exception as e:
        log("error", f"stagnation monitor start failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Claude subprocess execution
# ---------------------------------------------------------------------------

def run_claude_task(
    cmd: list,
    stdout_file: str,
    stderr_file: str,
    cwd: str,
    instance_dir: str = "",
    project_name: str = "",
    run_num: int = 0,
) -> int:
    """Run Claude CLI as a subprocess with SIGINT isolation and timeout.

    The child process ignores SIGINT (via preexec_fn) so the double-tap
    pattern works: first CTRL-C only warns the user, second kills the child.

    A watchdog timer kills the process if it exceeds the configured mission
    timeout (default 3600s). This prevents runaway sessions that block the
    entire agent loop.

    When *instance_dir* and *project_name* are provided and
    ``cli_output_journal`` is enabled, stdout is streamed to the project's
    daily journal file in real-time via a background tail thread.

    Returns the child exit code.
    """
    global _last_mission_timed_out, _last_mission_aborted
    global _stagnation_pattern_type, _stagnation_pattern_excerpt
    _last_mission_timed_out = False
    _last_mission_aborted = False
    _last_mission_stagnated.clear()
    _stagnation_pattern_type = ""
    _stagnation_pattern_excerpt = ""

    _sig.task_running = True
    _sig.first_ctrl_c = 0

    # Start journal streaming if configured
    journal_stream = None
    if instance_dir and project_name:
        from app.cli_journal_streamer import start_journal_stream
        journal_stream = start_journal_stream(
            stdout_file, instance_dir, project_name, run_num,
        )

    from app.cli_exec import popen_cli
    from app.config import get_mission_timeout

    mission_timeout = get_mission_timeout()

    exit_code = 1  # default if subprocess never completes
    try:
        with open(stdout_file, "w") as out_f, open(stderr_file, "w") as err_f:
            proc, cleanup = popen_cli(
                cmd,
                stdout=out_f,
                stderr=err_f,
                cwd=cwd,
                start_new_session=True,
            )
            _sig.claude_proc = proc

            from app.subprocess_runner import ProcessWatchdog

            watchdog = None
            if mission_timeout > 0:
                watchdog = ProcessWatchdog(
                    proc, mission_timeout,
                    on_timeout=lambda: log("error", f"Mission timed out ({mission_timeout}s) — killing process"),
                ).start()

            stagnation_monitor = _start_stagnation_monitor(
                stdout_file, proc, project_name,
            )

            try:
                # Wait for child, handling SIGINT interruptions gracefully.
                # Uses periodic timeout to detect watchdog kills — if
                # _kill_process_group fails silently, proc.wait() would
                # otherwise block forever.
                while True:
                    try:
                        proc.wait(timeout=30)
                        break
                    except subprocess.TimeoutExpired:
                        # Check for abort signal (user sent /abort)
                        koan_root_path = os.environ.get("KOAN_ROOT", "")
                        abort_path = Path(koan_root_path, ABORT_FILE) if koan_root_path else None
                        if abort_path and abort_path.exists():
                            log("koan", "Abort signal detected — aborting current mission")
                            abort_path.unlink(missing_ok=True)
                            _last_mission_aborted = True
                            _kill_process_group(proc)
                            try:
                                proc.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                log("error", f"Process {proc.pid} unkillable after abort — abandoning")
                            break
                        if watchdog and watchdog.fired:
                            # Watchdog already fired but process survived —
                            # make one last kill attempt from the main thread.
                            _kill_process_group(proc)
                            try:
                                proc.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                log("error", f"Process {proc.pid} unkillable — abandoning")
                            break
                    except (KeyboardInterrupt, InterruptedError):
                        # If task_running was cleared by on_sigint (double-tap),
                        # the child was terminated — wait for it to finish
                        if not _sig.task_running:
                            try:
                                proc.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                _kill_process_group(proc)
                            break
                        # Single CTRL-C — keep waiting
                        continue
            finally:
                if watchdog is not None:
                    watchdog.cancel()
                if stagnation_monitor is not None:
                    stagnation_monitor.stop()
                    if stagnation_monitor.stagnated:
                        _last_mission_stagnated.set()
                        _stagnation_pattern_type = stagnation_monitor.pattern_type
                        _stagnation_pattern_excerpt = stagnation_monitor.pattern_excerpt
                cleanup()

        exit_code = proc.returncode
        if _last_mission_aborted:
            exit_code = 1
        elif watchdog and watchdog.fired:
            exit_code = 1
            _last_mission_timed_out = True
        elif _last_mission_stagnated.is_set():
            exit_code = 1
    finally:
        # Always stop journal streaming, even on exception
        if journal_stream:
            from app.cli_journal_streamer import stop_journal_stream
            stop_journal_stream(
                journal_stream, exit_code, stderr_file,
                instance_dir, project_name, run_num,
            )
        # Reset signal state even on exception — otherwise _sig.task_running
        # stays True and CTRL-C requires a double-tap when no subprocess is running.
        _sig.claude_proc = None
        _sig.task_running = False
        _sig.first_ctrl_c = 0

    return exit_code


# ---------------------------------------------------------------------------
# Project configuration
# ---------------------------------------------------------------------------

def parse_projects() -> list:
    """Parse project configuration with validation.

    Delegates to get_known_projects() which checks:
    1. projects.yaml (if exists)
    2. KOAN_PROJECTS env var (fallback)

    Returns list of (name, path) tuples. Exits on error (only if no
    valid projects remain). Missing project directories are warned about
    and filtered out instead of crashing.
    """
    from app.utils import get_known_projects
    projects = get_known_projects()

    if not projects:
        log("error", "No projects configured. Create projects.yaml or set KOAN_PROJECTS env var.")
        sys.exit(1)

    if len(projects) > 50:
        log("error", f"Max 50 projects allowed. You have {len(projects)}.")
        sys.exit(1)

    valid = []
    for name, path in projects:
        if not Path(path).is_dir():
            log("warn", f"Project '{name}' path does not exist: {path} — skipping. "
                f"Remove it from projects.yaml to silence this warning.")
        else:
            valid.append((name, path))

    if not valid:
        log("error", "No valid project directories found. Check your projects.yaml paths.")
        sys.exit(1)

    return valid


# ---------------------------------------------------------------------------
# Startup sequence (delegated to startup_manager.py)
# ---------------------------------------------------------------------------

def run_startup(koan_root: str, instance: str, projects: list):
    """Run all startup tasks (crash recovery, health, sync, etc.).

    Delegates to app.startup_manager which decomposes the startup
    into independently testable steps.
    """
    from app.startup_manager import run_startup as _run_startup
    return _run_startup(koan_root, instance, projects)


# ---------------------------------------------------------------------------
# Notify helper
# ---------------------------------------------------------------------------

def _notify(instance: str, message: str):
    """Send a formatted notification to Telegram."""
    try:
        from app.notify import format_and_send
        format_and_send(message, instance_dir=instance)
    except Exception as e:
        log("error", f"Notification failed: {e}")


def _notify_raw(instance: str, message: str):
    """Send a notification straight to Telegram, skipping the Claude-CLI
    personality reformatter (notify.format_and_send → format_outbox.
    format_message). Use this for terse status updates (startup progress,
    auto-update restarts) where the verbatim text and emoji matter and the
    extra Claude CLI call would defeat the point. send_telegram still
    handles priority filtering, flood protection, and retries.
    """
    try:
        from app.notify import send_telegram
        send_telegram(message)
    except Exception as e:
        log("error", f"Raw notification failed: {e}")


def _notify_mission_end(
    instance: str,
    project_name: str,
    run_num: int,
    max_runs: int,
    exit_code: int,
    mission_title: str = "",
):
    """Send a notification when a mission or autonomous run completes.

    Always sends — both on success and failure — so the human always
    gets a status update. Uses unicode prefix: ✅ for success, ❌ for failure.
    On success, appends a brief journal summary when available.
    """
    if exit_code == 0:
        prefix = "✅"
        label = mission_title if mission_title else "Autonomous run"
        msg = f"{prefix} [{project_name}] Run {run_num}/{max_runs} — {label}"
        # Try to attach a brief summary from the journal
        try:
            from app.mission_summary import get_mission_summary
            summary = get_mission_summary(instance, project_name, max_chars=300)
            if summary:
                msg += f"\n\n{summary}"
        except Exception as e:
            log("error", f"Mission summary extraction failed: {e}")
    else:
        prefix = "❌"
        label = mission_title if mission_title else "Run"
        msg = f"{prefix} [{project_name}] Run {run_num}/{max_runs} — Failed: {label}"
        # Try to attach error context from the journal
        try:
            from app.mission_summary import get_failure_context
            context = get_failure_context(instance, project_name, max_chars=300)
            if context:
                msg += f"\n\n{context}"
        except Exception as e:
            log("error", f"Failure context extraction failed: {e}")

    _notify(instance, msg)


# ---------------------------------------------------------------------------
# Startup delay (#1039)
# ---------------------------------------------------------------------------

DEFAULT_STARTUP_DELAY = 30  # seconds


def _startup_delay(koan_root: str) -> None:
    """Wait before the first iteration so /pause can be processed.

    When ``make start`` launches koan, the first mission can be picked up
    before the Telegram bridge has time to process a /pause command.  This
    interruptible delay (default 30 s, configurable via ``startup_delay``
    in config.yaml) closes the race window.

    The delay is skipped when:
    - The agent is already paused (.koan-pause exists).
    - ``startup_delay`` is set to ``0``.

    The delay is interrupted early if any lifecycle signal appears
    (.koan-pause, .koan-stop, .koan-shutdown, .koan-restart).
    """
    from app.utils import load_config

    delay = load_config().get("startup_delay", DEFAULT_STARTUP_DELAY)
    if delay <= 0:
        return

    # Already paused — skip directly into the main loop's pause handler
    if Path(koan_root, PAUSE_FILE).exists():
        log("koan", "Already paused at startup — skipping startup delay.")
        return

    log(
        "koan",
        f"Startup delay: waiting {delay}s before first mission "
        f"(send /pause now if needed).",
    )

    tick = 2  # check signals every 2 s
    elapsed = 0
    while elapsed < delay:
        time.sleep(min(tick, delay - elapsed))
        elapsed += tick

        # Any lifecycle signal → break out
        for sig in (PAUSE_FILE, STOP_FILE, SHUTDOWN_FILE, RESTART_FILE):
            if Path(koan_root, sig).exists():
                log("koan", f"Signal detected during startup delay ({sig}), proceeding.")
                return

    log("koan", "Startup delay complete — entering main loop.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_current_project(koan_root: str) -> str:
    """Read the current project name from .koan-project, safely.

    Returns the project name or "unknown" if the file cannot be read
    (missing, locked, or corrupt).
    """
    try:
        return Path(koan_root, PROJECT_FILE).read_text().strip() or "unknown"
    except (OSError, ValueError):
        return "unknown"


# ---------------------------------------------------------------------------
# Instance commit helper
# ---------------------------------------------------------------------------

def _commit_instance(instance: str, message: str = ""):
    """Commit instance changes and push.

    Delegates to :func:`app.mission_runner.commit_instance` which is the
    single implementation of the git add / commit / push sequence.
    """
    from app.mission_runner import commit_instance
    commit_instance(instance, message)


# ---------------------------------------------------------------------------
# Update handler (graceful update + restart)
# ---------------------------------------------------------------------------

def _handle_update(koan_root: str, instance: str, count: int):
    """Handle /update: pull upstream updates, then trigger restart.

    Called after the current mission completes. Pulls the latest code
    and requests a restart. If the pull fails, notifies and still restarts
    (the user explicitly asked for an update).
    """
    from app.update_manager import pull_upstream
    from app.restart_manager import request_restart
    from app.pause_manager import remove_pause

    result = pull_upstream(Path(koan_root))
    if not result.success:
        log("koan", f"Update failed: {result.error}")
        _notify(instance, f"🔄 Update failed ({result.error}), restarting anyway.")
    elif result.changed:
        log("koan", f"Update: {result.summary()}")
        _notify(instance, f"🔄 Update complete after {count} runs. {result.summary()} Restarting...")
    else:
        log("koan", "Update: already up to date, restarting.")
        _notify(instance, f"🔄 Update complete after {count} runs. Already up to date. Restarting...")

    remove_pause(koan_root)
    request_restart(koan_root)


# ---------------------------------------------------------------------------
# Pause mode handler
# ---------------------------------------------------------------------------

def handle_pause(
    koan_root: str, instance: str, max_runs: int,
) -> Optional[str]:
    """Handle pause mode. Returns "resume" if resumed, None to stay paused.

    When paused, NO autonomous or contemplative work is performed.
    The agent only checks for resume conditions and sleeps.
    """
    timestamp = time.strftime('%H:%M')
    set_status(koan_root, f"Paused ({timestamp})")
    log("pause", f"Paused. Waiting for resume. ({timestamp})")

    # Check auto-resume
    try:
        from app.pause_manager import check_and_resume
        resume_msg = check_and_resume(koan_root)
        if resume_msg:
            log("pause", f"Auto-resume: {resume_msg}")
            _reset_usage_session(instance)
            _notify(instance, f"🔄 Kōan auto-resumed: {resume_msg}. Starting fresh (0/{max_runs} runs).")
            return "resume"
    except Exception as e:
        log("error", f"Auto-resume check failed: {e}")

    # Manual resume (pause file already removed — /resume handler already
    # resets session counters for quota pauses, but we reset here too as
    # a safety net for any resume path)
    if not Path(koan_root, PAUSE_FILE).exists():
        log("pause", "Manual resume detected")
        _reset_usage_session(instance)
        return "resume"

    # Sleep 5 min in 5s increments — check for resume/stop/restart/shutdown/update
    with protected_phase("Paused — waiting for resume"):
        for _ in range(60):
            if not Path(koan_root, PAUSE_FILE).exists():
                return "resume"
            if Path(koan_root, STOP_FILE).exists():
                log("pause", "Stop signal detected while paused")
                break
            if Path(koan_root, SHUTDOWN_FILE).exists():
                log("pause", "Shutdown signal detected while paused")
                break
            if Path(koan_root, CYCLE_FILE).exists():
                log("pause", "Update signal detected while paused")
                break
            if check_restart(koan_root, target="run"):
                break
            time.sleep(5)

    return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main_loop():
    """The Kōan main loop."""
    _init_colors()

    # Validate environment
    koan_root = os.environ.get("KOAN_ROOT", "")
    if not koan_root:
        log("error", "KOAN_ROOT environment variable not set.")
        sys.exit(1)

    instance = os.path.join(koan_root, "instance")
    if not Path(instance).is_dir():
        log("error", "No instance/ directory found. Run: cp -r instance.example instance")
        sys.exit(1)

    # Run pending data migrations (e.g. French→English header conversion)
    from app.migration_runner import run_pending_migrations
    applied = run_pending_migrations()
    if applied:
        log("init", f"Applied {len(applied)} migration(s)")

    # Set PYTHONPATH
    os.environ["PYTHONPATH"] = os.path.join(koan_root, "koan")

    # Parse projects (projects.yaml > KOAN_PROJECTS)
    projects = parse_projects()

    # Record startup time
    start_time = time.time()

    # Acquire PID (flock-based exclusive lock)
    pidfile_lock = acquire_pidfile(Path(koan_root), "run")

    # Clear stale signal files from a previous session.
    # If `make stop` or `/stop` ran while run.py was NOT running, the signal
    # file persists and would cause an immediate exit on next startup.
    Path(koan_root, STOP_FILE).unlink(missing_ok=True)
    Path(koan_root, SHUTDOWN_FILE).unlink(missing_ok=True)
    Path(koan_root, CYCLE_FILE).unlink(missing_ok=True)
    Path(koan_root, ABORT_FILE).unlink(missing_ok=True)
    clear_restart(koan_root, target="run")

    # Install SIGINT handler
    signal.signal(signal.SIGINT, _on_sigint)

    # Install SIGUSR1 handler — instant /abort from the bridge.
    # Avoids the up-to-30s wait for the ABORT_FILE poll cycle inside
    # run_claude_task(). The file is still written for durability so a
    # missed signal (runner restarting, etc.) is recovered on next poll.
    signal.signal(signal.SIGUSR1, _on_sigusr1)

    # Initialize project state
    if projects:
        atomic_write(Path(koan_root, PROJECT_FILE), projects[0][0])
        os.environ["KOAN_CURRENT_PROJECT"] = projects[0][0]
        os.environ["KOAN_CURRENT_PROJECT_PATH"] = projects[0][1]

    count = 0
    consecutive_errors = 0
    consecutive_idle = 0
    consecutive_nonproductive = 0
    idle_notified = False
    MAX_CONSECUTIVE_IDLE = 30  # ~30 min at 60s interval → auto-pause
    # Throttle kicks in only after several back-to-back non-productive
    # iterations so that one-off dedup skips / transient errors don't eat
    # an extra second each.
    NONPRODUCTIVE_THROTTLE_THRESHOLD = 3
    try:
        # Startup sequence
        max_runs, interval, branch_prefix = run_startup(koan_root, instance, projects)

        # Probe for optional rtk binary (https://github.com/rtk-ai/rtk).
        # When present, the prompt builder injects an awareness section so
        # Claude prefers ``rtk <cmd>`` over the raw command for 60-90 % less
        # tool output.  Detection is cheap, cached, and never mutates state.
        try:
            from app.rtk_detector import detect_rtk
            log("init", detect_rtk().summary_line())
        except Exception as e:
            log("error", f"rtk detection failed: {e}")

        git_sync_interval = int(os.environ.get("KOAN_GIT_SYNC_INTERVAL", "5"))

        # --- Startup delay (#1039) ---
        # Give the user a window to send /pause before the first mission runs.
        # Without this, a mission can be picked up immediately after startup,
        # racing with the Telegram bridge processing of /pause.
        _startup_delay(koan_root)

        while True:
            # --- Stop check ---
            stop_file = Path(koan_root, STOP_FILE)
            if stop_file.exists():
                log("koan", "Stop requested.")
                stop_file.unlink(missing_ok=True)
                current = _read_current_project(koan_root)
                _notify(instance, f"Kōan stopped on request after {count} runs. Last project: {current}.")
                break

            # --- Update check (finish mission → update → restart) ---
            cycle_file = Path(koan_root, CYCLE_FILE)
            if cycle_file.exists():
                log("koan", "Update requested. Updating and restarting...")
                cycle_file.unlink(missing_ok=True)
                _handle_update(koan_root, instance, count)
                sys.exit(RESTART_EXIT_CODE)

            # --- Shutdown check (stops both agent loop and bridge) ---
            if is_shutdown_requested(koan_root, start_time):
                log("koan", "Shutdown requested. Exiting.")
                clear_shutdown(koan_root)
                current = _read_current_project(koan_root)
                _notify(instance, f"Kōan shutdown after {count} runs. Last project: {current}.")
                break

            # --- Restart check ---
            if check_restart(koan_root, since=start_time, target="run"):
                log("koan", "Restart requested. Exiting for re-launch...")
                clear_restart(koan_root, target="run")
                sys.exit(RESTART_EXIT_CODE)

            # --- Pause mode ---
            if Path(koan_root, PAUSE_FILE).exists():
                result = handle_pause(koan_root, instance, max_runs)
                if result == "resume":
                    count = 0
                    consecutive_errors = 0
                    consecutive_idle = 0
                    consecutive_nonproductive = 0
                    idle_notified = False
                    from app.feature_tips import mark_active
                    mark_active()
                    global _startup_notified
                    _startup_notified = False
                continue

            # --- Iteration body (exception-protected) ---
            try:
                productive = _run_iteration(
                    koan_root=koan_root,
                    instance=instance,
                    projects=projects,
                    count=count,
                    max_runs=max_runs,
                    interval=interval,
                    git_sync_interval=git_sync_interval,
                )
                consecutive_errors = 0
                if productive is True:
                    count += 1
                    consecutive_idle = 0
                    consecutive_nonproductive = 0
                    idle_notified = False
                    from app.feature_tips import mark_active
                    mark_active()
                elif productive == "idle":
                    consecutive_idle += 1
                    consecutive_nonproductive = 0
                    if not idle_notified:
                        idle_notified = True
                        try:
                            from app.schedule_manager import is_scheduled_active
                            schedule_active = is_scheduled_active()
                        except (ImportError, Exception):
                            schedule_active = False
                        if schedule_active:
                            _notify(
                                instance,
                                "💤 No work available — but schedule is active, "
                                "staying awake for missions.",
                            )
                        else:
                            _notify(
                                instance,
                                "💤 No work available — waiting for pending reviews "
                                "or new missions. Auto-pause in ~30 min.",
                            )
                    if consecutive_idle >= MAX_CONSECUTIVE_IDLE:
                        # Check if a schedule window is active — if so, the
                        # human configured deep_hours or work_hours and the
                        # agent should stay active, not auto-pause.
                        with suppress_logged(log, "warning", "Schedule active check failed", Exception):
                            from app.schedule_manager import is_scheduled_active
                            if is_scheduled_active():
                                if consecutive_idle == MAX_CONSECUTIVE_IDLE:
                                    log("koan", "Idle timeout reached but schedule is active — staying awake")
                                continue

                        from app.config import get_auto_pause
                        if get_auto_pause():
                            idle_min = consecutive_idle * interval // 60
                            log("koan", f"Idle for {idle_min} min — auto-pausing.")
                            from app.pause_manager import create_pause
                            create_pause(koan_root, "idle_timeout")
                            _notify(
                                instance,
                                f"⏸️ Auto-paused after {idle_min} min idle. "
                                "Use /resume when ready.",
                            )
                        else:
                            consecutive_idle = 0  # Reset so we don't log every iteration
                else:
                    # Non-productive but not idle (error recovery, dedup, etc.)
                    # Don't count toward idle timeout. Throttle only after
                    # several back-to-back occurrences so one-off skips aren't
                    # penalized, but a persistent failure (e.g. dedup skipping
                    # a stuck mission) can't tight-loop and flood Telegram.
                    consecutive_nonproductive += 1
                    if consecutive_nonproductive >= NONPRODUCTIVE_THROTTLE_THRESHOLD:
                        time.sleep(1)
            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except Exception as e:
                consecutive_errors += 1
                _handle_iteration_error(
                    e, consecutive_errors, koan_root, instance,
                )

    except KeyboardInterrupt:
        current = _read_current_project(koan_root)
        _notify(instance, f"Kōan interrupted after {count} runs. Last project: {current}.")
    finally:
        # Fire session_end hook (fire-and-forget, exception-safe)
        try:
            from app.hooks import fire_hook
            fire_hook("session_end", instance_dir=instance, total_runs=count)
        except Exception as e:
            print(f"[hooks] session_end hook error: {e}", file=sys.stderr)
        # Cleanup
        Path(koan_root, STATUS_FILE).unlink(missing_ok=True)
        release_pidfile(pidfile_lock, Path(koan_root), "run")
        log("koan", f"Shutdown. {count} runs executed.")
        _reset_terminal()


# ---------------------------------------------------------------------------
# Iteration helpers (extracted from _run_iteration for readability)
# ---------------------------------------------------------------------------


def _sleep_between_runs(
    koan_root: str,
    instance: str,
    interval: int,
    run_num: int = 0,
    max_runs: int = 0,
    context: str = "",
):
    """Sleep between runs, waking early if new missions arrive.

    Checks for pending missions first — skips sleep entirely if found.
    """
    if check_pending_missions(instance):
        log("koan", "Pending missions found — skipping sleep")
        if run_num:
            set_status(koan_root, f"Run {run_num}/{max_runs} — done, next run starting")
        return

    status_suffix = f" ({time.strftime('%H:%M')})"
    if context:
        set_status(koan_root, f"{context}{status_suffix}")
    else:
        set_status(koan_root, f"Idle — sleeping {interval}s{status_suffix}")
    log("koan", f"Sleeping {interval}s (checking for new missions every 10s)...")
    with protected_phase("Sleeping between runs"):
        wake = interruptible_sleep(interval, koan_root, instance)
    if wake == "mission":
        log("koan", "New mission detected during sleep — waking up early")
        if run_num:
            set_status(koan_root, f"Run {run_num}/{max_runs} — done, new mission detected")


def _next_notification_due_in(
    github_enabled: bool,
    jira_enabled: bool,
) -> int:
    """Return the earliest known notification poll due time."""
    due_times = []
    if github_enabled:
        try:
            from app.loop_manager import get_github_notification_check_due_in
            due = get_github_notification_check_due_in()
            if due > 0:
                due_times.append(due)
        except Exception as e:
            log("warning", f"GitHub notification due-time check failed: {e}")
    if jira_enabled:
        try:
            from app.loop_manager import get_jira_notification_check_due_in
            due = get_jira_notification_check_due_in()
            if due > 0:
                due_times.append(due)
        except Exception as e:
            log("warning", f"Jira notification due-time check failed: {e}")
    return min(due_times) if due_times else 0


def _resolve_idle_wait_interval(
    configured_interval: int,
    github_enabled: bool,
    jira_enabled: bool,
) -> int:
    """Pick a nonzero idle wait without changing normal configured sleeps."""
    try:
        interval = max(0, int(configured_interval))
    except (TypeError, ValueError):
        interval = 0
    if interval > 0:
        return interval

    notification_due = _next_notification_due_in(github_enabled, jira_enabled)
    if notification_due > 0:
        return max(IDLE_LOOP_BREATH_SECONDS, notification_due)
    return IDLE_LOOP_BREATH_SECONDS


def _handle_contemplative(
    plan: dict,
    run_num: int,
    max_runs: int,
    koan_root: str,
    instance: str,
    interval: int,
):
    """Run a contemplative session and sleep afterwards."""
    project_name = plan["project_name"]
    log("pause", "Decision: CONTEMPLATIVE mode (random reflection)")
    print("  Action: Running contemplative session instead of autonomous work")
    print()
    _notify(instance, f"🪷 Run {run_num}/{max_runs} — Contemplative mode on {project_name}")

    log("pause", "Running contemplative session...")
    contemp_start = int(time.time())
    try:
        from app.contemplative_runner import build_contemplative_command
        cmd = build_contemplative_command(
            instance=instance,
            project_name=project_name,
            session_info=f"Run {run_num}/{max_runs} on {project_name}. Mode: {plan['autonomous_mode']}.",
        )
        fd_out, stdout_file = tempfile.mkstemp(prefix="koan-contemp-out-")
        os.close(fd_out)
        fd_err, stderr_file = tempfile.mkstemp(prefix="koan-contemp-err-")
        os.close(fd_err)
        cli_error = None
        try:
            run_claude_task(
                cmd, stdout_file, stderr_file, cwd=koan_root,
                instance_dir=instance, project_name=project_name, run_num=run_num,
            )
        except KeyboardInterrupt:
            raise
        except Exception as e:
            cli_error = traceback.format_exc()
            log("warn", f"Contemplative CLI failed: {e}")
        duration_seconds = int(time.time()) - contemp_start
        # Log contemplative usage before temp files are cleaned up
        try:
            from app.mission_runner import _log_activity_usage
            _log_activity_usage(
                instance, project_name, stdout_file,
                "contemplative", "",
                duration_seconds=duration_seconds,
            )
        except Exception as e:
            log("warn", f"Failed to log contemplative usage: {e}")
        # Record session outcome so contemplative sessions feed into
        # staleness detection, Thompson Sampling, and success-rate metrics.
        try:
            from app.mission_runner import (
                _read_pending_content,
                _read_stdout_summary,
                _record_session_outcome,
            )
            pending_content = _read_pending_content(instance)
            if not pending_content.strip():
                pending_content = _read_stdout_summary(stdout_file)
            _record_session_outcome(
                instance, project_name,
                plan.get("autonomous_mode", "unknown"),
                max(1, duration_seconds // 60),
                pending_content,
                mission_type="contemplative",
            )
        except Exception as e:
            log("warn", f"Failed to record contemplative outcome: {e}")
        _cleanup_temp(stdout_file, stderr_file)
        if cli_error:
            log("error", f"Contemplative error:\n{cli_error}")
    except KeyboardInterrupt:
        raise
    except Exception as e:
        log("error", f"Contemplative error: {e}\n{traceback.format_exc()}")
    log("pause", "Contemplative session ended.")

    # Commit any journal/memory changes from the contemplative session.
    # Without this, writings are lost if the agent crashes before the
    # next successful iteration commits.
    _commit_instance(instance)

    if check_pending_missions(instance):
        log("koan", "Pending missions found after contemplation — skipping sleep")
    else:
        set_status(koan_root, f"Idle — post-contemplation sleep ({time.strftime('%H:%M')})")
        log("pause", f"Contemplative session complete. Sleeping {interval}s...")
        with protected_phase("Sleeping between runs"):
            wake = interruptible_sleep(interval, koan_root, instance)
        if wake == "mission":
            log("koan", "New mission detected during sleep — waking up early")


def _handle_wait_pause(
    plan: dict,
    count: int,
    koan_root: str,
    instance: str,
):
    """Enter pause mode when budget is exhausted (WAIT action)."""
    project_name = plan["project_name"]
    log("quota", "Decision: WAIT mode (budget exhausted)")
    print(f"  Reason: {plan['decision_reason']}")
    print("  Action: Entering pause mode (will auto-resume when quota resets)")
    print()
    try:
        from app.send_retrospective import create_retrospective
        create_retrospective(Path(instance), project_name)
    except Exception as e:
        log("error", f"Retrospective sending failed: {e}\n{traceback.format_exc()}")

    # Commit retrospective before entering pause — otherwise these
    # journal/memory writes are lost if the machine reboots while paused.
    _commit_instance(instance)

    reset_ts, reset_display = _compute_quota_reset_ts(instance)
    from app.pause_manager import create_pause
    create_pause(koan_root, "quota", reset_ts, reset_display)

    quota_details = plan['decision_reason']
    if plan["display_lines"]:
        quota_details += "\n" + "\n".join(plan["display_lines"])

    _notify(instance, (
        f"⏸️ Kōan paused: budget exhausted after {count} runs on [{project_name}].\n"
        f"{quota_details}\n"
        f"Auto-resume when session resets or use /resume."
    ))


def _run_preflight_check(
    plan: dict,
    koan_root: str,
    instance: str,
    count: int,
) -> bool:
    """Run pre-flight quota check before mission/autonomous execution.

    Returns True if quota is exhausted (caller should abort), False to proceed.
    """
    project_path = plan["project_path"]
    project_name = plan["project_name"]
    try:
        from app.preflight import preflight_quota_check
        pf_ok, pf_error = preflight_quota_check(
            project_path=project_path,
            instance_dir=instance,
            project_name=project_name,
        )
        if not pf_ok:
            log("quota", "Pre-flight probe detected quota exhaustion")
            pf_reset_ts, pf_reset_display = _compute_preflight_reset_ts(pf_error)
            from app.pause_manager import create_pause
            create_pause(koan_root, "quota", pf_reset_ts, pf_reset_display)
            label = plan["mission_title"] if plan["mission_title"] else "autonomous run"
            _notify(instance, (
                f"⏸️ Pre-flight quota check failed before [{project_name}] {label}.\n"
                f"Pausing until quota resets. Use /resume to restart manually."
            ))
            return True
    except Exception as e:
        log("error", f"Pre-flight quota check error: {e}")
    return False


def _handle_skill_dispatch(
    mission_title: str,
    project_name: str,
    project_path: str,
    koan_root: str,
    instance: str,
    run_num: int,
    max_runs: int,
    autonomous_mode: str,
    interval: int,
    mission_tier: str = "",
) -> tuple:
    """Try to dispatch a mission as a skill command.

    Returns:
        (handled: bool, mission_title: str) — if handled is True the caller
        should return immediately; if False the caller should proceed to Claude
        using the returned mission_title (which may have been translated by a
        cli_skill mapping).
    """
    from app.debug import debug_log as _debug_log
    preview = f"{mission_title[:100]}..." if len(mission_title) > 100 else mission_title
    _debug_log(f"[run] checking skill dispatch for: {preview}")

    from app.skill_dispatch import dispatch_skill_mission, is_skill_mission
    skill_cmd = dispatch_skill_mission(
        mission_text=mission_title,
        project_name=project_name,
        project_path=project_path,
        koan_root=koan_root,
        instance_dir=instance,
    )
    if skill_cmd:
        _debug_log(f"[run] skill dispatch matched: {' '.join(skill_cmd[:5])}")
        log("mission", "Decision: SKILL DISPATCH (direct runner)")
        print(f"  Mission: {mission_title}")
        print(f"  Project: {project_name}")
        print(f"  Runner: {' '.join(skill_cmd[:4])}...")
        print()
        set_status(koan_root, f"Run {run_num}/{max_runs} — skill dispatch on {project_name}")
        _notify(instance, f"🚀 [{project_name}] Run {run_num}/{max_runs} — Skill: {mission_title}")

        # Create pending.md so /live can show progress during skill dispatch
        from app.loop_manager import create_pending_file
        try:
            create_pending_file(
                instance_dir=instance,
                project_name=project_name,
                run_num=run_num,
                max_runs=max_runs,
                autonomous_mode=autonomous_mode or "implement",
                mission_title=mission_title,
            )
        except Exception as e:
            log("error", f"Failed to create pending.md for skill dispatch: {e}")

        exit_code = 1
        skill_result = {"exit_code": 1, "stdout": "", "stderr": "",
                        "quota_exhausted": False, "quota_info": None}
        # Snapshot core files before skill execution
        from app.core_files import snapshot_core_files, check_core_files, log_integrity_warnings
        skill_core_snapshot = snapshot_core_files(koan_root, project_path)

        try:
            with protected_phase(f"Skill: {mission_title[:50]}"):
                skill_result = _run_skill_mission(
                    skill_cmd=skill_cmd,
                    koan_root=koan_root,
                    instance=instance,
                    project_name=project_name,
                    project_path=project_path,
                    run_num=run_num,
                    mission_title=mission_title,
                    autonomous_mode=autonomous_mode,
                    mission_tier=mission_tier,
                )
            exit_code = skill_result["exit_code"]
            if exit_code == 0:
                log("mission", f"Run {run_num}/{max_runs} — [{project_name}] skill completed")

            # Verify core files survived skill execution
            skill_integrity = check_core_files(koan_root, skill_core_snapshot, project_path)
            if skill_integrity:
                from app.core_files import recover_project_files
                missing = skill_core_snapshot - snapshot_core_files(koan_root, project_path)
                recovered, unrecoverable = recover_project_files(missing, project_path)
                if recovered:
                    log("core_files", f"Auto-recovered {len(recovered)} file(s): {', '.join(recovered)}")
                if unrecoverable:
                    log_integrity_warnings(unrecoverable)
                    log("error", f"Core file integrity check failed after skill: {len(unrecoverable)} file(s) unrecoverable")
                    exit_code = 1
        except KeyboardInterrupt:
            log("error", "Skill dispatch interrupted by user")
            _finalize_mission(instance, mission_title, project_name, 1)
            raise
        except Exception as e:
            log("error", f"Skill dispatch exception: {e}\n{traceback.format_exc()}")
        finally:
            # Clean up temp files created by skill command builders
            from app.skill_dispatch import cleanup_skill_temp_files
            cleanup_skill_temp_files(skill_cmd)

        _skill_provider_name, _skill_provider_label = _provider_identity()
        _skill_stdout = skill_result.get("stdout", "")
        _skill_stderr = skill_result.get("stderr", "")
        _skill_hqe = dict(
            stdout_text=_skill_stdout,
            stderr_text=_skill_stderr,
            exit_code=exit_code,
        )

        # --- Auth / quota classification ---
        # Skill stdout is a summarized agent transcript (DATA): it quotes CI
        # logs, failing tests, and Kōan's own identifiers — e.g. /ci_check
        # always prints ``"quota_exhausted": false``. Scanning it for quota
        # falsely paused the daemon, so classify from stderr only. Genuine
        # skill quota arrives via the structured ``quota_exhausted`` field
        # below, not from the transcript.
        if _classify_and_handle_cli_error(
            exit_code, _skill_stdout, _skill_stderr,
            provider_name=_skill_provider_name,
            provider_label=_skill_provider_label,
            koan_root=koan_root,
            instance=instance,
            project_name=project_name,
            mission_title=mission_title,
            run_num=run_num,
            hqe_kwargs=_skill_hqe,
            trust_stdout=False,
        ):
            return True, mission_title

        # --- Exit-0 quota probe ---
        # For skill dispatches, only check stderr (which IS CLI output).
        # Skill stdout contains summarized runner text where assistant
        # responses can mention "quota" or "hit your limit" and trip
        # false-positive detection.  (Fixes #1618)
        if exit_code == 0 and not skill_result.get("quota_exhausted"):
            _skill_hqe_stderr_only = dict(
                stdout_text="",
                stderr_text=_skill_stderr,
                exit_code=exit_code,
            )
            if _probe_exit0_quota(
                provider_name=_skill_provider_name,
                provider_label=_skill_provider_label,
                koan_root=koan_root,
                instance=instance,
                mission_title=mission_title,
                run_num=run_num,
                hqe_kwargs=_skill_hqe_stderr_only,
                project_name=project_name,
            ):
                return True, mission_title

        # --- Post-mission quota exhaustion (detected during pipeline) ---
        if skill_result.get("quota_exhausted"):
            _handle_pipeline_quota_flag(
                provider_label=_skill_provider_label,
                koan_root=koan_root,
                instance=instance,
                mission_title=mission_title,
                count=run_num,
                quota_info=skill_result.get("quota_info"),
            )
            return True, mission_title

        # Suppress redundant notification when the skill already notified
        # the user directly (e.g. fix_runner sends "⏭ Issue already closed").
        _skill_stdout = skill_result.get("stdout", "")
        _skill_already_notified = (
            exit_code == 0
            and "— skipping" in _skill_stdout
        )
        if not _skill_already_notified:
            _notify_mission_end(
                instance, project_name, run_num, max_runs,
                exit_code, mission_title,
            )
        _finalize_mission(instance, mission_title, project_name, exit_code)
        _commit_instance(instance)

        _sleep_between_runs(koan_root, instance, interval)
        return True, mission_title

    # Check for cli_skill translation before failing unrecognized /commands
    if is_skill_mission(mission_title):
        from pathlib import Path as _Path
        from app.skill_dispatch import (
            translate_cli_skill_mission,
            strip_passthrough_command,
            expand_combo_skill,
        )

        # Combo skills (e.g. /rr) are bridge-side handlers that queue
        # multiple sub-missions. Expand them and mark the original done.
        if expand_combo_skill(mission_title, instance):
            log("mission", "Decision: COMBO EXPAND (sub-missions queued)")
            _notify(instance, f"🔀 [{project_name}] Combo skill expanded into sub-missions")
            _finalize_mission(instance, mission_title, project_name, exit_code=0)
            _commit_instance(instance)
            return True, mission_title

        # Some /commands (e.g. /gh_request) are bridge-side handlers that
        # can also land in the mission queue via GitHub notifications.
        # Strip the prefix and let Claude handle them as regular missions.
        passthrough_text = strip_passthrough_command(mission_title)
        if passthrough_text is not None:
            _debug_log(
                f"[run] passthrough command: '{mission_title}' -> '{passthrough_text}'"
            )
            log("mission", "Decision: PASSTHROUGH (command stripped, sending to Claude)")
            return False, passthrough_text

        translated = translate_cli_skill_mission(
            mission_text=mission_title,
            koan_root=_Path(koan_root),
            instance_dir=_Path(instance),
        )
        if translated is not None:
            _debug_log(
                f"[run] cli_skill translation: '{mission_title[:80]}' -> '{translated[:80]}'"
            )
            log("mission", "Decision: CLI SKILL (provider slash command)")
            # Return untranslated=False so caller falls through to Claude with translated title
            return False, translated

        _debug_log(f"[run] skill mission unhandled, failing: {mission_title[:200]}")

        # Differentiate "unknown command" from "known command, bad arguments"
        from app.skill_dispatch import parse_skill_mission, validate_skill_args
        _, cmd_name, cmd_args = parse_skill_mission(mission_title)
        arg_error = validate_skill_args(cmd_name, cmd_args) if cmd_name else None
        if arg_error:
            log("warning", f"Skill mission invalid args: {arg_error}")
            _notify(instance, f"⚠️ [{project_name}] {arg_error}")
        else:
            log("warning", f"Skill mission has no runner, failing: {mission_title[:80]}")
            _notify(instance, f"⚠️ [{project_name}] Unknown skill command: {mission_title[:80]}")
        _finalize_mission(instance, mission_title, project_name, exit_code=1)
        _commit_instance(instance)
        return True, mission_title

    return False, mission_title


# ---------------------------------------------------------------------------
# Mission retry helpers
# ---------------------------------------------------------------------------

# Maximum retry attempts for mission-level CLI failures.
# Capped at 1 retry (2 total) since missions are expensive.
_MISSION_MAX_RETRIES = 1
_MISSION_RETRY_DELAY = 10  # seconds

# Set by run_claude_task when the watchdog timer kills a runaway session.
# Checked by _maybe_retry_mission to avoid retrying a timeout as if it
# were a transient network error (the retryable-pattern list matches
# "timeout" which would otherwise trigger a second full-length run).
_last_mission_timed_out = False
_last_mission_aborted = False
# Set by run_claude_task when StagnationMonitor aborts the session for
# repeating identical output. Distinguished from a watchdog timeout so the
# operator gets a clear "stuck in a loop" signal in Telegram + missions.md.
# Uses threading.Event for explicit cross-thread signaling between the
# stagnation daemon (writer) and the main loop's _finalize_mission (reader).
_last_mission_stagnated = threading.Event()
# Classification of the stagnation root cause (set alongside the Event).
_stagnation_pattern_type = ""
_stagnation_pattern_excerpt = ""

# Tracks whether the cold-start Telegram burst (GH scan / Jira scan / first
# mission pick) has already fired since process start or /resume. Decoupled
# from the productive-run `count` because idle/passive/quota/sleep-wake paths
# leave `count` at 0, which previously caused the startup trio to re-fire on
# every non-productive wake-up (issue #1193).
_startup_notified = False

# Tracks whether the initial boot burst has already fired in this process.
# Unlike _startup_notified, this is NEVER reset on /resume — it distinguishes
# "first iteration after process launch" from "first iteration after resume".
# Used to silence the "no new missions" / "Notifications clear" variants on
# resume, where those messages are noise (empty state, nothing changed).
# When resume produces new missions, the count-bearing variants still fire.
_boot_notified = False

_warned_missing_projects: set = set()


def _get_git_head(project_path: str) -> str:
    """Get current git HEAD SHA for retry safety check."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_path,
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


def _maybe_retry_mission(
    claude_exit: int,
    stdout_file: str,
    stderr_file: str,
    cmd: list,
    project_path: str,
    pre_head: str,
    instance: str,
    project_name: str,
    run_num: int,
    has_mission: bool,
    provider_name: str = "",
) -> tuple:
    """Attempt a single retry if the CLI error is transient.

    Returns ``(exit_code, stdout_file, stderr_file)`` — the files may
    be replaced if a retry was performed (old files are truncated to
    avoid double-counting output).

    Only retries if:
    - The error is classified as RETRYABLE
    - No commits were produced (HEAD didn't move)
    - This is a mission (not autonomous), since missions are higher-value
    """
    from app.cli_errors import ErrorCategory, classify_cli_error

    # Watchdog timeouts are NOT transient — don't retry a session that ran
    # for the full timeout duration.  Without this guard, "timeout" in the
    # agent's output text (test logs, error messages) would match the
    # RETRYABLE pattern and start another full-length session.
    if _last_mission_timed_out:
        log("koan", "Skipping retry — mission was killed by watchdog timeout")
        return claude_exit, stdout_file, stderr_file

    # User-initiated aborts must not be retried — the user explicitly asked
    # to stop this mission.
    if _last_mission_aborted:
        log("koan", "Skipping retry — mission was aborted by user")
        return claude_exit, stdout_file, stderr_file

    # Stagnated sessions have their own retry logic in _finalize_mission
    # (requeue with counter tracking).  Retrying here would clear the
    # _last_mission_stagnated flag, causing _finalize_mission to miss
    # the stagnation event entirely.
    if _last_mission_stagnated.is_set():
        log("koan", "Skipping retry — mission was killed by stagnation monitor")
        return claude_exit, stdout_file, stderr_file

    # Read output for classification
    try:
        stdout_text = Path(stdout_file).read_text()
    except OSError:
        stdout_text = ""
    try:
        stderr_text = Path(stderr_file).read_text()
    except OSError:
        stderr_text = ""

    category = classify_cli_error(
        claude_exit,
        stdout_text,
        stderr_text,
        provider_name=provider_name,
    )
    log("error", f"CLI error classified as {category.value} (exit={claude_exit})")

    if category != ErrorCategory.RETRYABLE:
        return claude_exit, stdout_file, stderr_file

    if not has_mission:
        log("koan", "Skipping retry for autonomous run (lower priority)")
        return claude_exit, stdout_file, stderr_file

    # Safety: don't retry if Claude already produced commits
    post_head = _get_git_head(project_path)
    if pre_head and post_head and pre_head != post_head:
        log("koan", "Skipping retry — commits were produced before the error")
        return claude_exit, stdout_file, stderr_file

    log("koan", f"Transient CLI error — retrying mission in {_MISSION_RETRY_DELAY}s")
    with protected_phase("Mission retry backoff"):
        time.sleep(_MISSION_RETRY_DELAY)

    # Clear output files before retry to avoid double-counting
    with suppress_logged(log, "debug", "Output file clear before retry failed", OSError):
        open(stdout_file, "w").close()
        open(stderr_file, "w").close()

    retry_exit = run_claude_task(
        cmd, stdout_file, stderr_file, cwd=project_path,
        instance_dir=instance, project_name=project_name, run_num=run_num,
    )
    log("koan", f"Mission retry exit_code={retry_exit}")
    return retry_exit, stdout_file, stderr_file


# ---------------------------------------------------------------------------
# Iteration body (extracted for exception isolation)
# ---------------------------------------------------------------------------

def _run_iteration(
    koan_root: str,
    instance: str,
    projects: list,
    count: int,
    max_runs: int,
    interval: int,
    git_sync_interval: int,
):
    """Execute a single iteration of the main loop.

    Called from main_loop() within a try/except block that catches
    unexpected exceptions without killing the process.

    Returns:
        True if this was a productive iteration (mission, autonomous, or
        contemplative session that consumed API budget).  ``"idle"`` for
        idle wait states (PR limit, schedule, focus, exploration).  False
        for other non-productive iterations (errors, dedup skips,
        preflight failures).  The caller only increments ``count`` on
        productive iterations so that ``max_runs`` reflects actual work
        done, not loop cycles.

    Exceptions:
        KeyboardInterrupt: Propagates to caller (user abort)
        SystemExit: Propagates to caller (restart signal)
        Exception: Caught by caller for recovery
    """
    run_num = count + 1
    set_status(koan_root, f"Run {run_num}/{max_runs} — preparing")

    # Write run-loop heartbeat so external monitors can detect a hung agent
    from app.health_check import write_run_heartbeat
    write_run_heartbeat(koan_root)

    print()
    print(bold_cyan(f"=== Run {run_num}/{max_runs} — {time.strftime('%Y-%m-%d %H:%M:%S')} ==="))

    # Refresh project list (picks up workspace changes since startup)
    from app.utils import get_known_projects
    refreshed = get_known_projects()
    if refreshed:
        # Filter out projects whose directories no longer exist
        global _warned_missing_projects
        valid = []
        for name, path in refreshed:
            if Path(path).is_dir():
                valid.append((name, path))
            elif name not in _warned_missing_projects:
                _warned_missing_projects.add(name)
                log("warn", f"Project '{name}' directory missing: {path} — skipping. "
                    f"Remove it from projects.yaml to silence this warning.")
        if valid:
            projects = valid

    # Per-phase Telegram visibility for the first iteration only. After
    # process start or /resume, count is 0 and the first iteration runs
    # several slow steps (GH cold-start, Jira scan, plan_iteration) that
    # together take ~30-90s before any mission notification fires. Surface
    # progress to Telegram so the human knows what's happening. count>=1
    # iterations stay quiet to avoid steady-state spam.
    global _startup_notified, _boot_notified
    is_first_iteration = not _startup_notified
    is_boot_iteration = not _boot_notified
    _startup_notified = True
    _boot_notified = True

    # Load config once for both GitHub and Jira gating below
    from app.utils import load_config
    from app.github_config import get_github_commands_enabled
    from app.jira_config import get_jira_enabled
    _boot_config = load_config()
    github_enabled = get_github_commands_enabled(_boot_config)
    jira_enabled = get_jira_enabled(_boot_config)

    # Check if /check_notifications was requested — only consume the signal
    # if at least one provider is enabled, otherwise leave it for the next
    # iteration where config may have changed (avoids silently dropping it).
    from app.loop_manager import _consume_check_notifications_signal
    force_notif_check = False
    if github_enabled or jira_enabled:
        force_notif_check = _consume_check_notifications_signal(koan_root)

    # Check GitHub notifications before planning (converts @mentions to missions
    # so plan_iteration() sees them immediately instead of waiting for sleep)
    gh_missions = 0
    if github_enabled:
        if is_first_iteration:
            _notify_raw(instance, "🔍 Scanning GitHub notifications (cold start, may take ~1 min)...")
        from app.loop_manager import (
            process_github_notifications,
            was_github_notification_check_throttled,
        )
        try:
            gh_missions = process_github_notifications(koan_root, instance, force=force_notif_check)
            if gh_missions > 0:
                log("github", f"Pre-iteration: {gh_missions} mission(s) created from GitHub notifications")
            elif not was_github_notification_check_throttled():
                log("koan", "No new GitHub notifications")
        except Exception as e:
            log("error", f"Pre-iteration GitHub notification check failed: {e}")

    # Check Jira notifications before planning (converts @mentions to missions
    # so plan_iteration() sees them immediately instead of waiting for sleep)
    jira_missions = 0
    if jira_enabled:
        # One first-iteration banner that combines the GitHub roll-up (when
        # applicable) with the cold-start latency hint. Avoids the prior
        # double-message ("🔍 Scanning Jira..." immediately followed by
        # "📋 GitHub: ... Scanning Jira...") that said the same thing twice.
        if is_first_iteration:
            cold = " (cold start, may take ~1 min)"
            if github_enabled and gh_missions > 0:
                _notify_raw(instance, f"📋 GitHub: {gh_missions} new mission(s) queued. Scanning Jira{cold}...")
            elif is_boot_iteration and github_enabled:
                _notify_raw(instance, f"📋 GitHub: scanned, no new missions. Scanning Jira{cold}...")
            else:
                # Boot without GitHub, or resume from pause: emit a single
                # cold-start banner so the human sees Jira IS being scanned.
                _notify_raw(instance, f"🔍 Scanning Jira notifications{cold}...")
        from app.loop_manager import (
            process_jira_notifications,
            was_jira_notification_check_throttled,
        )
        try:
            jira_missions = process_jira_notifications(koan_root, instance, force=force_notif_check)
            if jira_missions > 0:
                log("jira", f"Pre-iteration: {jira_missions} mission(s) created from Jira notifications")
            elif not was_jira_notification_check_throttled():
                log("koan", "No new Jira notifications")
        except Exception as e:
            log("error", f"Pre-iteration Jira notification check failed: {e}")

    if is_first_iteration:
        if jira_enabled and jira_missions > 0:
            _notify_raw(instance, f"🎯 Jira: {jira_missions} new mission(s) queued. Picking first mission from queue...")
        elif gh_missions > 0:
            _notify_raw(instance, f"🎯 GitHub: {gh_missions} new mission(s) queued. Picking first mission from queue...")
        elif is_boot_iteration:
            # Empty-state message: only at actual boot. Suppress on resume to
            # avoid spamming the human after every /pause+/resume or auto-resume.
            _notify_raw(instance, "🎯 Notifications clear. Picking first mission from queue...")

    # Startup update hint: surface upstream commits to the user (48 h throttled)
    if is_boot_iteration:
        try:
            from app.update_hint import maybe_send_update_hint
            maybe_send_update_hint(instance, koan_root)
        except Exception as e:
            log("error", f"Update hint check failed: {e}")

    # Plan iteration (delegated to iteration_manager)
    log("koan", "Planning iteration...")
    last_project = _read_current_project(koan_root)
    plan = plan_iteration(
        instance_dir=instance,
        koan_root=koan_root,
        run_num=run_num,
        count=count,
        projects=projects,
        last_project=last_project,
    )

    # --- Iteration decision summary (always visible in logs) ---
    log("koan", f"Iteration plan: action={plan['action']}, "
        f"project={plan['project_name']}, mode={plan['autonomous_mode']}, "
        f"budget={plan['available_pct']}%"
        f"{', mission=' + plan['mission_title'][:60] if plan['mission_title'] else ''}")
    if plan.get("error"):
        log("error", f"Iteration plan error: {plan['error']}")
    if plan.get("tracker_error"):
        log("error", f"Usage tracker broken: {plan['tracker_error']} — hard-capped to review mode")
        _notify(instance, f"⚠️ Budget tracker error: {plan['tracker_error']} — running in review-only mode until fixed")

    # Display usage
    log("quota", "Usage (token estimate — may differ from real API quota):")
    if plan["display_lines"]:
        for line in plan["display_lines"]:
            print(f"  {line}")
    else:
        print("  [No usage data available - using fallback mode]")
    if plan.get("cost_today", 0.0) > 0:
        print(f"  Cost today: ${plan['cost_today']:.2f}")
    print(f"  Safety margin: 10% → Available: {plan['available_pct']}%")
    print()

    # Log recurring injections
    for line in plan.get("recurring_injected", []):
        log("mission", line)

    # --- Handle special actions ---
    action = plan["action"]
    project_name = plan["project_name"]
    project_path = plan["project_path"]

    if action == "error":
        error_msg = plan.get("error", "Unknown error")
        mission_title = plan.get("mission_title", "")
        log("error", error_msg)
        # Move the mission to Failed so it doesn't block the queue.
        # Without this, the same mission gets picked every iteration,
        # causing a retry loop until MAX_CONSECUTIVE_ERRORS triggers pause.
        if mission_title:
            _update_mission_in_file(instance, mission_title, failed=True)
            _notify(instance, f"❌ Mission failed: {error_msg}")
            _commit_instance(instance)
        else:
            _notify(instance, f"⚠️ Iteration error: {error_msg}")
        return False  # error handling — not productive

    if action == "contemplative":
        _handle_contemplative(plan, run_num, max_runs, koan_root, instance, interval)
        return True  # contemplative sessions consume API budget

    # Idle wait actions — all follow the same sleep-and-check pattern
    _IDLE_WAIT_CONFIG = {
        "passive_wait": lambda p: (
            f"Passive mode — read-only, waiting for /active ({p.get('passive_remaining', 'indefinite')})",
            f"👁️ Passive — read-only ({p.get('passive_remaining', 'indefinite')})",
        ),
        "focus_wait": lambda p: (
            f"Focus mode active ({p.get('focus_remaining', 'permanent')}) — waiting for missions",
            f"Focus mode — waiting for missions ({p.get('focus_remaining', 'permanent')})",
        ),
        "schedule_wait": lambda _: (
            "Work hours active — waiting for missions (exploration suppressed)",
            f"Work hours — waiting for missions ({time.strftime('%H:%M')})",
        ),
        "exploration_wait": lambda _: (
            "All projects have exploration disabled — waiting for missions",
            f"Exploration disabled — waiting for missions ({time.strftime('%H:%M')})",
        ),
        "pr_limit_wait": lambda _: (
            "PR limit reached for all projects — waiting for reviews",
            f"PR limit reached — waiting for reviews ({time.strftime('%H:%M')})",
        ),
        "branch_saturated_wait": lambda p: (
            p.get("decision_reason") or "Project branch-saturated — waiting for reviews/merges",
            f"Branch-saturated — waiting ({time.strftime('%H:%M')})",
        ),
    }
    if action in _IDLE_WAIT_CONFIG:
        log_msg, status_msg = _IDLE_WAIT_CONFIG[action](plan)
        log("koan", log_msg)
        set_status(koan_root, status_msg)
        idle_interval = _resolve_idle_wait_interval(
            interval, github_enabled, jira_enabled,
        )
        # branch_saturated_wait: the pending missions ARE the blocker
        # (the picked mission's project is over its PR limit), so waking
        # on pending missions would just tight-loop back into the same
        # blocked state. Wait the full interval for PR count to change.
        # passive_wait: passive mode blocks all execution, so waking on
        # a pending mission tight-loops (logs flood in make logs).
        wake_on_mission = action not in ("branch_saturated_wait", "passive_wait")
        with protected_phase(status_msg):
            wake = interruptible_sleep(
                idle_interval, koan_root, instance,
                wake_on_mission=wake_on_mission,
            )
        if wake == "mission":
            log("koan", f"New mission detected during {action} — waking up")
        # branch_saturated_wait is a human-unblock state (review PRs),
        # not an idle state — don't accumulate toward auto-pause.
        if action == "branch_saturated_wait":
            return False  # blocked on external action — not idle, not productive
        return "idle"  # idle wait — not productive, trackable

    if action == "wait_pause":
        _handle_wait_pause(plan, count, koan_root, instance)
        return False  # budget exhausted — not productive

    # --- Pre-flight quota check ---
    if action in ("mission", "autonomous"):
        log("koan", "Running pre-flight quota check...")
        if _run_preflight_check(plan, koan_root, instance, count):
            return False  # quota exhausted pre-flight — not productive
        log("koan", "Pre-flight OK — quota available")

    # --- Execute mission or autonomous run ---
    mission_title = plan["mission_title"]
    autonomous_mode = plan["autonomous_mode"]
    focus_area = plan["focus_area"]
    available_pct = plan["available_pct"]
    mission_tier = plan.get("mission_tier")  # complexity tier (may be None)

    # --- Dedup guard ---
    if mission_title:
        log("koan", "Checking mission dedup history...")
        try:
            from app.mission_history import should_skip_mission
            if should_skip_mission(instance, mission_title, max_executions=3):
                log("mission", f"Skipping repeated mission (3+ attempts): {mission_title[:60]}")
                moved = _update_mission_in_file(
                    instance, mission_title, failed=True,
                    cause_tag="repeated-3x",
                )
                if moved:
                    _notify(instance, f"⚠️ Mission ran 3+ times without clearing, moved to Failed: {mission_title[:60]}")
                else:
                    # The mission could not be matched in missions.md, so it
                    # stays in Pending and would be re-picked forever. Surface
                    # this loudly — a silent retry loop is the worst outcome.
                    log("error", f"Repeated mission could not be removed from queue: {mission_title[:80]}")
                    _notify(instance, (
                        f"🛑 Mission ran 3+ times but could NOT be removed from the queue "
                        f"(text mismatch in missions.md). It will keep being retried until you "
                        f"edit/cancel it manually: {mission_title[:80]}"
                    ))
                _commit_instance(instance)
                return False  # dedup skip — not productive
        except Exception as e:
            log("warning", f"Dedup guard error (proceeding anyway): {e}")
            # Don't skip — running a mission once extra is cheaper than
            # silently dropping it every iteration.

    # Set project state
    atomic_write(Path(koan_root, PROJECT_FILE), project_name)
    os.environ["KOAN_CURRENT_PROJECT"] = project_name
    os.environ["KOAN_CURRENT_PROJECT_PATH"] = project_path

    print(bold_green(f">>> Current project: {project_name}") + f" ({project_path})")
    print()

    # --- Prepare project git state ---
    from app.git_prep import prepare_project_branch
    try:
        prep = prepare_project_branch(project_path, project_name, koan_root)
        if prep.stashed:
            log("git", f"Stashed uncommitted changes in {project_name}")
        if not prep.success:
            log("error", f"Git prep failed for {project_name}: {prep.error}")
            if mission_title:
                _update_mission_in_file(instance, mission_title, failed=True)
                _notify(instance, f"❌ [{project_name}] Git prep failed, aborting mission: {mission_title[:60]}")
            return False  # abort — branch state is unreliable
        else:
            log("git", f"Ready on {prep.base_branch} from {prep.remote_used}")
    except Exception as e:
        log("error", f"Git prep error for {project_name}: {e}\n{traceback.format_exc()}")
        if mission_title:
            _update_mission_in_file(instance, mission_title, failed=True)
            _notify(instance, f"❌ [{project_name}] Git prep error, aborting mission: {mission_title[:60]}")
        return False  # abort — branch state is unreliable

    # --- Mark mission as In Progress ---
    # Save the original title before skill dispatch may translate it.
    # _finalize_mission must use the original title because that's the
    # needle recorded in missions.md "In Progress" section.
    original_mission_title = mission_title
    if mission_title:
        _start_mission_in_file(instance, mission_title)

    # --- Create structured checkpoint for recovery ---
    if mission_title:
        try:
            from app.checkpoint_manager import create_checkpoint
            create_checkpoint(instance, mission_title, project_name, run_num)
        except Exception as e:
            log("error", f"Checkpoint creation failed (non-blocking): {e}")

    # --- Check for skill-dispatched mission ---
    if mission_title:
        handled, mission_title = _handle_skill_dispatch(
            mission_title, project_name, project_path, koan_root,
            instance, run_num, max_runs, autonomous_mode, interval,
            mission_tier=mission_tier or "",
        )
        if handled:
            return True  # skill dispatch — productive

    # Lifecycle notification
    if mission_title:
        log("mission", "Decision: MISSION mode (assigned)")
        print(f"  Mission: {mission_title}")
        print(f"  Project: {project_name}")
        print()
        _notify(instance, f"🚀 [{project_name}] Run {run_num}/{max_runs} — Starting: {mission_title}")
    else:
        mode_upper = autonomous_mode.upper()
        log("mission", f"Decision: {mode_upper} mode (estimated cost: 5.0% session)")
        print(f"  Reason: {plan['decision_reason']}")
        print(f"  Project: {project_name}")
        print(f"  Focus: {focus_area}")
        print()
        _notify(instance, f"🚀 [{project_name}] Run {run_num}/{max_runs} — Autonomous: {autonomous_mode} mode")

    # --- Fire pre-mission hook ---
    try:
        from app.hooks import fire_hook
        fire_hook(
            "pre_mission",
            instance_dir=instance,
            project_name=project_name,
            project_path=project_path,
            mission_title=mission_title,
            autonomous_mode=autonomous_mode,
            run_num=run_num,
        )
    except Exception as e:
        print(f"[hooks] pre_mission hook error: {e}", file=sys.stderr)

    # --- Generate mission spec for complex missions ---
    spec_content = ""
    if mission_title and autonomous_mode not in ("review", "wait"):
        try:
            from app.mission_complexity import is_complex_mission
            if is_complex_mission(mission_title):
                log("spec", "Complex mission detected — generating spec")
                from app.spec_generator import generate_spec, save_spec
                spec_content = generate_spec(project_path, mission_title, instance) or ""
                if spec_content:
                    spec_path = save_spec(instance, mission_title, spec_content)
                    if spec_path:
                        log("spec", f"Spec saved to {spec_path}")
                    else:
                        log("spec", "Spec generated but save failed")
                else:
                    log("spec", "Spec generation returned empty — proceeding without spec")
        except Exception as e:
            log("error", f"Spec generation error (non-blocking): {e}")

    # Build prompt (split into system/user for prompt caching)
    from app.prompt_builder import build_agent_prompt_parts
    system_prompt, prompt = build_agent_prompt_parts(
        instance=instance,
        project_name=project_name,
        project_path=project_path,
        run_num=run_num,
        max_runs=max_runs,
        autonomous_mode=autonomous_mode or "implement",
        focus_area=focus_area or "General autonomous work",
        available_pct=available_pct or 50,
        mission_title=mission_title,
        spec_content=spec_content,
    )

    # Create pending.md
    from app.loop_manager import create_pending_file
    try:
        create_pending_file(
            instance_dir=instance,
            project_name=project_name,
            run_num=run_num,
            max_runs=max_runs,
            autonomous_mode=autonomous_mode or "implement",
            mission_title=mission_title,
        )
    except Exception as e:
        log("error", f"Failed to create pending.md: {e}")

    # Execute Claude
    log("koan", "Building CLI command and launching provider...")
    if mission_title:
        set_status(koan_root, f"Run {run_num}/{max_runs} — executing mission on {project_name}")
    else:
        set_status(koan_root, f"Run {run_num}/{max_runs} — {autonomous_mode.upper()} on {project_name}")

    mission_start = int(time.time())
    fd_out, stdout_file = tempfile.mkstemp(prefix="koan-out-")
    os.close(fd_out)
    fd_err, stderr_file = tempfile.mkstemp(prefix="koan-err-")
    os.close(fd_err)
    claude_exit = 1  # default to failure; overwritten on successful execution
    provider_name = ""
    provider_label = "Provider"
    plugin_dir = None  # generated plugin dir for Skill tool (cleaned up in finally)
    cmd_cleanup_paths: List[str] = []  # temp files created by build_mission_command
    try:
        provider_name, provider_label = _provider_identity()
        # Build CLI command (provider-agnostic with per-project overrides)
        from app.mission_runner import build_mission_command
        from app.debug import debug_log as _debug_log
        if provider_name == "codex":
            try:
                from app.config import get_skip_permissions
                _codex_full_access = get_skip_permissions()
            except Exception as e:
                _codex_full_access = False
                _debug_log(f"[run] codex skip_permissions check failed: {e}")
            _mission_mode = (autonomous_mode or "implement").lower()
            if not _codex_full_access and _mission_mode in {"implement", "deep"}:
                log(
                    "warning",
                    "Codex workspace-write sandbox may make .git read-only; "
                    "branch, commit, push, and PR creation can fail. "
                    "Set skip_permissions: true when Koan runs in a trusted "
                    "external sandbox and Codex should use git directly.",
                )

        # Generate plugin directory so Claude CLI can discover Kōan skills
        plugin_dirs = None
        try:
            from app.plugin_generator import generate_plugin_dir, cleanup_plugin_dir
            from app.skills import build_registry
            extra_dirs = []
            # Include project-local skills (<project>/.claude/skills/)
            project_skills = Path(project_path) / ".claude" / "skills"
            if project_skills.is_dir():
                extra_dirs.append(project_skills)
            instance_skills = Path(instance) / "skills"
            if instance_skills.is_dir():
                extra_dirs.append(instance_skills)
            # Include user-installed Claude Code skills (~/.claude/skills/)
            user_skills = Path.home() / ".claude" / "skills"
            if user_skills.is_dir():
                extra_dirs.append(user_skills)
            registry = build_registry(extra_dirs=extra_dirs or None)
            if registry.list_by_audience("agent", "command", "hybrid"):
                plugin_dir = generate_plugin_dir(registry)
                plugin_dirs = [str(plugin_dir)]
        except Exception as e:
            _debug_log(f"[run] plugin dir generation skipped: {e}")

        cmd, cmd_cleanup_paths = build_mission_command(
            prompt=prompt,
            autonomous_mode=autonomous_mode,
            extra_flags="",
            project_name=project_name,
            plugin_dirs=plugin_dirs,
            system_prompt=system_prompt,
            tier=mission_tier,
        )

        cmd_display = [c[:100] + '...' if len(c) > 100 else c for c in cmd[:6]]
        _debug_log(f"[run] cli: cmd={' '.join(cmd_display)}... cwd={project_path}")

        # Capture git HEAD before execution for retry safety check
        pre_head = _get_git_head(project_path)

        # Snapshot core files before execution for integrity check
        from app.core_files import snapshot_core_files, check_core_files, log_integrity_warnings
        core_snapshot = snapshot_core_files(koan_root, project_path)

        claude_exit = run_claude_task(
            cmd, stdout_file, stderr_file, cwd=project_path,
            instance_dir=instance, project_name=project_name, run_num=run_num,
        )
        _debug_log(f"[run] cli: exit_code={claude_exit}")
        elapsed_min = (int(time.time()) - mission_start) / 60
        log("koan", f"{provider_label} CLI finished (exit={claude_exit}, {elapsed_min:.1f}min)")

        # --- Mission retry on transient CLI errors ---
        # One retry for missions, zero for autonomous (they're lower-priority).
        # Only retry if HEAD didn't move (no commits produced).
        if claude_exit != 0:
            claude_exit, stdout_file, stderr_file = _maybe_retry_mission(
                claude_exit=claude_exit,
                stdout_file=stdout_file,
                stderr_file=stderr_file,
                cmd=cmd,
                project_path=project_path,
                pre_head=pre_head,
                instance=instance,
                project_name=project_name,
                run_num=run_num,
                has_mission=bool(mission_title),
                provider_name=provider_name,
            )

        # --- JSON success override ---
        # Claude CLI can return non-zero even when the session JSON shows
        # success (is_error=false).  Override the exit code so the
        # post-mission pipeline (verification, reflection, auto-merge)
        # is not skipped and the notification shows ✅ instead of ❌.
        # NEVER override after a watchdog kill or user abort — partial
        # JSON output from a killed process is not trustworthy (#1254).
        if claude_exit != 0 and not _last_mission_timed_out and not _last_mission_aborted:
            from app.mission_runner import check_json_success
            if check_json_success(stdout_file):
                log("koan", f"CLI exited {claude_exit} but JSON output indicates success — overriding to 0")
                claude_exit = 0

        # Verify core files survived the mission (after retry, so result is final)
        log("koan", "Running core file integrity check...")
        integrity_warnings = check_core_files(koan_root, core_snapshot, project_path)
        if integrity_warnings:
            from app.core_files import recover_project_files
            missing = core_snapshot - snapshot_core_files(koan_root, project_path)
            recovered, unrecoverable = recover_project_files(missing, project_path)
            if recovered:
                log("core_files", f"Auto-recovered {len(recovered)} file(s): {', '.join(recovered)}")
            if unrecoverable:
                log_integrity_warnings(unrecoverable)
                log("error", f"Core file integrity check failed: {len(unrecoverable)} file(s) unrecoverable")
                claude_exit = 1

        # Parse and display output
        try:
            from app.mission_runner import parse_claude_output
            with open(stdout_file) as f:
                raw = f.read()
            text = parse_claude_output(raw)
            print(text)
        except Exception as e:
            try:
                with open(stdout_file) as f:
                    print(f.read())
            except Exception as e2:
                log("error", f"Failed to read CLI output: {e}, {e2}")
        _reset_terminal()

        # --- Update checkpoint with branch/progress as early as possible ---
        # Done before auth/quota checks so progress is captured even on early returns.
        if original_mission_title:
            try:
                from app.checkpoint_manager import (
                    update_checkpoint, update_from_pending, update_from_stdout,
                )
                from app.git_sync import run_git as _cp_run_git
                _cp_branch = _cp_run_git(project_path, "rev-parse", "--abbrev-ref", "HEAD")
                if _cp_branch:
                    update_checkpoint(instance, original_mission_title, branch=_cp_branch)
                update_from_pending(instance, original_mission_title)
                with suppress_logged(log, "warning", "Checkpoint stdout read failed", OSError):
                    _cp_stdout = Path(stdout_file).read_text(errors="replace")
                    update_from_stdout(instance, original_mission_title, _cp_stdout)
            except Exception as e:
                log("error", f"Checkpoint update failed (non-blocking): {e}")

        # --- Auth / Quota error detection (before finalizing mission) ---
        if claude_exit != 0 and original_mission_title:
            try:
                _cli_stdout = Path(stdout_file).read_text()
            except OSError:
                _cli_stdout = ""
            try:
                _cli_stderr = Path(stderr_file).read_text()
            except OSError:
                _cli_stderr = ""
            if _classify_and_handle_cli_error(
                claude_exit, _cli_stdout, _cli_stderr,
                provider_name=provider_name,
                provider_label=provider_label,
                koan_root=koan_root,
                instance=instance,
                project_name=project_name,
                mission_title=original_mission_title,
                run_num=run_num,
                hqe_kwargs=dict(
                    stdout_file=stdout_file,
                    stderr_file=stderr_file,
                    exit_code=claude_exit,
                ),
            ):
                return True

        # Exit-0 quota probe — check all CLI outcomes before finalization.
        if original_mission_title:
            _exit0_hqe = dict(
                stdout_file=stdout_file,
                stderr_file=stderr_file,
                exit_code=claude_exit,
            )
            if _probe_exit0_quota(
                provider_name=provider_name,
                provider_label=provider_label,
                koan_root=koan_root,
                instance=instance,
                mission_title=original_mission_title,
                run_num=run_num,
                hqe_kwargs=_exit0_hqe,
                project_name=project_name,
            ):
                return True

        # If mission was aborted, notify and skip heavy post-mission pipeline
        if _last_mission_aborted and original_mission_title:
            _finalize_mission(instance, original_mission_title, project_name, claude_exit)
            try:
                from app.checkpoint_manager import delete_checkpoint
                delete_checkpoint(instance, original_mission_title)
            except Exception as e:
                log("error", f"Checkpoint cleanup failed (non-blocking): {e}")
            log("koan", f"Mission aborted: {original_mission_title[:60]}")
            _notify(instance, f"⏭️ [{project_name}] Mission aborted: {original_mission_title[:60]}")
            return True  # count as productive so loop continues immediately

        # Post-mission pipeline
        log("koan", "Starting post-mission pipeline...")
        _status_prefix = f"Run {run_num}/{max_runs}"
        set_status(koan_root, f"{_status_prefix} — finalizing")
        try:
            from app.mission_runner import run_post_mission
            post_result = run_post_mission(
                instance_dir=instance,
                project_name=project_name,
                project_path=project_path,
                run_num=run_num,
                exit_code=claude_exit,
                stdout_file=stdout_file,
                stderr_file=stderr_file,
                mission_title=mission_title,
                autonomous_mode=autonomous_mode or "implement",
                start_time=mission_start,
                status_callback=lambda step: set_status(
                    koan_root, f"{_status_prefix} — {step}"
                ),
                mission_tier=mission_tier,
                provider_name=provider_name,
            )

            if post_result.get("pending_archived"):
                log("health", f"pending.md archived to journal ({provider_label} didn't clean up)")
            if post_result.get("auto_merge_branch"):
                log("git", f"Auto-merge checked for {post_result['auto_merge_branch']}")

            if post_result.get("quota_exhausted"):
                _handle_pipeline_quota_flag(
                    provider_label=provider_label,
                    koan_root=koan_root,
                    instance=instance,
                    mission_title=original_mission_title,
                    count=count,
                    quota_info=post_result.get("quota_info"),
                )
                return True  # ran Claude before quota hit — productive
        except Exception as e:
            log("error", f"Post-mission processing error: {e}\n{traceback.format_exc()}")

        # Complete/fail mission in missions.md after quota handling has had a
        # chance to requeue transient quota failures.
        if original_mission_title:
            _finalize_mission(instance, original_mission_title, project_name, claude_exit)

        # --- Clean up checkpoint after mission finalization ---
        # Delete on both success and failure to prevent orphaned checkpoint files.
        # Recovery only matters for in-progress missions (crash); once finalized,
        # the checkpoint is no longer needed.
        if original_mission_title:
            try:
                from app.checkpoint_manager import delete_checkpoint
                delete_checkpoint(instance, original_mission_title)
            except Exception as e:
                log("error", f"Checkpoint cleanup failed (non-blocking): {e}")
    finally:
        _cleanup_temp(stdout_file, stderr_file)
        if cmd_cleanup_paths:
            try:
                from app.provider import cleanup_managed_paths
                cleanup_managed_paths(cmd_cleanup_paths)
            except Exception as e:
                print(f"[run] sysprompt cleanup error: {e}", file=sys.stderr)
        if plugin_dir:
            try:
                from app.plugin_generator import cleanup_plugin_dir
                cleanup_plugin_dir(plugin_dir)
            except Exception as e:
                print(f"[run] plugin cleanup error: {e}", file=sys.stderr)

    # Report result — always notify on completion (success or failure)
    if claude_exit == 0:
        log("mission", f"Run {run_num}/{max_runs} — [{project_name}] completed successfully")
    _notify_mission_end(
        instance, project_name, run_num, max_runs,
        claude_exit, mission_title,
    )

    # Commit instance
    _commit_instance(instance)

    # Periodic git sync
    if (count + 1) % git_sync_interval == 0:
        with protected_phase("Git sync"):
            log("git", f"Periodic git sync (run {count + 1})...")
            from app.git_sync import GitSync
            for name, path in projects:
                try:
                    gs = GitSync(instance, name, path)
                    gs.sync_and_report()
                except Exception as e:
                    log("error", f"Periodic git sync failed for {name}: {e}")

    # Periodic auto-update check
    try:
        from app.auto_update import is_auto_update_enabled, get_check_interval
        if is_auto_update_enabled() and (count + 1) % get_check_interval() == 0:
            from app.auto_update import perform_auto_update
            updated = perform_auto_update(koan_root, instance)
            if updated:
                log("update", "Auto-update triggered restart.")
                sys.exit(RESTART_EXIT_CODE)
    except Exception as e:
        log("error", f"Periodic auto-update check failed: {e}")

    # Max runs check
    if count + 1 >= max_runs:
        from app.config import get_auto_pause
        if get_auto_pause():
            log("koan", f"Max runs ({max_runs}) reached. Running evening ritual before pause.")
            with protected_phase("Evening ritual"):
                try:
                    from app.rituals import run_ritual
                    run_ritual("evening", Path(instance))
                except Exception as e:
                    log("error", f"Evening ritual failed: {e}")
            log("pause", "Entering pause mode (auto-resume in 5h).")
            from app.pause_manager import create_pause
            create_pause(koan_root, "max_runs")
            _notify(instance, (
                f"⏸️ Kōan paused: {max_runs} runs completed. "
                "Auto-resume in 5h or use /resume to restart."
            ))
            return True  # completed final productive run
        else:
            log("koan", f"Max runs ({max_runs}) reached but auto_pause disabled — continuing.")

    # Sleep between runs (skip if pending missions)
    _sleep_between_runs(koan_root, instance, interval, run_num, max_runs)

    return True  # productive iteration completed


# ---------------------------------------------------------------------------
# Error recovery
# ---------------------------------------------------------------------------

def _handle_iteration_error(
    error: Exception,
    consecutive_errors: int,
    koan_root: str,
    instance: str,
):
    """Handle an exception from _run_iteration.

    Logs the error, backs off with increasing sleep, and enters
    pause mode after MAX_CONSECUTIVE_ERRORS to avoid thrashing.
    """
    tb = traceback.format_exc()
    log("error", f"Iteration failed ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}): {error}")
    log("error", f"Traceback:\n{tb}")
    set_status(koan_root, f"Error recovery ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS})")

    # Notify on first error and periodically
    if _should_notify_error(consecutive_errors):
        _notify(instance, (
            f"⚠️ Run loop error ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}): "
            f"{type(error).__name__}: {error}"
        ))

    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
        log("error", f"Too many consecutive errors ({consecutive_errors}). Entering pause mode.")
        _notify(instance, (
            f"🛑 Kōan entering pause mode after {consecutive_errors} consecutive errors.\n"
            f"Last error: {type(error).__name__}: {error}\n"
            f"Use /resume to restart."
        ))
        from app.pause_manager import create_pause
        create_pause(koan_root, "errors")
        return

    # Backoff with increasing delay
    backoff = _calculate_backoff(consecutive_errors, MAX_BACKOFF_ITERATION)
    log("koan", f"Recovering in {backoff}s...")
    time.sleep(backoff)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_quota_reset_ts(instance: str):
    """Compute quota reset timestamp and display string.

    Returns (reset_ts: int, reset_display: str). Delegates the buffer
    math (QUOTA_RESET_BUFFER_SECONDS) to
    :func:`app.quota_handler.compute_resume_info` so the buffer policy
    lives in exactly one place. Falls back to QUOTA_RETRY_SECONDS from
    now if estimation fails.
    """
    reset_ts = None
    reset_display = ""
    try:
        from app.usage_estimator import cmd_reset_time, _estimate_reset_time, _load_state
        from app.quota_handler import compute_resume_info
        usage_state_path = Path(instance, "usage_state.json")
        raw_reset_ts = cmd_reset_time(usage_state_path)
        state = _load_state(usage_state_path)
        reset_display = f"session reset in ~{_estimate_reset_time(state.get('session_start', ''), 5)}"
        if raw_reset_ts is not None:
            # compute_resume_info applies the canonical buffer; we keep the
            # estimator-derived display string instead of its resume message.
            reset_ts, _ = compute_resume_info(raw_reset_ts, reset_display)
    except Exception as e:
        log("error", f"Reset time estimation failed: {e}")
    if reset_ts is None:
        from app.pause_manager import QUOTA_RETRY_SECONDS
        reset_ts = int(time.time()) + QUOTA_RETRY_SECONDS
    return reset_ts, reset_display


def _compute_preflight_reset_ts(error_output: str):
    """Compute quota reset timestamp from preflight probe error output.

    Returns (reset_ts: int, reset_display: str). Adds the quota reset buffer
    to known reset times and falls back to QUOTA_RETRY_SECONDS from now if
    extraction fails.
    """
    reset_ts = None
    reset_display = ""
    try:
        from app.quota_handler import extract_reset_info, parse_reset_time, compute_resume_info
        reset_info = extract_reset_info(error_output or "")
        reset_ts, reset_display = parse_reset_time(reset_info)
        reset_ts, _ = compute_resume_info(reset_ts, reset_display)
    except Exception as e:
        log("error", f"Pre-flight reset time extraction failed: {e}")
    if reset_ts is None:
        from app.pause_manager import QUOTA_RETRY_SECONDS
        reset_ts = int(time.time()) + QUOTA_RETRY_SECONDS
    return reset_ts, reset_display


# ---------------------------------------------------------------------------
# Shared quota / auth error handling
# ---------------------------------------------------------------------------
# run.py had 3 nearly-identical code paths for auth/quota errors:
#   1. skill dispatch CLI error   (_handle_skill_dispatch)
#   2. regular mission CLI error  (_run_iteration)
#   3. exit-0 quota probe         (both paths)
# Factoring the shared logic here eliminates the synchronization burden.


def _handle_auth_error(
    *,
    provider_label: str,
    koan_root: str,
    instance: str,
    mission_title: str,
) -> None:
    """Requeue mission, enter auth pause, and notify on auth failure."""
    log("error", f"{provider_label} is logged out — requeueing mission to Pending")
    _requeue_mission_in_file(instance, mission_title)
    from app.pause_manager import create_pause
    create_pause(koan_root, "auth")
    _notify(instance, (
        f"🔐 {provider_label} is logged out. Please re-authenticate the provider CLI.\n\n"
        "The current mission has been moved back to Pending. "
        "Use /resume after logging in."
    ))


def _handle_quota_error(
    *,
    provider_name: str,
    provider_label: str,
    koan_root: str,
    instance: str,
    project_name: str,
    mission_title: str,
    run_num: int,
    hqe_kwargs: dict,
) -> None:
    """Requeue mission, detect reset time, pause, and notify on quota exhaustion.

    *hqe_kwargs* are forwarded to :func:`handle_quota_exhaustion` — callers
    pass either ``stdout_text``/``stderr_text`` (skill path) or
    ``stdout_file``/``stderr_file`` (regular mission path).
    """
    log("quota", "API quota exhausted — requeueing mission to Pending")
    _requeue_mission_in_file(instance, mission_title)
    from app.quota_handler import handle_quota_exhaustion, QUOTA_CHECK_UNRELIABLE
    quota_result = handle_quota_exhaustion(
        koan_root=koan_root,
        instance_dir=instance,
        project_name=project_name,
        run_count=run_num,
        provider_name=provider_name,
        **hqe_kwargs,
    )
    reset_display = ""
    if quota_result and quota_result is not QUOTA_CHECK_UNRELIABLE:
        reset_display = quota_result[0]
    else:
        reset_ts, reset_display = _compute_quota_reset_ts(instance)
        from app.pause_manager import create_pause
        create_pause(koan_root, "quota", reset_ts, reset_display)
    _notify(instance, (
        f"⏸️ API quota exhausted.{(' ' + reset_display) if reset_display else ''}\n"
        f"Mission '{mission_title[:60]}' moved back to Pending.\n"
        f"Use /resume after quota resets."
    ))


def _classify_and_handle_cli_error(
    exit_code: int,
    stdout_text: str,
    stderr_text: str,
    *,
    provider_name: str,
    provider_label: str,
    koan_root: str,
    instance: str,
    project_name: str,
    mission_title: str,
    run_num: int,
    hqe_kwargs: dict,
    trust_stdout: bool = True,
) -> bool:
    """Classify a non-zero CLI exit and handle AUTH / QUOTA errors.

    Shared by both the skill dispatch and regular mission paths.

    Args:
        exit_code: CLI process exit code.
        stdout_text / stderr_text: CLI output for error classification.
        hqe_kwargs: Forwarded to :func:`handle_quota_exhaustion` (text or file).
        trust_stdout: When False, stdout is treated as DATA and excluded from
            classification — only stderr (the trusted CLI channel) is scanned.
            Skill dispatches set this: their stdout is a summarized agent
            transcript that legitimately quotes CI logs and source identifiers
            (e.g. ``/ci_check`` always prints ``"quota_exhausted": false``),
            which otherwise tripped a false QUOTA classification and paused the
            daemon. Genuine skill quota propagates via the structured
            ``quota_exhausted`` result field, not via transcript scanning.

    Returns:
        True if an auth/quota error was handled (caller should return True).
    """
    if exit_code == 0:
        return False

    from app.cli_errors import ErrorCategory, classify_cli_error
    category = classify_cli_error(
        exit_code,
        stdout_text if trust_stdout else "",
        stderr_text,
        provider_name=provider_name,
    )
    # When stdout is DATA (a skill's summarized agent transcript) the broad
    # human-prose quota patterns are excluded above — they match content the
    # transcript merely quotes (CI logs, Kōan's own ``quota_exhausted`` field).
    # The CLI *runtime's* own signals, however, are safe to honor even in a
    # transcript: the "hit your session limit" abort line and a rejected
    # ``rate_limit_event`` are emitted by the runtime, not quotable prose.
    if category != ErrorCategory.QUOTA and not trust_stdout:
        from app.quota_handler import cli_runtime_quota_signal

        if cli_runtime_quota_signal(stdout_text):
            category = ErrorCategory.QUOTA
    if category != ErrorCategory.AUTH and not trust_stdout:
        if _cli_runtime_auth_signal(
            stdout_text=stdout_text,
            provider_name=provider_name,
            exit_code=exit_code,
        ):
            category = ErrorCategory.AUTH
    if category == ErrorCategory.AUTH:
        _handle_auth_error(
            provider_label=provider_label,
            koan_root=koan_root,
            instance=instance,
            mission_title=mission_title,
        )
        return True
    if category == ErrorCategory.QUOTA:
        _handle_quota_error(
            provider_name=provider_name,
            provider_label=provider_label,
            koan_root=koan_root,
            instance=instance,
            project_name=project_name,
            mission_title=mission_title,
            run_num=run_num,
            hqe_kwargs=hqe_kwargs,
        )
        return True
    return False


def _cli_runtime_auth_signal(
    *,
    stdout_text: str,
    provider_name: str,
    exit_code: int,
) -> bool:
    """Detect provider auth failures from stdout-safe runtime lines.

    Skill stdout is normally DATA, so broad stdout auth scans can false-positive
    on quoted CI logs or source text. Codex, however, reports real stream auth
    failures on stdout. Keep the trusted surface narrow: raw provider JSON
    events, Koan's ``[cli]`` stream summaries, and CLI failure summaries.
    """
    if exit_code == 0 or not stdout_text or not provider_name:
        return False

    from app.provider.base import PROVIDER_ERROR_EVENT_TYPES

    runtime_lines: list[str] = []
    for line in stdout_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("[cli]") or "CLI invocation failed:" in stripped:
            runtime_lines.append(stripped)
            continue
        if not stripped.startswith("{"):
            continue
        with contextlib.suppress(json.JSONDecodeError):
            event = json.loads(stripped)
            if (
                isinstance(event, dict)
                and str(event.get("type") or "") in PROVIDER_ERROR_EVENT_TYPES
            ):
                runtime_lines.append(stripped)

    if not runtime_lines:
        return False

    try:
        from app.provider import get_provider_by_name

        provider = get_provider_by_name(provider_name)
        return provider.detect_auth_failure(
            stdout_text="\n".join(runtime_lines),
            stderr_text="",
            exit_code=exit_code,
        )
    except KeyError as e:
        print(f"[run] unknown provider {provider_name!r}: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(
            f"[run] runtime auth detector failed for {provider_name!r}: {e}",
            file=sys.stderr,
        )
        return False


def _probe_exit0_quota(
    *,
    provider_name: str,
    provider_label: str,
    koan_root: str,
    instance: str,
    mission_title: str,
    run_num: int,
    hqe_kwargs: dict,
    project_name: str = "",
) -> bool:
    """Probe for quota exhaustion when CLI exited successfully (exit 0).

    Some provider wrappers emit quota payloads with exit 0.  Without this
    check the mission would be finalized to Done before any pause fires.

    Returns True if quota was detected and handled.
    """
    from app.quota_handler import handle_quota_exhaustion, QUOTA_CHECK_UNRELIABLE
    probe = handle_quota_exhaustion(
        koan_root=koan_root,
        instance_dir=instance,
        project_name=project_name,
        run_count=run_num,
        provider_name=provider_name,
        **hqe_kwargs,
    )
    if probe is None or probe is QUOTA_CHECK_UNRELIABLE:
        return False
    reset_display, resume_msg = probe
    log("quota", f"Exit-0 quota probe matched. {reset_display}")
    _requeue_mission_in_file(instance, mission_title)
    _commit_instance(instance, f"koan: quota exhausted {time.strftime('%Y-%m-%d-%H:%M')}")
    _notify(instance, (
        f"⏸️ {provider_label} quota exhausted.{(' ' + reset_display) if reset_display else ''}\n"
        f"Mission '{mission_title[:60]}' moved back to Pending.\n"
        f"{resume_msg} or use /resume to restart manually."
    ))
    return True


def _handle_pipeline_quota_flag(
    *,
    provider_label: str,
    koan_root: str,
    instance: str,
    mission_title: str,
    count: int,
    quota_info,
) -> bool:
    """Handle the ``quota_exhausted`` flag from :func:`run_post_mission`.

    ``handle_quota_exhaustion()`` inside ``run_post_mission`` already wrote
    the journal entry and created the pause state with accurate timing.
    This function handles the notification + requeue + fallback pause when
    ``quota_info`` is missing or incomplete.

    Returns True if quota was handled.
    """
    if quota_info and isinstance(quota_info, (list, tuple)) and len(quota_info) >= 2:
        reset_display, resume_msg = quota_info[0], quota_info[1]
    else:
        reset_display, resume_msg = "", "Auto-resume in ~5h"
        reset_ts, _disp = _compute_quota_reset_ts(instance)
        from app.pause_manager import create_pause
        create_pause(koan_root, "quota", reset_ts, reset_display or _disp)
    log("quota", f"Quota reached. {reset_display}")

    if mission_title:
        log("quota", "Requeueing mission to Pending (quota is transient)")
        _requeue_mission_in_file(instance, mission_title)

    _commit_instance(instance, f"koan: quota exhausted {time.strftime('%Y-%m-%d-%H:%M')}")
    _notify(instance, (
        f"⚠️ {provider_label} quota exhausted. {reset_display}\n\n"
        f"Mission '{mission_title[:60]}' moved back to Pending.\n"
        f"Kōan paused after {count} runs. {resume_msg} or use /resume to restart manually."
    ))
    return True


def _reset_usage_session(instance: str):
    """Reset internal usage session counters after resume.

    Ensures the usage estimator starts fresh so it doesn't
    re-pause immediately with stale high usage from the
    exhausted session.
    """
    try:
        from app.usage_estimator import cmd_reset_session
        usage_state = Path(instance, "usage_state.json")
        usage_md = Path(instance, "usage.md")
        cmd_reset_session(usage_state, usage_md)
        log("health", "Usage session counters reset after resume")
    except Exception as e:
        log("error", f"Usage session reset failed: {e}")


def _start_mission_in_file(instance: str, mission_title: str):
    """Move mission from Pending to In Progress via locked write."""
    try:
        from app.missions import start_mission
        from app.utils import modify_missions_file
        missions_path = Path(instance, "missions.md")
        if not missions_path.exists():
            return
        modify_missions_file(missions_path, lambda c: start_mission(c, mission_title))
    except Exception as e:
        log("error", f"Could not start mission in missions.md: {e}")


def _update_mission_in_file(
    instance: str,
    mission_title: str,
    *,
    failed: bool = False,
    cause_tag: str = "",
) -> bool:
    """Move mission from Pending/In Progress to Done/Failed via locked write.

    *cause_tag* is only honored when *failed* is True; it is appended to
    the missions.md entry (e.g. ``[stagnation]``) so the failure reason
    is visible without digging through journals.

    Returns True if the mission was actually moved, False otherwise (e.g.
    the mission text could not be matched in Pending/In Progress). A False
    return means the mission is still in the queue and will be re-picked —
    callers should surface this rather than let it loop silently.
    """
    try:
        from app.missions import complete_mission, fail_mission, prune_completed_sections
        from app.utils import modify_missions_file
        missions_path = Path(instance, "missions.md")
        if not missions_path.exists():
            return False

        if failed:
            def transform(content):
                return fail_mission(content, mission_title, cause_tag=cause_tag)
        else:
            def transform(content):
                return complete_mission(content, mission_title)

        before = [None]

        def tracked(content):
            before[0] = content
            result = transform(content)
            result, _ = prune_completed_sections(result)
            return result

        after = modify_missions_file(missions_path, tracked)
        if before[0] is not None and after == before[0]:
            log("warning", f"Mission not found (no change): {mission_title[:80]}")
            return False
        return True
    except Exception as e:
        label = "fail" if failed else "complete"
        log("error", f"Could not {label} mission in missions.md: {e}")
        return False


def _requeue_mission_in_file(instance: str, mission_title: str):
    """Move mission from In Progress back to Pending via locked write."""
    try:
        from app.missions import requeue_mission
        from app.utils import modify_missions_file
        missions_path = Path(instance, "missions.md")
        if not missions_path.exists():
            return
        modify_missions_file(missions_path, lambda c: requeue_mission(c, mission_title))
    except Exception as e:
        log("error", f"Could not requeue mission in missions.md: {e}")


def _finalize_mission(instance: str, mission_title: str, project_name: str, exit_code: int):
    """Complete or fail a mission and record execution history.

    When the last mission was killed by the stagnation monitor, the
    module-level flag ``_last_mission_stagnated`` is read and cleared
    here. Stagnation handling is gated by ``max_retry_on_stagnation``
    in the stagnation config:

    - if the per-mission retry count is below the cap, the mission is
      re-queued to Pending (not failed), the counter is incremented,
      and a "retry" Telegram notification is sent;
    - once the cap is reached, the mission is marked Failed with a
      ``[stagnation]`` tag, the counter is cleared, and the regular
      stagnation notification is sent.

    Successful completions and non-stagnation failures clear any
    pending retry counter so the next attempt at the same mission
    title starts fresh.
    """
    failed = exit_code != 0
    cause_tag = ""
    stagnated = False
    if failed and _last_mission_stagnated.is_set():
        stagnated = True
        _last_mission_stagnated.clear()

    if stagnated:
        from app.config import get_stagnation_config
        from app.stagnation_monitor import (
            clear_retry_count,
            get_retry_count,
            increment_retry_count,
        )

        pattern = _stagnation_pattern_type or "unknown"
        excerpt = _stagnation_pattern_excerpt or ""

        cfg = get_stagnation_config(project_name)
        max_retry = int(cfg.get("max_retry_on_stagnation", 0))
        already = get_retry_count(instance, mission_title)
        if max_retry > 0 and already < max_retry:
            new_count = increment_retry_count(
                instance, mission_title,
                pattern_type=pattern, pattern_excerpt=excerpt,
            )
            log("koan", (
                f"Stagnation retry {new_count}/{max_retry} ({pattern}) — "
                f"requeueing mission: {mission_title[:60]}"
            ))
            _requeue_mission_in_file(instance, mission_title)
            _notify_stagnation_retry(
                mission_title, project_name, new_count, max_retry,
                pattern_type=pattern, pattern_excerpt=excerpt,
            )
            try:
                from app.mission_history import record_execution
                record_execution(instance, mission_title, project_name, exit_code)
            except (OSError, ValueError) as e:
                log("error", f"Mission history recording error: {e}")
            return

        # Retry cap reached (or retries disabled): mark Failed with cause tag.
        cause_tag = f"stagnation:{pattern}"
        clear_retry_count(instance, mission_title)
        _notify_stagnation(mission_title, project_name, pattern, excerpt)
    else:
        # A non-stagnation outcome resets any prior retry counter so a
        # mission that completes (or fails for a different reason) does
        # not carry stale stagnation state into a later attempt.
        try:
            from app.stagnation_monitor import clear_retry_count
            clear_retry_count(instance, mission_title)
        except Exception as e:
            log("error", f"Stagnation retry counter cleanup error: {e}")

    _update_mission_in_file(
        instance, mission_title, failed=failed, cause_tag=cause_tag,
    )
    try:
        from app.mission_history import record_execution
        record_execution(instance, mission_title, project_name, exit_code)
    except (OSError, ValueError) as e:
        log("error", f"Mission history recording error: {e}")


def _notify_stagnation(
    mission_title: str,
    project_name: str,
    pattern_type: str = "",
    pattern_excerpt: str = "",
) -> None:
    """Send a Telegram message announcing a stagnation abort."""
    try:
        from app.notify import NotificationPriority, send_telegram
        short_title = mission_title[:120]
        project_prefix = f"[{project_name}] " if project_name else ""
        cause = f" ({pattern_type})" if pattern_type else ""
        message = (
            f"🛑 {project_prefix}Mission stopped — Claude was stuck in a loop"
            f"{cause}. Marked as Failed in missions.md.\n\n"
            f"Mission: {short_title}"
        )
        if pattern_excerpt:
            message += f"\n\nContext: {pattern_excerpt[:200]}"
        send_telegram(message, priority=NotificationPriority.WARNING)
    except Exception as e:
        log("error", f"Stagnation notification failed: {e}")


def _notify_stagnation_retry(
    mission_title: str,
    project_name: str,
    attempt: int,
    max_attempts: int,
    pattern_type: str = "",
    pattern_excerpt: str = "",
) -> None:
    """Send a Telegram message announcing a stagnation-triggered requeue."""
    try:
        from app.notify import NotificationPriority, send_telegram
        short_title = mission_title[:120]
        project_prefix = f"[{project_name}] " if project_name else ""
        cause = f" ({pattern_type})" if pattern_type else ""
        message = (
            f"🔁 {project_prefix}Mission stagnated{cause} — "
            f"requeueing for retry {attempt}/{max_attempts}.\n\n"
            f"Mission: {short_title}"
        )
        if pattern_excerpt:
            message += f"\n\nContext: {pattern_excerpt[:200]}"
        send_telegram(message, priority=NotificationPriority.WARNING)
    except Exception as e:
        log("error", f"Stagnation retry notification failed: {e}")


def _get_koan_branch(koan_root: str) -> str:
    """Get the current branch of the koan repository.

    Returns the branch name, or "" on error.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=koan_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


def _restore_koan_branch(koan_root: str, expected_branch: str):
    """Restore the koan repo to the expected branch if it drifted.

    Skills like /rebase and /recreate do git checkouts on their
    project_path.  When project_path is the koan repo itself, a
    crash in the skill can leave the working tree on the wrong
    branch, breaking all subsequent module lookups.
    """
    if not expected_branch:
        return
    current = _get_koan_branch(koan_root)
    if current and current != expected_branch:
        from app.debug import debug_log
        debug_log(
            f"[run] koan branch drifted: {current} -> restoring {expected_branch}"
        )
        log("git", f"Restoring koan branch: {current} -> {expected_branch}")
        try:
            subprocess.run(
                ["git", "checkout", expected_branch],
                cwd=koan_root,
                capture_output=True,
                timeout=10,
            )
        except Exception as e:
            log("error", f"Failed to restore koan branch: {e}")


def _run_skill_mission(
    skill_cmd: list,
    koan_root: str,
    instance: str,
    project_name: str,
    project_path: str,
    run_num: int,
    mission_title: str,
    autonomous_mode: str,
    mission_tier: str = "",
) -> dict:
    """Execute a skill-dispatched mission directly via subprocess.

    Streams stdout/stderr line-by-line to pending.md so /live can show
    real-time progress during skill dispatch.

    Returns a dict with:
        exit_code (int): Process exit code (0 = success).
        stdout (str): Captured stdout text.
        stderr (str): Captured stderr text.
        quota_exhausted (bool): Whether quota exhaustion was detected in
            the post-mission pipeline.
        quota_info (tuple|None): (reset_display, resume_message) if exhausted.
    """
    from app.debug import debug_log

    mission_start = int(time.time())
    koan_pkg_dir = os.path.join(koan_root, "koan")
    pending_path = Path(instance) / "journal" / "pending.md"

    # Record the koan repo's HEAD before execution.  Skills like
    # /rebase and /recreate do git checkouts on project_path which
    # may be the koan repo itself — if they crash without restoring
    # the branch, subsequent runs break.
    koan_branch_before = _get_koan_branch(koan_root)

    from app.config import get_skill_timeout
    skill_timeout = get_skill_timeout()

    debug_log(f"[run] skill exec: cmd={' '.join(skill_cmd)}")
    debug_log(f"[run] skill exec: cwd={koan_pkg_dir} timeout={skill_timeout}s")
    stdout_lines = []
    proc = None

    # Create temp files for post-mission processing up front.
    # stderr is redirected to a file instead of a pipe to eliminate
    # deadlock risk: if a background drain thread dies (e.g.
    # UnicodeDecodeError), the pipe fills and both processes stall.
    fd_out, stdout_file = tempfile.mkstemp(prefix="koan-out-")
    os.close(fd_out)
    fd_err, stderr_file = tempfile.mkstemp(prefix="koan-err-")
    os.close(fd_err)
    fd_usage, stream_usage_file = tempfile.mkstemp(prefix="koan-stream-usage-")
    os.close(fd_usage)
    # Explicitly set PYTHONPATH so the subprocess can always resolve
    # app.* modules even if the working tree changes (e.g. skill does
    # a git checkout on the koan repo itself).
    skill_env = {
        **os.environ,
        "PYTHONPATH": koan_pkg_dir,
        "KOAN_STREAM_USAGE_FILE": stream_usage_file,
    }
    stderr_fh = None
    try:
        stderr_fh = open(stderr_file, "w")
        proc = subprocess.Popen(
            skill_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=stderr_fh,
            cwd=koan_pkg_dir,
            env=skill_env,
            text=True,
            start_new_session=True,
        )
        # Register for double-tap CTRL-C termination.
        _sig.claude_proc = proc

        from app.subprocess_runner import ProcessWatchdog, LivenessWatchdog

        watchdog = ProcessWatchdog(proc, skill_timeout).start()

        from app.config import get_first_output_timeout, get_rebase_first_output_timeout
        from app.skill_dispatch import mission_command_name
        # Resolve the canonical command so the rebase override applies to all
        # dispatch paths: /rebase (GitHub), /core.rebase (Telegram), /rb alias.
        if mission_command_name(mission_title) == "rebase":
            first_output_timeout = get_rebase_first_output_timeout()
        else:
            first_output_timeout = get_first_output_timeout()
        liveness = None
        if first_output_timeout > 0:
            liveness = LivenessWatchdog(
                proc, first_output_timeout,
                on_timeout=lambda: log(
                    "error",
                    f"No output for {first_output_timeout}s "
                    f"— killing stuck process (elapsed: {int(time.time() - mission_start)}s)",
                ),
            ).start()

        # Stream stdout line-by-line, appending each to pending.md
        # so /live shows real-time progress.
        pending_fh = None
        try:
            pending_fh = open(pending_path, "a")
        except OSError as e:
            debug_log(f"[run] cannot open pending.md for streaming: {e}")
        try:
            for line in proc.stdout:
                if liveness is not None:
                    liveness.heartbeat()
                stripped = line.rstrip("\n")
                stdout_lines.append(stripped)
                print(stripped)
                if pending_fh is not None:
                    try:
                        pending_fh.write(f"{stripped}\n")
                        pending_fh.flush()
                    except OSError:
                        pending_fh = None
        finally:
            if pending_fh is not None:
                pending_fh.close()
            watchdog.cancel()
            if liveness is not None:
                liveness.cancel()

        proc.wait(timeout=30)
        if watchdog.fired or (liveness and liveness.fired):
            raise subprocess.TimeoutExpired(skill_cmd, skill_timeout)
        exit_code = proc.returncode
        skill_stdout = "\n".join(stdout_lines)
        # Provider stream mode can persist token usage to a sidecar file.
        # Append that JSON payload to stdout capture so token_parser can
        # account for skill-dispatch sessions in run_post_mission.
        with suppress_logged(log, "warning", "Skill stream usage read failed", OSError, json.JSONDecodeError):
            raw_usage = Path(stream_usage_file).read_text().strip()
            if raw_usage:
                usage_payload = json.loads(raw_usage)
                if isinstance(usage_payload, dict):
                    usage_json = json.dumps(usage_payload, separators=(",", ":"))
                    if skill_stdout:
                        skill_stdout = f"{skill_stdout}\n{usage_json}"
                    else:
                        skill_stdout = usage_json
        # Read stderr from file after process exits.
        stderr_fh.close()
        stderr_fh = None
        try:
            with open(stderr_file) as f:
                skill_stderr = f.read()
        except OSError:
            skill_stderr = ""
        if skill_stderr.strip():
            print(skill_stderr, file=sys.stderr)
        debug_log(
            f"[run] skill exec: exit_code={exit_code} "
            f"stdout_len={len(skill_stdout)} stderr_len={len(skill_stderr)}"
        )
        if exit_code != 0:
            if skill_stdout:
                debug_log(f"[run] skill stdout: {skill_stdout[:2000]}")
            if skill_stderr:
                debug_log(f"[run] skill stderr: {skill_stderr[:2000]}")
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        liveness_fired = liveness and liveness.fired
        timeout_kind = "liveness" if liveness_fired else "watchdog"
        timeout_val = first_output_timeout if liveness_fired else skill_timeout
        log("error", f"Skill runner timed out ({timeout_kind}: {timeout_val}s)")
        debug_log(f"[run] skill exec: TIMEOUT ({timeout_kind}: {timeout_val}s)")
        # Log last lines of captured output so the journal shows *where*
        # the run stalled, not just that it timed out.
        tail_lines = stdout_lines[-20:] if stdout_lines else []
        if tail_lines:
            tail_preview = "\n".join(tail_lines)
            log("info", f"Last output before timeout:\n{tail_preview}")
            debug_log(f"[run] timeout tail ({len(tail_lines)} lines):\n{tail_preview}")
        else:
            log("info", "No stdout captured before timeout")
            debug_log("[run] timeout: no stdout lines captured")
        # Log stderr — may contain API errors that explain the hang
        with suppress_logged(log, "warning", "Timeout stderr read failed", OSError):
            _timeout_stderr = Path(stderr_file).read_text().strip()
            if _timeout_stderr:
                debug_log(f"[run] timeout stderr:\n{_timeout_stderr[:2000]}")
        exit_code = 1
        skill_stdout = "\n".join(stdout_lines)
        skill_stderr = ""
    except Exception as e:
        if proc is not None:
            _kill_process_group(proc)
        log("error", f"Skill runner failed: {e}\n{traceback.format_exc()}")
        debug_log(f"[run] skill exec: EXCEPTION {e}")
        exit_code = 1
        skill_stdout = "\n".join(stdout_lines)
        skill_stderr = ""
    finally:
        if proc is not None and proc.stdout is not None:
            with suppress_logged(log, "debug", "Skill proc stdout close failed", OSError):
                proc.stdout.close()
        if stderr_fh is not None:
            stderr_fh.close()
        _sig.claude_proc = None
        _reset_terminal()
        # Restore koan repo branch if it was changed by the skill.
        _restore_koan_branch(koan_root, koan_branch_before)

    # Write stdout to its temp file for post-mission processing.
    # stderr is already in stderr_file from the subprocess redirect.
    # Wrap in try/finally so temp files are cleaned up even if the write
    # or post-mission processing raises an unexpected exception (consistent
    # with the contemplative and regular mission paths).
    skill_result = {
        "exit_code": exit_code,
        "stdout": skill_stdout,
        "stderr": skill_stderr,
        "quota_exhausted": False,
        "quota_info": None,
    }
    try:
        with open(stdout_file, 'wb') as f:
            f.write(skill_stdout.encode('utf-8'))

        _skill_prefix = f"Run {run_num}"
        set_status(koan_root, f"{_skill_prefix} — finalizing")
        from app.mission_runner import run_post_mission
        _skill_provider_name, _ = _provider_identity()
        post_result = run_post_mission(
            instance_dir=instance,
            project_name=project_name,
            project_path=project_path,
            run_num=run_num,
            exit_code=exit_code,
            stdout_file=stdout_file,
            stderr_file=stderr_file,
            mission_title=mission_title,
            autonomous_mode=autonomous_mode or "implement",
            start_time=mission_start,
            status_callback=lambda step: set_status(
                koan_root, f"{_skill_prefix} — {step}"
            ),
            mission_tier=mission_tier,
            provider_name=_skill_provider_name,
            is_skill_dispatch=True,
        )
        if isinstance(post_result, dict) and post_result.get("quota_exhausted"):
            skill_result["quota_exhausted"] = True
            skill_result["quota_info"] = post_result.get("quota_info")
    except Exception as e:
        log("error", f"Post-mission error: {e}")
    finally:
        _cleanup_temp(stdout_file, stderr_file, stream_usage_file)
    duration = int(time.time()) - mission_start
    debug_log(f"[run] skill exec: done in {duration}s, exit_code={exit_code}")
    return skill_result


def _cleanup_temp(*files):
    """Remove temporary files."""
    for f in files:
        with suppress_logged(log, "debug", f"Temp file cleanup failed ({f})", OSError):
            Path(f).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Entry point with restart wrapper
# ---------------------------------------------------------------------------

def main():
    """Entry point with restart wrapper (replaces bash outer loop).

    Handles four exit modes:
    - Normal exit (break)
    - CTRL-C (KeyboardInterrupt → break)
    - Restart signal (SystemExit(42) → restart)
    - Unexpected crash (Exception → restart with backoff)
    """
    crash_count = 0
    while True:
        try:
            main_loop()
            break  # Normal exit
        except KeyboardInterrupt:
            break
        except SystemExit as e:
            if e.code == RESTART_EXIT_CODE:
                # Restart signal
                crash_count = 0
                print("[koan] Restarting run loop...")
                time.sleep(1)
                continue
            raise
        except Exception:
            crash_count += 1
            tb = traceback.format_exc()
            print(f"[koan] Unexpected crash ({crash_count}/{MAX_MAIN_CRASHES}): {tb}", file=sys.stderr)

            if crash_count >= MAX_MAIN_CRASHES:
                print(f"[koan] Too many crashes ({MAX_MAIN_CRASHES}). Giving up.", file=sys.stderr)
                _reset_terminal()
                sys.exit(1)

            backoff = _calculate_backoff(crash_count, MAX_BACKOFF_MAIN)
            print(f"[koan] Restarting in {backoff}s...", file=sys.stderr)
            time.sleep(backoff)

    _reset_terminal()


if __name__ == "__main__":
    main()
