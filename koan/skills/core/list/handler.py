"""Koan list skill -- show current missions (pending + in progress)."""

import re

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

    if in_progress:
        parts.append("IN PROGRESS")
        for i, m in enumerate(in_progress, 1):
            prefix = mission_prefix(m)
            display = clean_mission_display(m)
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
            display = clean_mission_display(m)
            origin = _GITHUB_ORIGIN_MARKER if _GITHUB_ORIGIN_MARKER in m else ""
            if prefix:
                parts.append(f"  {i}. {origin}{prefix} {display}")
            else:
                parts.append(f"  {i}. {origin}{display}")

    return "\n".join(parts)
