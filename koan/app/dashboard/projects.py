"""Projects blueprint: registry page, per-project status, add-project."""
import logging
import threading

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
    # The add_project skill clones (and may fork) the repo — up to ~120s of
    # blocking I/O. Run it off the request thread so the dashboard stays
    # responsive; surface the outcome to the outbox (Telegram) and the log
    # since we can no longer report it inline.
    threading.Thread(target=_run_add_skill, args=(args,), daemon=True).start()
    return redirect(url_for("projects.projects_page", add_started=1))


def _run_add_skill(args: str) -> tuple:
    """Run the add_project skill in-process. Returns (ok, message).

    Runs on a background thread; the outcome is reported via the outbox and
    the log rather than the HTTP response, so failures are never silent.
    """
    parts = []
    try:
        from app.bridge_state import _get_registry
        from app.skills import SkillContext, execute_skill

        registry = _get_registry()
        skill = registry.find_by_command("add_project")
        if skill is None:
            _report(False, "add_project skill not found")
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
        _report(False, f"add_project failed: {e}")
        return False, f"Error: {e}"
    message = "\n".join(parts).strip()
    _report(True, message)
    return True, message


def _report(ok: bool, message: str) -> None:
    """Surface an async add_project outcome to the log and the outbox."""
    if ok:
        logger.info("add_project: %s", message)
    else:
        logger.warning("add_project failed: %s", message)
    try:
        from app.utils import append_to_outbox

        prefix = "✅ Add project:" if ok else "⚠️ Add project failed:"
        append_to_outbox(state.INSTANCE_DIR / "outbox.md",
                         f"{prefix} {message}".strip())
    except Exception:  # noqa: BLE001 - outbox notification is best-effort
        logger.exception("Failed to write add_project outcome to outbox")
