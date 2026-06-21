"""Koan /spec_audit skill -- queue a spec-drift detection mission."""


def handle(ctx):
    """Handle /spec_audit command -- queue a spec-drift scan.

    Usage:
        /spec_audit              -- scan the default project
        /spec_audit <project>    -- scan a specific project
    """
    args = ctx.args.strip()

    if args in ("-h", "--help"):
        return (
            "Usage: /spec_audit [project-name]\n\n"
            "Checks that documentation (user-manual.md, github-commands.md, "
            "skills.md, CLAUDE.md) stays in sync with the actual codebase.\n"
            "Produces a divergence report and queues fix missions.\n\n"
            "Examples:\n"
            "  /spec_audit koan\n"
            "  /sa"
        )

    project_name = args.split()[0] if args else None

    return _queue_spec_audit(ctx, project_name)


def _queue_spec_audit(ctx, project_name):
    """Queue a spec-drift detection mission."""
    from app.utils import (
        insert_pending_mission, resolve_project_name_and_path,
    )

    if project_name:
        project_name, path = resolve_project_name_and_path(project_name)
        if not path:
            from app.utils import get_known_projects

            known = ", ".join(n for n, _ in get_known_projects()) or "none"
            return (
                f"\u274c Unknown project '{project_name}'.\n"
                f"Known projects: {known}"
            )
    else:
        from app.utils import get_known_projects

        projects = get_known_projects()
        if not projects:
            return "\u274c No projects configured."
        project_name = projects[0][0]

    insert_pending_mission("/spec_audit", project_name)

    return f"\U0001f4d0 Spec-drift audit queued for {project_name}"
