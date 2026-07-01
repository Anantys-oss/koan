"""REST API observability routes: usage, metrics, and log tails."""

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from app.api.auth import require_token

bp = Blueprint("observability", __name__)


def _instance_dir() -> Path:
    return current_app.config["INSTANCE_DIR"]


def _koan_root() -> Path:
    return current_app.config["KOAN_ROOT"]


class _BadParam(ValueError):
    """Raised when a query param fails to parse as an integer."""


def _int_param(name: str, default: str) -> int:
    """Parse an integer query param, raising _BadParam on malformed input."""
    raw = request.args.get(name, default)
    try:
        return int(raw)
    except (ValueError, TypeError):
        raise _BadParam(f"'{name}' must be an integer, got {raw!r}")


@bp.route("/v1/usage")
@require_token
def usage():
    from app.usage_service import build_usage_payload

    try:
        days = _int_param("days", "7")
        offset = _int_param("offset", "0")
    except _BadParam as e:
        return jsonify({"error": {"code": "invalid_request", "message": str(e)}}), 422
    stacked = request.args.get("stacked", "false").lower() in ("true", "1", "yes")
    return jsonify(build_usage_payload(
        _instance_dir(),
        days=days,
        project=request.args.get("project", ""),
        granularity=request.args.get("granularity", "day"),
        stacked=stacked,
        offset=offset,
    ))


@bp.route("/v1/metrics")
@require_token
def metrics():
    from app.mission_metrics import (
        compute_global_metrics,
        compute_project_metrics,
        compute_project_trend,
    )

    try:
        days = max(0, min(_int_param("days", "30"), 365))
    except _BadParam as e:
        return jsonify({"error": {"code": "invalid_request", "message": str(e)}}), 422
    project = request.args.get("project", "")
    instance = str(_instance_dir())

    if project:
        data = compute_project_metrics(instance, project, days=days)
        data["trend"] = compute_project_trend(instance, project, days=days)
        return jsonify(data)

    data = compute_global_metrics(instance, days=days)
    for proj, pdata in data.get("by_project", {}).items():
        if isinstance(pdata, dict):
            pdata["trend"] = compute_project_trend(instance, proj, days=days)
    from app.security_review import count_security_blocks
    data["security_blocks_7d"] = count_security_blocks(instance, days=7)
    return jsonify(data)


@bp.route("/v1/logs")
@require_token
def logs():
    from app.log_reader import LOG_DEFAULT_LIMIT, read_logs

    source = request.args.get("source", "all")
    try:
        limit = _int_param("limit", str(LOG_DEFAULT_LIMIT))
    except _BadParam as e:
        return jsonify({"error": {"code": "invalid_request", "message": str(e)}}), 422
    q = request.args.get("q", "")
    return jsonify(read_logs(_koan_root(), source=source, limit=limit, q=q))
