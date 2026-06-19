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
    from app.mission_store import MissionStore

    store = MissionStore.load()
    pending = store.get_by_status("pending")

    if not pending:
        return "ℹ️ No pending missions."

    parts = ["Pending missions:\n"]
    for i, r in enumerate(pending, 1):
        parts.append(f"  {i}. {r.display_title()}")

    parts.append("\nReply /cancel <number> or /cancel 3,5,7 to cancel.")
    return "\n".join(parts)


def _cancel_mission(missions_file, identifier):
    """Cancel a mission by number or keyword."""
    from app.mission_store import locked_store

    cancelled_display = None

    with locked_store() as store:
        pending = store.get_by_status("pending")
        if not pending:
            return "ℹ️ No pending missions."

        record = None
        if identifier.lstrip("-").isdigit():
            pos = int(identifier)
            if 1 <= pos <= len(pending):
                record = pending[pos - 1]
            else:
                return f"⚠️ Invalid position. Use 1-{len(pending)}."
        else:
            kw = identifier.lower()
            for r in pending:
                if kw in r.text.lower():
                    record = r
                    break
            if record is None:
                return f"⚠️ No pending mission matching '{identifier}'."

        cancelled_display = record.display_title()
        store.cancel_pending(record.text)

    return f"🗑 Mission cancelled: {cancelled_display}"


def _cancel_bulk(missions_file, positions):
    """Cancel multiple pending missions by position."""
    from app.mission_store import locked_store

    with locked_store() as store:
        pending = store.get_by_status("pending")
        if not pending:
            return "ℹ️ No pending missions."

        for pos in positions:
            if pos < 1 or pos > len(pending):
                return f"⚠️ Invalid position {pos}. Use 1-{len(pending)}."

        # Snapshot records at the given positions before any mutation
        records_to_cancel = [pending[pos - 1] for pos in positions]

        displays = []
        for record in records_to_cancel:
            displays.append(record.display_title())
            store.cancel_pending(record.text)

    parts = ["🗑 Cancelled missions:"]
    parts.extend(f"  • {d}" for d in displays)
    return "\n".join(parts)
