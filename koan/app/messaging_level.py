"""Bridge verbosity level resolution (debug / normal) and gating helper.

Resolution precedence (highest first):
    1. KOAN_MESSAGING_LEVEL env var
    2. instance/.koan-messaging-level state file (written by the skill)
    3. messaging.level in config.yaml
    4. "normal" (default)

Every gated site routes user-facing emissions through ``debug_only`` so that
suppressed messages still land in the log stream for debugging.
"""
import contextlib
import os
import sys
import time
from pathlib import Path

VALID_LEVELS = ("debug", "normal")
DEFAULT_LEVEL = "normal"
STATE_FILE = ".koan-messaging-level"

# Short-TTL memoization for the resolved level. is_debug() is called on every
# mission start/end, every debug_only(), and once per GitHub/Jira mention in a
# batch — without a cache that is one env lookup + stat + read per call. A small
# TTL also prevents the level from flipping mid-batch if the state file races.
_CACHE_TTL = 5.0  # seconds
_cached_level = None
_cached_at = 0.0


def _koan_root() -> Path:
    return Path(os.environ["KOAN_ROOT"])


def _state_path() -> Path:
    return _koan_root() / "instance" / STATE_FILE


def _coerce(value: str) -> str:
    v = (value or "").strip().lower()
    return v if v in VALID_LEVELS else DEFAULT_LEVEL


def get_configured_messaging_level() -> str:
    """Persistent default from config.yaml (messaging.level)."""
    try:
        from app.config import get_configured_messaging_level as _cfg
        return _coerce(_cfg())
    except (ImportError, OSError, ValueError, KeyError, AttributeError):
        return DEFAULT_LEVEL


def _resolve_messaging_level() -> str:
    """Resolve: env -> state file -> config.yaml -> 'normal'. Never raises."""
    env = os.environ.get("KOAN_MESSAGING_LEVEL")
    if env:
        return _coerce(env)
    try:
        p = _state_path()
        if p.exists():
            return _coerce(p.read_text())
    except (OSError, KeyError) as e:
        # A KeyError means KOAN_ROOT is unset; an OSError means the state file
        # is unreadable. Leave a trace so an override that failed to apply is
        # diagnosable, then fall back to the config/default level.
        _log("messaging", f"messaging.level state read failed, using config/default: {e}")
    return get_configured_messaging_level()


def get_messaging_level() -> str:
    """Resolved level with short-TTL memoization. Never raises."""
    global _cached_level, _cached_at
    now = time.monotonic()
    if _cached_level is not None and (now - _cached_at) < _CACHE_TTL:
        return _cached_level
    _cached_level = _resolve_messaging_level()
    _cached_at = now
    return _cached_level


def _invalidate_cache() -> None:
    global _cached_level, _cached_at
    _cached_level = None
    _cached_at = 0.0


def is_debug() -> bool:
    return get_messaging_level() == "debug"


def set_messaging_level(level: str) -> str:
    """Write the runtime override state file. Returns the stored level."""
    level = _coerce(level)
    from app.utils import atomic_write
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, level + "\n")
    _invalidate_cache()
    return level


def clear_override() -> None:
    with contextlib.suppress(FileNotFoundError, KeyError):
        _state_path().unlink()
    _invalidate_cache()


def _log(category: str, msg: str) -> None:
    try:
        from app.run_log import log_safe
        log_safe(category, msg)
    except (ImportError, OSError, ValueError):
        # debug_only() promises every suppressed message still reaches the logs.
        # If the normal log sink is unavailable, fall back to stderr so the
        # message is never lost entirely (neither sent nor logged).
        with contextlib.suppress(Exception):
            print(f"[{category}] {msg}", file=sys.stderr)


def debug_only(msg: str, send_fn, *, log_category: str = "bridge") -> None:
    """Always log msg; only invoke send_fn (the user-facing emit) in debug mode.

    Honors the requirement that suppressed messages still reach the logs.
    """
    _log(log_category, msg)
    if is_debug():
        send_fn()
