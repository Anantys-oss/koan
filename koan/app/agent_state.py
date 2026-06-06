"""Shared agent state derivation from signal files.

Both the dashboard and the REST API need to classify the agent's current
state (idle, working, paused, stopped, …) from the same set of ``.koan-*``
signal files.  This module centralises that logic so both consumers share
one code path.
"""

import contextlib
import re
import time
from pathlib import Path

from app.signals import (
    DAILY_REPORT_FILE,
    FOCUS_FILE,
    PAUSE_FILE,
    PROJECT_FILE,
    QUOTA_RESET_FILE,
    STATUS_FILE,
    STOP_FILE,
)

# ── Staleness ────────────────────────────────────────────────────────────

STALE_THRESHOLD_SECONDS = 300  # 5 minutes

# ── Status-text classification ───────────────────────────────────────────
# Order matters: first match wins.

STATUS_PATTERNS = [
    (re.compile(r"Error recovery"), "error_recovery"),
    (re.compile(r"Paused"), "paused"),
    (re.compile(r"post-contemplation"), "contemplating"),
    (re.compile(r"Idle"), "sleeping"),
    (re.compile(r"Run \d+/\d+ — executing"), "working"),
    (re.compile(r"Run \d+/\d+ — skill dispatch"), "working"),
    (re.compile(r"Run \d+/\d+ — (REVIEW|IMPLEMENT|DEEP)"), "working"),
    (re.compile(r"Run \d+/\d+ — preparing"), "working"),
    (re.compile(r"Run \d+/\d+ — finalizing"), "working"),
    (re.compile(r"Run \d+/\d+ — done"), "working"),
]

BADGE_COLORS = {
    "working": "green",
    "sleeping": "blue",
    "contemplating": "blue",
    "paused": "orange",
    "stopped": "red",
    "error_recovery": "red",
    "idle": "muted",
}

RUN_INFO_RE = re.compile(r"Run (\d+/\d+)")
MODE_RE = re.compile(r"— (REVIEW|IMPLEMENT|DEEP)\b")
STATUS_PROJECT_RE = re.compile(r"on (\S+)\s*$")


# ── Signal reading ───────────────────────────────────────────────────────

def get_signal_status(koan_root: Path) -> dict:
    """Read ``.koan-*`` signal files and return raw status dict."""
    status = {
        "stop_requested": (koan_root / STOP_FILE).exists(),
        "quota_paused": (koan_root / QUOTA_RESET_FILE).exists(),
        "paused": (koan_root / PAUSE_FILE).exists(),
        "loop_status": "",
        "pause_reason": "",
        "reset_time": "",
    }

    if status["paused"]:
        from app.pause_manager import get_pause_state

        state = get_pause_state(str(koan_root))
        if state:
            status["pause_reason"] = state.reason
            if state.display:
                status["reset_time"] = state.display
            elif state.timestamp:
                try:
                    from app.reset_parser import time_until_reset

                    status["reset_time"] = f"in ~{time_until_reset(state.timestamp)}"
                except (ValueError, ImportError):
                    pass

    status_file = koan_root / STATUS_FILE
    if status_file.exists():
        status["loop_status"] = status_file.read_text().strip()

    report_file = koan_root / DAILY_REPORT_FILE
    if report_file.exists():
        status["last_report"] = report_file.read_text().strip()

    return status


# ── Full state derivation ────────────────────────────────────────────────

def get_agent_state(koan_root: Path) -> dict:
    """Derive a structured agent state from signal files.

    Returns a dict with keys: state, label, project, run_info,
    autonomous_mode, pause_reason, reset_time, focus, elapsed, badge_color.
    """
    signals = get_signal_status(koan_root)
    status_text = signals.get("loop_status", "")

    project = ""
    project_file = koan_root / PROJECT_FILE
    if project_file.exists():
        with contextlib.suppress(OSError):
            project = project_file.read_text().strip()

    focus = None
    focus_file = koan_root / FOCUS_FILE
    if focus_file.exists():
        try:
            from app.focus_manager import get_focus_state

            fs = get_focus_state(str(koan_root))
            if fs and not fs.is_expired():
                focus = {
                    "remaining": fs.remaining_display(),
                    "reason": fs.reason,
                }
        except (OSError, ImportError):
            pass

    elapsed = 0
    status_file = koan_root / STATUS_FILE
    is_stale = False
    if status_file.exists():
        try:
            elapsed = int(time.time() - status_file.stat().st_mtime)
            is_stale = elapsed > STALE_THRESHOLD_SECONDS
        except OSError:
            pass

    if signals["stop_requested"]:
        state = "stopped"
        label = "Stopped"
    elif signals["paused"] or signals["quota_paused"]:
        state = "paused"
        reason = signals.get("pause_reason", "")
        reset = signals.get("reset_time", "")
        if signals["quota_paused"] and not reason:
            reason = "quota"
        if reason == "quota":
            label = f"Paused — quota{f' ({reset})' if reset else ''}"
        elif reason:
            label = f"Paused — {reason}"
        else:
            label = "Paused"
    elif status_text and not is_stale:
        state = "idle"
        for pattern, matched_state in STATUS_PATTERNS:
            if pattern.search(status_text):
                state = matched_state
                break
        label = status_text
    else:
        state = "idle"
        label = "Idle" if not is_stale else "Idle (stale)"

    run_info = ""
    m = RUN_INFO_RE.search(status_text)
    if m:
        run_info = m.group(1)

    autonomous_mode = ""
    m = MODE_RE.search(status_text)
    if m:
        autonomous_mode = m.group(1)

    if not project:
        m = STATUS_PROJECT_RE.search(status_text)
        if m:
            project = m.group(1)

    return {
        "state": state,
        "label": label,
        "project": project,
        "run_info": run_info,
        "autonomous_mode": autonomous_mode,
        "pause_reason": signals.get("pause_reason", ""),
        "reset_time": signals.get("reset_time", ""),
        "focus": focus,
        "elapsed": elapsed,
        "badge_color": BADGE_COLORS.get(state, "muted"),
    }
