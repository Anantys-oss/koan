"""REST API status routes."""

import contextlib
import os
import re
import time
from pathlib import Path

from flask import Blueprint, current_app, jsonify

from app.api.auth import require_token

bp = Blueprint("status", __name__)

_STALE_THRESHOLD = 300  # 5 minutes

_STATUS_PATTERNS = [
    (re.compile(r"Error recovery"), "error_recovery"),
    (re.compile(r"Paused"), "paused"),
    (re.compile(r"post-contemplation"), "contemplating"),
    (re.compile(r"Idle"), "sleeping"),
    (re.compile(r"Run \d+/\d+"), "working"),
]

_MODE_RE = re.compile(r"— (REVIEW|IMPLEMENT|DEEP)\b")
_RUN_INFO_RE = re.compile(r"Run (\d+/\d+)")


def _instance_dir() -> Path:
    return current_app.config["INSTANCE_DIR"]


def _koan_root() -> Path:
    return current_app.config["KOAN_ROOT"]


def _get_agent_state() -> dict:
    """Derive structured agent state from signal files."""
    from app.signals import (
        PAUSE_FILE,
        PROJECT_FILE,
        STOP_FILE,
        STATUS_FILE,
        FOCUS_FILE,
    )
    root = _koan_root()

    paused = (root / PAUSE_FILE).exists()
    stopped = (root / STOP_FILE).exists()
    status_text = ""
    status_file = root / STATUS_FILE
    if status_file.exists():
        with contextlib.suppress(OSError):
            status_text = status_file.read_text().strip()
        try:
            mtime = status_file.stat().st_mtime
            if time.time() - mtime > _STALE_THRESHOLD:
                status_text = ""
        except OSError:
            pass

    project = ""
    project_file = root / PROJECT_FILE
    if project_file.exists():
        with contextlib.suppress(OSError):
            project = project_file.read_text().strip()

    focus = (root / FOCUS_FILE).exists()

    state = "idle"
    if stopped:
        state = "stopped"
    elif paused:
        state = "paused"
    else:
        for pattern, s in _STATUS_PATTERNS:
            if pattern.search(status_text):
                state = s
                break

    mode_match = _MODE_RE.search(status_text)
    mode = mode_match.group(1) if mode_match else None

    run_info_match = _RUN_INFO_RE.search(status_text)
    run_info = run_info_match.group(1) if run_info_match else None

    pause_info: dict = {}
    if paused:
        from app.pause_manager import get_pause_state
        ps = get_pause_state(str(root))
        if ps:
            pause_info = {
                "reason": ps.reason,
                "timestamp": ps.timestamp,
                "display": ps.display,
            }

    return {
        "state": state,
        "mode": mode,
        "run_info": run_info,
        "project": project,
        "focus": focus,
        "status_text": status_text,
        "pause": pause_info,
    }


def _mission_counts() -> dict:
    """Count missions by section."""
    missions_file = _instance_dir() / "missions.md"
    try:
        from app.missions import parse_sections
        content = missions_file.read_text() if missions_file.exists() else ""
        sections = parse_sections(content)
        return {
            "pending": len(sections.get("pending", [])),
            "in_progress": len(sections.get("in_progress", [])),
            "done": len(sections.get("done", [])),
            "failed": len(sections.get("failed", [])),
        }
    except Exception as e:
        print(f"[api/status] mission count error: {e}", file=__import__("sys").stderr)
        return {"pending": 0, "in_progress": 0, "done": 0, "failed": 0}


@bp.route("/v1/status")
@require_token
def status():
    state = _get_agent_state()
    counts = _mission_counts()
    return jsonify(
        {
            "agent": state,
            "missions": counts,
        }
    )
