"""Core blueprint: index, auth, status/health/forecast/provider endpoints."""
import hmac
import logging
import os
import shutil
from urllib.parse import urlparse

from flask import (
    Blueprint,
    jsonify,
    redirect,
    render_template,
    render_template_string,
    request,
    session,
    url_for,
)

from app.dashboard import state
from app.dashboard._helpers import _LOGIN_TEMPLATE
from app.dashboard_service import missions as missions_svc
from app.dashboard_service import read_file
from app.dashboard_service import stats as stats_svc
from app.missions import group_by_project

logger = logging.getLogger(__name__)

core_bp = Blueprint("core", __name__)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _safe_next_url(raw: str | None) -> str:
    """Return a same-origin relative redirect target, or the index fallback.

    Rejects absolute URLs, protocol-relative (``//evil.com``) and any target
    carrying a scheme or host so a crafted ``?next=`` cannot send an
    authenticated operator off-site (open-redirect phishing).
    """
    fallback = url_for("core.index")
    if not raw:
        return fallback
    # Reject protocol-relative and backslash-obfuscated targets outright.
    if not raw.startswith("/") or raw.startswith("//") or raw.startswith("/\\"):
        return fallback
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        return fallback
    return raw


@core_bp.route("/login", methods=["GET", "POST"])
def login():
    if not state.DASHBOARD_PWD:
        return redirect(url_for("core.index"))
    error = ""
    next_url = _safe_next_url(request.values.get("next"))
    if request.method == "POST":
        supplied = (request.form.get("passphrase") or "").strip()
        if hmac.compare_digest(supplied, state.DASHBOARD_PWD):
            session["koan_dashboard_authed"] = True
            session.permanent = True
            return redirect(next_url)
        error = "Incorrect passphrase."
    return render_template_string(_LOGIN_TEMPLATE, error=error, next_url=next_url)


@core_bp.route("/logout")
def logout():
    session.pop("koan_dashboard_authed", None)
    return redirect(url_for("core.login"))


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

def _configured_project_count() -> int:
    """Number of projects configured in projects.yaml / KOAN_PROJECTS."""
    from app.utils import get_known_projects
    try:
        return len(get_known_projects())
    except Exception:  # noqa: BLE001
        logger.warning("Failed to count configured projects; "
                       "defaulting to single-project view", exc_info=True)
        return 0


@core_bp.route("/")
def index():
    """Main dashboard page (redirects to /projects for multi-project setups).

    An explicit ``?project=`` keeps the classic single-project dashboard
    reachable on multi-project installs (e.g. the per-project links on the
    registry), so the redirect never traps users away from existing views.
    """
    selected_project = request.args.get("project", "")
    if not selected_project and _configured_project_count() >= 2:
        return redirect(url_for("projects.projects_page"))
    agent_state = stats_svc.get_agent_state()
    missions = missions_svc.parse_missions()
    filtered = missions_svc.filter_missions_by_project(missions, selected_project)

    # Per-project stats for multi-project summary
    project_stats = {}
    projects_list = missions_svc.get_all_project_names()
    if len(projects_list) > 1:
        by_project = group_by_project(read_file(state.MISSIONS_FILE))
        for pname, pdata in by_project.items():
            project_stats[pname] = {
                "pending": len(pdata["pending"]),
                "in_progress": len(pdata["in_progress"]),
            }

    # Map structured state to the template's existing state vocabulary
    tpl_state = agent_state["state"]
    if tpl_state in ("working", "contemplating", "error_recovery"):
        tpl_state = "running"
    elif tpl_state == "sleeping":
        tpl_state = "running"

    # Per-project skill metrics (plan approval + CI pass rates)
    skill_metrics = stats_svc.compute_dashboard_skill_metrics(selected_project)

    return render_template("dashboard.html",
        state=tpl_state,
        state_label=agent_state["label"],
        agent_state=agent_state,
        signals=stats_svc.get_signal_status(),
        missions=filtered,
        pending_count=len(filtered["pending"]),
        in_progress_count=len(filtered["in_progress"]),
        done_count=len(filtered["done"]),
        selected_project=selected_project,
        project_stats=project_stats,
        skill_metrics=skill_metrics,
    )


# ---------------------------------------------------------------------------
# Status / forecast
# ---------------------------------------------------------------------------

@core_bp.route("/api/forecast")
def api_forecast():
    """Return burn-rate and quota forecast as JSON."""
    return jsonify(stats_svc.build_forecast())


@core_bp.route("/api/status")
def api_status():
    """JSON status endpoint."""
    signals = stats_svc.get_signal_status()
    missions = missions_svc.parse_missions()
    return jsonify({
        "signals": signals,
        "missions": {
            "pending": len(missions["pending"]),
            "in_progress": len(missions["in_progress"]),
            "done": len(missions["done"]),
        },
        "agent_state": stats_svc.get_agent_state(),
    })


@core_bp.route("/api/provider")
def api_provider():
    """Return active CLI provider and resolved model config."""
    try:
        from app.provider import get_provider_name
        provider = get_provider_name()
    except Exception:
        logger.warning("provider lookup failed", exc_info=True)
        provider = "unknown"
    try:
        from app.config import get_model_config
        models = get_model_config()
    except Exception:
        logger.warning("model config lookup failed", exc_info=True)
        models = {}
    slot_order = ["mission", "chat", "lightweight", "fallback", "review_mode", "reflect"]
    model_list = []
    for slot in slot_order:
        value = models.get(slot, "")
        model_list.append({"slot": slot, "model": value or "(provider default)"})
    return jsonify({"provider": provider, "models": model_list})


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def _check_process_alive(koan_root, process_name: str) -> dict:
    """Check whether a Kōan process is alive via its PID file."""
    from app.signals import pid_file
    pid_path = koan_root / pid_file(process_name)
    if not pid_path.exists():
        return {"alive": False, "status": "warn"}
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)  # signal 0: existence check only
        return {"alive": True, "status": "ok"}
    except (ValueError, OSError, ProcessLookupError, PermissionError):
        return {"alive": False, "status": "warn"}


@core_bp.route("/api/health")
def api_health():
    """Aggregate health check: disk usage + process liveness."""
    # Disk
    try:
        usage = shutil.disk_usage(str(state.KOAN_ROOT))
        used_pct = int(usage.used * 100 / usage.total) if usage.total else 0
        if used_pct >= state._DISK_ERROR_PCT:
            disk_status = "error"
        elif used_pct >= state._DISK_WARN_PCT:
            disk_status = "warn"
        else:
            disk_status = "ok"
        disk = {"used_pct": used_pct, "status": disk_status}
    except OSError:
        disk = {"used_pct": None, "status": "error"}

    run_health = _check_process_alive(state.KOAN_ROOT, "run")
    awake_health = _check_process_alive(state.KOAN_ROOT, "awake")

    # Isolate the health endpoint from memory-status failures: a broken
    # snapshot must not take down liveness reporting.
    try:
        from app.memory_monitor import get_memory_status
        memory = get_memory_status(state.KOAN_ROOT)
    except Exception as exc:  # pragma: no cover - defensive
        # Return a schema-consistent block, not a bare {"error": ...}: a missing
        # watchdog_enabled reads as falsy and is indistinguishable from an
        # intentionally-disabled watchdog. Flag the failure explicitly.
        memory = {
            "config_error": True,
            "watchdog_enabled": None,
            "threshold_mb": None,
            "source": "unknown",
            "error": str(exc),
        }

    return jsonify({
        "disk": disk,
        "run": run_health,
        "awake": awake_health,
        "memory": memory,
    })
