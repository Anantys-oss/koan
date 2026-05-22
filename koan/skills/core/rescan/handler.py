"""Kōan rescan skill — detect remote HEAD changes and update workspaces."""

import os


def handle(ctx):
    """Handle /rescan command."""
    from app.head_tracker import check_all_projects, format_changes_report
    from app.utils import get_known_projects

    koan_root = os.environ.get("KOAN_ROOT", "")
    instance_dir = os.path.join(koan_root, "instance")
    projects = get_known_projects()

    if not projects:
        return "No projects configured."

    changes = check_all_projects(
        projects, instance_dir, koan_root, force=True
    )

    if not changes:
        return f"Scanned {len(projects)} project(s) — all remote HEADs match local tracking."

    return format_changes_report(changes)
