"""Koan /deep skill -- queue a deep exploration mission."""

import random
from typing import List, Optional, Tuple

from app.project_explorer import get_projects
from app.utils import resolve_project_alias


def handle(ctx):
    """Handle /deep command -- queue a deep exploration mission.

    Usage:
        /deep [project] [focus context]

    Queues a mission that runs a thorough autonomous exploration of a
    project via a dedicated CLI runner (deep_runner), with full tool
    access and higher turn limits than /ai.
    """
    projects = get_projects()
    if not projects:
        return "No projects configured."

    args = ctx.args.strip() if ctx.args else ""
    parts = args.split(None, 1)
    target = parts[0].lower() if parts else ""
    focus_context = parts[1] if len(parts) > 1 else ""

    name, path = _resolve_project(projects, target)
    if name is None:
        known = ", ".join(n for n, _ in projects)
        return f"Unknown project '{target}'. Known: {known}"

    from app.utils import insert_pending_mission

    context_suffix = f" {focus_context}" if focus_context else ""
    mission_entry = f"- [project:{name}] /deep {name}{context_suffix}"
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    context_hint = f" (focus: {focus_context})" if focus_context else ""
    return f"🧠 Deep exploration queued for {name}{context_hint}"


def _resolve_project(
    projects: List[Tuple[str, str]], target: str
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve a project by name or pick random."""
    if not target:
        return random.choice(projects)

    for name, path in projects:
        if name.lower() == target:
            return name, path

    canonical = resolve_project_alias(target)
    if canonical:
        for name, path in projects:
            if name.lower() == canonical.lower():
                return name, path

    return None, None
