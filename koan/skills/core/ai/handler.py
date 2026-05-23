"""Koan /ai skill -- queue an AI exploration mission."""

import random
import re
from pathlib import Path
from typing import List, Tuple

from app.project_explorer import get_projects

# Matches --issues flag in the command args
_ISSUES_RE = re.compile(r"--issues\b", re.IGNORECASE)


def _extract_issues_flag(text):
    """Extract --issues flag from text.

    Returns (True/False, cleaned_text).
    """
    m = _ISSUES_RE.search(text)
    if not m:
        return False, text
    cleaned = (text[:m.start()] + text[m.end():]).strip()
    cleaned = re.sub(r"  +", " ", cleaned)
    return True, cleaned


def handle(ctx):
    """Handle /ai command -- queue an AI exploration mission.

    Usage:
        /ai [project] [focus context] [--issues]

    Queues a mission that explores a project in depth via a dedicated
    CLI runner (app.ai_runner), gathers git context, and suggests
    creative improvements.

    --issues: Create GitHub issues for high-impact findings.
    """
    projects = get_projects()
    if not projects:
        return "No projects configured."

    # Extract flags before splitting
    args = ctx.args.strip() if ctx.args else ""
    create_issues, args = _extract_issues_flag(args)

    # Pick project: from args or random, rest is focus context
    parts = args.split(None, 1)
    target = parts[0].lower() if parts else ""
    focus_context = parts[1] if len(parts) > 1 else ""

    name, path = _resolve_project(projects, target)
    if name is None:
        known = ", ".join(n for n, _ in projects)
        return f"Unknown project '{target}'. Known: {known}"

    # Queue the mission with clean format
    from app.utils import insert_pending_mission

    context_suffix = f" {focus_context}" if focus_context else ""
    issues_suffix = " --issues" if create_issues else ""
    mission_entry = f"- [project:{name}] /ai {name}{context_suffix}{issues_suffix}"
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    context_hint = f" (focus: {focus_context})" if focus_context else ""
    issues_hint = ", with GitHub issues" if create_issues else ""
    return f"AI exploration queued for {name}{context_hint}{issues_hint}"


def _resolve_project(
    projects: List[Tuple[str, str]], target: str
) -> Tuple[str, str]:
    """Resolve a project by name or pick random.

    Returns (name, path) or (None, None) if target not found.
    """
    if not target:
        return random.choice(projects)

    for name, path in projects:
        if name.lower() == target:
            return name, path

    return None, None
