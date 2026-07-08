"""Operator config-form blueprint: structured per-project overrides + allow-listed config.yaml settings.

Split out of ``app.dashboard.config`` to keep each blueprint focused and under
the per-file line budget. Like ``config.py``, this module is
``app.dashboard.config_form`` and must never shadow the global ``app.config`` —
all global-config references use absolute imports.
"""
import logging

from flask import Blueprint, jsonify, request

from app.dashboard import state

config_form_bp = Blueprint("config_form", __name__)


# Dotted config.yaml keys the Settings sub-tab may edit, with value type.
EDITABLE_SETTINGS = {
    "dashboard.nickname": str,
    "git_auto_merge.enabled": bool,
    "ci_dispatch.enabled": bool,
    "review_dispatch.enabled": bool,
    "auto_update.enabled": bool,
}


@config_form_bp.route("/api/projects/<name>", methods=["GET"])
def api_project_get(name):
    """Return the editable subset of a project's merged config for the form."""
    from app.projects_config import (
        EDITABLE_PROJECT_FIELDS,
        _project_exists,
        get_project_config,
        load_projects_config,
    )
    config = load_projects_config(str(state.KOAN_ROOT))
    if not config:
        return jsonify({"ok": False, "error": "No projects.yaml"}), 404
    if not _project_exists(config.get("projects", {}), name):
        return jsonify({"ok": False, "error": f"Unknown project: {name}"}), 404
    merged = get_project_config(config, name)
    editable = {k: merged.get(k) for k in EDITABLE_PROJECT_FIELDS}
    return jsonify({"ok": True, "name": name, "config": editable})


@config_form_bp.route("/api/providers", methods=["GET"])
def api_providers():
    """Return registered CLI provider names so the form stays in sync."""
    from app.provider import known_providers
    return jsonify({"ok": True, "providers": known_providers()})


@config_form_bp.route("/api/config/settings", methods=["GET"])
def api_config_settings_get():
    """Return current values for the allow-listed Settings keys.

    Lets the Settings sub-tab hydrate its controls from the live config.yaml
    instead of rendering every toggle unchecked regardless of real state.
    """
    from app.utils import load_config
    cfg = load_config()
    values = {}
    unreadable = []
    for dotted, expected in EDITABLE_SETTINGS.items():
        node = cfg
        reachable = True
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                # Genuinely-absent key vs. a value stored in an unexpected
                # shape (e.g. `git_auto_merge: true` shorthand): the parent
                # isn't a dict, so the leaf is unreachable. Track why below.
                reachable = isinstance(node, dict)
                node = None
                break
        if node is None and reachable:
            # Leaf truly missing from an otherwise-navigable tree → default.
            values[dotted] = False if expected is bool else ""
        elif not reachable:
            # Present-but-wrong-shape: don't report a definitive False/"" that
            # could mask an enabled feature. Flag it so the UI can say so.
            values[dotted] = None
            unreadable.append(dotted)
        elif expected is bool:
            values[dotted] = bool(node)
        else:
            values[dotted] = node
    return jsonify({"ok": True, "settings": values, "unreadable": unreadable})


@config_form_bp.route("/api/config/setting", methods=["PUT"])
def api_config_setting():
    """Set one allow-listed config.yaml key, preserving comments."""
    from app.utils import update_config_yaml
    data = request.get_json(silent=True) or {}
    key, value = data.get("key"), data.get("value")
    if key not in EDITABLE_SETTINGS:
        return jsonify({"ok": False, "error": f"Setting not editable: {key}"}), 422
    expected = EDITABLE_SETTINGS[key]
    if expected is bool:
        if isinstance(value, bool):
            pass
        else:
            token = str(value).strip().lower()
            if token in ("1", "true", "yes", "on"):
                value = True
            elif token in ("0", "false", "no", "off"):
                value = False
            else:
                return jsonify({"ok": False, "error": f"Invalid value for {key}"}), 422
    else:
        if value is None:
            # A missing/null value would coerce to the literal string "None"
            # and be persisted — reject it as a malformed request instead.
            return jsonify({"ok": False, "error": f"Missing value for {key}"}), 422
        try:
            value = expected(value)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": f"Invalid value for {key}"}), 422
    try:
        update_config_yaml(state.KOAN_ROOT / "instance" / "config.yaml", key, value)
    except OSError as exc:
        return jsonify({"ok": False, "error": f"Write failed: {exc}"}), 500
    except Exception as exc:
        # A malformed config.yaml (parse/edit failure) is a client-fixable
        # condition, not a server crash — return 422 instead of a raw 500.
        # Log server-side first so genuine server faults stay visible.
        logging.exception("config.yaml edit failed for key %s", key)
        return jsonify({"ok": False, "error": f"Could not edit config.yaml: {exc}"}), 422
    return jsonify({"ok": True, "key": key, "value": value})


@config_form_bp.route("/api/projects/<name>", methods=["POST"])
def api_project_save(name):
    """Apply a validated partial patch to a project's overrides."""
    from app.projects_config import apply_project_patch
    data = request.get_json(silent=True) or {}
    patch = data.get("patch")
    if not isinstance(patch, dict):
        return jsonify({"ok": False, "error": "Missing 'patch' object"}), 400
    try:
        merged = apply_project_patch(str(state.KOAN_ROOT), name, patch)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 422
    return jsonify({"ok": True, "name": name, "config": merged})
