"""Handler for /reset command.

Resets the run counter to 0. If paused due to max_runs, also resumes.
"""

from pathlib import Path

from app.skills import SkillContext


def handle(ctx: SkillContext) -> str:
    """Reset the run counter to zero."""
    from app.pause_manager import get_pause_state, remove_pause
    from app.signals import PAUSE_FILE, RESET_COUNTER_FILE

    koan_root = str(ctx.koan_root)
    reset_file = Path(koan_root, RESET_COUNTER_FILE)
    pause_file = Path(koan_root, PAUSE_FILE)

    paused_max_runs = False
    if pause_file.exists():
        state = get_pause_state(koan_root)
        if state and state.reason == "max_runs":
            paused_max_runs = True
            remove_pause(koan_root)

    reset_file.touch()

    if paused_max_runs:
        return "🔄 Run counter reset and resumed from max_runs pause."
    return "🔄 Run counter reset to 0."
