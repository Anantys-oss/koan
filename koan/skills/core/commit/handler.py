"""Kōan commit skill -- queue a mission to commit changes."""


def handle(ctx):
    """Handle /commit command -- queue a mission to commit current changes.

    Usage:
        /commit                     -- auto-generate commit message from diff
        /commit <hint>              -- guide the commit message with a hint
        /commit <project> <hint>    -- commit in a specific project
    """
    args = ctx.args.strip()

    if not args:
        return _queue_commit(ctx, project=None, hint="")

    # Check if first word is a project name
    project, hint = _parse_project_arg(args)
    return _queue_commit(ctx, project=project, hint=hint)


def _parse_project_arg(args):
    """Parse optional project prefix from args.

    Supports:
        /commit koan Fix the bug        -> ("koan", "Fix the bug")
        /commit [project:koan] Fix bug  -> ("koan", "Fix bug")
        /commit Fix the bug             -> (None, "Fix the bug")
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


def _queue_commit(ctx, project, hint):
    """Queue a commit mission."""
    from app.utils import insert_pending_mission, get_known_projects

    # Resolve project path
    if project:
        known = get_known_projects()
        found = False
        for name, _ in known:
            if name.lower() == project.lower():
                found = True
                break
        if not found:
            names = ", ".join(n for n, _ in known) or "none"
            return f"Project '{project}' not found. Known: {names}"
        project_label = project
    else:
        project_label = _default_project()

    mission_text = "/commit"
    if hint:
        mission_text += f" {hint}"

    if project_label:
        mission_entry = f"- [project:{project_label}] {mission_text}"
    else:
        mission_entry = f"- {mission_text}"

    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    label = f" (project: {project_label})" if project_label else ""
    if hint:
        return f"Commit queued: {hint[:100]}{'...' if len(hint) > 100 else ''}{label}"
    return f"Commit queued{label}"


def _default_project():
    """Get the default project name (first known project)."""
    from app.utils import get_known_projects

    projects = get_known_projects()
    if projects:
        return projects[0][0]
    return None
