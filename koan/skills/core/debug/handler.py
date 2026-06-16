"""Koan -- /debug bridge handler.

Queues a /debug mission to the pending queue. The actual debugging
happens in debug_runner.py via the agent loop.
"""

from typing import Optional


def handle(ctx) -> Optional[str]:
    """Handle /debug — queue a structured debug mission."""
    args = ctx.args.strip() if ctx.args else ""
    if not args:
        return "Usage: `/debug <issue-url> [context]`"

    project_tag = ""
    if ctx.project_name:
        project_tag = f"[project:{ctx.project_name}] "

    from pathlib import Path
    from app.missions import insert_mission
    from app.utils import atomic_write

    missions_path = ctx.missions_path
    content = Path(missions_path).read_text() if Path(missions_path).exists() else ""
    entry = f"- {project_tag}/debug {args}"
    content = insert_mission(content, entry, urgent=True)
    atomic_write(missions_path, content)

    return f"Queued `/debug` mission (head of queue): {args[:80]}"
