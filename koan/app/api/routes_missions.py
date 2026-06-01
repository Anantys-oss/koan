"""REST API mission routes."""

import re
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from app.api.auth import require_token
from app.api.mission_index import (
    cancel_mission,
    get_mission,
    list_missions,
    record_mission,
    reconcile,
)

bp = Blueprint("missions", __name__)

# Validate command-style missions
_COMMAND_RE = re.compile(r"^/[a-zA-Z0-9_]+")


def _instance_dir() -> Path:
    return current_app.config["INSTANCE_DIR"]


def _missions_file() -> Path:
    return _instance_dir() / "missions.md"


def _validate_mission_body(data: dict):
    """Validate POST /v1/missions request body.

    Returns (text, project, urgent) or raises ValueError.
    """
    command = data.get("command", "").strip()
    text = data.get("text", "").strip()

    if not command and not text:
        raise ValueError("One of 'command' or 'text' is required")

    mission_text = command or text

    # Sanitize
    from app.missions import sanitize_mission_text
    mission_text = sanitize_mission_text(mission_text)

    if not mission_text:
        raise ValueError("Mission text cannot be empty after sanitization")

    project = data.get("project", "").strip() or None
    urgent = bool(data.get("urgent", False))

    return mission_text, project, urgent


def _build_entry(text: str, project: str | None) -> str:
    """Build the missions.md list entry with optional project tag."""
    if project:
        return f"- [project:{project}] {text}"
    return f"- {text}"


@bp.route("/v1/missions", methods=["GET"])
@require_token
def list_missions_route():
    status_filter = request.args.get("status")
    project_filter = request.args.get("project")
    records = list_missions(_instance_dir(), status_filter, project_filter)
    # Reconcile each record
    out = []
    for rec in records:
        rec = reconcile(_instance_dir(), _missions_file(), rec["id"])
        if rec:
            out.append(rec)
    return jsonify(out)


@bp.route("/v1/missions", methods=["POST"])
@require_token
def create_mission():
    data = request.get_json(silent=True) or {}
    try:
        text, project, urgent = _validate_mission_body(data)
    except ValueError as e:
        return jsonify({"error": {"code": "invalid_request", "message": str(e)}}), 422

    entry = _build_entry(text, project)

    from app.utils import insert_pending_mission
    insert_pending_mission(_missions_file(), entry, urgent=urgent)

    mission_id = record_mission(_instance_dir(), entry, project)
    return jsonify({"id": mission_id, "status": "pending"}), 202


@bp.route("/v1/missions/<mission_id>", methods=["GET"])
@require_token
def get_mission_route(mission_id: str):
    rec = get_mission(_instance_dir(), mission_id)
    if rec is None:
        return jsonify({"error": {"code": "not_found", "message": "Mission not found"}}), 404
    rec = reconcile(_instance_dir(), _missions_file(), mission_id)
    return jsonify(rec)


@bp.route("/v1/missions/<mission_id>", methods=["DELETE"])
@require_token
def delete_mission(mission_id: str):
    rec = get_mission(_instance_dir(), mission_id)
    if rec is None:
        return jsonify({"error": {"code": "not_found", "message": "Mission not found"}}), 404

    # Reconcile first to get current status
    rec = reconcile(_instance_dir(), _missions_file(), mission_id)
    status = rec.get("status", "pending")

    if status != "pending":
        return jsonify(
            {"error": {"code": "conflict", "message": f"Cannot cancel mission in status '{status}'"}}
        ), 409

    # Remove from missions.md
    stored_text = rec.get("text", "")
    needle = stored_text.lstrip("- ").strip()

    def _remove(content: str) -> str:
        lines = content.splitlines(keepends=True)
        result = []
        for line in lines:
            if needle in line:
                continue
            result.append(line)
        return "".join(result)

    from app.utils import modify_missions_file
    modify_missions_file(_missions_file(), _remove)

    cancel_mission(_instance_dir(), mission_id)
    return jsonify({"id": mission_id, "status": "removed"}), 200
