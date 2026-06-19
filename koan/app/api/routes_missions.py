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
    update_mission_text,
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


def _store_lookup_key(stored_text: str) -> str:
    """Derive the MissionStore lookup key from a sidecar-stored entry.

    The sidecar index stores the full entry text (which may carry a
    ``[project:X]`` tag), but :class:`MissionStore` keeps the project in a
    separate field and the record's ``text`` holds no project tag.  This
    strips the project tag (and any lifecycle markers via
    ``canonical_mission_key``) so ``store.find()`` matches reliably.
    """
    from app.missions import canonical_mission_key, extract_project_tag

    full_key = canonical_mission_key(stored_text)
    proj = extract_project_tag(full_key)
    if proj and proj != "default":
        full_key = re.sub(
            rf'\[projec?t:{re.escape(proj)}\]\s*', '', full_key, flags=re.IGNORECASE,
        ).strip()
    return full_key


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

    from app.utils import insert_pending_mission
    insert_pending_mission(text, project, urgent=urgent)

    entry = _build_entry(text, project)
    mission_id = record_mission(_instance_dir(), entry, project)
    return jsonify({"id": mission_id, "status": "pending"}), 202


@bp.route("/v1/missions/reorder", methods=["POST"])
@require_token
def reorder_mission_route():
    data = request.get_json(silent=True)
    if data is None:
        return jsonify(
            {"error": {"code": "invalid_request", "message": "Invalid JSON body"}}
        ), 422
    mission_id = data.get("mission_id", "").strip() if isinstance(data.get("mission_id"), str) else ""
    target_position = data.get("target_position")

    if not mission_id or target_position is None:
        return jsonify(
            {"error": {"code": "invalid_request", "message": "'mission_id' and 'target_position' are required"}}
        ), 422

    if isinstance(target_position, bool) or not isinstance(target_position, int):
        return jsonify(
            {"error": {"code": "invalid_request", "message": "'target_position' must be an integer"}}
        ), 422

    rec = get_mission(_instance_dir(), mission_id)
    if rec is None:
        return jsonify({"error": {"code": "not_found", "message": "Mission not found"}}), 404

    rec = reconcile(_instance_dir(), _missions_file(), mission_id)
    status = rec.get("status")

    if status != "pending":
        return jsonify(
            {"error": {"code": "conflict", "message": f"Cannot reorder mission in status '{status}'"}}
        ), 409

    from app.mission_store import locked_store

    stored_text = rec.get("text", "")
    clean_text = _store_lookup_key(stored_text)

    try:
        with locked_store() as store:
            from app.missions import canonical_mission_key
            needle = canonical_mission_key(clean_text)
            pending_matches = [
                r for r in store._records
                if r.status == "pending" and canonical_mission_key(r.text) == needle
            ]
            if len(pending_matches) > 1:
                raise ValueError(
                    "Ambiguous match: multiple pending missions have the same text"
                )
            record_obj = store.find(clean_text)
            if record_obj is None or record_obj.status != "pending":
                raise ValueError("Mission not found in pending queue")
            pending = store.get_by_status("pending")
            from_idx = next(
                (i for i, r in enumerate(pending) if r.id == record_obj.id), None
            )
            if from_idx is None:
                raise ValueError("Mission not found in pending queue")
            if not store.reorder_pending(from_idx, target_position - 1):
                raise ValueError(f"Invalid target position: {target_position}")
    except ValueError as e:
        msg = str(e)
        if "not found in pending" in msg or "Ambiguous match" in msg:
            return jsonify({"error": {"code": "conflict", "message": msg}}), 409
        return jsonify({"error": {"code": "invalid_request", "message": msg}}), 422

    return jsonify({"id": mission_id, "status": "pending"}), 200


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
    status = rec.get("status")

    if status != "pending":
        return jsonify(
            {"error": {"code": "conflict", "message": f"Cannot cancel mission in status '{status}'"}}
        ), 409

    # Remove from the mission store (regenerates missions.md view)
    from app.mission_store import locked_store

    stored_text = rec.get("text", "")
    clean_text = _store_lookup_key(stored_text)

    with locked_store() as store:
        store.cancel_pending(clean_text)

    cancel_mission(_instance_dir(), mission_id)
    return jsonify({"id": mission_id, "status": "removed"}), 200


@bp.route("/v1/missions/<mission_id>", methods=["PATCH"])
@require_token
def edit_mission(mission_id: str):
    rec = get_mission(_instance_dir(), mission_id)
    if rec is None:
        return jsonify({"error": {"code": "not_found", "message": "Mission not found"}}), 404

    rec = reconcile(_instance_dir(), _missions_file(), mission_id)
    status = rec.get("status")

    if status != "pending":
        return jsonify(
            {"error": {"code": "conflict", "message": f"Cannot edit mission in status '{status}'"}}
        ), 409

    data = request.get_json(silent=True)
    if data is None:
        return jsonify(
            {"error": {"code": "invalid_request", "message": "Invalid JSON body"}}
        ), 422
    raw_text = data.get("text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return jsonify(
            {"error": {"code": "invalid_request", "message": "'text' is required and cannot be empty"}}
        ), 422
    new_text = raw_text.strip()

    from app.missions import sanitize_mission_text
    new_text = sanitize_mission_text(new_text)
    if not new_text:
        return jsonify(
            {"error": {"code": "invalid_request", "message": "Mission text cannot be empty after sanitization"}}
        ), 422

    from app.mission_store import locked_store

    stored_text = rec.get("text", "")
    clean_text = _store_lookup_key(stored_text)

    project = rec.get("project")

    try:
        with locked_store() as store:
            if not store.edit(clean_text, new_text):
                raise ValueError("Mission not found in pending queue")
    except ValueError as e:
        msg = str(e)
        if "not found in pending" in msg or "Ambiguous match" in msg:
            return jsonify({"error": {"code": "conflict", "message": msg}}), 409
        return jsonify({"error": {"code": "invalid_request", "message": msg}}), 422

    new_entry = _build_entry(new_text, project)
    if not update_mission_text(_instance_dir(), mission_id, new_entry):
        return jsonify(
            {"error": {"code": "conflict", "message": "Failed to update mission index"}}
        ), 409

    return jsonify({"id": mission_id, "status": "pending"}), 200
