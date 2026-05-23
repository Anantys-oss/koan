"""Kōan plan skill -- queue a plan mission."""

from app.github_url_parser import search_issue_url


def handle(ctx):
    """Handle /plan command -- queue a mission to generate a plan.

    Usage:
        /plan                              -- usage help
        /plan <idea>                       -- plan for default project
        /plan <project> <idea>             -- plan for a specific project
        /plan <github-issue-url>           -- iterate on existing issue

    Queues a mission that invokes Claude to deep-think the idea,
    explore the codebase, and post a structured plan as a GitHub issue.
    """
    args = ctx.args.strip()

    if not args:
        return (
            "Usage:\n"
            "  /plan <idea> -- plan for default project\n"
            "  /plan <project> <idea> -- plan for a specific project\n"
            "  /plan <github-issue-url> -- iterate on an existing issue\n\n"
            "Queues a mission that generates a structured plan with "
            "implementation steps, corner cases, and open questions. "
            "Posts to GitHub as an issue."
        )

    # Mode 1: existing GitHub issue URL
    try:
        owner, repo, issue_number = search_issue_url(args)
        return _queue_issue_plan(ctx, owner, repo, issue_number)
    except ValueError:
        pass

    # Mode 2: new idea (optionally project-prefixed)
    project, idea = _parse_project_arg(args)

    if not idea:
        return "Please provide an idea to plan. Ex: /plan Add dark mode to the dashboard"

    return _queue_new_plan(ctx, project, idea)


def _parse_project_arg(args):
    """Parse optional project prefix from args.

    Supports:
        /plan koan Fix the bug        -> ("koan", "Fix the bug")
        /plan [project:koan] Fix bug  -> ("koan", "Fix bug")
        /plan Fix the bug             -> (None, "Fix the bug")
    """
    from app.utils import parse_project, get_known_projects

    # Try [project:X] tag first
    project, cleaned = parse_project(args)
    if project:
        return project, cleaned

    # Try first word as project name
    parts = args.split(None, 1)
    if len(parts) < 2:
        return None, args

    candidate = parts[0].lower()
    known = get_known_projects()
    for name, _ in known:
        if name.lower() == candidate:
            return name, parts[1]

    return None, args


def _resolve_project_path(project_name, fallback=False, owner=None):
    """Resolve project name to its local path.

    Delegates to utils.resolve_project_path() for name/owner matching,
    but manages its own fallback logic (return first project if nothing matches).
    """
    from pathlib import Path
    from app.utils import get_known_projects, resolve_project_path

    if project_name:
        if owner:
            path = resolve_project_path(project_name, owner=owner)
            if path:
                return path
        for name, path in get_known_projects():
            if name.lower() == project_name.lower():
                return path
        for name, path in get_known_projects():
            if Path(path).name.lower() == project_name.lower():
                return path
        if not fallback:
            return None

    projects = get_known_projects()
    if projects:
        return projects[0][1]

    return ""


def _queue_new_plan(ctx, project_name, idea):
    """Queue a mission to generate a plan for a new idea."""
    from app.utils import insert_pending_mission

    project_path = _resolve_project_path(project_name)
    if not project_path:
        from app.utils import get_known_projects
        known = ", ".join(n for n, _ in get_known_projects()) or "none"
        return f"Project '{project_name}' not found. Known: {known}"

    project_label = project_name or _project_name_for_path(project_path)

    mission_entry = f"- [project:{project_label}] /plan {idea}"
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    return f"\U0001f9e0 Plan queued: {idea[:100]}{'...' if len(idea) > 100 else ''} (project: {project_label})"


def _queue_issue_plan(ctx, owner, repo, issue_number):
    """Queue a mission to iterate on an existing GitHub issue."""
    from app.utils import insert_pending_mission

    issue_url = f"https://github.com/{owner}/{repo}/issues/{issue_number}"

    project_path = _resolve_project_path(repo, fallback=True, owner=owner)
    project_label = _project_name_for_path(project_path) if project_path else repo

    mission_entry = f"- [project:{project_label}] /plan {issue_url}"
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    return f"\U0001f4d6 Plan queued for issue #{issue_number} ({owner}/{repo})"


def _project_name_for_path(project_path):
    """Get project name from path, checking known projects first."""
    from app.utils import project_name_for_path
    return project_name_for_path(project_path)
