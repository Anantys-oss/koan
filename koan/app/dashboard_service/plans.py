"""Plan-issue fetching and progress parsing (no Flask)."""
import json
import re

from app.dashboard import state
from app.dashboard_service import read_file


def parse_plan_progress(markdown: str) -> dict:
    """Extract phase list and completion status from plan markdown.

    Plans follow a strict format with ``#### Phase N: Title`` headings.
    Completion is detected by ✅, [x]/[X], or "Done" markers in phase content.

    Returns a dict with keys:
        phases: list of {"title": str, "completed": bool}
        completed: int
        total: int
        percent: int
    """
    if not markdown:
        return {"phases": [], "completed": 0, "total": 0, "percent": 0}

    # Split markdown into lines for phase-aware parsing
    lines = markdown.splitlines()
    phases = []
    current_phase = None
    current_lines: list = []

    _phase_heading = re.compile(r'^####\s+Phase\s+\d+[:\s](.+)', re.IGNORECASE)
    # "Done" matches as completion only when NOT followed by "when" (avoids "Done when:" field)
    _done_marker = re.compile(r'✅|\[x\]|\bDone\b(?!\s+when)', re.IGNORECASE)

    def _finalize_phase(phase, content_lines):
        content = '\n'.join(content_lines)
        completed = bool(_done_marker.search(content))
        phases.append({"title": phase, "completed": completed})

    for line in lines:
        m = _phase_heading.match(line)
        if m:
            if current_phase is not None:
                _finalize_phase(current_phase, current_lines)
            current_phase = m.group(1).strip()
            current_lines = []
        elif current_phase is not None:
            current_lines.append(line)

    if current_phase is not None:
        _finalize_phase(current_phase, current_lines)

    completed = sum(1 for p in phases if p["completed"])
    total = len(phases)
    percent = int(completed / total * 100) if total else 0
    return {"phases": phases, "completed": completed, "total": total, "percent": percent}


def get_project_repo(project_name: str) -> str | None:
    """Return owner/repo string for a project, or None if not available."""
    from app.projects_config import get_project_config, load_projects_config
    from app.github_url_parser import parse_github_url

    projects_cfg = load_projects_config(str(state.KOAN_ROOT))
    if projects_cfg is None:
        return None
    config = get_project_config(projects_cfg, project_name)
    github_url = config.get("github_url", "")
    if not github_url:
        return None
    try:
        owner, repo, _, _ = parse_github_url(github_url + "/issues/1")
        return f"{owner}/{repo}"
    except ValueError:
        # github_url may already be owner/repo or just a base URL
        # Try parsing as base URL: https://github.com/owner/repo
        m = re.match(r'https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$', github_url)
        if m:
            return f"{m.group(1)}/{m.group(2)}"
    return None


def fetch_plans_for_project(project_name: str, repo: str) -> list:
    """Fetch open plan issues for a project via gh CLI."""
    from app.github import run_gh

    try:
        raw = run_gh(
            "search", "issues",
            "--repo", repo,
            "--label", "plan",
            "--state", "open",
            "--json", "number,title,state,body,updatedAt,url",
            "--limit", "50",
            timeout=30,
        )
        issues = json.loads(raw) if raw else []
    except (RuntimeError, json.JSONDecodeError, OSError):
        return []

    result = []
    for issue in issues:
        body = issue.get("body") or ""
        progress = parse_plan_progress(body)
        result.append({
            "number": issue.get("number"),
            "title": issue.get("title", ""),
            "state": issue.get("state", "open"),
            "url": issue.get("url", ""),
            "updatedAt": issue.get("updatedAt", ""),
            "body": body,
            "progress": progress,
            "project": project_name,
            "repo": repo,
        })
    return result


def find_linked_missions(issue_url: str, issue_number: int) -> list:
    """Find missions that reference the given plan issue URL or number."""
    content = read_file(state.MISSIONS_FILE)
    if not content:
        return []

    linked = []
    issue_number_str = f"#{issue_number}"
    for line in content.splitlines():
        stripped = line.strip().lstrip("- ~")
        if issue_url and issue_url in line:
            linked.append(stripped)
        elif issue_number_str in line and "/plan" in line.lower():
            linked.append(stripped)
    return linked[:20]  # cap to avoid huge responses
