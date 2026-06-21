"""Koan list skill -- show current missions (pending + in progress)."""

import re
from datetime import datetime, timedelta

from app.utils import PROJECT_NAME_CHARS

_MISSION_PREFIX = "📋"

# Trailing markers appended by GitHub/Jira @mention missions.
_GITHUB_ORIGIN_MARKER = "📬"
_JIRA_ORIGIN_MARKER = "🎫"
_ORIGIN_MARKERS = (_GITHUB_ORIGIN_MARKER, _JIRA_ORIGIN_MARKER)

# Extract slash command from raw mission line (after optional "- " and [project:X]).
# Project character class is sourced from utils.PROJECT_NAME_CHARS so it stays
# in sync with the precompiled tag regexes there.
_COMMAND_RE = re.compile(
    rf"^(?:-\s*)?(?:\[projec?t:[{PROJECT_NAME_CHARS}]+\]\s*)?/([a-zA-Z0-9_.]+)",
    re.IGNORECASE,
)


def _build_emoji_map():
    """Build a command→emoji map from the skill registry.

    Falls back to an empty dict if the registry can't be loaded.
    """
    try:
        from app.skills import build_registry
        from pathlib import Path
        import os

        registry = build_registry()
        emoji_map = {}
        for skill in registry.list_all():
            if not skill.emoji:
                continue
            for cmd in skill.commands:
                emoji_map[cmd.name] = skill.emoji
                for alias in cmd.aliases:
                    emoji_map[alias] = skill.emoji
        return emoji_map
    except Exception:
        return {}


# Lazy-loaded cache (populated on first call to mission_prefix).
_emoji_cache = None


def mission_prefix(raw_line):
    """Return a unicode prefix for a mission line based on its category.

    Known slash commands get their skill emoji from SKILL.md.
    Unknown slash commands and free-text missions both get the generic 📋.
    """
    global _emoji_cache
    if _emoji_cache is None:
        _emoji_cache = _build_emoji_map()

    m = _COMMAND_RE.match(raw_line.strip())
    if m:
        command = m.group(1).lower()
        return _emoji_cache.get(command, _MISSION_PREFIX)
    return _MISSION_PREFIX


# Pattern matching lifecycle timestamps: ⏳(...) ▶(...) ✅(...) ❌(...)
_LIFECYCLE_TS_RE = re.compile(
    r"\s*([⏳▶✅❌])\s*\((\d{4}-\d{2}-\d{2}T?\s*\d{2}:\d{2})\)"
)


def _format_time_friendly(hour: int, minute: int) -> str:
    """Format hour:minute as '9am', '2:30pm', '12pm'."""
    if hour == 0:
        h, suffix = 12, "am"
    elif hour < 12:
        h, suffix = hour, "am"
    elif hour == 12:
        h, suffix = 12, "pm"
    else:
        h, suffix = hour - 12, "pm"

    if minute == 0:
        return f"{h}{suffix}"
    return f"{h}:{minute:02d}{suffix}"


def _format_friendly_timestamp(iso_str: str, now: datetime) -> str:
    """Convert ISO timestamp to friendly display.

    - Today: '@ 9am'
    - This week (Mon-Sun containing today): 'Mon @ 9am'
    - Older: 'Mon 3/31 @ 9am'
    """
    # Parse both formats: 2026-04-07T20:14 and 2026-04-07 20:14
    iso_str = iso_str.strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(iso_str, fmt)
            break
        except ValueError:
            continue
    else:
        return iso_str  # unparseable, return as-is

    time_str = _format_time_friendly(dt.hour, dt.minute)

    if dt.date() == now.date():
        return f"@ {time_str}"

    # "Current week" = Monday through Sunday containing today
    today = now.date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    day_abbr = dt.strftime("%a")

    if monday <= dt.date() <= sunday:
        return f"{day_abbr} @ {time_str}"

    return f"{day_abbr} {dt.month}/{dt.day} @ {time_str}"


def _humanize_timestamps(text: str, now: datetime = None) -> str:
    """Replace raw lifecycle timestamps with friendly display.

    Only the last timestamp (most relevant) is shown.
    ⏳(2026-04-07T20:14) → ⏳@ 9pm
    """
    if now is None:
        now = datetime.now()

    matches = list(_LIFECYCLE_TS_RE.finditer(text))
    if not matches:
        return text

    # Strip all lifecycle timestamps from the text
    clean = _LIFECYCLE_TS_RE.sub("", text).rstrip()

    # Use the last timestamp (most recent lifecycle stage)
    last = matches[-1]
    emoji = last.group(1)
    friendly = _format_friendly_timestamp(last.group(2), now)

    return f"{clean} {emoji}{friendly}"


def _detect_origin_marker(raw_line: str) -> str:
    """Return the leading origin marker for a mission, or empty string."""
    for marker in _ORIGIN_MARKERS:
        if marker in raw_line:
            return marker
    return ""


def _strip_origin_markers(text: str) -> str:
    """Remove origin markers from display text to avoid duplication."""
    for marker in _ORIGIN_MARKERS:
        text = text.replace(marker, "")
    parts = text.split()
    return " ".join(parts)


def _record_ts_label(record, now: datetime) -> str:
    """Return a friendly timestamp suffix for a record from its typed fields.

    In-progress missions show their started_at time (▶); pending missions
    show their queued_at time (⏳).  Returns an empty string when the
    relevant timestamp field is absent.
    """
    if record.status == "in_progress" and record.started_at:
        return f" ▶{_format_friendly_timestamp(record.started_at, now)}"
    if record.status == "pending" and record.queued_at:
        return f" ⏳{_format_friendly_timestamp(record.queued_at, now)}"
    return ""


def handle(ctx):
    """Handle /list command -- display numbered mission list."""
    # Reset emoji cache on each /list invocation to pick up new skills.
    global _emoji_cache
    _emoji_cache = None

    from app.mission_store import MissionStore

    store = MissionStore()
    in_progress = store.get_by_status("in_progress")
    pending = store.get_by_status("pending")

    if not in_progress and not pending:
        return "ℹ️ No missions pending or in progress."

    now = datetime.now()
    parts = []

    if in_progress:
        parts.append("🔄 In Progress")
        parts.append("```")
        for r in in_progress:
            origin = r.origin_marker()
            prefix = mission_prefix(r.text)
            title = r.display_title()
            ts = _record_ts_label(r, now)
            parts.append(f"{origin}{prefix} {title}{ts}")
        parts.append("```")
        parts.append("")

    if pending:
        parts.append("⏳ Pending")
        for i, r in enumerate(pending, 1):
            origin = r.origin_marker()
            prefix = mission_prefix(r.text)
            title = r.display_title()
            ts = _record_ts_label(r, now)
            parts.append(f"  {i}. {origin}{prefix} {title}{ts}")

    return "\n".join(parts)
