#!/usr/bin/env python3
"""
Kōan — FIFO mission picker

Picks the first pending mission from missions.md in strict queue order.
The human controls priority via queue position (--now flag, /priority command).

Usage:
    python3 pick_mission.py <instance_dir> <projects_str> <run_num> <autonomous_mode> [last_project]

Output (stdout):
    project_name:mission title    — if a mission is picked
    (empty)                       — if autonomous mode (no pending missions)
"""

import sys

from app.utils import PROJECT_TAG_RE, PROJECT_TAG_STRIP_RE


def fallback_extract(content: str, projects_str: str) -> tuple[str | None, str | None]:
    """Extract the first pending mission in FIFO order."""
    from app.missions import extract_next_pending

    line = extract_next_pending(content)
    if not line:
        return (None, None)

    # Try to extract project from inline tag.
    # The sentinel tag [project:all] passes through verbatim here; it is
    # resolved to the workspace root downstream (iteration_manager
    # ._resolve_project_path) so the org-wide mission runs once over all repos.
    tag = PROJECT_TAG_RE.search(line)
    if tag:
        project = tag.group(1)
        title = PROJECT_TAG_STRIP_RE.sub("", line).removeprefix("- ").strip()
    else:
        # No tag: default to the first project (intentional for single-project
        # setups). Org-wide missions must carry an explicit [project:all] tag.
        parts = [p for p in projects_str.split(";") if p]
        project = parts[0].split(":")[0] if parts else "default"
        title = line.removeprefix("- ").strip()

    return (project, title)


def pick_mission(
    projects_str: str,
    run_num: str,
    autonomous_mode: str,
    last_project: str = "",
) -> str:
    """Pick the next mission in strict FIFO order.

    Always picks the first pending mission from the store.
    Queue position is the sole priority signal — no LLM-based reordering.
    Returns 'project:title' or empty string.
    """
    try:
        from app.mission_store import MissionStore
        store = MissionStore.load()
        pending = store.get_by_status("pending")
    except (OSError, ValueError):
        return ""
    if not pending:
        return ""
    record = pending[0]
    project = record.project
    if not project:
        parts = [p for p in projects_str.split(";") if p]
        project = parts[0].split(":")[0] if parts else "default"
    return f"{project}:{record.text}"


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print(
            f"Usage: {sys.argv[0]} <projects_str> <run_num> <autonomous_mode> [last_project]",
            file=sys.stderr,
        )
        sys.exit(1)

    projects_str = sys.argv[1]
    run_num = sys.argv[2]
    autonomous_mode = sys.argv[3]
    last_project = sys.argv[4] if len(sys.argv) > 4 else ""

    result = pick_mission(projects_str, run_num, autonomous_mode, last_project)
    print(result)
