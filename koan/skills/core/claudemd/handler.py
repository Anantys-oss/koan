"""Kōan claudemd skill -- queue a CLAUDE.md refresh mission."""


def handle(ctx):
    """Handle /claudemd <project-name> command.

    Queues a mission that updates or creates CLAUDE.md for the specified
    project, focusing on architecturally significant changes.
    """
    from app.utils import get_known_projects, insert_pending_mission, resolve_project_from_list

    args = ctx.args.strip()

    if not args:
        return (
            "Usage: /claudemd <project-name> [learnings]\n\n"
            "Refreshes the CLAUDE.md file for a project based on recent "
            "architectural changes.\n"
            "If CLAUDE.md doesn't exist, creates one from scratch.\n\n"
            "Add 'learnings' to instead distill Kōan's per-project learnings "
            "into a managed block in CLAUDE.md.\n\n"
            "Examples: /claudemd koan  ·  /claudemd koan learnings"
        )

    # Extract project name (first word)
    project_name = args.split()[0]

    # Resolve project path
    known = get_known_projects()
    matched_name, _ = resolve_project_from_list(known, project_name)

    if not matched_name:
        names = ", ".join(n for n, _ in known) or "none"
        return f"Project '{project_name}' not found. Known projects: {names}"

    # Detect the optional `learnings` sub-argument (case-insensitive).
    is_learnings = any(w.lower() == "learnings" for w in args.split()[1:])
    suffix = " learnings" if is_learnings else ""

    # Queue the mission with clean format
    mission_entry = f"- [project:{matched_name}] /claudemd {matched_name}{suffix}"
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    if is_learnings:
        return f"CLAUDE.md learnings-sync queued for project {matched_name}"
    return f"CLAUDE.md refresh queued for project {matched_name}"
