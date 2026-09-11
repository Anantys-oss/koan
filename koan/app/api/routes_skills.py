"""REST API skill-catalogue route."""

from flask import Blueprint, jsonify

from app.api.auth import require_token
from app.api.openapi_metadata import openapi_operation
from app.api.skill_catalog import SKILL_CATALOG

bp = Blueprint("skills", __name__)


@bp.route("/v1/skills", methods=["GET"])
@openapi_operation(
    mcp=True,
    mcp_description=(
        "Use this to discover API-exposed slash commands, their usage, "
        "aliases, and flags before queueing a skill-backed mission."
    ),
)
@require_token
def list_skills_route():
    """List API-exposed core skills.

    Returns only skills that explicitly opt into remote discovery.
    """
    return jsonify(SKILL_CATALOG)
