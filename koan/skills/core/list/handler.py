"""Koan list skill -- show current missions (pending + in progress)."""

import re
from datetime import datetime, timedelta

_MISSION_PREFIX = "📋"

# Trailing marker appended by GitHub @mention missions.
_GITHUB_ORIGIN_MARKER = "📬"

# Extract slash command from raw mission line (after optional "- " and [project:X]).
_COMMAND_RE = re.compile(
    r"^(?:-\s*)?(?:\[projec?t:[a-zA-Z0-9_-]+\]\s*)?/([a-zA-Z0-9_.]+)"
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


def handle(ctx):
    """Handle /list command -- display numbered mission list."""
    # Reset emoji cache on each /list invocation to pick up new skills.
    global _emoji_cache
    _emoji_cache = None

    missions_file = ctx.instance_dir / "missions.md"

    if not missions_file.exists():
        return "ℹ️ No missions file found."

    from app.missions import parse_sections, clean_mission_display

    content = missions_file.read_text()
    sections = parse_sections(content)

    in_progress = sections.get("in_progress", [])
    pending = sections.get("pending", [])

    if not in_progress and not pending:
        return "ℹ️ No missions pending or in progress."

    parts = []

    now = datetime.now()

    if in_progress:
        parts.append("IN PROGRESS")
        for i, m in enumerate(in_progress, 1):
            prefix = mission_prefix(m)
            display = _humanize_timestamps(clean_mission_display(m), now)
            origin = _GITHUB_ORIGIN_MARKER if _GITHUB_ORIGIN_MARKER in m else ""
            if prefix:
                parts.append(f"  {i}. {origin}{prefix} {display}")
            else:
                parts.append(f"  {i}. {origin}{display}")
        parts.append("")

    if pending:
        parts.append("PENDING")
        for i, m in enumerate(pending, 1):
            prefix = mission_prefix(m)
            display = _humanize_timestamps(clean_mission_display(m), now)
            origin = _GITHUB_ORIGIN_MARKER if _GITHUB_ORIGIN_MARKER in m else ""
            if prefix:
                parts.append(f"  {i}. {origin}{prefix} {display}")
            else:
                parts.append(f"  {i}. {origin}{display}")

    return "\n".join(parts)
