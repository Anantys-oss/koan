"""Kōan passive/active skill — toggle read-only passive mode."""

from app.focus_manager import parse_duration
from app.passive_manager import (
    check_passive,
    create_passive,
    remove_passive,
)


def handle(ctx):
    """Toggle passive mode on or off."""
    koan_root = str(ctx.koan_root)

    if ctx.command_name == "active":
        state = check_passive(koan_root)
        if state:
            remove_passive(koan_root)
            return "🟢 Active mode. Back to work."
        return "🟢 Already active — not in passive mode."

    # /passive [duration]
    args = ctx.args.strip() if ctx.args else ""

    # Check if already passive
    existing = check_passive(koan_root)
    if existing and not args:
        remaining = existing.remaining_display()
        if existing.duration == 0:
            return "👁️ Already in passive mode (indefinite). Use /active to resume."
        return f"👁️ Already in passive mode ({remaining} remaining). Use /active to resume."

    # Parse optional duration
    duration = 0  # indefinite by default
    if args:
        parsed = parse_duration(args)
        if parsed is not None:
            duration = parsed
        else:
            return f"❌ Invalid duration: '{args}'. Examples: 4h, 2h30m, 90m"

    state = create_passive(koan_root, duration=duration, reason="manual")
    remaining = state.remaining_display(now=state.activated_at)
    if duration == 0:
        return (
            "👁️ Passive mode ON. "
            "Read-only — no missions, no branches. "
            "Use /active to resume."
        )
    return (
        f"👁️ Passive mode ON for {remaining}. "
        "Read-only — no missions, no branches. "
        "Use /active to resume early."
    )
