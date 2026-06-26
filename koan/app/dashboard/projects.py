"""Projects blueprint: registry page, per-project status, add-project."""
import logging

from flask import (
    Blueprint,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from app.dashboard import state
from app.dashboard_service import projects as proj_svc
from app.dashboard_service import stats as stats_svc

logger = logging.getLogger(__name__)

projects_bp = Blueprint("projects", __name__)


@projects_bp.route("/projects")
def projects_page():
    """Project registry / welcome screen."""
    registry = proj_svc.build_project_registry()
    return render_template(
        "projects.html",
        projects=registry,
        signals=stats_svc.get_signal_status(),
        agent_state=stats_svc.get_agent_state(),
    )


@projects_bp.route("/api/projects/<name>/status")
def api_project_status(name: str):
    """JSON card for one project (async refresh)."""
    return jsonify(proj_svc.build_project_status(name))


@projects_bp.route("/projects/add", methods=["POST"])
def add_project():
    """Add a project from the modal by running the add_project skill."""
    github_url = (request.form.get("github_url") or "").strip()
    name = (request.form.get("name") or "").strip()
    if not github_url or not github_url.startswith(("http://", "https://", "git@")):
        registry = proj_svc.build_project_registry()
        return render_template(
            "projects.html",
            projects=registry,
            signals=stats_svc.get_signal_status(),
            agent_state=stats_svc.get_agent_state(),
            add_error="Enter a valid GitHub URL (https:// or git@).",
        ), 400

    args = f"{github_url} {name}".strip()
    ok, result = _run_add_skill(args)
    if not ok:
        logger.warning("add_project skill failed: %s", result)
    return redirect(url_for("projects.projects_page"))


def _run_add_skill(args: str) -> tuple:
    """Run the add_project skill in-process. Returns (ok, message)."""
    parts = []
    try:
        from app.bridge_state import _get_registry
        from app.skills import SkillContext, execute_skill

        registry = _get_registry()
        skill = registry.find_by_command("add_project")
        if skill is None:
            return False, "add_project skill not found"
        ctx = SkillContext(
            koan_root=state.KOAN_ROOT,
            instance_dir=state.INSTANCE_DIR,
            command_name="add_project",
            args=args,
            send_message=parts.append,
        )
        out = execute_skill(skill, ctx)
        if out:
            parts.append(str(out))
    except Exception as e:  # noqa: BLE001 - best-effort skill dispatch
        return False, f"Error: {e}"
    return True, "\n".join(parts).strip()
