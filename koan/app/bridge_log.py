"""Colored log output for the Kōan bridge process.

Mirrors the log() function from run.py: each category gets its own
ANSI color prefix for easy visual scanning in the terminal.

Categories:
  init     (blue)          — startup messages
  chat     (cyan)          — incoming/outgoing chat messages
  mission  (green)         — mission queuing
  outbox   (magenta)       — outbox flush events
  error    (red+bold)      — errors
  health   (yellow)        — compaction, heartbeat, maintenance
  skill    (green+dim)     — skill dispatch events

Usage:
    from app.bridge_log import log
    log("init", f"Token: ...{token[-8:]}")
    log("error", f"Telegram error: {e}")

Output goes to stderr so colors survive piping (e.g. start.sh
prefixes stdout through a while-read loop, stripping TTY status).
"""

import os
import sys
import time

# ANSI escape codes
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_MAGENTA = "\033[35m"
_CYAN = "\033[36m"
_WHITE = "\033[37m"

# Category → color mapping (mirrors run.py's log() function)
_COLORS = {
    "init": _BLUE,
    "chat": _CYAN,
    "mission": _GREEN,
    "outbox": _MAGENTA,
    "error": f"{_BOLD}{_RED}",
    "health": _YELLOW,
    "skill": f"{_DIM}{_GREEN}",
}

_DEFAULT_COLOR = _WHITE


def _use_color() -> bool:
    """Check if output should use ANSI colors.

    Checks stderr (our output target) for TTY status, with
    KOAN_FORCE_COLOR env var override for pipe contexts.
    """
    if os.environ.get("KOAN_FORCE_COLOR", ""):
        return True
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


def log(category: str, message: str) -> None:
    """Print a timestamped, colored log line to stderr: [HH:MM:SS] [category] message.

    The timestamp mirrors run_log.log() so awake.log and run.log can be
    correlated when debugging. Colors are only applied when stderr is a TTY
    (or KOAN_FORCE_COLOR is set); pipes/CI get a plain prefix.
    """
    ts = time.strftime("%H:%M:%S")
    if _use_color():
        color = _COLORS.get(category, _DEFAULT_COLOR)
        print(f"{_DIM}[{ts}]{_RESET} {color}[{category}]{_RESET} {message}", file=sys.stderr)
    else:
        print(f"[{ts}] [{category}] {message}", file=sys.stderr)
