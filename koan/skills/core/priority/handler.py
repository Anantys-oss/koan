"""Kōan priority skill -- reorder pending missions in the queue."""

import re


def handle(ctx):
    """Handle /priority command.

    /priority              — show queue with usage hint
    /priority 3            — move mission #3 to top of queue
    /priority 4,6,5        — reorder: #4 first, #6 second, #5 third
    /priority 4 6 5        — same (spaces work too)
    /priority 4 , 6, 5     — same (commas with spaces)
    """
    args = ctx.args.strip()

    if not args:
        return _show_queue_with_hint()

    positions = _parse_positions(args)
    if positions is None:
        return "⚠️ Could not parse positions.\nUsage: /prio 3 or /prio 4,6,5"

    if len(positions) == 1:
        return _reorder_single(positions[0])

    return _reorder_bulk(positions)


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


def _show_queue_with_hint():
    """Show queue with usage hint when /priority is called bare."""
    from app.mission_store import MissionStore

    store = MissionStore()
    pending = store.get_by_status("pending")
    if not pending:
        return "ℹ️ Queue is empty.\n\nUsage: /prio <n>"

    parts = ["PENDING"]
    for i, r in enumerate(pending, 1):
        parts.append(f"  {i}. {r.display_title()}")

    parts.append("\nUsage:")
    parts.append("  /prio <n>       — bump mission #n to the top")
    parts.append("  /prio 4,6,5     — reorder: #4 first, #6 second, #5 third")
    return "\n".join(parts)


def _reorder_single(position):
    """Move a single pending mission to top of queue."""
    from app.mission_store import locked_store

    with locked_store() as store:
        pending = store.get_by_status("pending")
        if not pending:
            return "⚠️ No pending missions to reorder."
        if position < 1 or position > len(pending):
            return f"⚠️ Invalid position. Use 1-{len(pending)}."

        record = pending[position - 1]
        moved_display = record.display_title()
        store.reorder_pending(position - 1, 0)

    return f"⬆️ Bumped to top: {moved_display}"


def _reorder_bulk(positions):
    """Reorder multiple pending missions to the top of the queue."""
    from app.mission_store import locked_store

    if len(set(positions)) != len(positions):
        return "⚠️ Duplicate positions: use each position only once."

    displays = []

    with locked_store() as store:
        pending = store.get_by_status("pending")
        if not pending:
            return "⚠️ No pending missions to reorder."

        for pos in positions:
            if pos < 1 or pos > len(pending):
                return f"⚠️ Invalid position {pos}. Use 1-{len(pending)}."

        # Snapshot records at the original positions before any mutation
        target_records = [pending[pos - 1] for pos in positions]

        for i, record in enumerate(target_records):
            displays.append(record.display_title())

            # Find current index of this record in the pending sub-queue
            current_pending = store.get_by_status("pending")
            from_idx = next(
                (idx for idx, r in enumerate(current_pending) if r.id == record.id),
                None,
            )
            if from_idx is not None:
                store.reorder_pending(from_idx, i)

    if not displays:
        return "⚠️ Error during reorder."

    parts = ["🔀 Reordered queue:"]
    for i, d in enumerate(displays, 1):
        parts.append(f"  {i}. {d}")
    return "\n".join(parts)
