"""REST API status routes."""

import logging
from pathlib import Path

from flask import Blueprint, current_app, jsonify

from app.api.auth import require_token

log = logging.getLogger("koan.api")

bp = Blueprint("status", __name__)


def _instance_dir() -> Path:
    return current_app.config["INSTANCE_DIR"]


def _koan_root() -> Path:
    return current_app.config["KOAN_ROOT"]


def _get_agent_state() -> dict:
    """Derive structured agent state from signal files.

    Delegates to ``agent_state`` module and reshapes the result into the
    REST API's response contract.
    """
    from app.agent_state import get_agent_state

    full = get_agent_state(_koan_root())

    pause_info: dict = {}
    if full["state"] == "paused":
        from app.pause_manager import get_pause_state

        ps = get_pause_state(str(_koan_root()))
        if ps:
            pause_info = {
                "reason": ps.reason,
                "timestamp": ps.timestamp,
                "display": ps.display,
            }

    return {
        "state": full["state"],
        "mode": full["autonomous_mode"] or None,
        "run_info": full["run_info"] or None,
        "project": full["project"],
        "focus": full["focus"] is not None,
        "status_text": full["label"],
        "pause": pause_info,
        "elapsed_seconds": full["elapsed"],
    }


def _mission_counts() -> dict:
    """Count missions by status from the mission store."""
    try:
        from app.mission_store import MissionStore
        store = MissionStore()
        return {
            "pending": len(store.get_by_status("pending")),
            "in_progress": len(store.get_by_status("in_progress")),
            "done": len(store.get_by_status("done")),
            "failed": len(store.get_by_status("failed")),
        }
    except Exception as e:
        log.error("mission count error: %s", e)
        return {"pending": 0, "in_progress": 0, "done": 0, "failed": 0}


def _signal_flags() -> dict:
    """Boolean flags for stop/quota/pause signal files."""
    from app.agent_state import get_signal_status

    sigs = get_signal_status(_koan_root())
    return {
        "stop_requested": sigs["stop_requested"],
        "quota_paused": sigs["quota_paused"],
        "paused": sigs["paused"],
    }


def _attention_count() -> int:
    """Count unresolved attention items, fallback to 0 on error."""
    from app.attention import get_attention_count

    try:
        return get_attention_count(str(_koan_root()))
    except Exception as e:
        log.warning("attention count unavailable: %s", e)
        return 0


@bp.route("/v1/status")
@require_token
def status():
    return jsonify(
        {
            "agent": _get_agent_state(),
            "missions": _mission_counts(),
            "signals": _signal_flags(),
            "attention_count": _attention_count(),
        }
    )
