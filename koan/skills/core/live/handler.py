"""Kōan live progress skill — show current mission progress."""

# Maximum activity lines to show in /live output.
# Keeps Telegram messages readable without scrolling.
_MAX_ACTIVITY_LINES = 30


def _get_parallel_slot_summary(instance_dir):
    """Return a slot-utilisation line when parallel mode is active.

    Returns None when max_parallel_sessions == 1 (default) to keep
    the /live output identical for single-slot installations.
    """
    from app.session_manager import get_max_parallel_sessions, SessionRegistry
    max_slots = get_max_parallel_sessions()
    if max_slots <= 1:
        return None
    try:
        registry = SessionRegistry(str(instance_dir))
        active = registry.get_active()
        if not active:
            return None
        lines = [f"Slots: {len(active)}/{max_slots} active"]
        for s in active:
            elapsed = ""
            if s.started_at:
                import time
                elapsed = f" ({int((time.time() - s.started_at) / 60)}m elapsed)"
            lines.append(f"  • [{s.project_name}] {s.mission_text[:60]}{elapsed}")
        return "\n".join(lines)
    except Exception as e:
        import logging
        logging.getLogger("koan").warning("[parallel] slot summary failed: %s", e)
        return None


def _read_live_progress(instance_dir):
    """Read live progress from journal/pending.md.

    Returns the mission header and all progress lines,
    or None if no mission is running.
    """
    pending_path = instance_dir / "journal" / "pending.md"
    if not pending_path.exists():
        return None

    content = pending_path.read_text().strip()
    if not content:
        return None

    return content


def _get_in_progress_missions(instance_dir):
    """Get in-progress missions from missions.md.

    Returns a list of (project, mission_text) tuples, or empty list.
    """
    try:
        from app.mission_store import MissionStore

        store = MissionStore.load(str(instance_dir))
        # record.text is already clean (no project tag, no timestamps);
        # untagged missions map to the "default" project (matching
        # extract_project_tag's fallback).
        return [
            (r.project or "default", r.text)
            for r in store.get_by_status("in_progress")
        ]
    except Exception:
        return []


def _format_no_output(missions):
    """Format a message for running missions with no output available."""
    if len(missions) == 1:
        project, text = missions[0]
        return f"Mission [{project}] running: {text}\nNo output available yet."

    lines = []
    for project, text in missions:
        lines.append(f"- [{project}] {text}")
    return "Missions running:\n" + "\n".join(lines) + "\nNo output available yet."


def _format_progress(content):
    """Format progress for Telegram: wrap activity tail in a code block.

    The pending.md format is:
        # Mission: ...
        Project: ...
        Started: ...
        ---
        HH:MM — did X
        HH:MM — did Y
        ... (CLI output when streaming)

    Shows the header plus the last N activity lines in a code block.
    When output is truncated, a note indicates how many lines were skipped.
    """
    parts = content.split("\n---\n", 1)
    if len(parts) < 2 or not parts[1].strip():
        return content

    header = parts[0]
    activity_lines = parts[1].strip().splitlines()

    total = len(activity_lines)
    if total > _MAX_ACTIVITY_LINES:
        skipped = total - _MAX_ACTIVITY_LINES
        tail = activity_lines[-_MAX_ACTIVITY_LINES:]
        activity = "\n".join(tail)
        return (
            f"{header}\n\n"
            f"_({skipped} earlier lines omitted)_\n"
            f"```\n{activity}\n```"
        )

    activity = "\n".join(activity_lines)
    return f"{header}\n\n```\n{activity}\n```"


def handle(ctx):
    """Handle /live command — show live progress of current mission."""
    slot_summary = _get_parallel_slot_summary(ctx.instance_dir)

    progress = _read_live_progress(ctx.instance_dir)
    if progress:
        output = _format_progress(progress)
        if slot_summary:
            output = f"{slot_summary}\n\n{output}"
        return output

    # No pending.md — check if missions are actually in progress
    missions = _get_in_progress_missions(ctx.instance_dir)
    if missions:
        output = _format_no_output(missions)
        if slot_summary:
            output = f"{slot_summary}\n\n{output}"
        return output

    if slot_summary:
        return slot_summary

    return "No mission running."
