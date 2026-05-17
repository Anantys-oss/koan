"""Centralized signal file constants for Kōan.

All ``.koan-*`` file names used for inter-process signaling and state
tracking are defined here as constants. Import from this module instead
of hardcoding the file names — renaming or auditing signal files is then
a single-file change.

PID files use a parameterized helper since the process name varies.
"""

# -- Process lifecycle signals ------------------------------------------------

STOP_FILE = ".koan-stop"
SHUTDOWN_FILE = ".koan-shutdown"
RESTART_FILE = ".koan-restart"
CYCLE_FILE = ".koan-cycle"
ABORT_FILE = ".koan-abort"

# -- Pause / quota signals ----------------------------------------------------

PAUSE_FILE = ".koan-pause"
SKIP_START_PAUSE_FILE = ".koan-skip-start-pause"
QUOTA_RESET_FILE = ".koan-quota-reset"

# -- Status / heartbeat -------------------------------------------------------

STATUS_FILE = ".koan-status"
HEARTBEAT_FILE = ".koan-heartbeat"
RUN_HEARTBEAT_FILE = ".koan-run-heartbeat"

# Code version the bridge process was launched against (git HEAD SHA).
# Written by awake.py at startup, read by the runner's bridge_watchdog
# to detect "bridge is alive but stuck on stale sys.modules" — a state
# the heartbeat alone cannot catch.
BRIDGE_VERSION_FILE = ".koan-bridge-version"
# Persistent state for the runner-side bridge self-healing tier escalator.
BRIDGE_HEAL_STATE_FILE = ".koan-bridge-heal-state"

# -- Mode flags ----------------------------------------------------------------

FOCUS_FILE = ".koan-focus"
PASSIVE_FILE = ".koan-passive"
VERBOSE_FILE = ".koan-verbose"

# -- Project tracking ----------------------------------------------------------

PROJECT_FILE = ".koan-project"

# -- Reports / logs ------------------------------------------------------------

DAILY_REPORT_FILE = ".koan-daily-report"
DEBUG_LOG_FILE = ".koan-debug.log"

# -- Notification signals ------------------------------------------------------

CHECK_NOTIFICATIONS_FILE = ".koan-check-notifications"

# -- Misc ----------------------------------------------------------------------

ONBOARDING_FILE = ".koan-onboarding.json"
LAST_CLEANUP_FILE = ".koan-last-cleanup"


def pid_file(process_name: str) -> str:
    """Return the signal file name for a PID file, e.g. ``.koan-pid-run``."""
    return f".koan-pid-{process_name}"
