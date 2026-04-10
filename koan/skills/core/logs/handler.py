"""Kōan logs skill — show last lines from run and/or awake logs."""

import re
from pathlib import Path

_TAIL_LINES = 30
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_VALID_FILTERS = {"run", "awake", "all"}
_LOG_MAP = {
    "run": ["run.log"],
    "awake": ["awake.log"],
    "all": ["run.log", "awake.log"],
}


def _strip_ansi(text):
    """Remove ANSI color/style escape sequences from text."""
    return _ANSI_RE.sub("", text)


def _tail(path, n=_TAIL_LINES):
    """Return the last n lines of a file, or None if unavailable."""
    if not path.exists():
        return None
    try:
        lines = path.read_text().splitlines()
        if not lines:
            return None
        return [_strip_ansi(line) for line in lines[-n:]]
    except OSError:
        return None


def handle(ctx):
    """Handle /logs command — show last lines from run and/or awake logs.

    Usage: /logs [run|awake|all]  (default: run)
    """
    logs_dir = ctx.koan_root / "logs"

    # Parse filter argument
    arg = (ctx.args or "").strip().lower()
    if arg and arg not in _VALID_FILTERS:
        return f"Unknown filter `{arg}`. Use: /logs [run|awake|all]"
    log_filter = arg or "run"
    log_files = _LOG_MAP[log_filter]

    sections = []
    for filename in log_files:
        lines = _tail(logs_dir / filename)
        if lines:
            label = filename.replace(".log", "")
            block = "\n".join(lines)
            sections.append(f"📋 {label}\n```\n{block}\n```")

    if not sections:
        return "No log files found. Start Kōan first with `make start`."

    return "\n\n".join(sections)
