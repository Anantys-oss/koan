"""Koan /doc skill -- queue a documentation extraction mission."""


def handle(ctx):
    """Handle /doc command -- queue a documentation extraction mission.

    Usage:
        /doc <project>                        -- generate all doc categories
        /doc <project> architecture,test-style -- specific categories only
        /doc <project> --mode=update           -- merge into existing docs
        /doc <project> --mode=replace          -- overwrite existing docs
    """
    args = ctx.args.strip()

    if args in ("-h", "--help"):
        return (
            "Usage: /doc <project-name> [categories] [--mode=create|update|replace]\n\n"
            "Investigates a project codebase and produces structured documentation\n"
            "under the project's docs/ directory.\n\n"
            "Categories: architecture, code-style, test-style, anti-patterns, modules\n"
            "(comma-separated, default: all)\n\n"
            "Modes:\n"
            "  create   — skip existing files (default)\n"
            "  update   — merge new sections into existing files\n"
            "  replace  — overwrite existing files entirely\n\n"
            "Examples:\n"
            "  /doc koan\n"
            "  /docs koan architecture,test-style\n"
            "  /doc webapp --mode=update"
        )

    if not args:
        return "\u274c Usage: /doc <project-name> [categories] [--mode=create|update|replace]"

    # Extract --mode flag
    mode = "create"
    mode_flags = ("--mode=create", "--mode=update", "--mode=replace")
    for flag in mode_flags:
        if flag in args:
            mode = flag.split("=")[1]
            args = args.replace(flag, "").strip()
            break

    # Parse project name and optional categories
    parts = args.split(None, 1)
    project_name = parts[0]
    categories = parts[1] if len(parts) > 1 else ""

    return _queue_doc(ctx, project_name, categories, mode)


def _queue_doc(ctx, project_name, categories, mode):
    """Queue a documentation extraction mission."""
    from app.utils import (
        insert_pending_mission, resolve_project_name_and_path,
    )

    project_name, path = resolve_project_name_and_path(project_name)
    if not path:
        from app.utils import get_known_projects

        known = ", ".join(n for n, _ in get_known_projects()) or "none"
        return (
            f"\u274c Unknown project '{project_name}'.\n"
            f"Known projects: {known}"
        )

    suffix = ""
    if categories:
        suffix += f" {categories}"
    if mode != "create":
        suffix += f" --mode={mode}"

    mission_entry = f"- [project:{project_name}] /doc{suffix}"
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    cat_text = categories if categories else "all"
    return f"\U0001f4da Documentation extraction queued for {project_name} (categories: {cat_text}, mode: {mode})"
