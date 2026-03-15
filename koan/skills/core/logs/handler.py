"""Kōan logs skill — show last lines from run and awake logs."""

from pathlib import Path

_LOG_FILES = ["run.log", "awake.log"]
_TAIL_LINES = 10


def _tail(path, n=_TAIL_LINES):
    """Return the last n lines of a file, or None if unavailable."""
    if not path.exists():
        return None
    try:
        lines = path.read_text().splitlines()
        return lines[-n:] if lines else None
    except OSError:
        return None


def handle(ctx):
    """Handle /logs command — show last 10 lines from each log file."""
    logs_dir = ctx.koan_root / "logs"
    sections = []

    for filename in _LOG_FILES:
        lines = _tail(logs_dir / filename)
        if lines:
            label = filename.replace(".log", "")
            block = "\n".join(lines)
            sections.append(f"📋 {label}\n```\n{block}\n```")

    if not sections:
        return "No log files found. Start Kōan first with `make start`."

    return "\n\n".join(sections)
