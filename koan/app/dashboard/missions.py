"""Missions blueprint: list/add/reorder/cancel/edit + attention routes."""
import logging

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from app.dashboard import state
from app.dashboard_service import missions as missions_svc
from app.missions import (
    cancel_pending_mission,
    edit_pending_mission,
    reorder_mission,
)
from app.utils import insert_pending_mission, modify_missions_file

missions_bp = Blueprint("missions", __name__)


@missions_bp.route("/missions")
def missions_page():
    """Missions management page."""
    from app.utils import get_known_projects

    selected_project = request.args.get("project", "")
    missions = missions_svc.parse_missions()
    filtered = missions_svc.filter_missions_by_project(missions, selected_project)
    projects = [name for name, _path in get_known_projects()]
    skills_commands = missions_svc.get_mission_skill_commands()
    return render_template("missions.html", missions=filtered,
                           selected_project=selected_project, projects=projects,
                           skills_commands=skills_commands)


@missions_bp.route("/missions/add", methods=["POST"])
def add_mission():
    """Add a new mission to pending."""
    from app.missions import sanitize_mission_text

    text = sanitize_mission_text(request.form.get("mission", ""))
    project = request.form.get("project", "").strip()
    skill = request.form.get("skill", "").strip()
    if not text:
        return redirect(url_for("missions.missions_page"))

    if skill and skill not in missions_svc.get_mission_skill_commands():
        skill = ""

    if skill:
        text = f"/{skill} {text}"

    # Format entry
    if project:
        entry = f"- [project:{project}] {text}"
    else:
        entry = f"- {text}"

    inserted = insert_pending_mission(state.MISSIONS_FILE, entry)
    if inserted:
        try:
            from app.api.mission_index import record_mission
            record_mission(state.INSTANCE_DIR, entry, project or None)
        except Exception as exc:
            logging.warning("record_mission failed (non-fatal): %s", exc)
    return redirect(url_for("missions.missions_page"))


@missions_bp.route("/api/projects")
def api_projects():
    """Return list of known project names."""
    return jsonify({"projects": missions_svc.get_all_project_names()})


@missions_bp.route("/api/missions")
def api_missions():
    """Return full mission lists as JSON."""
    missions = missions_svc.parse_missions()
    return jsonify({
        "pending": missions["pending"],
        "in_progress": missions["in_progress"],
        "done": missions["done"],
    })


@missions_bp.route("/api/missions/reorder", methods=["POST"])
def api_missions_reorder():
    """Reorder a pending mission."""
    data = request.get_json(silent=True) or {}
    position = data.get("position")
    target = data.get("target")

    if position is None or target is None:
        return jsonify({"ok": False, "error": "Missing position or target"}), 400

    try:
        position = int(position)
        target = int(target)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "position and target must be integers"}), 400

    try:
        result = {}

        def transform(content):
            new_content, display = reorder_mission(content, position, target)
            result["display"] = display
            return new_content

        modify_missions_file(state.MISSIONS_FILE, transform)
        missions = missions_svc.parse_missions()
        return jsonify({
            "ok": True,
            "display": result.get("display", ""),
            "pending": missions["pending"],
        })
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@missions_bp.route("/api/missions/cancel", methods=["POST"])
def api_missions_cancel():
    """Cancel a pending mission by position."""
    data = request.get_json(silent=True) or {}
    position = data.get("position")

    if position is None:
        return jsonify({"ok": False, "error": "Missing position"}), 400

    try:
        result = {}

        def transform(content):
            new_content, cancelled = cancel_pending_mission(content, str(int(position)))
            result["cancelled"] = cancelled
            return new_content

        modify_missions_file(state.MISSIONS_FILE, transform)
        cancelled_text = result.get("cancelled", "")
        if cancelled_text:
            try:
                from app.api.mission_index import cancel_by_text
                cancel_by_text(state.INSTANCE_DIR, cancelled_text)
            except Exception as exc:
                logging.warning("cancel_by_text failed (non-fatal): %s", exc)
        missions = missions_svc.parse_missions()
        return jsonify({
            "ok": True,
            "cancelled": cancelled_text,
            "pending": missions["pending"],
        })
    except (ValueError, TypeError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@missions_bp.route("/api/missions/edit", methods=["POST"])
def api_missions_edit():
    """Edit a pending mission's text."""
    data = request.get_json(silent=True) or {}
    position = data.get("position")
    text = data.get("text", "").strip()

    if position is None:
        return jsonify({"ok": False, "error": "Missing position"}), 400
    if not text:
        return jsonify({"ok": False, "error": "Mission text cannot be empty"}), 400

    try:
        position = int(position)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "position must be an integer"}), 400

    try:
        result = {}

        def transform(content):
            new_content, display = edit_pending_mission(content, position, text)
            result["display"] = display
            return new_content

        modify_missions_file(state.MISSIONS_FILE, transform)
        missions = missions_svc.parse_missions()
        return jsonify({
            "ok": True,
            "display": result.get("display", ""),
            "pending": missions["pending"],
        })
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@missions_bp.route("/api/attention")
def api_attention():
    """JSON list of attention items requiring human action."""
    from app.attention import get_attention_items

    project = request.args.get("project", "")
    items = get_attention_items(str(state.KOAN_ROOT), project_filter=project)
    return jsonify({"items": items})


@missions_bp.route("/api/attention/dismiss", methods=["POST"])
def api_attention_dismiss():
    """Dismiss an attention item by ID."""
    from app.attention import dismiss_item

    data = request.get_json(silent=True) or {}
    item_id = data.get("id", "").strip()
    if not item_id:
        return jsonify({"ok": False, "error": "Missing id"}), 400
    dismiss_item(str(state.KOAN_ROOT), item_id)
    return jsonify({"ok": True})


@missions_bp.route("/api/attention/dismiss-all", methods=["POST"])
def api_attention_dismiss_all():
    """Dismiss all current attention items at once."""
    from app.attention import dismiss_all

    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"ok": False, "error": "Invalid JSON"}), 400
    project = data.get("project", "")
    count = dismiss_all(str(state.KOAN_ROOT), project_filter=project)
    return jsonify({"ok": True, "dismissed": count})
