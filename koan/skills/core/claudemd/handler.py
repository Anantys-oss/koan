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
            "Usage: /claudemd <project-name>\n\n"
            "Refreshes the CLAUDE.md file for a project based on recent "
            "architectural changes.\n"
            "If CLAUDE.md doesn't exist, creates one from scratch.\n\n"
            "Example: /claudemd koan"
        )

    # Extract project name (first word)
    project_name = args.split()[0]

    # Resolve project path
    known = get_known_projects()
    matched_name, _ = resolve_project_from_list(known, project_name)

    if not matched_name:
        names = ", ".join(n for n, _ in known) or "none"
        return f"Project '{project_name}' not found. Known projects: {names}"

    # Queue the mission with clean format
    insert_pending_mission(f"/claudemd {matched_name}", matched_name)

    return f"CLAUDE.md refresh queued for project {matched_name}"
