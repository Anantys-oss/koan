"""PRs blueprint: open-PR tracking + GitHub-backed plan issue viewer."""
import json
import time

from flask import Blueprint, jsonify, render_template, request

from app.dashboard import state
from app.dashboard_service import plans as plans_svc

prs_bp = Blueprint("prs", __name__)


# ---------------------------------------------------------------------------
# PRs
# ---------------------------------------------------------------------------

@prs_bp.route("/prs")
def prs_page():
    """PR tracking page — open PRs across all projects."""
    return render_template("prs.html")


@prs_bp.route("/api/prs")
def api_prs():
    """JSON endpoint for open PRs across projects."""
    from app.pr_tracker import fetch_all_prs

    project = request.args.get("project", "")
    author_only = request.args.get("author_only", "true").lower() != "false"
    data = fetch_all_prs(str(state.KOAN_ROOT), project_filter=project,
                         author_only=author_only)
    return jsonify(data)


@prs_bp.route("/api/prs/<project>/<int:number>/checks")
def api_pr_checks(project, number):
    """Fetch CI checks for a specific PR."""
    from app.pr_tracker import fetch_pr_checks

    checks = fetch_pr_checks(project, number, str(state.KOAN_ROOT))
    return jsonify({"checks": checks})


@prs_bp.route("/api/prs/<project>/<int:number>/merge", methods=["POST"])
def api_pr_merge(project, number):
    """Merge a PR (requires auto-merge enabled for the project)."""
    from app.pr_tracker import merge_pr

    result = merge_pr(project, number, str(state.KOAN_ROOT))
    status_code = 200 if result["ok"] else 400
    return jsonify(result), status_code


# ---------------------------------------------------------------------------
# Plans — GitHub-backed plan issue viewer
# ---------------------------------------------------------------------------

@prs_bp.route("/plans")
def plans_page():
    """Plans viewer page — plan issues across all projects."""
    return render_template("plans.html")


@prs_bp.route("/api/plans")
def api_plans():
    """JSON endpoint returning plan issues across all projects."""
    from app.utils import get_known_projects

    project_filter = request.args.get("project", "")
    force_refresh = request.args.get("force", "") == "1"
    now = time.time()
    all_plans = []
    errors = []

    known = get_known_projects()
    for project_name, _path in known:
        if project_filter and project_name != project_filter:
            continue

        cache_key = f"plans:{project_name}"
        if not force_refresh and cache_key in state._plans_cache:
            cached_ts, cached_data = state._plans_cache[cache_key]
            if now - cached_ts < state._PLANS_CACHE_TTL:
                all_plans.extend(cached_data)
                continue

        repo = plans_svc.get_project_repo(project_name)
        if not repo:
            continue

        plans = plans_svc.fetch_plans_for_project(project_name, repo)
        state._plans_cache[cache_key] = (now, plans)
        all_plans.extend(plans)

    # Sort by updatedAt descending
    all_plans.sort(key=lambda p: p.get("updatedAt", ""), reverse=True)

    return jsonify({"plans": all_plans, "errors": errors})


@prs_bp.route("/api/plans/<project>/<int:number>")
def api_plan_detail(project, number):
    """Single plan detail — full body + latest iteration (last comment)."""
    from app.github import run_gh

    repo = plans_svc.get_project_repo(project)
    if not repo:
        return jsonify({"error": f"No github_url configured for project {project!r}"}), 404

    # Fetch issue with all comments
    try:
        raw = run_gh(
            "issue", "view", str(number),
            "--repo", repo,
            "--json", "number,title,state,body,url,updatedAt,comments",
            timeout=30,
        )
        issue = json.loads(raw) if raw else {}
    except (RuntimeError, json.JSONDecodeError, OSError) as e:
        return jsonify({"error": str(e)}), 502

    body = issue.get("body") or ""
    comments = issue.get("comments") or []

    # Latest iteration: last comment body if exists, else issue body
    latest_body = comments[-1].get("body", body) if comments else body

    # Linked missions: search missions.md for the issue URL
    issue_url = issue.get("url", "")
    linked_missions = plans_svc.find_linked_missions(issue_url, number)

    progress = plans_svc.parse_plan_progress(latest_body)

    return jsonify({
        "number": issue.get("number"),
        "title": issue.get("title", ""),
        "state": issue.get("state", "open"),
        "url": issue_url,
        "updatedAt": issue.get("updatedAt", ""),
        "body": body,
        "latest_body": latest_body,
        "comments": [{"body": c.get("body", ""), "createdAt": c.get("createdAt", "")} for c in comments],
        "progress": progress,
        "project": project,
        "repo": repo,
        "linked_missions": linked_missions,
    })
