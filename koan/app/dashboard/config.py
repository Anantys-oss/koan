"""Config blueprint: config/nickname editing + automation rules + recurring tasks.

NOTE: this module is ``app.dashboard.config`` — it must never shadow the global
``app.config``. All references to the global config use absolute imports.
"""
from flask import Blueprint, jsonify, render_template, request

from app.automation_rules import (
    KNOWN_ACTIONS,
    KNOWN_EVENTS,
    add_rule,
    load_rules,
    remove_rule,
    toggle_rule,
    update_rule_params,
)
from app.dashboard import state
from app.dashboard_service import journal as journal_svc
from app.dashboard_service import missions as missions_svc
from app.dashboard_service import stats as stats_svc
from app.dashboard_service import validate_yaml
from app.recurring import (
    _locked_modify as _recurring_locked_modify,
    add_recurring,
    add_recurring_interval,
    force_run,
    list_recurring,
    load_recurring,
    parse_at_time,
    parse_days,
    parse_interval,
)

config_bp = Blueprint("config", __name__)


# Config page

@config_bp.route("/config")
def config_page():
    """Dedicated config editor page."""
    return render_template("config.html")


@config_bp.route("/api/config/<target>", methods=["PUT"])
def api_config_save(target: str):
    """Validate and save config.yaml or projects.yaml."""
    from app.projects_config import resolve_projects_config_write_path
    from app.utils import atomic_write

    paths = {
        "config": state.KOAN_ROOT / "instance" / "config.yaml",
        "projects": resolve_projects_config_write_path(str(state.KOAN_ROOT)),
    }
    if target not in paths:
        return jsonify({"ok": False, "error": f"Unknown config target: {target}"}), 404

    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"ok": False, "error": "Invalid or missing JSON body"}), 400
    content = data.get("content")
    if content is None:
        return jsonify({"ok": False, "error": "Missing content"}), 400

    if state._SENSITIVE_KEY_RE.search(content) and "<redacted>" in content:
        return jsonify({"ok": False, "error": "Content contains <redacted> placeholders — cannot save masked values"}), 422

    error = validate_yaml(content)
    if error:
        return jsonify({"ok": False, "error": f"Invalid YAML: {error}"}), 422

    path = paths[target]
    try:
        atomic_write(path, content)
    except OSError as e:
        return jsonify({"ok": False, "error": f"Write failed: {e}"}), 500
    return jsonify({"ok": True})


@config_bp.route("/api/config/restart", methods=["POST"])
def api_config_restart():
    """Signal the agent loop to restart."""
    import sys

    from app.restart_manager import request_restart
    try:
        request_restart(str(state.KOAN_ROOT))
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[dashboard] restart signal failed: {e}", file=sys.stderr)
        return jsonify({"ok": False, "error": "Failed to send restart signal"}), 500


@config_bp.route("/api/config/sync", methods=["GET"])
def api_config_sync():
    """Non-SSE poll fallback for config-sync status."""
    from app import config_sync
    return jsonify(config_sync.compute_status(state.KOAN_ROOT))


@config_bp.route("/api/config/restart-if-idle", methods=["POST"])
def api_config_restart_if_idle():
    """Restart the agent only when idle (no in-flight mission); 409 if busy."""
    import sys
    agent_state = stats_svc.get_agent_state()
    if agent_state.get("state") != "idle":
        return jsonify({"ok": False, "error": "agent_busy",
                        "state": agent_state.get("state")}), 409
    from app.restart_manager import request_restart
    try:
        request_restart(str(state.KOAN_ROOT))
    except Exception as e:
        print(f"[dashboard] restart-if-idle failed: {e}", file=sys.stderr)
        return jsonify({"ok": False, "error": "restart_failed"}), 500
    return jsonify({"ok": True, "status": "restart_signaled"})


@config_bp.route("/api/nickname", methods=["GET"])
def api_nickname_get():
    """Return the current instance nickname."""
    from app.config import get_dashboard_nickname
    return jsonify({"nickname": get_dashboard_nickname()})


@config_bp.route("/api/nickname", methods=["PUT"])
def api_nickname_set():
    """Update the instance nickname in config.yaml, preserving comments."""
    from app.utils import update_config_yaml

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"ok": False, "error": "Invalid JSON"}), 400

    nickname = str(payload.get("nickname", "")).strip()[:50]

    config_path = state.INSTANCE_DIR / "config.yaml"
    update_config_yaml(config_path, ["dashboard", "nickname"], nickname)
    return jsonify({"ok": True, "nickname": nickname})


# Automation rules

@config_bp.route("/rules")
def rules_page():
    """Automation rules management page."""
    rules = load_rules(str(state.INSTANCE_DIR))
    history = journal_svc.get_rule_history()
    return render_template(
        "rules.html",
        rules=rules,
        history=history,
        known_events=sorted(KNOWN_EVENTS),
        known_actions=sorted(KNOWN_ACTIONS),
    )


@config_bp.route("/api/rules", methods=["GET"])
def api_rules_list():
    """Return all automation rules as JSON."""
    rules = load_rules(str(state.INSTANCE_DIR))
    return jsonify([r.to_dict() for r in rules])


@config_bp.route("/api/rules", methods=["POST"])
def api_rules_create():
    """Create a new automation rule."""
    data = request.get_json(force=True) or {}
    event = data.get("event", "")
    action = data.get("action", "")

    if event not in KNOWN_EVENTS:
        return jsonify({"error": f"Unknown event '{event}'. Valid: {sorted(KNOWN_EVENTS)}"}), 400
    if action not in KNOWN_ACTIONS:
        return jsonify({"error": f"Unknown action '{action}'. Valid: {sorted(KNOWN_ACTIONS)}"}), 400

    rule = add_rule(
        str(state.INSTANCE_DIR),
        event=event,
        action=action,
        params=data.get("params") or {},
        enabled=bool(data.get("enabled", True)),
    )
    return jsonify(rule.to_dict()), 201


@config_bp.route("/api/rules/<rule_id>", methods=["PATCH"])
def api_rules_update(rule_id):
    """Toggle enabled state or update params of a rule."""
    data = request.get_json(force=True) or {}

    updated = None
    if "enabled" in data:
        updated = toggle_rule(str(state.INSTANCE_DIR), rule_id, enabled=bool(data["enabled"]))
    if "params" in data and updated is None:
        updated = update_rule_params(str(state.INSTANCE_DIR), rule_id, data["params"])
    elif "params" in data and updated is not None:
        updated = update_rule_params(str(state.INSTANCE_DIR), rule_id, data["params"])

    if updated is None:
        return jsonify({"error": "Rule not found"}), 404
    return jsonify(updated.to_dict())


@config_bp.route("/api/rules/<rule_id>", methods=["DELETE"])
def api_rules_delete(rule_id):
    """Delete a rule by id."""
    removed = remove_rule(str(state.INSTANCE_DIR), rule_id)
    if not removed:
        return jsonify({"error": "Rule not found"}), 404
    return jsonify({"ok": True})


# Recurring tasks

@config_bp.route("/recurring")
def recurring_page():
    """Recurring tasks management page."""
    tasks = list_recurring(state.RECURRING_FILE)
    projects = missions_svc.get_all_project_names()
    return render_template("recurring.html", tasks=tasks, projects=projects)


@config_bp.route("/api/recurring", methods=["GET"])
def api_recurring_list():
    """Return all recurring tasks as JSON."""
    return jsonify(list_recurring(state.RECURRING_FILE))


@config_bp.route("/api/recurring", methods=["POST"])
def api_recurring_create():
    """Create a new recurring task."""
    data = request.get_json(force=True) or {}
    frequency = data.get("frequency", "")
    text = data.get("text", "").strip()

    if not text:
        return jsonify({"error": "Task text is required"}), 400
    if frequency not in ("hourly", "daily", "weekly", "every"):
        return jsonify({"error": f"Invalid frequency: {frequency}"}), 400

    project = data.get("project") or None
    at = data.get("at") or None

    if at:
        try:
            at, _ = parse_at_time(at + " _")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    days = data.get("days")
    if days:
        try:
            days = parse_days(days)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    if frequency == "every":
        interval_str = data.get("interval", "")
        if not interval_str:
            return jsonify({"error": "Interval is required for 'every' frequency"}), 400
        try:
            interval_secs = parse_interval(interval_str)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        task = add_recurring_interval(
            state.RECURRING_FILE,
            interval_seconds=interval_secs,
            interval_display=interval_str,
            text=text,
            project=project,
        )
    else:
        task = add_recurring(
            state.RECURRING_FILE,
            frequency=frequency,
            text=text,
            project=project,
            at=at,
        )

    if days:
        task_id = task["id"]

        def _set_days(missions):
            for m in missions:
                if m["id"] == task_id:
                    m["days"] = days
                    break

        _recurring_locked_modify(state.RECURRING_FILE, _set_days)
        task["days"] = days

    return jsonify(task), 201


@config_bp.route("/api/recurring/<task_id>", methods=["PATCH"])
def api_recurring_update(task_id):
    """Update a recurring task's fields."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid or empty JSON body"}), 400

    if "frequency" in data:
        freq = data["frequency"]
        if freq not in ("hourly", "daily", "weekly", "every"):
            return jsonify({"error": f"Invalid frequency: {freq}"}), 400
    if "at" in data and data["at"]:
        try:
            parse_at_time(data["at"] + " _")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    if "days" in data and data["days"]:
        try:
            parse_days(data["days"])
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    if "interval" in data and data.get("interval"):
        try:
            parse_interval(data["interval"])
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    result = {}

    def _update(missions):
        target = None
        for m in missions:
            if m["id"] == task_id:
                target = m
                break
        if target is None:
            return None

        if "enabled" in data:
            target["enabled"] = bool(data["enabled"])
        if "text" in data and data["text"].strip():
            target["text"] = data["text"].strip()
        if "frequency" in data:
            target["frequency"] = data["frequency"]
        if "at" in data:
            at_val = data["at"]
            if at_val:
                at_val, _ = parse_at_time(at_val + " _")
            target["at"] = at_val or None
        if "days" in data:
            days_val = data["days"]
            if days_val:
                days_val = parse_days(days_val)
            target["days"] = days_val or None
        if "project" in data:
            target["project"] = data["project"] or None
        if "interval" in data and target.get("frequency") == "every":
            interval_str = data["interval"]
            if interval_str:
                target["interval_seconds"] = parse_interval(interval_str)
                target["interval_display"] = interval_str.strip().lower()

        result.update(target)
        return True

    found = _recurring_locked_modify(state.RECURRING_FILE, _update)
    if found is None:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(result)


@config_bp.route("/api/recurring/<task_id>", methods=["DELETE"])
def api_recurring_delete(task_id):
    """Delete a recurring task."""

    def _delete(missions):
        before = len(missions)
        missions[:] = [m for m in missions if m["id"] != task_id]
        return len(missions) < before

    found = _recurring_locked_modify(state.RECURRING_FILE, _delete)
    if not found:
        return jsonify({"error": "Task not found"}), 404
    return jsonify({"ok": True})


@config_bp.route("/api/recurring/<task_id>/run", methods=["POST"])
def api_recurring_run(task_id):
    """Force-run a recurring task immediately."""
    missions = load_recurring(state.RECURRING_FILE)
    target = None
    for m in missions:
        if m["id"] == task_id:
            target = m
            break
    if target is None:
        return jsonify({"error": "Task not found"}), 404

    try:
        injected = force_run(state.RECURRING_FILE, state.MISSIONS_FILE, identifier=target["text"][:20])
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "injected": injected})
