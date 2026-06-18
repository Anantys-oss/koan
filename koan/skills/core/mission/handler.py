"""Kōan mission skill — create missions."""


def handle(ctx):
    """Handle /mission <text> command."""
    from app.utils import (
        parse_project as _parse_project,
        detect_project_from_text,
        insert_pending_mission,
        get_known_projects,
    )
    from app.missions import extract_now_flag

    raw_args = ctx.args.strip()
    if not raw_args:
        return (
            "Usage: /mission <description>\n\n"
            "Examples:\n"
            "  /mission fix the login bug\n"
            "  /mission --now urgent hotfix\n"
            "  /mission [project:koan] add retry logic\n"
            "  /mission koan add retry logic"
        )

    # Check for --now flag in first 5 words (queue at top instead of bottom)
    urgent, raw_args = extract_now_flag(raw_args)

    # Check for explicit [project:name] tag first
    project, mission_text = _parse_project(raw_args)

    # Auto-detect project from first word (e.g. "/mission koan do something")
    if not project:
        project, detected_text = detect_project_from_text(raw_args)
        if project:
            mission_text = detected_text

    if not project:
        known = get_known_projects()
        if len(known) > 1 and not urgent:
            project_list = "\n".join(f"  - {name}" for name, _path in known)
            first_name = known[0][0]
            return (
                f"Which project for this mission?\n\n"
                f"{project_list}\n\n"
                f"Reply with the tag, e.g.:\n"
                f"  /mission [project:{first_name}] {raw_args[:80]}"
            )

    # Clean up mission prefix
    if mission_text.lower().startswith("mission:"):
        mission_text = mission_text[8:].strip()
    elif mission_text.lower().startswith("mission :"):
        mission_text = mission_text[9:].strip()

    insert_pending_mission(mission_text, project, urgent=urgent)

    ack = "✅ Mission received"
    if urgent:
        ack += " (priority)"
    if project:
        ack += f" (project: {project})"
    ack += f":\n\n{mission_text[:500]}"
    return ack
