"""Kōan priority skill -- reorder pending missions in the queue."""

import re


def handle(ctx):
    """Handle /priority command.

    /priority              — show queue with usage hint
    /priority 3            — move mission #3 to top of queue
    /priority 5 2          — move mission #5 to position 2
    /priority 4,6,5        — reorder: #4 first, #6 second, #5 third
    /priority 4 6 5        — same (3+ numbers = bulk reorder)
    /priority 4 , 6, 5     — same (commas with spaces)
    """
    args = ctx.args.strip()
    missions_file = ctx.instance_dir / "missions.md"

    if not args:
        return _show_queue_with_hint(missions_file)

    positions = _parse_positions(args)
    if positions is None:
        return "⚠️ Could not parse positions.\nUsage: /prio 3 or /prio 4,6,5"

    if len(positions) == 1:
        return _reorder_single(missions_file, positions[0], target=1)

    # Two numbers without commas → legacy "move N to position M"
    if len(positions) == 2 and "," not in args:
        return _reorder_single(missions_file, positions[0], target=positions[1])

    return _reorder_bulk(missions_file, positions)


def _parse_positions(args):
    """Parse position numbers from flexible input formats.

    Supports: "4 6 5", "4,6,5", "4, 6, 5", "4 , 6 , 5"
    Returns list of ints or None on failure.
    """
    # Split on commas and/or whitespace
    tokens = re.split(r"[,\s]+", args.strip())
    tokens = [t for t in tokens if t]  # drop empty strings
    if not tokens:
        return None
    try:
        return [int(t) for t in tokens]
    except ValueError:
        return None


def _show_queue_with_hint(missions_file):
    """Show queue with usage hint when /priority is called bare."""
    if not missions_file.exists():
        return "ℹ️ Queue is empty.\n\nUsage: /prio <n>"

    from app.missions import list_pending, clean_mission_display

    pending = list_pending(missions_file.read_text())
    if not pending:
        return "ℹ️ Queue is empty.\n\nUsage: /prio <n>"

    parts = ["PENDING"]
    for i, m in enumerate(pending, 1):
        display = clean_mission_display(m)
        parts.append(f"  {i}. {display}")

    parts.append("\nUsage:")
    parts.append("  /prio <n>       — bump mission #n to the top")
    parts.append("  /prio <n> <m>   — move mission #n to position m")
    parts.append("  /prio 4,6,5     — reorder: #4 first, #6 second, #5 third")
    return "\n".join(parts)


def _reorder_single(missions_file, position, target):
    """Reorder a single pending mission."""
    from app.missions import reorder_mission, clean_mission_display
    from app.utils import modify_missions_file

    moved_display = None

    def _transform(content):
        nonlocal moved_display
        updated, moved_display = reorder_mission(content, position, target)
        return updated

    try:
        modify_missions_file(missions_file, _transform)
    except ValueError as e:
        return f"⚠️ {e}"

    if moved_display is None:
        return "⚠️ Error during reorder."

    if target == 1:
        return f"⬆️ Bumped to top: {moved_display}"
    return f"🔀 Moved to position {target}: {moved_display}"


def _reorder_bulk(missions_file, positions):
    """Reorder multiple pending missions to the top of the queue."""
    from app.missions import reorder_missions_bulk
    from app.utils import modify_missions_file

    displays = None

    def _transform(content):
        nonlocal displays
        updated, displays = reorder_missions_bulk(content, positions)
        return updated

    try:
        modify_missions_file(missions_file, _transform)
    except ValueError as e:
        return f"⚠️ {e}"

    if displays is None:
        return "⚠️ Error during reorder."

    parts = ["🔀 Reordered queue:"]
    for i, d in enumerate(displays, 1):
        parts.append(f"  {i}. {d}")
    return "\n".join(parts)
