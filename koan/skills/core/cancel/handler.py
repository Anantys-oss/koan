"""Kōan cancel skill -- cancel pending missions from the queue."""

import re


def handle(ctx):
    """Handle /cancel command.

    /cancel            — show numbered list of pending missions
    /cancel 3          — cancel mission #3
    /cancel 3,5,7      — cancel missions #3, #5, #7
    /cancel 3 5 7      — same (spaces work too)
    /cancel auth       — cancel first mission matching keyword "auth"
    """
    args = ctx.args.strip()
    missions_file = ctx.instance_dir / "missions.md"

    if not args:
        return _list_pending(missions_file)

    positions = _parse_positions(args)
    if positions is not None:
        if len(positions) == 1:
            return _cancel_mission(missions_file, str(positions[0]))
        return _cancel_bulk(missions_file, positions)

    # Keyword match
    return _cancel_mission(missions_file, args)


def _parse_positions(args):
    """Parse position numbers from flexible input formats.

    Supports: "3", "3 5 7", "3,5,7", "3, 5, 7"
    Returns list of ints or None if input contains non-numeric tokens.
    """
    tokens = re.split(r"[,\s]+", args.strip())
    tokens = [t for t in tokens if t]
    if not tokens:
        return None
    try:
        return [int(t) for t in tokens]
    except ValueError:
        return None


def _list_pending(missions_file):
    """Show numbered list of pending missions for selection."""
    if not missions_file.exists():
        return "ℹ️ No pending missions."

    from app.missions import list_pending, clean_mission_display

    pending = list_pending(missions_file.read_text())

    if not pending:
        return "ℹ️ No pending missions."

    parts = ["Pending missions:\n"]
    for i, m in enumerate(pending, 1):
        display = clean_mission_display(m)
        parts.append(f"  {i}. {display}")

    parts.append("\nReply /cancel <number> or /cancel 3,5,7 to cancel.")
    return "\n".join(parts)


def _cancel_mission(missions_file, identifier):
    """Cancel a mission by number or keyword."""
    from app.missions import cancel_pending_mission, clean_mission_display
    from app.utils import modify_missions_file

    cancelled_text = None

    def _transform(content):
        nonlocal cancelled_text
        updated, cancelled_text = cancel_pending_mission(content, identifier)
        return updated

    try:
        modify_missions_file(missions_file, _transform)
    except ValueError as e:
        return f"⚠️ {e}"

    if cancelled_text is None:
        return "⚠️ Error during cancellation."

    display = clean_mission_display(cancelled_text)
    return f"🗑 Mission cancelled: {display}"


def _cancel_bulk(missions_file, positions):
    """Cancel multiple pending missions by position."""
    from app.missions import cancel_pending_missions_bulk
    from app.utils import modify_missions_file

    displays = None

    def _transform(content):
        nonlocal displays
        updated, displays = cancel_pending_missions_bulk(content, positions)
        return updated

    try:
        modify_missions_file(missions_file, _transform)
    except ValueError as e:
        return f"⚠️ {e}"

    if displays is None:
        return "⚠️ Error during cancellation."

    parts = ["🗑 Cancelled missions:"]
    parts.extend(f"  • {d}" for d in displays)
    return "\n".join(parts)
